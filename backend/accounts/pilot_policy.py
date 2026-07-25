"""A4: constrained-pilot capability policy — the runtime safety boundary.

One central policy governs which product capabilities may execute for a company
under a constrained-pilot profile. Runtime gates at the deepest shared mutation
boundaries call ``require_supported()`` (interactive) or ``skip_if_unsupported()``
(scheduled). Those runtime gates ARE the safety guarantee. The ``pilot_preflight``
command is an exhaustive point-in-time proof + drift detector, never a substitute
for these gates.

``ISOLATED_SHADOW_LEDGER_V1`` (see ``Company.PilotProfile``) blocks:

  - Stripe (connect / sync / webhook-driven accounting / commands / tasks);
  - Shopify Payments payout accounting (payout settlement events + JEs, incl. the
    ``abs()`` negative-payout branch) — Shopify order/refund accounting remains;
  - the legacy ``/banking`` module;
  - projection rebuild / replay (every entry point);
  - adding members / invitations / a second merchant company;
  - inventory: ``INVENTORY`` items and inventory/COGS account mappings (Option B);
  - currency / fiscal-configuration changes;
  - unsafe automatic bank match / unmatch / rematch.

Profile ``NONE`` supports every capability (existing behavior unchanged). An
unrecognized stored profile value fails closed (everything gated is blocked).
"""

from __future__ import annotations

import logging
from enum import StrEnum

from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

SKIPPED_PILOT_SCOPE = "skipped_pilot_scope"


class Capability(StrEnum):
    STRIPE = "stripe"
    SHOPIFY_PAYOUT_ACCOUNTING = "shopify_payout_accounting"
    SHOPIFY_DISPUTES = "shopify_disputes"
    LEGACY_BANKING = "legacy_banking"
    PROJECTION_REBUILD = "projection_rebuild"
    ADD_MEMBER = "add_member"
    CREATE_COMPANY = "create_company"
    INVENTORY = "inventory"
    CURRENCY_FISCAL_CHANGE = "currency_fiscal_change"
    UNSAFE_BANK_MATCH = "unsafe_bank_match"


# The constrained pilot ingests only the merchant's home currency, so no foreign
# leg — and therefore no FX conversion — is ever booked. Foreign financial inputs
# are rejected at the ingestion boundary, before any event or mutation.
PILOT_CURRENCY = "EGP"
NON_EGP_INGESTION = "non_egp_ingestion"


# Capabilities blocked under each known constrained profile.
_BLOCKED_BY_PROFILE: dict[str, frozenset[str]] = {
    "ISOLATED_SHADOW_LEDGER_V1": frozenset(c.value for c in Capability),
}


class PilotScopeBlocked(APIException):
    """Raised when an interactive request attempts a capability the company's
    constrained-pilot profile forbids. A DRF ``APIException`` so any gated
    command renders a stable HTTP 403 pilot-scope error without per-view
    handling; also carries the machine ``code`` and ``capability`` for callers
    that inspect it directly (e.g. management commands, tests)."""

    status_code = 403
    default_code = "pilot_scope_blocked"
    code = "pilot_scope_blocked"

    def __init__(self, capability: str, profile: str, detail: str = ""):
        self.capability = capability
        self.profile = profile
        super().__init__(
            detail or f"Capability '{capability}' is not available under pilot profile '{profile}'.",
            code="pilot_scope_blocked",
        )


def _cap_value(capability) -> str:
    return capability.value if isinstance(capability, Capability) else str(capability)


def profile_of(company) -> str:
    from accounts.models import Company

    return str(getattr(company, "pilot_profile", None) or Company.PilotProfile.NONE)


def is_pilot(company) -> bool:
    """True when any constrained-pilot profile (not NONE) is active."""
    from accounts.models import Company

    return profile_of(company) != Company.PilotProfile.NONE


def deployment_has_pilot() -> bool:
    """True when ANY company in this deployment (active OR inactive) is on a
    constrained-pilot profile. Enforces one-merchant-per-deployment: a second
    company is blocked once a pilot exists, and a deactivated pilot row still
    blocks it (deactivation cannot bypass the isolated-deployment contract)."""
    from accounts.models import Company
    from accounts.rls import rls_bypass

    with rls_bypass():
        return Company.objects.exclude(pilot_profile=Company.PilotProfile.NONE).exists()


def is_supported(company, capability) -> bool:
    """Whether ``capability`` may execute for ``company``.

    ``NONE`` → everything supported. A known constrained profile → blocked iff
    the capability is in its block set. An unrecognized profile → fail closed.
    """
    from accounts.models import Company

    profile = profile_of(company)
    if profile == Company.PilotProfile.NONE:
        return True
    blocked = _BLOCKED_BY_PROFILE.get(profile)
    if blocked is None:
        logger.error(
            "pilot_policy.unknown_profile company=%s profile=%r — failing closed",
            getattr(company, "id", None),
            profile,
        )
        return False
    return _cap_value(capability) not in blocked


def require_supported(company, capability) -> None:
    """Interactive gate. Raise ``PilotScopeBlocked`` when the capability is
    forbidden; do nothing when supported."""
    if not is_supported(company, capability):
        raise PilotScopeBlocked(_cap_value(capability), profile_of(company))


def inventory_forced_non_stock(company) -> bool:
    """Option B: True when the company's profile forbids inventory, so every item
    must be created NON_STOCK with no inventory/COGS accounts. Item creation paths
    call this rather than raising — the pilot keeps working, just without stock."""
    return not is_supported(company, Capability.INVENTORY)


def skip_if_unsupported(company, capability, *, task: str = "") -> dict | None:
    """Scheduled-task gate.

    Returns a structured ``SKIPPED_PILOT_SCOPE`` dict when the capability is
    forbidden — the caller returns it, performs NO mutation and does NOT retry —
    or ``None`` when supported. Emits a structured log so a blocked capability is
    never silently reported as processed.
    """
    if is_supported(company, capability):
        return None
    cap = _cap_value(capability)
    logger.info(
        "pilot_scope_skipped task=%s company=%s capability=%s",
        task,
        getattr(company, "id", None),
        cap,
    )
    return {"status": SKIPPED_PILOT_SCOPE, "capability": cap, "company_id": getattr(company, "id", None)}


def _norm_currency(currency) -> str:
    return (currency or "").strip().upper()


def require_pilot_currency(company, currency, *, context: str = "") -> None:
    """Interactive ingestion gate. Reject a foreign-currency financial input for a
    pilot company BEFORE any event/mutation so no FX conversion is ever needed.

    An empty/absent currency means "book at the company's home currency" and is
    accepted (the home currency IS ``EGP`` under the pilot). ``NONE`` profile
    companies are never restricted.
    """
    if not is_pilot(company):
        return
    cur = _norm_currency(currency)
    if cur and cur != PILOT_CURRENCY:
        raise PilotScopeBlocked(
            NON_EGP_INGESTION,
            profile_of(company),
            f"{context or 'Financial input'} currency {cur} is not supported; the "
            f"constrained pilot ingests {PILOT_CURRENCY} only.",
        )


def skip_pilot_currency(company, currency, *, task: str = "") -> dict | None:
    """Scheduled/background variant of :func:`require_pilot_currency`: returns the
    structured ``SKIPPED_PILOT_SCOPE`` dict (no mutation, no retry) instead of
    raising, or ``None`` when the input is in-scope."""
    if not is_pilot(company):
        return None
    cur = _norm_currency(currency)
    if not cur or cur == PILOT_CURRENCY:
        return None
    logger.info(
        "pilot_currency_skipped task=%s company=%s currency=%s",
        task,
        getattr(company, "id", None),
        cur,
    )
    return {
        "status": SKIPPED_PILOT_SCOPE,
        "capability": NON_EGP_INGESTION,
        "company_id": getattr(company, "id", None),
        "currency": cur,
    }
