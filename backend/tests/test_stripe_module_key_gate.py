# tests/test_stripe_module_key_gate.py
"""S0 regression guard — one canonical module key per payment/settlement provider.

Before the fix, the same logical ModuleAccountMapping resolved THREE different
keys for a single Stripe provider (order/refund/dispute JEs -> 'platform_stripe';
settlement JEs -> 'stripe_connector' fallback; settlement EBD lookup -> hardcoded
'shopify_connector') — seed one, the others skip with only a warning and the
books are silently incomplete.

`module_key_for_provider()` (accounting/mappings.py) is now the single source,
routed through by both PaymentSettlementProjection and PlatformAccountingProjection.
This guard pins the contract so a future inline-key regression fails CI.
See docs/adr/0002-canonical-payments-stripe-adapter.md.
"""

from accounting.mappings import module_key_for_provider


def test_stripe_resolves_to_the_platform_key_for_both_order_and_settlement_jes():
    # PlatformAccountingProjection (order/refund/dispute) and
    # PaymentSettlementProjection (settlement) both call module_key_for_provider,
    # so Stripe's order JEs and settlement JEs land on ONE mapping.
    assert module_key_for_provider("stripe") == "platform_stripe"


def test_existing_providers_keep_shopify_connector_key():
    # The unification must NOT move shopify / paymob / bosta (they ride
    # external_system='shopify') off 'shopify_connector', seeded by
    # _setup_shopify_accounts.
    assert module_key_for_provider("shopify") == "shopify_connector"


def test_module_key_is_case_and_whitespace_insensitive():
    assert module_key_for_provider("  Stripe ") == "platform_stripe"
    assert module_key_for_provider("SHOPIFY") == "shopify_connector"
