# tests/test_settlement_parser_registry.py
"""S0 — the settlement CSV parser registry replaces the hardcoded
paymob/bosta if/elif dispatch, so a new provider self-registers without editing
preview/import_settlement_csv. See docs/adr/0002-canonical-payments-stripe-adapter.md.
"""

import pytest

from accounting.settlement_imports import (
    SettlementImportError,
    get_settlement_parser,
    import_settlement_csv,
    register_settlement_parser,
    supported_settlement_providers,
)


def test_builtin_parsers_are_registered():
    assert set(supported_settlement_providers()) >= {"bosta", "paymob"}
    assert get_settlement_parser("paymob").default_method == "card"
    # Lookup is case/whitespace-insensitive.
    assert get_settlement_parser("  BOSTA ").default_method == "cash_on_delivery"
    assert get_settlement_parser("nope") is None


def test_a_new_provider_can_self_register():
    def _fake_parse(content):
        return []

    register_settlement_parser("acme", _fake_parse, "card")
    try:
        spec = get_settlement_parser("acme")
        assert spec is not None
        assert spec.parse is _fake_parse
        assert spec.default_method == "card"
        assert "acme" in supported_settlement_providers()
    finally:
        # Keep the module-level registry pristine for other tests.
        from accounting.settlement_imports import _SETTLEMENT_PARSERS

        _SETTLEMENT_PARSERS.pop("acme", None)


def test_unknown_provider_is_rejected_with_supported_list(db, company):
    # The import dispatch now routes through the registry — an unregistered
    # provider raises with the registry's supported list.
    with pytest.raises(SettlementImportError) as exc:
        import_settlement_csv(
            company=company,
            provider_normalized_code="nope",
            file_content=b"order_id,gross,fee,net,payout_batch_id,payout_date\n",
            source_filename="x.csv",
        )
    msg = str(exc.value)
    assert "No CSV parser registered" in msg
    assert "bosta" in msg and "paymob" in msg
