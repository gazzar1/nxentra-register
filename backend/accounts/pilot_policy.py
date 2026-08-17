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
  - purchasing / accounts-payable: purchase documents (bills, orders, goods
    receipts, credit notes), purchase-originated journals, and the
    vendor-payment / AP-allocation posting workflow — manual journals (incl.
    vendor-tagged lines) remain governed by the ordinary manual-journal rules;
  - currency / fiscal-configuration changes;
  - unsafe automatic bank match / unmatch / rematch;
  - in-app backup RESTORE (total-state overwrite) — export/download stay
    available; G2 recovery is a separate isolated-database drill.

Profile ``NONE`` supports every capability (existing behavior unchanged). An
unrecognized stored profile value fails closed (everything gated is blocked).
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from django.db import transaction
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
    PURCHASING_ACCOUNTING = "purchasing_accounting"
    CURRENCY_FISCAL_CHANGE = "currency_fiscal_change"
    UNSAFE_BANK_MATCH = "unsafe_bank_match"
    # In-app backup RESTORE overwrites the whole company (books, config, event
    # stream) in one transaction and is unsupported while a constrained pilot is
    # active — G2 recovery is an ISOLATED-DATABASE restore drill, not this route.
    # Blocked automatically under every constrained profile via _BLOCKED_BY_PROFILE
    # below; enforced at the canonical boundary backups.importer.restore_company
    # under the Company admission lock (export/download stay available).
    BACKUP_RESTORE = "backup_restore"


# The constrained pilot ingests only the merchant's home currency, so no foreign
# leg — and therefore no FX conversion — is ever booked. Foreign financial inputs
# are rejected at the ingestion boundary, before any event or mutation.
PILOT_CURRENCY = "EGP"
NON_EGP_INGESTION = "non_egp_ingestion"


# Capabilities blocked under each known constrained profile.
_BLOCKED_BY_PROFILE: dict[str, frozenset[str]] = {
    "ISOLATED_SHADOW_LEDGER_V1": frozenset(c.value for c in Capability),
}


# Optional modules whose ``ModuleEnabled`` route permission is capability-aware:
# under a pilot profile that forbids the capability, the permission RAISES
# ``PilotScopeBlocked`` (403) BEFORE the enablement lookup, so a stale
# ``CompanyModule.is_enabled=True`` row cannot reopen the module. Both
# module-enable doors (``CompanyModulesView.put`` + onboarding Step 4) consult
# this map to refuse enabling a blocked module. Modules NOT listed here are
# unaffected (their ``ModuleEnabled`` behavior is unchanged).
MODULE_CAPABILITIES: dict[str, Capability] = {
    "purchases": Capability.PURCHASING_ACCOUNTING,
}

# Every OTHER registered optional module carries an explicit MODULE-ENABLEMENT
# disposition here: it is currently NOT gated at the module-enablement boundary.
# Ungated at module enablement does NOT mean process-certified, supported, or
# safe for pilot use — several of these modules are blocked at deeper runtime
# boundaries, and the process-level posture of the verticals is unresolved
# pending the Pilot Process-Surface Completeness Assessment. This set has NO
# runtime effect: it is consumed only by the module-enablement-disposition
# architecture ratchet, which requires every registered optional module to be
# either mapped in ``MODULE_CAPABILITIES`` or listed here, so a NEW optional
# module cannot silently join this surface without a reviewed decision.
#   - sales / shopify_connector — the pilot's own supported workflow;
#   - inventory — enablement ungated; items are forced NON_STOCK by Option B
#     (``Capability.INVENTORY``) at the item layer, not here;
#   - stripe_connector — module visible; Stripe is blocked at connect / sync /
#     webhook (``Capability.STRIPE``);
#   - bank_connector — legacy; blocked at ``Capability.LEGACY_BANKING`` and
#     surfaced as ``legacy_bank_data`` preflight residue;
#   - clinic / properties — verticals that post their own journals; their
#     posted-JE-emitter posture under the pilot is unresolved pending the
#     read-only assessment tracked for the pre-G1 review, NOT decided here.
MODULES_UNGATED_AT_PILOT_ENABLEMENT: frozenset[str] = frozenset(
    {
        "sales",
        "shopify_connector",
        "inventory",
        "stripe_connector",
        "bank_connector",
        "clinic",
        "properties",
    }
)


def capability_for_module(module_key: str) -> Capability | None:
    """The pilot capability gating ``module_key`` at the module-enablement
    boundary, or ``None`` when the module is not capability-gated there."""
    return MODULE_CAPABILITIES.get(module_key)


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


def lock_company_for_admission(company_pk):
    """The mutation-side Company ADMISSION LOCK — the serialization point
    between capability-gated mutations and pilot activation.

    Contract:

    - MUST be called inside ``transaction.atomic()`` (raises otherwise); the
      caller must keep that transaction open through the COMPLETE mutation
      commit/rollback — the returned row lock is what serializes the mutation
      against ``activate_pilot_profile`` (which takes ``Company`` ``FOR
      UPDATE`` before its preflight and profile write). A lock released before
      the mutation commits is NOT a safety boundary.
    - REFETCHES the exact Company row from the database — never trust a cached
      instance's ``pilot_profile`` for a mutation decision.
    - Lock mode: ``FOR NO KEY UPDATE`` where supported
      (``connection.features.has_select_for_no_key_update`` — PostgreSQL).
      Deliberately NOT full ``FOR UPDATE``: NO KEY UPDATE conflicts with
      activation's explicit ``FOR UPDATE`` (both orderings serialize) and with
      other admission locks (covered mutations serialize per company), while
      staying compatible with the implicit ``FOR KEY SHARE`` taken by every
      company-FK row INSERT (events, documents) — full FOR UPDATE would block
      those and widen the Counter→Company deadlock surface. Falls back to the
      established plain ``select_for_update()`` where the feature is
      unavailable (a no-op on SQLite, where the local battery is
      single-threaded; the PostgreSQL e2e suite carries the concurrency proof).

    Plain ``require_supported()`` / ``is_supported()`` / the ``ModuleEnabled``
    route permission and similar UNLOCKED checks are insufficient as the sole
    authorization for any mutation that can race with activation — they read a
    point-in-time (usually cached) profile with no serialization. Use this
    lock (directly, or via ``requires_capability``) for every such mutation.
    """
    from accounts.models import Company

    conn = transaction.get_connection()
    if not conn.in_atomic_block:
        raise RuntimeError(
            "lock_company_for_admission() must be called inside transaction.atomic() — "
            "the admission lock must live until the mutation's outermost commit/rollback."
        )
    qs = (
        Company.objects.select_for_update(no_key=True)
        if conn.features.has_select_for_no_key_update
        else Company.objects.select_for_update()
    )
    return qs.get(pk=company_pk)


@contextmanager
def serialized_company_admission(company_pk):
    """Serialize a NON-actor mutation against pilot activation on the Company
    ADMISSION LOCK — the context-manager twin of :func:`requires_capability`
    for direct-company / webhook / ingestion / scheduled / CLI paths whose
    first argument is NOT an ``ActorContext``.

    Opens the mutation's OUTERMOST transaction, takes the Company admission lock
    (:func:`lock_company_for_admission` — ``FOR NO KEY UPDATE`` where supported,
    refetched fresh) and YIELDS the locked ``Company``. Unlike
    ``requires_capability`` it makes NO capability decision itself: the caller
    decides on the YIELDED row with the existing point-in-time checks
    (``require_supported`` / ``skip_if_unsupported`` / ``require_pilot_currency``
    / ``skip_pilot_currency`` / ``inventory_forced_non_stock``). Currency gates,
    Option-B non-stock forcing and scheduled-skip *returns* each need a
    different decision and control-flow (raise vs structured-skip return) on the
    locked row, so the choice stays with the caller.

    Contract (inherited from :func:`lock_company_for_admission`):

    - the yielded row is the SOLE authoritative profile source for the mutation
      — never decide on a cached instance;
    - the ``with`` block IS the transaction: perform EVERY authoritative local
      mutation (and any irreversible external effect that must not rest on an
      unlocked profile decision) INSIDE it, so the lock is held to the outermost
      commit/rollback — a lock released before the mutation commits closes
      nothing;
    - FORBIDDEN: release+reacquire, ``refresh_from_db`` without locking, or a
      final pre-commit recheck instead of this Company-first admission;
    - lock order is Company admission -> domain/config rows ->
      ``CompanyEventCounter`` -> ``Account`` rows (decide on the yielded row
      FIRST, before acquiring any of those).

    Reentrant: a nested ``serialized_company_admission`` /
    ``requires_capability`` for the same company re-locks the same row in the
    same transaction (free), so decorated inner commands compose::

        with serialized_company_admission(store.company.pk) as company:
            if skip_pilot_currency(company, currency, task="..."):
                return skipped
            ...  # authoritative mutation, lock held to commit

    A raise inside the ``with`` rolls the outer transaction back with the lock
    still held to that rollback — activation then wins the ordering.
    """
    with transaction.atomic():
        yield lock_company_for_admission(company_pk)


def requires_capability(capability):
    """Reusable SERIALIZED interactive command gate.

    Decorate a command whose FIRST positional argument is the ``ActorContext``.
    The wrapper owns the mutation's OUTERMOST transaction and the Company
    admission lock:

        transaction.atomic()
          -> lock_company_for_admission(actor.company.pk)   (fresh row, locked)
          -> require_supported(locked_company, capability)  (raises 403 when forbidden)
          -> func(dataclasses.replace(actor, company=locked_company), ...)
          -> outer commit/rollback (lock held throughout)

    So exactly one serializable ordering with ``activate_pilot_profile`` can
    occur: either this mutation commits first and activation's preflight sees
    its durable state, or activation commits first and this wrapper reads the
    ACTIVE profile and raises ``PilotScopeBlocked`` with zero side effects. A
    stale ``ActorContext`` resolved before activation cannot bypass the gate —
    the profile is re-read from the locked row, and the wrapped command runs
    with an ActorContext referencing that locked instance (``ActorContext`` is
    frozen, hence ``dataclasses.replace``). Existing inner
    ``@transaction.atomic`` decorators become savepoints; nested decorated
    commands re-lock the same row in the same transaction (free/reentrant).

    Must remain the OUTERMOST decorator. ``NONE``-profile companies keep their
    functional behavior; the covered mutations serialize per company on the
    admission row (the deliberate correctness-first tradeoff).

    ``_pilot_capability`` / ``_pilot_capability_serialized`` are introspectable
    markers so architecture ratchets can prove every command on a gated surface
    carries the SERIALIZED gate.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(actor, *args, **kwargs):
            company = getattr(actor, "company", None)
            if company is None:
                # Company-less actor: preserve the pre-existing gate semantics
                # (profile_of(None) == NONE -> supported); the command itself
                # fails on actor.company access. Nothing to lock.
                require_supported(company, capability)
                return func(actor, *args, **kwargs)
            with transaction.atomic():
                locked_company = lock_company_for_admission(company.pk)
                require_supported(locked_company, capability)
                return func(dataclasses.replace(actor, company=locked_company), *args, **kwargs)

        wrapper._pilot_capability = capability
        wrapper._pilot_capability_serialized = True
        return wrapper

    return decorator


def require_module_enable_allowed(company, modules) -> None:
    """Module-enable-door gate. Given an inbound ``[{"key", "is_enabled"}, ...]``
    payload, validate the COMPLETE payload and RAISE ``PilotScopeBlocked`` for
    the first module whose enablement the company's pilot profile forbids —
    BEFORE the caller performs any module write, so a refusal never leaves a
    partial apply. ENABLING a capability-gated module is refused; DISABLING it
    (``is_enabled`` falsy) and any unmapped module are always allowed;
    ``NONE``-profile companies are never restricted (so ordinary onboarding,
    which runs before pilot activation, is never blocked)."""
    if not is_pilot(company):
        return
    for item in modules or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key", "")
        if not key or not item.get("is_enabled", False):
            continue
        capability = capability_for_module(key)
        if capability is not None:
            require_supported(company, capability)


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


def require_pilot_journal_currency(
    company,
    *,
    header_currency,
    line_currencies=(),
    amount_currency_values=(),
    exchange_rates=(),
    context: str = "",
) -> None:
    """Canonical EGP-only invariant for the MANUAL general-journal process.

    Validates the RESULTING journal state (header + every line, after the
    command's normal currency resolution) for a pilot company:

      - the header currency must be blank/EGP;
      - every line currency must be blank/EGP;
      - no line may represent a foreign leg via ``amount_currency``;
      - no exchange rate other than 1 may be supplied (EGP books convert
        nothing, so a non-1 rate is either a foreign conversion or drift the
        ``fx_*_rate_residue`` preflight codes would immediately flag).

    Blank/absent values keep the established "book at home currency" semantics
    of :func:`require_pilot_currency`; profile ``NONE`` is never restricted and
    unknown constrained profiles fail closed through :func:`is_pilot` /
    ``_BLOCKED_BY_PROFILE`` exactly like every other gate.

    LOCK CONTRACT: this helper takes NO lock itself. It must be called only
    while the manual-journal process boundary owns the Company admission lock
    (``serialized_company_admission``) — the shared journal commands invoke it
    at their currency-resolution points solely on behalf of that boundary.
    """
    if not is_pilot(company):
        return
    label = context or "Manual journal"

    def _blocked(detail: str):
        return PilotScopeBlocked(NON_EGP_INGESTION, profile_of(company), detail)

    header = _norm_currency(header_currency)
    if header and header != PILOT_CURRENCY:
        raise _blocked(
            f"{label}: header currency {header} is not supported; the constrained pilot books {PILOT_CURRENCY} only."
        )
    for line_currency in line_currencies:
        cur = _norm_currency(line_currency)
        if cur and cur != PILOT_CURRENCY:
            raise _blocked(
                f"{label}: line currency {cur} is not supported; the constrained pilot books {PILOT_CURRENCY} only."
            )
    for value in amount_currency_values:
        if value is None or value == "":
            continue
        raise _blocked(
            f"{label}: amount_currency={value} represents a foreign-currency leg; "
            f"the constrained pilot books {PILOT_CURRENCY} only."
        )
    for rate in exchange_rates:
        if rate is None or rate == "":
            continue
        try:
            rate_value = Decimal(str(rate))
        except InvalidOperation:
            raise _blocked(f"{label}: exchange rate {rate!r} is not a valid rate.") from None
        if rate_value != 1:
            raise _blocked(
                f"{label}: exchange rate {rate_value} is not supported; {PILOT_CURRENCY}-only books convert nothing."
            )


def canonical_journal_currency(company, currency):
    """Manual-journal boundary: the canonical currency SPELLING to PERSIST for a
    pilot company, so the value written to journal events MATCHES what
    :func:`require_pilot_journal_currency` validated (that gate compares via
    :func:`_norm_currency`, i.e. ``strip().upper()``, but never rewrites the
    value). A pilot only ever reaches persistence here with blank or an EGP
    spelling, so this collapses e.g. ``"egp"`` / ``" egp "`` to canonical
    ``"EGP"`` and leaves blank as blank.

    Without it a lowercase ``"egp"`` would VALIDATE (normalized) yet PERSIST
    verbatim, then (a) trip the CASE-SENSITIVE post-time FX branch
    (``line_currency != functional_currency`` → a bogus ``egp→EGP`` rate lookup
    that fails the post) and (b) be miscounted by the case-sensitive
    ``non_egp_*`` / ``fx_*`` residue preflight — both consumers compare against
    an uppercase-``EGP`` company config. Profile ``NONE`` is returned UNCHANGED
    (no EGP invariant, and currency casing is not this boundary's concern);
    already-canonical values are returned unchanged.
    """
    if not is_pilot(company):
        return currency
    return _norm_currency(currency)


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
