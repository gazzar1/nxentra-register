# tests/test_a139_settlement_provider_dim.py
"""A139 — platform charge/refund JEs must tag SETTLEMENT_PROVIDER on their
clearing line.

/finance/reconciliation Stage 1 pivots EXCLUSIVELY on journal lines tagged
with the SETTLEMENT_PROVIDER dimension. Pre-A139, Stripe charge JEs carried
only the platform/store CONTEXT dims, so booked charges were invisible to the
page and the settlement drain (whose clearing CR IS tagged) rendered the
provider as Expected 0 / Open −gross on perfectly healthy books.

Pinned here:
  * the charge JE tags its clearing DEBIT line only (a tag on revenue lines
    would mint bogus per-account Stage-1 rows);
  * the full circle: charge → Stage 1 Expected; settlement drain → Settled,
    Open 0 — the page tells the true story end-to-end;
  * refunds land in "Refunded" (platform_* source_module + credit>0), not
    "Settled";
  * the backfill command retro-tags pre-A139 JEs idempotently;
  * the resolver never picks a courier row off Shopify's provider list.
"""

import hashlib
import hmac
import json
import time
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from stripe_connector.models import StripeAccount

WHSEC = "whsec_test_secret_abcdefghijklmnop"
WEBHOOK_URL = "/api/platforms/stripe/webhooks/"
SETTLEMENT_DIM_CODE = "SETTLEMENT_PROVIDER"


def _sign(secret: str, body: bytes) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _post_webhook(client, event: dict):
    body = json.dumps(event).encode()
    return client.post(
        WEBHOOK_URL, data=body, content_type="application/json", HTTP_STRIPE_SIGNATURE=_sign(WHSEC, body)
    )


def _charge_event(charge_id: str, amount_cents: int = 10000):
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "charge.succeeded",
        "data": {
            "object": {
                "id": charge_id,
                "amount": amount_cents,
                "currency": "usd",
                "billing_details": {"email": None, "name": None, "address": None, "phone": None},
                "created": 1700000000,
                "payment_intent": None,
                "description": None,
                "receipt_email": None,
            }
        },
    }


def _refund_event(charge_id: str, refund_cents: int):
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "charge.refunded",
        "data": {
            "object": {
                "id": charge_id,
                "amount": 10000,
                "amount_refunded": refund_cents,
                "currency": "usd",
                "refunds": {
                    "data": [
                        {
                            "id": f"re_{uuid4().hex[:10]}",
                            "amount": refund_cents,
                            "reason": "requested_by_customer",
                            "created": 1700000100,
                        }
                    ]
                },
                "billing_details": {"email": None, "name": None, "address": None, "phone": None},
                "created": 1700000000,
                "payment_intent": None,
                "description": None,
                "receipt_email": None,
            }
        },
    }


@pytest.fixture
def stripe_ready(db, company):
    """Connected Stripe account + seeded platform (mappings, SettlementProvider
    with dimension value STRIPE, clearing 11510)."""
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
        webhook_secret=WHSEC,
    )


def _process_platform_projection(company):
    from platform_connectors.projections import PlatformAccountingProjection

    PlatformAccountingProjection().process_pending(company)


def _stripe_stage1_row(company):
    from accounting.reconciliation_views import _stage1_per_provider

    rows = [r for r in _stage1_per_provider(company, date.today()) if r["dimension_value_code"] == "STRIPE"]
    return rows[0] if rows else None


@pytest.mark.django_db
def test_charge_je_tags_clearing_line_only(client, company, stripe_ready):
    from accounting.models import AnalysisDimension, JournalEntry

    assert _post_webhook(client, _charge_event("ch_a139")).status_code == 200
    _process_platform_projection(company)

    je = JournalEntry.objects.get(company=company, source_module="platform_stripe", source_document="ch_a139")
    assert je.status == JournalEntry.Status.POSTED

    dim = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_DIM_CODE)
    clearing_line = je.lines.get(debit__gt=0)
    revenue_line = je.lines.get(credit__gt=0)

    tags = clearing_line.analysis_tags.filter(dimension=dim)
    assert tags.count() == 1
    assert tags.first().dimension_value.code == "STRIPE"
    # Revenue must NOT carry the tag — it would mint a bogus Stage-1 row
    # on the revenue account.
    assert not revenue_line.analysis_tags.filter(dimension=dim).exists()


@pytest.mark.django_db
def test_stage1_full_circle_charge_then_settlement(settings, client, company, stripe_ready, owner_membership):
    """The page story A139 exists for: charge → Expected 100 / Open 100;
    settlement drain → Settled 100 / Open 0. Pre-A139: Expected 0 / Open −100.

    Runs with event validation ON (as in production): the JOURNAL_ENTRY_POSTED
    payload now carries per-line analysis_tags and must still validate."""
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData

    settings.DISABLE_EVENT_VALIDATION = False

    assert _post_webhook(client, _charge_event("ch_circle")).status_code == 200
    _process_platform_projection(company)

    row = _stripe_stage1_row(company)
    assert row is not None, "charge must surface a Stripe Stage-1 row"
    assert Decimal(row["total_debit"]) == Decimal("100.00")
    assert Decimal(row["open_balance"]) == Decimal("100.00")

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="stripe:po_circle",
        idempotency_key="payment.settlement.received:stripe:po_circle",
        data=PaymentSettlementReceivedData(
            amount="100.00",
            currency="USD",
            transaction_date="2026-07-02",
            document_ref="po_circle",
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id="po_circle",
            gross_amount="100.00",
            fees="3.20",
            net_amount="96.80",
            uncollected_amount="0",
            payment_method="card",
            payout_date="2026-07-02",
            line_items=[
                {"order_id": "ch_circle", "gross": "100.00", "fee": "3.20", "net": "96.80", "status": "charge"}
            ],
            provider_status="paid",
        ),
    )
    PaymentSettlementProjection().process_pending(company)

    row = _stripe_stage1_row(company)
    assert Decimal(row["total_debit"]) == Decimal("100.00")
    assert Decimal(row["total_credit"]) == Decimal("100.00")  # settled
    assert Decimal(row["open_balance"]) == Decimal("0.00")
    assert Decimal(row["total_refunded"]) == Decimal("0.00")


@pytest.mark.django_db
def test_refund_lands_in_refunded_not_settled(client, company, stripe_ready):
    assert _post_webhook(client, _charge_event("ch_ref")).status_code == 200
    assert _post_webhook(client, _refund_event("ch_ref", 2000)).status_code == 200
    _process_platform_projection(company)

    row = _stripe_stage1_row(company)
    assert row is not None
    assert Decimal(row["total_debit"]) == Decimal("100.00")
    assert Decimal(row["total_refunded"]) == Decimal("20.00")
    assert Decimal(row["total_credit"]) == Decimal("0.00")  # settled excludes the refund CR
    assert Decimal(row["open_balance"]) == Decimal("80.00")


@pytest.mark.django_db
def test_backfill_command_retro_tags_pre_a139_jes(client, company, stripe_ready):
    from accounting.models import AnalysisDimension, JournalLineAnalysis
    from platform_connectors.management.commands.backfill_platform_settlement_dims import backfill_company

    assert _post_webhook(client, _charge_event("ch_old")).status_code == 200
    _process_platform_projection(company)

    # Simulate a pre-A139 JE: strip the settlement-provider tags.
    dim = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_DIM_CODE)
    JournalLineAnalysis.objects.filter(company=company, dimension=dim).delete()
    assert _stripe_stage1_row(company) is None  # invisible again — the pre-A139 state

    report = backfill_company(company, apply=False)
    assert report == [{"provider": "stripe", "clearing": report[0]["clearing"], "untagged": 1, "tagged": 0}]
    assert _stripe_stage1_row(company) is None  # report-only must not write

    applied = backfill_company(company, apply=True)
    assert applied[0]["tagged"] == 1

    row = _stripe_stage1_row(company)
    assert row is not None
    assert Decimal(row["total_debit"]) == Decimal("100.00")

    # Idempotent: nothing left to tag.
    again = backfill_company(company, apply=True)
    assert again[0]["untagged"] == 0


def test_resolver_never_picks_a_courier_row(db, company):
    """Shopify's external_system carries paymob/bosta/... provider rows; the
    platform's own resolver must not grab one of them."""
    from accounting.models import Account
    from accounting.settlement_provider import SettlementProvider
    from platform_connectors.dimensions import resolve_settlement_provider_value
    from sales.models import PostingProfile

    clearing = Account.objects.create(
        company=company,
        code="11550",
        name="Paymob Clearing",
        account_type=Account.AccountType.ASSET,
    )
    profile = PostingProfile.objects.create(
        company=company,
        code="PG-PAYMOB",
        name="Paymob",
        control_account=clearing,
    )
    SettlementProvider.objects.create(
        company=company,
        external_system="shopify",
        source_code="Paymob",
        normalized_code="paymob",
        display_name="Paymob",
        provider_type=SettlementProvider.ProviderType.GATEWAY,
        posting_profile=profile,
        is_active=True,
    )
    assert resolve_settlement_provider_value(company, "shopify") is None


def _dispute_event(charge_id: str, dispute_cents: int):
    return {
        "id": f"evt_{uuid4().hex[:12]}",
        "type": "charge.dispute.created",
        "data": {
            "object": {
                "id": f"dp_{uuid4().hex[:10]}",
                "charge": charge_id,
                "amount": dispute_cents,
                "currency": "usd",
                "reason": "fraudulent",
                "status": "needs_response",
                "evidence_details": {"due_by": 1700000000},
            }
        },
    }


@pytest.mark.django_db
def test_journal_entry_replay_reconstructs_tags(client, company, stripe_ready):
    """The durability fix itself: JournalEntryProjection._replace_lines wipes
    and rebuilds lines from the JOURNAL_ENTRY_POSTED payload — the payload's
    per-line analysis_tags must reconstruct the tags (public-id resolution +
    line rekeying), clearing line tagged, revenue line not."""
    from accounting.models import AnalysisDimension, JournalEntry
    from events.models import BusinessEvent
    from events.types import EventTypes
    from projections.accounting import JournalEntryProjection

    assert _post_webhook(client, _charge_event("ch_replay")).status_code == 200
    _process_platform_projection(company)

    je = JournalEntry.objects.get(company=company, source_module="platform_stripe", source_document="ch_replay")
    posted = BusinessEvent.objects.get(
        company=company, event_type=EventTypes.JOURNAL_ENTRY_POSTED, aggregate_id=str(je.public_id)
    )

    JournalEntryProjection()._replace_lines(je, posted.get_data()["lines"])

    dim = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_DIM_CODE)
    clearing_line = je.lines.get(debit__gt=0)
    revenue_line = je.lines.get(credit__gt=0)
    assert clearing_line.analysis_tags.filter(dimension=dim, dimension_value__code="STRIPE").exists()
    assert not revenue_line.analysis_tags.filter(dimension=dim).exists()


@pytest.mark.django_db
def test_dispute_je_stays_untagged_even_after_backfill(client, company, stripe_ready):
    """Dispute JEs (DR chargeback expense / CR clearing) are deliberately
    untagged — and the backfill must not tag them either, or Stage 1 would
    misread historical chargebacks as customer refunds."""
    from accounting.models import AnalysisDimension, JournalEntry
    from platform_connectors.management.commands.backfill_platform_settlement_dims import backfill_company

    assert _post_webhook(client, _charge_event("ch_dsp")).status_code == 200
    assert _post_webhook(client, _dispute_event("ch_dsp", 5000)).status_code == 200
    _process_platform_projection(company)

    dispute_jes = JournalEntry.objects.filter(company=company, source_module="platform_stripe").exclude(
        source_document="ch_dsp"
    )
    assert dispute_jes.exists(), "the dispute webhook must post a JE for this test to mean anything"

    backfill_company(company, apply=True)

    dim = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_DIM_CODE)
    for je in dispute_jes:
        for line in je.lines.all():
            assert not line.analysis_tags.filter(dimension=dim).exists()

    # Stage 1: the dispute is not "Refunded"; the charge is Expected.
    row = _stripe_stage1_row(company)
    assert Decimal(row["total_refunded"]) == Decimal("0.00")
    assert Decimal(row["total_debit"]) == Decimal("100.00")


@pytest.mark.django_db
def test_backfill_tags_survive_projection_replay(client, company, stripe_ready):
    """The backfilled tag must survive a full JE-projection replay: the
    pre-A139 POSTED payload (no analysis_tags) wipes the row, and the
    JOURNAL_LINE_ANALYSIS_SET event the backfill emitted restores it."""
    from accounting.models import AnalysisDimension, JournalEntry, JournalLineAnalysis
    from events.models import BusinessEvent
    from events.types import EventTypes
    from platform_connectors.management.commands.backfill_platform_settlement_dims import backfill_company
    from projections.accounting import JournalEntryProjection

    assert _post_webhook(client, _charge_event("ch_durable")).status_code == 200
    _process_platform_projection(company)

    # Simulate a pre-A139 entry: strip tags from rows AND from the stored
    # POSTED payload (a real pre-A139 event carries none).
    dim = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_DIM_CODE)
    JournalLineAnalysis.objects.filter(company=company).delete()
    je = JournalEntry.objects.get(company=company, source_module="platform_stripe", source_document="ch_durable")
    posted = BusinessEvent.objects.get(
        company=company, event_type=EventTypes.JOURNAL_ENTRY_POSTED, aggregate_id=str(je.public_id)
    )
    pre_a139_lines = [{k: v for k, v in line.items() if k != "analysis_tags"} for line in posted.get_data()["lines"]]

    assert backfill_company(company, apply=True)[0]["tagged"] == 1
    assert _stripe_stage1_row(company) is not None

    # Replay wipes the direct rows (pre-A139 payload has no tags)...
    JournalEntryProjection()._replace_lines(je, pre_a139_lines)
    assert _stripe_stage1_row(company) is None

    # ...and the backfill's JOURNAL_LINE_ANALYSIS_SET event restores them.
    analysis_set = BusinessEvent.objects.get(
        company=company, event_type=EventTypes.JOURNAL_LINE_ANALYSIS_SET, aggregate_id=str(je.public_id)
    )
    JournalEntryProjection().handle(analysis_set)
    row = _stripe_stage1_row(company)
    assert row is not None
    assert Decimal(row["total_debit"]) == Decimal("100.00")


@pytest.mark.django_db
def test_attach_dimensions_compat_import(client, company, stripe_ready):
    """shopify_connector imports _attach_dimensions inside a broad try/except —
    if the symbol disappears, restock JEs silently lose all dims. Pin both the
    import path and the behavior."""
    from accounting.models import JournalEntry
    from platform_connectors.dimensions import sync_platform_dimensions
    from platform_connectors.je_builder import _attach_dimensions

    sync_platform_dimensions(company)
    assert _post_webhook(client, _charge_event("ch_compat")).status_code == 200
    _process_platform_projection(company)

    je = JournalEntry.objects.get(company=company, source_module="platform_stripe", source_document="ch_compat")
    lines = list(je.lines.all())
    _attach_dimensions(company, lines, {"platform": "stripe"})
    for line in lines:
        assert line.analysis_tags.filter(dimension__code="platform", dimension_value__code="stripe").exists()
