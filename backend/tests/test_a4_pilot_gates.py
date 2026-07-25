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
@pytest.mark.django_db
def test_preflight_passes_clean_company(company, user, owner_membership):
    from shopify_connector.models import ShopifyStore

    _make_pilot(company)
    ShopifyStore.objects.create(
        company=company, shop_domain="c.myshopify.com", access_token="x", status=ShopifyStore.Status.ACTIVE
    )
    violations = pilot_policy_run(company, phase="go-live")
    assert violations == [], violations


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


# Bind the preflight runner lazily so the module imports without Django set up.
def pilot_policy_run(company, *, phase):
    from accounts.pilot_preflight import run_preflight

    return run_preflight(company, phase=phase)
