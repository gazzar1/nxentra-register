# backend/tests/test_a143_stripe_dashboard_summary.py
"""A143 — /stripe dashboard tiles read canonical payout fees, not charge-side zeros.

The old dashboard summed ``StripeCharge.fee`` client-side, which is 0 by design
(webhooks carry no fee — real fees only become known at payout time from
balance transactions). The new GET /api/stripe/summary/ serves:
- charge counts + per-currency gross revenue via DB aggregates (the charges
  list endpoint is capped at 100 rows, so client-side sums under-counted), and
- per-currency fee totals from the canonical ProviderPayout headers
  (provider="stripe" only) — the same numbers the settlement JE posts to the
  fee account.

Also pins the A143 currency field on the reconciliation summary endpoint:
single payout currency in range → that code; mixed → "" (the totals are a
blend and the frontend says so instead of mislabeling them).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.test import override_settings

from platform_connectors.models import ProviderPayout, derive_provider_payout_id
from stripe_connector.models import StripeAccount, StripeCharge, StripePayout


@pytest.fixture
def stripe_account(db, company):
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_a143",
        display_name="Acme Stripe",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )


def _mk_charge(company, account, charge_id, amount, *, currency="USD", status=StripeCharge.Status.PROCESSED):
    return StripeCharge.objects.create(
        company=company,
        account=account,
        stripe_charge_id=charge_id,
        amount=Decimal(amount),
        net=Decimal(amount),
        currency=currency,
        charge_date=date(2026, 6, 15),
        stripe_created_at=datetime(2026, 6, 15, tzinfo=UTC),
        status=status,
    )


def _mk_provider_payout(company, provider, batch_id, fees, *, currency="USD", payout_date=date(2026, 6, 20)):
    # settings.TESTING bypasses the sole-writer save guard (same pattern as
    # the s2f read-switch tests).
    return ProviderPayout.objects.create(
        id=derive_provider_payout_id(company.id, provider, batch_id),
        company=company,
        provider=provider,
        payout_batch_id=batch_id,
        gross_amount=Decimal(fees) + Decimal("100.00"),
        fees=Decimal(fees),
        net_amount=Decimal("100.00"),
        currency=currency,
        payout_date=payout_date,
        provider_status="paid",
    )


def _get_json(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.content
    return resp.json()


def _decimalized(entries):
    """[(currency, Decimal(amount), payouts?), ...] for aggregate asserts."""
    out = []
    for e in entries:
        row = (e["currency"], Decimal(e["amount"])) + ((e["payouts"],) if "payouts" in e else ())
        out.append(row)
    return out


# ── GET /api/stripe/summary/ ────────────────────────────────────────


def test_fees_come_from_canonical_stripe_payouts_only(
    db, company, stripe_account, authenticated_client, owner_membership
):
    """Fees aggregate ProviderPayout provider='stripe' rows only — a Paymob
    payout on the same company must not leak into the Stripe dashboard."""
    _mk_provider_payout(company, "stripe", "po_1", "6.40")
    _mk_provider_payout(company, "stripe", "po_2", "3.20")
    _mk_provider_payout(company, "paymob", "PAYMOB-BATCH-1", "38.50")

    data = _get_json(authenticated_client, "/api/stripe/summary/")

    # Compare as Decimal: SQLite hands aggregates back un-quantized ("9.6"),
    # Postgres exact 2dp — same convention as the s2f contract tests.
    assert _decimalized(data["fees"]) == [("USD", Decimal("9.60"), 2)]


def test_zero_payouts_returns_empty_fees(db, company, stripe_account, authenticated_client, owner_membership):
    """No canonical payouts yet → empty list (frontend renders 0.00), never a
    charge-side fee sum."""
    _mk_charge(company, stripe_account, "ch_1", "100.00")

    data = _get_json(authenticated_client, "/api/stripe/summary/")

    assert data["fees"] == []
    assert data["charges"]["total"] == 1


def test_fees_grouped_per_currency_never_blended(db, company, stripe_account, authenticated_client, owner_membership):
    _mk_provider_payout(company, "stripe", "po_usd", "6.40", currency="USD")
    _mk_provider_payout(company, "stripe", "po_eur", "2.10", currency="EUR")

    data = _get_json(authenticated_client, "/api/stripe/summary/")

    assert _decimalized(data["fees"]) == [
        ("EUR", Decimal("2.10"), 1),
        ("USD", Decimal("6.40"), 1),
    ]


def test_charge_aggregates_group_by_status_and_currency(
    db, company, stripe_account, authenticated_client, owner_membership
):
    _mk_charge(company, stripe_account, "ch_1", "100.00")
    _mk_charge(company, stripe_account, "ch_2", "50.00")
    _mk_charge(company, stripe_account, "ch_3", "20.00", currency="EUR")
    _mk_charge(company, stripe_account, "ch_4", "999.00", status=StripeCharge.Status.RECEIVED)
    _mk_charge(company, stripe_account, "ch_5", "888.00", status=StripeCharge.Status.ERROR)

    data = _get_json(authenticated_client, "/api/stripe/summary/")

    assert data["charges"]["total"] == 5
    assert data["charges"]["processed"] == 3
    assert data["charges"]["errors"] == 1
    # PROCESSED only, per currency — RECEIVED/ERROR amounts never count.
    assert _decimalized(data["charges"]["revenue"]) == [
        ("EUR", Decimal("20.00")),
        ("USD", Decimal("150.00")),
    ]


# ── reconciliation summary currency (A143 label fix) ────────────────


@pytest.fixture
def legacy_and_canonical_payout(db, company, stripe_account):
    """A payout present in BOTH models (the dual-write shape) so the summary
    endpoint answers identically under either flag value."""
    StripePayout.objects.create(
        company=company,
        account=stripe_account,
        stripe_payout_id="po_cur",
        gross_amount=Decimal("103.20"),
        fees=Decimal("6.40"),
        net_amount=Decimal("96.80"),
        currency="USD",
        stripe_status="paid",
        payout_date=date(2026, 6, 20),
    )
    return _mk_provider_payout(company, "stripe", "po_cur", "6.40")


def test_summary_currency_single(db, company, legacy_and_canonical_payout, authenticated_client, owner_membership):
    url = "/api/stripe/reconciliation/?date_from=2026-06-01&date_to=2026-06-30"

    legacy = _get_json(authenticated_client, url)
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical = _get_json(authenticated_client, url)

    assert legacy["currency"] == "USD"
    assert legacy["currencies"] == ["USD"]
    assert canonical["currency"] == legacy["currency"]
    assert canonical["currencies"] == legacy["currencies"]


def test_summary_currency_blank_when_mixed(
    db, company, stripe_account, legacy_and_canonical_payout, authenticated_client, owner_membership
):
    StripePayout.objects.create(
        company=company,
        account=stripe_account,
        stripe_payout_id="po_eur",
        gross_amount=Decimal("50.00"),
        fees=Decimal("2.10"),
        net_amount=Decimal("47.90"),
        currency="EUR",
        stripe_status="paid",
        payout_date=date(2026, 6, 21),
    )
    _mk_provider_payout(company, "stripe", "po_eur", "2.10", currency="EUR", payout_date=date(2026, 6, 21))

    url = "/api/stripe/reconciliation/?date_from=2026-06-01&date_to=2026-06-30"
    legacy = _get_json(authenticated_client, url)
    with override_settings(STRIPE_CANONICAL_PAYOUT_READS=True):
        canonical = _get_json(authenticated_client, url)

    assert legacy["currency"] == ""
    assert legacy["currencies"] == ["EUR", "USD"]
    assert canonical["currency"] == legacy["currency"]
    assert canonical["currencies"] == legacy["currencies"]
