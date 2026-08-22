# accounting/import_rejects.py
"""A5-PR3b/c — the ONE write path for durable per-row import rejects.

Both importers (settlement CSV and bank-statement CSV) persist dropped/flagged
source rows through :func:`persist_import_rejects` so the idempotency contract
lives in exactly one place (AGENTS.md non-negotiable #3):

- ``dedup_hash`` is derived from the STABLE import identity — company, source
  kind, provider, filename, row index, reason, and the canonicalized raw row —
  NEVER from ``import_batch_id`` (a fresh UUID per upload). A re-upload of the
  same file therefore bumps ``occurrence_count`` on the existing row instead of
  creating a duplicate (`test_a5_pr3a_import_rejected_row.py` pins this).
- A re-seen reject does NOT clear ``resolved`` (deliberate divergence from the
  ``ProjectionFailureLog`` dedup contract): re-uploading a file re-presents the
  same immutable bad row every time, so reopening the ack on each overlap-period
  re-upload would make operator acks meaningless. ``occurrence_count`` and
  ``last_seen_at`` still record the re-sighting.

Reject descriptors are plain dicts produced at parse/commit time:
``{"row_index": int, "raw_row": dict, "reason_code": str, "reason_message": str}``
(optional ``"status"`` for the QUARANTINED review-flag flavor, e.g. orphan
order_ids). ``ImportRejectedRow`` is a plain operational write-model (the
``ProjectionFailureLog`` precedent) — no write-barrier context is required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from django.db.models import F

logger = logging.getLogger(__name__)

# Descriptors arriving from outside the process (the bank commit endpoint echoes
# parse-time descriptors back through the client) are validated against this set —
# an unknown reason_code is dropped, never written.
_VALID_REASON_CODES: frozenset[str] | None = None


def _valid_reason_codes() -> frozenset[str]:
    global _VALID_REASON_CODES
    if _VALID_REASON_CODES is None:
        from accounting.models import ImportRejectedRow

        _VALID_REASON_CODES = frozenset(ImportRejectedRow.ReasonCode.values)
    return _VALID_REASON_CODES


def sanitize_raw_row(row: dict) -> dict:
    """Make a csv.DictReader row JSON-safe: stringify keys (extra columns parse
    with key ``None``) and coerce non-JSON values to strings."""
    out: dict[str, object] = {}
    for k, v in (row or {}).items():
        key = "_extra" if k is None else str(k)
        if v is None or isinstance(v, str | int | float | bool):
            out[key] = v
        else:
            out[key] = str(v)
    return out


def reject_descriptor(*, row_index: int, raw_row: dict, reason_code: str, reason_message: str) -> dict:
    """Build one reject descriptor (parse-time shape, not yet persisted)."""
    return {
        "row_index": row_index,
        "raw_row": sanitize_raw_row(raw_row),
        "reason_code": reason_code,
        "reason_message": reason_message,
    }


def compute_reject_dedup_hash(
    *,
    company_id: int,
    source_kind: str,
    provider_code: str,
    identity_scope: str,
    source_filename: str,
    row_index: int,
    reason_code: str,
    raw_row: dict,
) -> str:
    """Stable identity for one rejected row across re-uploads (NOT batch-scoped).

    ``identity_scope`` narrows the identity beyond provider_code where needed —
    bank rejects pass ``account:<pk>`` (Codex round-3: two ACCOUNTS importing a
    same-named file with an identical bad row at the same position are distinct
    evidence, but a re-upload to the SAME account still dedups); settlement
    rejects pass "" (provider_code already scopes them).
    """
    canonical = json.dumps(raw_row, sort_keys=True, default=str, ensure_ascii=False)
    material = "|".join(
        [
            str(company_id),
            source_kind,
            provider_code,
            identity_scope,
            source_filename,
            str(row_index),
            reason_code,
            canonical,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def persist_import_rejects(
    company,
    *,
    source_kind: str,
    provider_code: str,
    source_filename: str,
    import_batch_id: uuid.UUID,
    rejects: list[dict],
    statement=None,
    identity_scope: str = "",
) -> int:
    """Persist reject descriptors idempotently; returns how many were written
    (created or occurrence-bumped). Malformed/unknown descriptors are skipped
    with a log line — evidence persistence must never fail an import."""
    from accounting.models import ImportRejectedRow

    written = 0
    valid_reasons = _valid_reason_codes()
    valid_statuses = frozenset(ImportRejectedRow.Status.values)
    for desc in rejects or []:
        if not isinstance(desc, dict):
            logger.warning("Import reject descriptor is not a dict — skipped: %r", desc)
            continue
        reason_code = str(desc.get("reason_code") or "")
        if reason_code not in valid_reasons:
            logger.warning("Import reject descriptor has unknown reason_code %r — skipped", reason_code)
            continue
        try:
            row_index = max(int(desc.get("row_index") or 0), 1)
        except (TypeError, ValueError):
            row_index = 1
        raw_row = desc.get("raw_row")
        raw_row = sanitize_raw_row(raw_row) if isinstance(raw_row, dict) else {"_raw": str(raw_row)}
        status = str(desc.get("status") or ImportRejectedRow.Status.REJECTED)
        if status not in valid_statuses:
            status = ImportRejectedRow.Status.REJECTED
        reason_message = str(desc.get("reason_message") or "")[:5000]

        dedup_hash = compute_reject_dedup_hash(
            company_id=company.pk,
            source_kind=source_kind,
            provider_code=provider_code,
            identity_scope=identity_scope,
            source_filename=source_filename,
            row_index=row_index,
            reason_code=reason_code,
            raw_row=raw_row,
        )
        _row, created = ImportRejectedRow.objects.update_or_create(
            company=company,
            dedup_hash=dedup_hash,
            defaults={
                "source_kind": source_kind,
                "provider_code": provider_code,
                "source_filename": source_filename,
                "import_batch_id": import_batch_id,
                "statement": statement,
                "row_index": row_index,
                "raw_row": raw_row,
                "reason_code": reason_code,
                "reason_message": reason_message,
                "status": status,
            },
        )
        if not created:
            # Re-sighting: bump the counter atomically (update_or_create already
            # refreshed the mutable fields + last_seen_at). resolved is NOT
            # cleared — see the module docstring.
            ImportRejectedRow.objects.filter(pk=_row.pk).update(occurrence_count=F("occurrence_count") + 1)
        written += 1
    return written


# ---------------------------------------------------------------------------
# Settlement orphan-order review flags (Codex round-9: provider-NEUTRAL home —
# the projection must not depend on the CSV-adapter module settlement_imports;
# this module is the reject write path and knows no parser).
#
# Codex round-10: the ORDER LOOKUP is provider knowledge, so it is INVERTED —
# adapters register how to resolve which order ids exist locally for their
# external system (the register_settlement_parser precedent; AGENTS.md:
# adapters depend on the core, the core never imports an adapter). No lookup
# registered for an event's external_system ⇒ the core cannot determine
# orphan-ness and writes NO flags (guessing would false-page).
# ---------------------------------------------------------------------------

_KNOWN_ORDER_LOOKUPS: dict[str, object] = {}


def register_known_order_lookup(external_system: str, lookup) -> None:
    """Adapter registration point: ``lookup(company, digit_order_ids) -> set[str]``
    returns which of the given digit order ids exist locally for this external
    system. Called from the adapter's AppConfig.ready()."""
    _KNOWN_ORDER_LOOKUPS[(external_system or "").strip().lower()] = lookup


def orphan_review_import_batch_id(company_id: int, event_id: int):
    """Deterministic reject-group id for one settlement event's orphan review
    flags. The flags are written by the PROJECTION after the JE posts (Codex
    round-8), where the upload's per-request UUID is not available — a
    deterministic id keeps rebuild/replay idempotent and lets the import view
    read the flags back for its HTTP response."""
    import uuid as _uuid

    return _uuid.uuid5(_uuid.NAMESPACE_URL, f"nxentra:settlement-orphan:{company_id}:{event_id}")


def persist_orphan_review_flags_for_posted_event(company, event) -> None:
    """Write QUARANTINED ORPHAN_ORDER_ID review flags for the CANONICAL rows in
    ``event``'s payload whose digit order_id matches no local order.

    Called by ``PaymentSettlementProjection.handle`` immediately AFTER the
    batch JE posts, inside the same per-event atomic — so a flag claiming "the
    JE posted" exists IFF the posting it describes committed (Codex round-8:
    all-zero / fully-credited / quarantined / failed batches write NO flags).
    Deriving from the stored event also makes the flags truthful under emitter
    deduplication and concurrent-upload races (Codex rounds 2-3), and a replay
    of the same event just bumps occurrence_count.

    Digit ids only: adapter lookups match on numeric platform order ids, so
    only a digit id can be a genuinely orphaned order reference; non-digit refs
    (Bosta shipment / tracking ids like "ORD-1") are ALWAYS "unknown" by
    construction and would page a false review flag for every COD row.

    Provider-neutral (Codex round-10): the order lookup is dispatched through
    the adapter-registered ``register_known_order_lookup`` registry — this
    module imports no adapter and carries no provider branch. An event whose
    external_system has no registered lookup writes NO flags.
    """
    if event is None:
        return

    data = event.get_data() or {}
    # Codex round-11: dispatch ONLY on an explicitly-present system — no
    # provider default in core. A missing/blank external_system writes no
    # flags, exactly like an unregistered one (the importer layer always
    # stamps the system into the event payload, so real events are explicit).
    external_system = str(data.get("external_system") or "").strip().lower()
    lookup = _KNOWN_ORDER_LOOKUPS.get(external_system) if external_system else None
    if lookup is None:
        return
    provider_code = str(data.get("provider_normalized_code") or "")
    import_batch_id = orphan_review_import_batch_id(company.pk, event.id)
    line_items = data.get("line_items") or []
    payout_batch_id = str(data.get("payout_batch_id") or "")
    # Canonical filename from the STORED event's metadata (not this upload's) —
    # it names the file that created the canonical rows AND keeps the flag
    # identity stable when the same batch is re-uploaded under a new filename.
    canonical_filename = str((getattr(event, "metadata", None) or {}).get("filename") or "")
    digit_ids = sorted(
        {str(li.get("order_id") or "").strip() for li in line_items if str(li.get("order_id") or "").strip().isdigit()}
    )
    if not digit_ids:
        return
    known = {str(oid) for oid in (lookup(company, digit_ids) or set())}

    orphan_rejects = []
    for li_index, li in enumerate(line_items, start=1):
        li_order_id = str(li.get("order_id") or "").strip()
        if li_order_id and li_order_id.isdigit() and li_order_id not in known:
            orphan_rejects.append(
                {
                    # Position within the batch's canonical line items (the
                    # parser aggregates by batch, so the original file index is
                    # gone; the stamped batch id disambiguates — and keeps
                    # identical rows in different batches from colliding in the
                    # dedup hash, Codex round-1).
                    "row_index": li_index,
                    "raw_row": {**li, "payout_batch_id": payout_batch_id},
                    "reason_code": "ORPHAN_ORDER_ID",
                    "reason_message": (
                        f"Batch {payout_batch_id}: order_id {li_order_id} matches no local order. "
                        "The JE posted — provider clearing may go negative on this row. "
                        "Investigate, then resolve."
                    ),
                    "status": "QUARANTINED",
                }
            )
    persist_import_rejects(
        company,
        source_kind="SETTLEMENT",
        provider_code=provider_code,
        source_filename=canonical_filename,
        import_batch_id=import_batch_id,
        rejects=orphan_rejects,
    )
