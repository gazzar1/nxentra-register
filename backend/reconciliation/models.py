# reconciliation/models.py
"""Durable read model for the reconciliation bounded context.

ADR-0001 prerequisite P5 — ``ReconciliationLink`` makes a confirmed match a
first-class, queryable ROW instead of a tuple of flags on ``BankStatementLine``
plus an unjoinable event stream. It is the substrate the unification phase needs
(Banked/Open computed from links rather than ``source_document`` string-parsing;
the Money Trace "proof button"; the AI propose→confirm seam).

Ownership + replay
==================
``ReconciliationLink`` is a GUARD (read) model — written ONLY by
``ReconciliationProjection`` from ``ReconciliationMatchConfirmed`` /
``...Unmatched`` events, within ``projection_writes_allowed()``. Identity is
deterministic: ``id = uuid5(company, idempotency_key)`` where
``idempotency_key = "{bank_line_public_id}:{journal_line_public_id}"`` (both
replay-stable — bank-line public_id is projection-stable, journal-line public_id
is deterministic since P1). So a from-scratch rebuild reproduces the SAME link
row, and unmatch→rematch of the same pair reuses the SAME link (CONFIRMED →
UNMATCHED → CONFIRMED) rather than spawning duplicates.

Scope note (ADR Open Q#1): the matcher is one-to-one today, so the two primary
legs live as denormalized fields here. A ``ReconciliationLeg`` child table for
many-to-one (many orders → one payout → one deposit) lands with the many-to-one
matcher; nothing is lost by deferring it.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from accounting.models import AccountingReadModel, ProjectionWriteManager
from accounts.models import Company
from projections.write_barrier import write_context_allowed


def derive_link_idempotency_key(bank_line_public_id: str, journal_line_public_id: str) -> str:
    """Deterministic, replay-stable key for the (bank line, journal line) pair.

    For the platform-payout path there is no bank line (``bank_line_public_id``
    is ""), so the key degrades to ``":{journal_line_public_id}"`` — still
    deterministic. Confirmed and Unmatched events for the same match derive the
    SAME key (unmatch carries ``previously_matched_journal_line_public_id`` =
    the original journal line), so the link is a single state machine, not a
    duplicate per transition.
    """
    return f"{bank_line_public_id or ''}:{journal_line_public_id or ''}"


def derive_link_id(company_id: int, idempotency_key: str) -> uuid.UUID:
    """Deterministic primary key so the same match → the same link row across a
    from-scratch projection rebuild."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"nxentra:reconciliation_link:{company_id}:{idempotency_key}")


class ReconciliationLink(AccountingReadModel):
    """A durable record of one reconciliation match (one-to-one today)."""

    class Status(models.TextChoices):
        PROPOSED = "PROPOSED", "Proposed (advisory)"
        CONFIRMED = "CONFIRMED", "Confirmed"
        NEEDS_REVIEW = "NEEDS_REVIEW", "Confirmed with unexplained difference"
        REJECTED = "REJECTED", "Rejected"
        UNMATCHED = "UNMATCHED", "Unmatched"
        EXCLUDED = "EXCLUDED", "Excluded (operator marked out of scope)"
        REVERSED = "REVERSED", "Reversed"
        DISPUTED = "DISPUTED", "Disputed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="reconciliation_links")

    # Deterministic upsert key for the match: "{bank_line}:{journal_line}".
    idempotency_key = models.CharField(max_length=255)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    match_kind = models.CharField(max_length=40, blank=True)
    confirmation_kind = models.CharField(max_length=40, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Denormalized legs (one-to-one today; child legs arrive with many-to-one).
    bank_line_public_id = models.CharField(max_length=64, blank=True)
    journal_line_public_id = models.CharField(max_length=64, blank=True)
    additional_journal_line_public_ids = models.JSONField(default=list, blank=True)

    # A16 difference carried for the NEEDS_REVIEW state.
    difference_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    difference_reason = models.CharField(max_length=40, blank=True)

    # U5a structural legs — for settlement-clearance matches, the projection
    # parses the clearance JE's `{provider}:{batch}` source_document ONCE at
    # write time and stores the result here, so reads (the Money Trace; U5c's
    # Banked/Open) use structured, provider-scoped fields instead of re-parsing
    # source_document with .split(':'). Blank for non-settlement matches and for
    # links written before U5a (those fall back to the legacy suffix lookup).
    provider_normalized_code = models.CharField(max_length=64, blank=True)
    settlement_batch_id = models.CharField(max_length=128, blank=True)
    clearance_je_public_id = models.CharField(max_length=64, blank=True)

    # Accountability (finally captured — the events define these but the
    # legacy BankStatementLine flags never recorded them).
    confirmed_by_user_id = models.IntegerField(null=True, blank=True)
    confirmed_by_email = models.CharField(max_length=255, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unmatched_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectionWriteManager()

    class Meta:
        db_table = "reconciliation_link"
        constraints = [
            models.UniqueConstraint(fields=["company", "idempotency_key"], name="uniq_recon_link_company_key"),
        ]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "bank_line_public_id"]),
            models.Index(fields=["company", "journal_line_public_id"]),
            models.Index(fields=["company", "settlement_batch_id"]),
        ]

    def __str__(self):
        return f"ReconciliationLink({self.idempotency_key} {self.status})"

    def save(self, *args, _projection_write: bool = False, **kwargs):
        # Guard: a read model. Sole writer is ReconciliationProjection within
        # projection_writes_allowed(). TESTING bypasses (matches AccountingReadModel).
        if not write_context_allowed({"projection"}) and not getattr(settings, "TESTING", False):
            raise RuntimeError(
                "ReconciliationLink is a read model. It is written only by "
                "ReconciliationProjection within projection_writes_allowed()."
            )
        super().save(*args, **kwargs)
