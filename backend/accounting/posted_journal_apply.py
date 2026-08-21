# accounting/posted_journal_apply.py
"""A3-PR3: THE apply/replay boundary for the posted-journal invariant.

The emit side (``posted_journal_boundary.emit_posted_journal``) guarantees no
NEW ``journal_entry.posted`` event can enter the store invalid. This module
is the other half of A3: no stored journal event — historical, rebuilt,
replayed, or imported — materializes into a read model without passing the
SAME canonical invariant (``journal_invariant.check_posted_journal``,
``mode="apply"``), through the framework choke point
``projections.apply_validation`` that ``BaseProjection.process_pending``
calls for every event before its handler runs.

Disposition (founder decisions, 2026-08-21): universal strict. EVERY apply
violation is terminal — the event is QUARANTINED via
``ProjectionTerminalSkip`` (operator-visible ``ProjectionFailureLog``, event
consumed, bookmark advances, stream flows; re-application requires deliberate
repair + rebuild). No warn-and-log cutover state, no tolerance, no settings
flag. Because apply-mode checks are a strict subset of emit-mode checks
(apply amount parsing is the WEAKER quantized interpretation, and the
emit-only policy codes are excluded), an event emitted through the A3-PR2
boundary can never fail here — quarantine fires only for pre-enforcement
history, foreign streams, or payloads corrupted at rest — PROVIDED the
account facts apply consults stay what they were at emit. That precondition
is enforced, not assumed: the two memo-classification inputs are immutable
once an account carries posted history (``ledger_domain`` is outside the
frozen ACCOUNT_UPDATE_ALLOWED_FIELDS set; ``account_type`` is refused by
``can_change_account_type`` on durable posted-EVENT evidence — a payload scan
of the stored stream, since projection rows/markers cannot prove absence —
A3-PR3),
account rows soft-delete (existence is monotone), and read-model LAG on
``JE_ACCOUNT_UNKNOWN`` defers instead of quarantining (see
``validate_posted_journal_apply``). Residual: direct DB surgery outside the
command layer can still drift facts — operator-trust, same class as any raw
write.

Beyond ``journal_entry.posted``, the journal family's sibling doors into the
same read models carry boundary-local guards (founder decision D5):

- ``journal_entry.reversed`` — the payload's entry references must be
  well-formed UUIDs (a malformed payload previously KeyError-halted the
  whole projection stream);
- ``journal_entry.deleted`` — deleting a POSTED/REVERSED entry from the read
  model is refused (posted financial history must never vanish from the read
  model while the event stream still asserts it; the delete command only
  ever emits for INCOMPLETE/DRAFT entries);
- ``journal.lines_chunk_added`` — quarantined unconditionally: a single
  chunk cannot satisfy the entry-level invariant by construction, the
  chunked emit family is dormant (zero production emitters; A171 tracks its
  removal), and per-chunk balance application was a validation-free parallel
  door into the balance projections.

Apply-boundary outcome codes (``APPLY_*``) are deliberately DISJOINT from
the frozen canonical ``JE_*`` set: they label boundary outcomes (unreadable
payload, sibling-guard refusals), not payload-invariant violations.
``APPLY_UNREADABLE_PAYLOAD`` is the runtime twin of the corpus scanner's
``SCANNER_UNREADABLE_PAYLOAD``: same condition (payload-integrity failure /
non-dict payload), caught at apply instead of audit.

Import-light and provider-neutral, like the invariant module: no connector
or vertical imports; ORM access is limited to ``load_account_facts`` and the
single read-model status probe the delete guard needs.

Governance: docs/architecture/architecture-constitution.md Rule 3;
docs/architecture/canonical-money-spine.md §7 (A3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from projections.exceptions import ProjectionTerminalSkip

if TYPE_CHECKING:
    from events.models import BusinessEvent

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Apply-boundary outcome codes (NOT part of JE_VIOLATION_CODES — see docstring)
# --------------------------------------------------------------------------- #

APPLY_UNREADABLE_PAYLOAD = "APPLY_UNREADABLE_PAYLOAD"
APPLY_ENTRY_REF_INVALID = "APPLY_ENTRY_REF_INVALID"
APPLY_DELETE_TARGET_POSTED = "APPLY_DELETE_TARGET_POSTED"
APPLY_CHUNKED_JOURNAL_UNSUPPORTED = "APPLY_CHUNKED_JOURNAL_UNSUPPORTED"

_REPAIR_HINT = (
    "The event is quarantined, not lost: repair the cause (event payload at "
    "source, account state, or exclude the event by deliberate decision), "
    "then rebuild the affected projections to re-apply it."
)


class PostedJournalApplyInvalid(ProjectionTerminalSkip):
    """Quarantine signal carrying the structured violation codes.

    A ``ProjectionTerminalSkip`` subclass, so the framework's existing
    quarantine machinery (ProjectionFailureLog + advance) handles it
    unchanged. ``str(exc)`` contains only stable codes — no amounts, memos,
    or account identifiers — so it is safe to log and to surface verbatim
    in the failure log.
    """

    def __init__(self, event_type: str, codes: list[str]) -> None:
        self.codes = list(codes)
        unique_codes = ", ".join(dict.fromkeys(self.codes))
        super().__init__(
            f"{event_type} event failed the canonical apply invariant: {unique_codes}",
            fix_hint=_REPAIR_HINT,
        )


# JournalLine.line_no is a PositiveIntegerField on a 32-bit integer column;
# a payload line_no outside this range cannot materialize (D4 makes payload
# line_no the line-identity input, so storability is an identity precondition).
MAX_STORABLE_LINE_NO = 2**31 - 1


# --------------------------------------------------------------------------- #
# The one evaluator (shared by the apply hook, restore verification, and the
# event-first audit — the corpus scanner keeps its own identical loop because
# it reports scanner-level outcomes, but both call the SAME pure invariant).
# --------------------------------------------------------------------------- #


def evaluate_posted_journal_for_apply(event: BusinessEvent, facts_cache: dict | None = None) -> list[str]:
    """Evaluate one stored JOURNAL_ENTRY_POSTED event exactly as apply-time
    enforcement does — the COMPLETE apply verdict, so every consumer (the
    choke-point validator, restore verification, the event-first audit)
    shares one identical outcome. Returns sorted unique codes ([] == clean):

    - the canonical ``JE_*`` codes from ``check_posted_journal(mode="apply")``;
    - ``APPLY_UNREADABLE_PAYLOAD`` when the payload cannot be evaluated at
      all (hash mismatch, missing external payload, non-dict payload);
    - ``APPLY_ENTRY_REF_INVALID`` for the identity guards: an
      ``entry_public_id`` that is not a well-formed UUID (the entry cannot
      materialize — the pre-PR3 KeyError-head-of-line class), or an integer
      line ``line_no`` outside the storable JournalLine range (D4 makes
      payload line numbers the line-identity input; an unstorable value
      would IntegrityError-halt the stream instead of quarantining; non-int
      line_no values are historical metadata the invariant tolerates and the
      applier renumbers sequentially).

    ``facts_cache`` (canonical account id -> AccountFacts | None) lets batch
    callers (restore verification, audits) share account lookups across
    events; the per-event apply hook passes none. Read-only: no writes, ever.
    """
    from django.db import IntegrityError

    from accounting.journal_invariant import canonical_account_id, check_posted_journal, load_account_facts

    try:
        data = event.get_data()
    except (IntegrityError, ValueError):
        # The documented get_data() failure contract — payload-integrity
        # failure (missing external payload, hash mismatch) or chunk-assembly
        # misuse: at-rest corruption, the runtime twin of the corpus
        # scanner's SCANNER_UNREADABLE_PAYLOAD. Anything ELSE (e.g. a
        # transient OperationalError on the lazy external-payload fetch)
        # deliberately propagates as an ordinary retryable halt — a
        # transient database error must never terminally consume a valid
        # financial event.
        return [APPLY_UNREADABLE_PAYLOAD]
    if not isinstance(data, dict):
        return [APPLY_UNREADABLE_PAYLOAD]

    # Prefetch facts for every well-formed referenced account id. The lines
    # container may itself be malformed ({"lines": 1}) — the invariant
    # classifies that; the prefetch must not crash on it. Memo classification
    # is account-derived, so memo-account facts are needed too.
    raw_lines = data.get("lines")
    line_iter = raw_lines if isinstance(raw_lines, list) else []
    referenced = {
        cid
        for cid in (canonical_account_id(line.get("account_public_id")) for line in line_iter if isinstance(line, dict))
        if cid is not None
    }
    if facts_cache is None:
        facts_cache = {}
    unseen = referenced - facts_cache.keys()
    if unseen:
        facts_cache.update(load_account_facts(event.company, unseen))
        # Negative caching: unresolved ids stay absent from load_account_facts'
        # result; record them so batch callers do not re-query per event.
        for missing in unseen - facts_cache.keys():
            facts_cache[missing] = None
    usable_facts = {k: v for k, v in facts_cache.items() if v is not None}

    violations = check_posted_journal(data, company_id=event.company_id, account_facts=usable_facts, mode="apply")
    codes = {v.code for v in violations}

    # Identity guards — see the docstring. Part of the evaluator (not the
    # registered validator) so restore verification and the audit apply the
    # EXACT verdict apply-time enforcement applies.
    if not _is_uuid(data.get("entry_public_id")):
        codes.add(APPLY_ENTRY_REF_INVALID)
    else:
        for line in line_iter:
            if not isinstance(line, dict):
                continue
            n = line.get("line_no")
            if isinstance(n, int) and not isinstance(n, bool) and not 0 <= n <= MAX_STORABLE_LINE_NO:
                codes.add(APPLY_ENTRY_REF_INVALID)
                break
    return sorted(codes)


# --------------------------------------------------------------------------- #
# The registered validators
# --------------------------------------------------------------------------- #


def validate_posted_journal_apply(event: BusinessEvent) -> None:
    """journal_entry.posted: raise the shared evaluator's verdict — with one
    refinement for read-model lag.

    ``JE_ACCOUNT_UNKNOWN`` as the SOLE failure class is time-dependent in
    exactly one benign way: a bounded drain window can apply this journal
    event before the Account read model has materialized a later-in-window
    ``account.created`` event's row (registry order fixes the ordering
    WITHIN a pass; it cannot align the per-event-type windows of a
    limit-bounded fresh-database replay). When every unresolved reference
    has an ACCOUNT_CREATED event EARLIER in this company's stream, the
    verdict is ``DeferEvent`` — the framework's invisible retry (A41
    semantics: rolled back, bookmark rewound, re-attempted next pass) — so a
    VALID event is never terminally consumed by read-model lag. A reference
    with no such prior stream event is a genuine unknown and stays terminal,
    as does JE_ACCOUNT_UNKNOWN combined with any other code.

    Deliberately NOT guarded here: materialization-shape fields outside the
    invariant and the identity refs (``date``, ``period``). A garbage date
    on an invariant-clean payload still raises in the handler — a LOUD
    generic halt with an A80 failure log, retried until repaired — which is
    the correct disposition for a materialization defect that is not a
    financial invariant violation (and is what the purge-recovery
    convergence verification treats as a non-converged drain)."""
    codes = evaluate_posted_journal_for_apply(event)
    if not codes:
        return
    if is_deferrable_apply_verdict(event, codes):
        from projections.base import DeferEvent

        raise DeferEvent(
            "posted journal references account(s) whose read-model rows are not yet "
            "materialized; matching account events exist earlier in the stream — "
            "deferring until the account read model catches up"
        )
    raise PostedJournalApplyInvalid(event.event_type, codes)


def is_deferrable_apply_verdict(event: BusinessEvent, codes: list[str]) -> bool:
    """THE single defer predicate, shared by every verdict consumer (the
    choke-point validator, restore verification, the event-first audit —
    Codex round-1 P2: verdict symmetry must include the lag disposition, or
    a batch caller reports as corrupt / refuses a backup of exactly the
    replayable-lag state the choke point defers on). True iff the verdict is
    SOLELY ``JE_ACCOUNT_UNKNOWN`` and every unresolved reference is pending
    materialization per :func:`_unknown_accounts_are_pending_materialization`.
    """
    from accounting.journal_invariant import JE_ACCOUNT_UNKNOWN

    return set(codes) == {JE_ACCOUNT_UNKNOWN} and _unknown_accounts_are_pending_materialization(event)


def _entry_pending_materialization(event: BusinessEvent, entry_public_id: object) -> bool:
    """True iff the referenced journal entry's row is absent while its OWN
    JOURNAL_ENTRY_POSTED event sits EARLIER in the stream, payload-verified
    and not yet consumed by the JournalEntry read model — the deferred-post
    case (Codex round-5 P1). Applies the same accumulated rules as the
    account probe: payload identity beats aggregate metadata; a consumed
    marker triggers a row re-resolution (the round-4 stale-read rule) and
    counts as pending only if the row exists now; consumed-without-row (a
    quarantined/partial prior post) is NOT pending — the sibling handler's
    tolerant behavior for genuinely absent referents stays unchanged."""
    from accounting.models import JournalEntry
    from events.models import BusinessEvent as _BusinessEvent
    from events.types import EventTypes
    from projections.accounting import JournalEntryProjection
    from projections.models import ProjectionAppliedEvent

    target = str(entry_public_id)
    if JournalEntry.objects.filter(company=event.company, public_id=target).exists():
        return False  # the row exists — nothing pending
    candidates = _BusinessEvent.objects.filter(
        company=event.company,
        aggregate_type="JournalEntry",
        aggregate_id=target,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        company_sequence__lt=event.company_sequence,
    ).order_by("company_sequence")[:5]
    je_read_model = JournalEntryProjection().name
    for prior in candidates:
        try:
            prior_data = prior.get_data()
        except Exception:
            continue
        if not isinstance(prior_data, dict) or str(prior_data.get("entry_public_id")) != target:
            continue
        if ProjectionAppliedEvent.objects.filter(
            company=event.company,
            projection_name=je_read_model,
            event=prior,
        ).exists():
            # Consumed — re-resolve the row (stale-read rule): present now
            # means the retry will see it; still absent means the prior post
            # was quarantined/partial, which is not pending.
            if JournalEntry.objects.filter(company=event.company, public_id=target).exists():
                return True
            continue
        return True  # unconsumed prior posted event — genuinely pending
    return False


def _unknown_accounts_are_pending_materialization(event: BusinessEvent) -> bool:
    """True iff EVERY referenced account id that fails to resolve against the
    Account read model has an ACCOUNT_CREATED event EARLIER in this company's
    stream WHOSE PAYLOAD ACTUALLY CREATES that id — pending materialization,
    not a genuine unknown. The payload check matters (Codex round-1 P2): the
    AccountProjection materializes the payload's ``account_public_id``, so a
    foreign/corrupted event whose aggregate metadata says ``cid`` but whose
    payload creates a DIFFERENT id would never resolve — trusting
    ``aggregate_id`` alone would defer such a reference forever instead of
    quarantining it. Runs only on the failure path (zero cost for valid
    events). Ids that resolve on this re-check (materialized between
    evaluations) also count as pending — the retry will succeed."""
    from accounting.journal_invariant import canonical_account_id, load_account_facts
    from events.models import BusinessEvent as _BusinessEvent
    from events.types import EventTypes

    try:
        data = event.get_data()
    except Exception:
        return False
    raw_lines = data.get("lines") if isinstance(data, dict) else None
    line_iter = raw_lines if isinstance(raw_lines, list) else []
    referenced = {
        cid
        for cid in (canonical_account_id(line.get("account_public_id")) for line in line_iter if isinstance(line, dict))
        if cid is not None
    }
    if not referenced:
        return False
    unresolved = referenced - load_account_facts(event.company, referenced).keys()
    for cid in unresolved:
        # Bounded payload verification: real streams carry at most one
        # ACCOUNT_CREATED per aggregate; the cap only guards degenerate
        # foreign streams from turning this probe into a scan.
        candidates = _BusinessEvent.objects.filter(
            company=event.company,
            aggregate_type="Account",
            aggregate_id=cid,
            event_type=EventTypes.ACCOUNT_CREATED,
            company_sequence__lt=event.company_sequence,
        ).order_by("company_sequence")[:5]
        creates_cid = False
        for prior in candidates:
            try:
                prior_data = prior.get_data()
            except Exception:
                continue
            if not isinstance(prior_data, dict):
                continue
            if canonical_account_id(prior_data.get("account_public_id")) != cid:
                continue
            # Materializability (Codex rounds 2+3 P2). Two layers, because
            # statically re-predicting the projection's write logic is an
            # unwinnable arms race (missing keys, None values, uniqueness
            # collisions, ...):
            #
            # (a) static: the creation fields AccountProjection.handle
            #     subscripts unconditionally must be present and non-empty
            #     strings — statically-evident garbage is never evidence;
            # (b) dynamic (the closing rule): the evidence only counts while
            #     the ACCOUNT read model has NOT yet consumed the prior
            #     event. Once its ProjectionAppliedEvent marker exists and
            #     the row STILL does not resolve, draining can never
            #     materialize it (applied-without-row, or terminally
            #     skipped) — the reference is permanently unknown and must
            #     go terminal, not defer forever. While the account
            #     projection is instead HALTED on a bad prior event, the
            #     defer persists exactly as long as that visible,
            #     operator-repairable halt does (the A41 dependency
            #     contract), and flips terminal the moment the operator
            #     terminally skips it.
            if not all(
                isinstance(prior_data.get(key), str) and prior_data.get(key) for key in ("code", "name", "account_type")
            ):
                continue
            from projections.accounting import AccountProjection
            from projections.models import ProjectionAppliedEvent

            if ProjectionAppliedEvent.objects.filter(
                company=event.company,
                projection_name=AccountProjection().name,
                event=prior,
            ).exists():
                # Codex round-4 P1: the marker alone is a stale-read hazard —
                # the account projection may have COMMITTED (row + marker)
                # between this function's earlier facts query and this marker
                # query. Re-resolve the row AFTER observing the marker: if it
                # exists now, the reference is materialized and the retry
                # will succeed (defer); only marker-with-row-still-absent
                # means draining can never materialize it (terminal).
                if load_account_facts(event.company, [cid]):
                    creates_cid = True
                    break
                # Consumed, row still absent — not pending.
                continue
            creates_cid = True
            break
        if not creates_cid:
            return False
    return True


def _readable_payload(event: BusinessEvent) -> dict:
    """Shared sibling-guard preamble: the payload must be readable and a dict.
    Same narrowed catch as the evaluator — transient database errors
    propagate as retryable halts, never as terminal quarantine."""
    from django.db import IntegrityError

    try:
        data = event.get_data()
    except (IntegrityError, ValueError):
        raise PostedJournalApplyInvalid(event.event_type, [APPLY_UNREADABLE_PAYLOAD]) from None
    if not isinstance(data, dict):
        raise PostedJournalApplyInvalid(event.event_type, [APPLY_UNREADABLE_PAYLOAD])
    return data


def _is_uuid(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def validate_reversed_journal_apply(event: BusinessEvent) -> None:
    """journal_entry.reversed: shape guard (D5) plus lifecycle ordering
    (Codex round-5 P1). Both entry references must be well-formed UUIDs — a
    payload missing them previously raised KeyError in the handler, halting
    the whole projection stream head-of-line. And when either referenced
    entry's OWN posted event is still pending materialization (a deferred
    post earlier in the same replay batch), this event must DEFER too:
    letting the handler run would silently no-op on the missing rows and
    consume the event, so the retried posts could never receive their
    reversal — the relationship would be permanently lost."""
    data = _readable_payload(event)
    for key in ("original_entry_public_id", "reversal_entry_public_id"):
        if not _is_uuid(data.get(key)):
            raise PostedJournalApplyInvalid(event.event_type, [APPLY_ENTRY_REF_INVALID])
    for key in ("original_entry_public_id", "reversal_entry_public_id"):
        if _entry_pending_materialization(event, data[key]):
            from projections.base import DeferEvent

            raise DeferEvent(
                "reversal references a journal entry whose own posted event is still "
                "pending materialization — deferring so the lifecycle applies in order"
            )


def validate_deleted_journal_apply(event: BusinessEvent) -> None:
    """journal_entry.deleted: refuse deleting posted financial history from
    the read model (D5). ``delete_journal_entry`` only ever emits for
    INCOMPLETE/DRAFT entries, so a delete targeting a POSTED/REVERSED row can
    only come from a corrupted or foreign stream — applying it would erase a
    posted entry from the read model while the event stream still asserts it.
    A missing row stays the handler's idempotent no-op delete."""
    from accounting.models import JournalEntry

    data = _readable_payload(event)
    entry_public_id = data.get("entry_public_id")
    if not _is_uuid(entry_public_id):
        raise PostedJournalApplyInvalid(event.event_type, [APPLY_ENTRY_REF_INVALID])
    # Lifecycle ordering (Codex round-5 P1, same rule as the reversed door):
    # with the target's own posted event still pending materialization, the
    # status probe below would read an absent row and wave the delete
    # through as a no-op — defer so the guard decides against the
    # materialized row instead.
    if _entry_pending_materialization(event, entry_public_id):
        from projections.base import DeferEvent

        raise DeferEvent(
            "delete targets a journal entry whose own posted event is still "
            "pending materialization — deferring so the guard decides against the row"
        )
    status = (
        JournalEntry.objects.filter(company=event.company, public_id=str(entry_public_id))
        .values_list("status", flat=True)
        .first()
    )
    if status in (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED):
        raise PostedJournalApplyInvalid(event.event_type, [APPLY_DELETE_TARGET_POSTED])


def validate_chunked_journal_apply(event: BusinessEvent) -> None:
    """journal.lines_chunk_added: quarantined unconditionally (D5) — see the
    module docstring. Consume-to-quarantine, not de-listing: the event must
    surface as an operator-visible failure, never advance past silently."""
    raise PostedJournalApplyInvalid(event.event_type, [APPLY_CHUNKED_JOURNAL_UNSUPPORTED])


# --------------------------------------------------------------------------- #
# Registration (called from projections.apps.ready — pinned by an
# architecture test; the mapping below is the complete validated family)
# --------------------------------------------------------------------------- #


def apply_validator_map() -> dict[str, Callable[[BusinessEvent], None]]:
    """The complete event_type -> validator mapping this module owns."""
    from events.types import EventTypes

    return {
        EventTypes.JOURNAL_ENTRY_POSTED: validate_posted_journal_apply,
        EventTypes.JOURNAL_ENTRY_REVERSED: validate_reversed_journal_apply,
        EventTypes.JOURNAL_ENTRY_DELETED: validate_deleted_journal_apply,
        EventTypes.JOURNAL_LINES_CHUNK_ADDED: validate_chunked_journal_apply,
    }


def register_apply_validators() -> None:
    """Idempotent registration of the journal-family apply validators."""
    from projections.apply_validation import register_apply_validator

    for event_type, validator in apply_validator_map().items():
        register_apply_validator(event_type, validator)
