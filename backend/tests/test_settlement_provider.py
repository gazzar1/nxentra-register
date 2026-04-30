# tests/test_settlement_provider.py
"""
Tests for A2 + A2.5 — SettlementProvider routing primitive.

Covers:
- normalize_gateway_code canonical-form behavior
- bootstrap creates per-provider PostingProfile + SettlementProvider rows
  for the seven default Shopify provider codes, with provider_type set
- bootstrap is idempotent (re-run = no duplicates)
- _handle_order_paid routes to the provider's posting profile when the
  Shopify payload's gateway matches a known SettlementProvider row
- _handle_order_paid lazy-creates a needs_review row when the gateway is
  unknown, and posts via the fallback profile
- _handle_order_paid falls back to store.default_posting_profile when the
  payload carries no gateway at all
- SettlementProvider unique constraint is enforced
- Lookup helpers behave consistently across raw / normalized inputs
"""

from datetime import UTC

import pytest

from accounting.settlement_provider import SettlementProvider, normalize_gateway_code

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
        shop_domain="sp-bootstrap.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    return {"store": store, "clearing": clearing}


def test_bootstrap_creates_default_providers(shopify_with_clearing, company):
    # Seven active providers + the deprecated cash_on_delivery row (inactive,
    # preserved from A2 for historical compatibility). Each active row has
    # its own PostingProfile and an AnalysisDimensionValue for reconciliation.
    from accounting.models import AnalysisDimension, AnalysisDimensionValue
    from sales.models import PostingProfile
    from shopify_connector.commands import _ensure_shopify_sales_setup

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])

    expected_active_codes = {
        "paymob",
        "paypal",
        "shopify_payments",
        "manual",
        "bank_transfer",
        "bosta",
        "unknown",
    }

    rows = SettlementProvider.objects.filter(company=company, external_system="shopify")
    rows_by_code = {r.normalized_code: r for r in rows}

    # All seven active providers present
    active_codes = {r.normalized_code for r in rows if r.is_active}
    assert active_codes == expected_active_codes

    # cash_on_delivery row exists (from A2) but is deactivated by A12 — it
    # is no longer a provider; cash_on_delivery is a payment method that
    # routes to a real courier (Bosta / DHL / Aramex / ...) via
    # ShopifyStore.default_cod_settlement_provider.
    assert "cash_on_delivery" in rows_by_code
    assert rows_by_code["cash_on_delivery"].is_active is False

    # All providers anchored on Shopify clearing initially.
    for row in rows:
        assert row.posting_profile.control_account_id == shopify_with_clearing["clearing"].id
        assert row.needs_review is False
        # A12: every bootstrap row carries a dimension_value for the
        # reconciliation pivot.
        assert row.dimension_value_id is not None

    # provider_type populated correctly.
    expected_types = {
        "paymob": "gateway",
        "paypal": "gateway",
        "shopify_payments": "gateway",
        "manual": "manual",
        "bank_transfer": "bank_transfer",
        "bosta": "courier",  # A12: bosta replaces cash_on_delivery as the routable COD provider
        "unknown": "manual",
        "cash_on_delivery": "manual",
    }
    for code, expected_type in expected_types.items():
        assert rows_by_code[code].provider_type == expected_type, (
            f"{code} should be provider_type={expected_type}, got {rows_by_code[code].provider_type}"
        )

    # PostingProfiles: one per provider including cash_on_delivery (deactivation
    # of the SettlementProvider doesn't drop the PostingProfile — it's still
    # referenced by historical JEs).
    pg_profiles = PostingProfile.objects.filter(company=company, code__startswith="PG-")
    assert pg_profiles.count() == 8  # 7 active + 1 cash_on_delivery (deprecated)
    for profile in pg_profiles:
        assert profile.profile_type == PostingProfile.ProfileType.CUSTOMER
        assert profile.control_account_id == shopify_with_clearing["clearing"].id

    # A12: AnalysisDimension + values
    dim = AnalysisDimension.objects.get(company=company, code="SETTLEMENT_PROVIDER")
    assert dim.dimension_kind == AnalysisDimension.DimensionKind.CONTEXT
    values = AnalysisDimensionValue.objects.filter(dimension=dim)
    expected_value_codes = {c.upper() for c in expected_active_codes | {"cash_on_delivery"}}
    assert {v.code for v in values} == expected_value_codes


def test_bootstrap_is_idempotent(shopify_with_clearing, company):
    # Running setup twice must not create duplicate rows or profiles.
    from accounting.models import AnalysisDimension, AnalysisDimensionValue
    from sales.models import PostingProfile
    from shopify_connector.commands import _ensure_shopify_sales_setup

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])
    first_provider_count = SettlementProvider.objects.filter(company=company).count()
    first_profile_count = PostingProfile.objects.filter(company=company, code__startswith="PG-").count()
    first_dim_count = AnalysisDimension.objects.filter(company=company).count()
    first_value_count = AnalysisDimensionValue.objects.filter(company=company).count()

    _ensure_shopify_sales_setup(shopify_with_clearing["store"])

    assert SettlementProvider.objects.filter(company=company).count() == first_provider_count
    assert PostingProfile.objects.filter(company=company, code__startswith="PG-").count() == first_profile_count
    assert AnalysisDimension.objects.filter(company=company).count() == first_dim_count
    assert AnalysisDimensionValue.objects.filter(company=company).count() == first_value_count


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
    # When the order's gateway is "paymob" and a SettlementProvider row exists,
    # the invoice is posted using the provider's dedicated PostingProfile and
    # the AR Control JE line is tagged with the provider's
    # AnalysisDimensionValue for reconciliation.
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    provider = SettlementProvider.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="paymob",
    )
    store = shopify_setup_with_revenue["store"]
    assert provider.posting_profile_id != store.default_posting_profile_id, (
        "bootstrap must give each provider its own PostingProfile, not the store-level one"
    )
    assert provider.dimension_value_id is not None, "bootstrap must populate dimension_value"

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="paymob")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    proj._handle_order_paid(event, data, mapping)

    assert captured["kwargs"]["posting_profile_id"] == provider.posting_profile_id
    # A12: clearing JE line is tagged with the paymob dimension value.
    tags = captured["kwargs"]["control_line_analysis_tags"]
    assert len(tags) == 1
    assert tags[0]["value_public_id"] == str(provider.dimension_value.public_id)
    assert tags[0]["dimension_public_id"] == str(provider.dimension_value.dimension.public_id)


def test_handle_order_paid_cod_routes_via_default_cod_settlement_provider(
    shopify_setup_with_revenue, company, monkeypatch
):
    # A12 contract: cash_on_delivery orders do NOT look up the deprecated
    # cash_on_delivery SettlementProvider. They route via
    # ShopifyStore.default_cod_settlement_provider — Bosta / DHL / Aramex.
    # The clearing JE line is tagged with the courier's dimension value.
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    bosta = SettlementProvider.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="bosta",
    )
    assert bosta.provider_type == "courier"
    assert bosta.dimension_value_id is not None

    store = shopify_setup_with_revenue["store"]
    with command_writes_allowed():
        store.default_cod_settlement_provider = bosta
        store.save(update_fields=["default_cod_settlement_provider"])
    store.refresh_from_db()

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="cash_on_delivery")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    proj._handle_order_paid(event, data, mapping)

    assert captured["kwargs"]["posting_profile_id"] == bosta.posting_profile_id
    tags = captured["kwargs"]["control_line_analysis_tags"]
    assert len(tags) == 1
    assert tags[0]["value_public_id"] == str(bosta.dimension_value.public_id)


def test_handle_order_paid_cod_with_unset_default_lazy_creates_pending_setup(
    shopify_setup_with_revenue, company, monkeypatch
):
    # A12 safety net: a cash_on_delivery order arriving before the merchant
    # has configured ShopifyStore.default_cod_settlement_provider must NOT
    # silently mis-route. Lazy-create a `pending_cod_setup` row flagged for
    # review; order still posts via the fallback profile.
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    store = shopify_setup_with_revenue["store"]
    assert store.default_cod_settlement_provider_id is None, (
        "fixture default — merchant hasn't configured COD courier yet"
    )

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="cash_on_delivery")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    proj._handle_order_paid(event, data, mapping)

    pending = SettlementProvider.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="pending_cod_setup",
    )
    assert pending.needs_review is True
    assert pending.is_active is True
    assert pending.posting_profile_id == store.default_posting_profile_id
    # A12: lazy-created rows also get a dimension_value populated so the
    # JE line still carries a tag (operator can re-route later without
    # losing the order's reconciliation lineage).
    assert pending.dimension_value_id is not None

    assert captured["kwargs"]["posting_profile_id"] == store.default_posting_profile_id
    tags = captured["kwargs"]["control_line_analysis_tags"]
    assert len(tags) == 1
    assert tags[0]["value_public_id"] == str(pending.dimension_value.public_id)


def test_handle_order_paid_lazy_creates_unknown_gateway(shopify_setup_with_revenue, company, monkeypatch):
    # An order with a gateway code we've never seen lazy-creates a
    # SettlementProvider row with needs_review=True, and routes the invoice
    # via that row's posting profile (which equals the fallback default).
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="Tap Payments (KSA)")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    assert not SettlementProvider.objects.filter(
        company=company,
        normalized_code="tap_payments_ksa",
    ).exists(), "fixture must not pre-create the provider under test"

    proj._handle_order_paid(event, data, mapping)

    new_row = SettlementProvider.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="tap_payments_ksa",
    )
    assert new_row.needs_review is True
    assert new_row.is_active is True
    assert new_row.source_code == "Tap Payments (KSA)"
    assert new_row.provider_type == "manual", "lazy-create defaults provider_type to manual until human review"
    # A12: dimension_value populated even on lazy-create.
    assert new_row.dimension_value_id is not None
    # Lazy-created row points at the store-level default fallback profile —
    # the order still posts, just visibly flagged for operator review.
    store = shopify_setup_with_revenue["store"]
    assert new_row.posting_profile_id == store.default_posting_profile_id
    assert captured["kwargs"]["posting_profile_id"] == store.default_posting_profile_id


def test_handle_order_paid_with_empty_gateway_uses_default_profile(shopify_setup_with_revenue, company, monkeypatch):
    # Some early Shopify orders ship without a gateway at all (admin-paid
    # drafts, etc.). The router must fall back cleanly to the store-level
    # default profile and NOT lazy-create an empty-named row. No analysis
    # tag is applied (no provider to attribute it to).
    from shopify_connector.projections import ShopifyAccountingHandler

    captured = _routing_capture(monkeypatch)

    proj = ShopifyAccountingHandler()
    event, data = _fake_event(company, gateway="")
    mapping = {"SALES_REVENUE": shopify_setup_with_revenue["revenue"]}

    before = SettlementProvider.objects.filter(company=company).count()
    proj._handle_order_paid(event, data, mapping)
    after = SettlementProvider.objects.filter(company=company).count()

    assert before == after, "empty gateway must not create a SettlementProvider row"
    store = shopify_setup_with_revenue["store"]
    assert captured["kwargs"]["posting_profile_id"] == store.default_posting_profile_id
    assert captured["kwargs"]["control_line_analysis_tags"] == []


# =============================================================================
# Lookup helpers + invariants
# =============================================================================


def test_lookup_or_create_for_review_is_idempotent(shopify_setup_with_revenue, company):
    # Calling lazy-create twice for the same raw code returns the same row
    # — no duplicate, no constraint violation.
    store = shopify_setup_with_revenue["store"]

    row1 = SettlementProvider.lookup_or_create_for_review(
        company=company,
        external_system="shopify",
        raw_gateway="Some New Wallet",
        fallback_posting_profile=store.default_posting_profile,
    )
    row2 = SettlementProvider.lookup_or_create_for_review(
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
        SettlementProvider.lookup(
            company=company,
            external_system="shopify",
            raw_gateway="never_seen_before",
        )
        is None
    )
    assert (
        SettlementProvider.lookup(
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

    provider = SettlementProvider.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="paymob",
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        SettlementProvider.objects.create(
            company=company,
            external_system="shopify",
            source_code="paymob",
            normalized_code="paymob",
            display_name="Duplicate",
            posting_profile=provider.posting_profile,
        )


def test_external_system_scoping_allows_same_code_across_systems(shopify_setup_with_revenue, company):
    # paypal-from-Shopify and paypal-from-WooCommerce are not the same
    # routing decision. Same normalized_code under a different
    # external_system must coexist.
    shopify_paypal = SettlementProvider.objects.get(
        company=company,
        external_system="shopify",
        normalized_code="paypal",
    )
    woo_paypal = SettlementProvider.objects.create(
        company=company,
        external_system="woocommerce",
        source_code="paypal",
        normalized_code="paypal",
        display_name="WooCommerce PayPal",
        posting_profile=shopify_paypal.posting_profile,
    )
    assert woo_paypal.id != shopify_paypal.id
