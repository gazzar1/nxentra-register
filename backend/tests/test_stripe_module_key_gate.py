# tests/test_stripe_module_key_gate.py
"""S0 truth gate — the Stripe module-key split is a financial-routing bug.

For a single Stripe provider, the same logical ModuleAccountMapping resolves
THREE different keys today:
  - order/refund/dispute JEs (PlatformAccountingProjection):
        module_key = f"platform_{slug}"  -> 'platform_stripe'   (platform_connectors/projections.py:94)
  - settlement JEs (PaymentSettlementProjection):
        _MODULE_BY_EXTERNAL_SYSTEM fallback -> 'stripe_connector' (accounting/payment_settlement_projection.py:205)
  - settlement EBD lookup (bank-clearance leg):
        hardcoded 'shopify_connector'                            (accounting/bank_reconciliation.py:633)

Seed one, the others skip with only a warning log → the books are silently
incomplete. Shopify is unaffected (its own shopify_accounting projection +
shopify_connector key), and paymob/bosta ride external_system='shopify' →
'shopify_connector' and MUST keep doing so.

The xfail(strict=True) gate reproduces the divergence and flips to a forced
passing regression guard the moment S0's `module_key_for_provider()`
unification lands (Stripe -> 'platform_stripe'). See
docs/adr/0002-canonical-payments-stripe-adapter.md.
"""

import pytest


def _settlement_module_key(external_system: str) -> str:
    """Mirror of PaymentSettlementProjection's resolution
    (payment_settlement_projection.py:205)."""
    from accounting.payment_settlement_projection import _MODULE_BY_EXTERNAL_SYSTEM

    return _MODULE_BY_EXTERNAL_SYSTEM.get(external_system, f"{external_system}_connector")


def _platform_module_key(slug: str) -> str:
    """Mirror of PlatformAccountingProjection's resolution
    (platform_connectors/projections.py:94)."""
    return f"platform_{slug}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "S0: Stripe settlement JEs resolve 'stripe_connector' while its order/refund/dispute "
        "JEs resolve 'platform_stripe' — unify via module_key_for_provider()."
    ),
)
def test_stripe_settlement_and_platform_jes_resolve_the_same_module_key():
    # Both must point at ONE Stripe mapping, or one path silently skips.
    assert _settlement_module_key("stripe") == _platform_module_key("stripe")


def test_existing_providers_keep_shopify_connector_key():
    # The unification must NOT move shopify / paymob / bosta (external_system='shopify')
    # off 'shopify_connector' — they are seeded there by _setup_shopify_accounts.
    assert _settlement_module_key("shopify") == "shopify_connector"
