# backend/tests/test_s2f_c3_read_switch.py
"""ADR-0002 PR-C3 — the flag-gated canonical payout read-switch.

With ``STRIPE_CANONICAL_PAYOUT_READS=True`` every Stripe payout read surface
(stripe views, bank-match discovery/explain, reconcile variance math) must
serve the EXACT legacy contract — pinned by test_s2c — from the canonical
``ProviderPayout``/``ProviderPayoutLine`` read-models, with journal_entry_id
and verified match-state still joined from legacy (no canonical home until
PR-D / C4). The default (False) path stays byte-identical; test_s2c keeps
pinning it independently.

The switch must be real, not vacuous: mutating the canonical header changes
flag-ON output and leaves flag-OFF output alone.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.test import override_settings


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, arrival_date_gte=None, status=None):
        return self._payouts

    def list_balance_transactions(self, payout_id):
        return self._txns.get(payout_id, [])


@pytest.fixture
def canonical_payout(db, company, monkeypatch):
    """The s2c payout (net 141.15 + fees 8.85 = gross 150.00, two charges),
    synced through the real pull AND materialized into the canonical
    read-models (the on-commit projection trigger doesn't fire inside the
    test transaction, so process_pending runs explicitly)."""
    from platform_connectors.projections import PaymentsProjection
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
    payout = {"id": "po_1", "amount": 14115, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    txns = [
        {"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"},
        {"id": "txn_2", "type": "charge", "amount": 5000, "fee": 295, "source": "ch_2"},
        {"id": "txn_p", "type": "payout", "amount": -14115, "fee": 0, "source": "po_1"},
    ]
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {"po_1": txns}))
    sync_mod.sync_payouts(account)
    PaymentsProjection().process_pending(company)
    return account


# ── bank-match discovery (_get_stripe_payouts) ──────────────────────


def test_bank_match_header_identical_under_flag(db, company, canonical_payout):
    """The s2c-pinned header dict must be byte-identical under both flag values —
    including the legacy int pk and the (unset) journal_entry_id."""
    from bank_connector.matching import _get_stripe_payouts

    legacy_rows = _get_stripe_payouts(company)
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical_rows = _get_stripe_payouts(company)

    assert canonical_rows == legacy_rows
    assert len(canonical_rows) == 1
    row = canonical_rows[0]
    assert row["payout_id"] == "po_1"
    assert (row["gross_amount"], row["fees"], row["net_amount"]) == (
        Decimal("150.00"),
        Decimal("8.85"),
        Decimal("141.15"),
    )
    assert row["status"] == "paid"
    assert row["journal_entry_id"] is None


def test_flag_on_reads_canonical_not_legacy(db, company, canonical_payout):
    """Non-vacuous proof: a canonical-only mutation shows up ONLY under the flag."""
    from bank_connector.matching import _get_stripe_payouts
    from platform_connectors.models import ProviderPayout

    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_1")
    header.net_amount = Decimal("999.99")
    header.save()  # settings.TESTING bypasses the projection-only save guard

    assert _get_stripe_payouts(company)[0]["net_amount"] == Decimal("141.15")
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        assert _get_stripe_payouts(company)[0]["net_amount"] == Decimal("999.99")


def test_je_writeback_stays_legacy_and_visible_flag_on(db, company, canonical_payout):
    """The bank-match JE stamp lands on the legacy row (canonical has no such
    field) and canonical reads must surface it via the legacy join."""
    from bank_connector.matching import _get_stripe_payouts
    from stripe_connector.models import StripePayout

    je_public_id = uuid.uuid4()
    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    payout.journal_entry_id = je_public_id
    payout.save(update_fields=["journal_entry_id"])

    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        row = _get_stripe_payouts(company)[0]
    assert row["journal_entry_id"] == str(je_public_id)
    assert row["id"] == payout.id  # the pk namespace bank matching persists


def test_canonical_row_without_legacy_twin_is_skipped(db, company, canonical_payout):
    """A canonical header with no legacy twin can't join the pk/JE namespace —
    it must be skipped (loudly), never emitted mis-shaped."""
    from bank_connector.matching import _get_stripe_payouts
    from platform_connectors.models import ProviderPayout
    from stripe_connector.models import StripePayout

    StripePayout.objects.filter(company=company).delete()
    assert ProviderPayout.objects.filter(company=company, provider="stripe").exists()

    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        assert _get_stripe_payouts(company) == []


# ── reconcile_payout (stripe detail view + bank exception scan) ─────


def test_reconcile_payout_contract_flag_on(db, company, canonical_payout):
    """The exact s2c reconcile pins, under the flag: canonical header totals
    against the legacy line cache."""
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        recon = reconcile_payout(company, payout)

    assert recon.stripe_payout_id == "po_1"
    assert (recon.gross_amount, recon.fees, recon.net_amount) == (
        Decimal("150.00"),
        Decimal("8.85"),
        Decimal("141.15"),
    )
    assert recon.currency == "USD"
    assert recon.total_transactions == 2
    assert (recon.gross_variance, recon.fee_variance, recon.net_variance) == (
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    assert recon.matched_transactions == 0
    assert recon.unmatched_transactions == 2
    assert recon.status == "discrepancy"
    assert recon.discrepancies == ["2 unmatched transaction(s)"]


def test_reconcile_variances_compare_canonical_header(db, company, canonical_payout):
    """Flag ON, a drifted canonical header must surface as a variance against
    the legacy line sums — drift is surfaced, never hidden."""
    from platform_connectors.models import ProviderPayout
    from stripe_connector.models import StripePayout
    from stripe_connector.reconciliation import reconcile_payout

    header = ProviderPayout.objects.get(company=company, provider="stripe", payout_batch_id="po_1")
    header.gross_amount = Decimal("151.00")
    header.save()

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        recon = reconcile_payout(company, payout)
    assert recon.gross_variance == Decimal("1.00")
    assert "Gross variance: 1.00" in recon.discrepancies

    # Flag OFF is untouched by the canonical drift.
    recon_legacy = reconcile_payout(company, payout)
    assert recon_legacy.gross_variance == Decimal("0")


# ── the three stripe payout views ───────────────────────────────────


def _get_json(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.content
    return resp.json()


def test_payouts_list_view_identical_under_flag(db, company, canonical_payout, authenticated_client, owner_membership):
    legacy = _get_json(authenticated_client, "/api/stripe/payouts/")
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical = _get_json(authenticated_client, "/api/stripe/payouts/")

    assert canonical == legacy
    assert canonical["total"] == 1
    row = canonical["results"][0]
    assert row["stripe_payout_id"] == "po_1"
    assert (row["gross_amount"], row["fees"], row["net_amount"]) == ("150.00", "8.85", "141.15")
    assert row["stripe_status"] == "paid"
    assert row["account_name"] == "Acme Stripe"
    assert row["reconciliation_status"] == "unverified"
    assert (row["transactions_total"], row["transactions_verified"]) == (2, 0)
    assert row["journal_entry_id"] is None


def test_reconciliation_summary_view_identical_under_flag(
    db, company, canonical_payout, authenticated_client, owner_membership
):
    url = "/api/stripe/reconciliation/?date_from=2026-06-01&date_to=2026-06-30"
    legacy = _get_json(authenticated_client, url)
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical = _get_json(authenticated_client, url)

    assert canonical == legacy
    assert canonical["total_payouts"] == 1
    # Compare as Decimal: SQLite hands aggregate totals back un-quantized
    # (both paths, identically); Postgres returns exact 2dp.
    assert (
        Decimal(canonical["total_gross"]),
        Decimal(canonical["total_fees"]),
        Decimal(canonical["total_net"]),
    ) == (Decimal("150.00"), Decimal("8.85"), Decimal("141.15"))
    assert canonical["unverified_payouts"] == 1
    assert canonical["payouts"][0]["status"] == "paid"


def test_payout_reconciliation_detail_identical_under_flag(
    db, company, canonical_payout, authenticated_client, owner_membership
):
    # No local StripeCharge rows exist, so reconcile's auto-match writes
    # nothing — calling the GET twice is state-stable and comparable.
    url = "/api/stripe/reconciliation/po_1/"
    legacy = _get_json(authenticated_client, url)
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical = _get_json(authenticated_client, url)

    assert canonical == legacy
    assert canonical["stripe_payout_id"] == "po_1"
    assert canonical["status"] == "discrepancy"
    assert len(canonical["transactions"]) == 2
    assert {t["transaction_type"] for t in canonical["transactions"]} == {"charge"}


def test_detail_view_keyed_on_canonical_under_flag(
    db, company, canonical_payout, authenticated_client, owner_membership
):
    """Flag ON, existence is answered by the canonical model: a payout with no
    canonical header 404s even though the legacy row still exists."""
    from platform_connectors.models import ProviderPayout, ProviderPayoutLine

    ProviderPayoutLine.objects.filter(company=company, provider="stripe").delete()
    ProviderPayout.objects.filter(company=company, provider="stripe").delete()

    assert authenticated_client.get("/api/stripe/reconciliation/po_1/").status_code == 200
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        assert authenticated_client.get("/api/stripe/reconciliation/po_1/").status_code == 404
        # And the list is canonical-driven: the event-less legacy row is not served.
        assert _get_json(authenticated_client, "/api/stripe/payouts/")["total"] == 0


# ── payout explainer (bank_connector) ───────────────────────────────


def test_explain_stripe_payout_identical_under_flag(db, company, canonical_payout):
    from bank_connector.matching import _explain_stripe_payout
    from stripe_connector.models import StripePayout

    payout = StripePayout.objects.get(company=company, stripe_payout_id="po_1")
    legacy = _explain_stripe_payout(company, payout)
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical = _explain_stripe_payout(company, payout)

    assert canonical == legacy
    assert canonical["payout_external_id"] == "po_1"
    assert canonical["summary"]["computed_net"] == "141.15"
    assert canonical["summary"]["has_discrepancy"] is False
    assert canonical["transaction_count"] == 2
    assert all(t["verified"] is False for t in canonical["transactions"])
