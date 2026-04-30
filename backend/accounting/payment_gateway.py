# accounting/payment_gateway.py
"""
PaymentGateway routing primitive.

Maps an external payment source (Paymob, PayPal, Manual COD, Shopify Payments,
bank transfer, etc.) to an internal PostingProfile. The PostingProfile already
carries the AR / clearing control account used by JE construction in
sales.commands.post_sales_invoice — so this table chooses which profile a
given platform-imported invoice posts under, and the JE side stays unchanged.

Conceptually this is a *mapping* (PaymentGatewayMapping would be more
accurate) — but the simpler `PaymentGateway` name is used because the brief
and roadmap reference it that way. The row is a routing primitive, not the
gateway itself.

Why this lives in `accounting/` rather than `platform_connectors/`:
    Connectors detect facts (gateway = "Paymob"). Accounting decides meaning
    (this gateway routes to that posting profile). Putting the table in a
    connector app would scatter financial-routing logic across connector
    folders as Stripe / Paymob / WooCommerce / Amazon are added.

External-system scoping:
    `paypal` from Shopify and `paypal` from WooCommerce are not the same
    routing decision. The unique constraint is
    (company, external_system, normalized_code).

Unknown gateways:
    On first sight of an unmapped gateway code, the projection lazy-creates
    a row with `needs_review=True` and points it at the connector's default
    posting profile so the order still posts. The flag is operator-visible
    via the API (?needs_review=true) and the
    `list_review_payment_gateways` management command — silent fallback
    would violate ENGINEERING_PROTOCOL.md §2.4 "any skipped or partial
    posting must surface visibly to operators."
"""

from __future__ import annotations

import logging
import re

from django.db import models

from accounts.models import Company
from projections.write_barrier import write_context_allowed
from sales.models import PostingProfile

logger = logging.getLogger(__name__)


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


class PaymentGateway(models.Model):
    """
    Per-company mapping of an external payment source to a PostingProfile.

    Bootstrap creates the common rows on connector setup. Unknown gateway
    codes lazy-create a row with `needs_review=True` and point at the
    connector's default profile.

    The clearing/control account is *derived* — read it as
    `payment_gateway.posting_profile.control_account`. Do not duplicate it
    on this row; that would create a second source of truth and force sync
    logic.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="payment_gateways",
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
    posting_profile = models.ForeignKey(
        PostingProfile,
        on_delete=models.PROTECT,
        related_name="payment_gateways",
        help_text=("Routing target. The clearing/control account used by the JE is this profile's control_account."),
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
                name="uniq_payment_gateway_per_company_system_code",
            ),
        ]
        indexes = [
            models.Index(fields=("company", "external_system", "is_active")),
            models.Index(fields=("company", "needs_review")),
        ]
        verbose_name = "Payment Gateway"
        verbose_name_plural = "Payment Gateways"

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
                "PaymentGateway is a configuration model. "
                "Direct saves are only allowed within command/projection/bootstrap/migration contexts."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.conf import settings

        if not write_context_allowed(self._ALLOWED_WRITE_CONTEXTS) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "PaymentGateway is a configuration model. "
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
    ) -> PaymentGateway | None:
        """
        Resolve a raw gateway string to a PaymentGateway row, or None.

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
    ) -> PaymentGateway | None:
        """
        Resolve gateway → row, or lazy-create one flagged for review.

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

        with command_writes_allowed():
            row, created = cls.objects.get_or_create(
                company=company,
                external_system=external_system,
                normalized_code=normalized,
                defaults={
                    "source_code": (raw_gateway or "").strip()[:100],
                    "display_name": (raw_gateway or normalized).strip()[:255] or normalized,
                    "posting_profile": fallback_posting_profile,
                    "is_active": True,
                    "needs_review": True,
                },
            )
        if created:
            logger.warning(
                "Unknown payment gateway %r seen for company %s on %s — "
                "lazy-created row pointing at fallback profile %s, needs_review=True",
                raw_gateway,
                company.id,
                external_system,
                fallback_posting_profile.code,
            )
        return row
