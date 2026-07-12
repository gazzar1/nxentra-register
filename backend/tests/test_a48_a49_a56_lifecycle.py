# tests/test_a48_a49_a56_lifecycle.py
"""
A48 + A49 + A56 — Shopify store lifecycle hardening.

A48: app/uninstalled stamps uninstalled_at (first receipt wins, webhook
retries stop emitting duplicate DISCONNECTED events); reconnect clears it.
A49: a revoked token flips needs_reauth (401 / dead refresh token /
deprecated non-expiring format) so the settings page can banner the
merchant — the store stays ACTIVE (webhooks may still flow; the
connected contract keys on ACTIVE). Transient failures never nag.
A56: the OAuth IntegrityError catch runs under an inner savepoint (a
bare catch inside atomic left the transaction aborted) and deletes the
orphan PENDING row; a beat sweep clears abandoned PENDING stores and
expired token-bearing PendingShopifyInstall rows.
"""

from datetime import timedelta
from unittest import mock
from uuid import uuid4

import pytest
import requests as requests_lib
from django.utils import timezone

from projections.write_barrier import command_writes_allowed
from shopify_connector import commands
from shopify_connector.commands import (
    _get_valid_access_token,
    complete_oauth,
    process_app_uninstalled,
)
from shopify_connector.models import PendingShopifyInstall, ShopifyStore

pytestmark = pytest.mark.django_db


@pytest.fixture
def store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="lifecycle-test.myshopify.com",
        access_token="test-token",
        refresh_token="shprt_refresh",
        status=ShopifyStore.Status.ACTIVE,
    )


def _http_error(status_code):
    response = mock.Mock()
    response.status_code = status_code
    err = requests_lib.HTTPError(f"{status_code} error")
    err.response = response
    return err


# =============================================================================
# A48 — uninstalled_at
# =============================================================================


class TestA48Uninstall:
    def test_uninstall_stamps_timestamp_and_clears_tokens(self, store, company):
        result = process_app_uninstalled(store, {})
        assert result.success

        store.refresh_from_db()
        assert store.status == ShopifyStore.Status.DISCONNECTED
        assert store.uninstalled_at is not None
        assert store.access_token == ""
        assert store.needs_reauth is False

    def test_webhook_retry_preserves_first_timestamp_and_event(self, store, company):
        from events.models import BusinessEvent

        process_app_uninstalled(store, {})
        store.refresh_from_db()
        first_stamp = store.uninstalled_at
        events_after_first = BusinessEvent.objects.filter(
            company=company, event_type="shopify.store_disconnected"
        ).count()

        result = process_app_uninstalled(store, {})
        assert result.success and result.data.get("skipped")

        store.refresh_from_db()
        assert store.uninstalled_at == first_stamp
        assert (
            BusinessEvent.objects.filter(company=company, event_type="shopify.store_disconnected").count()
            == events_after_first
        ), "webhook retries must not emit duplicate DISCONNECTED events"

    def test_reconnect_clears_lifecycle_flags(self, store, company, monkeypatch):
        process_app_uninstalled(store, {})
        with command_writes_allowed():
            store.refresh_from_db()
            store.status = ShopifyStore.Status.PENDING
            store.oauth_nonce = "nonce-1"
            store.needs_reauth = True
            store.save()

        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"access_token": "new-token", "scope": "read_orders"}
        fake_resp.raise_for_status.return_value = None
        monkeypatch.setattr(commands.requests, "post", lambda *a, **kw: fake_resp)
        # Post-install setup needs real infra — not this test's subject.
        monkeypatch.setattr(commands, "_ensure_shopify_warehouse", lambda s: None)
        monkeypatch.setattr(commands, "_ensure_shopify_sales_setup", lambda s: None)
        monkeypatch.setattr(commands, "_schedule_initial_sync", lambda s: None)

        result = complete_oauth(company, store.shop_domain, "code", "nonce-1")
        assert result.success, result.error

        store.refresh_from_db()
        assert store.status == ShopifyStore.Status.ACTIVE
        assert store.uninstalled_at is None
        assert store.needs_reauth is False


# =============================================================================
# A49 — needs_reauth
# =============================================================================


class TestA49NeedsReauth:
    def _expiring(self, store):
        with command_writes_allowed():
            store.token_expires_at = timezone.now() - timedelta(minutes=5)
            store.refresh_token_expires_at = timezone.now() + timedelta(days=30)
            store.save()

    def test_expired_refresh_token_marks_needs_reauth(self, store):
        with command_writes_allowed():
            store.token_expires_at = timezone.now() - timedelta(minutes=5)
            store.refresh_token_expires_at = timezone.now() - timedelta(days=1)
            store.save()

        assert _get_valid_access_token(store) is None
        store.refresh_from_db()
        assert store.needs_reauth is True
        assert "reconnect" in store.error_message.lower()

    def test_401_on_refresh_marks_needs_reauth(self, store, monkeypatch):
        self._expiring(store)

        def _post(*a, **kw):
            raise _http_error(401)

        monkeypatch.setattr(commands.requests, "post", _post)
        assert _get_valid_access_token(store) is None
        store.refresh_from_db()
        assert store.needs_reauth is True

    def test_transient_5xx_on_refresh_does_not_nag(self, store, monkeypatch):
        self._expiring(store)

        def _post(*a, **kw):
            raise _http_error(503)

        monkeypatch.setattr(commands.requests, "post", _post)
        assert _get_valid_access_token(store) is None
        store.refresh_from_db()
        assert store.needs_reauth is False, "a transient failure must not show the reconnect banner"

    def test_sync_payouts_401_flags_but_stays_unavailable(self, store, monkeypatch):
        class _Client:
            def list_payouts(self, status="paid", limit=None):
                raise _http_error(401)

        monkeypatch.setattr(commands, "_admin_client", lambda s: _Client())
        result = commands.sync_payouts(store)
        assert result.success
        assert result.data["status"] == "unavailable", "A120 graceful result must survive"
        store.refresh_from_db()
        assert store.needs_reauth is True

    def test_sync_payouts_403_stays_quiet(self, store, monkeypatch):
        """The A120 boundary: a bare dev store 403s here with a perfectly
        valid token — no banner."""

        class _Client:
            def list_payouts(self, status="paid", limit=None):
                raise _http_error(403)

        monkeypatch.setattr(commands, "_admin_client", lambda s: _Client())
        result = commands.sync_payouts(store)
        assert result.success
        assert result.data["status"] == "unavailable"
        store.refresh_from_db()
        assert store.needs_reauth is False

    def test_store_api_exposes_flags(self, store, company, user, owner_membership):
        from rest_framework.test import APIClient

        with command_writes_allowed():
            store.needs_reauth = True
            store.save(update_fields=["needs_reauth"])

        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.get("/api/shopify/store/")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["connected"] is True, "needs_reauth must NOT flip the connected contract"
        assert payload["stores"][0]["needs_reauth"] is True
        assert "uninstalled_at" in payload["stores"][0]


# =============================================================================
# A56 — orphan PENDING cleanup
# =============================================================================


class TestA56Cleanup:
    def test_domain_taken_deletes_pending_orphan(self, company, second_company, monkeypatch):
        """Company A holds shop X ACTIVE; company B's OAuth callback for
        the same shop hits uniq_active_shop_domain. The old bare catch
        left B's PENDING row (stale nonce) forever — and on Postgres the
        aborted transaction made even the error path blow up."""
        ShopifyStore.objects.create(
            company=company,
            shop_domain="taken.myshopify.com",
            access_token="a-token",
            status=ShopifyStore.Status.ACTIVE,
        )
        ShopifyStore.objects.create(
            company=second_company,
            shop_domain="taken.myshopify.com",
            access_token="",
            oauth_nonce="nonce-b",
            status=ShopifyStore.Status.PENDING,
        )

        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"access_token": "b-token", "scope": "read_orders"}
        fake_resp.raise_for_status.return_value = None
        monkeypatch.setattr(commands.requests, "post", lambda *a, **kw: fake_resp)

        result = complete_oauth(second_company, "taken.myshopify.com", "code", "nonce-b")
        assert not result.success
        assert "already connected" in result.error

        # The orphan is gone; company A's ACTIVE row is untouched; the
        # transaction is still usable (this query would raise
        # TransactionManagementError without the savepoint).
        assert not ShopifyStore.objects.filter(company=second_company, shop_domain="taken.myshopify.com").exists()
        assert ShopifyStore.objects.filter(
            company=company, shop_domain="taken.myshopify.com", status=ShopifyStore.Status.ACTIVE
        ).exists()

    def test_sweep_deletes_stale_installs_only(self, company):
        from shopify_connector.tasks import cleanup_stale_installs

        now = timezone.now()
        stale = ShopifyStore.objects.create(
            company=company,
            shop_domain="stale-pending.myshopify.com",
            access_token="",
            status=ShopifyStore.Status.PENDING,
        )
        fresh = ShopifyStore.objects.create(
            company=company,
            shop_domain="fresh-pending.myshopify.com",
            access_token="",
            status=ShopifyStore.Status.PENDING,
        )
        # Backdate past the sweep threshold (bypass auto_now).
        ShopifyStore.objects.filter(pk=stale.pk).update(updated_at=now - timedelta(hours=25))

        expired = PendingShopifyInstall.objects.create(
            public_id=uuid4(),
            shop_domain="expired-install.myshopify.com",
            access_token="tok",
            expires_at=now - timedelta(hours=2),
        )
        live = PendingShopifyInstall.objects.create(
            public_id=uuid4(),
            shop_domain="live-install.myshopify.com",
            access_token="tok",
            expires_at=now + timedelta(minutes=20),
        )
        consumed_old = PendingShopifyInstall.objects.create(
            public_id=uuid4(),
            shop_domain="consumed-old.myshopify.com",
            access_token="tok",
            expires_at=now - timedelta(days=40),
            consumed_at=now - timedelta(days=35),
        )

        result = cleanup_stale_installs()
        assert result["pending_stores_deleted"] == 1
        assert result["expired_installs_deleted"] == 1
        assert result["consumed_installs_deleted"] == 1

        assert not ShopifyStore.objects.filter(pk=stale.pk).exists()
        assert ShopifyStore.objects.filter(pk=fresh.pk).exists()
        assert not PendingShopifyInstall.objects.filter(pk=expired.pk).exists()
        assert PendingShopifyInstall.objects.filter(pk=live.pk).exists()
        assert not PendingShopifyInstall.objects.filter(pk=consumed_old.pk).exists()
