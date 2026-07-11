# backups/model_registry.py
"""
Registry of all company-scoped models for backup/restore.

Models are organized in dependency order (parents before children)
so that FK references resolve correctly during import.

Three categories:
1. EVENT_STORE — The canonical source of truth (BusinessEvent + payloads)
2. WRITE_MODELS — Mutable state not derived from events
3. READ_MODELS — Projections that can be rebuilt from events (optional in backup)
"""

from collections import OrderedDict


def get_export_registry():
    """
    Return an OrderedDict of {label: ModelClass} in FK dependency order.

    Import these lazily to avoid circular imports.
    """
    from accounting.mappings import ModuleAccountMapping

    # Accounting read models (optional snapshot — speeds up restore)
    from accounting.models import (
        Account,
        AccountAnalysisDefault,
        AccountDimensionRule,
        AnalysisDimension,
        AnalysisDimensionValue,
        BankReconciliation,
        BankStatementLine,
        CompanySequence,
        Customer,
        ExchangeRate,
        JournalEntry,
        JournalLine,
        JournalLineAnalysis,
        PeriodOverrideAudit,
        SettlementProvider,
        StatisticalEntry,
        Vendor,
    )
    from accounting.models import (
        BankStatement as AcctBankStatement,
    )
    from bank_connector.models import (
        BankAccount,
        BankTransaction,
        ReconciliationException,
    )
    from bank_connector.models import (
        BankStatement as BankConnStatement,
    )
    from clinic.models import (
        Doctor,
        Patient,
        Visit,
    )
    from clinic.models import (
        Invoice as ClinicInvoice,
    )
    from clinic.models import (
        Payment as ClinicPayment,
    )
    from edim.models import (
        IdentityCrosswalk,
        IngestionBatch,
        MappingProfile,
        SourceSystem,
        StagedRecord,
    )
    from events.api_keys import ExternalAPIKey
    from events.models import BusinessEvent, CompanyEventCounter, EventBookmark, EventPayload
    from inventory.models import (
        FifoLayer,
        InventoryTransfer,
        InventoryTransferLine,
        StockLedgerEntry,
        StockLedgerSequenceCounter,
        Warehouse,
    )
    from platform_connectors.models import (
        PlatformSettlement,
        ProviderPayout,
        ProviderPayoutLine,
        ProviderRawObject,
    )
    from projections.models import (
        AccountBalance,
        CustomerBalance,
        DimensionBalance,
        FiscalPeriod,
        FiscalPeriodConfig,
        FiscalYear,
        InventoryBalance,
        PeriodAccountBalance,
        ProjectionAppliedEvent,
        ProjectionFailureLog,
        ProjectionStatus,
        VendorBalance,
    )
    from properties.models import (
        Lease,
        Lessee,
        PaymentReceipt,
        Property,
        PropertyAccountMapping,
        PropertyExpense,
        RentScheduleLine,
        SecurityDepositTransaction,
        Unit,
    )
    from properties.models import (
        PaymentAllocation as PropertiesPaymentAllocation,
    )
    from purchases.models import (
        GoodsReceipt,
        GoodsReceiptLine,
        PurchaseBill,
        PurchaseBillLine,
        PurchaseCreditNote,
        PurchaseCreditNoteLine,
        PurchaseOrder,
        PurchaseOrderLine,
    )
    from reconciliation.models import ReconciliationLink
    from sales.models import Item as SalesItem
    from sales.models import PaymentAllocation as SalesPaymentAllocation
    from sales.models import (
        PostingProfile,
        ReceiptAllocation,
        SalesCreditNote,
        SalesCreditNoteLine,
        SalesInvoice,
        SalesInvoiceLine,
        TaxCode,
    )
    from scratchpad.models import ScratchpadRow, ScratchpadRowDimension, VoiceUsageEvent
    from shopify_connector.models import (
        ShopifyDispute,
        ShopifyFulfillment,
        ShopifyOrder,
        ShopifyPayout,
        ShopifyPayoutTransaction,
        ShopifyProduct,
        ShopifyRefund,
        ShopifyStore,
    )
    from stripe_connector.models import (
        StripeAccount,
        StripeCharge,
        StripePayout,
        StripePayoutTransaction,
    )
    from stripe_connector.models import (
        StripeRefund as StripeRefundModel,
    )

    registry = OrderedDict()

    # ── Layer 1: Event Store ──────────────────────────────────────
    registry["events.EventPayload"] = EventPayload
    registry["events.CompanyEventCounter"] = CompanyEventCounter
    registry["events.BusinessEvent"] = BusinessEvent
    registry["events.EventBookmark"] = EventBookmark
    registry["events.ExternalAPIKey"] = ExternalAPIKey

    # ── Layer 2: Accounting Read Models ─────────────────────────
    # These must come before write models because TaxCode, SalesInvoice,
    # PurchaseBill, etc. have FKs to Account, Customer, Vendor.
    registry["accounting.CompanySequence"] = CompanySequence
    registry["accounting.AnalysisDimension"] = AnalysisDimension
    registry["accounting.AnalysisDimensionValue"] = AnalysisDimensionValue
    registry["accounting.Account"] = Account
    registry["accounting.AccountDimensionRule"] = AccountDimensionRule
    registry["accounting.Customer"] = Customer
    registry["accounting.Vendor"] = Vendor
    registry["accounting.ExchangeRate"] = ExchangeRate
    registry["accounting.JournalEntry"] = JournalEntry
    registry["accounting.JournalLine"] = JournalLine
    # A161: JE-line dimension/counterparty analysis + statistical entries
    # — previously omitted, so backups silently lost every dimension tag.
    registry["accounting.JournalLineAnalysis"] = JournalLineAnalysis
    registry["accounting.AccountAnalysisDefault"] = AccountAnalysisDefault
    registry["accounting.StatisticalEntry"] = StatisticalEntry
    registry["accounting.SettlementProvider"] = SettlementProvider
    registry["accounting.BankStatement"] = AcctBankStatement
    registry["accounting.BankStatementLine"] = BankStatementLine
    registry["accounting.BankReconciliation"] = BankReconciliation
    registry["accounting.ModuleAccountMapping"] = ModuleAccountMapping

    # ── Layer 3: Projection Read Models ─────────────────────────
    registry["projections.FiscalYear"] = FiscalYear
    registry["projections.FiscalPeriod"] = FiscalPeriod
    registry["projections.FiscalPeriodConfig"] = FiscalPeriodConfig
    registry["projections.AccountBalance"] = AccountBalance
    registry["projections.PeriodAccountBalance"] = PeriodAccountBalance
    registry["projections.DimensionBalance"] = DimensionBalance
    registry["projections.CustomerBalance"] = CustomerBalance
    registry["projections.VendorBalance"] = VendorBalance
    registry["projections.InventoryBalance"] = InventoryBalance
    # A161: the projection idempotency ledger + status. Omitting
    # ProjectionAppliedEvent while restoring balances is the A154
    # double-apply family: the next process_pending would re-apply the
    # whole restored stream.
    registry["projections.ProjectionAppliedEvent"] = ProjectionAppliedEvent
    registry["projections.ProjectionStatus"] = ProjectionStatus
    registry["projections.ProjectionFailureLog"] = ProjectionFailureLog
    registry["accounting.PeriodOverrideAudit"] = PeriodOverrideAudit

    # ── Layer 4: Base Config (depend on Account, Customer, Vendor) ──
    registry["sales.TaxCode"] = TaxCode
    registry["sales.PostingProfile"] = PostingProfile
    registry["sales.Item"] = SalesItem
    registry["inventory.Warehouse"] = Warehouse
    registry["inventory.StockLedgerSequenceCounter"] = StockLedgerSequenceCounter

    # ── Layer 5: Documents (depend on Layer 4) ────────────────────
    registry["sales.SalesInvoice"] = SalesInvoice
    registry["sales.SalesInvoiceLine"] = SalesInvoiceLine
    registry["sales.SalesCreditNote"] = SalesCreditNote
    registry["sales.SalesCreditNoteLine"] = SalesCreditNoteLine
    registry["sales.ReceiptAllocation"] = ReceiptAllocation
    registry["sales.PaymentAllocation"] = SalesPaymentAllocation
    # POs/receipts before bills: PurchaseBillLine FKs po_line.
    registry["purchases.PurchaseOrder"] = PurchaseOrder
    registry["purchases.PurchaseOrderLine"] = PurchaseOrderLine
    registry["purchases.PurchaseBill"] = PurchaseBill
    registry["purchases.PurchaseBillLine"] = PurchaseBillLine
    registry["purchases.GoodsReceipt"] = GoodsReceipt
    registry["purchases.GoodsReceiptLine"] = GoodsReceiptLine
    registry["purchases.PurchaseCreditNote"] = PurchaseCreditNote
    registry["purchases.PurchaseCreditNoteLine"] = PurchaseCreditNoteLine
    registry["inventory.StockLedgerEntry"] = StockLedgerEntry
    # A161: FIFO costing state (A152) + transfers — previously omitted.
    registry["inventory.FifoLayer"] = FifoLayer
    registry["inventory.InventoryTransfer"] = InventoryTransfer
    registry["inventory.InventoryTransferLine"] = InventoryTransferLine
    registry["scratchpad.ScratchpadRow"] = ScratchpadRow
    registry["scratchpad.ScratchpadRowDimension"] = ScratchpadRowDimension
    registry["scratchpad.VoiceUsageEvent"] = VoiceUsageEvent

    # ── Layer 6: EDIM ─────────────────────────────────────────────
    registry["edim.SourceSystem"] = SourceSystem
    registry["edim.MappingProfile"] = MappingProfile
    registry["edim.IdentityCrosswalk"] = IdentityCrosswalk
    registry["edim.IngestionBatch"] = IngestionBatch
    registry["edim.StagedRecord"] = StagedRecord

    # ── Layer 7: Platform Connectors ──────────────────────────────
    registry["shopify_connector.ShopifyStore"] = ShopifyStore
    registry["shopify_connector.ShopifyOrder"] = ShopifyOrder
    registry["shopify_connector.ShopifyRefund"] = ShopifyRefund
    registry["shopify_connector.ShopifyPayout"] = ShopifyPayout
    registry["shopify_connector.ShopifyPayoutTransaction"] = ShopifyPayoutTransaction
    registry["shopify_connector.ShopifyFulfillment"] = ShopifyFulfillment
    registry["shopify_connector.ShopifyDispute"] = ShopifyDispute
    registry["shopify_connector.ShopifyProduct"] = ShopifyProduct

    registry["stripe_connector.StripeAccount"] = StripeAccount
    registry["stripe_connector.StripeCharge"] = StripeCharge
    registry["stripe_connector.StripeRefund"] = StripeRefundModel
    registry["stripe_connector.StripePayout"] = StripePayout
    registry["stripe_connector.StripePayoutTransaction"] = StripePayoutTransaction

    registry["bank_connector.BankAccount"] = BankAccount
    registry["bank_connector.BankStatement"] = BankConnStatement
    registry["bank_connector.BankTransaction"] = BankTransaction
    registry["bank_connector.ReconciliationException"] = ReconciliationException

    # A161: the ADR-0002 canonical payments layer + the ADR-0001 match-state
    # lever — previously omitted, so a restore lost all reconciliation
    # state AND left stale rows in place (clear iterates this registry).
    registry["platform_connectors.ProviderRawObject"] = ProviderRawObject
    registry["platform_connectors.PlatformSettlement"] = PlatformSettlement
    registry["platform_connectors.ProviderPayout"] = ProviderPayout
    registry["platform_connectors.ProviderPayoutLine"] = ProviderPayoutLine
    registry["reconciliation.ReconciliationLink"] = ReconciliationLink

    # ── Layer 8: Verticals ────────────────────────────────────────
    registry["properties.Property"] = Property
    registry["properties.Unit"] = Unit
    registry["properties.Lessee"] = Lessee
    registry["properties.Lease"] = Lease
    registry["properties.RentScheduleLine"] = RentScheduleLine
    registry["properties.PropertyAccountMapping"] = PropertyAccountMapping
    registry["properties.PaymentReceipt"] = PaymentReceipt
    registry["properties.PaymentAllocation"] = PropertiesPaymentAllocation
    registry["properties.SecurityDepositTransaction"] = SecurityDepositTransaction
    registry["properties.PropertyExpense"] = PropertyExpense

    registry["clinic.Doctor"] = Doctor
    registry["clinic.Patient"] = Patient
    registry["clinic.Visit"] = Visit
    registry["clinic.Invoice"] = ClinicInvoice
    registry["clinic.Payment"] = ClinicPayment

    return registry


# Fields to exclude from export (secrets, auto-generated timestamps).
# These are nulled by NAME on export — a name that doesn't match a real
# concrete field silently redacts NOTHING (that was the A47 bug below).
EXCLUDED_FIELDS = {
    # access_token alone left the rotating refresh_token (shprt_*) — which can
    # re-mint access — exporting in clear; oauth_nonce is a live OAuth secret.
    "shopify_connector.ShopifyStore": ["access_token", "refresh_token", "oauth_nonce"],
    # A47 fix: the old ["access_token","refresh_token"] named fields that don't
    # exist on StripeAccount, so webhook_secret + credential_ref leaked. These
    # are the real secret fields.
    "stripe_connector.StripeAccount": ["webhook_secret", "credential_ref"],
    "events.ExternalAPIKey": ["key_hash"],
}

# A161: company-scoped models DELIBERATELY excluded from backup/restore.
# Every entry needs a reason — tests/test_backup_registry_completeness.py
# fails on any company-FK model that is neither registered nor listed here.
BACKUP_EXEMPT: set[str] = {
    # Identity/access — cross-restore semantics are undefined and restoring
    # another company's members/permissions would be an escalation vector.
    "accounts.CompanyMembership",
    "accounts.CompanyMembershipPermission",
    "accounts.CompanyModule",
    "accounts.Invitation",
    "accounts.Notification",
    # Backup metadata about backups — restoring it would rewrite history.
    "backups.BackupRecord",
    # Compliance log tied to the LIVE store lifecycle (deletion clocks must
    # not be reset by a restore).
    "shopify_connector.GdprRequest",
    # Pre-company OAuth handshake state — no meaning after restore.
    "shopify_connector.PendingShopifyInstall",
    # User.active_company is a UI pointer, not company data.
    "accounts.User",
    # Multi-DB routing infrastructure (parked feature) — not company books.
    "tenant.TenantDirectory",
}

# Models whose data can be rebuilt from events (not critical for backup)
REBUILDABLE_MODELS = {
    "accounting.Account",
    "accounting.Customer",
    "accounting.Vendor",
    "accounting.JournalEntry",
    "accounting.JournalLine",
    "accounting.BankStatement",
    "accounting.BankStatementLine",
    "accounting.BankReconciliation",
    "accounting.ModuleAccountMapping",
    "projections.FiscalYear",
    "projections.FiscalPeriod",
    "projections.AccountBalance",
    "projections.CustomerBalance",
    "projections.VendorBalance",
}
