# accounting/settlement_provider.py
"""
SettlementProvider routing primitive.

Maps an external payment source (Paymob, PayPal, Bosta, DHL, Aramex,
Shopify Payments, bank transfer, manual collection) to an internal
PostingProfile. The PostingProfile already carries the AR / clearing
control account used by JE construction in
sales.commands.post_sales_invoice — so this table chooses which profile
a given platform-imported invoice posts under, and the JE side stays
unchanged.

Conceptually this is a *mapping* (SettlementProviderMapping would be
more precise) — but for Phase 1 the row IS the provider entity AND the
mapping rule. When multi-courier-per-store routing arrives (A15), the
two concerns split into distinct tables.

Why "settlement provider" not "payment gateway":
    Reconciliation pivots on "who holds or remits the money," not "how
    the customer paid." Bosta-COD and DHL-COD are different
    reconciliation cases (different parties, different schedules,
    different bank deposits) — so the right primary identity is the
    *settlement provider*, not the payment method. payment_method
    survives as a denormalized fact on settlement events for analytics
    ("COD vs card refund rate"); reconciliation queries pivot on
    settlement_provider.

Why this lives in `accounting/` rather than `platform_connectors/`:
    Connectors detect facts (gateway = "Paymob"). Accounting decides
    meaning (this provider routes to that posting profile). Putting
    the table in a connector app would scatter financial-routing logic
    across connector folders as Stripe / Paymob / WooCommerce / Amazon
    / Bosta / DHL are added.

External-system scoping:
    `paypal` from Shopify and `paypal` from a future WooCommerce
    connector are not the same routing decision. The unique constraint
    is (company, external_system, normalized_code).

Unknown providers:
    On first sight of an unmapped gateway code, the projection
    lazy-creates a row with `needs_review=True` and points it at the
    connector's default posting profile so the order still posts. The
    flag is operator-visible via the API (?needs_review=true) and the
    `list_review_settlement_providers` management command — silent
    fallback would violate ENGINEERING_PROTOCOL.md §2.4 "any skipped
    or partial posting must surface visibly to operators."
"""

from __future__ import annotations

import logging
import re

from django.db import models

from accounts.models import Company
from projections.write_barrier import projection_writes_allowed, write_context_allowed
from sales.models import PostingProfile

logger = logging.getLogger(__name__)


# A12: AnalysisDimension code for the settlement-provider routing dimension.
# JE lines tagged with this dimension support reconciliation queries that
# pivot on (clearing_account, dimension_value).
SETTLEMENT_PROVIDER_DIMENSION_CODE = "SETTLEMENT_PROVIDER"


def _provider_dimension_value_code(normalized_code: str) -> str:
    """Build a deterministic AnalysisDimensionValue.code (max 20 chars)."""
    return normalized_code.upper()[:20]


def ensure_settlement_provider_dimension(company):
    """Get-or-create the SETTLEMENT_PROVIDER AnalysisDimension for a company.

    Idempotent. AnalysisDimension is a projection-owned read model, so
    creation is gated under projection_writes_allowed().
    """
    from accounting.models import AnalysisDimension

    existing = AnalysisDimension.objects.filter(
        company=company,
        code=SETTLEMENT_PROVIDER_DIMENSION_CODE,
    ).first()
    if existing:
        return existing

    with projection_writes_allowed():
        dimension = AnalysisDimension.objects.projection().create(
            company=company,
            code=SETTLEMENT_PROVIDER_DIMENSION_CODE,
            name="Settlement Provider",
            name_ar="بوابة التسوية",
            description=(
                "Identifies which external party holds or remits the money "
                "for a given transaction (Paymob, PayPal, Bosta, DHL, etc.). "
                "The reconciliation engine pivots on this dimension to "
                "answer 'where is my money?'."
            ),
            dimension_kind=AnalysisDimension.DimensionKind.CONTEXT,
            is_required_on_posting=False,
            is_active=True,
            applies_to_account_types=[],
            display_order=10,
        )
    return dimension


def ensure_settlement_provider_dimension_value(dimension, normalized_code: str, display_name: str):
    """Get-or-create an AnalysisDimensionValue for a settlement provider.

    Idempotent. The value's code mirrors the provider's normalized_code
    (uppercased) so reconciliation queries can join cleanly.
    """
    from accounting.models import AnalysisDimensionValue

    code = _provider_dimension_value_code(normalized_code)
    existing = AnalysisDimensionValue.objects.filter(
        dimension=dimension,
        code=code,
    ).first()
    if existing:
        return existing

    with projection_writes_allowed():
        value = AnalysisDimensionValue.objects.projection().create(
            dimension=dimension,
            company=dimension.company,
            code=code,
            name=display_name,
            is_active=True,
        )
    return value


def normalize_gateway_code(raw: str | None) -> str:
    """
    Canonicalize a raw gateway string from a connector payload.

    Shopify (and similar) emits gateway names inconsistently across stores
    and over time: "Paymob", "paymob", "Paymob Accept", "PayPal Express
    Checkout", "Cash on Delivery (COD)". Normalize to a stable lookup key:

      - lowercase
      - collapse runs of whitespace / punctuation to a single underscore
      - strip leading/trailing underscores

    Empty / None input returns "" — callers treat that as "unknown".
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s


class SettlementProvider(models.Model):
    """
    Per-company mapping of an external settlement source to a PostingProfile.

    Bootstrap creates the common rows on connector setup. Unknown gateway
    codes lazy-create a row with `needs_review=True` and point at the
    connector's default profile.

    The clearing/control account is *derived* — read it as
    `settlement_provider.posting_profile.control_account`. Do not duplicate
    it on this row; that would create a second source of truth and force
    sync logic.
    """

    class ProviderType(models.TextChoices):
        GATEWAY = "gateway", "Payment Gateway"
        COURIER = "courier", "Courier (COD)"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        MANUAL = "manual", "Manual / Other"
        MARKETPLACE = "marketplace", "Marketplace"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="settlement_providers",
    )
    external_system = models.CharField(
        max_length=50,
        help_text="Connector source, e.g. 'shopify', 'woocommerce', 'stripe_direct'.",
    )
    source_code = models.CharField(
        max_length=100,
        help_text="Raw gateway code from the connector payload (preserved for audit).",
    )
    normalized_code = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Lookup key derived by normalize_gateway_code().",
    )
    display_name = models.CharField(
        max_length=255,
        help_text="Human-readable label shown in UI / reports.",
    )
    provider_type = models.CharField(
        max_length=20,
        choices=ProviderType.choices,
        default=ProviderType.MANUAL,
        help_text=(
            "Kind of settlement entity. Drives UI iconography, analytics "
            "slicing, and (later) divergent reconciliation logic."
        ),
    )
    posting_profile = models.ForeignKey(
        PostingProfile,
        on_delete=models.PROTECT,
        related_name="settlement_providers",
        help_text=("Routing target. The clearing/control account used by the JE is this profile's control_account."),
    )
    dimension_value = models.ForeignKey(
        "accounting.AnalysisDimensionValue",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text=(
            "A12: AnalysisDimensionValue applied to the clearing JE line "
            "when this provider routes an order. The reconciliation engine "
            "pivots on (clearing_account, dimension_value) to surface "
            "per-provider balances. Nullable to allow incremental population "
            "during the A12 rollout; bootstrap and lazy-create paths fill "
            "this FK so production rows are never missing it."
        ),
    )
    is_active = models.BooleanField(default=True)
    needs_review = models.BooleanField(
        default=False,
        help_text=(
            "True for lazy-created rows from unknown gateway codes. "
            "Operator must confirm or re-route before reconciliation."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("external_system", "normalized_code")
        constraints = [
            models.UniqueConstraint(
                fields=("company", "external_system", "normalized_code"),
                name="uniq_settlement_provider_per_company_system_code",
            ),
        ]
        indexes = [
            models.Index(
                fields=("company", "external_system", "is_active"),
                name="accounting__company_8b7ee8_idx",
            ),
            models.Index(
                fields=("company", "needs_review"),
                name="accounting__company_204d43_idx",
            ),
        ]
        verbose_name = "Settlement Provider"
        verbose_name_plural = "Settlement Providers"

    def __str__(self):
        flag = " [REVIEW]" if self.needs_review else ""
        return f"{self.external_system}.{self.normalized_code} -> {self.posting_profile.code}{flag}"

    # ------------------------------------------------------------------
    # Write protection — same pattern as ModuleAccountMapping
    # ------------------------------------------------------------------

    _ALLOWED_WRITE_CONTEXTS = {"command", "projection", "bootstrap", "migration"}

    def save(self, *args, **kwargs):
        from django.conf import settings

        if not write_context_allowed(self._ALLOWED_WRITE_CONTEXTS) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "SettlementProvider is a configuration model. "
                "Direct saves are only allowed within command/projection/bootstrap/migration contexts."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.conf import settings

        if not write_context_allowed(self._ALLOWED_WRITE_CONTEXTS) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "SettlementProvider is a configuration model. "
                "Direct deletes are only allowed within an allowed write context."
            )
        return super().delete(*args, **kwargs)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    @classmethod
    def lookup(
        cls,
        company: Company,
        external_system: str,
        raw_gateway: str | None,
    ) -> SettlementProvider | None:
        """
        Resolve a raw gateway string to a SettlementProvider row, or None.

        Caller decides what to do on miss (typically: lazy-create via
        `lookup_or_create_for_review`).
        """
        normalized = normalize_gateway_code(raw_gateway)
        if not normalized:
            return None
        return (
            cls.objects.filter(
                company=company,
                external_system=external_system,
                normalized_code=normalized,
                is_active=True,
            )
            .select_related("posting_profile")
            .first()
        )

    @classmethod
    def lookup_or_create_for_review(
        cls,
        company: Company,
        external_system: str,
        raw_gateway: str | None,
        fallback_posting_profile: PostingProfile,
    ) -> SettlementProvider | None:
        """
        Resolve provider, or lazy-create one flagged for review.

        Used by projections that have just received an event with a gateway
        we don't recognize. The order still posts (using the fallback
        profile), but the unknown code is recorded so an operator can map
        it deliberately.

        Returns None if `raw_gateway` is empty/None (caller falls back to
        whatever default it already had — no row is created for the empty
        case to avoid polluting the table).
        """
        normalized = normalize_gateway_code(raw_gateway)
        if not normalized:
            return None

        existing = cls.objects.filter(
            company=company,
            external_system=external_system,
            normalized_code=normalized,
        ).first()
        if existing:
            return existing

        from projections.write_barrier import command_writes_allowed

        # A12: lazy-created rows also need a dimension_value so the
        # reconciliation engine can pivot on them. Create the dimension
        # (idempotent) and a matching value before the SettlementProvider
        # row so the FK is populated atomically.
        display_name = (raw_gateway or normalized).strip()[:255] or normalized
        dimension = ensure_settlement_provider_dimension(company)
        dimension_value = ensure_settlement_provider_dimension_value(
            dimension=dimension,
            normalized_code=normalized,
            display_name=display_name,
        )

        with command_writes_allowed():
            row, created = cls.objects.get_or_create(
                company=company,
                external_system=external_system,
                normalized_code=normalized,
                defaults={
                    "source_code": (raw_gateway or "").strip()[:100],
                    "display_name": display_name,
                    "provider_type": cls.ProviderType.MANUAL,
                    "posting_profile": fallback_posting_profile,
                    "dimension_value": dimension_value,
                    "is_active": True,
                    "needs_review": True,
                },
            )
            # Backfill dimension_value on a pre-A12 row that already
            # existed without the FK populated.
            if not created and row.dimension_value_id is None:
                row.dimension_value = dimension_value
                row.save(update_fields=["dimension_value", "updated_at"])
        if created:
            logger.warning(
                "Unknown settlement provider %r seen for company %s on %s — "
                "lazy-created row pointing at fallback profile %s, needs_review=True",
                raw_gateway,
                company.id,
                external_system,
                fallback_posting_profile.code,
            )
        return row
