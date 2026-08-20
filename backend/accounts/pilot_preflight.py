"""A4: exhaustive, read-only constrained-pilot preflight.

Point-in-time proof + drift detector for ``ISOLATED_SHADOW_LEDGER_V1``. It NEVER
mutates or "repairs" data — it reports every violation so the founder can
correct them. It is not a substitute for the runtime gates in
``accounts.pilot_policy`` (which are the safety guarantee); it verifies the
company's state matches the contract and detects drift after sync/import.

Phases:
  - ``setup``   — zero Shopify stores permitted (store not connected yet);
  - ``go-live`` — exactly one active supported store required (G1 prerequisite).

``for_activation=True`` runs every forbidden-STATE check but skips the
"profile already enabled" check (activation is about to enable it).
"""

from __future__ import annotations

from dataclasses import dataclass

EGP = "EGP"


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


def run_preflight(company, *, phase: str = "go-live", for_activation: bool = False) -> list[Violation]:
    """Return the list of contract violations for ``company`` (empty == safe)."""
    from accounts import pilot_policy
    from accounts.models import Company, CompanyMembership, Invitation
    from accounts.rls import rls_bypass

    v: list[Violation] = []

    with rls_bypass():
        # --- profile ------------------------------------------------------
        if not for_activation:
            if pilot_policy.profile_of(company) != Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1:
                v.append(
                    Violation(
                        "profile_not_enabled",
                        "Company is not on the ISOLATED_SHADOW_LEDGER_V1 pilot profile.",
                    )
                )

        # --- isolation: exactly one merchant company ----------------------
        active_companies = Company.objects.filter(is_active=True).count()
        if active_companies != 1:
            v.append(
                Violation(
                    "not_isolated",
                    f"Deployment has {active_companies} active companies; the isolated "
                    "shadow-ledger contract permits exactly one.",
                )
            )
        # Activation demands true single-tenancy: no OTHER company ROW may exist
        # in the database, active or not — a deactivated row could otherwise be
        # reactivated to bypass the one-merchant-per-deployment contract.
        other_rows = Company.objects.exclude(id=company.id).count()
        if other_rows:
            v.append(
                Violation(
                    "not_isolated_rows",
                    f"Deployment contains {other_rows} other company row(s); the isolated "
                    "database must hold exactly one merchant company (active or not).",
                )
            )

        # --- exactly one active OWNER membership --------------------------
        memberships = list(CompanyMembership.objects.filter(company=company, is_active=True))
        if len(memberships) != 1:
            v.append(
                Violation(
                    "membership_count",
                    f"Company has {len(memberships)} active memberships; exactly one is permitted.",
                )
            )
        elif memberships[0].role != CompanyMembership.Role.OWNER:
            v.append(
                Violation(
                    "membership_not_owner",
                    "The single active membership must be OWNER.",
                )
            )

        # --- no pending invitations ---------------------------------------
        pending_invites = Invitation.objects.filter(primary_company=company, status=Invitation.Status.PENDING).count()
        if pending_invites:
            v.append(Violation("pending_invitations", f"{pending_invites} pending invitation(s) must be cancelled."))

        # --- currency + fiscal --------------------------------------------
        if company.default_currency != EGP or company.functional_currency != EGP:
            v.append(
                Violation(
                    "currency_not_egp",
                    f"Currency must be EGP (default={company.default_currency}, "
                    f"functional={company.functional_currency}).",
                )
            )
        if company.fiscal_year_start_month != 1:
            v.append(
                Violation(
                    "fiscal_not_january",
                    f"Fiscal year must start in January (got month {company.fiscal_year_start_month}).",
                )
            )
        v += _period_violations(company)

        # --- Shopify store count (phase-dependent) ------------------------
        v += _shopify_store_violations(company, phase)

        # --- go-live readiness: the FULL agreed workflow must be configured.
        # ISOLATED_SHADOW_LEDGER_V1 is the Shopify → Paymob/Bosta → canonical
        # bank → ledger pilot; a Shopify-only or Shopify+bank-only variant is a
        # DIFFERENT (future) contract and must not appear implicitly here.
        if phase == "go-live":
            v += _binding_violations(company)
            v += _supported_mapping_violations(company)
            v += _provider_violations(company)
            v += _bank_account_violations(company)

        # --- capability gates must be ON (config proof) -------------------
        # Only meaningful once the profile is enabled; at activation time the
        # profile is still NONE (about to be set), so capabilities are not yet
        # blocked — that is expected, not a violation.
        if not for_activation:
            v += _capability_gate_violations(company)

        # --- forbidden data state -----------------------------------------
        v += _stripe_state_violations(company)
        v += _inventory_state_violations(company)
        v += _out_of_scope_data_violations(company)
        v += _legacy_bank_violations(company)
        v += _purchase_state_violations(company)
        v += _journal_currency_residue_violations(company)
        v += _manual_ar_violations(company)
        v += _edim_violations(company)
        v += _revaluation_violations(company)
        v += _exchange_rate_violations(company)
        v += _period_date_violations(company)
        v += _vertical_module_violations(company)
        v += _external_ingest_violations(company)
        v += _seeded_event_violations(company)
        v += _projection_rebuild_violations(company)

    return v


def _period_violations(company) -> list[Violation]:
    from projections.models import FiscalPeriodConfig

    cfg = FiscalPeriodConfig.objects.filter(company=company).order_by("-fiscal_year").first()
    if cfg is None:
        return [Violation("no_period_config", "No fiscal-period configuration exists.")]
    # The supported structure is 12 monthly periods, with or without the extra
    # adjustment period (13). Anything else is a non-standard setup.
    if cfg.period_count not in (12, 13):
        return [
            Violation(
                "period_structure",
                f"Unsupported period structure (period_count={cfg.period_count}; "
                "expected 12 monthly periods, or 13 with the adjustment period).",
            )
        ]
    return []


def _shopify_store_violations(company, phase: str) -> list[Violation]:
    from shopify_connector.models import ShopifyStore

    stores = list(ShopifyStore.objects.filter(company=company, status=ShopifyStore.Status.ACTIVE))
    if phase == "go-live":
        if len(stores) != 1:
            return [
                Violation("store_count", f"go-live requires exactly one active Shopify store (found {len(stores)}).")
            ]
        return _store_currency_violations(stores[0])
    # setup
    if len(stores) > 1:
        return [Violation("store_count", f"At most one active Shopify store is permitted (found {len(stores)}).")]
    return []


def _store_currency_violations(store) -> list[Violation]:
    """Go-live: PROVE the store itself operates in EGP — runtime order rejection
    and an absence of historical non-EGP orders do not establish it. Uses the
    durable ``shop_currency`` snapshot; when empty, a read-only live probe
    (recent order → shop API), never persisted from this read-only preflight.
    Unknown or unreachable currency fails go-live."""
    from shopify_connector.commands import resolve_store_currency

    currency = resolve_store_currency(store, allow_remote=True, persist=False)
    if not currency:
        return [
            Violation(
                "store_currency_unknown",
                f"Could not determine the Shopify currency for {store.shop_domain} "
                "(no durable snapshot; live probe failed). Run a product sync or fix "
                "API access, then re-run the preflight.",
            )
        ]
    if currency != EGP:
        return [
            Violation(
                "store_currency_not_egp",
                f"Shopify store {store.shop_domain} operates in {currency}; the pilot requires {EGP}.",
            )
        ]
    return []


def _binding_violations(company) -> list[Violation]:
    """Go-live: an ACTIVE binding must join the EXACT sole active store, the EXACT
    sole active OWNER membership, and an active user (A1). A binding on a
    disconnected/other store, an inactive membership, or a deactivated user does
    not satisfy the check."""
    from accounts.models import CompanyMembership
    from shopify_connector.models import ShopifyStore, ShopifyUserBinding

    stores = list(ShopifyStore.objects.filter(company=company, status=ShopifyStore.Status.ACTIVE))
    owners = list(CompanyMembership.objects.filter(company=company, is_active=True, role=CompanyMembership.Role.OWNER))
    if len(stores) != 1 or len(owners) != 1:
        # store_count / membership_* violations describe the root cause; this
        # records that the exact-binding requirement cannot be verified either.
        return [
            Violation(
                "binding_missing",
                "Binding cannot be verified: go-live requires exactly one active store "
                f"(found {len(stores)}) and one active OWNER membership (found {len(owners)}).",
            )
        ]
    ok = ShopifyUserBinding.objects.filter(
        store=stores[0],
        membership=owners[0],
        is_active=True,
        membership__is_active=True,
        membership__user__is_active=True,
    ).exists()
    if not ok:
        return [
            Violation(
                "binding_missing",
                f"No active ShopifyUserBinding joins store {stores[0].shop_domain} to the sole "
                "active OWNER membership (with an active user). Complete the A1 link ceremony.",
            )
        ]
    return []


def _supported_mapping_violations(company) -> list[Violation]:
    """Go-live: the supported Shopify order/refund + settlement + bank workflows
    post through the Shopify clearing and Expected-Bank-Deposit accounts. Each
    required mapping must exist on the exact module, point at a non-null account
    of the SAME company, and that account must be postable (ACTIVE, non-header)."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account

    required = ("SHOPIFY_CLEARING", "EXPECTED_BANK_DEPOSIT")
    rows = {
        m.role: m
        for m in ModuleAccountMapping.objects.filter(
            company=company, module="shopify_connector", role__in=required
        ).select_related("account")
    }
    problems: list[str] = []
    for role in required:
        m = rows.get(role)
        if m is None or m.account_id is None:
            problems.append(f"{role}: mapping missing")
            continue
        acct = m.account
        if acct.company_id != company.id:
            problems.append(f"{role}: account belongs to another company")
        elif acct.is_header or acct.status != Account.Status.ACTIVE:
            problems.append(f"{role}: account {acct.code} is not postable (header or not ACTIVE)")
    if problems:
        return [Violation("missing_supported_mapping", "; ".join(problems) + ".")]
    return []


def _provider_violations(company) -> list[Violation]:
    """Go-live: the agreed pilot workflow settles through Paymob and/or Bosta. At
    least one ACTIVE supported settlement provider must exist, and every active
    supported provider must route to an ACTIVE posting profile of the same
    company whose control account is postable."""
    from accounting.settlement_imports import supported_settlement_providers
    from accounting.settlement_provider import SettlementProvider

    supported = set(supported_settlement_providers())  # {"paymob", "bosta"}
    providers = list(
        SettlementProvider.objects.filter(
            company=company, is_active=True, normalized_code__in=supported
        ).select_related("posting_profile", "posting_profile__control_account")
    )
    if not providers:
        return [
            Violation(
                "provider_missing",
                "No active Paymob/Bosta settlement provider is configured; the pilot's "
                "Shopify → Paymob/Bosta → bank workflow requires at least one.",
            )
        ]
    problems: list[str] = []
    for p in providers:
        pp = p.posting_profile
        if pp is None or not pp.is_active or pp.company_id != company.id:
            problems.append(f"{p.normalized_code}: posting profile missing/inactive")
            continue
        ctrl = pp.control_account
        if ctrl is None or ctrl.is_header or str(ctrl.status) != "ACTIVE":
            problems.append(f"{p.normalized_code}: control account not postable")
    if problems:
        return [Violation("provider_posting_profile", "; ".join(problems) + ".")]
    return []


def _bank_account_violations(company) -> list[Violation]:
    """Go-live: the canonical bank-import workflow needs a postable Cash/Bank GL
    account. (The GL account model carries no currency; EGP-ness of bank DATA is
    enforced at import by ``require_pilot_currency`` and drift-checked by
    ``non_egp_bank_data``.)"""
    from accounting.models import Account

    ok = Account.objects.filter(
        company=company, role=Account.AccountRole.LIQUIDITY, status=Account.Status.ACTIVE, is_header=False
    ).exists()
    if not ok:
        return [
            Violation(
                "bank_account_missing",
                "No active postable Cash/Bank (LIQUIDITY) GL account exists for the canonical bank import.",
            )
        ]
    return []


def _legacy_bank_violations(company) -> list[Violation]:
    """The legacy ``bank_connector`` module is out of scope (the canonical
    ``accounting`` bank import is the supported path). A clean isolated deployment
    holds no legacy bank rows; any present are drift the founder must clear."""
    from bank_connector.models import BankAccount, BankStatement

    n = BankAccount.objects.filter(company=company).count() + BankStatement.objects.filter(company=company).count()
    if n:
        return [
            Violation(
                "legacy_bank_data",
                f"{n} legacy bank_connector record(s) exist; the legacy banking module is out of scope.",
            )
        ]
    return []


def _purchase_state_violations(company) -> list[Violation]:
    """A4: the purchasing / accounts-payable workflow is out of scope. Detect —
    read-only, never repaired — purchases-module enablement, purchase documents,
    and purchase/AP FINANCIAL history via DURABLE canonical evidence, not only
    surviving document rows: the immutable event stream and vendor-side payment
    allocations survive document deletion (the privileged admin bulk-delete
    residual), and ``record_vendor_payment`` creates a posted journal, a
    ``cash.vendor_payment_recorded`` event and allocation rows WITHOUT any
    purchase document. Explicit canonical event constants only — never prefix
    matching. Vendors, VENDOR posting profiles, NON_STOCK items and the purchase
    sequence counters are shared master data / harmless counters, NOT purchasing
    execution state, and are intentionally exempt. (Going-forward, the runtime
    gates on the purchasing commands and ``record_vendor_payment`` keep these
    paths unreachable; this is drift detection for anything that predates
    activation or bypassed a guard.)"""
    from accounts.models import CompanyModule
    from events.models import BusinessEvent
    from events.types import EventTypes
    from purchases.models import GoodsReceipt, PurchaseBill, PurchaseCreditNote, PurchaseOrder
    from sales.models import PaymentAllocation

    # Any of these proves a purchase document existed, even if the row is gone.
    document_event_types = (
        EventTypes.PURCHASES_BILL_CREATED,
        EventTypes.PURCHASES_BILL_UPDATED,
        EventTypes.PURCHASES_BILL_POSTED,
        EventTypes.PURCHASES_BILL_VOIDED,
        EventTypes.PURCHASES_ORDER_CREATED,
        EventTypes.PURCHASES_ORDER_UPDATED,
        EventTypes.PURCHASES_ORDER_APPROVED,
        EventTypes.PURCHASES_ORDER_CANCELLED,
        EventTypes.PURCHASES_ORDER_CLOSED,
        EventTypes.PURCHASES_GOODS_RECEIPT_CREATED,
        EventTypes.PURCHASES_GOODS_RECEIPT_POSTED,
        EventTypes.PURCHASES_GOODS_RECEIPT_VOIDED,
        EventTypes.PURCHASES_CREDIT_NOTE_CREATED,
        EventTypes.PURCHASES_CREDIT_NOTE_POSTED,
        EventTypes.PURCHASES_CREDIT_NOTE_VOIDED,
    )
    # Any of these proves purchase-originated or AP JOURNAL history (posting,
    # reversal-on-void, or a vendor payment).
    financial_event_types = (
        EventTypes.PURCHASES_BILL_POSTED,
        EventTypes.PURCHASES_BILL_VOIDED,
        EventTypes.PURCHASES_CREDIT_NOTE_POSTED,
        EventTypes.PURCHASES_CREDIT_NOTE_VOIDED,
        EventTypes.VENDOR_PAYMENT_RECORDED,
    )

    out: list[Violation] = []

    if CompanyModule.objects.filter(company=company, module_key="purchases", is_enabled=True).exists():
        out.append(
            Violation(
                "purchases_module_enabled",
                "The purchases module is enabled; the purchasing/AP workflow is out of scope for the pilot.",
            )
        )

    # --- document evidence: live rows OR canonical lifecycle events ---------
    documents = (
        PurchaseBill.objects.filter(company=company).count()
        + PurchaseOrder.objects.filter(company=company).count()
        + GoodsReceipt.objects.filter(company=company).count()
        + PurchaseCreditNote.objects.filter(company=company).count()
    )
    document_events = BusinessEvent.objects.filter(company=company, event_type__in=document_event_types).count()
    if documents or document_events:
        parts: list[str] = []
        if documents:
            parts.append(f"{documents} purchase document row(s) (bills / orders / goods receipts / credit notes)")
        if document_events:
            parts.append(f"{document_events} canonical purchase lifecycle event(s)")
        out.append(
            Violation(
                "purchase_document_state",
                "; ".join(parts) + " exist; the purchasing workflow is out of scope for the pilot.",
            )
        )

    # --- financial evidence: surviving posted FKs, posting/void + vendor-
    # payment events, and vendor-side allocations. sales.PaymentAllocation is
    # vendor-only by construction: record_vendor_payment is its sole writer
    # (customer receipts use ReceiptAllocation; the properties vertical uses
    # its own properties.PaymentAllocation model), so no customer allocation
    # can be falsely counted here. ---------------------------------------
    posted_rows = (
        PurchaseBill.objects.filter(company=company, posted_journal_entry__isnull=False).count()
        + PurchaseCreditNote.objects.filter(company=company, posted_journal_entry__isnull=False).count()
    )
    financial_events = BusinessEvent.objects.filter(company=company, event_type__in=financial_event_types).count()
    vendor_allocations = PaymentAllocation.objects.filter(company=company).count()
    if posted_rows or financial_events or vendor_allocations:
        parts = []
        if posted_rows:
            parts.append(f"{posted_rows} purchase document(s) with posted journal entries")
        if financial_events:
            parts.append(f"{financial_events} purchase posting/void or vendor-payment event(s)")
        if vendor_allocations:
            parts.append(f"{vendor_allocations} vendor payment allocation(s)")
        out.append(
            Violation(
                "purchase_financial_state",
                "; ".join(parts) + " exist; purchase-originated AP/expense/input-VAT and "
                "vendor-payment history is out of scope for the pilot.",
            )
        )

    return out


def _capability_gate_violations(company) -> list[Violation]:
    """Prove the runtime gates are ON: each unsupported capability must be blocked
    by the profile. Catches an unknown/misconfigured profile that failed to block."""
    from accounts import pilot_policy

    out: list[Violation] = []
    for cap in pilot_policy.Capability:
        if pilot_policy.is_supported(company, cap):
            out.append(
                Violation(
                    f"capability_not_blocked:{cap.value}",
                    f"Capability '{cap.value}' must be blocked under the pilot profile but is not.",
                )
            )
    return out


def _stripe_state_violations(company) -> list[Violation]:
    from stripe_connector.models import StripeAccount

    if StripeAccount.objects.filter(company=company).exists():
        return [Violation("stripe_connected", "A Stripe account is connected; Stripe is out of scope for the pilot.")]
    return []


def _inventory_state_violations(company) -> list[Violation]:
    """Option B: no executable/authoritative inventory state may exist."""
    from django.db.models import Q

    from inventory.models import FifoLayer, StockLedgerEntry
    from sales.models import Item
    from shopify_connector.models import ShopifyFulfillment, ShopifyStore

    out: list[Violation] = []

    inv_items = Item.objects.filter(company=company, item_type=Item.ItemType.INVENTORY).count()
    if inv_items:
        out.append(Violation("inventory_items", f"{inv_items} INVENTORY item(s) exist; Option B forbids them."))

    mapped = (
        Item.objects.filter(company=company)
        .filter(Q(inventory_account__isnull=False) | Q(cogs_account__isnull=False))
        .count()
    )
    if mapped:
        out.append(Violation("item_inv_cogs_mapping", f"{mapped} item(s) carry inventory/COGS accounts."))

    # Option B never wires the shopify_connector INVENTORY / COGS module roles
    # (the fulfillment COGS builder consults these). A fresh isolated deployment
    # activated at setup (0 stores) has none; a store onboarded before activation
    # would — the founder must remove them before go-live.
    from accounting.mappings import ModuleAccountMapping

    module_maps = ModuleAccountMapping.objects.filter(
        company=company, module="shopify_connector", role__in=("INVENTORY", "COGS")
    ).count()
    if module_maps:
        out.append(
            Violation(
                "module_inv_cogs_mapping",
                f"{module_maps} shopify_connector INVENTORY/COGS module mapping(s) exist; Option B forbids them.",
            )
        )

    store_mappings = (
        ShopifyStore.objects.filter(company=company)
        .filter(Q(default_inventory_account__isnull=False) | Q(default_cogs_account__isnull=False))
        .count()
    )
    if store_mappings:
        out.append(Violation("store_inv_cogs_mapping", "A Shopify store has default inventory/COGS account mappings."))

    if FifoLayer.objects.filter(company=company).exists():
        out.append(Violation("fifo_layers", "FIFO layers exist; Option B forbids inventory costing state."))

    if StockLedgerEntry.objects.filter(company=company).exists():
        out.append(Violation("stock_ledger", "Stock-ledger entries exist; Option B forbids stock movements."))

    from projections.models import InventoryBalance

    if InventoryBalance.objects.filter(company=company).exists():
        out.append(
            Violation("inventory_balances", "Inventory-balance rows exist; Option B permits no residual balance state.")
        )

    cogs_pending = ShopifyFulfillment.objects.filter(
        company=company, status=ShopifyFulfillment.Status.COGS_PENDING
    ).count()
    if cogs_pending:
        out.append(
            Violation("cogs_pending", f"{cogs_pending} COGS_PENDING fulfillment(s) exist (deferred-COGS residue).")
        )

    return out


def _out_of_scope_data_violations(company) -> list[Violation]:
    """Detect data from capabilities the pilot forbids: Shopify Payments payout
    accounting, disputes/chargebacks, and any non-EGP order/refund the pilot's
    ingestion gates should never have admitted (drift detection)."""
    from shopify_connector.models import ShopifyDispute, ShopifyOrder, ShopifyPayout, ShopifyRefund

    out: list[Violation] = []

    payouts = ShopifyPayout.objects.filter(company=company).count()
    if payouts:
        out.append(
            Violation("payout_data", f"{payouts} Shopify payout record(s) exist; payout accounting is out of scope.")
        )

    disputes = ShopifyDispute.objects.filter(company=company).count()
    if disputes:
        out.append(Violation("dispute_data", f"{disputes} Shopify dispute record(s) exist; disputes are out of scope."))

    non_egp_orders = ShopifyOrder.objects.filter(company=company).exclude(currency=EGP).exclude(currency="").count()
    non_egp_refunds = ShopifyRefund.objects.filter(company=company).exclude(currency=EGP).exclude(currency="").count()
    if non_egp_orders or non_egp_refunds:
        out.append(
            Violation(
                "non_egp_shopify_data",
                f"{non_egp_orders} non-EGP Shopify order(s) and {non_egp_refunds} non-EGP refund(s) "
                "exist; the pilot ingests EGP only.",
            )
        )

    out += _non_egp_financial_state_violations(company)
    return out


def _non_egp_financial_state_violations(company) -> list[Violation]:
    """EGP-only drift detection across the rest of the in-scope financial state:
    journal entries, provider settlement records, and canonical bank statements.
    Runtime gates reject new foreign ingestion; these catch anything that predates
    activation or slipped through an ungated path."""
    from accounting.models import BankStatement as CanonicalBankStatement
    from accounting.models import JournalEntry
    from platform_connectors.models import PlatformSettlement

    out: list[Violation] = []

    je = JournalEntry.objects.filter(company=company).exclude(currency=EGP).exclude(currency="").count()
    if je:
        out.append(
            Violation("non_egp_journal_data", f"{je} non-EGP journal entr(ies) exist; the pilot books EGP only.")
        )

    st = PlatformSettlement.objects.filter(company=company).exclude(currency=EGP).exclude(currency="").count()
    if st:
        out.append(
            Violation("non_egp_settlement_data", f"{st} non-EGP provider settlement record(s) exist (EGP only).")
        )

    bank = CanonicalBankStatement.objects.filter(company=company).exclude(currency=EGP).exclude(currency="").count()
    if bank:
        out.append(Violation("non_egp_bank_data", f"{bank} non-EGP canonical bank statement(s) exist (EGP only)."))

    return out


def _journal_currency_residue_violations(company) -> list[Violation]:
    """Journal-level EGP drift the header-only ``non_egp_journal_data`` check
    structurally cannot see (all statuses, INCOMPLETE/DRAFT included):

    - ``non_egp_journal_line_data`` — a non-blank/non-EGP JournalLine beneath an
      EGP/blank header. Disjoint from the header code by construction: lines
      whose ENTRY header is already foreign are that header's rows, not these.
    - ``fx_line_residue``   — an EGP/blank line carrying FX mechanics: a foreign
      leg via ``amount_currency``, or a non-1 conversion rate. Normal stamps are
      explicitly excluded (``amount_currency`` NULL and rate NULL/1 are the
      legitimate EGP/default projection values), so clean EGP books never fire.
    - ``fx_header_rate_residue`` — an EGP/blank header carrying a non-1
      exchange rate. The A142 stamp only overrides the 1.0 default on a real
      foreign conversion, so this is always drift on EGP-only books.

    Read-only counts; blank ``""`` keeps its home-currency meaning throughout.
    """
    from decimal import Decimal

    from django.db.models import Q

    from accounting.models import JournalEntry, JournalLine

    out: list[Violation] = []
    home = (EGP, "")

    lines = JournalLine.objects.filter(company=company, entry__currency__in=home).exclude(currency__in=home).count()
    if lines:
        out.append(
            Violation(
                "non_egp_journal_line_data",
                f"{lines} non-EGP journal line(s) exist beneath EGP/blank headers; the pilot books EGP only.",
            )
        )

    fx_lines = (
        JournalLine.objects.filter(company=company, currency__in=home)
        .filter(Q(amount_currency__isnull=False) | (Q(exchange_rate__isnull=False) & ~Q(exchange_rate=Decimal("1"))))
        .count()
    )
    if fx_lines:
        out.append(
            Violation(
                "fx_line_residue",
                f"{fx_lines} EGP/blank journal line(s) carry foreign-leg mechanics "
                "(amount_currency or a non-1 exchange rate); EGP-only books convert nothing.",
            )
        )

    fx_headers = (
        JournalEntry.objects.filter(company=company, currency__in=home).exclude(exchange_rate=Decimal("1")).count()
    )
    if fx_headers:
        out.append(
            Violation(
                "fx_header_rate_residue",
                f"{fx_headers} EGP/blank journal header(s) carry a non-1 exchange rate; "
                "EGP-only books convert nothing.",
            )
        )

    return out


def _manual_ar_violations(company) -> list[Violation]:
    """A4: manual accounts-receivable (Capability.MANUAL_AR) is out of scope.
    Detect — read-only, never repaired:

    - ``non_egp_sales_document_data`` — invoice/credit-note rows in a foreign
      currency (any origin; the supported platform limb is EGP-only anyway);
    - ``customer_currency_not_egp`` — Customer master data carrying a foreign
      currency (the model default is 'USD'; a foreign customer currency is the
      leak path into receipt currency resolution);
    - ``manual_ar_document_state`` — manually created (``auto_created=False``)
      invoice/credit-note rows. ROW-based evidence only: the sales lifecycle
      events do not record the manual/platform discriminator, and the deferred
      payload addition is a tracked event-schema decision — a hard-deleted
      manual DRAFT therefore leaves no trace (documented, accepted);
    - ``manual_ar_financial_state`` — manual documents with posted journals,
      durable ``cash.customer_receipt_recorded`` events, and ReceiptAllocation
      rows (receipts have no void path, so any receipt evidence is permanent).
    """
    from accounting.models import Customer
    from events.models import BusinessEvent
    from events.types import EventTypes
    from sales.models import ReceiptAllocation, SalesCreditNote, SalesInvoice

    out: list[Violation] = []
    home = (EGP, "")

    foreign_docs = (
        SalesInvoice.objects.filter(company=company).exclude(currency__in=home).count()
        + SalesCreditNote.objects.filter(company=company).exclude(currency__in=home).count()
    )
    if foreign_docs:
        out.append(
            Violation(
                "non_egp_sales_document_data",
                f"{foreign_docs} non-EGP sales document(s) (invoices / credit notes) exist; the pilot books EGP only.",
            )
        )

    foreign_customers = Customer.objects.filter(company=company).exclude(currency__in=home).count()
    if foreign_customers:
        out.append(
            Violation(
                "customer_currency_not_egp",
                f"{foreign_customers} customer(s) carry a non-EGP currency; customer currency feeds "
                "receipt/document currency resolution and must be EGP (or blank).",
            )
        )

    manual_docs = (
        SalesInvoice.objects.filter(company=company, auto_created=False).count()
        + SalesCreditNote.objects.filter(company=company, auto_created=False).count()
    )
    if manual_docs:
        out.append(
            Violation(
                "manual_ar_document_state",
                f"{manual_docs} manually created sales document(s) (invoices / credit notes) exist; "
                "manual AR is out of scope for the pilot.",
            )
        )

    manual_posted = (
        SalesInvoice.objects.filter(company=company, auto_created=False, posted_journal_entry__isnull=False).count()
        + SalesCreditNote.objects.filter(
            company=company, auto_created=False, posted_journal_entry__isnull=False
        ).count()
    )
    receipt_events = BusinessEvent.objects.filter(
        company=company, event_type=EventTypes.CUSTOMER_RECEIPT_RECORDED
    ).count()
    receipt_allocations = ReceiptAllocation.objects.filter(company=company).count()
    if manual_posted or receipt_events or receipt_allocations:
        parts: list[str] = []
        if manual_posted:
            parts.append(f"{manual_posted} manual sales document(s) with posted journals")
        if receipt_events:
            parts.append(f"{receipt_events} customer-receipt event(s)")
        if receipt_allocations:
            parts.append(f"{receipt_allocations} receipt allocation(s)")
        out.append(
            Violation(
                "manual_ar_financial_state",
                "; ".join(parts) + " exist; manual AR posting and cash application are out of scope for the pilot.",
            )
        )

    return out


def _edim_violations(company) -> list[Violation]:
    """A4: EDIM commit (Capability.EDIM_FINANCIAL_COMMIT) is out of scope.
    Detect the loaded gun, not just fired bullets:

    - ``non_egp_edim_data`` — staged records whose mapped payload carries a
      non-EGP currency (foreign CSV data sitting commit-ready);
    - ``edim_commit_state`` — commit-eligible batches (VALIDATED / PREVIEWED),
      committed batches (durable evidence of EDIM-originated journals), and
      AUTO_POST-armed configuration (a FINANCIAL-trust source paired with an
      ACTIVE AUTO_POST mapping profile).
    """
    from edim.models import IngestionBatch, MappingProfile, SourceSystem, StagedRecord

    out: list[Violation] = []
    home = (EGP, "")

    foreign_staged = 0
    for payload in StagedRecord.objects.filter(company=company).values_list("mapped_payload", flat=True).iterator():
        currency = str((payload or {}).get("currency", "") or "").upper()
        if currency and currency not in home:
            foreign_staged += 1
    if foreign_staged:
        out.append(
            Violation(
                "non_egp_edim_data",
                f"{foreign_staged} EDIM staged record(s) carry a non-EGP currency; the pilot ingests EGP only.",
            )
        )

    commit_eligible = IngestionBatch.objects.filter(
        company=company,
        status__in=(IngestionBatch.Status.VALIDATED, IngestionBatch.Status.PREVIEWED),
    ).count()
    committed = IngestionBatch.objects.filter(company=company, status=IngestionBatch.Status.COMMITTED).count()
    armed_config = 0
    if (
        SourceSystem.objects.filter(company=company, trust_level=SourceSystem.TrustLevel.FINANCIAL).exists()
        and MappingProfile.objects.filter(
            company=company,
            status=MappingProfile.ProfileStatus.ACTIVE,
            posting_policy=MappingProfile.PostingPolicy.AUTO_POST,
        ).exists()
    ):
        armed_config = 1
    if commit_eligible or committed or armed_config:
        parts = []
        if commit_eligible:
            parts.append(f"{commit_eligible} commit-eligible EDIM batch(es) (VALIDATED/PREVIEWED)")
        if committed:
            parts.append(f"{committed} committed EDIM batch(es)")
        if armed_config:
            parts.append("an AUTO_POST-armed source/profile configuration")
        out.append(
            Violation(
                "edim_commit_state",
                "; ".join(parts) + " exist(s); EDIM financial commit is out of scope for the pilot.",
            )
        )

    return out


def _revaluation_violations(company) -> list[Violation]:
    """A4: FX revaluation (Capability.FX_REVALUATION) is out of scope. Only the
    revaluation flows set ``kind=ADJUSTMENT`` (the manual HTTP door cannot pass
    ``kind``), so ADJUSTMENT journals across ALL statuses — the historical
    multi-commit flow could strand INCOMPLETE/DRAFT residue — are revaluation
    evidence on EGP-only books."""
    from accounting.models import JournalEntry

    n = JournalEntry.objects.filter(company=company, kind=JournalEntry.Kind.ADJUSTMENT).count()
    if n:
        return [
            Violation(
                "revaluation_data",
                f"{n} ADJUSTMENT journal entr(ies) exist (currency-revaluation evidence, any "
                "status); EGP-only books have nothing to revalue.",
            )
        ]
    return []


def _exchange_rate_violations(company) -> list[Violation]:
    """A4: exchange-rate maintenance (Capability.EXCHANGE_RATE_MAINTENANCE) is
    out of scope and EGP-only books need ZERO rate rows (``get_rate``
    short-circuits EGP→EGP without touching the table; every stored row is
    cross-currency by construction). ALL rows are residue — this check is
    load-bearing for the deny-as-rate-miss design: the capability deny stops
    the FETCH, but a pre-existing row still resolves."""
    from accounting.models import ExchangeRate

    total = ExchangeRate.objects.filter(company=company).count()
    if total:
        auto = ExchangeRate.objects.filter(company=company, source="ECB (auto-fetched)").count()
        detail = f" ({auto} ECB auto-fetched)" if auto else ""
        return [
            Violation(
                "exchange_rate_data",
                f"{total} exchange-rate row(s) exist{detail}; EGP-only books need no rates and "
                "stored rows still resolve in lookups.",
            )
        ]
    return []


def _period_date_violations(company) -> list[Violation]:
    """A4: fiscal-period DATE/tiling drift the count-only ``_period_violations``
    cannot see. Under the frozen January fiscal year, every NORMAL period must
    exactly tile its calendar month (start = 1st, end = last day of the SAME
    month, period == month ordinal) and each ADJUSTMENT period must sit on
    Dec 31 of its year (start == end == fiscal-year end, mirroring
    ``_calculate_period_boundaries``). Overlapping NORMAL coverage (any two
    rows, any fiscal-year label, covering one date) additionally defends the
    date-range posting lookup. Every CONFIGURED fiscal year must also be
    COMPLETE — periods 1–12 present (and 13 when the config says 13): a
    deleted month is a hole the date-range posting lookup silently falls
    through. Skipped while the start month is not January —
    ``fiscal_not_january`` already fires there and this check's expectations
    assume the frozen structure."""
    import calendar
    from datetime import date as _date

    from projections.models import FiscalPeriod, FiscalPeriodConfig

    if company.fiscal_year_start_month != 1:
        return []

    out: list[Violation] = []
    drifted = 0
    normal_periods: list = []

    for fp in FiscalPeriod.objects.filter(company=company).order_by("fiscal_year", "period").iterator():
        # Adjustment-period expectation keys on period == 13 OR the ADJUSTMENT
        # type: the onboarding path historically created P13 without stamping
        # period_type, so a NORMAL-typed P13 with the correct Dec-31 shape is
        # legitimate configuration, not drift.
        if fp.period == 13 or fp.period_type == FiscalPeriod.PeriodType.ADJUSTMENT:
            fy_end = _date(fp.fiscal_year, 12, 31)
            if fp.start_date != fy_end or fp.end_date != fy_end:
                drifted += 1
            continue
        normal_periods.append(fp)
        if not (1 <= fp.period <= 12):
            drifted += 1
            continue
        year = fp.fiscal_year
        month = fp.period
        expected_start = _date(year, month, 1)
        expected_end = _date(year, month, calendar.monthrange(year, month)[1])
        if fp.start_date != expected_start or fp.end_date != expected_end:
            drifted += 1

    if drifted:
        out.append(
            Violation(
                "period_dates_drift",
                f"{drifted} fiscal period(s) deviate from the frozen January calendar tiling "
                "(NORMAL periods must exactly cover their calendar month; adjustment periods "
                "sit on Dec 31).",
            )
        )

    seen_months: dict[tuple[int, int], int] = {}
    overlaps = 0
    for fp in normal_periods:
        cursor = fp.start_date
        # Walk month-granular coverage; period dates are month-shaped by
        # contract, so month granularity is sufficient once tiling holds and
        # still catches gross overlaps when it does not.
        while cursor <= fp.end_date:
            key = (cursor.year, cursor.month)
            if key in seen_months:
                overlaps += 1
                break
            seen_months[key] = fp.pk
            cursor = _date(cursor.year + (cursor.month // 12), (cursor.month % 12) + 1, 1)
    if overlaps:
        out.append(
            Violation(
                "period_overlap",
                f"{overlaps} NORMAL fiscal period(s) overlap another period's calendar coverage; "
                "the date-range posting lookup requires disjoint periods.",
            )
        )

    # Completeness: every fiscal year the company has CONFIGURED must hold its
    # full period set — the tiling/overlap checks above only see rows that
    # exist, so a deleted month would otherwise pass.
    incomplete: list[str] = []
    for cfg in FiscalPeriodConfig.objects.filter(company=company).order_by("fiscal_year"):
        have = set(
            FiscalPeriod.objects.filter(company=company, fiscal_year=cfg.fiscal_year).values_list("period", flat=True)
        )
        expected = set(range(1, 13)) | ({13} if cfg.period_count == 13 else set())
        missing = sorted(expected - have)
        if missing:
            incomplete.append(f"FY{cfg.fiscal_year} missing period(s) {missing}")
    if incomplete:
        out.append(
            Violation(
                "period_calendar_incomplete",
                "Configured fiscal year(s) are missing periods — the date-range posting "
                "lookup would silently fall through the hole(s): " + "; ".join(incomplete) + ".",
            )
        )

    return out


def _vertical_module_violations(company) -> list[Violation]:
    """A4: the clinic / properties verticals (Capability.VERTICAL_MODULES) are
    out of scope. Detect enablement and any vertical state — rows or durable
    canonical events (explicit constants, never prefix matching), mirroring the
    purchasing precedent."""
    from accounting.mappings import ModuleAccountMapping
    from accounts.models import CompanyModule
    from clinic.models import Doctor, Patient, Visit
    from clinic.models import Invoice as ClinicInvoice
    from clinic.models import Payment as ClinicPayment
    from events.models import BusinessEvent
    from events.types import EventTypes
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

    clinic_event_types = (
        EventTypes.CLINIC_DOCTOR_CREATED,
        EventTypes.CLINIC_PATIENT_CREATED,
        EventTypes.CLINIC_PATIENT_UPDATED,
        EventTypes.CLINIC_VISIT_CREATED,
        EventTypes.CLINIC_VISIT_COMPLETED,
        EventTypes.CLINIC_INVOICE_ISSUED,
        EventTypes.CLINIC_PAYMENT_RECEIVED,
        EventTypes.CLINIC_PAYMENT_VOIDED,
    )
    property_event_types = (
        EventTypes.PROPERTY_CREATED,
        EventTypes.PROPERTY_UPDATED,
        EventTypes.LEASE_CREATED,
        EventTypes.LEASE_UPDATED,
        EventTypes.LEASE_ACTIVATED,
        EventTypes.LEASE_TERMINATED,
        EventTypes.LEASE_RENEWED,
        EventTypes.RENT_SCHEDULE_GENERATED,
        EventTypes.RENT_DUE_POSTED,
        EventTypes.RENT_OVERDUE_DETECTED,
        EventTypes.RENT_LINE_WAIVED,
        EventTypes.RENT_PAYMENT_RECEIVED,
        EventTypes.RENT_PAYMENT_ALLOCATED,
        EventTypes.RENT_PAYMENT_VOIDED,
        EventTypes.DEPOSIT_RECEIVED,
        EventTypes.DEPOSIT_ADJUSTED,
        EventTypes.DEPOSIT_REFUNDED,
        EventTypes.DEPOSIT_FORFEITED,
        EventTypes.LEASE_EXPIRY_ALERT,
        EventTypes.PROPERTY_EXPENSE_RECORDED,
        EventTypes.PROPERTY_ACCOUNT_MAPPING_UPDATED,
    )

    out: list[Violation] = []

    for module_key, code in (("clinic", "clinic_module_enabled"), ("properties", "properties_module_enabled")):
        if CompanyModule.objects.filter(company=company, module_key=module_key, is_enabled=True).exists():
            out.append(
                Violation(
                    code,
                    f"The {module_key} module is enabled; the verticals are out of scope for the pilot.",
                )
            )

    clinic_rows = (
        Patient.objects.filter(company=company).count()
        + Doctor.objects.filter(company=company).count()
        + Visit.objects.filter(company=company).count()
        + ClinicInvoice.objects.filter(company=company).count()
        + ClinicPayment.objects.filter(company=company).count()
        + ModuleAccountMapping.objects.filter(company=company, module="clinic").count()
    )
    clinic_events = BusinessEvent.objects.filter(company=company, event_type__in=clinic_event_types).count()
    if clinic_rows or clinic_events:
        parts = []
        if clinic_rows:
            parts.append(f"{clinic_rows} clinic row(s)")
        if clinic_events:
            parts.append(f"{clinic_events} clinic event(s)")
        out.append(
            Violation(
                "clinic_state",
                "; ".join(parts) + " exist; the clinic vertical is out of scope for the pilot.",
            )
        )

    property_rows = (
        Property.objects.filter(company=company).count()
        + Unit.objects.filter(company=company).count()
        + Lessee.objects.filter(company=company).count()
        + Lease.objects.filter(company=company).count()
        + RentScheduleLine.objects.filter(company=company).count()
        + PaymentReceipt.objects.filter(company=company).count()
        + SecurityDepositTransaction.objects.filter(company=company).count()
        + PropertyExpense.objects.filter(company=company).count()
        + PropertyAccountMapping.objects.filter(company=company).count()
    )
    property_events = BusinessEvent.objects.filter(company=company, event_type__in=property_event_types).count()
    if property_rows or property_events:
        parts = []
        if property_rows:
            parts.append(f"{property_rows} property row(s)")
        if property_events:
            parts.append(f"{property_events} property event(s)")
        out.append(
            Violation(
                "property_state",
                "; ".join(parts) + " exist; the properties vertical is out of scope for the pilot.",
            )
        )

    return out


def _external_ingest_violations(company) -> list[Violation]:
    """A4: no ExternalAPIKey may exist on the pilot deployment — the generic
    ingest endpoint is the one ingress that bypasses ModuleEnabled entirely,
    and its per-key ``allowed_event_types`` allowlist is only as safe as the
    keys that exist. Deployment-wide count (keys for ANY company break the
    isolation posture). Broader ingest-surface hardening remains the tracked
    External-Ingest-Surface-Hardening deferral."""
    from events.api_keys import ExternalAPIKey

    n = ExternalAPIKey.objects.count()
    if n:
        return [
            Violation(
                "external_api_key_present",
                f"{n} external API key(s) exist; the pilot deployment must hold none "
                "(generic ingest bypasses module gating).",
            )
        ]
    return []


def _seeded_event_violations(company) -> list[Violation]:
    """A4: seed/demo tooling stamps its events with ``metadata.source`` — the
    ONLY durable marker distinguishing seeded EGP test-pack orders from real
    merchant data. Any tagged event means this database was used for demo/test
    seeding and must not carry a pilot."""
    from events.models import BusinessEvent

    n = BusinessEvent.objects.filter(company=company, metadata__source__in=("demo_seed", "test_csv_pack")).count()
    if n:
        return [
            Violation(
                "seeded_event_residue",
                f"{n} seeded demo/test event(s) exist (metadata.source demo_seed/test_csv_pack); "
                "seeded databases must not carry a pilot.",
            )
        ]
    return []


def _projection_rebuild_violations(company) -> list[Violation]:
    """A4: a projection rebuild admitted before activation could still be
    draining while the profile flips (the one undetected window of the
    design-deferred rebuild residual). Refuse while any projection reports
    REBUILDING."""
    from projections.models import ProjectionStatus

    rebuilding = list(
        ProjectionStatus.objects.filter(company=company, status=ProjectionStatus.Status.REBUILDING).values_list(
            "projection_name", flat=True
        )
    )
    if rebuilding:
        return [
            Violation(
                "projection_rebuild_in_flight",
                f"Projection rebuild in flight for: {', '.join(sorted(rebuilding))}; wait for the "
                "drain to finish (or resolve its failure) before activation.",
            )
        ]
    return []
