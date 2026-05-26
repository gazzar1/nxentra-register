# tests/test_a85_auto_match_preview.py
"""
A85 chunk 2c (2026-05-26): preview + period override for auto_match_statement.

The settlement pre-pass inside auto_match_statement is the only step that
synthesizes new JEs (clearance JEs that drain Expected Bank Deposit). This
chunk:
- extracts the matching decisions into a pure-read planner
  (_plan_settlement_prepass_matches) so preview + execute share logic
- adds preview_auto_match() that returns the planned matches + period
  info + blockers without mutating state
- adds optional period_override params to auto_match_statement, gated on
  `accounting.je.override_period` permission + reason >= 10 chars
- writes one PeriodOverrideAudit row per planned match BEFORE the
  clearance JE is created so the audit trail survives partial failure

Test scenarios:
- preview happy path (open period, exact match)
- preview surfaces blocker when natural period is closed
- preview with valid override flips dry_run_safe and effective_period
- preview rejects bad override (returns blocker + override_warning)
- preview is read-only (no JEs created, no audit rows)
- auto_match with override writes audit row + JE lands in override period
- auto_match without override matches behave as before chunk 2c
- auto_match rejects override without permission / short reason
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import (
    auto_match_statement,
    preview_auto_match,
)
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
    PeriodOverrideAudit,
)
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from accounts.models import CompanyMembershipPermission, NxPermission
from projections.models import FiscalPeriod
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a85-2c-test.myshopify.com",
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


@pytest.fixture
def april_2026(db, company):
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=4,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


@pytest.fixture
def may_2026(db, company):
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=5,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 31),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


def _grant_override_permission(user, company, membership):
    with command_writes_allowed():
        perm, _ = NxPermission.objects.get_or_create(
            code="accounting.je.override_period",
            defaults={"name": "Override JE period", "module": "accounting"},
        )
        CompanyMembershipPermission.objects.get_or_create(
            membership=membership,
            company=company,
            permission=perm,
        )


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,A85-2C-555,2026-04-25
ORD-2,500.00,15.00,485.00,A85-2C-555,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="a85-2c.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_description, line_date):
    from accounting.bank_reconciliation import import_bank_statement

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
# Preview: pure-read behavior
# =============================================================================


@pytest.mark.django_db
def test_preview_returns_settlement_match_plan_for_open_period(
    shopify_setup, company, actor, merchant_bank, april_2026
):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: A85-2C-555",
        line_date=date(2026, 4, 26),
    )

    result = preview_auto_match(actor, statement.id)

    assert result.success
    data = result.data
    assert data["statement_id"] == statement.id
    assert data["unmatched_bank_lines"] == 1
    assert len(data["match_plan"]) == 1

    plan = data["match_plan"][0]
    assert plan["batch_id"] == "A85-2C-555"
    assert plan["bank_line_amount"] == "1455.00"
    assert plan["actual_amount"] == "1455.00"
    assert plan["expected_amount"] == "1455.00"
    assert plan["is_near_match"] is False
    assert plan["will_create_clearance_je"] is True
    assert plan["natural_period"]["resolved"] is True
    assert plan["natural_period"]["period"] == 4
    assert plan["natural_period"]["fiscal_year"] == 2026
    assert plan["natural_period"]["status"] == FiscalPeriod.Status.OPEN
    # No override → effective_period mirrors natural
    assert plan["effective_period"]["period"] == 4
    assert plan["effective_period"]["fiscal_year"] == 2026

    summary = data["summary"]
    assert summary["total_settlement_matches"] == 1
    assert summary["total_journal_entries_to_create"] == 1
    assert summary["total_clearance_amount"] == "1455.00"
    assert summary["exact_matches"] == 1
    assert summary["near_matches"] == 0
    assert summary["periods_affected"] == [
        {
            "fiscal_year": 2026,
            "period": 4,
            "period_name": "April 2026",
            "status": FiscalPeriod.Status.OPEN,
            "journal_entries": 1,
        }
    ]
    assert summary["blockers"] == []
    assert summary["dry_run_safe"] is True
    assert summary["override_requested"] is False


@pytest.mark.django_db
def test_preview_does_not_create_je_or_audit_rows(shopify_setup, company, actor, merchant_bank, april_2026):
    """Preview is read-only — verifies no clearance JE and no audit row
    land in the DB just from previewing."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )

    clearance_je_count_before = JournalEntry.objects.filter(
        company=company, source_module="payment_settlement_clearance"
    ).count()
    audit_count_before = PeriodOverrideAudit.objects.filter(company=company).count()

    result = preview_auto_match(actor, statement.id)
    assert result.success
    assert len(result.data["match_plan"]) == 1

    # No clearance JE, no audit row created.
    assert (
        JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").count()
        == clearance_je_count_before
    )
    assert PeriodOverrideAudit.objects.filter(company=company).count() == audit_count_before

    # And the bank line remains UNMATCHED.
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED


@pytest.mark.django_db
def test_preview_surfaces_blocker_when_natural_period_is_closed(
    shopify_setup, company, actor, merchant_bank, april_2026
):
    """When the bank line's value_date resolves to a CLOSED period, the
    preview marks dry_run_safe=False and lists the blocker."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )

    # Close April after the settlement JE is already posted (so the
    # natural period is "closed at preview time").
    with projection_writes_allowed():
        april_2026.status = FiscalPeriod.Status.CLOSED
        april_2026.save()

    result = preview_auto_match(actor, statement.id)
    assert result.success
    summary = result.data["summary"]
    assert summary["dry_run_safe"] is False
    assert any("CLOSED" in b for b in summary["blockers"]), summary["blockers"]


@pytest.mark.django_db
def test_preview_with_valid_override_flips_dry_run_safe(
    shopify_setup, company, user, actor, owner_membership, merchant_bank, april_2026, may_2026
):
    """When override targets an OPEN period and the operator has the
    permission + reason, the preview reports dry_run_safe=True and
    effective_period reflects the override."""
    _import_paymob_and_post(company)
    _grant_override_permission(user, company, owner_membership)

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )
    # Close April so the natural period would block.
    with projection_writes_allowed():
        april_2026.status = FiscalPeriod.Status.CLOSED
        april_2026.save()

    # Override to May (open) with a sufficient reason.
    actor_with_perm = ActorContext(
        user=user,
        company=company,
        membership=owner_membership,
        perms=frozenset(owner_membership.permissions.values_list("code", flat=True)),
    )
    result = preview_auto_match(
        actor_with_perm,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="April was closed for audit; posting clearance to May.",
    )
    assert result.success
    summary = result.data["summary"]
    assert summary["override_requested"] is True
    assert summary["override_warning"] is None
    assert summary["dry_run_safe"] is True, summary["blockers"]
    assert summary["blockers"] == []

    plan = result.data["match_plan"][0]
    assert plan["natural_period"]["period"] == 4
    assert plan["natural_period"]["status"] == FiscalPeriod.Status.CLOSED
    assert plan["effective_period"]["period"] == 5
    assert plan["effective_period"]["fiscal_year"] == 2026
    assert plan["effective_period"]["status"] == FiscalPeriod.Status.OPEN


@pytest.mark.django_db
def test_preview_rejects_override_without_permission(
    shopify_setup, company, actor, merchant_bank, april_2026, may_2026
):
    """When override is supplied but the user lacks the permission, the
    preview surfaces a blocker + override_warning instead of silently
    accepting."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )

    result = preview_auto_match(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="A long enough reason text here.",
    )
    assert result.success
    summary = result.data["summary"]
    assert summary["override_requested"] is True
    assert summary["override_warning"] is not None
    assert "override_period" in summary["override_warning"]
    assert summary["dry_run_safe"] is False
    assert any("override rejected" in b.lower() for b in summary["blockers"])


@pytest.mark.django_db
def test_preview_returns_empty_plan_when_no_settlement_entries_match(
    shopify_setup, company, actor, merchant_bank, april_2026
):
    """If the bank line amount/date doesn't align with any settlement JE,
    the plan is empty and dry_run_safe is False (no JEs to create)."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("999.99"),  # Doesn't match the settlement net of 1455
        line_description="random deposit",
        line_date=date(2026, 4, 26),
    )

    result = preview_auto_match(actor, statement.id)
    assert result.success
    assert result.data["match_plan"] == []
    summary = result.data["summary"]
    assert summary["total_settlement_matches"] == 0
    assert summary["dry_run_safe"] is False
    assert summary["blockers"] == []


# =============================================================================
# Execute: auto_match_statement with override params
# =============================================================================


@pytest.mark.django_db
def test_auto_match_with_override_writes_audit_row_and_lands_je_in_override_period(
    shopify_setup, company, user, owner_membership, merchant_bank, april_2026, may_2026
):
    """Happy path for the override on auto_match: per planned match, one
    PeriodOverrideAudit row is written AND the clearance JE's `period`
    column reflects the override (not the bank line's natural month)."""
    _import_paymob_and_post(company)
    _grant_override_permission(user, company, owner_membership)

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
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: A85-2C-555",
        line_date=date(2026, 4, 26),  # April
    )

    result = auto_match_statement(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="April was closed; posting clearance to May per audit.",
    )
    assert result.success, result.error
    assert result.data["settlement_matched"] == 1

    # Clearance JE landed in May 2026 (the override), not April (natural).
    clearance_je = JournalEntry.objects.get(company=company, source_module="payment_settlement_clearance")
    assert clearance_je.period == 5
    # Date is still the bank deposit date — only the period was overridden.
    assert clearance_je.date == date(2026, 4, 26)

    # Audit row written
    audits = PeriodOverrideAudit.objects.filter(company=company)
    assert audits.count() == 1
    audit = audits.first()
    assert audit.source == PeriodOverrideAudit.Source.RECON_MATCH
    assert audit.source_document_ref == "auto-match:settlement:A85-2C-555"
    assert audit.original_date == date(2026, 4, 26)
    assert audit.original_period == 4
    assert audit.override_period == 5
    assert audit.override_fiscal_year == 2026
    assert audit.user_id == user.id
    assert "audit" in audit.reason.lower()


@pytest.mark.django_db
def test_auto_match_without_override_writes_no_audit_row_and_uses_natural_period(
    shopify_setup, company, actor, merchant_bank, april_2026
):
    """Status quo: when no override is supplied, behavior is identical
    to pre-chunk-2c — no audit row, JE lands in the natural period."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )

    audit_count_before = PeriodOverrideAudit.objects.filter(company=company).count()

    result = auto_match_statement(actor, statement.id)
    assert result.success
    assert result.data["settlement_matched"] == 1

    # No audit row written
    assert PeriodOverrideAudit.objects.filter(company=company).count() == audit_count_before

    # Clearance JE lands in April (natural period)
    clearance_je = JournalEntry.objects.get(company=company, source_module="payment_settlement_clearance")
    assert clearance_je.period == 4
    assert clearance_je.date == date(2026, 4, 26)


@pytest.mark.django_db
def test_auto_match_override_rejected_without_permission(
    shopify_setup, company, actor, merchant_bank, april_2026, may_2026
):
    """No accounting.je.override_period → command returns a failure
    result; no JE created, no audit row."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="Long enough reason text here for the test.",
    )
    assert not result.success
    assert "override_period" in result.error

    # State unchanged
    assert JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").count() == 0
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 0
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED


@pytest.mark.django_db
def test_auto_match_override_rejected_with_short_reason(
    shopify_setup, company, user, owner_membership, merchant_bank, april_2026, may_2026
):
    """Reason < 10 chars → rejected at command layer even with permission."""
    _import_paymob_and_post(company)
    _grant_override_permission(user, company, owner_membership)

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
        line_description="A85-2C-555 deposit",
        line_date=date(2026, 4, 26),
    )

    result = auto_match_statement(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="short",
    )
    assert not result.success
    assert "at least 10 characters" in result.error
