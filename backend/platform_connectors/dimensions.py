# platform_connectors/dimensions.py
"""
Platform dimension sync — creates and maintains analysis dimensions
for commerce platform connectors.

Two CONTEXT dimensions are auto-created per company:
    platform  — Which platform (Shopify, Stripe, WooCommerce)
    store     — Which store/account on that platform

Values are synced when stores are connected/disconnected.

Usage from commands or management commands:
    from platform_connectors.dimensions import sync_platform_dimensions
    sync_platform_dimensions(company)

    # Or just for a specific store:
    from platform_connectors.dimensions import ensure_store_dimension_value
    ensure_store_dimension_value(company, platform_slug="shopify", store_code="us-store")
"""

import logging
import uuid

from accounting.models import AnalysisDimension, AnalysisDimensionValue
from projections.write_barrier import projection_writes_allowed

logger = logging.getLogger(__name__)

# Dimension definitions: (code, name, name_ar, kind)
PLATFORM_DIMENSIONS = [
    ("platform", "Platform", "المنصة"),
    ("store", "Store", "المتجر"),
]


def ensure_dimension(company, code, name, name_ar):
    """Get or create a CONTEXT dimension. Returns the AnalysisDimension."""
    dim = AnalysisDimension.objects.filter(
        company=company,
        code=code,
    ).first()
    if dim:
        return dim

    with projection_writes_allowed():
        dim = AnalysisDimension.objects.projection().create(
            company=company,
            public_id=uuid.uuid4(),
            code=code,
            name=name,
            name_ar=name_ar,
            dimension_kind=AnalysisDimension.DimensionKind.CONTEXT,
            is_required_on_posting=False,
            is_active=True,
            applies_to_account_types=[],
            display_order=0,
        )
    logger.info("Created dimension '%s' for company %s", code, company)
    return dim


def ensure_dimension_value(company, dimension, code, name, name_ar=""):
    """Get or create a dimension value. Returns the AnalysisDimensionValue."""
    val = AnalysisDimensionValue.objects.filter(
        dimension=dimension,
        company=company,
        code=code,
    ).first()
    if val:
        return val

    with projection_writes_allowed():
        val = AnalysisDimensionValue.objects.projection().create(
            dimension=dimension,
            company=company,
            public_id=uuid.uuid4(),
            code=code,
            name=name,
            name_ar=name_ar,
            is_active=True,
        )
    logger.info("Created dimension value '%s=%s' for company %s", dimension.code, code, company)
    return val


def sync_platform_dimensions(company):
    """
    Ensure platform and store dimensions exist for a company.

    Creates the 'platform' and 'store' dimensions if they don't exist.
    Then syncs values from registered connectors and connected stores.
    Returns dict of {dimension_code: AnalysisDimension}.
    """
    from accounts.rls import rls_bypass

    dimensions = {}
    with rls_bypass():
        for code, name, name_ar in PLATFORM_DIMENSIONS:
            dimensions[code] = ensure_dimension(company, code, name, name_ar)

        # Sync platform values from the connector registry
        from platform_connectors.registry import connector_registry

        platform_dim = dimensions["platform"]

        for connector in connector_registry.all():
            ensure_dimension_value(
                company,
                platform_dim,
                code=connector.platform_slug,
                name=connector.platform_name,
            )

    return dimensions


def ensure_store_dimension_value(company, platform_slug, store_code, store_name=""):
    """
    Ensure a store dimension value exists for a specific store.

    Called when a new store is connected. The value code format is
    '{platform_slug}:{store_identifier}' to avoid collisions across platforms.

    Args:
        company: Company instance
        platform_slug: e.g. "shopify"
        store_code: Unique identifier for the store (e.g. shop_domain)
        store_name: Human-readable name (defaults to store_code)
    """
    from accounts.rls import rls_bypass

    with rls_bypass():
        store_dim = ensure_dimension(company, "store", "Store", "المتجر")
        value_code = f"{platform_slug}:{store_code}"
        return ensure_dimension_value(
            company,
            store_dim,
            code=value_code,
            name=store_name or store_code,
        )


def resolve_settlement_provider_value(company, platform_slug):
    """The SETTLEMENT_PROVIDER AnalysisDimensionValue for a platform's OWN
    gateway (external_system == normalized_code == platform_slug), or None.

    A139: platform charge/refund JEs must tag their clearing line with this
    value — /finance/reconciliation Stage 1 pivots on the SETTLEMENT_PROVIDER
    dimension, so untagged clearing movement is invisible there and the
    settlement drain then reads as a negative balance on healthy books.

    The double filter (external_system AND normalized_code) is deliberate:
    Shopify's external_system carries many providers (paymob, bosta,
    shopify_payments, ...) and none of them is the platform itself — this
    resolver must never pick an arbitrary courier row.
    """
    from accounting.settlement_provider import SettlementProvider

    provider = (
        SettlementProvider.objects.filter(
            company=company,
            external_system=platform_slug,
            normalized_code=platform_slug,
            is_active=True,
            dimension_value__isnull=False,
        )
        .select_related("dimension_value__dimension")
        .first()
    )
    return provider.dimension_value if provider else None


def resolve_platform_dimensions(company, platform_slug, store_code=None):
    """
    Resolve dimension context for a platform event.

    Returns a dict like {"platform": "shopify", "store": "shopify:us-store.myshopify.com"}
    suitable for passing to _attach_dimensions() or the JE builder.

    If dimensions don't exist yet, returns empty dict (graceful degradation).
    """
    context = {}

    platform_dim = AnalysisDimension.objects.filter(
        company=company,
        code="platform",
        is_active=True,
    ).first()
    if platform_dim:
        val = AnalysisDimensionValue.objects.filter(
            dimension=platform_dim,
            company=company,
            code=platform_slug,
        ).first()
        if val:
            context["platform"] = platform_slug

    if store_code:
        store_dim = AnalysisDimension.objects.filter(
            company=company,
            code="store",
            is_active=True,
        ).first()
        if store_dim:
            value_code = f"{platform_slug}:{store_code}"
            val = AnalysisDimensionValue.objects.filter(
                dimension=store_dim,
                company=company,
                code=value_code,
            ).first()
            if val:
                context["store"] = value_code

    return context
