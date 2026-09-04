# tests/test_g1_f1_store_sweep_history_guard.py
"""
G1-F1 — ShopifyStore PENDING-sweep history protection.

The G1 gate's first hard precondition (tracker G1 row; A5 closure review):
a store with canonical history must never be deleted by an abandoned
reconnect / PENDING cleanup. ShopifyOrder (and through it refunds and
fulfillments), ShopifyPayout, ShopifyDispute, ShopifyProduct and the A1
ShopifyUserBinding all CASCADE on the store row, so the old sweeps — which
selected on ``status=PENDING`` + age alone — would have destroyed the
source-document mirrors and the binding of a DISCONNECTED store whose
merchant clicked Connect and then bounced off the Shopify authorize screen
(``get_install_url`` puts that row back to PENDING on the SAME row).

Three deletion doors share one disposition
(``retire_or_delete_stale_pending_stores``): the per-company sweep in
``get_install_url``, the domain-taken branch of ``complete_oauth`` and the
``cleanup_stale_installs`` beat task. A stale PENDING row WITHOUT history is
still deleted; a stale PENDING row WITH history is returned to DISCONNECTED
with its nonce cleared, mirrors and binding intact, and the merchant can
reconnect through the unchanged A48 path.
"""

from datetime import date, timedelta
from unittest import mock

import pytest
from django.utils import timezone

from projections.write_barrier import command_writes_allowed
from shopify_connector import commands
from shopify_connector import rejected_evidence as re_mod
from shopify_connector.commands import (
    STALE_RECONNECT_RETAINED_MESSAGE,
    complete_oauth,
    get_install_url,
    process_app_uninstalled,
    retire_or_delete_stale_pending_stores,
    store_has_canonical_history,
)
from shopify_connector.models import (
    ShopifyOrder,
    ShopifyPayout,
    ShopifyProduct,
    ShopifyRejectedEvidence,
    ShopifyStore,
    ShopifyUserBinding,
)
from shopify_connector.tasks import cleanup_stale_installs

pytestmark = pytest.mark.django_db


def _store(company, domain, status=ShopifyStore.Status.PENDING, **extra):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain=domain,
        access_token="" if status == ShopifyStore.Status.PENDING else "tok",
        status=status,
        **extra,
    )


def _order(store, order_id):
    return ShopifyOrder.objects.create(
        company=store.company,
        store=store,
        shopify_order_id=order_id,
        shopify_order_number=str(order_id),
        shopify_order_name=f"#{order_id}",
        total_price="100.00",
        subtotal_price="100.00",
        total_tax="0.00",
        currency="EGP",
        financial_status="paid",
        shopify_created_at=timezone.now(),
        order_date=date(2026, 5, 1),
    )


def _backdate(store, hours):
    # updated_at is auto_now — bypass it the way the A56 test does.
    ShopifyStore.objects.filter(pk=store.pk).update(updated_at=timezone.now() - timedelta(hours=hours))


def _abandoned_reconnect(store, nonce="stale-nonce"):
    """Model the real path: an ACTIVE store is uninstalled (DISCONNECTED),
    the merchant clicks Connect (row → PENDING on the same pk), then bounces."""
    process_app_uninstalled(store, {})
    with command_writes_allowed():
        store.refresh_from_db()
        store.status = ShopifyStore.Status.PENDING
        store.oauth_nonce = nonce
        store.save()
    return store


# =============================================================================
# The predicate
# =============================================================================


class TestHistoryPredicate:
    def test_fresh_pending_row_has_no_history(self, company):
        store = _store(company, "fresh.myshopify.com")
        assert store_has_canonical_history(store) is False

    @pytest.mark.parametrize(
        "marker",
        [{"last_sync_at": None}, {"uninstalled_at": None}, {"scopes": "read_orders"}],
        ids=["last_sync_at", "uninstalled_at", "scopes"],
    )
    def test_once_active_markers_count_as_history(self, company, marker):
        """`scopes` is the marker that survives a user-initiated
        `disconnect_store` (which stamps neither last_sync_at nor
        uninstalled_at): it is written only by a successful token exchange."""
        values = {k: (timezone.now() if v is None else v) for k, v in marker.items()}
        store = _store(company, "was-active.myshopify.com", **values)
        assert store_has_canonical_history(store) is True

    def test_payout_mirror_counts_as_history(self, company):
        store = _store(company, "has-payout.myshopify.com")
        ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=1,
            gross_amount="10.00",
            fees="1.00",
            net_amount="9.00",
            currency="EGP",
            payout_date=date(2026, 5, 1),
        )
        assert store_has_canonical_history(store) is True

    def test_rejected_evidence_counts_as_history(self, company):
        store = _store(company, "has-evidence.myshopify.com")
        payload = {"probe": "f1"}
        ShopifyRejectedEvidence.objects.create(
            company=company,
            store=store,
            store_public_id=store.public_id,
            shop_domain=store.shop_domain,
            resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
            ingress_kind=ShopifyRejectedEvidence.IngressKind.WEBHOOK,
            source_topic="orders/paid",
            parsed_payload=payload,
            payload_hash=re_mod.canonical_payload_hash(payload),
            rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
            rejection_message="probe",
            validation_errors=[],
            dedup_hash=re_mod.compute_dedup_hash(
                company.id, store.public_id, "ORDER", re_mod.canonical_payload_hash(payload)
            ),
        )
        assert store_has_canonical_history(store) is True

    def test_order_mirror_counts_as_history(self, company):
        store = _store(company, "has-order.myshopify.com")
        _order(store, 5001)
        assert store_has_canonical_history(store) is True

    def test_product_mirror_counts_as_history(self, company):
        store = _store(company, "has-product.myshopify.com")
        ShopifyProduct.objects.create(
            company=company,
            store=store,
            shopify_product_id=1,
            shopify_variant_id=1,
            title="Widget",
        )
        assert store_has_canonical_history(store) is True

    def test_binding_counts_as_history(self, company, owner_membership):
        store = _store(company, "has-binding.myshopify.com")
        ShopifyUserBinding.objects.create(
            store=store, shopify_sub="gid://shopify/StaffMember/77", membership=owner_membership, is_active=False
        )
        assert store_has_canonical_history(store) is True


# =============================================================================
# The shared disposition
# =============================================================================


class TestRetireOrDelete:
    def test_history_row_is_retained_as_disconnected_with_mirrors_intact(self, company, owner_membership):
        store = _store(company, "history.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        _order(store, 6001)
        binding = ShopifyUserBinding.objects.create(
            store=store, shopify_sub="gid://shopify/StaffMember/1", membership=owner_membership, is_active=True
        )
        _abandoned_reconnect(store)

        with command_writes_allowed():
            result = retire_or_delete_stale_pending_stores(ShopifyStore.objects.filter(pk=store.pk))

        assert result["retained"] == 1 and result["deleted"] == 0
        assert result["retained_domains"] == ["history.myshopify.com"]
        store.refresh_from_db()
        assert store.status == ShopifyStore.Status.DISCONNECTED
        assert store.oauth_nonce == ""
        assert store.error_message == STALE_RECONNECT_RETAINED_MESSAGE
        # The A48 stamp and every mirror survive.
        assert store.uninstalled_at is not None
        assert ShopifyOrder.objects.filter(store=store).count() == 1
        assert ShopifyUserBinding.objects.filter(pk=binding.pk).exists()

    def test_retained_row_carries_no_live_tokens(self, company):
        """A47: the canonical DISCONNECTED writers clear the tokens; so must
        the retention. Reachable path: ACTIVE -> token exchange fails (ERROR
        keeps the tokens) -> Connect again (PENDING keeps them) -> bounce."""
        store = _store(
            company,
            "tokens.myshopify.com",
            status=ShopifyStore.Status.ERROR,
            scopes="read_orders",
            refresh_token="shprt_live",
            token_expires_at=timezone.now() + timedelta(hours=1),
            refresh_token_expires_at=timezone.now() + timedelta(days=30),
            needs_reauth=True,
        )
        _order(store, 6002)
        get_install_url(company, "tokens.myshopify.com")
        store.refresh_from_db()
        assert store.status == ShopifyStore.Status.PENDING and store.access_token == "tok"

        with command_writes_allowed():
            result = retire_or_delete_stale_pending_stores(ShopifyStore.objects.filter(pk=store.pk))

        assert result["retained"] == 1
        store.refresh_from_db()
        assert store.status == ShopifyStore.Status.DISCONNECTED
        assert store.access_token == "" and store.refresh_token == ""
        assert store.token_expires_at is None and store.refresh_token_expires_at is None
        assert store.needs_reauth is False
        assert store.scopes == "read_orders"  # the once-ACTIVE marker is kept
        assert ShopifyOrder.objects.filter(store=store).count() == 1

    def test_no_history_row_is_deleted(self, company):
        store = _store(company, "orphan.myshopify.com", oauth_nonce="n")
        with command_writes_allowed():
            result = retire_or_delete_stale_pending_stores(ShopifyStore.objects.filter(pk=store.pk))
        assert result["deleted"] == 1 and result["retained"] == 0
        assert not ShopifyStore.objects.filter(pk=store.pk).exists()

    @pytest.mark.parametrize("with_history", [False, True], ids=["delete-branch", "retain-branch"])
    def test_row_activated_before_disposition_is_neither_deleted_nor_downgraded(self, company, with_history):
        """The committed-before-selection case only: the caller's in-memory
        candidate still says PENDING, but the locked re-read filtered on
        status=PENDING returns nothing, so neither branch runs. The in-flight
        (lock-held) ordering is proven on PostgreSQL in
        tests/e2e/test_g1_f1_store_sweep_serialization.py."""
        store = _store(company, "raced.myshopify.com", oauth_nonce="n")
        if with_history:
            _order(store, 6003)
        with command_writes_allowed():
            ShopifyStore.objects.filter(pk=store.pk).update(status=ShopifyStore.Status.ACTIVE, access_token="tok")
            result = retire_or_delete_stale_pending_stores([store])  # stale in-memory candidate
        assert result == {"deleted": 0, "retained": 0, "deleted_domains": [], "retained_domains": []}
        store.refresh_from_db()
        assert store.status == ShopifyStore.Status.ACTIVE
        assert store.oauth_nonce == "n" and store.error_message == ""


# =============================================================================
# Door 1 — the beat sweep
# =============================================================================


class TestCleanupStaleInstalls:
    def test_sweep_retains_history_and_deletes_orphans(self, company, owner_membership):
        history = _store(company, "history-sweep.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        _order(history, 7001)
        ShopifyUserBinding.objects.create(
            store=history, shopify_sub="gid://shopify/StaffMember/2", membership=owner_membership, is_active=True
        )
        _abandoned_reconnect(history)
        orphan = _store(company, "orphan-sweep.myshopify.com", oauth_nonce="n")
        fresh = _store(company, "fresh-sweep.myshopify.com", oauth_nonce="n")
        _backdate(history, 25)
        _backdate(orphan, 25)

        result = cleanup_stale_installs()

        assert result["pending_stores_deleted"] == 1
        assert result["pending_stores_retained"] == 1
        history.refresh_from_db()
        assert history.status == ShopifyStore.Status.DISCONNECTED
        assert history.oauth_nonce == ""
        assert ShopifyOrder.objects.filter(store=history).count() == 1
        assert ShopifyUserBinding.objects.filter(store=history).count() == 1
        assert not ShopifyStore.objects.filter(pk=orphan.pk).exists()
        assert ShopifyStore.objects.filter(pk=fresh.pk, status=ShopifyStore.Status.PENDING).exists()

    def test_sweep_is_idempotent_for_a_retained_store(self, company):
        history = _store(company, "idem.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        _order(history, 7002)
        _abandoned_reconnect(history)
        _backdate(history, 25)

        first = cleanup_stale_installs()
        second = cleanup_stale_installs()

        assert first["pending_stores_retained"] == 1
        assert second["pending_stores_retained"] == 0 and second["pending_stores_deleted"] == 0
        assert ShopifyStore.objects.filter(pk=history.pk, status=ShopifyStore.Status.DISCONNECTED).exists()


# =============================================================================
# Door 2 — the per-company sweep in get_install_url
# =============================================================================


class TestInstallUrlSweep:
    def test_other_domain_with_history_is_retained(self, company):
        history = _store(company, "old-store.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        _order(history, 8001)
        _abandoned_reconnect(history)
        orphan = _store(company, "old-orphan.myshopify.com", oauth_nonce="n")
        _backdate(history, 2)
        _backdate(orphan, 2)

        get_install_url(company, "new-store.myshopify.com")

        history.refresh_from_db()
        assert history.status == ShopifyStore.Status.DISCONNECTED
        assert ShopifyOrder.objects.filter(store=history).count() == 1
        assert not ShopifyStore.objects.filter(pk=orphan.pk).exists()
        assert ShopifyStore.objects.filter(
            company=company, shop_domain="new-store.myshopify.com", status=ShopifyStore.Status.PENDING
        ).exists()

    def test_retained_store_reconnects_through_the_normal_path(self, company, monkeypatch):
        """The retained DISCONNECTED row is exactly the A48 reconnect
        precondition: Connect → PENDING (same pk) → OAuth success → ACTIVE,
        history still attached."""
        history = _store(company, "again.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        _order(history, 8002)
        _abandoned_reconnect(history)
        _backdate(history, 25)
        cleanup_stale_installs()
        history.refresh_from_db()
        assert history.status == ShopifyStore.Status.DISCONNECTED

        nonce = get_install_url(company, "again.myshopify.com")["nonce"]
        history.refresh_from_db()
        assert history.status == ShopifyStore.Status.PENDING

        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"access_token": "new-token", "scope": "read_orders"}
        fake_resp.raise_for_status.return_value = None
        monkeypatch.setattr(commands.requests, "post", lambda *a, **kw: fake_resp)
        monkeypatch.setattr(commands, "_ensure_shopify_warehouse", lambda s: None)
        monkeypatch.setattr(commands, "_ensure_shopify_sales_setup", lambda s: None)
        monkeypatch.setattr(commands, "_schedule_initial_sync", lambda s: None)

        result = complete_oauth(company, "again.myshopify.com", "code", nonce)
        assert result.success, result.error
        history.refresh_from_db()
        assert history.status == ShopifyStore.Status.ACTIVE
        assert history.uninstalled_at is None
        assert history.error_message == ""
        assert ShopifyOrder.objects.filter(store=history).count() == 1


# =============================================================================
# Door 3 — the domain-taken branch of complete_oauth
# =============================================================================


class TestOAuthDomainTaken:
    def test_pending_row_with_history_is_retained_when_domain_is_taken(
        self, company, second_company, owner_membership, monkeypatch
    ):
        """Company B connected the store before (history), the store moved to
        company A, B tries to reconnect: uniq_active_shop_domain refuses and
        B's PENDING row must be retained as DISCONNECTED, not deleted."""
        _store(company, "moved.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        b_store = _store(second_company, "moved.myshopify.com", status=ShopifyStore.Status.DISCONNECTED)
        _order(b_store, 9001)
        with command_writes_allowed():
            b_store.status = ShopifyStore.Status.PENDING
            b_store.oauth_nonce = "nonce-b"
            b_store.save()

        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"access_token": "b-token", "scope": "read_orders"}
        fake_resp.raise_for_status.return_value = None
        monkeypatch.setattr(commands.requests, "post", lambda *a, **kw: fake_resp)

        result = complete_oauth(second_company, "moved.myshopify.com", "code", "nonce-b")
        assert not result.success
        assert "already connected" in result.error

        b_store.refresh_from_db()
        assert b_store.status == ShopifyStore.Status.DISCONNECTED
        assert b_store.oauth_nonce == ""
        assert ShopifyOrder.objects.filter(store=b_store).count() == 1
        assert ShopifyStore.objects.filter(
            company=company, shop_domain="moved.myshopify.com", status=ShopifyStore.Status.ACTIVE
        ).exists()

    def test_pending_row_without_history_is_still_deleted_when_domain_is_taken(
        self, company, second_company, monkeypatch
    ):
        """The A56 behavior is unchanged for a genuine orphan."""
        _store(company, "taken.myshopify.com", status=ShopifyStore.Status.ACTIVE)
        _store(second_company, "taken.myshopify.com", oauth_nonce="nonce-b")

        fake_resp = mock.Mock()
        fake_resp.json.return_value = {"access_token": "b-token", "scope": "read_orders"}
        fake_resp.raise_for_status.return_value = None
        monkeypatch.setattr(commands.requests, "post", lambda *a, **kw: fake_resp)

        result = complete_oauth(second_company, "taken.myshopify.com", "code", "nonce-b")
        assert not result.success
        assert not ShopifyStore.objects.filter(company=second_company, shop_domain="taken.myshopify.com").exists()
