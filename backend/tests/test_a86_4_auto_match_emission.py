# tests/test_a86_4_auto_match_emission.py
"""A86.4 + A86.7b: auto_match_statement emits ReconciliationMatchConfirmed.

A86.4 made auto_match_statement emit a ReconciliationMatchConfirmed event
for every confirmed match. A86.7b made ReconciliationProjection the
sole writer of bank-line match state, driven by those events.

These tests now assert on the canonical fields the projection writes
(match_status / matched_journal_line / match_confidence) rather than
the dropped event_* shadow fields, and rely on auto_match_statement's
internal sync-projection trigger (no separate process_pending call
needed in production paths).

Scenarios covered:

- Settlement prepass: emits MatchConfirmed (match_kind="settlement_clearance")
- Platform-payout prepass: emits MatchConfirmed (match_kind="platform_payout")
- Generic GL match: emits MatchConfirmed (match_kind="generic_gl")
- Difference > 0: emission carries the difference; projection sets
  status to MATCHED_WITH_DIFFERENCE
- Auto-match with period override (A85 chunk 2c): emission still works,
  audit row + match event coexist correctly
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import import_bank_statement
from accounting.models import Account, BankStatementLine, JournalEntry
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import auto_match_statement

# =============================================================================
# Fixtures (mirror test_a14b for compatibility with the settlement-prepass
# flow that drives the most realistic exercise of the new emission)
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a86-4-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-A,1000.00,30.00,970.00,A86-4-555,2026-04-25
ORD-B,500.00,15.00,485.00,A86-4-555,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="a86-4.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(
    company,
    actor,
    merchant_bank,
    *,
    line_amount,
    line_description,
    line_date,
):
    period_start = line_date - timedelta(days=2)
    period_end = line_date + timedelta(days=2)
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0"),
        closing_balance=line_amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "amount": str(line_amount),
                "description": line_description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, f"statement import failed: {result.error}"
    return result.data["statement"]


# =============================================================================
# Settlement prepass: emission + convergence
# =============================================================================


@pytest.mark.django_db
def test_settlement_prepass_emits_match_confirmed_event(shopify_setup, company, actor, merchant_bank):
    """Settlement-prepass match produces one ReconciliationMatchConfirmed
    event per confirmed match, with match_kind='settlement_clearance'."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: A86-4-555",
        line_date=date(2026, 4, 26),
    )

    events_before = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()

    result = auto_match_statement(actor, statement.id)
    assert result.success
    assert result.data["settlement_matched"] == 1

    events_after = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).order_by("company_sequence")
    assert events_after.count() == events_before + 1

    new_event = events_after.last()
    data = new_event.get_data()
    assert data["match_kind"] == "settlement_clearance"
    assert data["confirmation_kind"] == "auto"
    assert data["confidence"] == "100"  # exact batch-id match → CONFIDENCE_EXACT
    assert data["difference_amount"] == "0"
    # bank_line_public_id points to the bank line, journal_line points to
    # the clearance JE's bank-side line — the same line bank_line.matched_journal_line
    # was set to by the direct-mutation path.
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert data["bank_line_public_id"] == str(bank_line.public_id)
    assert data["journal_line_public_id"] == str(bank_line.matched_journal_line.public_id)


@pytest.mark.django_db
def test_settlement_prepass_writes_canonical_match_state(shopify_setup, company, actor, merchant_bank):
    """A86.7b: after auto-match, the projection (run synchronously inside
    auto_match_statement) has written the canonical match_status /
    matched_journal_line / match_confidence fields."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE A86-4-555",
        line_date=date(2026, 4, 26),
    )

    auto_match_statement(actor, statement.id)

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.matched_journal_line is not None
    assert bank_line.match_confidence == Decimal("100")


@pytest.mark.django_db
def test_settlement_prepass_near_match_writes_with_difference(shopify_setup, company, actor, merchant_bank):
    """A16 near-match: bank deposit short by less than tolerance → match
    confirmed with difference_amount > 0; projection writes
    MATCHED_WITH_DIFFERENCE status."""
    _import_paymob_and_post(company)
    # Bank amount 1400 vs expected 1455 → 55 short, ~3.8% — within 15% tolerance.
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1400.00"),
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: A86-4-555 (short)",
        line_date=date(2026, 4, 26),
    )

    auto_match_statement(actor, statement.id)

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
    assert bank_line.difference_amount == Decimal("55.00")

    # The emitted event carries the difference for downstream consumers.
    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        )
        .order_by("-company_sequence")
        .first()
    )
    assert event.get_data()["difference_amount"] == "55.00"


# =============================================================================
# Platform-payout prepass: emission with match_kind='platform_payout'
# =============================================================================


@pytest.mark.django_db
def test_platform_prepass_emits_match_confirmed_event(shopify_setup, company, actor, merchant_bank):
    """When the Shopify payout prepass finds a match, it emits a
    ReconciliationMatchConfirmed with match_kind='platform_payout'."""
    from shopify_connector.models import ShopifyPayout

    # Set up a Shopify payout + its corresponding JE so the prepass
    # has something to match. The settlement prepass is bypassed (no
    # settlement events emitted), so the platform path is exercised.
    payout_date_value = date(2026, 4, 26)
    payout_net = Decimal("2500.00")

    # ShopifyPayout aligned with the bank deposit
    payout = ShopifyPayout.objects.create(
        company=company,
        store=shopify_setup["store"],
        shopify_payout_id=12345,  # BigIntegerField
        payout_date=payout_date_value,
        gross_amount=payout_net,
        net_amount=payout_net,
        fees=Decimal("0"),
        currency="USD",
        shopify_status="paid",
    )

    # The bank-side JE that the prepass looks for. _platform_prepass_match
    # locates the journal line by memo="Shopify payout: {shopify_payout_id}".
    with projection_writes_allowed():
        from accounting.models import JournalLine

        payout_je = JournalEntry.objects.create(
            company=company,
            date=payout_date_value,
            period=4,
            memo=f"Shopify payout: {payout.shopify_payout_id}",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-PO-A86-4",
        )
        JournalLine.objects.create(
            company=company,
            entry=payout_je,
            line_no=1,
            account=merchant_bank,
            debit=payout_net,
            credit=Decimal("0"),
        )

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=payout_net,
        line_description="Shopify Payments deposit",
        line_date=payout_date_value,
    )

    result = auto_match_statement(actor, statement.id)
    assert result.success
    assert result.data["platform_matched"] == 1

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        )
        .order_by("-company_sequence")
        .first()
    )
    assert event is not None
    data = event.get_data()
    assert data["match_kind"] == "platform_payout"
    assert data["confirmation_kind"] == "auto"

    # Canonical write applied via the projection (run synchronously by
    # auto_match_statement).
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.matched_journal_line is not None


# =============================================================================
# Generic GL match: emission with match_kind='generic_gl'
# =============================================================================


@pytest.mark.django_db
def test_generic_gl_match_emits_match_confirmed_event(company, actor, merchant_bank):
    """When neither settlement-prepass nor platform-prepass match,
    the generic GL loop falls back to amount+date matching against
    any unreconciled JL on the bank account. Each such match also
    emits MatchConfirmed (match_kind='generic_gl')."""
    # Skip Shopify setup entirely so prepass paths find nothing.
    from accounting.models import JournalLine

    # Create a pre-existing JE with an unreconciled bank-side line that
    # matches the bank deposit's amount + date.
    deposit_date = date(2026, 4, 26)
    deposit_amount = Decimal("777.00")
    with projection_writes_allowed():
        je = JournalEntry.objects.create(
            company=company,
            date=deposit_date,
            period=4,
            memo="Manual JE before bank deposit",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-GEN-A86-4",
        )
        bank_jl = JournalLine.objects.create(
            company=company,
            entry=je,
            line_no=1,
            account=merchant_bank,
            debit=deposit_amount,
            credit=Decimal("0"),
        )

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=deposit_amount,
        line_description="Generic bank deposit (no platform reference)",
        line_date=deposit_date,
    )

    result = auto_match_statement(actor, statement.id)
    assert result.success
    # Settlement prepass and platform prepass both miss; the generic
    # GL loop picks it up.
    assert result.data["settlement_matched"] == 0
    assert result.data["platform_matched"] == 0
    assert result.data["matched"] == 1

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        )
        .order_by("-company_sequence")
        .first()
    )
    assert event is not None
    data = event.get_data()
    assert data["match_kind"] == "generic_gl"
    assert data["journal_line_public_id"] == str(bank_jl.public_id)

    # Canonical write applied via the sync projection trigger
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.matched_journal_line_id == bank_jl.id


# =============================================================================
# Period override (A85 chunk 2c) coexists with the new emission
# =============================================================================


@pytest.mark.django_db
def test_auto_match_with_period_override_emits_event_and_writes_audit_row(
    shopify_setup, company, user, owner_membership, merchant_bank
):
    """Period override (A85 chunk 2c) + reconciliation emission (A86.4)
    coexist. Both an audit row AND a MatchConfirmed event are produced;
    convergence still holds after projection runs."""
    from accounting.models import PeriodOverrideAudit
    from accounts.models import CompanyMembershipPermission, NxPermission
    from projections.models import FiscalPeriod
    from projections.write_barrier import command_writes_allowed

    _import_paymob_and_post(company)

    # Grant override permission + ensure May 2026 is open as override target.
    with command_writes_allowed():
        perm, _ = NxPermission.objects.get_or_create(
            code="accounting.je.override_period",
            defaults={"name": "Override JE period", "module": "accounting"},
        )
        CompanyMembershipPermission.objects.get_or_create(
            membership=owner_membership,
            company=company,
            permission=perm,
        )

    # April + May 2026 are pre-created by conftest auto_fiscal_periods; close April.
    april = FiscalPeriod.objects.get(company=company, fiscal_year=2026, period=4)
    with projection_writes_allowed():
        april.status = FiscalPeriod.Status.CLOSED
        april.save()

    actor = ActorContext(
        user=user,
        company=company,
        membership=owner_membership,
        perms=frozenset(owner_membership.permissions.values_list("code", flat=True)),
    )
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE A86-4-555 (override-to-May)",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="April closed for audit review; clear settlement to May.",
    )
    assert result.success

    # Audit row written (chunk 6 audit-after-JE-success ordering)
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 1
    # MatchConfirmed event emitted
    assert (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        ).count()
        == 1
    )

    # Canonical match state applied via the sync projection trigger
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED


# =============================================================================
# Idempotency: re-running auto_match doesn't double-emit
# =============================================================================


@pytest.mark.django_db
def test_rerunning_auto_match_does_not_double_emit(shopify_setup, company, actor, merchant_bank):
    """Once a bank line is matched, it's no longer UNMATCHED and the
    second auto_match invocation skips it — no second emission."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE A86-4-555",
        line_date=date(2026, 4, 26),
    )

    auto_match_statement(actor, statement.id)
    first_count = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()
    assert first_count == 1

    auto_match_statement(actor, statement.id)
    second_count = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()
    assert second_count == 1, "Second auto_match should not emit again (bank line no longer UNMATCHED)"
