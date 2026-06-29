# tests/test_platform_clearing_role_resolution.py
"""
Regression: the generic PlatformAccountingProjection looked up the clearing
account under the canonical role PLATFORM_CLEARING, but the Stripe seed registers
its dedicated per-provider clearing account (code 11510) under role
STRIPE_CLEARING. So `mapping.get("PLATFORM_CLEARING")` was None and the projection
skipped EVERY JE — celery logged "Account mapping missing CLEARING or
SALES_REVENUE for stripe — skipping".

Invisible until charges first flowed end-to-end (2026-06-29): the charge stored
(Stripe dashboard showed it) but no journal entry ever posted. Fix: the
projection now accepts either the canonical PLATFORM_CLEARING or a
provider-specific {PROVIDER}_CLEARING role.
"""

from decimal import Decimal

import pytest

from platform_connectors.projections import PlatformAccountingProjection


def test_clearing_account_accepts_provider_specific_and_canonical_roles():
    proj = PlatformAccountingProjection()
    clearing = object()
    # Stripe seeds STRIPE_CLEARING (its dedicated per-provider clearing account).
    assert proj._clearing_account({"STRIPE_CLEARING": clearing}, "stripe") is clearing
    # The canonical role still resolves (for connectors that use it).
    assert proj._clearing_account({"PLATFORM_CLEARING": clearing}, "woocommerce") is clearing
    # Canonical wins when both are present.
    other = object()
    assert proj._clearing_account({"PLATFORM_CLEARING": clearing, "STRIPE_CLEARING": other}, "stripe") is clearing
    # Genuinely unmapped → None (the projection still skips, by design).
    assert proj._clearing_account({"SALES_REVENUE": object()}, "stripe") is None


@pytest.mark.django_db
def test_stripe_order_paid_posts_je_with_stripe_clearing_role(company, owner_membership):
    """End-to-end with the REAL Stripe seed (role STRIPE_CLEARING): a
    PLATFORM_ORDER_PAID event must post a JE DR 11510 Stripe Clearing /
    CR 41000 Sales Revenue. Pre-fix the projection skipped (no JE at all)."""
    from accounting.models import JournalEntry, JournalLine
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PlatformOrderPaidData
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)  # seeds accounts + mapping under role STRIPE_CLEARING

    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PLATFORM_ORDER_PAID,
        aggregate_type="PlatformOrder",
        aggregate_id="ch_role_test",
        idempotency_key="test.order_paid:ch_role_test",
        data=PlatformOrderPaidData(
            platform_slug="stripe",
            platform_order_id="ch_role_test",
            order_name="ch_role_test",
            amount="20.00",
            subtotal="20.00",
            total_tax="0",
            total_shipping="0",
            currency="USD",
            transaction_date="2026-06-20",
        ),
    )

    PlatformAccountingProjection().process_pending(company)

    je = JournalEntry.objects.filter(company=company, source_module="platform_stripe").first()
    assert je is not None, "projection skipped the JE — clearing role not resolved"

    lines = {ln.account.code: ln for ln in JournalLine.objects.filter(entry=je).select_related("account")}
    assert lines["11510"].debit == Decimal("20.00")  # Stripe Clearing (STRIPE_CLEARING role)
    assert lines["41000"].credit == Decimal("20.00")  # Sales Revenue
