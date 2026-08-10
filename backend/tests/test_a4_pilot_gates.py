# tests/test_a4_pilot_gates.py
"""A4: constrained-pilot capability gates + Option B + preflight/activation.

Proves technical enforcement at the runtime boundaries (not UI hiding): direct
backend calls fail even when the frontend is bypassed; scheduled paths skip with
zero mutation; Option B keeps inventory non-executable; and the preflight detects
every seeded violation. NONE-profile companies retain existing behavior.
"""

from uuid import uuid4

import pytest

from accounts.models import Company, CompanyMembership
from accounts.pilot_policy import (
    Capability,
    PilotScopeBlocked,
    inventory_forced_non_stock,
    is_supported,
    skip_if_unsupported,
)

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_pilot(company, *, currency="EGP", with_periods=True):
    company.pilot_profile = ISO
    company.default_currency = currency
    company.functional_currency = currency
    company.fiscal_year_start_month = 1
    company.save()
    if with_periods:
        from projections.models import FiscalPeriodConfig

        FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})
    return company


def _grant(company, membership, *codes):
    from accounts.models import CompanyMembershipPermission, NxPermission

    for code in codes:
        perm, _ = NxPermission.objects.get_or_create(code=code, defaults={"name": code})
        CompanyMembershipPermission.objects.get_or_create(company=company, membership=membership, permission=perm)


def _actor(company):
    from accounts.authz import system_actor_for_company

    return system_actor_for_company(company)


# --------------------------------------------------------------------------- #
# policy core
# --------------------------------------------------------------------------- #
def test_none_profile_supports_everything():
    c = Company(pilot_profile=Company.PilotProfile.NONE)
    for cap in Capability:
        assert is_supported(c, cap) is True


def test_pilot_blocks_all_capabilities():
    c = Company(pilot_profile=ISO)
    for cap in Capability:
        assert is_supported(c, cap) is False


def test_unknown_profile_fails_closed():
    c = Company(pilot_profile="SOMETHING_ELSE")
    for cap in Capability:
        assert is_supported(c, cap) is False


def test_skip_helper_semantics():
    pilot = Company(pilot_profile=ISO, id=1)
    out = skip_if_unsupported(pilot, Capability.STRIPE, task="t")
    assert out == {"status": "skipped_pilot_scope", "capability": "stripe", "company_id": 1}
    assert skip_if_unsupported(Company(pilot_profile="NONE", id=2), Capability.STRIPE) is None


# --------------------------------------------------------------------------- #
# rebuild / replay — one shared choke point
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_rebuild_blocked_for_pilot(company):
    from projections.base import BaseProjection

    class _Dummy(BaseProjection):
        name = "a4_dummy"
        consumes: list = []

        def handle(self, event):
            pass

    _make_pilot(company)
    with pytest.raises(PilotScopeBlocked):
        _Dummy().rebuild(company)


@pytest.mark.django_db
def test_rebuild_allowed_for_normal_company(company):
    from projections.base import BaseProjection

    class _Dummy(BaseProjection):
        name = "a4_dummy_ok"
        consumes: list = []

        def handle(self, event):
            pass

    # NONE profile: rebuild proceeds (0 events → returns 0, no raise).
    assert _Dummy().rebuild(company) == 0


# --------------------------------------------------------------------------- #
# Stripe — interactive connect blocked, scheduled sync skipped
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_connect_stripe_blocked_for_pilot(company):
    from stripe_connector.commands import connect_stripe_account

    _make_pilot(company)
    with pytest.raises(PilotScopeBlocked):
        connect_stripe_account(company, "rk_test_whatever")


@pytest.mark.django_db
def test_platform_webhook_capability_blocked(company):
    _make_pilot(company)
    assert is_supported(company, Capability.STRIPE) is False


# --------------------------------------------------------------------------- #
# Shopify Payments payout accounting — scheduled skip, no event
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_shopify_payout_sync_skips_for_pilot(company):
    from events.models import BusinessEvent
    from shopify_connector.commands import sync_payouts
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="p.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    before = BusinessEvent.objects.filter(company=company).count()
    result = sync_payouts(store)
    assert result.success
    assert result.data["status"] == "skipped_pilot_scope"
    # No SHOPIFY_PAYOUT_SETTLED (or any) event emitted.
    assert BusinessEvent.objects.filter(company=company).count() == before


# --------------------------------------------------------------------------- #
# single-user + one-merchant-per-deployment
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_add_member_blocked_for_pilot(company, user, owner_membership):
    from accounts.commands import create_invitation

    _make_pilot(company)
    _grant(company, owner_membership, "company.manage_users")
    with pytest.raises(PilotScopeBlocked):
        create_invitation(_actor(company), email="new@test.com", role="ADMIN")


@pytest.mark.django_db
def test_second_company_blocked_when_pilot_active(company, user):
    from accounts.commands import create_company

    _make_pilot(company)
    result = create_company(user, "Another Merchant")
    assert not result.success
    assert "constrained-pilot" in result.error


@pytest.mark.django_db
def test_register_blocked_when_pilot_active(company):
    from accounts.commands import register_signup

    _make_pilot(company)
    result = register_signup(email="brand-new@test.com", password="Testpass123!", name="X", company_name="New Co")
    assert not result.success


# --------------------------------------------------------------------------- #
# unsafe bank actions (auto-match) blocked; manual retained
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_auto_match_blocked_for_pilot(company, user, owner_membership):
    from reconciliation.commands import auto_match_statement

    _make_pilot(company)
    _grant(company, owner_membership, "accounting.reconciliation")
    with pytest.raises(PilotScopeBlocked):
        auto_match_statement(_actor(company), statement_id=999999)


# --------------------------------------------------------------------------- #
# Option B — inventory non-executable
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_inventory_forced_non_stock_flag(company):
    _make_pilot(company)
    assert inventory_forced_non_stock(company) is True
    company.pilot_profile = Company.PilotProfile.NONE
    company.save()
    assert inventory_forced_non_stock(company) is False


@pytest.mark.django_db
def test_manual_inventory_item_blocked(company, user, owner_membership):
    from sales.commands import create_item

    _make_pilot(company)
    _grant(company, owner_membership, "sales.item.create")
    # Even attempting an INVENTORY item is blocked.
    with pytest.raises(PilotScopeBlocked):
        create_item(_actor(company), code="SKU1", name="Widget", item_type="INVENTORY")


@pytest.mark.django_db
def test_non_stock_item_allowed_for_pilot(company, user, owner_membership):
    from sales.commands import create_item
    from sales.models import Item

    _make_pilot(company)
    _grant(company, owner_membership, "sales.item.create")
    result = create_item(_actor(company), code="SKU2", name="Service", item_type="NON_STOCK")
    assert result.success
    item = Item.objects.get(company=company, code="SKU2")
    assert item.item_type == Item.ItemType.NON_STOCK
    assert item.inventory_account_id is None
    assert item.cogs_account_id is None


@pytest.mark.django_db
def test_shopify_item_creation_forced_non_stock(company):
    """The product-sync creation path downgrades to NON_STOCK for a pilot company
    even when inventory/COGS accounts are supplied."""
    from accounting.models import Account
    from sales.models import Item
    from shopify_connector import commands as sc

    _make_pilot(company)
    inv = Account.objects.create(company=company, code="1300", name="Inv", account_type="ASSET")
    cogs = Account.objects.create(company=company, code="5000", name="COGS", account_type="EXPENSE")
    sc._create_item_from_variant(
        company=company,
        sku="SH1",
        product_title="P",
        variant_title="V",
        price=10,
        cost=4,
        inv_account=inv,
        cogs_account=cogs,
        sales_account=None,
        purchase_account=None,
    )
    item = Item.objects.get(company=company, code="SH1")
    assert item.item_type == Item.ItemType.NON_STOCK
    assert item.inventory_account_id is None
    assert item.cogs_account_id is None


# --------------------------------------------------------------------------- #
# preflight — clean pass + every seeded violation detected
# --------------------------------------------------------------------------- #
def _seed_go_live_readiness(company, store, owner_membership, *, with_provider=True, with_bank=True):
    """A go-live-clean pilot: EGP store, exact OWNER binding, postable
    clearing/EBD mappings, an active Paymob provider routed to an active posting
    profile, and a postable Cash/Bank GL account."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from accounting.settlement_provider import SettlementProvider
    from sales.models import PostingProfile
    from shopify_connector.models import ShopifyUserBinding

    store.shop_currency = "EGP"
    store.save(update_fields=["shop_currency"])
    ShopifyUserBinding.objects.create(
        store=store, shopify_sub="gid://shopify/StaffMember/1", membership=owner_membership, is_active=True
    )
    for role, code in (("SHOPIFY_CLEARING", "11500"), ("EXPECTED_BANK_DEPOSIT", "11600")):
        acct = Account.objects.create(
            company=company, code=code, name=role, account_type="ASSET", status="ACTIVE", is_header=False
        )
        ModuleAccountMapping.objects.create(company=company, module="shopify_connector", role=role, account=acct)
    if with_bank:
        Account.objects.create(
            company=company,
            code="11000",
            name="Bank EGP",
            account_type="ASSET",
            role="LIQUIDITY",
            status="ACTIVE",
            is_header=False,
        )
    if with_provider:
        control = Account.objects.create(
            company=company, code="11700", name="Paymob Clearing", account_type="ASSET", status="ACTIVE"
        )
        profile = PostingProfile.objects.create(
            company=company,
            code="PAYMOB",
            name="Paymob",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            usage=PostingProfile.Usage.GATEWAY,
            control_account=control,
            is_active=True,
        )
        SettlementProvider.objects.create(
            company=company,
            external_system="shopify",
            source_code="paymob",
            normalized_code="paymob",
            display_name="Paymob",
            provider_type="gateway",
            posting_profile=profile,
            is_active=True,
        )


@pytest.mark.django_db
def test_preflight_passes_clean_company(company, user, owner_membership):
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="c.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    _seed_go_live_readiness(company, store, owner_membership)
    violations = pilot_policy_run(company, phase="go-live")
    assert violations == [], violations


@pytest.mark.django_db
def test_preflight_go_live_requires_binding(company, user, owner_membership):
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="b.myshopify.com",
        access_token="x",
        status=ShopifyStore.Status.ACTIVE,
        shop_currency="EGP",
    )
    # Seed mappings but NOT the binding → binding_missing fires, mapping doesn't.
    for role, code in (("SHOPIFY_CLEARING", "11500"), ("EXPECTED_BANK_DEPOSIT", "11600")):
        acct = Account.objects.create(
            company=company, code=code, name=role, account_type="ASSET", status="ACTIVE", is_header=False
        )
        ModuleAccountMapping.objects.create(company=company, module="shopify_connector", role=role, account=acct)
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "binding_missing" in codes
    assert "missing_supported_mapping" not in codes


@pytest.mark.django_db
def test_preflight_binding_on_other_store_does_not_satisfy(company, user, owner_membership):
    """A binding that exists but joins a DISCONNECTED store must not pass."""
    from shopify_connector.models import ShopifyStore, ShopifyUserBinding

    _make_pilot(company)
    ShopifyStore.objects.create(
        company=company,
        shop_domain="live.myshopify.com",
        access_token="x",
        status=ShopifyStore.Status.ACTIVE,
        shop_currency="EGP",
    )
    old_store = ShopifyStore.objects.create(
        company=company, shop_domain="old.myshopify.com", access_token="x", status=ShopifyStore.Status.DISCONNECTED
    )
    ShopifyUserBinding.objects.create(
        store=old_store, shopify_sub="gid://shopify/StaffMember/9", membership=owner_membership, is_active=True
    )
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "binding_missing" in codes


@pytest.mark.django_db
def test_preflight_go_live_requires_clearing_mapping(company, user, owner_membership):
    from shopify_connector.models import ShopifyStore, ShopifyUserBinding

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="m.myshopify.com",
        access_token="x",
        status=ShopifyStore.Status.ACTIVE,
        shop_currency="EGP",
    )
    ShopifyUserBinding.objects.create(
        store=store, shopify_sub="gid://shopify/StaffMember/2", membership=owner_membership, is_active=True
    )
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "missing_supported_mapping" in codes


@pytest.mark.django_db
def test_preflight_detects_legacy_bank_data(company, user, owner_membership):
    from bank_connector.models import BankAccount

    _make_pilot(company)
    BankAccount.objects.create(company=company, bank_name="Legacy Bank", account_name="Legacy Main")
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "legacy_bank_data" in codes


@pytest.mark.django_db
def test_preflight_setup_allows_zero_stores(company, user, owner_membership):
    _make_pilot(company)
    assert pilot_policy_run(company, phase="setup") == []


@pytest.mark.django_db
def test_preflight_detects_non_egp(company, user, owner_membership):
    _make_pilot(company, currency="USD")
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "currency_not_egp" in codes


@pytest.mark.django_db
def test_preflight_detects_extra_membership(company, user, owner_membership, admin_user):
    _make_pilot(company)
    CompanyMembership.objects.create(
        public_id=uuid4(), company=company, user=admin_user, role=CompanyMembership.Role.ADMIN, is_active=True
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "membership_count" in codes


@pytest.mark.django_db
def test_preflight_detects_inventory_item(company, user, owner_membership):
    from sales.models import Item

    _make_pilot(company)
    Item.objects.create(company=company, code="INV", name="I", item_type=Item.ItemType.NON_STOCK)
    # Flip to INVENTORY directly (bypassing the gate) to simulate drift.
    Item.objects.filter(company=company, code="INV").update(item_type=Item.ItemType.INVENTORY)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "inventory_items" in codes


@pytest.mark.django_db
def test_preflight_detects_second_active_company(company, user, owner_membership, second_company):
    _make_pilot(company)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "not_isolated" in codes


# --------------------------------------------------------------------------- #
# activation — refuse dirty, succeed clean, transactional
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
def test_activation_refuses_dirty_company(company, user, owner_membership):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    # Not EGP → forbidden state → activation refused, profile unchanged.
    company.default_currency = "USD"
    company.functional_currency = "USD"
    company.save()
    with pytest.raises(CommandError):
        call_command("activate_pilot_profile", "--company", str(company.id), "--yes")
    company.refresh_from_db()
    assert company.pilot_profile == Company.PilotProfile.NONE


@pytest.mark.django_db
def test_activation_succeeds_clean_company(company, user, owner_membership):
    from django.core.management import call_command

    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})

    call_command("activate_pilot_profile", "--company", str(company.id), "--yes")
    company.refresh_from_db()
    assert company.pilot_profile == ISO


# --------------------------------------------------------------------------- #
# A4 correction pass — disputes, per-platform webhook map, payout, no-FX,
# EGP ingestion, is_active freeze, activation audit, expanded preflight.
# --------------------------------------------------------------------------- #
def test_dispute_capability_present_and_blocked():
    pilot = Company(pilot_profile=ISO)
    normal = Company(pilot_profile=Company.PilotProfile.NONE)
    assert is_supported(pilot, Capability.SHOPIFY_DISPUTES) is False
    assert is_supported(normal, Capability.SHOPIFY_DISPUTES) is True


@pytest.mark.django_db
def test_process_dispute_skips_for_pilot_no_row_no_event(company):
    from events.models import BusinessEvent
    from shopify_connector.commands import process_dispute
    from shopify_connector.models import ShopifyDispute, ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="d.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    before = BusinessEvent.objects.filter(company=company).count()
    result = process_dispute(store, {"id": "dp_1", "status": "needs_response", "amount": "100", "currency": "EGP"})
    assert result.success
    assert result.data["status"] == "skipped_pilot_scope"
    assert result.data["capability"] == "shopify_disputes"
    assert not ShopifyDispute.objects.filter(company=company).exists()
    assert BusinessEvent.objects.filter(company=company).count() == before


def test_required_capability_map_per_platform_topic():
    from platform_connectors.views import _required_capability

    # Stripe: fully out of scope for every topic (including the early None call).
    assert _required_capability("stripe", None) == Capability.STRIPE
    assert _required_capability("stripe", "order_paid") == Capability.STRIPE
    # Shopify: payout + dispute blocked; order/refund/fulfillment in scope.
    assert _required_capability("shopify", "payout_settled") == Capability.SHOPIFY_PAYOUT_ACCOUNTING
    assert _required_capability("shopify", "dispute_created") == Capability.SHOPIFY_DISPUTES
    assert _required_capability("shopify", "order_paid") is None
    assert _required_capability("shopify", "refund_created") is None
    assert _required_capability("shopify", "fulfillment_created") is None
    assert _required_capability("shopify", None) is None
    # Unknown platform: in scope by default.
    assert _required_capability("other", "order_paid") is None


# --- currency ingestion helpers -------------------------------------------- #
def test_require_pilot_currency_semantics():
    from accounts.pilot_policy import require_pilot_currency

    pilot = Company(pilot_profile=ISO, id=1)
    normal = Company(pilot_profile=Company.PilotProfile.NONE, id=2)
    # EGP + empty are accepted; foreign is rejected.
    require_pilot_currency(pilot, "EGP")
    require_pilot_currency(pilot, "")
    require_pilot_currency(pilot, None)
    with pytest.raises(PilotScopeBlocked):
        require_pilot_currency(pilot, "USD")
    # NONE profile is never restricted.
    require_pilot_currency(normal, "USD")


def test_skip_pilot_currency_semantics():
    from accounts.pilot_policy import skip_pilot_currency

    pilot = Company(pilot_profile=ISO, id=7)
    assert skip_pilot_currency(pilot, "EGP") is None
    assert skip_pilot_currency(pilot, "") is None
    out = skip_pilot_currency(pilot, "usd", task="t")
    assert out["status"] == "skipped_pilot_scope"
    assert out["capability"] == "non_egp_ingestion"
    assert out["currency"] == "USD"
    assert skip_pilot_currency(Company(pilot_profile="NONE", id=8), "USD") is None


@pytest.mark.django_db
def test_bank_import_rejects_non_egp_before_write(company, owner_membership):
    from accounting.bank_reconciliation import import_bank_statement
    from accounting.models import Account, BankStatement

    _make_pilot(company)
    _grant(company, owner_membership, "accounting.reconciliation")
    acct = Account.objects.create(company=company, code="1000", name="Cash", account_type="ASSET")
    with pytest.raises(PilotScopeBlocked):
        import_bank_statement(
            _actor(company),
            account_id=acct.id,
            statement_date=__import__("datetime").date(2026, 1, 31),
            period_start=__import__("datetime").date(2026, 1, 1),
            period_end=__import__("datetime").date(2026, 1, 31),
            opening_balance=0,
            closing_balance=0,
            lines_data=[{"line_date": "2026-01-15", "description": "x", "amount": "10"}],
            currency="USD",
        )
    assert not BankStatement.objects.filter(company=company).exists()


@pytest.mark.django_db
def test_settlement_import_rejects_non_egp_before_emission(company, monkeypatch):
    from accounting import settlement_imports as si
    from events.models import BusinessEvent

    _make_pilot(company)

    class _Spec:
        default_method = "card"

        def parse(self, content):
            return [{"currency": "USD", "payout_batch_id": "B1", "gross_amount": "100"}]

    monkeypatch.setattr(si, "get_settlement_parser", lambda code: _Spec())
    before = BusinessEvent.objects.filter(company=company).count()
    with pytest.raises(PilotScopeBlocked):
        si.import_settlement_csv(company, "paymob", b"irrelevant")
    assert BusinessEvent.objects.filter(company=company).count() == before


@pytest.mark.django_db
def test_shopify_order_non_egp_skipped(company):
    from events.models import BusinessEvent
    from shopify_connector.commands import process_order_paid
    from shopify_connector.models import ShopifyOrder, ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="o.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    before = BusinessEvent.objects.filter(company=company).count()
    result = process_order_paid(
        store, {"id": "111", "currency": "USD", "total_price": "50", "created_at": "2026-01-05T00:00:00Z"}
    )
    assert result.success
    assert result.data["status"] == "skipped_pilot_scope"
    assert not ShopifyOrder.objects.filter(company=company, shopify_order_id="111").exists()
    assert BusinessEvent.objects.filter(company=company).count() == before


# --- Option B / no-FX ------------------------------------------------------ #
@pytest.mark.django_db
def test_variant_cost_fetch_skips_fx_for_pilot(company, monkeypatch):
    from decimal import Decimal

    from accounting.models import ExchangeRate
    from shopify_connector import commands as sc

    _make_pilot(company)
    store = sc.ShopifyStore.objects.create(
        company=company, shop_domain="f.myshopify.com", access_token="x", status=sc.ShopifyStore.Status.ACTIVE
    )
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("ExchangeRate.get_rate must not be reached for a pilot")

    monkeypatch.setattr(ExchangeRate, "get_rate", classmethod(_boom))
    # No Shopify admin client is created and no FX rate is fetched.
    assert sc._fetch_variant_cost(store, "v1", convert_to_currency="EGP") == Decimal("0")
    assert sc._convert_costs_to_functional(store, None, {"i1": Decimal("5")}) == {"i1": Decimal("5")}
    assert called["n"] == 0


@pytest.mark.django_db
def test_update_item_defaults_never_attaches_inv_cogs_for_pilot(company):
    from accounting.models import Account
    from sales.models import Item
    from shopify_connector.commands import _update_item_defaults

    _make_pilot(company)
    item = Item.objects.create(company=company, code="U1", name="U", item_type=Item.ItemType.NON_STOCK)
    inv = Account.objects.create(company=company, code="1300", name="Inv", account_type="ASSET")
    cogs = Account.objects.create(company=company, code="5000", name="COGS", account_type="EXPENSE")
    _update_item_defaults(item, cost=4, inv_account=inv, cogs_account=cogs, sales_account=None, purchase_account=None)
    item.refresh_from_db()
    assert item.inventory_account_id is None
    assert item.cogs_account_id is None


@pytest.mark.django_db
def test_setup_shopify_accounts_omits_inv_cogs_mapping_for_pilot(company):
    from accounting.mappings import ModuleAccountMapping
    from accounts.commands import _setup_shopify_accounts

    _make_pilot(company)
    _setup_shopify_accounts(company)
    roles = set(
        ModuleAccountMapping.objects.filter(company=company, module="shopify_connector").values_list("role", flat=True)
    )
    assert "INVENTORY" not in roles
    assert "COGS" not in roles
    assert "SALES_REVENUE" in roles  # non-inventory roles still wired


# --- currency / fiscal / membership gates ---------------------------------- #
@pytest.mark.django_db
def test_currency_change_blocked_for_pilot(company, owner_membership):
    from accounts.commands import update_company_settings

    _make_pilot(company)
    _grant(company, owner_membership, "company.settings.update")
    with pytest.raises(PilotScopeBlocked):
        update_company_settings(_actor(company), default_currency="USD")


@pytest.mark.django_db
def test_fiscal_change_blocked_for_pilot(company, owner_membership):
    from accounts.commands import update_company_settings

    _make_pilot(company)
    _grant(company, owner_membership, "company.settings.update")
    with pytest.raises(PilotScopeBlocked):
        update_company_settings(_actor(company), fiscal_year_start_month=4)


@pytest.mark.django_db
def test_non_currency_setting_still_allowed_for_pilot(company, owner_membership):
    from accounts.commands import update_company_settings

    _make_pilot(company)
    _grant(company, owner_membership, "company.settings.update")
    result = update_company_settings(_actor(company), name="Renamed Co")
    assert result.success


@pytest.mark.django_db
def test_configure_periods_blocked_for_pilot(company, owner_membership):
    from accounting.commands import configure_periods

    _make_pilot(company)
    _grant(company, owner_membership, "periods.configure")
    with pytest.raises(PilotScopeBlocked):
        configure_periods(_actor(company), fiscal_year=2026)


@pytest.mark.django_db
def test_create_user_with_membership_blocked_for_pilot(company, owner_membership):
    from accounts.commands import create_user_with_membership

    _make_pilot(company)
    _grant(company, owner_membership, "company.manage_users")
    with pytest.raises(PilotScopeBlocked):
        create_user_with_membership(_actor(company), email="x@test.com", name="X", password="Testpass123!")


# --- is_active freeze ------------------------------------------------------ #
@pytest.mark.django_db
def test_pilot_company_is_active_frozen(company):
    _make_pilot(company)
    company.is_active = False
    with pytest.raises(PilotScopeBlocked):
        company.save()
    company.refresh_from_db()
    assert company.is_active is True


@pytest.mark.django_db
def test_non_pilot_company_is_active_mutable(company):
    # NONE profile: deactivation proceeds normally.
    company.is_active = False
    company.save()
    company.refresh_from_db()
    assert company.is_active is False


@pytest.mark.django_db
def test_admin_change_form_cannot_deactivate_pilot_company(company):
    """The Django admin change form is the ONLY reachable writer of
    Company.is_active (no command/view/.update() path exists — audited). Prove the
    real admin save_model path is blocked, not just a bare model.save()."""
    from django.contrib import admin as dj_admin

    from accounts.admin import CompanyAdmin

    _make_pilot(company)
    model_admin = CompanyAdmin(Company, dj_admin.site)
    company.is_active = False
    with pytest.raises(PilotScopeBlocked):
        # Default ModelAdmin.save_model(request, obj, form, change) → obj.save().
        model_admin.save_model(request=None, obj=company, form=None, change=True)
    company.refresh_from_db()
    assert company.is_active is True


@pytest.mark.django_db
def test_queryset_update_bypasses_signal_documented(company):
    """Documents WHY the audit (no reachable .update()/bulk_update path exists) is
    load-bearing: QuerySet.update() does not fire pre_save, so the guard would not
    catch it. No application/admin path uses it — enforced by the audit, asserted
    here so a future .update(is_active=...) is a conscious, reviewed choice."""
    _make_pilot(company)
    # Raw ORM .update() intentionally bypasses the signal (Django semantics).
    Company.objects.filter(pk=company.pk).update(is_active=False)
    company.refresh_from_db()
    assert company.is_active is False  # bypass confirmed → guard is not a DB constraint
    # Restore for isolation.
    Company.objects.filter(pk=company.pk).update(is_active=True)


# --- activation audit + write-guard ---------------------------------------- #
@pytest.mark.django_db
def test_activation_writes_audit_row_in_same_transaction(company, owner_membership):
    from django.core.management import call_command

    from accounts.models import PilotProfileActivation

    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})

    call_command("activate_pilot_profile", "--company", str(company.id), "--yes")
    company.refresh_from_db()
    assert company.pilot_profile == ISO
    audit = PilotProfileActivation.objects.filter(company=company).first()
    assert audit is not None
    assert audit.profile == ISO
    assert audit.source == "cli"


# --- expanded preflight: one seeded violation per new code ----------------- #
@pytest.mark.django_db
def test_preflight_detects_other_company_row(company, owner_membership, second_company):
    # second_company exists (any state) → not_isolated_rows at activation.
    _make_pilot(company)
    codes = {v.code for v in _run_preflight_activation(company)}
    assert "not_isolated_rows" in codes


@pytest.mark.django_db
def test_preflight_detects_inv_cogs_module_mapping(company, owner_membership):
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account

    _make_pilot(company)
    acct = Account.objects.create(company=company, code="1300", name="Inv", account_type="ASSET")
    ModuleAccountMapping.objects.create(company=company, module="shopify_connector", role="INVENTORY", account=acct)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "module_inv_cogs_mapping" in codes


@pytest.mark.django_db
def test_preflight_detects_payout_and_dispute_data(company, owner_membership):
    from shopify_connector.models import ShopifyDispute, ShopifyPayout, ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="s.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    ShopifyPayout.objects.create(
        company=company,
        store=store,
        shopify_payout_id=12345,
        currency="EGP",
        gross_amount=0,
        fees=0,
        net_amount=0,
        payout_date="2026-01-10",
    )
    ShopifyDispute.objects.create(
        company=company, store=store, shopify_dispute_id=67890, currency="EGP", amount=0, fee=0
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "payout_data" in codes
    assert "dispute_data" in codes


@pytest.mark.django_db
def test_preflight_detects_non_egp_order_data(company, owner_membership):
    from shopify_connector.models import ShopifyOrder, ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="n.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id="900",
        shopify_order_number="900",
        currency="USD",
        total_price=10,
        subtotal_price=10,
        shopify_created_at="2026-01-02T00:00:00Z",
        order_date="2026-01-02",
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "non_egp_shopify_data" in codes


@pytest.mark.django_db
def test_preflight_go_live_requires_active_store(company, owner_membership):
    _make_pilot(company)
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "store_count" in codes


# --- one seeded positive test per remaining preflight violation code ------- #
@pytest.mark.django_db
def test_preflight_detects_profile_not_enabled(company, owner_membership):
    # A NONE-profile company run as a non-activation preflight flags the profile.
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "profile_not_enabled" in codes


@pytest.mark.django_db
def test_preflight_detects_membership_not_owner(company, owner_membership):
    _make_pilot(company)
    owner_membership.role = CompanyMembership.Role.USER
    owner_membership.save()
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "membership_not_owner" in codes


@pytest.mark.django_db
def test_preflight_detects_pending_invitation(company, user, owner_membership):
    from datetime import timedelta

    from django.utils import timezone

    from accounts.models import Invitation

    _make_pilot(company)
    Invitation.objects.create(
        email="pending@test.com",
        token_hash="h" * 64,
        invited_by=user,
        primary_company=company,
        role="ADMIN",
        company_ids=[company.id],
        status=Invitation.Status.PENDING,
        expires_at=timezone.now() + timedelta(days=1),
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "pending_invitations" in codes


@pytest.mark.django_db
def test_preflight_detects_fiscal_not_january(company, owner_membership):
    _make_pilot(company)
    company.fiscal_year_start_month = 4
    company.save()
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "fiscal_not_january" in codes


@pytest.mark.django_db
def test_preflight_detects_no_period_config(company, owner_membership):
    from projections.models import FiscalPeriodConfig

    _make_pilot(company, with_periods=False)
    # The company fixture yields a default config; a "no config" state must
    # remove it explicitly.
    FiscalPeriodConfig.objects.filter(company=company).delete()
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "no_period_config" in codes


@pytest.mark.django_db
def test_preflight_detects_period_structure(company, owner_membership):
    from projections.models import FiscalPeriodConfig

    _make_pilot(company, with_periods=False)
    FiscalPeriodConfig.objects.filter(company=company).delete()
    FiscalPeriodConfig.objects.create(company=company, fiscal_year=2026, period_count=6)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "period_structure" in codes


@pytest.mark.django_db
def test_preflight_detects_stripe_connected(company, owner_membership):
    from stripe_connector.models import StripeAccount

    _make_pilot(company)
    StripeAccount.objects.create(company=company, stripe_account_id="acct_test")
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "stripe_connected" in codes


@pytest.mark.django_db
def test_preflight_detects_item_inv_cogs_mapping(company, owner_membership):
    from accounting.models import Account
    from sales.models import Item

    _make_pilot(company)
    acct = Account.objects.create(company=company, code="1300", name="Inv", account_type="ASSET")
    Item.objects.create(
        company=company, code="ITM", name="I", item_type=Item.ItemType.NON_STOCK, inventory_account=acct
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "item_inv_cogs_mapping" in codes


@pytest.mark.django_db
def test_preflight_detects_store_inv_cogs_mapping(company, owner_membership):
    from accounting.models import Account
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    acct = Account.objects.create(company=company, code="1300", name="Inv", account_type="ASSET")
    ShopifyStore.objects.create(
        company=company,
        shop_domain="si.myshopify.com",
        access_token="x",
        status=ShopifyStore.Status.ACTIVE,
        default_inventory_account=acct,
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "store_inv_cogs_mapping" in codes


@pytest.mark.django_db
def test_preflight_detects_cogs_pending(company, owner_membership):
    from shopify_connector.models import ShopifyFulfillment, ShopifyOrder, ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="cp.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    order = ShopifyOrder.objects.create(
        company=company,
        store=store,
        shopify_order_id="500",
        shopify_order_number="500",
        currency="EGP",
        total_price=10,
        subtotal_price=10,
        shopify_created_at="2026-01-02T00:00:00Z",
        order_date="2026-01-02",
    )
    ShopifyFulfillment.objects.create(
        company=company,
        order=order,
        shopify_fulfillment_id=1,
        shopify_order_id=500,
        shopify_created_at="2026-01-03T00:00:00Z",
        currency="EGP",
        status=ShopifyFulfillment.Status.COGS_PENDING,
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "cogs_pending" in codes


@pytest.mark.django_db
def test_preflight_detects_stock_ledger_and_fifo_layers(company, owner_membership):
    from decimal import Decimal

    from django.utils import timezone

    from inventory.models import FifoLayer, StockLedgerEntry, Warehouse
    from projections.write_barrier import command_writes_allowed
    from sales.models import Item

    _make_pilot(company)
    wh = Warehouse.objects.create(company=company, code="WH1", name="Main")
    item = Item.objects.create(company=company, code="SLEITEM", name="S", item_type=Item.ItemType.NON_STOCK)
    with command_writes_allowed():
        sle = StockLedgerEntry.objects.create(
            company=company,
            sequence=1,
            source_type=StockLedgerEntry.SourceType.OPENING_BALANCE,
            source_id=uuid4(),
            warehouse=wh,
            item=item,
            qty_delta=Decimal("1"),
            unit_cost=Decimal("1"),
            value_delta=Decimal("1"),
            costing_method_snapshot="FIFO",
            qty_balance_after=Decimal("1"),
            value_balance_after=Decimal("1"),
            avg_cost_after=Decimal("1"),
            posted_at=timezone.now(),
        )
        FifoLayer.objects.create(
            company=company,
            item=item,
            warehouse=wh,
            receipt_entry=sle,
            qty_original=Decimal("1"),
            qty_remaining=Decimal("1"),
            unit_cost=Decimal("1"),
            sequence=1,
        )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "stock_ledger" in codes
    assert "fifo_layers" in codes


# --- correction pass 2: new preflight codes --------------------------------- #
@pytest.mark.django_db
def test_preflight_detects_inventory_balances(company, owner_membership):
    from inventory.models import Warehouse
    from projections.models import InventoryBalance
    from sales.models import Item

    _make_pilot(company)
    wh = Warehouse.objects.create(company=company, code="WHB", name="Main")
    item = Item.objects.create(company=company, code="BALITEM", name="B", item_type=Item.ItemType.NON_STOCK)
    InventoryBalance.objects.create(company=company, item=item, warehouse=wh, qty_on_hand=1, avg_cost=1, stock_value=1)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "inventory_balances" in codes


@pytest.mark.django_db
def test_preflight_go_live_rejects_non_egp_store(company, user, owner_membership):
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    ShopifyStore.objects.create(
        company=company,
        shop_domain="usd.myshopify.com",
        access_token="x",
        status=ShopifyStore.Status.ACTIVE,
        shop_currency="USD",
    )
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "store_currency_not_egp" in codes


@pytest.mark.django_db
def test_preflight_go_live_fails_on_unknown_store_currency(company, user, owner_membership, monkeypatch):
    """No durable snapshot and the live probe fails → go-live must FAIL, not pass."""
    from shopify_connector import commands as sc
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    ShopifyStore.objects.create(
        company=company, shop_domain="u.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    monkeypatch.setattr(sc, "_get_shopify_store_currency", lambda store, client=None: "")
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "store_currency_unknown" in codes


@pytest.mark.django_db
def test_preflight_go_live_requires_provider(company, user, owner_membership):
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="np.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    _seed_go_live_readiness(company, store, owner_membership, with_provider=False)
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "provider_missing" in codes


@pytest.mark.django_db
def test_preflight_go_live_requires_active_posting_profile(company, user, owner_membership):
    from sales.models import PostingProfile
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="pp.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    _seed_go_live_readiness(company, store, owner_membership)
    PostingProfile.objects.filter(company=company, code="PAYMOB").update(is_active=False)
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "provider_posting_profile" in codes


@pytest.mark.django_db
def test_preflight_go_live_requires_bank_account(company, user, owner_membership):
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    store = ShopifyStore.objects.create(
        company=company, shop_domain="nb.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    _seed_go_live_readiness(company, store, owner_membership, with_bank=False)
    codes = {v.code for v in pilot_policy_run(company, phase="go-live")}
    assert "bank_account_missing" in codes


@pytest.mark.django_db
def test_preflight_detects_non_egp_journal_data(company, owner_membership):
    from accounting.models import JournalEntry

    _make_pilot(company)
    JournalEntry.objects.create(
        company=company,
        entry_number="JE-USD-1",
        date="2026-01-15",
        period=1,
        currency="USD",
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "non_egp_journal_data" in codes


@pytest.mark.django_db
def test_preflight_detects_non_egp_settlement_data(company, owner_membership):
    from platform_connectors.models import PlatformSettlement

    _make_pilot(company)
    PlatformSettlement.objects.create(
        company=company,
        platform="shopify",
        platform_document_id="p-usd-1",
        settlement_type="PAYOUT",
        gross_amount=10,
        fees=1,
        net_amount=9,
        currency="USD",
        settlement_date="2026-01-15",
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "non_egp_settlement_data" in codes


@pytest.mark.django_db
def test_preflight_detects_non_egp_bank_data(company, owner_membership):
    from accounting.models import Account, BankStatement

    _make_pilot(company)
    acct = Account.objects.create(company=company, code="1001", name="Bank", account_type="ASSET")
    BankStatement.objects.create(
        company=company,
        account=acct,
        statement_date="2026-01-31",
        period_start="2026-01-01",
        period_end="2026-01-31",
        opening_balance=0,
        closing_balance=0,
        currency="USD",
    )
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "non_egp_bank_data" in codes


# --- admin bypasses closed --------------------------------------------------- #
@pytest.mark.django_db
def test_company_admin_freezes_pilot_fields(company):
    from django.contrib import admin as dj_admin

    from accounts.admin import CompanyAdmin

    _make_pilot(company)
    model_admin = CompanyAdmin(Company, dj_admin.site)
    ro = model_admin.get_readonly_fields(request=None, obj=company)
    for field in ("default_currency", "fiscal_year_start_month", "is_active"):
        assert field in ro, field
    # NONE-profile companies keep the normal editable form.
    normal = Company(pilot_profile=Company.PilotProfile.NONE)
    ro_normal = model_admin.get_readonly_fields(request=None, obj=normal)
    assert "default_currency" not in ro_normal


@pytest.mark.django_db
def test_company_admin_cannot_delete_pilot(company):
    from django.contrib import admin as dj_admin

    from accounts.admin import CompanyAdmin

    _make_pilot(company)
    model_admin = CompanyAdmin(Company, dj_admin.site)
    # Change-form delete (obj given) and bulk delete action (obj=None) both blocked.
    assert model_admin.has_delete_permission(request=None, obj=company) is False
    assert model_admin.has_delete_permission(request=None, obj=None) is False


@pytest.mark.django_db
def test_membership_admin_cannot_add_or_reactivate_under_pilot(company, user, owner_membership):
    from django.contrib import admin as dj_admin

    from accounts.admin import CompanyMembershipAdmin, CompanyMembershipInline

    _make_pilot(company)
    ma = CompanyMembershipAdmin(CompanyMembership, dj_admin.site)
    assert ma.has_add_permission(request=None) is False
    ro = ma.get_readonly_fields(request=None, obj=owner_membership)
    for field in ("is_active", "company", "user", "role"):
        assert field in ro, field
    inline = CompanyMembershipInline(Company, dj_admin.site)
    assert inline.has_add_permission(request=None, obj=company) is False


# --- interactive payout routes return 403 (not a silent skip) -------------- #
@pytest.mark.django_db
def test_route_sync_payouts_blocked_for_pilot(company, user, owner_membership, api_client):
    _make_pilot(company)
    _grant(company, owner_membership, "settings.edit")
    api_client.force_authenticate(user=user)
    resp = api_client.post("/api/shopify/sync-payouts/")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_route_verify_payout_blocked_for_pilot(company, user, owner_membership, api_client):
    _make_pilot(company)
    _grant(company, owner_membership, "reports.view")
    api_client.force_authenticate(user=user)
    resp = api_client.post("/api/shopify/payouts/123/verify/")
    assert resp.status_code == 403


# Bind the preflight runner lazily so the module imports without Django set up.
def pilot_policy_run(company, *, phase):
    from accounts.pilot_preflight import run_preflight

    return run_preflight(company, phase=phase)


def _run_preflight_activation(company):
    from accounts.pilot_preflight import run_preflight

    return run_preflight(company, phase="setup", for_activation=True)


# =========================================================================== #
# A4 correction (2026-08-10): purchasing / accounts-payable exclusion.
#
# Gap re-closed after the PR #117 review: a pilot OWNER could enable the
# `purchases` module via two doors and post NON_STOCK bills → full AP / expense
# / input-VAT journals through the posted-journal boundary. These prove the
# runtime boundaries block every path (not UI hiding), with zero side effects,
# plus preflight/activation drift detection — and that NONE-profile companies
# keep the existing behavior.
# =========================================================================== #

_PURCHASING_COMMANDS = [
    "create_purchase_bill",
    "post_purchase_bill",
    "void_purchase_bill",
    "delete_purchase_bill",
    "create_purchase_order",
    "approve_purchase_order",
    "cancel_purchase_order",
    "close_purchase_order",
    "create_goods_receipt",
    "post_goods_receipt",
    "void_goods_receipt",
    "create_bill_from_po",
    "create_purchase_credit_note",
    "post_purchase_credit_note",
    "void_purchase_credit_note",
]

_PURCHASE_MUTATION_ROUTES = [
    ("post", "/api/purchases/bills/"),
    ("post", "/api/purchases/orders/"),
    ("post", "/api/purchases/receipts/"),
    ("post", "/api/purchases/credit-notes/"),
    ("post", "/api/purchases/bills/999999/post/"),
    ("post", "/api/purchases/bills/999999/void/"),
    ("post", "/api/purchases/orders/999999/approve/"),
    ("delete", "/api/purchases/bills/999999/"),
]


def _seed_purchase_bill(company, *, posted=False):
    """Create a minimal purchase-bill graph DIRECTLY (bypassing the runtime gate,
    as pre-activation drift would) so the preflight has something to catch. The
    Account, VENDOR PostingProfile and Vendor are EXEMPT master data; only the
    PurchaseBill document (and, when ``posted``, its posted journal entry) is
    purchasing execution state."""
    import datetime as _dt

    from accounting.models import Account, JournalEntry, Vendor
    from purchases.models import PurchaseBill
    from sales.models import PostingProfile

    ap = Account.objects.create(company=company, code="21000", name="AP Control", account_type="LIABILITY")
    profile = PostingProfile.objects.create(
        company=company,
        code="AP-M",
        name="AP Manual",
        profile_type=PostingProfile.ProfileType.VENDOR,
        usage=PostingProfile.Usage.MANUAL,
        control_account=ap,
    )
    vendor = Vendor.objects.create(company=company, code="VEND1", name="Vendor One")
    je = None
    if posted:
        je = JournalEntry.objects.create(
            company=company, entry_number="JE-PUR-1", date=_dt.date(2026, 1, 15), period=1, currency="EGP"
        )
    return PurchaseBill.objects.create(
        company=company,
        bill_number="BILL-000001",
        bill_date=_dt.date(2026, 1, 15),
        vendor=vendor,
        posting_profile=profile,
        posted_journal_entry=je,
        status=PurchaseBill.Status.POSTED if posted else PurchaseBill.Status.DRAFT,
    )


# --- capability presence ---------------------------------------------------- #
def test_purchasing_capability_present_and_blocked():
    pilot = Company(pilot_profile=ISO)
    normal = Company(pilot_profile=Company.PilotProfile.NONE)
    assert is_supported(pilot, Capability.PURCHASING_ACCOUNTING) is False
    assert is_supported(normal, Capability.PURCHASING_ACCOUNTING) is True


# --- command-layer gate: raises before any side effect ---------------------- #
@pytest.mark.django_db
@pytest.mark.parametrize("cmd_name", _PURCHASING_COMMANDS)
def test_every_purchasing_command_blocked_for_pilot(cmd_name, company, owner_membership):
    """Every public purchasing command RAISES PilotScopeBlocked for a pilot — the
    decorator gate fires before the wrapped body, so even a call with no valid
    arguments is refused (the gate precedes sequence/lock/emit/write)."""
    from purchases import commands as pc

    _make_pilot(company)
    fn = getattr(pc, cmd_name)
    with pytest.raises(PilotScopeBlocked):
        fn(_actor(company))


@pytest.mark.django_db
def test_create_purchase_bill_blocked_with_zero_side_effects(company, owner_membership):
    from accounting.models import CompanySequence, JournalEntry
    from events.models import BusinessEvent
    from inventory.models import StockLedgerEntry
    from purchases.commands import create_purchase_bill
    from purchases.models import PurchaseBill

    _make_pilot(company)
    je_before = JournalEntry.objects.filter(company=company).count()
    be_before = BusinessEvent.objects.filter(company=company).count()

    with pytest.raises(PilotScopeBlocked):
        create_purchase_bill(
            _actor(company),
            vendor_id=1,
            posting_profile_id=1,
            lines=[{"account_id": 1, "quantity": "1", "unit_price": "10"}],
            bill_date="2026-01-15",
        )

    assert not PurchaseBill.objects.filter(company=company).exists()
    assert JournalEntry.objects.filter(company=company).count() == je_before
    assert BusinessEvent.objects.filter(company=company).count() == be_before
    assert not StockLedgerEntry.objects.filter(company=company).exists()
    # The gate precedes sequence allocation — no purchase_bill_number is burned.
    assert not CompanySequence.objects.filter(company=company, name="purchase_bill_number").exists()


@pytest.mark.django_db
def test_record_vendor_payment_blocked_with_zero_side_effects(company, owner_membership):
    from accounting.commands import record_vendor_payment
    from accounting.models import CompanySequence, JournalEntry
    from events.models import BusinessEvent
    from sales.models import PaymentAllocation

    _make_pilot(company)
    je_before = JournalEntry.objects.filter(company=company).count()
    be_before = BusinessEvent.objects.filter(company=company).count()

    with pytest.raises(PilotScopeBlocked):
        record_vendor_payment(
            _actor(company),
            vendor_id=1,
            payment_date="2026-01-15",
            amount="100",
            bank_account_id=1,
            ap_control_account_id=1,
        )

    assert JournalEntry.objects.filter(company=company).count() == je_before
    assert BusinessEvent.objects.filter(company=company).count() == be_before
    assert not PaymentAllocation.objects.filter(company=company).exists()
    assert not CompanySequence.objects.filter(company=company, name="journal_entry_number").exists()


@pytest.mark.django_db
def test_purchasing_command_unaffected_for_none_profile(company, owner_membership):
    """NONE profile: the gate is inert — the command runs normally and reports an
    ordinary domain failure (missing vendor), never PilotScopeBlocked."""
    from purchases.commands import create_purchase_bill

    result = create_purchase_bill(
        _actor(company),
        vendor_id=999999,
        posting_profile_id=999999,
        lines=[{"account_id": 999999, "quantity": "1", "unit_price": "10"}],
        bill_date="2026-01-15",
    )
    assert not result.success
    assert "Vendor not found" in result.error


# --- route-level gate: 403 pre-serializer, even with a stale enabled row ----- #
@pytest.mark.django_db
@pytest.mark.parametrize("method,path", _PURCHASE_MUTATION_ROUTES)
def test_purchase_routes_403_for_pilot_even_with_stale_enabled_row(
    method, path, company, user, owner_membership, api_client
):
    """Route-level gate: for a pilot, EVERY purchases mutation route returns 403
    at permission-check time — BEFORE the serializer — even when a stale
    CompanyModule row says the module is enabled (drift cannot reopen it). An
    invalid payload still yields 403, not a 400 validation error, proving the
    gate precedes the serializer."""
    from accounts.models import CompanyModule

    _make_pilot(company)
    CompanyModule.objects.create(company=company, module_key="purchases", is_enabled=True)
    api_client.force_authenticate(user=user)

    if method == "post":
        resp = api_client.post(path, {"definitely": "invalid"}, format="json")
    else:
        resp = api_client.delete(path)
    assert resp.status_code == 403, (path, resp.status_code)


@pytest.mark.django_db
def test_purchase_route_not_pilot_blocked_for_none_profile(company, user, owner_membership, api_client):
    """NONE profile with the module enabled: the pilot gate is inert — an invalid
    payload reaches the serializer and returns 400, never the pilot 403."""
    from accounts.models import CompanyModule

    CompanyModule.objects.create(company=company, module_key="purchases", is_enabled=True)
    api_client.force_authenticate(user=user)
    resp = api_client.post("/api/purchases/bills/", {"definitely": "invalid"}, format="json")
    assert resp.status_code == 400


# --- both module-enable doors refuse enabling; disabling stays allowed ------- #
@pytest.mark.django_db
@pytest.mark.parametrize("door", ["modules_put", "onboarding_step4"])
def test_both_enable_doors_refuse_enabling_purchases_for_pilot(door, company, user, owner_membership, api_client):
    from accounts.models import CompanyModule

    _make_pilot(company)
    if door == "modules_put":
        api_client.force_authenticate(user=user)
        resp = api_client.put("/api/modules/", [{"key": "purchases", "is_enabled": True}], format="json")
        assert resp.status_code == 403
    else:
        from accounts.commands import complete_onboarding

        with pytest.raises(PilotScopeBlocked):
            complete_onboarding(_actor(company), modules=[{"key": "purchases", "is_enabled": True}])
    # Validate-before-write: no purchases CompanyModule row was created at all.
    assert not CompanyModule.objects.filter(company=company, module_key="purchases").exists()


@pytest.mark.django_db
def test_module_put_allows_disabling_purchases_for_pilot(company, user, owner_membership, api_client):
    _make_pilot(company)
    api_client.force_authenticate(user=user)
    resp = api_client.put("/api/modules/", [{"key": "purchases", "is_enabled": False}], format="json")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_module_put_enables_purchases_for_none_profile(company, user, owner_membership, api_client):
    from accounts.models import CompanyModule

    api_client.force_authenticate(user=user)
    resp = api_client.put("/api/modules/", [{"key": "purchases", "is_enabled": True}], format="json")
    assert resp.status_code == 200
    assert CompanyModule.objects.filter(company=company, module_key="purchases", is_enabled=True).exists()


# --- preflight / activation drift detection --------------------------------- #
@pytest.mark.django_db
def test_preflight_detects_purchases_module_enabled(company, owner_membership):
    from accounts.models import CompanyModule

    _make_pilot(company)
    CompanyModule.objects.create(company=company, module_key="purchases", is_enabled=True)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "purchases_module_enabled" in codes


@pytest.mark.django_db
def test_preflight_detects_purchase_document_state(company, owner_membership):
    _make_pilot(company)
    _seed_purchase_bill(company, posted=False)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "purchase_document_state" in codes
    assert "purchase_financial_state" not in codes  # a DRAFT bill has no posted JE


@pytest.mark.django_db
def test_preflight_detects_purchase_financial_state(company, owner_membership):
    _make_pilot(company)
    _seed_purchase_bill(company, posted=True)
    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "purchase_financial_state" in codes
    assert "purchase_document_state" in codes


@pytest.mark.django_db
def test_preflight_purchase_exemptions_are_clean(company, owner_membership):
    """Vendors, VENDOR posting profiles, NON_STOCK items and the purchase
    sequence counters are shared master data / harmless counters — NOT purchasing
    execution state — so none of the purchase violation codes fire for them."""
    from accounting.models import Account, CompanySequence, Vendor
    from sales.models import Item, PostingProfile

    _make_pilot(company)
    ap = Account.objects.create(company=company, code="21000", name="AP", account_type="LIABILITY")
    PostingProfile.objects.create(
        company=company,
        code="AP-M",
        name="AP Manual",
        profile_type=PostingProfile.ProfileType.VENDOR,
        usage=PostingProfile.Usage.MANUAL,
        control_account=ap,
    )
    Vendor.objects.create(company=company, code="VEND1", name="Vendor One")
    Item.objects.create(company=company, code="SVC1", name="Service", item_type=Item.ItemType.NON_STOCK)
    CompanySequence.objects.create(company=company, name="purchase_bill_number", next_value=7)

    codes = {v.code for v in pilot_policy_run(company, phase="setup")}
    assert "purchase_document_state" not in codes
    assert "purchase_financial_state" not in codes
    assert "purchases_module_enabled" not in codes


@pytest.mark.django_db
def test_activation_refuses_company_with_purchase_document(company, user, owner_membership):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    # An otherwise setup-clean EGP company; a purchase document is forbidden drift.
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})
    _seed_purchase_bill(company)

    with pytest.raises(CommandError):
        call_command("activate_pilot_profile", "--company", str(company.id), "--yes")
    company.refresh_from_db()
    assert company.pilot_profile == Company.PilotProfile.NONE
