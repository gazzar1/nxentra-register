# backend/tests/test_s2b_payments_projection_gate.py
"""ADR-0002 Phase 2 — architecture gate (the "★ run Paymob/Bosta through the
canonical projection" check, done early while the abstraction is still soft).

PaymentsProjection (PR-A) was built against Stripe's line_items shape
({order_id, gross, fee, net, status}). Paymob and Bosta ALSO emit
PAYMENT_SETTLEMENT_RECEIVED with line_items, but their economically-important
amount can live in a DIFFERENT key:
  * Bosta failed-delivery (returned) lines: collected/net are 0 and the order
    value is in ``uncollected``.
  * Paymob refund/chargeback lines: the deducted amount is in ``refund``.

If the canonical projection only reads gross/fee/net, those lines materialize as
0/0/0 and the uncollected/refunded amount is silently dropped — the Stripe-shaped
leak this gate exists to catch. The fix is the canonical ``uncollected_amount``
field on ProviderPayoutLine. This test runs the REAL Bosta/Paymob parse+emit path
(import_settlement_csv) through PaymentsProjection so a future change to either
parser's line shape re-trips the gate.
"""

from decimal import Decimal

import pytest

from accounting.settlement_imports import import_settlement_csv

# Real Bosta COD export shape (mirrors tests/test_a39): a delivered row (money
# collected) + a returned row (collected/net 0, value in returned_uncollected).
BOSTA_CSV = b"""shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status,returned_uncollected_amount
SHIP-1,ORD-101,1500.00,100.00,1400.00,COD-A,2026-04-26,delivered,
SHIP-2,ORD-103,0,0,0,COD-A,2026-04-26,returned,1200.00
"""

# Real Paymob export shape: a settled row + a refunded row (refund deducted).
PAYMOB_CSV = b"""order_id,gross,fee,net,refund,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,0,PMB-9,2026-04-25
ORD-2,500.00,15.00,285.00,200.00,PMB-9,2026-04-25
"""


@pytest.fixture
def projected(db, company):
    """Import a Bosta + a Paymob settlement (real parse+emit), then run the
    canonical PaymentsProjection over the emitted events."""
    from platform_connectors.projections import PaymentsProjection

    import_settlement_csv(company=company, provider_normalized_code="bosta", file_content=BOSTA_CSV)
    import_settlement_csv(company=company, provider_normalized_code="paymob", file_content=PAYMOB_CSV)
    PaymentsProjection().process_pending(company)


def test_bosta_returned_line_keeps_its_uncollected_amount(db, company, projected):
    """The Bosta returned line must NOT be dropped to 0/0/0 — its uncollected
    (failed-delivery) amount is the whole economic content of the row."""
    from platform_connectors.models import ProviderPayoutLine

    lines = {
        line.source_id: line
        for line in ProviderPayoutLine.objects.filter(company=company, provider="bosta", payout_batch_id="COD-A")
    }
    assert set(lines) == {"ORD-101", "ORD-103"}

    delivered = lines["ORD-101"]
    assert (delivered.gross_amount, delivered.net_amount, delivered.uncollected_amount) == (
        Decimal("1500.00"),
        Decimal("1400.00"),
        Decimal("0.00"),
    )

    returned = lines["ORD-103"]
    assert returned.kind == "returned"
    assert returned.gross_amount == Decimal("0.00")
    # The leak: a Stripe-shaped projection drops this to 0.
    assert returned.uncollected_amount == Decimal("1200.00")


def test_paymob_refund_line_keeps_its_refund_amount(db, company, projected):
    from platform_connectors.models import ProviderPayoutLine

    lines = {
        line.source_id: line
        for line in ProviderPayoutLine.objects.filter(company=company, provider="paymob", payout_batch_id="PMB-9")
    }
    assert set(lines) == {"ORD-1", "ORD-2"}

    settled = lines["ORD-1"]
    assert (settled.net_amount, settled.uncollected_amount) == (Decimal("970.00"), Decimal("0.00"))

    refunded = lines["ORD-2"]
    assert refunded.net_amount == Decimal("285.00")
    # Paymob carries the deducted amount in `refund`, not `uncollected`.
    assert refunded.uncollected_amount == Decimal("200.00")


def test_batch_uncollected_total_reconciles_with_lines(db, company, projected):
    """Sum of per-line uncollected_amount == the batch's uncollected_amount on
    the settlement event — the canonical layer loses nothing in aggregate."""
    from events.models import BusinessEvent
    from events.types import EventTypes
    from platform_connectors.models import ProviderPayoutLine

    for batch_id, provider in (("COD-A", "bosta"), ("PMB-9", "paymob")):
        line_total = sum(
            (
                line.uncollected_amount
                for line in ProviderPayoutLine.objects.filter(
                    company=company, provider=provider, payout_batch_id=batch_id
                )
            ),
            Decimal("0"),
        )
        event = BusinessEvent.objects.get(
            company=company,
            event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
            idempotency_key=f"payment.settlement.received:{provider}:{batch_id}",
        )
        assert line_total == Decimal(str(event.get_data().get("uncollected_amount")))
