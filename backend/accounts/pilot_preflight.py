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

        # --- isolation: exactly one active merchant company ---------------
        active_companies = Company.objects.filter(is_active=True).count()
        if active_companies != 1:
            v.append(
                Violation(
                    "not_isolated",
                    f"Deployment has {active_companies} active companies; the isolated "
                    "shadow-ledger contract permits exactly one.",
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
        pending_invites = Invitation.objects.filter(company=company, status=Invitation.Status.PENDING).count()
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

        # --- capability gates must be ON (config proof) -------------------
        v += _capability_gate_violations(company)

        # --- forbidden data state -----------------------------------------
        v += _stripe_state_violations(company)
        v += _inventory_state_violations(company)

    return v


def _period_violations(company) -> list[Violation]:
    from projections.models import FiscalPeriodConfig

    cfg = FiscalPeriodConfig.objects.filter(company=company).order_by("-fiscal_year").first()
    if cfg is None:
        return [Violation("no_period_config", "No fiscal-period configuration exists.")]
    if cfg.period_count != 13:
        return [
            Violation(
                "period_structure",
                f"Unsupported period structure (period_count={cfg.period_count}; "
                "expected 13 = 12 normal + 1 adjustment).",
            )
        ]
    return []


def _shopify_store_violations(company, phase: str) -> list[Violation]:
    from shopify_connector.models import ShopifyStore

    active = ShopifyStore.objects.filter(company=company, status=ShopifyStore.Status.ACTIVE).count()
    if phase == "go-live":
        if active != 1:
            return [Violation("store_count", f"go-live requires exactly one active Shopify store (found {active}).")]
    else:  # setup
        if active > 1:
            return [Violation("store_count", f"At most one active Shopify store is permitted (found {active}).")]
    return []


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

    cogs_pending = ShopifyFulfillment.objects.filter(
        store__company=company, status=ShopifyFulfillment.Status.COGS_PENDING
    ).count()
    if cogs_pending:
        out.append(
            Violation("cogs_pending", f"{cogs_pending} COGS_PENDING fulfillment(s) exist (deferred-COGS residue).")
        )

    return out
