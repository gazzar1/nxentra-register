# tests/test_s1c_stripe_connect.py
"""S1 PR-C (backend) — Stripe connect endpoint.

The single place a real Stripe credential first enters the system. Its job is to
NEVER accept a key that could write to a merchant's Stripe account (reject sk_),
to validate the key live, and to store it A47-encrypted. Covers the command +
the HTTP endpoint.
"""

import json

import pytest

from stripe_connector.commands import connect_stripe_account
from stripe_connector.models import StripeAccount


@pytest.fixture
def _no_async(monkeypatch):
    """Don't enqueue the real initial backfill (no broker / no live Stripe call)."""
    import stripe_connector.tasks as t

    monkeypatch.setattr(t.initial_stripe_sync, "delay", lambda *a, **k: None)


def _mock_probe(monkeypatch, acct):
    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.retrieve_account", lambda self: acct)
    # connect also exercises the pull-path scopes (Payouts + Balance Transactions).
    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.probe", lambda self: True)


# ── security: only restricted read keys are accepted ──────────────────


def test_rejects_secret_key(db, company):
    result = connect_stripe_account(company, "sk_live_abc123")
    assert not result.success
    assert "SECRET" in result.error
    assert not StripeAccount.objects.filter(company=company).exists()


def test_rejects_publishable_key(db, company):
    result = connect_stripe_account(company, "pk_live_abc")
    assert not result.success
    assert not StripeAccount.objects.filter(company=company).exists()


def test_rejects_malformed_key(db, company):
    result = connect_stripe_account(company, "totally-not-a-key")
    assert not result.success


def test_probe_access_denied_fails_without_persisting(db, company, monkeypatch):
    # probe() (Payouts + Balance read) is the gate — its denial rejects the key.
    from stripe_connector.api_client import StripeAccessDenied

    def _denied(self):
        raise StripeAccessDenied("insufficient read scope")

    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.probe", _denied)
    result = connect_stripe_account(company, "rk_test_badscope")
    assert not result.success
    assert "read scope" in result.error
    assert not StripeAccount.objects.filter(company=company).exists()


def test_connects_without_account_read_permission(db, company, monkeypatch, _no_async):
    """The pull only needs Balance + Payouts read. A key that can pull but CAN'T
    read /v1/account (the KYC scope Stripe doesn't expose in the restricted-key
    editor) must still connect — account id + livemode captured best-effort."""
    from stripe_connector.api_client import StripeAccessDenied

    def _account_denied(self):
        raise StripeAccessDenied("accounts_kyc_basic_read not granted")

    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.retrieve_account", _account_denied)
    monkeypatch.setattr("stripe_connector.api_client.StripeApiClient.probe", lambda self: True)

    result = connect_stripe_account(company, "rk_live_pullonly")
    assert result.success

    acct = StripeAccount.objects.get(company=company)
    assert acct.status == StripeAccount.Status.ACTIVE
    assert acct.credential_ref == "rk_live_pullonly"
    assert acct.livemode is True  # derived from the rk_live_ prefix, not /v1/account
    assert acct.stripe_account_id  # a stable synthetic id was assigned


# ── happy path: connect + encrypt + seed ──────────────────────────────


def test_valid_restricted_key_connects_seeds_and_encrypts(db, company, monkeypatch, _no_async):
    _mock_probe(monkeypatch, {"id": "acct_live_1", "livemode": True, "business_profile": {"name": "Acme"}})

    result = connect_stripe_account(company, "rk_live_validkey")
    assert result.success

    acct = StripeAccount.objects.get(company=company, stripe_account_id="acct_live_1")
    assert acct.status == StripeAccount.Status.ACTIVE
    assert acct.auth_type == StripeAccount.AuthType.RESTRICTED_KEY
    assert acct.livemode is True
    assert acct.display_name == "Acme"

    # ORM decrypts (A47); the column holds ciphertext, never the raw key
    assert acct.credential_ref == "rk_live_validkey"
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(f'SELECT credential_ref FROM "{StripeAccount._meta.db_table}" WHERE id = %s', [acct.id])
        raw = cur.fetchone()[0]
    assert raw.startswith("enc:v1:") and "rk_live_validkey" not in raw

    # platform accounts + SettlementProvider seeded so the first payout JE resolves
    from accounting.mappings import ModuleAccountMapping
    from accounting.settlement_provider import SettlementProvider

    assert ModuleAccountMapping.get_account(company, "platform_stripe", "EXPECTED_BANK_DEPOSIT") is not None
    assert SettlementProvider.objects.filter(
        company=company, external_system="stripe", normalized_code="stripe"
    ).exists()


def test_reconnect_is_idempotent_on_account_id(db, company, monkeypatch, _no_async):
    _mock_probe(monkeypatch, {"id": "acct_x", "livemode": False})
    connect_stripe_account(company, "rk_test_one")
    connect_stripe_account(company, "rk_test_two")  # rotate the key
    accts = StripeAccount.objects.filter(company=company, stripe_account_id="acct_x")
    assert accts.count() == 1
    assert accts.first().credential_ref == "rk_test_two"  # latest key wins


# ── HTTP endpoint ─────────────────────────────────────────────────────


def test_connect_endpoint_happy_path(db, company, authenticated_client, owner_membership, monkeypatch, _no_async):
    _mock_probe(monkeypatch, {"id": "acct_test_1", "livemode": False})
    resp = authenticated_client.post(
        "/api/stripe/connect/",
        data=json.dumps({"credential": "rk_test_x"}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["connected"] is True
    assert body["stripe_account_id"] == "acct_test_1"


def test_connect_endpoint_rejects_secret_key(db, company, authenticated_client, owner_membership):
    resp = authenticated_client.post(
        "/api/stripe/connect/",
        data=json.dumps({"credential": "sk_live_danger"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "SECRET" in resp.json()["error"]
    assert not StripeAccount.objects.filter(company=company).exists()
