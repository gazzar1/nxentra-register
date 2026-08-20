# tests/test_a4_dispositions.py
"""A4 dispositions PR: capability gates + preflight residue for the final
re-closure surfaces — manual AR, EDIM commit, FX revaluation, exchange-rate
maintenance, the fiscal-date / fiscal-year-lifecycle doors, the clinic /
properties verticals, and the operator-CLI refusals.

Proves technical enforcement at the runtime boundaries (direct backend calls
fail even with the frontend bypassed), that the supported platform limbs stay
open, that scheduled paths skip with zero mutation, and that the preflight
detects every seeded violation. NONE-profile companies retain existing
behavior except where a serialized admission wrap was a disclosed tradeoff.
"""

from datetime import date
from decimal import Decimal

import pytest
from test_a4_pilot_gates import _actor, _grant, _make_pilot

from accounts.models import Company
from accounts.pilot_policy import (
    Capability,
    PilotDeploymentRefused,
    PilotScopeBlocked,
    require_module_enable_allowed,
    require_no_pilot_deployment,
)

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


def _run_preflight(company, *, phase="setup", for_activation=False):
    from accounts.pilot_preflight import run_preflight

    return {v.code for v in run_preflight(company, phase=phase, for_activation=for_activation)}


def _seed_customer(company, *, currency="EGP", code="C-1"):
    from accounting.models import Customer

    return Customer.objects.create(company=company, code=code, name="Cust", currency=currency)


def _seed_posting_profile(company):
    from accounting.models import Account
    from sales.models import PostingProfile

    control = Account.objects.create(
        company=company, code="11901", name="AR Control", account_type="ASSET", status="ACTIVE", is_header=False
    )
    return PostingProfile.objects.create(
        company=company,
        code="PP-M",
        name="Manual",
        profile_type=PostingProfile.ProfileType.CUSTOMER,
        control_account=control,
    )


# --------------------------------------------------------------------------- #
# Manual AR — commands blocked; platform limb (auto_created) not MANUAL_AR-gated
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_manual_invoice_create_blocked_for_pilot(company, user, owner_membership):
    from sales.commands import create_sales_invoice

    _make_pilot(company)
    _grant(company, owner_membership, "sales.invoice.create")
    with pytest.raises(PilotScopeBlocked) as exc:
        create_sales_invoice(_actor(company), customer_id=1, posting_profile_id=1, lines=[{}])
    assert exc.value.capability == Capability.MANUAL_AR.value


@pytest.mark.django_db
def test_platform_invoice_limb_not_manual_ar_gated(company, user, owner_membership):
    """auto_created=True (the Shopify wrapper limb) must NOT hit the MANUAL_AR
    gate — it fails later on ordinary validation instead."""
    from sales.commands import create_sales_invoice

    _make_pilot(company)
    _grant(company, owner_membership, "sales.invoice.create")
    result = create_sales_invoice(
        _actor(company), customer_id=999999, posting_profile_id=999999, lines=[{}], auto_created=True
    )
    assert not result.success
    assert "Customer not found" in result.error


@pytest.mark.django_db
def test_manual_invoice_update_blocked_for_pilot(company, user, owner_membership):
    from sales.commands import update_sales_invoice

    _make_pilot(company)
    _grant(company, owner_membership, "sales.invoice.update")
    with pytest.raises(PilotScopeBlocked):
        update_sales_invoice(_actor(company), 999999)


@pytest.mark.django_db
def test_manual_credit_note_create_blocked_for_pilot(company, user, owner_membership):
    from sales.commands import create_credit_note

    _make_pilot(company)
    _grant(company, owner_membership, "sales.invoice.create")
    with pytest.raises(PilotScopeBlocked):
        create_credit_note(_actor(company), invoice_id=999999, lines=[{}])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "command,perm",
    [
        ("post_sales_invoice", "sales.invoice.post"),
        ("post_credit_note", "sales.invoice.post"),
        ("void_sales_invoice", "sales.invoice.void"),
        ("void_credit_note", "sales.invoice.post"),
    ],
)
def test_manual_post_void_boundaries_blocked_for_pilot(company, user, owner_membership, command, perm):
    import sales.commands as sales_commands

    _make_pilot(company)
    _grant(company, owner_membership, perm)
    fn = getattr(sales_commands, command)
    with pytest.raises(PilotScopeBlocked) as exc:
        fn(_actor(company), 999999)
    assert exc.value.capability == Capability.MANUAL_AR.value


@pytest.mark.django_db
def test_customer_receipt_blocked_for_pilot(company, user, owner_membership):
    from accounting.commands import record_customer_receipt

    _make_pilot(company)
    _grant(company, owner_membership, "journal.post")
    with pytest.raises(PilotScopeBlocked) as exc:
        record_customer_receipt(
            _actor(company),
            customer_id=1,
            receipt_date="2026-08-01",
            amount="10.00",
            bank_account_id=1,
            ar_control_account_id=1,
        )
    assert exc.value.capability == Capability.MANUAL_AR.value


# --------------------------------------------------------------------------- #
# EDIM — commit is the one blocked door
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_edim_commit_blocked_for_pilot(company, user, owner_membership):
    from edim.commands import commit_batch

    _make_pilot(company)
    _grant(company, owner_membership, "edim.commit_batches")
    with pytest.raises(PilotScopeBlocked) as exc:
        commit_batch(_actor(company), 999999)
    assert exc.value.capability == Capability.EDIM_FINANCIAL_COMMIT.value


@pytest.mark.django_db
def test_edim_commit_none_profile_unaffected(company, user, owner_membership):
    from edim.commands import commit_batch

    _grant(company, owner_membership, "edim.commit_batches")
    result = commit_batch(_actor(company), 999999)
    assert not result.success
    assert "Batch not found" in result.error


# --------------------------------------------------------------------------- #
# FX revaluation — scheduled task skips with zero mutation
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_revaluation_task_skips_pilot_company_zero_events(company, user, owner_membership):
    from accounting.tasks import _revalue_company
    from events.models import BusinessEvent

    _make_pilot(company)
    before = BusinessEvent.objects.filter(company=company).count()
    out = _revalue_company(company, date.today(), auto_reverse=True)
    assert out["status"] == "skipped_pilot_scope"
    assert out["capability"] == Capability.FX_REVALUATION.value
    assert BusinessEvent.objects.filter(company=company).count() == before


# --------------------------------------------------------------------------- #
# Exchange-rate maintenance — auto-fetch denies as rate-miss, no network
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_auto_fetch_denied_for_pilot_reads_as_rate_miss(company, monkeypatch):
    from accounting.models import ExchangeRate

    _make_pilot(company)

    def _no_network(*a, **k):  # pragma: no cover - would fail the test
        raise AssertionError("pilot auto-fetch must not touch the network")

    monkeypatch.setattr("requests.get", _no_network)
    assert ExchangeRate.get_rate(company, "USD", "EGP", date.today()) is None
    assert not ExchangeRate.objects.filter(company=company).exists()


@pytest.mark.django_db
def test_auto_fetch_still_attempted_for_none_profile(company, monkeypatch):
    from accounting.models import ExchangeRate

    calls = {"n": 0}

    def _fetch(*a, **k):
        calls["n"] += 1
        raise OSError("offline")

    monkeypatch.setattr("requests.get", _fetch)
    assert ExchangeRate.get_rate(company, "USD", "EGP", date.today()) is None
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# Fiscal doors — dates/pointer/range + fiscal-year lifecycle
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.parametrize(
    "command,args,perm",
    [
        ("set_period_range", {"fiscal_year": 2026, "open_from_period": 1, "open_to_period": 12}, "periods.configure"),
        ("set_current_period", {"fiscal_year": 2026, "period": 2}, "periods.configure"),
        (
            "update_period_dates",
            {"fiscal_year": 2026, "period": 2, "start_date": "2026-02-01", "end_date": "2026-02-28"},
            "periods.configure",
        ),
        ("close_fiscal_year", {"fiscal_year": 2026, "retained_earnings_account_code": "39999"}, "fiscal_year.close"),
        ("reopen_fiscal_year", {"fiscal_year": 2026, "reason": "test"}, "fiscal_year.reopen"),
    ],
)
def test_fiscal_structure_doors_blocked_for_pilot(company, user, owner_membership, command, args, perm):
    import accounting.commands as accounting_commands

    _make_pilot(company)
    _grant(company, owner_membership, perm)
    fn = getattr(accounting_commands, command)
    with pytest.raises(PilotScopeBlocked) as exc:
        fn(_actor(company), **args)
    assert exc.value.capability == Capability.CURRENCY_FISCAL_CHANGE.value


@pytest.mark.django_db
def test_onboarding_fiscal_rerun_blocked_for_pilot(company, user, owner_membership):
    from accounts.commands import complete_onboarding

    _make_pilot(company)
    _grant(company, owner_membership, "company.settings.update")
    with pytest.raises(PilotScopeBlocked) as exc:
        complete_onboarding(_actor(company), fiscal_year=2027)
    assert exc.value.capability == Capability.CURRENCY_FISCAL_CHANGE.value
    with pytest.raises(PilotScopeBlocked):
        complete_onboarding(_actor(company), fiscal_year_start_month=7)


@pytest.mark.django_db
def test_onboarding_non_fiscal_rerun_still_allowed_for_pilot(company, user, owner_membership):
    """A fiscal-free onboarding call (e.g. renaming) is not the frozen surface."""
    from accounts.commands import complete_onboarding

    _make_pilot(company)
    _grant(company, owner_membership, "company.settings.update")
    result = complete_onboarding(_actor(company), company_name="Renamed Pilot Co")
    assert result.success, result.error


# --------------------------------------------------------------------------- #
# Verticals — module-enable doors refuse; properties tasks skip with no writes
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
@pytest.mark.parametrize("module_key", ["clinic", "properties"])
def test_vertical_module_enable_refused_for_pilot(company, module_key):
    _make_pilot(company)
    with pytest.raises(PilotScopeBlocked) as exc:
        require_module_enable_allowed(company, [{"key": module_key, "is_enabled": True}])
    assert exc.value.capability == Capability.VERTICAL_MODULES.value


@pytest.mark.django_db
def test_properties_rent_task_skips_pilot_zero_mutation(company):
    from events.models import BusinessEvent
    from properties.models import Lease, Lessee, Property, RentScheduleLine
    from properties.tasks import _post_due

    _make_pilot(company)
    prop = Property.objects.create(company=company, code="P1", name="P", property_type="RESIDENTIAL")
    lessee = Lessee.objects.create(company=company, code="L1", lessee_type="INDIVIDUAL", display_name="L")
    lease = Lease.objects.create(
        company=company,
        contract_no="CT-1",
        property=prop,
        lessee=lessee,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        payment_frequency="MONTHLY",
        rent_amount=Decimal("100.00"),
        due_day_rule=1,
    )
    line = RentScheduleLine.objects.create(
        company=company,
        lease=lease,
        installment_no=1,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        due_date=date(2026, 1, 1),
        base_rent=Decimal("100.00"),
        total_due=Decimal("100.00"),
        outstanding=Decimal("100.00"),
        status=RentScheduleLine.ScheduleStatus.UPCOMING,
    )
    before = BusinessEvent.objects.filter(company=company).count()
    _post_due(company, line)
    line.refresh_from_db()
    assert line.status == RentScheduleLine.ScheduleStatus.UPCOMING, "pilot skip must not mutate the line"
    assert BusinessEvent.objects.filter(company=company).count() == before


@pytest.mark.django_db
def test_properties_expiry_task_skips_pilot_zero_events(company):
    from events.models import BusinessEvent
    from properties.tasks import _check_expiry_for_company

    _make_pilot(company)
    before = BusinessEvent.objects.filter(company=company).count()
    _check_expiry_for_company(company)
    assert BusinessEvent.objects.filter(company=company).count() == before


# --------------------------------------------------------------------------- #
# Operator-CLI refusals
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_require_no_pilot_deployment_semantics(company):
    require_no_pilot_deployment("unit-test")  # no pilot anywhere → passes
    _make_pilot(company)
    with pytest.raises(PilotDeploymentRefused):
        require_no_pilot_deployment("unit-test")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "command,args",
    [
        ("seed_demo_company", ["--company-slug", "whatever"]),
        ("seed_shopify_demo", ["--company-slug", "whatever"]),
        ("seed_test_csv_pack", ["--csv", "nonexistent.csv", "--company-slug", "whatever"]),
        ("seed_stripe_demo", []),
        ("seed_test_payout", []),
    ],
)
def test_seed_commands_refuse_on_pilot_deployment(company, command, args):
    from django.core.management import CommandError, call_command

    _make_pilot(company)
    with pytest.raises(CommandError, match="pilot"):
        call_command(command, *args)


@pytest.mark.django_db
def test_import_tenant_events_refuses_on_pilot_deployment(tmp_path, company):
    import json

    from django.core.management import CommandError, call_command

    _make_pilot(company)
    export = tmp_path / "export.json"
    export.write_text(
        json.dumps({"version": "1.0", "company": {"id": company.id, "name": company.name}, "events": []}),
        encoding="utf-8",
    )
    with pytest.raises(CommandError, match="pilot"):
        call_command("import_tenant_events", "--db-alias", "default", "--in", str(export))


@pytest.mark.django_db
def test_purge_orphan_je_events_refuses_before_any_delete(company):
    from django.core.management import CommandError, call_command

    from events.models import BusinessEvent

    _make_pilot(company)
    before = BusinessEvent.objects.filter(company=company).count()
    with pytest.raises(CommandError, match="pilot"):
        call_command("purge_orphan_je_events", "--company-id", company.id)
    assert BusinessEvent.objects.filter(company=company).count() == before


@pytest.mark.django_db
def test_rebuild_dimension_balances_refuses_for_pilot(company):
    from django.core.management import call_command

    _make_pilot(company)
    with pytest.raises(PilotScopeBlocked):
        call_command("rebuild_dimension_balances", "--company", company.name)


# --------------------------------------------------------------------------- #
# Admin surfaces — read-only
# --------------------------------------------------------------------------- #
def test_connector_and_projection_admins_deny_writes():
    from django.contrib.admin.sites import AdminSite

    from bank_connector.admin import BankAccountAdmin, BankStatementAdmin, BankTransactionAdmin
    from bank_connector.models import BankAccount, BankStatement, BankTransaction
    from projections.admin import PeriodAccountBalanceAdmin
    from projections.models import PeriodAccountBalance
    from stripe_connector.admin import StripeAccountAdmin
    from stripe_connector.models import StripeAccount

    site = AdminSite()
    for admin_cls, model in (
        (StripeAccountAdmin, StripeAccount),
        (BankAccountAdmin, BankAccount),
        (BankStatementAdmin, BankStatement),
        (BankTransactionAdmin, BankTransaction),
    ):
        instance = admin_cls(model, site)
        assert instance.has_add_permission(None) is False
        assert instance.has_change_permission(None) is False
        assert instance.has_delete_permission(None) is False
    assert PeriodAccountBalanceAdmin(PeriodAccountBalance, site).has_delete_permission(None) is False


# --------------------------------------------------------------------------- #
# Preflight — the new residue codes fire on seeded violations, and a clean
# company fires none of them
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_preflight_detects_non_egp_sales_document(company, owner_membership):
    from sales.models import SalesInvoice

    _make_pilot(company)
    customer = _seed_customer(company)
    profile = _seed_posting_profile(company)
    SalesInvoice.objects.create(
        company=company,
        invoice_number="INV-X1",
        invoice_date=date.today(),
        customer=customer,
        posting_profile=profile,
        currency="USD",
        auto_created=True,
    )
    codes = _run_preflight(company)
    assert "non_egp_sales_document_data" in codes


@pytest.mark.django_db
def test_preflight_detects_customer_currency(company, owner_membership):
    _make_pilot(company)
    _seed_customer(company, currency="USD")
    assert "customer_currency_not_egp" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_detects_manual_ar_document_and_financial_state(company, owner_membership):
    from accounting.models import JournalEntry
    from sales.models import SalesInvoice

    _make_pilot(company)
    customer = _seed_customer(company)
    profile = _seed_posting_profile(company)
    je = JournalEntry.objects.create(
        company=company, date=date.today(), period=8, memo="m", status=JournalEntry.Status.POSTED
    )
    SalesInvoice.objects.create(
        company=company,
        invoice_number="INV-M1",
        invoice_date=date.today(),
        customer=customer,
        posting_profile=profile,
        currency="EGP",
        auto_created=False,
        posted_journal_entry=je,
    )
    codes = _run_preflight(company)
    assert "manual_ar_document_state" in codes
    assert "manual_ar_financial_state" in codes


@pytest.mark.django_db
def test_preflight_platform_documents_are_not_manual_ar_state(company, owner_membership):
    from sales.models import SalesInvoice

    _make_pilot(company)
    customer = _seed_customer(company)
    profile = _seed_posting_profile(company)
    SalesInvoice.objects.create(
        company=company,
        invoice_number="INV-P1",
        invoice_date=date.today(),
        customer=customer,
        posting_profile=profile,
        currency="EGP",
        auto_created=True,
    )
    codes = _run_preflight(company)
    assert "manual_ar_document_state" not in codes
    assert "manual_ar_financial_state" not in codes


@pytest.mark.django_db
def test_preflight_detects_receipt_event(company, owner_membership):
    from events.models import BusinessEvent
    from events.types import EventTypes

    _make_pilot(company)
    BusinessEvent.objects.create(
        company=company,
        event_type=str(EventTypes.CUSTOMER_RECEIPT_RECORDED),
        aggregate_type="CustomerReceipt",
        aggregate_id="r-1",
        idempotency_key="a4-receipt-1",
        company_sequence=990101,
        data={},
    )
    assert "manual_ar_financial_state" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_detects_edim_state(company, owner_membership):
    from edim.models import IngestionBatch, SourceSystem, StagedRecord

    _make_pilot(company)
    src = SourceSystem.objects.create(
        company=company,
        code="S1",
        name="S",
        system_type="OTHER",
        trust_level=SourceSystem.TrustLevel.FINANCIAL,
    )
    batch = IngestionBatch.objects.create(
        company=company,
        source_system=src,
        ingestion_type="CSV",
        status=IngestionBatch.Status.VALIDATED,
    )
    StagedRecord.objects.create(
        batch=batch,
        company=company,
        row_number=1,
        raw_payload={},
        row_hash="h1",
        mapped_payload={"currency": "USD"},
    )
    codes = _run_preflight(company)
    assert "edim_commit_state" in codes
    assert "non_egp_edim_data" in codes


@pytest.mark.django_db
def test_preflight_detects_revaluation_journals(company, owner_membership):
    from accounting.models import JournalEntry

    _make_pilot(company)
    JournalEntry.objects.create(
        company=company,
        date=date.today(),
        period=8,
        memo="Currency revaluation as of 2026-08-01",
        status=JournalEntry.Status.INCOMPLETE,
        kind=JournalEntry.Kind.ADJUSTMENT,
    )
    assert "revaluation_data" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_detects_exchange_rate_rows(company, owner_membership):
    from accounting.models import ExchangeRate

    _make_pilot(company)
    ExchangeRate.objects.create(
        company=company,
        from_currency="USD",
        to_currency="EGP",
        rate=Decimal("48.0"),
        effective_date=date.today(),
        source="ECB (auto-fetched)",
    )
    assert "exchange_rate_data" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_detects_period_date_drift_and_overlap(company, owner_membership):
    from projections.models import FiscalPeriod

    _make_pilot(company)
    # P1 stretched over two months (drift + overlap with P2's month).
    FiscalPeriod.objects.create(
        company=company,
        fiscal_year=2031,
        period=1,
        start_date=date(2031, 1, 1),
        end_date=date(2031, 2, 28),
        period_type=FiscalPeriod.PeriodType.NORMAL,
    )
    FiscalPeriod.objects.create(
        company=company,
        fiscal_year=2031,
        period=2,
        start_date=date(2031, 2, 1),
        end_date=date(2031, 2, 28),
        period_type=FiscalPeriod.PeriodType.NORMAL,
    )
    codes = _run_preflight(company)
    assert "period_dates_drift" in codes
    assert "period_overlap" in codes


@pytest.mark.django_db
def test_preflight_accepts_clean_january_tiling_and_untyped_p13(company, owner_membership):
    """The frozen calendar passes, including a legacy P13 created without the
    ADJUSTMENT period_type (the onboarding path historically omitted it)."""
    import calendar as _calendar

    from projections.models import FiscalPeriod

    _make_pilot(company)
    for month in range(1, 13):
        FiscalPeriod.objects.create(
            company=company,
            fiscal_year=2031,
            period=month,
            start_date=date(2031, month, 1),
            end_date=date(2031, month, _calendar.monthrange(2031, month)[1]),
            period_type=FiscalPeriod.PeriodType.NORMAL,
        )
    FiscalPeriod.objects.create(
        company=company,
        fiscal_year=2031,
        period=13,
        start_date=date(2031, 12, 31),
        end_date=date(2031, 12, 31),
        # Deliberately NOT ADJUSTMENT-typed: legacy onboarding shape.
    )
    codes = _run_preflight(company)
    assert "period_dates_drift" not in codes
    assert "period_overlap" not in codes


@pytest.mark.django_db
def test_preflight_detects_vertical_enablement_and_state(company, owner_membership):
    from accounts.models import CompanyModule
    from clinic.models import Patient
    from properties.models import Property

    _make_pilot(company)
    CompanyModule.objects.create(company=company, module_key="clinic", is_enabled=True)
    CompanyModule.objects.create(company=company, module_key="properties", is_enabled=True)
    Patient.objects.create(company=company, code="PT1", name="P")
    Property.objects.create(company=company, code="PR1", name="P", property_type="RESIDENTIAL")
    codes = _run_preflight(company)
    for expected in ("clinic_module_enabled", "properties_module_enabled", "clinic_state", "property_state"):
        assert expected in codes


@pytest.mark.django_db
def test_preflight_detects_external_api_key(company, owner_membership):
    from events.api_keys import ExternalAPIKey

    _make_pilot(company)
    ExternalAPIKey.objects.create(
        company=company, name="k", source_system="ext", key_prefix="nx_test", key_hash="h" * 8
    )
    assert "external_api_key_present" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_detects_seeded_events(company, owner_membership):
    from events.models import BusinessEvent

    _make_pilot(company)
    BusinessEvent.objects.create(
        company=company,
        event_type="shopify.order_paid",
        aggregate_type="ShopifyOrder",
        aggregate_id="o-1",
        idempotency_key="a4-seeded-1",
        company_sequence=990201,
        data={},
        metadata={"source": "test_csv_pack"},
    )
    assert "seeded_event_residue" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_detects_rebuild_in_flight(company, owner_membership):
    from projections.models import ProjectionStatus

    _make_pilot(company)
    ProjectionStatus.objects.create(
        company=company, projection_name="account_balance", status=ProjectionStatus.Status.REBUILDING
    )
    assert "projection_rebuild_in_flight" in _run_preflight(company)


@pytest.mark.django_db
def test_preflight_clean_company_fires_none_of_the_new_codes(company, user, owner_membership):
    _make_pilot(company)
    codes = _run_preflight(company)
    new_codes = {
        "non_egp_sales_document_data",
        "customer_currency_not_egp",
        "manual_ar_document_state",
        "manual_ar_financial_state",
        "non_egp_edim_data",
        "edim_commit_state",
        "revaluation_data",
        "exchange_rate_data",
        "period_dates_drift",
        "period_overlap",
        "period_calendar_incomplete",
        "clinic_module_enabled",
        "properties_module_enabled",
        "clinic_state",
        "property_state",
        "external_api_key_present",
        "seeded_event_residue",
        "projection_rebuild_in_flight",
    }
    assert not (codes & new_codes), codes & new_codes


# --------------------------------------------------------------------------- #
# Route-level 403s — view exception handlers must NOT swallow the stable
# PilotScopeBlocked (Codex P2s on PR #123: patch's 500-with-traceback wrapper
# and the receipt view's 400 wrapper both converted the 403)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_route_invoice_patch_returns_403_for_pilot(company, user, owner_membership, api_client):
    from accounts.models import CompanyModule

    _make_pilot(company)
    _grant(company, owner_membership, "sales.invoice.update")
    CompanyModule.objects.create(company=company, module_key="sales", is_enabled=True)
    api_client.force_authenticate(user=user)
    resp = api_client.patch("/api/sales/invoices/999999/", {}, format="json")
    assert resp.status_code == 403, resp.content


@pytest.mark.django_db
def test_route_customer_receipt_returns_403_for_pilot(company, user, owner_membership, api_client):
    _make_pilot(company)
    _grant(company, owner_membership, "journal.post")
    api_client.force_authenticate(user=user)
    resp = api_client.post(
        "/api/accounting/customer-receipts/",
        {
            "customer_id": 1,
            "receipt_date": "2026-08-01",
            "amount": "10.00",
            "bank_account_id": 1,
            "ar_control_account_id": 1,
        },
        format="json",
    )
    assert resp.status_code == 403, resp.content


# --------------------------------------------------------------------------- #
# Codex round-2 fixes: vertical COMMANDS carry the serialized gate (not just
# the point-in-time ModuleEnabled permission), and preflight rejects an
# INCOMPLETE configured calendar (a deleted month row must not pass)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_clinic_command_blocked_for_pilot(company, user, owner_membership):
    from clinic.commands import create_patient

    _make_pilot(company)
    _grant(company, owner_membership, "clinic.patients.create")
    with pytest.raises(PilotScopeBlocked) as exc:
        create_patient(_actor(company), code="PT-X", name="P")
    assert exc.value.capability == Capability.VERTICAL_MODULES.value


@pytest.mark.django_db
def test_properties_command_blocked_for_pilot(company, user, owner_membership):
    from properties.commands import create_property

    _make_pilot(company)
    _grant(company, owner_membership, "properties.create")
    with pytest.raises(PilotScopeBlocked) as exc:
        create_property(_actor(company), code="PR-X", name="P", property_type="RESIDENTIAL")
    assert exc.value.capability == Capability.VERTICAL_MODULES.value


@pytest.mark.django_db
def test_preflight_detects_incomplete_configured_calendar(company, owner_membership):
    import calendar as _calendar

    from projections.models import FiscalPeriod, FiscalPeriodConfig

    _make_pilot(company)
    FiscalPeriodConfig.objects.create(company=company, fiscal_year=2032, period_count=12)
    for month in range(1, 12):  # deliberately missing December
        FiscalPeriod.objects.create(
            company=company,
            fiscal_year=2032,
            period=month,
            start_date=date(2032, month, 1),
            end_date=date(2032, month, _calendar.monthrange(2032, month)[1]),
            period_type=FiscalPeriod.PeriodType.NORMAL,
        )
    codes = _run_preflight(company)
    assert "period_calendar_incomplete" in codes
    assert "period_dates_drift" not in codes


# --------------------------------------------------------------------------- #
# Codex round-3 fixes: properties REPORT routes 403 under the pilot, and the
# purge command's REBUILDING markers keep activation excluded through the
# post-purge rebuild gap
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_route_properties_report_returns_403_for_pilot(company, user, owner_membership, api_client):
    from accounts.models import CompanyModule

    _make_pilot(company)
    # Even a stale enabled row must not reopen the route: the capability check
    # runs BEFORE the enablement lookup.
    CompanyModule.objects.create(company=company, module_key="properties", is_enabled=True)
    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/properties/reports/rent-roll/")
    assert resp.status_code == 403, resp.content


@pytest.mark.django_db
def test_purge_marks_projections_rebuilding_inside_admission_and_blocks_activation(company, owner_membership):
    """NONE-profile purge with --no-rebuild: the REBUILDING markers are set in
    the same admission transaction as the delete, so an activation slipping
    into the post-commit gap refuses on projection_rebuild_in_flight until the
    rebuild actually runs."""
    from django.core.management import call_command

    from events.models import BusinessEvent
    from projections.models import ProjectionStatus

    # An orphan candidate: a sales-invoice memo with no surviving invoice row.
    BusinessEvent.objects.create(
        company=company,
        event_type="journal_entry.posted",
        aggregate_type="JournalEntry",
        aggregate_id="je-orphan-1",
        idempotency_key="a4-purge-orphan-1",
        company_sequence=990301,
        data={"memo": "Sales Invoice INV-000042"},
    )
    call_command("purge_orphan_je_events", "--company-id", company.id, "--no-rebuild")
    assert not BusinessEvent.objects.filter(company=company, idempotency_key="a4-purge-orphan-1").exists()
    rebuilding = set(
        ProjectionStatus.objects.filter(company=company, status=ProjectionStatus.Status.REBUILDING).values_list(
            "projection_name", flat=True
        )
    )
    assert "journal_entry_read_model" in rebuilding and "account_balance" in rebuilding
    # The activation-mode preflight refuses while the drain is outstanding.
    assert "projection_rebuild_in_flight" in _run_preflight(company, for_activation=True)


@pytest.mark.django_db
def test_purge_rerun_verifies_convergence_before_clearing_markers(company, owner_membership):
    """Codex round-4: the marker clear is convergence-VERIFIED. After a
    --no-rebuild purge, a re-run clears the markers only when every drain has
    zero lag and no paused bookmark; a pending relevant event keeps the
    markers (and the activation refusal) in place."""
    from django.core.management import call_command

    from events.models import BusinessEvent
    from projections.models import ProjectionStatus

    BusinessEvent.objects.create(
        company=company,
        event_type="journal_entry.posted",
        aggregate_type="JournalEntry",
        aggregate_id="je-orphan-2",
        idempotency_key="a4-purge-orphan-2",
        company_sequence=990401,
        data={"memo": "Sales Invoice INV-000043"},
    )
    call_command("purge_orphan_je_events", "--company-id", company.id, "--no-rebuild")
    assert ProjectionStatus.objects.filter(company=company, status=ProjectionStatus.Status.REBUILDING).exists()

    # A surviving relevant event = nonzero lag for the never-drained
    # projections -> the recovery re-run must NOT clear the markers.
    BusinessEvent.objects.create(
        company=company,
        event_type="journal_entry.posted",
        aggregate_type="JournalEntry",
        aggregate_id="je-alive-1",
        idempotency_key="a4-purge-alive-1",
        company_sequence=990402,
        data={"memo": "Opening balances"},
    )
    call_command("purge_orphan_je_events", "--company-id", company.id)
    assert ProjectionStatus.objects.filter(company=company, status=ProjectionStatus.Status.REBUILDING).exists()
    assert "projection_rebuild_in_flight" in _run_preflight(company, for_activation=True)

    # Repair (remove the poison event so the drains can converge to zero lag),
    # then the re-run verifies and clears.
    BusinessEvent.objects.filter(company=company, idempotency_key="a4-purge-alive-1").delete()
    call_command("purge_orphan_je_events", "--company-id", company.id)
    assert not ProjectionStatus.objects.filter(company=company, status=ProjectionStatus.Status.REBUILDING).exists()
    assert "projection_rebuild_in_flight" not in _run_preflight(company, for_activation=True)
