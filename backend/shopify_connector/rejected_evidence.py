# shopify_connector/rejected_evidence.py
"""A5-PR2b — the single write path for ``ShopifyRejectedEvidence``.

Both ingress kinds (webhook + poller) and the malformed-JSON view hook persist
rejected evidence through :func:`record_rejected_evidence` so the identity,
re-sighting, reopen, redaction-persistence and notification contracts live in
exactly one place (AGENTS.md non-negotiable #3 — one invariant implementation).

Contracts (founder spec + markups, 2026-08-23):

- IDENTITY: ``dedup_hash = sha256(company_id | store_public_id | resource_kind
  | payload_hash)``. ``rejection_code`` is software interpretation, not source
  identity — a validator refinement must never create a second evidence row for
  the exact same payload.
- RE-SIGHTING: a repeated delivery of the same still-malformed payload updates
  exactly one record — ``occurrence_count`` += 1, ``last_seen_at`` /
  ``last_delivery_id`` refreshed, acknowledgment CLEARED (the queue item
  reopens; the operator note is preserved as history). A SUPERSEDED record
  stays superseded on stale duplicate delivery (bump only, never reopened).
  A REDACTED record never has PII written back into it (the bump touches no
  payload field; identical payload ⇒ identical content by construction anyway).
- NOTIFICATION: delivery/visibility only, and only when the row is NEWLY
  created — never on a re-sight (``Notification.notify_company_admins`` has no
  dedup of its own, so the caller-side gate here IS the dedup).
- PERSISTENCE FAILURE: raises out — the caller must keep the webhook retryable
  (never acknowledge malformed evidence whose durable record was not stored).

Write-barrier posture: plain operational write-model (the ``ImportRejectedRow``
precedent) — records the ABSENCE of a canonical fact; no barrier context.

RLS posture: ``shopify_rejected_evidence`` carries a FORCED tenant-isolation
policy (migration 0024). The webhook request session has no tenant context of
its own (``AllowAny``; ``TenantRlsMiddleware`` CASE 5 — which today also sets
the session ``rls_bypass`` GUC for unauthenticated paths, a pre-existing
middleware posture this PR does not change), so the writer establishes
``app.current_company_id`` for the store's company on the working connection
before writing. THIS module grants no bypass anywhere (founder decision: no
broad bypass for normal evidence writes) — the write passes the policy's
company predicate on its own, proven under a NOBYPASSRLS role with bypass OFF
by tests/e2e/test_a5_pr2b_rejected_evidence_rls.py. No-op on SQLite; idempotent
under the poller's already established tenant context. The same context
re-establishment guards :func:`supersede_open_evidence`, which runs after
``emit_event_no_actor`` has CLEARED the connection's RLS session settings.
"""

import base64
import hashlib
import json
import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts import rls

from .models import ShopifyRejectedEvidence, ShopifyStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngressContext:
    """Provenance of one delivery — metadata only, never evidence identity."""

    kind: str  # ShopifyRejectedEvidence.IngressKind value
    topic: str  # e.g. "orders/paid", "refunds/create", "poller/orders-paid"
    delivery_id: str = ""  # X-Shopify-Webhook-Id, or "poller"
    raw_body: bytes | None = None  # exact authenticated webhook bytes, when available


def poller_ingress(topic: str) -> IngressContext:
    """Default provenance for the non-webhook entry points (4h beat, manual
    re-sync, initial sync, refund backfill — all poller-driven)."""
    return IngressContext(kind=ShopifyRejectedEvidence.IngressKind.POLLER, topic=topic, delivery_id="poller")


def canonical_payload_hash(payload) -> str:
    """SHA-256 of deterministic canonical JSON (sorted keys, compact separators,
    raw unicode). Two payloads equal up to key order hash identically.
    ``surrogatepass``: a lone \\ud800-style surrogate (which json.loads happily
    produces) would make a plain UTF-8 encode raise BEFORE the evidence insert
    (Codex round-7) — surrogatepass encodes it deterministically and keeps
    distinct payloads distinct. Normal payloads are byte-identical either way."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def _fix_scalar(value):
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return repr(value)
    if isinstance(value, str):
        # PostgreSQL jsonb cannot store U+0000 (Codex round-6) or a lone
        # Unicode surrogate (round-7 — UTF-8 encoding raises on it) — keep the
        # evidence storable with visible escapes in their place.
        if "\x00" in value:
            value = value.replace("\x00", "\\u0000")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            value = value.encode("utf-8", "backslashreplace").decode("utf-8")
        return value
    return value


def sanitize_operator_text(value: str) -> str:
    """Public wrapper for operator-supplied free text (the acknowledgment note,
    Codex round-9): DRF's JSON parser happily materializes \u0000 escapes and
    lone surrogates from a request body, and PostgreSQL text columns refuse
    both — sanitize with the same visible-escape rules as evidence storage."""
    return _fix_scalar(value)


def _storable_json(value):
    """Replace non-finite floats with their string form throughout the
    document. Python's json.loads accepts bare NaN/Infinity tokens, but
    PostgreSQL jsonb refuses them — an unsanitized parsed_payload would make
    the evidence INSERT itself fail, turning a permanent provider-data error
    into the endless retry loop this table exists to prevent. Identity is
    unaffected: payload_hash is computed from the ORIGINAL parsed value before
    sanitization. ITERATIVE (Codex round-3 P2): a deeply nested but parseable
    payload must never RecursionError the evidence writer — that would strand
    the delivery in the retry loop with no preserved evidence. Structure-
    preserving copy; a no-op-shaped result for every normal payload."""
    if not isinstance(value, dict | list):
        return _fix_scalar(value)
    root: dict | list = {} if isinstance(value, dict) else []
    stack: list[tuple[dict | list, dict | list]] = [(value, root)]
    while stack:
        source, target = stack.pop()
        if isinstance(source, dict):
            for key, item in source.items():
                key = _fix_scalar(key)  # jsonb refuses NUL in object keys too
                if isinstance(item, dict | list):
                    child: dict | list = {} if isinstance(item, dict) else []
                    target[key] = child
                    stack.append((item, child))
                else:
                    target[key] = _fix_scalar(item)
        else:
            for item in source:
                if isinstance(item, dict | list):
                    child = {} if isinstance(item, dict) else []
                    target.append(child)
                    stack.append((item, child))
                else:
                    target.append(_fix_scalar(item))
    return root


def compute_dedup_hash(company_id: int, store_public_id, resource_kind: str, payload_hash: str) -> str:
    """The founder-decided evidence identity — see the module docstring."""
    material = f"{company_id}|{store_public_id}|{resource_kind}|{payload_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def record_rejected_evidence(
    *,
    store: ShopifyStore,
    resource_kind: str,
    rejection_code: str,
    rejection_message: str,
    validation_errors: list[dict],
    ingress: IngressContext,
    parsed_payload=None,
    external_id: str | None = None,
    parent_external_id: str | None = None,
) -> tuple[ShopifyRejectedEvidence, bool]:
    """Persist (or re-sight) one rejected-evidence record in its OWN committed
    transaction, and notify company admins only when the row is newly created.

    Exactly one of the payload shapes applies:
    - ``parsed_payload`` set (parsed Shopify JSON — webhook or poller):
      ``payload_hash`` = canonical-JSON hash; ``transport_hash`` /
      ``raw_body_b64`` from ``ingress.raw_body`` when available.
    - ``parsed_payload`` None with ``ingress.raw_body`` set (HMAC-valid
      malformed JSON): both hashes are the exact-body SHA-256 and
      ``raw_body_b64`` retains the authenticated bytes.

    Raises on persistence failure — the caller classifies that as retryable.
    Returns ``(row, created)``.
    """
    if parsed_payload is None and ingress.raw_body is None:
        raise ValueError("rejected evidence needs a parsed payload or the raw authenticated body")

    if parsed_payload is not None:
        payload_hash = canonical_payload_hash(parsed_payload)
    else:
        payload_hash = hashlib.sha256(ingress.raw_body).hexdigest()

    transport_hash = hashlib.sha256(ingress.raw_body).hexdigest() if ingress.raw_body is not None else ""
    raw_body_b64 = base64.b64encode(ingress.raw_body).decode("ascii") if ingress.raw_body is not None else ""

    dedup_hash = compute_dedup_hash(store.company_id, store.public_id, resource_kind, payload_hash)
    now = timezone.now()

    with transaction.atomic():
        # FORCED RLS: give this connection the company-scoped tenant context so
        # the INSERT/UPDATE passes the policy predicate without any bypass (the
        # webhook session has no tenant context of its own). No-op on SQLite.
        rls.set_current_company_id(store.company_id)

        created = False
        try:
            with transaction.atomic():  # savepoint: a lost create-race must not poison the outer tx
                row = ShopifyRejectedEvidence.objects.create(
                    company=store.company,
                    store=store,
                    store_public_id=store.public_id,
                    shop_domain=store.shop_domain,
                    resource_kind=resource_kind,
                    # _storable_json/_fix_scalar on EVERY caller-supplied text/
                    # JSON field (Codex round-8): validation_errors and
                    # rejection_message can quote payload fragments (defect
                    # paths embed object KEYS), and provenance strings ride
                    # HTTP headers — none may carry a jsonb-unstorable NUL or
                    # lone surrogate into the evidence INSERT itself.
                    ingress_kind=ingress.kind,
                    source_topic=_fix_scalar(ingress.topic[:64]),
                    external_id=external_id,
                    parent_external_id=parent_external_id,
                    parsed_payload=_storable_json(parsed_payload) if parsed_payload is not None else None,
                    raw_body_b64=raw_body_b64,
                    payload_hash=payload_hash,
                    transport_hash=transport_hash,
                    rejection_code=rejection_code,
                    rejection_message=_fix_scalar(rejection_message),
                    validation_errors=_storable_json(validation_errors),
                    last_seen_at=now,
                    last_delivery_id=_fix_scalar(ingress.delivery_id[:128]),
                    dedup_hash=dedup_hash,
                )
                created = True
        except IntegrityError:
            # Same (company, dedup_hash) — a genuine re-sighting (or a
            # concurrent duplicate delivery losing the create race).
            row = ShopifyRejectedEvidence.objects.get(company=store.company, dedup_hash=dedup_hash)
            updates = {
                # F() so concurrent duplicate deliveries never lose a count.
                "occurrence_count": F("occurrence_count") + 1,
                "last_seen_at": now,
                "last_delivery_id": _fix_scalar(ingress.delivery_id[:128]),
            }
            if row.superseded_at is None:
                # Reopen: the payload is STILL arriving malformed — a stale
                # acknowledgment must not hide it. The note survives as history.
                updates.update(acknowledged=False, acknowledged_at=None, acknowledged_by=None)
            # Deliberately no payload-field writes: a redacted record must never
            # get PII restored, and an unredacted record already holds the
            # identical content (payload_hash is part of the identity).
            ShopifyRejectedEvidence.objects.filter(pk=row.pk).update(**updates)
            row.refresh_from_db()

    if created:
        # Delivery/visibility only — the durable source record is the row above.
        # Newly-created gate = the no-repeat-notification guarantee. The message
        # carries CODES and ids only, never the defect detail: rejection_message
        # can quote a truncated repr of the offending payload value (possible
        # PII), and Notification rows sit outside the evidence table's
        # GDPR-redaction path — the reviewable detail lives in the queue.
        #
        # BEST-EFFORT (Codex round-2): raising here would 503/500 a delivery
        # whose durable record is (sav)committed, and every redelivery takes
        # the dedup path (created=False) — so the raise could never be retried
        # into a notification anyway; it would only loop the webhook. The spec
        # is explicit that Notification is delivery/visibility, NOT the durable
        # source record — and /_health/alerts pages on the OPEN evidence count
        # regardless, so a lost notification never hides the rejection.
        #
        # The inner atomic is LOAD-BEARING (Codex round-3 P1): when the caller
        # holds an outer transaction (process_refund's @transaction.atomic; the
        # paid orders/create delegation; the poller's per-order savepoint), the
        # evidence write above is only a SAVEPOINT — catching a real database
        # error here WITHOUT this savepoint would mark the outer transaction
        # rollback-only, silently discarding the evidence while the webhook
        # still acks 200 (the exact never-ack-unstored-evidence violation).
        try:
            from accounts.models import Notification

            with transaction.atomic():
                Notification.notify_company_admins(
                    company=store.company,
                    title=f"Shopify {resource_kind.lower()} rejected — malformed payload",
                    message=(
                        f"A Shopify {resource_kind.lower()} payload ({ingress.topic}, "
                        f"{'id ' + external_id if external_id else 'no trustworthy id'}) could not be processed "
                        f"({rejection_code}). The authenticated payload is preserved as rejected evidence — no order, "
                        f"refund, event or journal was created. Review it under Finance → Exceptions."
                    ),
                    level=Notification.Level.ERROR,
                    link="/finance/exceptions",
                    source_module="shopify_connector",
                )
        except Exception:
            logger.exception(
                "Rejected-evidence notification failed for %s (%s) — the durable record is committed "
                "and /_health/alerts still pages on it",
                store.shop_domain,
                resource_kind,
            )

    return row, created


def record_malformed_webhook_body(
    *,
    store: ShopifyStore,
    resource_kind: str,
    topic: str,
    raw_body: bytes,
    delivery_id: str,
    error_message: str,
) -> tuple[ShopifyRejectedEvidence, bool]:
    """HMAC-valid but unparseable webhook body (the MALFORMED_JSON case):
    authenticated evidence with no parsed form — both hashes are the exact-body
    SHA-256 and the body survives verbatim in ``raw_body_b64``."""
    return record_rejected_evidence(
        store=store,
        resource_kind=resource_kind,
        rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_JSON,
        rejection_message=f"Webhook body is not valid JSON: {error_message}",
        validation_errors=[{"code": "MALFORMED_JSON", "field": "body", "message": error_message}],
        ingress=IngressContext(
            kind=ShopifyRejectedEvidence.IngressKind.WEBHOOK,
            topic=topic,
            delivery_id=delivery_id,
            raw_body=raw_body,
        ),
        parsed_payload=None,
    )


def supersede_open_evidence(
    *,
    store: ShopifyStore,
    resource_kind: str,
    external_id,
    order=None,
    refund=None,
) -> int:
    """Corrected redelivery healed: mark every OPEN evidence record for this
    external identity superseded and link it to the canonical row. Runs inside
    the caller's committing transaction (same tx as the canonical row + event),
    so supersession is recorded iff the healing commit lands. Historical
    payloads are never deleted; acknowledgment/redaction fields are untouched.
    """
    target = order if order is not None else refund
    if not external_id or target is None:
        return 0
    # FORCED RLS: re-establish the company context on this connection before the
    # UPDATE. The healing paths run AFTER emit_event_no_actor, whose `finally`
    # CLEARS the connection's RLS session settings (the documented
    # _reassert_shopify_rls exposure) — without this, the UPDATE would silently
    # match ZERO rows on a shared-tenant PostgreSQL deployment and supersession
    # would never be recorded. No-op on SQLite; idempotent elsewhere.
    rls.set_current_company_id(store.company_id)
    # Canonical digit form — evidence stores trustworthy ids via
    # payload_validation.trustworthy_external_id, which normalizes "0123" → "123".
    ext = str(external_id)
    if ext.isdigit():
        ext = str(int(ext))
    # Scoped by the IMMUTABLE company/shop_domain snapshot, not the convenience
    # store FK (Codex round-13): evidence deliberately survives store deletion/
    # replacement (SET_NULL + snapshots), and a corrected delivery processed
    # through a REPLACEMENT store row must still heal it — an exact-FK
    # predicate would leave the rejection permanently open and paging.
    return ShopifyRejectedEvidence.objects.filter(
        company_id=store.company_id,
        shop_domain=store.shop_domain,
        resource_kind=resource_kind,
        external_id=ext,
        superseded_at__isnull=True,
    ).update(
        superseded_at=timezone.now(),
        superseded_by_order=order,
        superseded_by_refund=refund,
        superseded_target_public_id=target.public_id,
    )


def redact_evidence(queryset) -> int:
    """GDPR scrub for the given evidence rows: clear ``parsed_payload`` /
    ``raw_body_b64``, scrub the payload-value fragments embedded in
    ``rejection_message`` / ``validation_errors`` (the validator's defect
    messages quote a truncated repr of the offending value, which can itself
    carry PII), clear ``acknowledgment_note`` (free-form operator input shown
    next to the raw evidence — an operator may have copied shopper PII into it;
    Codex round-1), and stamp ``redacted_at``. Hashes, CODES, defect FIELD
    names, timestamps, the acknowledged/at/by audit facts and evidence identity
    remain unchanged; re-sighting never restores PII
    (:func:`record_rejected_evidence` writes no payload or message field on a
    bump). Idempotent — already-redacted rows are left untouched."""
    scrubbed = 0
    now = timezone.now()
    for row in queryset.filter(redacted_at__isnull=True):
        ShopifyRejectedEvidence.objects.filter(pk=row.pk).update(
            parsed_payload=None,
            raw_body_b64="",
            rejection_message=f"{row.rejection_code} (defect details redacted under GDPR)",
            validation_errors=[
                {"code": e.get("code", ""), "field": e.get("field", ""), "message": "[redacted]"}
                for e in (row.validation_errors or [])
                if isinstance(e, dict)
            ],
            acknowledgment_note="",
            redacted_at=now,
        )
        scrubbed += 1
    return scrubbed
