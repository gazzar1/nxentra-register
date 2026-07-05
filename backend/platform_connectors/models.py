# platform_connectors/models.py
"""
Abstract base models for platform connectors.

Each concrete platform (Shopify, Stripe, etc.) inherits from these abstract
models to get consistent fields while keeping platform-specific tables
(avoids wide sparse tables with a platform_slug column).

These models are abstract (Meta.abstract = True) — they create no database
tables. Concrete models live in each platform's own app.
"""

import uuid

from django.db import models

from accounts.models import Company


class AbstractPlatformConnection(models.Model):
    """
    Base model for a connected platform store/account.

    Concrete examples:
    - ShopifyStore (shopify_connector.models)
    - StripeAccount (future stripe_connector.models)
    """

    class ConnectionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        DISCONNECTED = "DISCONNECTED", "Disconnected"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_connections",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    connection_status = models.CharField(
        max_length=20,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.PENDING,
    )
    error_message = models.TextField(blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.__class__.__name__} ({self.connection_status})"


class AbstractPlatformOrder(models.Model):
    """
    Base model for a commerce order from any platform.

    Stores the financial data needed for reconciliation.
    Platform-specific fields (e.g. Shopify's order_name) go on the
    concrete subclass.
    """

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_orders",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_order_id = models.CharField(max_length=100, db_index=True)

    # Financial data
    total_price = models.DecimalField(max_digits=18, decimal_places=2)
    subtotal_price = models.DecimalField(max_digits=18, decimal_places=2)
    total_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_discounts = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)

    # Processing state
    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    order_date = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Order {self.platform_order_id}"


class AbstractPlatformRefund(models.Model):
    """Base model for a refund from any platform."""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_refunds",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_refund_id = models.CharField(max_length=100, db_index=True)
    platform_order_id = models.CharField(max_length=100, blank=True)

    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    refund_date = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Refund {self.platform_refund_id}"


class AbstractPlatformPayout(models.Model):
    """Base model for a payout/settlement from any platform."""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_payouts",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_payout_id = models.CharField(max_length=100, db_index=True)

    gross_amount = models.DecimalField(max_digits=18, decimal_places=2)
    fees = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    platform_status = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    payout_date = models.DateField()
    journal_entry_id = models.UUIDField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Payout {self.platform_payout_id}"


class AbstractPlatformDispute(models.Model):
    """Base model for a dispute/chargeback from any platform."""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSED = "PROCESSED", "Processed"
        WON = "WON", "Won"
        LOST = "LOST", "Lost"
        ERROR = "ERROR", "Error"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_disputes",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    platform_dispute_id = models.CharField(max_length=100, db_index=True)
    platform_order_id = models.CharField(max_length=100, blank=True)

    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Dispute {self.platform_dispute_id}"


# =============================================================================
# Concrete Model: PlatformSettlement
# =============================================================================

from decimal import Decimal


class PlatformSettlement(models.Model):
    """
    Financial transaction from a platform that isn't a sale or purchase.

    Covers payouts, disputes, fees, adjustments, and chargebacks from any
    connected platform (Shopify, Stripe, Amazon, etc.).

    Each settlement creates a journal entry through the command layer.
    This is the financial document equivalent of SalesInvoice for sales —
    it gives every platform money movement a proper record with FK links
    to the journal entry and optional bank statement line match.

    Examples:
    - PAYOUT: Shopify deposits $847.50 to bank (gross $900 - $52.50 fees)
    - DISPUTE: Customer disputes $150 order + $15 chargeback fee
    - DISPUTE_WON: Dispute reversed, $165 returned
    - FEE: Monthly platform subscription fee
    - ADJUSTMENT: Platform correction or reserve hold/release
    """

    class SettlementType(models.TextChoices):
        PAYOUT = "PAYOUT", "Payout"
        FEE = "FEE", "Fee"
        DISPUTE = "DISPUTE", "Dispute / Chargeback"
        DISPUTE_WON = "DISPUTE_WON", "Dispute Won / Reversed"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        POSTED = "POSTED", "Posted"
        VOIDED = "VOIDED", "Voided"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="platform_settlements",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Platform identification
    platform = models.CharField(
        max_length=50,
        help_text="Platform connector (shopify, stripe, amazon, etc.)",
    )
    platform_document_id = models.CharField(
        max_length=100,
        help_text="External ID (payout ID, dispute ID, etc.)",
    )
    settlement_type = models.CharField(
        max_length=20,
        choices=SettlementType.choices,
    )

    # Financial amounts
    gross_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Gross amount before fees",
    )
    fees = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Platform fees deducted",
    )
    net_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        help_text="Net amount (gross - fees). For payouts: amount deposited to bank.",
    )
    currency = models.CharField(max_length=3)

    # Settlement date
    settlement_date = models.DateField()

    # Status and posting
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_settlements",
        help_text="Journal entry created when posted",
    )

    # Bank reconciliation link
    matched_bank_line = models.ForeignKey(
        "accounting.BankStatementLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_settlements",
        help_text="Matched bank statement line (for reconciliation)",
    )

    # Metadata
    reference = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    auto_created = models.BooleanField(default=False)

    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "platform_settlement"
        ordering = ["-settlement_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "platform", "platform_document_id", "settlement_type"],
                name="uniq_platform_settlement",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "platform", "settlement_type"]),
            models.Index(fields=["company", "settlement_date"]),
            models.Index(fields=["company", "status"]),
        ]
        verbose_name = "Platform Settlement"
        verbose_name_plural = "Platform Settlements"

    def __str__(self):
        return f"{self.platform} {self.settlement_type} {self.platform_document_id} ({self.net_amount} {self.currency})"


class ProviderRawObject(models.Model):
    """Raw, source-of-record copy of one external provider object — a Stripe
    BalanceTransaction/Payout, a webhook event, a settlement CSV row — WITH
    provenance (api_version, fetched_at, source channel, payload hash), so
    normalization is REPLAYABLE after a bug.

    Explicitly RAW / source-only, NOT a truth model: truth lives in events +
    projections. The normalizer reads from here; replay = re-run it over
    `payload_json`. Consolidates the scattered per-connector `raw_payload`
    columns (the event store keeps normalized OUTPUT, not replayable raw INPUT).
    See ADR-0002.

    Dedup key `(company, provider, object_type, external_id, payload_hash)`: a
    re-fetch of the SAME payload is idempotent; a CHANGED payload appends a new
    row, giving an append-only snapshot history per object.
    """

    class Source(models.TextChoices):
        API = "api", "API pull"
        WEBHOOK = "webhook", "Webhook"
        REPORT = "report", "Reporting API / report"
        CSV = "csv", "CSV import"
        MANUAL = "manual", "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="provider_raw_objects")
    provider = models.CharField(max_length=40)  # 'stripe' / 'shopify' / 'paymob' / …
    object_type = models.CharField(max_length=60)  # 'balance_transaction' / 'payout' / 'charge' / 'event' / …
    external_id = models.CharField(max_length=255)  # provider id (bt_… / po_… / evt_…)
    api_version = models.CharField(max_length=40, default="", blank=True)
    source = models.CharField(max_length=20, choices=Source.choices)
    fetched_at = models.DateTimeField(help_text="When WE fetched/received it (not the provider's created time).")
    payload_hash = models.CharField(max_length=64)  # SHA-256 of the canonical payload
    payload_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "provider_raw_object"
        ordering = ["-fetched_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "provider", "object_type", "external_id", "payload_hash"],
                name="uniq_provider_raw_object",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "provider", "object_type"]),
            models.Index(fields=["company", "provider", "external_id"]),
            models.Index(fields=["company", "payload_hash"]),
        ]
        verbose_name = "Provider Raw Object"
        verbose_name_plural = "Provider Raw Objects"

    def __str__(self):
        return f"ProviderRawObject({self.provider}:{self.object_type}:{self.external_id})"

    @classmethod
    def record(
        cls,
        *,
        company,
        provider: str,
        object_type: str,
        external_id: str,
        payload: dict,
        source: str,
        api_version: str = "",
        fetched_at=None,
    ):
        """Idempotently record a raw provider object (the replay/audit seam an
        adapter calls at ingestion). Dedups on payload_hash: the same payload is
        a no-op; a changed payload appends a new snapshot. Returns (obj, created).
        """
        from django.utils import timezone

        from events.serialization import compute_payload_hash

        return cls.objects.get_or_create(
            company=company,
            provider=(provider or "").strip().lower(),
            object_type=object_type,
            external_id=external_id,
            payload_hash=compute_payload_hash(payload),
            defaults={
                "payload_json": payload,
                "source": source,
                "api_version": api_version,
                "fetched_at": fetched_at or timezone.now(),
            },
        )


def derive_provider_payout_id(company_id: int, provider: str, payout_batch_id: str) -> uuid.UUID:
    """Deterministic primary key for the canonical payout HEADER, so the same payout
    → the same row across a from-scratch projection rebuild (mirrors
    reconciliation.derive_link_id)."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nxentra:provider_payout:{company_id}:{provider}:{payout_batch_id}",
    )


class ProviderPayout(models.Model):
    """Canonical, provider-agnostic payout/settlement HEADER, materialized by the
    sole-writer PaymentsProjection from PAYMENT_SETTLEMENT_RECEIVED (ADR-0002 PR-C1).

    The header companion to ProviderPayoutLine — carries the batch-level totals +
    provider-NEUTRAL status/account fields the legacy stripe_connector.StripePayout
    header exposes to the recon views + bank-reconciliation match engine. A READ
    MODEL, not truth (truth is the event). Deterministic id → replay/rebuild upserts
    the same row. Sole writer is PaymentsProjection within projection_writes_allowed().

    Cutover (PR-C) is staged: this is the additive "expand" step — reads are NOT
    switched here. journal_entry_id is intentionally absent: it's bank-match-mutated
    state with no settlement event (a documented C2/C3 parity gap, candidate source
    = ReconciliationLink), the same category as the line cache's `verified` (PR-D).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="provider_payouts")
    provider = models.CharField(max_length=40)  # 'stripe' / 'paymob' / 'bosta' / …
    payout_batch_id = models.CharField(max_length=255)
    provider_status = models.CharField(max_length=40, blank=True)  # the provider's own status (Stripe "paid")
    provider_account_reference = models.CharField(max_length=255, blank=True)  # provider account id
    provider_account_name = models.CharField(max_length=255, blank=True)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    fees = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    uncollected_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, blank=True)
    payout_date = models.DateField(null=True, blank=True)
    provider_metadata = models.JSONField(default=dict, blank=True)  # provider-specific residue only
    # ADR-0002 PR-D2: header reconciliation outcome, stamped from the latest
    # PROVIDER_PAYOUT_RECONCILED snapshot (last-write-wins). "" = never reconciled.
    # Variances are the snapshot's event-frozen numbers (settlement-event header
    # totals vs its line sums), NOT the legacy header-vs-cache comparison.
    reconciliation_outcome = models.CharField(max_length=20, blank=True, default="")  # ""|verified|discrepancy
    matched_line_count = models.PositiveIntegerField(default=0)
    unmatched_line_count = models.PositiveIntegerField(default=0)
    verified_line_count = models.PositiveIntegerField(default=0)
    gross_variance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    fee_variance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_variance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    reconciliation_source = models.CharField(
        max_length=20, blank=True, default=""
    )  # auto_reconcile|manual_verify|backfill
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider_payout"
        ordering = ["company", "provider", "-payout_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "provider", "payout_batch_id"],
                name="uniq_provider_payout",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "provider"]),
            models.Index(fields=["company", "provider", "provider_status"]),
        ]
        verbose_name = "Provider Payout"
        verbose_name_plural = "Provider Payouts"

    def __str__(self):
        return f"ProviderPayout({self.provider}:{self.payout_batch_id})"

    def save(self, *args, **kwargs):
        # Read model — sole writer is PaymentsProjection within projection_writes_allowed().
        # TESTING bypasses (matches ProviderPayoutLine / ReconciliationLink).
        from django.conf import settings

        from projections.write_barrier import write_context_allowed

        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "ProviderPayout is a read model. It is written only by "
                "PaymentsProjection within projection_writes_allowed()."
            )
        super().save(*args, **kwargs)


def derive_provider_payout_line_id(company_id: int, provider: str, payout_batch_id: str, line_index: int) -> uuid.UUID:
    """Deterministic primary key so the same payout line → the same row across a
    from-scratch projection rebuild (mirrors reconciliation.derive_link_id).

    Keyed on line_index — the position within the event's frozen line_items[].
    The settlement event is idempotent (one per payout id), so its line ordering
    never changes after first emit; replay reproduces identical ids.
    """
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"nxentra:provider_payout_line:{company_id}:{provider}:{payout_batch_id}:{line_index}",
    )


class ProviderPayoutLine(models.Model):
    """One constituent line of a provider payout — a charge/refund/adjustment that
    settled into the batch — materialized by the sole-writer PaymentsProjection
    from ``PAYMENT_SETTLEMENT_RECEIVED.line_items[]`` (ADR-0002 Phase 2).

    Provider-agnostic (Stripe first; Paymob/Bosta ride the same settlement event).
    A READ MODEL, not truth: truth is the event. Sole writer is PaymentsProjection
    within ``projection_writes_allowed()`` (mirrors ReconciliationLink). Identity is
    the deterministic ``derive_provider_payout_line_id`` so replay/rebuild upserts
    the same rows instead of duplicating.

    Dual-write phase (PR-A): ``stripe_connector.sync._upsert_read_models`` still
    direct-writes the legacy StripePayout/StripePayoutTransaction caches; PR-C
    removes them once this projection is the source of truth.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="provider_payout_lines")
    provider = models.CharField(max_length=40)  # 'stripe' / 'paymob' / 'bosta' / …
    payout_batch_id = models.CharField(max_length=255)  # the payout/settlement id this line settled into
    line_index = models.PositiveIntegerField()  # stable position within the frozen event line_items[]
    source_id = models.CharField(max_length=255, blank=True)  # the line's order/charge id (line_items.order_id)
    kind = models.CharField(max_length=40, blank=True)  # the line type (line_items.status: charge/refund/adjustment/…)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    fee = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    # Canonical contra/returned portion of the line: Bosta failed-delivery
    # (line_items "uncollected") + Paymob refund/chargeback (line_items "refund").
    # Mirrors the event-level uncollected_amount → Sales Returns. Keeps non-Stripe
    # lines whose economic content lives outside gross/fee/net from being dropped
    # to zero (ADR-0002 Phase 2 architecture gate).
    uncollected_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3, blank=True)
    # ADR-0002 PR-D2: match state, stamped from PROVIDER_PAYOUT_RECONCILED
    # snapshots (last-write-wins) — the replay-durable home for the legacy
    # StripePayoutTransaction.verified/local_charge direct writes. `verified`
    # mirrors the persisted legacy value at snapshot time (parity target for
    # the STRIPE_CANONICAL_VERIFIED_READS switch); matched_ref replaces the
    # local_charge FK with the matched object's external id.
    verified = models.BooleanField(default=False)
    match_kind = models.CharField(max_length=20, blank=True, default="")  # charge|refund|auto_type|none|""
    matched_ref = models.CharField(max_length=255, blank=True, default="")
    matched_ref_type = models.CharField(max_length=20, blank=True, default="")  # charge|refund|""
    provider_line_ref = models.CharField(max_length=255, blank=True, default="")  # provider's line id (balance txn)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "provider_payout_line"
        ordering = ["company", "provider", "payout_batch_id", "line_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "provider", "payout_batch_id", "line_index"],
                name="uniq_provider_payout_line",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "provider", "payout_batch_id"]),
            models.Index(fields=["company", "provider", "source_id"]),
            # Serves the per-batch verified-count aggregation (PR-D2 read switch).
            models.Index(fields=["company", "provider", "payout_batch_id", "verified"]),
        ]
        verbose_name = "Provider Payout Line"
        verbose_name_plural = "Provider Payout Lines"

    def __str__(self):
        return f"ProviderPayoutLine({self.provider}:{self.payout_batch_id}#{self.line_index})"

    def save(self, *args, **kwargs):
        # Guard: a read model. Sole writer is PaymentsProjection within
        # projection_writes_allowed(). TESTING bypasses (matches ReconciliationLink).
        from django.conf import settings

        from projections.write_barrier import write_context_allowed

        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "ProviderPayoutLine is a read model. It is written only by "
                "PaymentsProjection within projection_writes_allowed()."
            )
        super().save(*args, **kwargs)
