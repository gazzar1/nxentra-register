# tests/test_a141_stage2_ebd_union.py
"""A141 — Stage 2 "Net to bank" must union EBD accounts across ALL provider
modules.

EXPECTED_BANK_DEPOSIT is seeded per provider module (shopify_connector for
Shopify, platform_stripe for Stripe, ...). ``_stage2_summary`` hardcoded the
shopify_connector lookup, so a Stripe settlement JE incremented "Settlements
posted" while adding 0.00 to "Net to bank" — a visible count-vs-amount
inconsistency on /finance/reconciliation. Same class as the S1 PR-A bank-match
union fix (test_s1a), applied to the recon summary.
"""

from decimal import Decimal

import pytest


@pytest.fixture
def stripe_settlement(db, company, owner_membership):
    """A real Stripe settlement drain JE, produced by the actual projection
    from a PAYMENT_SETTLEMENT_RECEIVED event (DR EBD 96.80 / DR fees 3.20 /
    CR clearing 100.00). setup_stripe_platform seeds the platform_stripe
    mappings incl. EXPECTED_BANK_DEPOSIT — the account the old hardcode
    missed."""
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="stripe:po_stage2",
        idempotency_key="payment.settlement.received:stripe:po_stage2",
        data=PaymentSettlementReceivedData(
            amount="100.00",
            currency="USD",
            transaction_date="2026-07-02",
            document_ref="po_stage2",
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id="po_stage2",
            gross_amount="100.00",
            fees="3.20",
            net_amount="96.80",
            uncollected_amount="0",
            payment_method="card",
            payout_date="2026-07-02",
            line_items=[{"order_id": "ch_x", "gross": "100.00", "fee": "3.20", "net": "96.80", "status": "charge"}],
            provider_status="paid",
        ),
    )
    PaymentSettlementProjection().process_pending(company)


def test_stage2_net_to_bank_includes_stripe_ebd(db, company, stripe_settlement):
    from accounting.models import JournalEntry
    from accounting.reconciliation_views import _stage2_summary

    # Preflight: the drain JE really posted (else the assertion below would
    # pass vacuously on 0 == 0).
    je = JournalEntry.objects.get(
        company=company, source_module="payment_settlement", source_document="stripe:po_stage2"
    )
    assert je.status == JournalEntry.Status.POSTED

    summary = _stage2_summary(company)
    assert summary["settled_count"] == 1
    # Pre-A141 this was "0.00": the JE counted but its EBD debit (on the
    # platform_stripe-mapped account) was invisible to the shopify hardcode.
    assert Decimal(summary["settled_total"]) == Decimal("96.80")
