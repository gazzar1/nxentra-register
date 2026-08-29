# accounting/pilot_adjustments.py
"""A5-PR4a: the pilot-adjustment traceability contract for manual journals.

Under ``ISOLATED_SHADOW_LEDGER_V1`` every manual journal POSTED after
activation is a **pilot adjustment**: a supervised correction that must name
the source item it corrects. This module owns the whole contract in one
place (constitution Rule 3 — one canonical implementation):

- the closed source-kind vocabulary and its canonical ``<kind>:<id>``
  encoding into the existing ``JournalEntry.source_document`` field
  (``source_module`` carries the server-stamped ``"pilot_adjustment"``
  discriminator — the manual HTTP surface can never write either raw field);
- the resolver registry: core kinds register here, provider kinds register
  from their adapter's ``AppConfig.ready()`` (the same dependency inversion
  as ``accounting.import_rejects.register_known_order_lookup`` — core never
  imports provider models);
- the reason contract (the existing ``memo`` is the V1 reason carrier:
  stripped length 10–180 so the reversal-memo composition can never overflow
  the 255-char column);
- the POST-time validator called from the shared post command's
  ``_MANUAL_JOURNAL_PROCESS`` sentinel branch, under the manual wrapper's
  Company admission lock — a refusal RAISES so nothing survives (no entry
  number, no event, no sequence; the draft stays intact), exactly the
  ``require_pilot_journal_currency`` semantics;
- the reversal contract: a manual pilot reversal needs its own reason and
  inherits the original's source reference (or supplies a new one when the
  original predates activation).

Deliberately NOT here: no journal payload validation (A3 owns that), no
capability decision (A4 owns those), no writes of any kind — every helper is
a pure read + raise.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from rest_framework.exceptions import APIException

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# The server-stamped discriminator. Deliberately NOT JournalEntry.Kind.
# ADJUSTMENT: the manual door never passes ``kind`` (every manual JE is
# NORMAL) and the ``revaluation_data`` preflight counts ALL ADJUSTMENT-kind
# journals as FX-revaluation residue on that exact premise.
PILOT_ADJUSTMENT_SOURCE_MODULE = "pilot_adjustment"

# Reason bounds (settled founder decision): trimmed 10–180 characters. The
# 180 cap keeps the bounded reversal-memo composition ("<reason> — Reverses
# JE-######") comfortably inside the memo CharField(255).
ADJUSTMENT_REASON_MIN = 10
ADJUSTMENT_REASON_MAX = 180

# JournalEntry.source_document is CharField(max_length=100); every canonical
# encoding must fit.
SOURCE_DOCUMENT_MAX_LENGTH = 100

# Source kinds whose referent may legitimately disappear later (sanctioned
# statement delete/re-import; the Shopify store-PENDING sweep cascade). The
# referent must exist when the adjustment POSTS; preflight must never
# manufacture a violation solely because one of these rows was later
# deleted. All other kinds are strict: a now-missing referent IS drift.
DANGLING_TOLERANT_SOURCE_KINDS = frozenset({"shopify_order", "shopify_refund", "bank_line"})

# Stable machine codes (spec D.5): one shared code for nonexistent AND
# cross-company references so the API never reveals another company's data.
CODE_REQUIRED = "pilot_adjustment_required"
CODE_INVALID_SOURCE = "pilot_adjustment_invalid_source"
CODE_INVALID_REASON = "pilot_adjustment_invalid_reason"
CODE_REVERSAL_REASON = "pilot_adjustment_reversal_reason_required"


class PilotAdjustmentInvalid(APIException):
    """Raised (never returned) so a refusal escapes the shared command's
    atomic block AND the manual wrapper's admission transaction — the proven
    zero-residue shape of the EGP gate: no posted event, no CompanySequence
    increment, no CompanyEventCounter consumption, draft preserved. Renders
    as a stable HTTP 400 with a machine ``code``."""

    status_code = 400
    default_code = "pilot_adjustment_invalid"

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail, code=code)


# kind -> resolver(company, reference_body) -> bool. A resolver is a PURE
# company-scoped existence read: True iff the referent exists AND belongs to
# ``company``. It must never write, never mutate the referent, and must not
# distinguish nonexistent from cross-company (the caller reports both with
# one public code).
_SOURCE_RESOLVERS: dict[str, Callable] = {}

# kind -> owner_probe(reference_body) -> company_id | None. OPTIONAL, used
# only by read-only drift tooling (preflight) to report a company mismatch
# when it is deterministically observable (globally-unique identifiers).
# Never consulted on the write path.
_SOURCE_OWNER_PROBES: dict[str, Callable] = {}


def register_adjustment_source_resolver(kind: str, resolver: Callable, *, owner_probe: Callable | None = None) -> None:
    """Register the resolver for one source kind.

    Same contract as the apply-validator and health-counter registries:
    re-registering the IDENTICAL callable is an idempotent no-op (app-ready
    may run more than once in test harnesses); a DIFFERENT callable for an
    already-claimed kind raises — silently replacing a source resolver would
    be a forgery door.
    """
    existing = _SOURCE_RESOLVERS.get(kind)
    if existing is not None and existing is not resolver:
        raise RuntimeError(
            f"Pilot-adjustment source resolver for kind '{kind}' is already registered "
            f"({existing.__module__}.{existing.__qualname__}); refusing to replace it with "
            f"{resolver.__module__}.{resolver.__qualname__}."
        )
    _SOURCE_RESOLVERS[kind] = resolver
    if owner_probe is not None:
        existing_probe = _SOURCE_OWNER_PROBES.get(kind)
        if existing_probe is not None and existing_probe is not owner_probe:
            raise RuntimeError(f"Pilot-adjustment owner probe for kind '{kind}' is already registered.")
        _SOURCE_OWNER_PROBES[kind] = owner_probe


def registered_adjustment_source_kinds() -> frozenset[str]:
    """The closed vocabulary (for the architecture ratchet)."""
    return frozenset(_SOURCE_RESOLVERS)


def registered_adjustment_source_resolvers() -> dict[str, Callable]:
    """Identity view for the architecture ratchet (module+qualname pinning)."""
    return dict(_SOURCE_RESOLVERS)


def source_owner_probe(kind: str) -> Callable | None:
    """The optional drift-only owner probe for a kind (preflight use)."""
    return _SOURCE_OWNER_PROBES.get(kind)


def parse_source_reference(source_document: str) -> tuple[str, str] | None:
    """Split a canonical ``<kind>:<body>`` reference. Returns None when the
    string is not even grammatically a reference (no colon, blank half,
    unregistered kind, over-length)."""
    if not source_document or len(source_document) > SOURCE_DOCUMENT_MAX_LENGTH:
        return None
    kind, sep, body = source_document.partition(":")
    if not sep or not kind or not body:
        return None
    if kind not in _SOURCE_RESOLVERS:
        return None
    return kind, body


def encode_source_reference(kind: str, body: str) -> str:
    """Canonical encoding, length-checked against the column."""
    encoded = f"{kind}:{body}"
    if len(encoded) > SOURCE_DOCUMENT_MAX_LENGTH:
        raise PilotAdjustmentInvalid(
            CODE_INVALID_SOURCE,
            f"Source reference is too long ({len(encoded)} > {SOURCE_DOCUMENT_MAX_LENGTH} characters).",
        )
    return encoded


def resolve_source_reference(company, source_document: str) -> bool:
    """True iff ``source_document`` parses AND its referent exists for
    ``company``. Nonexistent and cross-company answer identically."""
    parsed = parse_source_reference(source_document)
    if parsed is None:
        return False
    kind, body = parsed
    return bool(_SOURCE_RESOLVERS[kind](company, body))


def _invalid_source() -> PilotAdjustmentInvalid:
    # ONE public message for malformed, unknown-kind, nonexistent and
    # cross-company references — no information leak about other tenants.
    return PilotAdjustmentInvalid(
        CODE_INVALID_SOURCE,
        "Invalid pilot-adjustment source reference: the source item does not exist for this company.",
    )


def validate_manual_source_stamp(company, source_module: str, source_document: str) -> None:
    """Draft-door validation (create/update, under the manual sentinel):
    the manual surface may stamp ONLY the pilot-adjustment discriminator,
    and a supplied reference must resolve same-company. Raises; emits
    nothing. Runs for every profile — the reference must be real whenever
    it is stored; only the post-time REQUIREMENT is pilot-scoped."""
    if not source_module and not source_document:
        return  # draft without a source is allowed
    if source_module != PILOT_ADJUSTMENT_SOURCE_MODULE:
        # The manual door must never stamp (or clear into) a system module
        # value — "payment_settlement" etc. are load-bearing recon joins.
        raise PilotAdjustmentInvalid(
            CODE_INVALID_SOURCE,
            "Manual journals may only carry the pilot_adjustment source stamp.",
        )
    if not resolve_source_reference(company, source_document):
        raise _invalid_source()


def _validate_reason(memo: str, *, code: str, what: str) -> None:
    stripped = (memo or "").strip()
    if not (ADJUSTMENT_REASON_MIN <= len(stripped) <= ADJUSTMENT_REASON_MAX):
        raise PilotAdjustmentInvalid(
            code,
            f"{what} must be {ADJUSTMENT_REASON_MIN}-{ADJUSTMENT_REASON_MAX} characters "
            f"(got {len(stripped)} after trimming).",
        )


def require_pilot_adjustment_traceability(company, *, source_module: str, source_document: str, memo: str) -> None:
    """THE post-time gate (A5-PR4a). Called by the shared post command inside
    its ``_MANUAL_JOURNAL_PROCESS`` sentinel branch, under the manual
    wrapper's Company admission lock, BEFORE the entry-number mint and every
    emit. No-op for profile NONE. Takes NO lock itself.

    Under an active pilot the posting refuses unless the entry carries the
    server-stamped discriminator, a same-company-resolvable canonical source
    reference (re-resolved HERE, at post time), and a qualifying reason in
    the memo. The raise unwinds the whole admission transaction — zero
    residue, draft intact (the EGP-gate-proven shape)."""
    from accounts.pilot_policy import is_pilot

    if not is_pilot(company):
        return
    if source_module != PILOT_ADJUSTMENT_SOURCE_MODULE or not source_document:
        raise PilotAdjustmentInvalid(
            CODE_REQUIRED,
            "Under the constrained pilot every manual journal is a supervised pilot "
            "adjustment: choose a source item (the evidence this entry corrects) "
            "before posting.",
        )
    if not resolve_source_reference(company, source_document):
        raise _invalid_source()
    _validate_reason(memo, code=CODE_INVALID_REASON, what="The adjustment reason (memo)")


def require_pilot_reversal_traceability(
    company,
    *,
    original_source_module: str,
    original_source_document: str,
    reversal_reason: str,
    new_source_kind: str = "",
    new_source_reference: str = "",
) -> tuple[str, str]:
    """The manual-reversal gate. Returns the (source_module, source_document)
    pair to stamp on the reversal's posted payload.

    Off-pilot: pure pass-through of the original pair (the provenance echo
    applies to every reversal). Under an active pilot a manual reversal
    requires its own 10–180-char reason; when the original already carries
    pilot-adjustment provenance the reference is INHERITED without requiring
    the referent to still exist (the posted trace outlives its referent);
    when the original predates activation or lacks provenance, a new typed
    source is required and validated."""
    from accounts.pilot_policy import is_pilot

    if not is_pilot(company):
        return original_source_module or "", original_source_document or ""

    _validate_reason(reversal_reason, code=CODE_REVERSAL_REASON, what="The reversal reason")

    if original_source_module == PILOT_ADJUSTMENT_SOURCE_MODULE and original_source_document:
        # A reversal is a reversal of the original adjustment, not a new
        # unrelated correction — inherit, never re-resolve.
        return original_source_module, original_source_document

    if not new_source_kind or not new_source_reference:
        raise PilotAdjustmentInvalid(
            CODE_REQUIRED,
            "Reversing an entry without pilot-adjustment provenance requires a "
            "source item reference for the reversal itself.",
        )
    encoded = encode_source_reference(new_source_kind, new_source_reference)
    if not resolve_source_reference(company, encoded):
        raise _invalid_source()
    return PILOT_ADJUSTMENT_SOURCE_MODULE, encoded


# ---------------------------------------------------------------------------
# Core source resolvers (provider kinds register from their adapter's
# AppConfig.ready — see shopify_connector/pilot_adjustment_sources.py).
# ---------------------------------------------------------------------------


def _parse_uuid(body: str):
    try:
        return _uuid.UUID(body)
    except (ValueError, AttributeError, TypeError):
        return None


def _resolve_projection_failure(company, body: str) -> bool:
    """``projection_failure:<event_uuid>`` — anchored on the immutable
    BusinessEvent (restore keeps its UUID verbatim; the failure row's integer
    pk does not survive restore), qualified by at least one ProjectionFailureLog
    for that company/event so an arbitrary event cannot masquerade as a
    failure reference."""
    event_uuid = _parse_uuid(body)
    if event_uuid is None:
        return False
    from events.models import BusinessEvent
    from projections.models import ProjectionFailureLog

    if not BusinessEvent.objects.filter(id=event_uuid, company=company).exists():
        return False
    return ProjectionFailureLog.objects.filter(company=company, event_id=event_uuid).exists()


def _probe_projection_failure(body: str):
    event_uuid = _parse_uuid(body)
    if event_uuid is None:
        return None
    from events.models import BusinessEvent

    return BusinessEvent.objects.filter(id=event_uuid).values_list("company_id", flat=True).first()


def _resolve_import_reject(company, body: str) -> bool:
    """``import_reject:<public_uuid>`` — REJECTED and QUARANTINED rows are
    both eligible; resolution state is irrelevant (an acked reject stays a
    real source item)."""
    row_uuid = _parse_uuid(body)
    if row_uuid is None:
        return False
    from accounting.models import ImportRejectedRow

    return ImportRejectedRow.objects.filter(company=company, public_id=row_uuid).exists()


def _probe_import_reject(body: str):
    row_uuid = _parse_uuid(body)
    if row_uuid is None:
        return None
    from accounting.models import ImportRejectedRow

    return ImportRejectedRow.objects.filter(public_id=row_uuid).values_list("company_id", flat=True).first()


def _resolve_settlement_event(company, body: str) -> bool:
    """``settlement_event:<event_uuid>`` — must be the canonical
    payment-settlement-received event for this company; arbitrary events are
    not eligible."""
    event_uuid = _parse_uuid(body)
    if event_uuid is None:
        return False
    from events.models import BusinessEvent
    from events.types import EventTypes

    return BusinessEvent.objects.filter(
        id=event_uuid,
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    ).exists()


def _probe_settlement_event(body: str):
    event_uuid = _parse_uuid(body)
    if event_uuid is None:
        return None
    from events.models import BusinessEvent
    from events.types import EventTypes

    return (
        BusinessEvent.objects.filter(id=event_uuid, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
        .values_list("company_id", flat=True)
        .first()
    )


def _resolve_bank_line(company, body: str) -> bool:
    """``bank_line:<public_uuid>`` — must exist at post time; the kind is
    dangling-tolerant afterwards (statement delete/re-import is sanctioned)."""
    line_uuid = _parse_uuid(body)
    if line_uuid is None:
        return False
    from accounting.models import BankStatementLine

    return BankStatementLine.objects.filter(company=company, public_id=line_uuid).exists()


def _probe_bank_line(body: str):
    line_uuid = _parse_uuid(body)
    if line_uuid is None:
        return None
    from accounting.models import BankStatementLine

    return BankStatementLine.objects.filter(public_id=line_uuid).values_list("company_id", flat=True).first()


register_adjustment_source_resolver(
    "projection_failure", _resolve_projection_failure, owner_probe=_probe_projection_failure
)
register_adjustment_source_resolver("import_reject", _resolve_import_reject, owner_probe=_probe_import_reject)
register_adjustment_source_resolver("settlement_event", _resolve_settlement_event, owner_probe=_probe_settlement_event)
register_adjustment_source_resolver("bank_line", _resolve_bank_line, owner_probe=_probe_bank_line)
