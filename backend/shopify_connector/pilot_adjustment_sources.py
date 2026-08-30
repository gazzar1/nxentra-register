# shopify_connector/pilot_adjustment_sources.py
"""A5-PR4a: the ADAPTER's pilot-adjustment source resolvers.

Registered from ``ShopifyConnectorConfig.ready()`` into the core
``accounting.pilot_adjustments`` registry — the same dependency inversion as
``register_known_order_lookup`` and the health counters: core accounting
never imports shopify_connector models; the adapter registers in.

Resolvers are pure company-scoped existence reads. References store only the
canonical Shopify numeric id / evidence public UUID — never customer names,
emails or payload data.
"""

from __future__ import annotations

import uuid as _uuid


def _parse_uuid(body: str):
    try:
        return _uuid.UUID(body)
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_shopify_id(body: str):
    # Shopify order/refund ids are positive integers; the natural key
    # (company, shopify_order_id) is the recreate-stable identity (a re-sync
    # after row loss mints a NEW public_id, so public_id is deliberately NOT
    # the reference here). Codex PR #134 round-4 P2: str.isdigit() accepts
    # non-convertible Unicode digits ("²") that make int() raise — restrict
    # to ASCII digits and never let the conversion escape.
    if not body or not body.isascii() or not body.isdigit():
        return None
    try:
        return int(body)
    except ValueError:
        return None


def resolve_shopify_order(company, body: str) -> bool:
    """``shopify_order:<shopify_order_id>`` (dangling-tolerant kind)."""
    order_id = _parse_shopify_id(body)
    if order_id is None:
        return False
    from shopify_connector.models import ShopifyOrder

    return ShopifyOrder.objects.filter(company=company, shopify_order_id=order_id).exists()


def resolve_shopify_refund(company, body: str) -> bool:
    """``shopify_refund:<shopify_refund_id>`` (dangling-tolerant kind)."""
    refund_id = _parse_shopify_id(body)
    if refund_id is None:
        return False
    from shopify_connector.models import ShopifyRefund

    return ShopifyRefund.objects.filter(company=company, shopify_refund_id=refund_id).exists()


def resolve_shopify_reject(company, body: str) -> bool:
    """``shopify_reject:<public_uuid>`` — acknowledged, superseded and
    redacted rows all stay eligible (the evidence row is never deleted; its
    lifecycle state is not a referenceability gate)."""
    row_uuid = _parse_uuid(body)
    if row_uuid is None:
        return False
    from shopify_connector.models import ShopifyRejectedEvidence

    return ShopifyRejectedEvidence.objects.filter(company=company, public_id=row_uuid).exists()


def probe_shopify_reject(body: str):
    row_uuid = _parse_uuid(body)
    if row_uuid is None:
        return None
    from shopify_connector.models import ShopifyRejectedEvidence

    return ShopifyRejectedEvidence.objects.filter(public_id=row_uuid).values_list("company_id", flat=True).first()


def _digit_body_syntax(body: str) -> bool:
    return _parse_shopify_id(body) is not None


def _uuid_body_syntax(body: str) -> bool:
    return _parse_uuid(body) is not None


def register_pilot_adjustment_sources() -> None:
    """Idempotent registration (app-ready may run more than once)."""
    from accounting.pilot_adjustments import register_adjustment_source_resolver

    # No owner probes for shopify_order/shopify_refund: the numeric id is
    # unique only per (company, id), so cross-company ownership is not
    # deterministically observable from the reference alone. Body-syntax
    # validators make grammar checks (write path AND drift) reject a
    # malformed body regardless of referent existence.
    register_adjustment_source_resolver("shopify_order", resolve_shopify_order, body_syntax=_digit_body_syntax)
    register_adjustment_source_resolver("shopify_refund", resolve_shopify_refund, body_syntax=_digit_body_syntax)
    register_adjustment_source_resolver(
        "shopify_reject", resolve_shopify_reject, owner_probe=probe_shopify_reject, body_syntax=_uuid_body_syntax
    )
