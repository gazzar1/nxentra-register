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
    from inventory.models import StockLedgerEntry, StockLedgerSequenceCounter, Warehouse
    from projections.models import (
        AccountBalance,
        CustomerBalance,
        FiscalPeriod,
        FiscalYear,
        InventoryBalance,
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
    from purchases.models import PurchaseBill, PurchaseBillLine
    from sales.models import Item as SalesItem
    from sales.models import PaymentAllocation as SalesPaymentAllocation
    from sales.models import PostingProfile, ReceiptAllocation, SalesInvoice, SalesInvoiceLine, TaxCode
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
    registry["accounting.BankStatement"] = AcctBankStatement
    registry["accounting.BankStatementLine"] = BankStatementLine
    registry["accounting.BankReconciliation"] = BankReconciliation
    registry["accounting.ModuleAccountMapping"] = ModuleAccountMapping

    # ── Layer 3: Projection Read Models ─────────────────────────
    registry["projections.FiscalYear"] = FiscalYear
    registry["projections.FiscalPeriod"] = FiscalPeriod
    registry["projections.AccountBalance"] = AccountBalance
    registry["projections.CustomerBalance"] = CustomerBalance
    registry["projections.VendorBalance"] = VendorBalance
    registry["projections.InventoryBalance"] = InventoryBalance

    # ── Layer 4: Base Config (depend on Account, Customer, Vendor) ──
    registry["sales.TaxCode"] = TaxCode
    registry["sales.PostingProfile"] = PostingProfile
    registry["sales.Item"] = SalesItem
    registry["inventory.Warehouse"] = Warehouse
    registry["inventory.StockLedgerSequenceCounter"] = StockLedgerSequenceCounter

    # ── Layer 5: Documents (depend on Layer 4) ────────────────────
    registry["sales.SalesInvoice"] = SalesInvoice
    registry["sales.SalesInvoiceLine"] = SalesInvoiceLine
    registry["sales.ReceiptAllocation"] = ReceiptAllocation
    registry["sales.PaymentAllocation"] = SalesPaymentAllocation
    registry["purchases.PurchaseBill"] = PurchaseBill
    registry["purchases.PurchaseBillLine"] = PurchaseBillLine
    registry["inventory.StockLedgerEntry"] = StockLedgerEntry
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


# Fields to exclude from export (secrets, auto-generated timestamps)
EXCLUDED_FIELDS = {
    "shopify_connector.ShopifyStore": ["access_token"],
    "stripe_connector.StripeAccount": ["access_token", "refresh_token"],
    "events.ExternalAPIKey": ["key_hash"],
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
