# tests/test_a134_store_resolution.py
"""
A134: the shopify_accounting projection resolves the *exact* store a
financial event belongs to (by ``store_public_id`` / ``shop_domain``)
instead of blindly taking the company's first ACTIVE store.

The old ``ShopifyStore.filter(status=ACTIVE).first()`` pattern (same bug
family as A57) had two failure modes:

1. **Multi-store mis-attribution** — a paid order from store B posted under
   store A's customer / posting profile.
2. **Post-disconnect re-error** — after a connect/disconnect churn left the
   company with no ACTIVE store, every order_paid event raised
   ``ProjectionStateError`` on *every* projection beat, spamming Sentry (the
   b74379 reviewer-store residual on Shopify_R that motivated A134).

Coverage:
- Payload-based resolution picks the store named by ``store_public_id`` even
  when another active store exists (multi-store correctness).
- ``shop_domain`` metadata resolves the store when ``store_public_id`` is absent.
- The identifier is authoritative regardless of status: an event for a
  DISCONNECTED store posts under THAT store, never re-homed to a surviving
  active store (the review's HIGH mis-attribution finding).
- Self-heal: an active store missing its sales-routing defaults is repaired
  in-flight via ``_ensure_shopify_sales_setup`` and the order still posts.
- Still-missing raises ``ProjectionStateError`` (loud-not-silent per A80) when
  self-heal can't complete (no clearing account to anchor the profile).
- Truly unresolvable (identifier matches no row, or legacy event + 2+ active
  stores) → ``DeferEvent`` while fresh (quiet retry, no ProjectionFailureLog),
  then ``ProjectionStateError`` past a 24h age cap so it surfaces to operators.
- Backward-compat: a legacy event with neither identifier still resolves via
  the sole-active-store fallback.
- ``shopify_health_check`` flags an active store missing defaults.
- A136 sibling: ``disconnect_store`` refuses to guess among multiple
  connected stores.

Reuses the `shopify_company` fixture + chart-of-accounts scaffolding from
test_system_je_validation.py.
"""

from datetime import date
from uuid import uuid4

import pytest

from tests.test_system_je_validation import (  # noqa: F401
    _make_shopify_order_event,
    shopify_company,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order_event(
    company,
    shopify_order_id,
    *,
    store_public_id=None,
    shop_domain=None,
    amount="100.00",
):
    """Build a SHOPIFY_ORDER_PAID BusinessEvent, optionally carrying a
    ``store_public_id`` in the payload and a ``shop_domain`` in metadata."""
    from django.utils import timezone

    from events.models import BusinessEvent, CompanyEventCounter

    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()

    data = {
        "amount": amount,
        "currency": "USD",
        "transaction_date": date.today().isoformat(),
        "document_ref": f"#{shopify_order_id}",
        "shopify_order_id": str(shopify_order_id),
        "order_number": str(shopify_order_id),
        "order_name": f"#{shopify_order_id}",
        "subtotal": amount,
        "total_tax": "0",
        "total_shipping": "0",
        "total_discounts": "0",
        "financial_status": "paid",
        "gateway": "shopify_payments",
        "line_items": [],
    }
    if store_public_id is not None:
        data["store_public_id"] = str(store_public_id)

    metadata = {"source": "shopify_webhook"}
    if shop_domain is not None:
        metadata["shop_domain"] = shop_domain

    return BusinessEvent.objects.create(
        company=company,
        event_type="shopify.order_paid",
        aggregate_type="ShopifyOrder",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"shopify.order.paid:{shopify_order_id}",
        data=data,
        metadata=metadata,
        occurred_at=timezone.now(),
    )


def _make_store(company, label, *, status=None, with_setup=True):
    """Create a ShopifyStore for `company`. With `with_setup`, also wire its
    default Customer + PostingProfile via `_ensure_shopify_sales_setup`."""
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    status = status or ShopifyStore.Status.ACTIVE
    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain=f"{label}-{uuid4().hex[:6]}.myshopify.com",
            access_token="test-token",
            status=status,
        )
    if with_setup:
        _ensure_shopify_sales_setup(store)
        store.refresh_from_db()
    return store


# ---------------------------------------------------------------------------
# 1. Payload-based resolution — multi-store picks the RIGHT store
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolves_store_by_public_id_with_second_active_store(shopify_company):  # noqa: F811
    """Two active stores exist; an event tagged with store B's
    ``store_public_id`` must post under store B's customer — not store A's
    (the one the old ``.first()`` would have grabbed)."""
    from sales.models import SalesInvoice
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    store_a = ShopifyStore.objects.get(company=shopify_company)
    store_b = _make_store(shopify_company, "store-b")

    assert store_a.default_customer_id != store_b.default_customer_id

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134001,
        store_public_id=store_b.public_id,
        shop_domain=store_b.shop_domain,
    )

    ShopifyAccountingHandler().handle(event)

    invoice = SalesInvoice.objects.get(company=shopify_company, source="shopify", source_document_id="134001")
    assert invoice.status == SalesInvoice.Status.POSTED
    assert invoice.customer_id == store_b.default_customer_id, (
        "Order tagged with store B's public_id must post under store B's "
        "customer — the A57-family bug posted under whichever store was first."
    )


@pytest.mark.django_db
def test_resolves_store_by_shop_domain_when_public_id_absent(shopify_company):  # noqa: F811
    """When the payload lacks ``store_public_id`` but metadata carries
    ``shop_domain``, the store still resolves by domain (covers payloads
    predating the store_public_id field)."""
    from sales.models import SalesInvoice
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    store_a = ShopifyStore.objects.get(company=shopify_company)
    store_b = _make_store(shopify_company, "store-b")

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134002,
        store_public_id=None,
        shop_domain=store_b.shop_domain,
    )

    ShopifyAccountingHandler().handle(event)

    invoice = SalesInvoice.objects.get(company=shopify_company, source="shopify", source_document_id="134002")
    assert invoice.customer_id == store_b.default_customer_id
    assert invoice.customer_id != store_a.default_customer_id


# ---------------------------------------------------------------------------
# 2. Self-heal — active store missing defaults is repaired in-flight
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_self_heals_store_missing_defaults(shopify_company):  # noqa: F811
    """An active store with no default_customer/posting_profile (the
    OAuth-callback ordering gap) is healed by the projection and the order
    posts. The company already has SHOPIFY_CLEARING mapped, so
    `_ensure_shopify_sales_setup` can complete."""
    from sales.models import SalesInvoice
    from shopify_connector.projections import ShopifyAccountingHandler

    bare = _make_store(shopify_company, "bare-store", with_setup=False)
    assert bare.default_customer_id is None
    assert bare.default_posting_profile_id is None

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134003,
        store_public_id=bare.public_id,
        shop_domain=bare.shop_domain,
    )

    ShopifyAccountingHandler().handle(event)

    bare.refresh_from_db()
    assert bare.default_customer_id is not None, "self-heal should have set default_customer"
    assert bare.default_posting_profile_id is not None

    invoice = SalesInvoice.objects.get(company=shopify_company, source="shopify", source_document_id="134003")
    assert invoice.status == SalesInvoice.Status.POSTED
    assert invoice.customer_id == bare.default_customer_id


@pytest.mark.django_db
def test_still_missing_after_self_heal_raises(db):
    """When self-heal can't complete (no SHOPIFY_CLEARING mapping and no
    clearing account to fall back to), the handler raises
    ProjectionStateError rather than silently skipping the order (A80)."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from accounts.models import Company
    from projections.exceptions import ProjectionStateError
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    uid = uuid4().hex[:8]
    company = Company.objects.create(
        public_id=uuid4(),
        name=f"No-Clearing Co {uid}",
        slug=f"no-clearing-{uid}",
        default_currency="USD",
        functional_currency="USD",
        is_active=True,
    )

    # SALES_REVENUE mapped (so handle() passes the mapping gate and reaches
    # the store check) but NO SHOPIFY_CLEARING and no 1150/11500 account, so
    # _ensure_shopify_sales_setup cannot create the posting profile.
    with projection_writes_allowed():
        revenue = Account.objects.projection().create(
            company=company,
            code="4000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company, module="shopify_connector", role="SALES_REVENUE", account=revenue
    )

    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain=f"no-clearing-{uid}.myshopify.com",
            access_token="test-token",
            status=ShopifyStore.Status.ACTIVE,
        )

    event = _make_order_event(
        company,
        shopify_order_id=134004,
        store_public_id=store.public_id,
        shop_domain=store.shop_domain,
    )

    with pytest.raises(ProjectionStateError):
        ShopifyAccountingHandler().handle(event)


# ---------------------------------------------------------------------------
# 3. Disconnected-but-IDENTIFIED store posts under its own store (the HIGH bug)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_disconnected_identified_store_posts_under_itself_not_other_active(shopify_company):  # noqa: F811
    """Regression for the A134 review's HIGH finding: an event identifying a
    DISCONNECTED store must NOT be re-homed to a *different* ACTIVE store.

    The identifier is authoritative; disconnect preserves the store's
    customer/posting-profile, so the order posts under its own (disconnected)
    store — never under the surviving active store (which would mis-attribute
    the money: the exact A57 bug)."""
    from sales.models import SalesInvoice
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    store_a = ShopifyStore.objects.get(company=shopify_company)  # stays ACTIVE
    store_b = _make_store(shopify_company, "store-b")
    store_b.status = ShopifyStore.Status.DISCONNECTED
    store_b.save(update_fields=["status"])

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134005,
        store_public_id=store_b.public_id,
        shop_domain=store_b.shop_domain,
    )

    ShopifyAccountingHandler().handle(event)

    invoice = SalesInvoice.objects.get(company=shopify_company, source="shopify", source_document_id="134005")
    assert invoice.customer_id == store_b.default_customer_id, "must post under the identified (disconnected) store B"
    assert invoice.customer_id != store_a.default_customer_id, "must NOT re-home to the surviving active store A (A57)"


@pytest.mark.django_db
def test_unresolvable_store_defers_while_fresh(shopify_company):  # noqa: F811
    """When an event names a store_public_id that matches NO row (store
    hard-deleted) and the event is fresh, the handler raises DeferEvent (quiet
    retry) — NOT ProjectionStateError (which re-hits Sentry every beat). The
    presence of an identifier means we never silently re-home to the active
    store."""
    from projections.base import DeferEvent
    from shopify_connector.projections import ShopifyAccountingHandler

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134006,
        store_public_id=uuid4(),  # matches no store row
        shop_domain="ghost-store.myshopify.com",
    )

    with pytest.raises(DeferEvent):
        ShopifyAccountingHandler().handle(event)


@pytest.mark.django_db
def test_unresolvable_old_event_raises_after_age_cap(shopify_company):  # noqa: F811
    """A134 review (medium): the order_paid defer is BOUNDED. Past 24h, a
    genuinely unresolvable event stops deferring and surfaces loudly
    (ProjectionStateError → ProjectionFailureLog) so it becomes operator-
    visible instead of re-scanning the company stream forever."""
    from datetime import timedelta

    from django.utils import timezone

    from events.models import BusinessEvent
    from projections.exceptions import ProjectionStateError
    from shopify_connector.projections import ShopifyAccountingHandler

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134008,
        store_public_id=uuid4(),  # matches no store row
    )
    # recorded_at is auto_now_add; force it 25h into the past via .update().
    BusinessEvent.objects.filter(pk=event.pk).update(recorded_at=timezone.now() - timedelta(hours=25))
    event.refresh_from_db()

    with pytest.raises(ProjectionStateError):
        ShopifyAccountingHandler().handle(event)


@pytest.mark.django_db
def test_two_active_stores_legacy_event_defers(shopify_company):  # noqa: F811
    """A134 review (medium): a legacy event with NO identifier is ambiguous
    when the company has 2+ active stores — defer rather than guess (and the
    age cap above eventually surfaces it if never resolved)."""
    from projections.base import DeferEvent
    from shopify_connector.projections import ShopifyAccountingHandler

    _make_store(shopify_company, "store-b")  # now two active stores

    event = _make_order_event(shopify_company, shopify_order_id=134009)  # no ids

    with pytest.raises(DeferEvent):
        ShopifyAccountingHandler().handle(event)


@pytest.mark.django_db
def test_defer_is_quiet_through_process_pending(shopify_company):  # noqa: F811
    """End-to-end defer contract: through process_pending, a deferred
    order_paid event produces NO ProjectionFailureLog (no Sentry noise), NO
    SalesInvoice, and stays unprocessed (no ProjectionAppliedEvent) so it can
    self-heal once a store reconnects."""
    from projections.models import ProjectionAppliedEvent, ProjectionFailureLog
    from sales.models import SalesInvoice
    from shopify_connector.projections import ShopifyAccountingHandler

    event = _make_order_event(
        shopify_company,
        shopify_order_id=134007,
        store_public_id=uuid4(),  # no matching store → fresh defer
    )

    processed = ShopifyAccountingHandler().process_pending(shopify_company)

    assert processed == 0
    assert (
        ProjectionFailureLog.objects.filter(company=shopify_company, projection_name="shopify_accounting").count() == 0
    ), "a defer must not write a ProjectionFailureLog (that would page the operator)"
    assert SalesInvoice.objects.filter(company=shopify_company, source="shopify").count() == 0
    assert not ProjectionAppliedEvent.objects.filter(
        company=shopify_company, projection_name="shopify_accounting", event=event
    ).exists(), "deferred event must remain unprocessed so it retries"


# ---------------------------------------------------------------------------
# 4. Backward-compat — legacy event with neither identifier
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_legacy_event_resolves_via_sole_active_store(shopify_company):  # noqa: F811
    """A legacy event carrying neither store_public_id nor shop_domain still
    resolves via the sole-active-store fallback (the single-store common
    case). This is the path the pre-existing e2e tests exercise."""
    from sales.models import SalesInvoice
    from shopify_connector.models import ShopifyStore
    from shopify_connector.projections import ShopifyAccountingHandler

    store = ShopifyStore.objects.get(company=shopify_company)

    event = _make_order_event(shopify_company, shopify_order_id=134007)  # no ids

    ShopifyAccountingHandler().handle(event)

    invoice = SalesInvoice.objects.get(company=shopify_company, source="shopify", source_document_id="134007")
    assert invoice.customer_id == store.default_customer_id


# ---------------------------------------------------------------------------
# 5. Health check flags an active store missing defaults
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_health_check_flags_second_store_missing_defaults(shopify_company):  # noqa: F811
    """shopify_health_check must report a *second* active store missing its
    sales-routing defaults even when the primary store is healthy."""
    from shopify_connector.management.commands.shopify_health_check import Command

    bare = _make_store(shopify_company, "unhealthy-store", with_setup=False)

    cmd = Command()
    report = cmd._build_report(shopify_company, window_days=7)
    problems = cmd._collect_problems(report)

    assert any(bare.shop_domain in p for p in problems), (
        f"health check should flag {bare.shop_domain} missing defaults; got {problems}"
    )


# ---------------------------------------------------------------------------
# 6. A136 (sibling) — disconnect_store must not pick an arbitrary store
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_disconnect_store_refuses_when_multiple_connected(shopify_company):  # noqa: F811
    """A136: with two connected stores and no store_public_id, disconnect_store
    must refuse rather than silently disconnecting an arbitrary one (the
    multi-store footgun that stranded the wrong store's sync)."""
    from accounts.authz import system_actor_for_company
    from shopify_connector.commands import disconnect_store
    from shopify_connector.models import ShopifyStore

    _make_store(shopify_company, "store-b")  # second connected store

    actor = system_actor_for_company(shopify_company)
    result = disconnect_store(actor)  # no store_public_id

    assert not result.success
    assert "specify which" in result.error.lower()
    # Nothing was disconnected.
    assert not ShopifyStore.objects.filter(company=shopify_company, status=ShopifyStore.Status.DISCONNECTED).exists()


@pytest.mark.django_db
def test_disconnect_store_by_id_targets_exact_store(shopify_company):  # noqa: F811
    """A136: an explicit store_public_id disconnects exactly that store and
    leaves the other connected store active."""
    from accounts.authz import system_actor_for_company
    from shopify_connector.commands import disconnect_store
    from shopify_connector.models import ShopifyStore

    store_a = ShopifyStore.objects.get(company=shopify_company)
    store_b = _make_store(shopify_company, "store-b")

    actor = system_actor_for_company(shopify_company)
    result = disconnect_store(actor, store_public_id=str(store_b.public_id))

    assert result.success
    store_a.refresh_from_db()
    store_b.refresh_from_db()
    assert store_b.status == ShopifyStore.Status.DISCONNECTED
    assert store_a.status == ShopifyStore.Status.ACTIVE


@pytest.mark.django_db
def test_disconnect_store_auto_selects_sole_connected(shopify_company):  # noqa: F811
    """A136: the single-store common case still auto-selects without an id."""
    from accounts.authz import system_actor_for_company
    from shopify_connector.commands import disconnect_store
    from shopify_connector.models import ShopifyStore

    actor = system_actor_for_company(shopify_company)
    result = disconnect_store(actor)

    assert result.success
    assert ShopifyStore.objects.get(company=shopify_company).status == ShopifyStore.Status.DISCONNECTED
