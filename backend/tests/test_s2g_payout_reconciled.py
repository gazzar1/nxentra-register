# backend/tests/test_s2g_payout_reconciled.py
"""ADR-0002 PR-D1 — PROVIDER_PAYOUT_RECONCILED emission (dual-write phase).

The legacy StripePayoutTransaction.verified/local_charge match state gets an
event home: reconcile_payout + StripePayoutVerifyView snapshot the persisted
state as a full-state event (emit-on-change), and payout variance feeds the
ReconciliationException queue. Legacy writes stay byte-identical — pinned
independently by test_s2c/test_s2f, both of which must stay green untouched.

PR-D2 adds the projection consumer; the strict-xfail here flips green then.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from events.models import BusinessEvent
from events.types import EventTypes


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        return self._txns.get(payout_id, [])


def _sync_payout(company, monkeypatch, *, payout_id="po_1", txns=None):
    """Pull one payout through the real sync so the settlement event + legacy
    caches exist. Default: the s2c shape (net 141.15 + fees 8.85 = gross 150.00,
    two charges — lines sum exactly to the header, so variances are zero)."""
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    account = StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        display_name="Acme Stripe",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )
    arrival = int(datetime(2026, 6, 20, tzinfo=UTC).timestamp())
    if txns is None:
        txns = [
            {"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"},
            {"id": "txn_2", "type": "charge", "amount": 5000, "fee": 295, "source": "ch_2"},
            {"id": "txn_p", "type": "payout", "amount": -14115, "fee": 0, "source": payout_id},
        ]
    net_cents = sum(t["amount"] - t["fee"] for t in txns if t["type"] != "payout")
    payout = {"id": payout_id, "amount": net_cents, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {payout_id: txns}))
    sync_mod.sync_payouts(account)
    return account


def _seed_charge(company, account, charge_id, amount, fee):
    from stripe_connector.models import StripeCharge

    return StripeCharge.objects.create(
        company=company,
        account=account,
        stripe_charge_id=charge_id,
        amount=amount,
        fee=fee,
        net=amount - fee,
        currency="USD",
        charge_date=datetime(2026, 6, 19, tzinfo=UTC).date(),
        stripe_created_at=timezone.now(),
    )


def _reconciled_events(company):
    return list(
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.PROVIDER_PAYOUT_RECONCILED,
        ).order_by("company_sequence")
    )


# ── payload schema: validator-safe by construction ─────────────────


def test_snapshot_payload_passes_real_validation(db, company, monkeypatch):
    """Emit through the REAL validator path (no DISABLE_EVENT_VALIDATION):
    every field name — top-level and inside line_verdicts — must clear the
    name-keyed scalar validation. This is the guard for the reserved-name trap
    the adversarial review caught in the draft design (a verdict field named
    `kind` is enum-validated against JournalEntry.Kind and rejects "charge")."""
    from events.types import validate_event_payload
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciled_emit import build_reconciled_snapshot

    _sync_payout(company, monkeypatch)
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    snapshot = build_reconciled_snapshot(company, payout)
    snapshot["reconciled_at"] = timezone.now().isoformat()
    snapshot["source"] = "auto_reconcile"

    validate_event_payload(EventTypes.PROVIDER_PAYOUT_RECONCILED, snapshot)  # must not raise

    # Document the trap: nested dicts ARE walked, names ARE validated.
    from events.types import InvalidEventPayload

    poisoned = dict(snapshot)
    poisoned["line_verdicts"] = [{**snapshot["line_verdicts"][0], "kind": "charge"}]
    with pytest.raises(InvalidEventPayload):
        validate_event_payload(EventTypes.PROVIDER_PAYOUT_RECONCILED, poisoned)


# ── emit-on-change guard ────────────────────────────────────────────


def test_reconcile_emits_once_then_only_on_change(db, company, monkeypatch):
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    account = _sync_payout(company, monkeypatch)
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")

    # First reconcile: nothing matches (no local charges) → discrepancy snapshot.
    reconcile_payout(company, payout)
    events = _reconciled_events(company)
    assert len(events) == 1
    data = events[0].get_data()
    assert data["provider"] == "stripe"
    assert data["payout_batch_id"] == "po_1"
    assert data["source"] == "auto_reconcile"
    assert data["outcome"] == "discrepancy"
    assert (data["matched_count"], data["unmatched_count"], data["total_count"]) == (0, 2, 2)
    assert data["verified_count"] == 0
    # Lines sum exactly to the header in this fixture → zero variances; the
    # discrepancy comes from the unmatched lines. Free integrity identity:
    # net == gross − fee, always, for Stripe-derived events.
    assert (data["gross_variance"], data["fee_variance"], data["net_variance"]) == ("0.00", "0.00", "0.00")
    assert Decimal(data["net_variance"]) == Decimal(data["gross_variance"]) - Decimal(data["fee_variance"])
    assert data["currency"] == "USD"

    # Steady-state reconcile (the detail GET / 30-day scan case): no new event.
    reconcile_payout(company, payout)
    assert len(_reconciled_events(company)) == 1

    # State change (a charge arrives, auto-match persists verified) → new event.
    _seed_charge(company, account, "ch_1", Decimal("100.00"), Decimal("5.90"))
    reconcile_payout(company, payout)
    events = _reconciled_events(company)
    assert len(events) == 2
    data = events[-1].get_data()
    assert data["verified_count"] == 1
    assert data["matched_count"] == 1
    verdicts = {v["provider_line_ref"]: v for v in data["line_verdicts"]}
    assert verdicts["txn_1"]["verified"] is True
    assert verdicts["txn_1"]["match_kind"] == "charge"
    assert verdicts["txn_1"]["matched_ref"] == "ch_1"
    assert verdicts["txn_2"]["verified"] is False
    assert verdicts["txn_2"]["match_kind"] == "none"


def test_line_index_correlates_to_settlement_line_items(db, company, monkeypatch):
    """line_index is the correlation key: verdict[i] describes the settlement
    event's line_items[i], including the source-less balance txn whose
    order_id falls back to the bt id."""
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    txns = [
        {"id": "txn_a", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_a"},
        {"id": "txn_b", "type": "adjustment", "amount": -250, "fee": 0, "source": None},
        {"id": "txn_p", "type": "payout", "amount": -9150, "fee": 0, "source": "po_2"},
    ]
    _sync_payout(company, monkeypatch, payout_id="po_2", txns=txns)
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_2")
    reconcile_payout(company, payout)

    settlement = BusinessEvent.objects.get(company=company, idempotency_key="payment.settlement.received:stripe:po_2")
    line_items = settlement.get_data()["line_items"]
    [event] = _reconciled_events(company)
    verdicts = event.get_data()["line_verdicts"]

    assert [v["line_index"] for v in verdicts] == list(range(len(line_items)))
    by_ref = {v["provider_line_ref"]: v for v in verdicts}
    # The source-less adjustment: order_id fell back to the bt id ("txn_b"),
    # and the legacy twin's (source_id or stripe_balance_txn_id) matches it.
    assert line_items[1]["order_id"] == "txn_b"
    assert by_ref["txn_b"]["line_index"] == 1
    # Adjustment lines are auto-matched for header counts (reconcile's
    # in-memory rule) but NOT verified (reconcile never persists them).
    assert by_ref["txn_b"]["match_kind"] == "auto_type"
    assert by_ref["txn_b"]["verified"] is False
    assert event.get_data()["matched_count"] == 1  # the adjustment only
    assert event.get_data()["verified_count"] == 0


def test_no_settlement_event_no_emit(db, company, monkeypatch):
    """Payouts predating the settlement event stream (or seeded demo rows)
    have no canonical lines — nothing to stamp, so no event and no crash."""
    from stripe_connector.models import StripeAccount, StripePayout, StripePayoutTransaction
    from stripe_connector.reconciliation import reconcile_payout

    account = StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_legacy",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )
    payout = StripePayout.objects.create(
        company=company,
        account=account,
        stripe_payout_id="po_legacy",
        gross_amount=Decimal("50.00"),
        fees=Decimal("2.00"),
        net_amount=Decimal("48.00"),
        currency="USD",
        payout_date=datetime(2026, 5, 1, tzinfo=UTC).date(),
    )
    StripePayoutTransaction.objects.create(
        company=company,
        payout=payout,
        stripe_balance_txn_id="txn_legacy",
        transaction_type=StripePayoutTransaction.TransactionType.CHARGE,
        amount=Decimal("50.00"),
        fee=Decimal("2.00"),
        net=Decimal("48.00"),
        currency="USD",
        source_id="ch_legacy",
        verified=True,
    )

    recon = reconcile_payout(company, payout)
    assert recon.total_transactions == 1
    assert _reconciled_events(company) == []


# ── verify endpoint (first-ever coverage) ───────────────────────────


def test_verify_endpoint_emits_manual_snapshot(db, company, monkeypatch, authenticated_client, user, owner_membership):
    from stripe_connector.models import StripePayout

    account = _sync_payout(company, monkeypatch)
    _seed_charge(company, account, "ch_1", Decimal("100.00"), Decimal("5.90"))
    _seed_charge(company, account, "ch_2", Decimal("50.00"), Decimal("2.95"))

    resp = authenticated_client.post("/api/stripe/payouts/po_1/verify/")
    assert resp.status_code == 200
    # The pre-PR-D response contract, pinned so C4b can't silently change it.
    assert resp.json() == {"status": "verified", "matched": 2, "unmatched": 0}

    [event] = _reconciled_events(company)
    data = event.get_data()
    assert data["source"] == "manual_verify"
    assert data["triggered_by_user_id"] == user.id
    assert data["triggered_by_email"] == user.email
    assert data["outcome"] == "verified"  # all matched, zero variances
    assert data["verified_count"] == 2

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    assert payout.transactions.filter(verified=True).count() == 2  # legacy write untouched


# ── failure isolation ───────────────────────────────────────────────


def test_emit_failure_never_breaks_the_legacy_path(db, company, monkeypatch, authenticated_client, owner_membership):
    from stripe_connector import reconciled_emit
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    _sync_payout(company, monkeypatch)

    def _boom(**kwargs):
        raise RuntimeError("emitter down")

    monkeypatch.setattr(reconciled_emit, "emit_event_no_actor", _boom)

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    recon = reconcile_payout(company, payout)  # must not raise
    assert recon.status == "discrepancy"
    assert _reconciled_events(company) == []

    resp = authenticated_client.post("/api/stripe/payouts/po_1/verify/")
    assert resp.status_code == 200


# ── exception producer ──────────────────────────────────────────────


def test_variance_exception_dedupes_with_scan_and_autoresolves(db, company, monkeypatch):
    from bank_connector.exceptions import detect_payout_discrepancies
    from bank_connector.models import ReconciliationException
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    account = _sync_payout(company, monkeypatch)
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")

    # The 30-day scan runs first (it calls reconcile_payout, which now also
    # emits) — producer and scan must fold onto ONE open row.
    detect_payout_discrepancies(company)
    reconcile_payout(company, payout)
    open_rows = ReconciliationException.objects.filter(
        company=company,
        exception_type=ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
        reference_type="stripe_payout",
        reference_id=payout.id,
        status=ReconciliationException.Status.OPEN,
    )
    assert open_rows.count() == 1
    exc = open_rows.get()
    assert exc.details["payout_batch_id"] == "po_1"

    # Both charges arrive → next reconcile persists the matches, the changed
    # snapshot has outcome=verified → the open exception auto-resolves.
    _seed_charge(company, account, "ch_1", Decimal("100.00"), Decimal("5.90"))
    _seed_charge(company, account, "ch_2", Decimal("50.00"), Decimal("2.95"))
    reconcile_payout(company, payout)

    exc.refresh_from_db()
    assert exc.status == ReconciliationException.Status.RESOLVED
    assert "reconciled clean" in exc.resolution_note


def test_escalated_exception_is_never_machine_resolved(db, company, monkeypatch):
    """An operator-escalated row is parked for review — a machine verdict must
    not close it (matches auto_resolve_matched's convention)."""
    from bank_connector.models import ReconciliationException
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    account = _sync_payout(company, monkeypatch)
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    reconcile_payout(company, payout)  # discrepancy → opens the exception

    exc = ReconciliationException.objects.get(
        company=company,
        exception_type=ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
        reference_id=payout.id,
    )
    exc.status = ReconciliationException.Status.ESCALATED
    exc.save(update_fields=["status"])

    _seed_charge(company, account, "ch_1", Decimal("100.00"), Decimal("5.90"))
    _seed_charge(company, account, "ch_2", Decimal("50.00"), Decimal("2.95"))
    reconcile_payout(company, payout)  # outcome flips to verified

    exc.refresh_from_db()
    assert exc.status == ReconciliationException.Status.ESCALATED  # untouched


def test_dedup_hit_refreshes_stale_severity_and_amount(db, company):
    """The shared _create_exception fix: a variance that grew must refresh the
    open row's severity/amount/title instead of keeping the stale detection."""
    from datetime import date

    from bank_connector.exceptions import _create_exception
    from bank_connector.models import ReconciliationException

    common = {
        "exception_type": ReconciliationException.ExceptionType.PAYOUT_DISCREPANCY,
        "currency": "USD",
        "exception_date": date(2026, 6, 20),
        "platform": "stripe",
        "reference_type": "stripe_payout",
        "reference_id": 1,
        "reference_label": "Stripe payout po_x",
    }
    first = _create_exception(
        company,
        severity=ReconciliationException.Severity.HIGH,
        title="Payout discrepancy: -50.00 on 2026-06-20",
        description="small",
        amount=Decimal("50.00"),
        details={"net_variance": "-50.00"},
        **common,
    )
    second = _create_exception(
        company,
        severity=ReconciliationException.Severity.CRITICAL,
        title="Payout discrepancy: -5000.00 on 2026-06-20",
        description="big",
        amount=Decimal("5000.00"),
        details={"net_variance": "-5000.00"},
        **common,
    )
    assert second.pk == first.pk  # deduped, not duplicated
    second.refresh_from_db()
    assert second.severity == ReconciliationException.Severity.CRITICAL
    assert second.amount == Decimal("5000.00")
    assert "5000.00" in second.title
    assert second.description == "big"

    # Severity is monotonic: a shrinking-but-still-open variance keeps its
    # peak severity (amount/title do track the current fact).
    third = _create_exception(
        company,
        severity=ReconciliationException.Severity.LOW,
        title="Payout discrepancy: -10.00 on 2026-06-20",
        description="shrunk",
        amount=Decimal("10.00"),
        details={"net_variance": "-10.00"},
        **common,
    )
    assert third.pk == first.pk
    third.refresh_from_db()
    assert third.severity == ReconciliationException.Severity.CRITICAL  # no downgrade
    assert third.amount == Decimal("10.00")


# ── backfill command ────────────────────────────────────────────────


def test_backfill_captures_preexisting_verified_state(db, company, monkeypatch):
    """Pre-PR-D history: verified rows stamped before any emit existed get ONE
    snapshot per payout from the backfill; re-runs are no-ops."""
    from django.core.management import call_command

    from stripe_connector.models import StripePayout

    _sync_payout(company, monkeypatch)
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    payout.transactions.update(verified=True)  # legacy state, no event (pre-PR-D)

    call_command("stripe_reconciled_backfill", "--company-id", str(company.id))  # report-only
    assert _reconciled_events(company) == []

    call_command("stripe_reconciled_backfill", "--company-id", str(company.id), "--apply")
    [event] = _reconciled_events(company)
    data = event.get_data()
    assert data["source"] == "backfill"
    assert data["verified_count"] == 2

    # The backfill seeds EVENT history only — it must not flood the live
    # operator queue with (or re-open triaged) stale discrepancies. The
    # bounded 30-day scan stays the producer for historical variance.
    from bank_connector.models import ReconciliationException

    assert not ReconciliationException.objects.filter(company=company).exists()
    # Refund-style rule doesn't apply (both are charges without local_charge FK):
    # verified=True is captured (DB truth) even though match_kind is "none".
    assert all(v["verified"] for v in data["line_verdicts"])

    call_command("stripe_reconciled_backfill", "--company-id", str(company.id), "--apply")
    assert len(_reconciled_events(company)) == 1  # unchanged → guard suppressed


# ── PR-D2 gate (promoted from strict-xfail when D2 landed, 2026-07-05) ──


def test_payments_projection_consumes_reconciled_events(db):
    """Was xfail(strict) in PR-D1; PR-D2 wired the consumer — now a permanent
    guard that the reconciled stream can never silently drop out of the
    payments projection (test_s2h covers the stamping behavior)."""
    from platform_connectors.projections import PaymentsProjection

    assert EventTypes.PROVIDER_PAYOUT_RECONCILED in PaymentsProjection().consumes
