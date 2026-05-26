# tests/test_a85_chunk6_end_to_end.py
"""
A85 chunk 6 (2026-05-26): end-to-end integration tests for the override
+ audit contract.

Validates the entire flow that a merchant goes through:
1. Operator previews the action via the dry-run endpoint.
2. Preview surfaces a CLOSED-period blocker.
3. Operator picks an override and previews again with override params.
4. Second preview is dry_run_safe=True and shows the effective period.
5. Operator commits via the execute endpoint with the same override.
6. The clearance JE lands in the override period.
7. A PeriodOverrideAudit row is written for the action.
8. The row is visible via GET /api/accounting/period-overrides/.

Also covers the audit-in-same-txn hardening: if a settlement_import
batch's `emit_event_no_actor` raises, the audit row for THAT batch
rolls back too (the audit log only contains entries for events that
actually landed).
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounting.bank_reconciliation import (
    auto_match_statement,
    import_bank_statement,
    preview_auto_match,
)
from accounting.models import (
    Account,
    JournalEntry,
    PeriodOverrideAudit,
)
from accounting.period_override_audit_views import PeriodOverrideAuditListView
from accounting.settlement_imports import (
    import_settlement_csv,
)
from accounts.authz import ActorContext
from accounts.models import CompanyMembershipPermission, NxPermission
from projections.models import FiscalPeriod
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,A85-6-E2E,2026-04-25
ORD-2,500.00,15.00,485.00,A85-6-E2E,2026-04-25
"""


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a85-6-e2e.myshopify.com",
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


def _grant_override(user, company, membership):
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


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="a85-6-e2e.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_description, line_date):
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


@pytest.fixture
def may_2026(db, company):
    """Override target — May 2026 is auto-created by the conftest's
    auto_fiscal_periods fixture and starts OPEN."""
    return FiscalPeriod.objects.get(company=company, fiscal_year=2026, period=5)


@pytest.fixture
def april_2026(db, company):
    return FiscalPeriod.objects.get(company=company, fiscal_year=2026, period=4)


# =============================================================================
# End-to-end: preview → close period → re-preview with override → commit
# =============================================================================


@pytest.mark.django_db
def test_e2e_preview_then_override_then_commit_then_audit_visible(
    shopify_setup, company, user, owner_membership, merchant_bank, april_2026, may_2026
):
    """The merchant journey through chunk 2c:
    preview shows blocker → operator picks override → re-preview is safe
    → commit lands JE in override period + writes audit → /period-overrides
    surfaces the audit row.
    """
    _grant_override(user, company, owner_membership)
    _import_paymob_and_post(company)

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
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: A85-6-E2E",
        line_date=date(2026, 4, 26),
    )

    # Step 1: close April so the natural period would be blocked.
    with projection_writes_allowed():
        april_2026.status = FiscalPeriod.Status.CLOSED
        april_2026.save()

    # Step 2: first preview — natural period CLOSED, blocker visible.
    first_preview = preview_auto_match(actor, statement.id)
    assert first_preview.success
    assert first_preview.data["summary"]["dry_run_safe"] is False
    assert any("CLOSED" in b for b in first_preview.data["summary"]["blockers"])

    # Step 3: operator picks May override + supplies a reason → second
    # preview reports dry_run_safe=True and effective_period reflects May.
    second_preview = preview_auto_match(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="April closed for audit; settlement clears to May.",
    )
    assert second_preview.success
    summary = second_preview.data["summary"]
    assert summary["dry_run_safe"] is True
    assert summary["override_requested"] is True
    assert summary["override_warning"] is None
    plan = second_preview.data["match_plan"][0]
    assert plan["effective_period"]["period"] == 5

    # Step 4: commit auto_match with the same override.
    commit = auto_match_statement(
        actor,
        statement.id,
        period_override=5,
        fiscal_year_override=2026,
        override_reason="April closed for audit; settlement clears to May.",
    )
    assert commit.success
    assert commit.data["settlement_matched"] == 1

    # Step 5: clearance JE lands in May 2026 (override), dated April 26 (truth).
    clearance_je = JournalEntry.objects.get(company=company, source_module="payment_settlement_clearance")
    assert clearance_je.period == 5
    assert clearance_je.date == date(2026, 4, 26)

    # Step 6: audit row exists, linked to the clearance JE.
    audit = PeriodOverrideAudit.objects.get(company=company)
    assert audit.source == PeriodOverrideAudit.Source.RECON_MATCH
    assert audit.override_period == 5
    assert audit.override_fiscal_year == 2026
    assert audit.journal_entry_id == clearance_je.id, (
        "Chunk 6: audit row must be linked to the clearance JE since both commit in the same transaction."
    )

    # Step 7: GET /api/accounting/period-overrides/ surfaces the row.
    factory = APIRequestFactory()
    request = factory.get("/api/accounting/period-overrides/")
    force_authenticate(request, user=user)
    user.active_company = company
    user.save(update_fields=["active_company"])
    response = PeriodOverrideAuditListView.as_view()(request)
    assert response.status_code == 200, response.data
    assert response.data["total_count"] == 1
    row = response.data["results"][0]
    assert row["source"] == "RECON_MATCH"
    assert row["override"] == {"period": 5, "fiscal_year": 2026}
    assert row["journal_entry_id"] == clearance_je.id


# =============================================================================
# Chunk 6 audit-in-same-txn hardening
# =============================================================================


@pytest.mark.django_db
def test_settlement_import_audit_row_rolls_back_when_event_emit_fails(
    company, user, owner_membership, april_2026, may_2026
):
    """A85 chunk 6: each batch's (audit, event) pair commits atomically.
    If emit_event_no_actor raises, the matching audit row rolls back —
    the audit log never contains a row whose event didn't land.
    """
    _grant_override(user, company, owner_membership)

    audit_before = PeriodOverrideAudit.objects.filter(company=company).count()

    with patch(
        "accounting.settlement_imports.emit_event_no_actor",
        side_effect=RuntimeError("simulated event-store outage"),
    ):
        with pytest.raises(RuntimeError, match="simulated event-store outage"):
            import_settlement_csv(
                company=company,
                provider_normalized_code="paymob",
                file_content=PAYMOB_CSV,
                source_filename="hardening.csv",
                period_override=5,
                fiscal_year_override=2026,
                override_reason="Audit-rollback hardening check.",
                override_user=user,
            )

    # The audit row that was about to be written rolled back along with
    # the failed event emission. The audit log is consistent with the
    # event log: no orphan rows.
    audit_after = PeriodOverrideAudit.objects.filter(company=company).count()
    assert audit_after == audit_before


@pytest.mark.django_db
def test_auto_match_audit_row_only_written_after_clearance_je_posts(
    shopify_setup, company, user, owner_membership, merchant_bank, april_2026, may_2026
):
    """A85 chunk 6: the auto-match audit row is written AFTER the
    clearance JE successfully posts. If the JE creation fails, the audit
    row is NOT written (and the outer @transaction.atomic ensures the
    bank line stays UNMATCHED).
    """
    _grant_override(user, company, owner_membership)
    _import_paymob_and_post(company)

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
        line_description="A85-6-E2E deposit",
        line_date=date(2026, 4, 26),
    )

    audit_before = PeriodOverrideAudit.objects.filter(company=company).count()

    # Force the clearance JE creator to return None (simulates a post
    # failure such as a missing mapping). The override is active, so
    # without the chunk 6 hardening an orphan audit row would land.
    with patch(
        "reconciliation.commands._create_settlement_clearance_je",
        return_value=None,
    ):
        result = auto_match_statement(
            actor,
            statement.id,
            period_override=5,
            fiscal_year_override=2026,
            override_reason="Should NOT result in an orphan audit row.",
        )

    # Auto-match returns success but matched count is zero (the planner
    # found one match, the apply skipped because JE creation failed).
    assert result.success
    assert result.data["settlement_matched"] == 0

    # No audit row should exist — JE never landed.
    audit_after = PeriodOverrideAudit.objects.filter(company=company).count()
    assert audit_after == audit_before, "Audit log must not contain an entry whose clearance JE failed to post."
