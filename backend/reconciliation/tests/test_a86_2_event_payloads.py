# reconciliation/tests/test_a86_2_event_payloads.py
"""A86.2 (2026-05-26): reconciliation event payload acceptance tests.

Asserts the 6 ReconciliationMatch*/Exception* payload dataclasses are:
- Auto-registered into events.types.EVENT_DATA_CLASSES via the
  AppConfig.event_types_module hook
- Schema-validatable through validate_event_payload() — required fields
  enforced, unexpected fields rejected, type checks honored
- Round-trip clean through to_dict() / __init__ — no field drift

Plus one architecture-load-bearing test:
- MatchProposed defaults to NOT mutating canonical match state (the
  contract that separates "AI suggestion" from "operator decision").
  A86.3 will exercise this end-to-end when the projection lands; here
  we just pin the field shape that supports it.
"""

import pytest

from events.types import (
    EVENT_DATA_CLASSES,
    EventTypes,
    InvalidEventPayload,
    validate_event_payload,
)
from reconciliation.event_types import (
    REGISTERED_EVENTS,
    ReconciliationDifferenceResolvedData,
    ReconciliationExceptionRaisedData,
    ReconciliationExceptionResolvedData,
    ReconciliationMatchConfirmedData,
    ReconciliationMatchProposedData,
    ReconciliationMatchRejectedData,
    ReconciliationMatchUnmatchedData,
)

# =============================================================================
# Registration acceptance
# =============================================================================


def test_all_seven_event_types_have_constants_on_EventTypes():
    """The EventTypes registry exposes every reconciliation event."""
    assert EventTypes.RECONCILIATION_MATCH_PROPOSED == "reconciliation.match_proposed"
    assert EventTypes.RECONCILIATION_MATCH_CONFIRMED == "reconciliation.match_confirmed"
    assert EventTypes.RECONCILIATION_MATCH_REJECTED == "reconciliation.match_rejected"
    assert EventTypes.RECONCILIATION_MATCH_UNMATCHED == "reconciliation.match_unmatched"
    # A180: resolution state is event-carried so rebuilds reproduce it.
    assert EventTypes.RECONCILIATION_DIFFERENCE_RESOLVED == "reconciliation.difference_resolved"
    assert EventTypes.RECONCILIATION_EXCEPTION_RAISED == "reconciliation.exception_raised"
    assert EventTypes.RECONCILIATION_EXCEPTION_RESOLVED == "reconciliation.exception_resolved"


def test_REGISTERED_EVENTS_maps_each_type_to_its_dataclass():
    """The module-local REGISTERED_EVENTS dict pairs each event type
    to its dataclass — this is what ProjectionsConfig.ready() discovers."""
    assert {
        EventTypes.RECONCILIATION_MATCH_PROPOSED: ReconciliationMatchProposedData,
        EventTypes.RECONCILIATION_MATCH_CONFIRMED: ReconciliationMatchConfirmedData,
        EventTypes.RECONCILIATION_MATCH_REJECTED: ReconciliationMatchRejectedData,
        EventTypes.RECONCILIATION_MATCH_UNMATCHED: ReconciliationMatchUnmatchedData,
        EventTypes.RECONCILIATION_DIFFERENCE_RESOLVED: ReconciliationDifferenceResolvedData,
        EventTypes.RECONCILIATION_EXCEPTION_RAISED: ReconciliationExceptionRaisedData,
        EventTypes.RECONCILIATION_EXCEPTION_RESOLVED: ReconciliationExceptionResolvedData,
    } == REGISTERED_EVENTS


def test_event_data_classes_globally_registered_via_app_config():
    """ProjectionsConfig.ready() merges REGISTERED_EVENTS into the
    global EVENT_DATA_CLASSES so validate_event_payload() can find
    each event type without explicit imports from the caller."""
    for event_type in REGISTERED_EVENTS:
        assert event_type in EVENT_DATA_CLASSES, (
            f"{event_type} not in EVENT_DATA_CLASSES — the AppConfig's "
            f"event_types_module attribute may be misconfigured."
        )
        assert EVENT_DATA_CLASSES[event_type] is REGISTERED_EVENTS[event_type]


# =============================================================================
# Schema validation — round-trip + happy path
# =============================================================================


def _full_proposed_payload() -> dict:
    return ReconciliationMatchProposedData(
        bank_line_public_id="bl-001",
        journal_line_public_id="jl-001",
        match_kind="settlement_clearance",
        confidence="92.5",
        proposer="auto_match_settlement_prepass_v1",
        proposed_at="2026-04-26T10:30:00+00:00",
        proposer_metadata={"matched_on": "amount+batch_id", "batch_id": "PMB-555"},
    ).to_dict()


def _full_confirmed_payload() -> dict:
    return ReconciliationMatchConfirmedData(
        bank_line_public_id="bl-001",
        journal_line_public_id="jl-001",
        match_kind="settlement_clearance",
        confidence="92.5",
        confirmation_kind="auto",
        confirmed_by_user_id=None,
        confirmed_by_email="",
        confirmed_at="2026-04-26T10:30:00+00:00",
        proposed_by_event_id="evt-abc-123",
        difference_amount="0",
        difference_reason="UNRESOLVED",
        statement_date="2026-04-26",
    ).to_dict()


def _full_rejected_payload() -> dict:
    return ReconciliationMatchRejectedData(
        bank_line_public_id="bl-001",
        journal_line_public_id="jl-001",
        rejected_by_user_id=42,
        rejected_by_email="ops@example.com",
        rejected_at="2026-04-26T11:00:00+00:00",
        rejection_reason="Not the right batch; this is for May.",
        proposed_by_event_id="evt-abc-123",
    ).to_dict()


def _full_unmatched_payload() -> dict:
    return ReconciliationMatchUnmatchedData(
        bank_line_public_id="bl-001",
        previously_matched_journal_line_public_id="jl-001",
        match_kind="settlement_clearance",
        unmatched_by_user_id=42,
        unmatched_by_email="ops@example.com",
        unmatched_at="2026-04-27T09:00:00+00:00",
        unmatch_reason="Operator selected wrong settlement; reversing.",
        final_status="UNMATCHED",
        reversed_clearance_je_public_ids=["je-clearance-001"],
        confirmed_by_event_id="evt-confirmed-001",
    ).to_dict()


def _full_exception_raised_payload() -> dict:
    return ReconciliationExceptionRaisedData(
        exception_public_id="exc-001",
        bank_line_public_id="bl-002",
        journal_entry_public_id=None,
        exception_kind="orphan_bank_deposit",
        severity="warning",
        title="Bank deposit has no matching settlement",
        detail="Bank line 2 ($1455.00, 2026-04-26) has no settlement candidate within tolerance.",
        detected_at="2026-04-27T08:00:00+00:00",
        evidence={"amount": "1455.00", "search_window_days": 7},
    ).to_dict()


def _full_exception_resolved_payload() -> dict:
    return ReconciliationExceptionResolvedData(
        exception_public_id="exc-001",
        resolved_by_user_id=42,
        resolved_by_email="ops@example.com",
        resolved_at="2026-04-27T10:00:00+00:00",
        resolution_kind="matched",
        resolution_note="Manually matched after locating the late settlement CSV.",
        related_event_ids=["evt-confirmed-002"],
    ).to_dict()


@pytest.mark.parametrize(
    "event_type,payload_builder",
    [
        (EventTypes.RECONCILIATION_MATCH_PROPOSED, _full_proposed_payload),
        (EventTypes.RECONCILIATION_MATCH_CONFIRMED, _full_confirmed_payload),
        (EventTypes.RECONCILIATION_MATCH_REJECTED, _full_rejected_payload),
        (EventTypes.RECONCILIATION_MATCH_UNMATCHED, _full_unmatched_payload),
        (EventTypes.RECONCILIATION_EXCEPTION_RAISED, _full_exception_raised_payload),
        (EventTypes.RECONCILIATION_EXCEPTION_RESOLVED, _full_exception_resolved_payload),
    ],
)
def test_full_payload_validates(event_type, payload_builder):
    """A fully-populated payload passes validate_event_payload()."""
    payload = payload_builder()
    # Should not raise.
    validate_event_payload(event_type, payload)


@pytest.mark.parametrize(
    "event_type,payload_builder",
    [
        (EventTypes.RECONCILIATION_MATCH_PROPOSED, _full_proposed_payload),
        (EventTypes.RECONCILIATION_MATCH_CONFIRMED, _full_confirmed_payload),
        (EventTypes.RECONCILIATION_MATCH_REJECTED, _full_rejected_payload),
        (EventTypes.RECONCILIATION_MATCH_UNMATCHED, _full_unmatched_payload),
        (EventTypes.RECONCILIATION_EXCEPTION_RAISED, _full_exception_raised_payload),
        (EventTypes.RECONCILIATION_EXCEPTION_RESOLVED, _full_exception_resolved_payload),
    ],
)
def test_unexpected_field_rejected(event_type, payload_builder):
    """validate_event_payload() rejects unexpected fields — strict schema.
    This catches typos in caller code at emission time."""
    payload = payload_builder()
    payload["__unexpected_field"] = "should not be here"

    with pytest.raises(InvalidEventPayload) as excinfo:
        validate_event_payload(event_type, payload)

    assert "__unexpected_field" in str(excinfo.value)


# =============================================================================
# to_dict() round-trip
# =============================================================================


def test_to_dict_preserves_all_fields_with_defaults():
    """A minimally-constructed payload (all defaults) round-trips through
    to_dict() and includes every declared field."""
    instance = ReconciliationMatchProposedData()
    d = instance.to_dict()

    expected_keys = {
        "bank_line_public_id",
        "journal_line_public_id",
        "match_kind",
        "confidence",
        "proposer",
        "proposed_at",
        "proposer_metadata",
    }
    assert set(d.keys()) == expected_keys


def test_to_dict_preserves_dict_and_list_fields():
    """proposer_metadata (dict) and reversed_clearance_je_public_ids
    (list) survive to_dict() without flattening."""
    proposed_dict = ReconciliationMatchProposedData(
        proposer_metadata={"a": 1, "nested": {"b": 2}},
    ).to_dict()
    assert proposed_dict["proposer_metadata"] == {"a": 1, "nested": {"b": 2}}

    unmatched_dict = ReconciliationMatchUnmatchedData(
        reversed_clearance_je_public_ids=["je-1", "je-2"],
    ).to_dict()
    assert unmatched_dict["reversed_clearance_je_public_ids"] == ["je-1", "je-2"]


def test_to_dict_default_dict_and_list_are_per_instance():
    """field(default_factory=dict|list) — confirm independent instances
    don't share state (the classic mutable-default bug)."""
    a = ReconciliationMatchProposedData()
    b = ReconciliationMatchProposedData()
    a.proposer_metadata["mutated"] = True
    assert "mutated" not in b.proposer_metadata


# =============================================================================
# Architectural-contract tests
# =============================================================================


def test_match_proposed_carries_no_state_mutating_fields():
    """LOAD-BEARING: MatchProposed is advisory — its payload must NOT
    contain fields that imply canonical state change (match_status,
    confirmed_at, confirmed_by, etc.). The projection in A86.3 enforces
    that only Confirmed/Rejected/Unmatched mutate read-model state;
    this test pins the dataclass shape that supports it.

    If you find yourself adding a 'confirmed_*' or 'matched_at' field
    here, you're confusing a suggestion with a decision — stop and
    re-read ENGINEERING_PROTOCOL.md §1.5 (auditability beats convenience).
    """
    from dataclasses import fields as dataclass_fields

    forbidden_field_substrings = {
        "confirmed_at",
        "confirmed_by",
        "confirmation_",
        "matched_at",
        "matched_by",
        "match_status",
        "reconciled",
    }
    proposed_field_names = {f.name for f in dataclass_fields(ReconciliationMatchProposedData)}
    leaks = {name for name in proposed_field_names if any(substr in name for substr in forbidden_field_substrings)}
    assert leaks == set(), (
        f"MatchProposed must not carry state-mutating fields. Found: {leaks}. "
        f"A proposal is evidence; only Confirmed/Rejected/Unmatched are canonical decisions."
    )


def test_match_confirmed_links_back_to_proposed_for_audit_chain():
    """Confirmed carries proposed_by_event_id so a confirmation that
    followed a Proposed (e.g., operator accepted the suggestion) keeps
    the audit chain intact."""
    confirmed = ReconciliationMatchConfirmedData(
        bank_line_public_id="bl-1",
        journal_line_public_id="jl-1",
        match_kind="settlement_clearance",
        confirmation_kind="manual",
        proposed_by_event_id="evt-proposed-7",
    )
    d = confirmed.to_dict()
    assert d["proposed_by_event_id"] == "evt-proposed-7"


def test_exception_resolved_links_back_to_raised():
    """ExceptionResolved carries exception_public_id (the Raised
    event's stable UUID) so the projection can pair them on replay."""
    resolved = ReconciliationExceptionResolvedData(
        exception_public_id="exc-9",
        resolution_kind="matched",
        resolution_note="Resolved by manual match — see related_event_ids.",
    )
    d = resolved.to_dict()
    assert d["exception_public_id"] == "exc-9"


def test_confirmation_kind_supports_all_four_paths():
    """confirmation_kind discriminates the four confirmation paths
    A86.4-A86.6 will route through. The dataclass accepts the strings;
    enum tightening (if desired) lands in policies.py rather than the
    payload schema (matches BaseEventData's no-enum convention)."""
    for kind in ("auto", "manual", "rule", "platform_payout_reconcile"):
        confirmed = ReconciliationMatchConfirmedData(
            bank_line_public_id="bl-1",
            journal_line_public_id="jl-1",
            match_kind="settlement_clearance",
            confirmation_kind=kind,
        )
        d = confirmed.to_dict()
        assert d["confirmation_kind"] == kind
