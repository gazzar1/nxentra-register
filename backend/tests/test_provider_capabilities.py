# tests/test_provider_capabilities.py
"""S0 — connector capability matrix + canonical payment DTOs (ADR-0002).

The `capabilities` descriptor lets the engine/UI adapt per provider (Stripe
pulls rich balance transactions; a CSV gateway only imports a file), and
ParsedProviderTransaction/ParsedPayoutLine are the canonical grains the Stripe
adapter normalizes into.
"""

from decimal import Decimal

from platform_connectors.canonical import (
    ParsedPayoutLine,
    ParsedProviderTransaction,
    ProviderCapabilities,
)
from stripe_connector.connector import StripeConnector


def test_stripe_declares_its_capabilities():
    caps = StripeConnector().capabilities
    assert caps.pull_payouts is True
    assert caps.pull_transactions is True
    assert caps.payout_line_breakdown is True
    assert caps.disputes is True and caps.dispute_resolution is True
    assert caps.reserves is True and caps.adjustments is True
    assert caps.multi_currency is True
    # payout.paid lacks the fee split -> fees are derived from balance txns.
    assert caps.fee_in_payout == "derived"
    assert caps.auth == "restricted_read_key"
    assert caps.csv_import is False


def test_default_capabilities_are_conservative():
    # A connector that declares nothing gets the dataclass defaults: no pull,
    # no advanced lifecycle, fees not pre-netted.
    caps = ProviderCapabilities()
    assert caps.pull_payouts is False
    assert caps.pull_transactions is False
    assert caps.payout_line_breakdown is False
    assert caps.disputes is False
    assert caps.fee_in_payout == "none"
    assert caps.auth == ""
    # frozen — capability declarations are immutable.
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.pull_payouts = True  # type: ignore[misc]


def test_stripe_get_module_key_is_unified_to_platform_stripe():
    # The 4th module-key site (the previously hardcoded 'stripe_connector'
    # override) now inherits the base helper -> 'platform_stripe'.
    assert StripeConnector().get_module_key() == "platform_stripe"


def test_canonical_payment_dtos_construct_with_defaults():
    tx = ParsedProviderTransaction(external_id="txn_1", txn_type="charge", gross_amount=Decimal("100"))
    assert tx.currency == "USD"
    assert tx.net_amount == Decimal("0")
    assert tx.payout_external_id == ""

    line = ParsedPayoutLine(payout_external_id="po_1", transaction_external_id="txn_1", line_type="charge")
    assert line.currency == "USD"
    assert line.fee_amount == Decimal("0")
