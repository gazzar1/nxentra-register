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
policy (migration 0024). The webhook request session has no tenant context
(``AllowAny``; ``TenantRlsMiddleware`` CASE 5), so the writer establishes
``app.current_company_id`` for the store's company on the working connection
before writing — the write passes the policy's company predicate on its own,
with no bypass granted here (founder decision: no broad bypass for normal
evidence writes). No-op on SQLite; idempotent under the poller's already
established tenant context.
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
    raw unicode). Two payloads equal up to key order hash identically."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
                    ingress_kind=ingress.kind,
                    source_topic=ingress.topic[:64],
                    external_id=external_id,
                    parent_external_id=parent_external_id,
                    parsed_payload=parsed_payload,
                    raw_body_b64=raw_body_b64,
                    payload_hash=payload_hash,
                    transport_hash=transport_hash,
                    rejection_code=rejection_code,
                    rejection_message=rejection_message,
                    validation_errors=validation_errors,
                    last_seen_at=now,
                    last_delivery_id=ingress.delivery_id[:128],
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
                "last_delivery_id": ingress.delivery_id[:128],
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
        # Newly-created gate = the no-repeat-notification guarantee.
        from accounts.models import Notification

        Notification.notify_company_admins(
            company=store.company,
            title=f"Shopify {resource_kind.lower()} rejected — malformed payload",
            message=(
                f"A Shopify {resource_kind.lower()} payload ({ingress.topic}, "
                f"{'id ' + external_id if external_id else 'no trustworthy id'}) could not be processed: "
                f"{rejection_message} The authenticated payload is preserved as rejected evidence — no order, "
                f"refund, event or journal was created. Review it under Finance → Exceptions."
            ),
            level=Notification.Level.ERROR,
            link="/finance/exceptions",
            source_module="shopify_connector",
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
    # Canonical digit form — evidence stores trustworthy ids via
    # payload_validation.trustworthy_external_id, which normalizes "0123" → "123".
    ext = str(external_id)
    if ext.isdigit():
        ext = str(int(ext))
    return ShopifyRejectedEvidence.objects.filter(
        company_id=store.company_id,
        store=store,
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
    ``raw_body_b64`` and stamp ``redacted_at``. Hashes, codes, timestamps and
    evidence identity remain unchanged; re-sighting never restores PII
    (:func:`record_rejected_evidence` writes no payload field on a bump).
    Idempotent — already-redacted rows are left untouched."""
    return queryset.filter(redacted_at__isnull=True).update(
        parsed_payload=None,
        raw_body_b64="",
        redacted_at=timezone.now(),
    )
