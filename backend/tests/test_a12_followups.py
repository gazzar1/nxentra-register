# tests/test_a12_followups.py
"""
A12 follow-ups landed alongside A13:

1. Refund / payout / dispute JEs that touch the clearing account now
   carry the SETTLEMENT_PROVIDER dimension tag, so the reconciliation
   engine sees credits drain the right provider's clearing balance.
2. AccountDimensionRule(REQUIRED) on the clearing account makes the
   dimension mandatory — manual JEs to clearing without the tag are
   rejected by post_journal_entry's dimension validation.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import (
    Account,
    AccountDimensionRule,
    AnalysisDimension,
    JournalEntry,
)
from accounting.settlement_provider import (
    SETTLEMENT_PROVIDER_DIMENSION_CODE,
    SettlementProvider,
)
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    """Bootstrap the Shopify clearing + dimension + provider rows + the
    AccountDimensionRule(REQUIRED) on clearing."""
    from accounting.mappings import ModuleAccountMapping
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    with projection_writes_allowed():
        clearing = Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SHOPIFY_CLEARING",
        account=clearing,
    )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SALES_REVENUE",
        account=revenue,
    )
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a12-followup.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store, "clearing": clearing, "revenue": revenue}


# =============================================================================
# AccountDimensionRule(REQUIRED) on clearing
# =============================================================================


def test_bootstrap_creates_required_rule_on_clearing(shopify_setup, company):
    # Bootstrap must register the SETTLEMENT_PROVIDER dimension as REQUIRED
    # on the clearing account so manual JEs without the tag are rejected.
    dimension = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_PROVIDER_DIMENSION_CODE)
    rule = AccountDimensionRule.objects.get(
        company=company,
        account=shopify_setup["clearing"],
        dimension=dimension,
    )
    assert rule.rule_type == AccountDimensionRule.RuleType.REQUIRED


def test_bootstrap_idempotent_on_clearing_rule(shopify_setup, company):
    # Running the bootstrap twice must not duplicate the rule.
    from shopify_connector.commands import _ensure_shopify_sales_setup

    before = AccountDimensionRule.objects.filter(company=company).count()
    _ensure_shopify_sales_setup(shopify_setup["store"])
    after = AccountDimensionRule.objects.filter(company=company).count()
    assert before == after


# =============================================================================
# Settlement (payout) tags clearing line with shopify_payments
# =============================================================================


def test_create_and_post_settlement_tags_clearing_with_provider(shopify_setup, company):
    # A POSTED Shopify Payments payout tags its clearing JE line with the
    # shopify_payments SettlementProvider's dimension value, so Stage 1's
    # credit side drains the right provider's balance.
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import JournalLineAnalysis
    from platform_connectors.commands import create_and_post_settlement
    from platform_connectors.models import PlatformSettlement

    # The settlement command needs CASH_BANK + PAYMENT_PROCESSING_FEES
    # accounts mapped, in addition to SHOPIFY_CLEARING (already in fixture).
    with projection_writes_allowed():
        bank = Account.objects.projection().create(
            company=company,
            code="10100",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        fees_acct = Account.objects.projection().create(
            company=company,
            code="60000",
            name="Payment Processing Fees",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="CASH_BANK",
        account=bank,
    )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="PAYMENT_PROCESSING_FEES",
        account=fees_acct,
    )

    shopify_payments = SettlementProvider.objects.get(company=company, normalized_code="shopify_payments")

    tags = [
        {
            "dimension_public_id": str(shopify_payments.dimension_value.dimension.public_id),
            "value_public_id": str(shopify_payments.dimension_value.public_id),
        }
    ]

    result = create_and_post_settlement(
        company=company,
        platform="shopify",
        platform_document_id="test-payout-1",
        settlement_type=PlatformSettlement.SettlementType.PAYOUT,
        gross_amount=Decimal("100.00"),
        fees=Decimal("3.00"),
        net_amount=Decimal("97.00"),
        currency=company.default_currency,
        settlement_date=date.today(),
        reference="Test payout",
        clearing_line_analysis_tags=tags,
    )

    assert result.success, f"settlement failed: {result.error}"
    je = result.data["journal_entry"]

    clearing_line = je.lines.filter(account=shopify_setup["clearing"]).first()
    assert clearing_line is not None, "expected a clearing line on the settlement JE"
    line_tags = list(JournalLineAnalysis.objects.filter(journal_line=clearing_line))
    assert len(line_tags) == 1
    assert line_tags[0].dimension_value_id == shopify_payments.dimension_value_id


# =============================================================================
# Manual JE on clearing without the tag — must be rejected
# =============================================================================


def test_manual_je_to_clearing_without_dimension_is_rejected(shopify_setup, company, user, owner_membership):
    # A manual JE that hits the clearing account must include the
    # SETTLEMENT_PROVIDER dimension tag. If it doesn't, post_journal_entry
    # should refuse to post (the dimension validation runs there). This
    # is the contract that prevents back-door bypassing of reconciliation.
    from accounting.commands import create_journal_entry, post_journal_entry, save_journal_entry_complete
    from accounts.authz import ActorContext

    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    actor = ActorContext(user=user, company=company, membership=owner_membership, perms=perms)

    je_lines = [
        {
            "account_id": shopify_setup["clearing"].id,
            "description": "Manual debit to clearing (no dimension)",
            "debit": Decimal("100.00"),
            "credit": Decimal("0"),
        },
        {
            "account_id": shopify_setup["revenue"].id,
            "description": "Revenue offset",
            "debit": Decimal("0"),
            "credit": Decimal("100.00"),
        },
    ]

    # create_journal_entry creates an INCOMPLETE entry — that step doesn't
    # validate dimensions. save_journal_entry_complete moves it to DRAFT.
    create_result = create_journal_entry(
        actor=actor,
        date=date.today(),
        memo="Manual JE missing dimension",
        lines=je_lines,
        kind=JournalEntry.Kind.NORMAL,
    )
    assert create_result.success
    entry = create_result.data

    save_result = save_journal_entry_complete(actor, entry.id)
    assert save_result.success
    entry = save_result.data

    # post_journal_entry runs validate_line_dimensions, which should
    # reject the missing SETTLEMENT_PROVIDER tag on the clearing line.
    post_result = post_journal_entry(actor, entry.id)
    assert not post_result.success, "manual JE missing dim tag should be rejected"
    error_text = (post_result.error or "").lower()
    assert "settlement" in error_text or "dimension" in error_text, (
        f"expected dimension-required error, got: {post_result.error!r}"
    )


def test_manual_je_to_clearing_with_dimension_is_accepted(shopify_setup, company, user, owner_membership):
    # The same JE with the dimension tag on the clearing line posts
    # cleanly — proves the rejection above is specifically about the
    # missing tag, not something else.
    from accounting.commands import create_journal_entry, post_journal_entry, save_journal_entry_complete
    from accounts.authz import ActorContext

    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    actor = ActorContext(user=user, company=company, membership=owner_membership, perms=perms)

    paymob = SettlementProvider.objects.get(company=company, normalized_code="paymob")

    je_lines = [
        {
            "account_id": shopify_setup["clearing"].id,
            "description": "Manual debit to clearing (with dimension)",
            "debit": Decimal("100.00"),
            "credit": Decimal("0"),
            "analysis_tags": [
                {
                    "dimension_public_id": str(paymob.dimension_value.dimension.public_id),
                    "value_public_id": str(paymob.dimension_value.public_id),
                }
            ],
        },
        {
            "account_id": shopify_setup["revenue"].id,
            "description": "Revenue offset",
            "debit": Decimal("0"),
            "credit": Decimal("100.00"),
        },
    ]

    create_result = create_journal_entry(
        actor=actor,
        date=date.today(),
        memo="Manual JE with dimension",
        lines=je_lines,
        kind=JournalEntry.Kind.NORMAL,
    )
    assert create_result.success
    entry = create_result.data

    save_result = save_journal_entry_complete(actor, entry.id)
    assert save_result.success
    entry = save_result.data

    post_result = post_journal_entry(actor, entry.id)
    assert post_result.success, f"with-dimension JE should post: {post_result.error}"
