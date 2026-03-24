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
    from events.models import EventPayload, CompanyEventCounter, BusinessEvent, EventBookmark
    from events.api_keys import ExternalAPIKey

    from accounting.models import CompanySequence
    from sales.models import TaxCode, PostingProfile, Item as SalesItem
    from sales.models import SalesInvoice, SalesInvoiceLine, ReceiptAllocation, PaymentAllocation as SalesPaymentAllocation
    from purchases.models import PurchaseBill, PurchaseBillLine
    from inventory.models import Warehouse, StockLedgerEntry, StockLedgerSequenceCounter
    from scratchpad.models import ScratchpadRow, ScratchpadRowDimension, VoiceUsageEvent

    from edim.models import (
        SourceSystem, MappingProfile, IdentityCrosswalk,
        IngestionBatch, StagedRecord,
    )

    from shopify_connector.models import (
        ShopifyStore, ShopifyOrder,
        ShopifyRefund,
        ShopifyPayout, ShopifyPayoutTransaction,
        ShopifyFulfillment, ShopifyDispute, ShopifyProduct,
    )
    from stripe_connector.models import (
        StripeAccount, StripeCharge, StripeRefund as StripeRefundModel,
        StripePayout, StripePayoutTransaction,
    )
    from bank_connector.models import (
        BankAccount, BankStatement as BankConnStatement,
        BankTransaction, ReconciliationException,
    )

    from properties.models import (
        Property, Unit, Lessee, Lease, RentScheduleLine,
        PropertyAccountMapping, PaymentReceipt,
        PaymentAllocation as PropertiesPaymentAllocation,
        SecurityDepositTransaction, PropertyExpense,
    )
    from clinic.models import (
        Doctor, Patient, Visit,
        Payment as ClinicPayment, Invoice as ClinicInvoice,
    )

    # Accounting read models (optional snapshot — speeds up restore)
    from accounting.models import (
        Account, Customer, Vendor,
        JournalEntry, JournalLine,
        AnalysisDimension, AnalysisDimensionValue,
        AccountDimensionRule,
        ExchangeRate,
        BankStatement as AcctBankStatement,
        BankStatementLine, BankReconciliation,
    )
    from accounting.mappings import ModuleAccountMapping

    from projections.models import (
        FiscalYear, FiscalPeriod,
        AccountBalance, CustomerBalance, VendorBalance,
    )

    registry = OrderedDict()

    # ── Layer 1: Event Store ──────────────────────────────────────
    registry["events.EventPayload"] = EventPayload
    registry["events.CompanyEventCounter"] = CompanyEventCounter
    registry["events.BusinessEvent"] = BusinessEvent
    registry["events.EventBookmark"] = EventBookmark
    registry["events.ExternalAPIKey"] = ExternalAPIKey

    # ── Layer 2: Base Config (no inter-model FKs) ─────────────────
    registry["accounting.CompanySequence"] = CompanySequence
    registry["sales.TaxCode"] = TaxCode
    registry["sales.PostingProfile"] = PostingProfile
    registry["sales.Item"] = SalesItem
    registry["inventory.Warehouse"] = Warehouse
    registry["inventory.StockLedgerSequenceCounter"] = StockLedgerSequenceCounter

    # ── Layer 3: Documents (depend on Layer 2) ────────────────────
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

    # ── Layer 4: EDIM ─────────────────────────────────────────────
    registry["edim.SourceSystem"] = SourceSystem
    registry["edim.MappingProfile"] = MappingProfile
    registry["edim.IdentityCrosswalk"] = IdentityCrosswalk
    registry["edim.IngestionBatch"] = IngestionBatch
    registry["edim.StagedRecord"] = StagedRecord

    # ── Layer 5: Platform Connectors ──────────────────────────────
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

    # ── Layer 6: Verticals ────────────────────────────────────────
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
    registry["clinic.Payment"] = ClinicPayment
    registry["clinic.Invoice"] = ClinicInvoice

    # ── Layer 7: Accounting Read Models (snapshot) ────────────────
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

    # ── Layer 8: Projection Read Models (snapshot) ────────────────
    registry["projections.FiscalYear"] = FiscalYear
    registry["projections.FiscalPeriod"] = FiscalPeriod
    registry["projections.AccountBalance"] = AccountBalance
    registry["projections.CustomerBalance"] = CustomerBalance
    registry["projections.VendorBalance"] = VendorBalance

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
