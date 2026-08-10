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
    read-only, never repaired — any purchases-module enablement, purchase
    document, or purchase-originated posting drift. Vendors, VENDOR posting
    profiles, NON_STOCK items and the purchase sequence counters are shared
    master data / harmless counters, NOT purchasing execution state, and are
    intentionally exempt. (Going-forward, the runtime gates on the purchasing
    commands and ``record_vendor_payment`` keep these paths unreachable; this is
    drift detection for anything that predates activation.)"""
    from accounts.models import CompanyModule
    from purchases.models import GoodsReceipt, PurchaseBill, PurchaseCreditNote, PurchaseOrder

    out: list[Violation] = []

    if CompanyModule.objects.filter(company=company, module_key="purchases", is_enabled=True).exists():
        out.append(
            Violation(
                "purchases_module_enabled",
                "The purchases module is enabled; the purchasing/AP workflow is out of scope for the pilot.",
            )
        )

    documents = (
        PurchaseBill.objects.filter(company=company).count()
        + PurchaseOrder.objects.filter(company=company).count()
        + GoodsReceipt.objects.filter(company=company).count()
        + PurchaseCreditNote.objects.filter(company=company).count()
    )
    if documents:
        out.append(
            Violation(
                "purchase_document_state",
                f"{documents} purchase document(s) (bills / orders / goods receipts / credit notes) exist; "
                "the purchasing workflow is out of scope for the pilot.",
            )
        )

    posted = (
        PurchaseBill.objects.filter(company=company, posted_journal_entry__isnull=False).count()
        + PurchaseCreditNote.objects.filter(company=company, posted_journal_entry__isnull=False).count()
    )
    if posted:
        out.append(
            Violation(
                "purchase_financial_state",
                f"{posted} purchase document(s) carry posted journal entries; purchase-originated "
                "AP/expense/input-VAT postings are out of scope for the pilot.",
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
