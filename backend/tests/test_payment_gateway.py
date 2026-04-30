# tests/test_payment_gateway.py
"""
Tests for A2 — PaymentGateway routing primitive.

Covers:
- normalize_gateway_code canonical-form behavior
- bootstrap creates per-gateway PostingProfile + PaymentGateway rows for
  the seven default Shopify gateway codes
- bootstrap is idempotent (re-run = no duplicates)
- _handle_order_paid routes to the gateway's posting profile when the
  Shopify payload's gateway matches a known PaymentGateway row
- _handle_order_paid lazy-creates a needs_review row when the gateway is
  unknown, and posts via the fallback profile
- _handle_order_paid falls back to store.default_posting_profile when the
  payload carries no gateway at all
- PaymentGateway unique constraint is enforced
- Lookup helpers behave consistently across raw / normalized inputs
"""

from datetime import UTC

import pytest

from accounting.payment_gateway import PaymentGateway, normalize_gateway_code

# =============================================================================
# normalize_gateway_code
# =============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Canonical forms are stable
        ("paymob", "paymob"),
        ("PAYMOB", "paymob"),
        # Casing variants Shopify emits
        ("Paymob", "paymob"),
        # Punctuation + whitespace collapse to a single underscore
        ("Cash on Delivery", "cash_on_delivery"),
        ("Cash on Delivery (COD)", "cash_on_delivery_cod"),
        ("PayPal Express Checkout", "paypal_express_checkout"),
        # Strip leading/trailing junk
        (" paymob ", "paymob"),
        ("  paymob_accept  ", "paymob_accept"),
        # Empty / None → ""
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_gateway_code_canonicalizes(raw, expected):
    assert normalize_gateway_code(raw) == expected


# =============================================================================
# Bootstrap on _ensure_shopify_sales_setup
# =============================================================================


@pytest.fixture
def shopify_with_clearing(db, company):
    """Seed the SHOPIFY_CLEARING account + mapping that bootstrap depends on."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed
    from shopify_connector.models import ShopifyStore

    with projection_writes_allowed():
        clearing = Account.objects.projection().create(
            company=company,
            code="11500",
            name="Shopify Clearing",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SHOPIFY_CLEARING",
        account=clearing,
    )
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="pg-bootstrap.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    return {"store": store, "clearing": clearing}


def test_bootstrap_creates_seven_default_gateways(shopify_with_clearing, company):
    # All seven default gateways for Shopify get rows + dedicated profiles.
    # Each profile initially points at the same SHOPIFY_CLEARING account;
    # the merchant later edits any one to split a gateway off.
    from sales.models import PostingProfile
    from shopify_connector.commands import _ensure_shopify_sales_setup

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])

    expected_codes = {
        "paymob",
        "paypal",
        "manual",
        "shopify_payments",
        "cash_on_delivery",
        "bank_transfer",
        "unknown",
    }

    rows = PaymentGateway.objects.filter(company=company, external_system="shopify")
    assert {r.normalized_code for r in rows} == expected_codes
    # All anchored on Shopify clearing initially
    for row in rows:
        assert row.posting_profile.control_account_id == shopify_with_clearing["clearing"].id
        assert row.is_active is True
        assert row.needs_review is False

    # Seven dedicated posting profiles created (PG-* prefix)
    pg_profiles = PostingProfile.objects.filter(company=company, code__startswith="PG-")
    assert pg_profiles.count() == 7
    for profile in pg_profiles:
        assert profile.profile_type == PostingProfile.ProfileType.CUSTOMER
        assert profile.control_account_id == shopify_with_clearing["clearing"].id


def test_bootstrap_is_idempotent(shopify_with_clearing, company):
    # Running setup twice must not create duplicate rows or profiles.
    from sales.models import PostingProfile
    from shopify_connector.commands import _ensure_shopify_sales_setup

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])
    first_count = PaymentGateway.objects.filter(company=company).count()
    first_profile_count = PostingProfile.objects.filter(company=company, code__startswith="PG-").count()

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])

    assert PaymentGateway.objects.filter(company=company).count() == first_count
    assert PostingProfile.objects.filter(company=company, code__startswith="PG-").count() == first_profile_count


# =============================================================================
# Projection routing — _handle_order_paid
# =============================================================================


@pytest.fixture
def shopify_setup_with_revenue(shopify_with_clearing, company):
    """Extend the bootstrap fixture with a SALES_REVENUE account + mapping
    so _handle_order_paid runs to completion."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed
    from shopify_connector.commands import _ensure_shopify_sales_setup

    with projection_writes_allowed():
        revenue = Account.objects.projection().create(
            company=company,
            code="41000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    ModuleAccountMapping.objects.create(
        company=company,
        module="shopify_connector",
        role="SALES_REVENUE",
        account=revenue,
    )

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])
    shopify_with_clearing["store"].refresh_from_db()
    return {**shopify_with_clearing, "revenue": revenue}


def _fake_event(company, gateway: str = "", currency: str = "EGP"):
    """Build a stand-in event object for direct projection invocation."""

    from datetime import datetime

    class _E:
        pass

    e = _E()
    e.id = 1
    e.company = company
    e.event_type = "shopify.order.paid"
    e.metadata = {}
    e.created_at = datetime.now(UTC)

    data = {
        "amount": "500.00",
        "subtotal": "500.00",
        "total_tax": "0",
        "total_shipping": "0",
        "total_discounts": "0",
        "currency": currency,
        "transaction_date": "2026-04-29",
        "order_name": "#1001",
        "order_number": "1001",
        "shopify_order_id": "9000001",
        "financial_status": "paid",
        "gateway": gateway,
        "line_items": [],
    }
    return e, data


def _routing_capture(monkeypatch):
    """Patch create_and_post_invoice_for_platform so it records the
    posting_profile_id it was called with and returns a fake success
    without doing any JE work. Returns the captured-args holder."""
    captured = {}

    class _Result:
        success = True
        error = None
        data = {"invoice": None, "journal_entry": None}

    def _fake(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _Result()

    # Patch where the projection looks the symbol up (it imports inside the function).
    monkeypatch.setattr("sales.commands.create_and_post_invoice_for_platform", _fake)
    return captured


def test_handle_order_paid_routes_to_paymob_profile(shopify_setup_with_revenue, company, monkeypatch):
    # When the order's gateway is "paymob" and a PaymentGateway row exists,
    # the invoice is posted using the gateway's dedicated PostingProfile —
    # not the store-level default profile.
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    pg = PaymentGateway.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="paymob",
    )
    store = shopify_setup_with_revenue["store"]
    assert pg.posting_profile_id != store.default_posting_profile_id, (
        "bootstrap must give each gateway its own PostingProfile, not the store-level one"
    )

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="paymob")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    proj._handle_order_paid(event, data, mapping)

    assert captured["kwargs"]["posting_profile_id"] == pg.posting_profile_id


def test_handle_order_paid_lazy_creates_unknown_gateway(shopify_setup_with_revenue, company, monkeypatch):
    # An order with a gateway code we've never seen lazy-creates a
    # PaymentGateway row with needs_review=True, and routes the invoice
    # via that row's posting profile (which equals the fallback default).
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="Tap Payments (KSA)")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    assert not PaymentGateway.objects.filter(
        company=company,
        normalized_code="tap_payments_ksa",
    ).exists(), "fixture must not pre-create the gateway under test"

    proj._handle_order_paid(event, data, mapping)

    new_row = PaymentGateway.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="tap_payments_ksa",
    )
    assert new_row.needs_review is True
    assert new_row.is_active is True
    assert new_row.source_code == "Tap Payments (KSA)"
    # Lazy-created row points at the store-level default fallback profile —
    # the order still posts, just visibly flagged for operator review.
    store = shopify_setup_with_revenue["store"]
    assert new_row.posting_profile_id == store.default_posting_profile_id
    assert captured["kwargs"]["posting_profile_id"] == store.default_posting_profile_id


def test_handle_order_paid_with_empty_gateway_uses_default_profile(shopify_setup_with_revenue, company, monkeypatch):
    # Some early Shopify orders ship without a gateway at all (admin-paid
    # drafts, etc.). The router must fall back cleanly to the store-level
    # default profile and NOT lazy-create an empty-named row.
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    before = PaymentGateway.objects.filter(company=company).count()
    proj._handle_order_paid(event, data, mapping)
    after = PaymentGateway.objects.filter(company=company).count()

    assert before == after, "empty gateway must not create a PaymentGateway row"
    store = shopify_setup_with_revenue["store"]
    assert captured["kwargs"]["posting_profile_id"] == store.default_posting_profile_id


# =============================================================================
# Lookup helpers + invariants
# =============================================================================


def test_lookup_or_create_for_review_is_idempotent(shopify_setup_with_revenue, company):
    # Calling lazy-create twice for the same raw code returns the same row
    # — no duplicate, no constraint violation.
    store = shopify_setup_with_revenue["store"]

    row1 = PaymentGateway.lookup_or_create_for_review(
        company=company,
        external_system="shopify",
        raw_gateway="Some New Wallet",
        fallback_posting_profile=store.default_posting_profile,
    )
    row2 = PaymentGateway.lookup_or_create_for_review(
        company=company,
        external_system="shopify",
        raw_gateway="some new wallet",  # different casing — same normalized form
        fallback_posting_profile=store.default_posting_profile,
    )
    assert row1.id == row2.id
    assert row1.needs_review is True


def test_lookup_returns_none_for_unknown_or_empty(shopify_setup_with_revenue, company):
    # `lookup` must NOT auto-create — that's `lookup_or_create_for_review`.
    assert (
        PaymentGateway.lookup(
            company=company,
            external_system="shopify",
            raw_gateway="never_seen_before",
        )
        is None
    )
    assert (
        PaymentGateway.lookup(
            company=company,
            external_system="shopify",
            raw_gateway="",
        )
        is None
    )


def test_unique_constraint_per_company_external_system_normalized(shopify_setup_with_revenue, company):
    # Bootstrap already created a paymob row; trying to create a duplicate
    # must violate the unique constraint.
    from django.db import IntegrityError, transaction

    pg = PaymentGateway.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="paymob",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentGateway.objects.create(
            company=company,
            external_system="shopify",
            source_code="paymob",
            normalized_code="paymob",
            display_name="Duplicate",
            posting_profile=pg.posting_profile,
        )


def test_external_system_scoping_allows_same_code_across_systems(shopify_setup_with_revenue, company):
    # paypal-from-Shopify and paypal-from-WooCommerce are not the same
    # routing decision. Same normalized_code under a different
    # external_system must coexist.
    pg_shopify_paypal = PaymentGateway.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="paypal",
    )
    woo_paypal = PaymentGateway.objects.create(
        company=company,
        external_system="woocommerce",
        source_code="paypal",
        normalized_code="paypal",
        display_name="WooCommerce PayPal",
        posting_profile=pg_shopify_paypal.posting_profile,
    )
    assert woo_paypal.id != pg_shopify_paypal.id
