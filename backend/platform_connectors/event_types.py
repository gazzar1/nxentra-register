# platform_connectors/event_types.py
"""Canonical payment-layer event payloads (ADR-0002 PR-D).

PROVIDER_PAYOUT_RECONCILED is a FULL-STATE SNAPSHOT of one payout's line match
verdicts + header reconciliation outcome, emitted by a provider adapter after
its reconcile/verify pass (Stripe first: stripe_connector/reconciled_emit.py).
Replay is last-write-wins in company_sequence order — each event is
self-sufficient, so the projection handler is a dumb stamp and rebuild
reconstructs match state from the snapshots alone (A139).

Design constraints baked into the field names:
- Provider-NEUTRAL: no stripe_* names; provider-specific residue goes in
  provider_metadata. Paymob/Bosta/Shopify can ride the same event.
- Validator-safe: events.types.validate_event_payload walks every nested dict
  and validates scalars BY NAME (enum_fields/decimal_fields/currency_fields).
  line_verdicts entries therefore avoid ALL reserved names — notably `kind`
  (bound to JournalEntry.Kind) and `currency`/`amount`/`debit`/`credit`.
  The settlement event's line_items[] hit the same trap and uses `status`;
  verdicts simply don't carry a line-type field (the canonical line row
  already has `kind`; `match_kind`/`matched_ref_type` carry the match
  semantics). Guarded by test_s2g_payout_reconciled.
- Variances are computed against the settlement EVENT's own frozen totals and
  line sums (not the mutable legacy header, not the flag-selected header), so
  the payload is flag-independent and replay-consistent with the canonical
  rows PaymentsProjection materializes from the same settlement event.

REGISTERED_EVENTS is discovered via PlatformConnectorsConfig.event_types_module
and merged into events.types.EVENT_DATA_CLASSES by ProjectionsConfig.ready().
"""

from dataclasses import dataclass, field
from typing import Optional

from events.types import BaseEventData, EventTypes

# line_verdicts[] entry contract (plain dicts; strict-mode validation applies
# only to top-level dataclass fields, name-keyed scalar validation to all):
#   line_index: int        — THE correlation key: position in the settlement
#                            event's frozen line_items[]. The projection
#                            recomputes derive_provider_payout_line_id from it.
#   verified: bool         — the value persisted on the legacy line at snapshot
#                            time (DB semantics, parity target for
#                            _legacy_verified_counts).
#   match_kind: str        — "charge" | "refund" | "auto_type" | "none".
#                            auto_type mirrors reconcile_payout's in-memory
#                            treatment of adjustment/payout/other lines (matched
#                            for header counts, verified only if the verify
#                            endpoint persisted it).
#   matched_ref: str       — matched object's external id (charge/refund id);
#                            replaces local_charge FK semantics canonically.
#   matched_ref_type: str  — "charge" | "refund" | "".
#   provider_line_ref: str — the provider's line id (Stripe balance-txn id);
#                            "" when the event line has no legacy twin.


@dataclass
class ProviderPayoutReconciledData(BaseEventData):
    """Data for provider_payout.reconciled — see module docstring."""

    provider: str = ""  # normalized lowercase code ("stripe"), same spelling as
    #                     PaymentsProjection's provider derivation or correlation misses
    payout_batch_id: str = ""  # == settlement event payout_batch_id (Stripe po_ id)
    reconciled_at: str = ""  # ISO datetime (convention field, like confirmed_at)
    source: str = ""  # "auto_reconcile" | "manual_verify" | "backfill"
    triggered_by_user_id: Optional[int] = None  # None for system-triggered runs
    triggered_by_email: str = ""
    outcome: str = ""  # header verdict: "verified" | "discrepancy"
    matched_count: int = 0  # verdicts with match_kind != "none" (reconcile's in-memory semantics)
    unmatched_count: int = 0
    total_count: int = 0  # == len(line_verdicts) == len(settlement line_items)
    verified_count: int = 0  # verdicts with verified=True (DB semantics — the parity target)
    gross_variance: str = "0"  # Decimal strings; event-frozen: header totals − Σ line_items
    fee_variance: str = "0"
    net_variance: str = "0"
    currency: Optional[str] = None  # valid 3-letter uppercase or None (validator-enforced by name)
    line_verdicts: list = field(default_factory=list)
    provider_metadata: dict = field(default_factory=dict)  # provider-specific residue only


REGISTERED_EVENTS: dict[str, type[BaseEventData]] = {
    EventTypes.PROVIDER_PAYOUT_RECONCILED: ProviderPayoutReconciledData,
}
