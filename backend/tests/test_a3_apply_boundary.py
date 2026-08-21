# tests/test_a3_apply_boundary.py
"""A3-PR3: the apply/replay boundary — invariant enforcement at projection
apply time.

Covers, per the founder decisions of 2026-08-21:

- the process_pending choke point: every JOURNAL_ENTRY_POSTED event passes
  ``check_posted_journal(mode="apply")`` before any handler runs; violations
  QUARANTINE (ProjectionFailureLog + advance, stream flows) — D1/D2 strict,
  no cutover state;
- cross-projection consistency: every consumer of the event gets the same
  verdict — a bad event materializes into NO read model;
- the emit→apply subset property: a payload prepared by the emit boundary
  can never fail apply;
- sibling guards (D5): reversed shape guard (no more KeyError halts),
  deleted posted-target refusal, chunk-family consume-to-quarantine;
- line identity (D4): the posted payload's ``line_no`` owns
  ``derive_journal_line_public_id`` inputs; sequential fallback when absent;
  draft events keep their tolerant contract;
- rebuild over corrupt history completes without stalling (quarantine
  advances).

Malformed payloads are constructed by emitting a schema-valid event and then
corrupting the stored inline payload via ``queryset.update`` — exactly the
at-rest-corruption / foreign-stream surface the apply boundary exists to
catch (inline payloads carry no hash; the emit boundary cannot help a
payload that changed after emission).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.models import JournalEntry
from accounting.posted_journal_apply import (
    APPLY_CHUNKED_JOURNAL_UNSUPPORTED,
    APPLY_DELETE_TARGET_POSTED,
    APPLY_ENTRY_REF_INVALID,
    APPLY_UNREADABLE_PAYLOAD,
    PostedJournalApplyInvalid,
    apply_validator_map,
    evaluate_posted_journal_for_apply,
)
from events.emitter import emit_event
from events.models import BusinessEvent
from events.types import EventTypes
from projections.account_balance import AccountBalanceProjection
from projections.accounting import JournalEntryProjection, derive_journal_line_public_id
from projections.apply_validation import get_apply_validator, registered_apply_event_types
from projections.base import projection_registry
from projections.dimension_balance import DimensionBalanceProjection
from projections.models import AccountBalance, ProjectionAppliedEvent, ProjectionFailureLog
from projections.period_balance import PeriodAccountBalanceProjection
from projections.subledger_balance import SubledgerBalanceProjection

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

JE_CONSUMERS = [
    JournalEntryProjection,
    AccountBalanceProjection,
    PeriodAccountBalanceProjection,
    SubledgerBalanceProjection,
    DimensionBalanceProjection,
]


def _posted_payload(entry_public_id, user, cash_account, revenue_account, amount="100.00"):
    return {
        "entry_public_id": str(entry_public_id),
        "entry_number": "JE-APPLY-1",
        "date": date.today().isoformat(),
        "memo": "apply boundary test",
        "kind": "NORMAL",
        "posted_at": "2026-01-01T12:00:00",
        "posted_by_id": user.id,
        "posted_by_email": user.email,
        "period": 1,
        "total_debit": amount,
        "total_credit": amount,
        "lines": [
            {
                "line_no": 1,
                "account_public_id": str(cash_account.public_id),
                "description": "cash",
                "debit": amount,
                "credit": "0.00",
            },
            {
                "line_no": 2,
                "account_public_id": str(revenue_account.public_id),
                "description": "revenue",
                "debit": "0.00",
                "credit": amount,
            },
        ],
    }


def _emit_posted(company, user, payload):
    return emit_event(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=payload["entry_public_id"],
        data=payload,
        caused_by_user=user,
        idempotency_key=f"apply-test:posted:{payload['entry_public_id']}",
    )


def _corrupt(event, new_data):
    """Simulate at-rest corruption / a foreign stream: rewrite the stored
    inline payload underneath the emitted event."""
    BusinessEvent.objects.filter(pk=event.pk).update(data=new_data)
    return BusinessEvent.objects.get(pk=event.pk)


def _drain_all(company):
    for projection_cls in JE_CONSUMERS:
        projection_cls().process_pending(company)


# --------------------------------------------------------------------------- #
# Registration + wiring
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestApplyValidatorWiring:
    def test_journal_family_validators_registered(self):
        """App-ready registered the complete journal-family validator map."""
        expected = apply_validator_map()
        assert set(expected) <= registered_apply_event_types()
        for event_type, fn in expected.items():
            assert get_apply_validator(event_type) is fn

    def test_read_models_precede_balance_projections(self):
        """A3-PR3 registry-order fix: the Account and JournalEntry read models
        must apply before every balance projection in each drain pass, or a
        fresh-database replay quarantines on spurious JE_ACCOUNT_UNKNOWN."""
        order = [p.name for p in projection_registry.all()]
        for read_model in ("account_read_model", "journal_entry_read_model"):
            for balance in ("account_balance", "period_account_balance", "subledger_balance", "dimension_balance"):
                assert order.index(read_model) < order.index(balance), (
                    f"{read_model} must register before {balance}; got {order}"
                )
        assert order.index("fiscal_period_read_model") < order.index("period_account_balance")


# --------------------------------------------------------------------------- #
# The choke point: valid events apply, violations quarantine
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestPostedApplyEnforcement:
    def test_valid_posted_event_applies_everywhere(self, company, user, cash_account, revenue_account):
        entry_id = uuid4()
        _emit_posted(company, user, _posted_payload(entry_id, user, cash_account, revenue_account))
        _drain_all(company)

        entry = JournalEntry.objects.get(company=company, public_id=entry_id)
        assert entry.status == JournalEntry.Status.POSTED
        assert entry.lines.count() == 2
        assert AccountBalance.objects.get(company=company, account=cash_account).debit_total == Decimal("100.00")
        assert not ProjectionFailureLog.objects.filter(company=company).exists()

    def test_unbalanced_event_quarantines_in_every_consumer(self, company, user, cash_account, revenue_account):
        """The headline D2 case: a historical unbalanced payload (the A194
        class) is quarantined by EVERY consuming projection — visible failure
        log, event consumed, zero rows anywhere, stream keeps flowing."""
        entry_id = uuid4()
        payload = _posted_payload(entry_id, user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        bad = dict(payload)
        bad["lines"] = [dict(payload["lines"][0]), dict(payload["lines"][1], credit="90.00")]
        event = _corrupt(event, bad)

        # A later, valid event must still apply (no head-of-line stall).
        good_id = uuid4()
        _emit_posted(company, user, _posted_payload(good_id, user, cash_account, revenue_account, amount="70.00"))

        _drain_all(company)

        # The bad entry materialized NOWHERE.
        assert not JournalEntry.objects.filter(company=company, public_id=entry_id).exists()
        cash = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash.debit_total == Decimal("70.00")  # only the good event

        # Quarantined by every consumer: failure log + applied marker each.
        for projection_cls in JE_CONSUMERS:
            name = projection_cls().name
            log = ProjectionFailureLog.objects.get(company=company, projection_name=name, event=event)
            assert "JE_UNBALANCED" in log.message
            assert log.category == ProjectionFailureLog.Category.MISSING_CONFIG
            assert ProjectionAppliedEvent.objects.filter(company=company, projection_name=name, event=event).exists()

        # The good entry applied fully.
        assert JournalEntry.objects.filter(company=company, public_id=good_id).exists()

    def test_quarantined_event_not_reattempted(self, company, user, cash_account, revenue_account):
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        _corrupt(event, dict(payload, lines=1))  # malformed container

        projection = JournalEntryProjection()
        projection.process_pending(company)
        projection.process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name)
        assert log.occurrence_count == 1  # consumed, not retried
        assert "JE_AMOUNT_INVALID" in log.message

    def test_unknown_account_quarantines(self, company, user, cash_account, revenue_account):
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        bad = dict(payload)
        bad["lines"] = [dict(payload["lines"][0], account_public_id=str(uuid4())), dict(payload["lines"][1])]
        _corrupt(event, bad)

        AccountBalanceProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name="account_balance")
        assert "JE_ACCOUNT_UNKNOWN" in log.message
        assert not AccountBalance.objects.filter(company=company).exists()

    def test_cross_company_account_quarantines(self, company, second_company, user, cash_account, revenue_account):
        from accounting.models import Account

        foreign = Account.objects.create(
            public_id=uuid4(),
            company=second_company,
            code="1000",
            name="Foreign Cash",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        bad = dict(payload)
        bad["lines"] = [dict(payload["lines"][0], account_public_id=str(foreign.public_id)), dict(payload["lines"][1])]
        _corrupt(event, bad)

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name="journal_entry_read_model")
        assert "JE_ACCOUNT_CROSS_COMPANY" in log.message

    def test_non_dict_payload_is_unreadable(self, company, user, cash_account, revenue_account):
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        _corrupt(event, [1, 2, 3])

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name="journal_entry_read_model")
        assert APPLY_UNREADABLE_PAYLOAD in log.message

    def test_raising_get_data_is_unreadable(self, company, user, cash_account, revenue_account):
        """External-payload integrity failure → the runtime twin of
        SCANNER_UNREADABLE_PAYLOAD, via the real get_data raise path."""
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        # external storage with no payload_ref → get_data raises IntegrityError
        BusinessEvent.objects.filter(pk=event.pk).update(payload_storage="external")
        event = BusinessEvent.objects.get(pk=event.pk)

        assert evaluate_posted_journal_for_apply(event) == [APPLY_UNREADABLE_PAYLOAD]

        JournalEntryProjection().process_pending(company)
        log = ProjectionFailureLog.objects.get(company=company, projection_name="journal_entry_read_model")
        assert APPLY_UNREADABLE_PAYLOAD in log.message

    def test_invariant_clean_payload_without_entry_id_quarantines(self, company, user, cash_account, revenue_account):
        """The posted door's identity-ref guard (same family as the
        reversed/deleted guards): an invariant-CLEAN payload whose
        entry_public_id is missing/malformed cannot materialize an entry —
        pre-PR3 it KeyError-halted the whole stream."""
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        bad = dict(payload)
        del bad["entry_public_id"]
        _corrupt(event, bad)

        good_id = uuid4()
        _emit_posted(company, user, _posted_payload(good_id, user, cash_account, revenue_account))

        projection = JournalEntryProjection()
        projection.process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name, event_id=event.id)
        assert APPLY_ENTRY_REF_INVALID in log.message
        assert JournalEntry.objects.filter(company=company, public_id=good_id).exists()

    def test_emit_prepared_payload_always_passes_apply(self, company, user, cash_account, revenue_account):
        """The subset property that makes D2 strict safe: apply-mode checks
        are a subset of emit-mode checks, so a payload the emit boundary
        prepared can never fail the apply boundary."""
        from accounting.journal_invariant import prepare_posted_journal_for_emit

        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        # A caller-supplied wrong memo flag is normalized by preparation.
        payload["lines"][0]["is_memo_line"] = True
        prepared = prepare_posted_journal_for_emit(company, payload)
        event = _emit_posted(company, user, prepared)

        assert evaluate_posted_journal_for_apply(event) == []


# --------------------------------------------------------------------------- #
# Sibling guards (D5)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestSiblingGuards:
    def _post_entry(self, company, user, cash_account, revenue_account):
        entry_id = uuid4()
        _emit_posted(company, user, _posted_payload(entry_id, user, cash_account, revenue_account))
        JournalEntryProjection().process_pending(company)
        return JournalEntry.objects.get(company=company, public_id=entry_id)

    def test_malformed_reversed_quarantines_instead_of_halting(self, company, user, cash_account, revenue_account):
        """Pre-PR3 a payload missing original_entry_public_id raised KeyError
        → whole-projection halt. Now: structured quarantine, stream flows."""
        event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
            aggregate_type="JournalEntry",
            aggregate_id=str(uuid4()),
            data={
                "original_entry_public_id": str(uuid4()),
                "reversal_entry_public_id": str(uuid4()),
                "reversed_at": "2026-01-01T12:00:00",
                "reversed_by_id": user.id,
                "reversed_by_email": user.email,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:reversed:{uuid4()}",
        )
        _corrupt(event, {"reversal_entry_public_id": "not-a-uuid"})

        # A later valid posted event must still apply in the same pass.
        good_id = uuid4()
        _emit_posted(company, user, _posted_payload(good_id, user, cash_account, revenue_account))

        projection = JournalEntryProjection()
        projection.process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name, event_id=event.id)
        assert APPLY_ENTRY_REF_INVALID in log.message
        assert JournalEntry.objects.filter(company=company, public_id=good_id).exists()

    def test_valid_reversed_still_applies(self, company, user, cash_account, revenue_account):
        original = self._post_entry(company, user, cash_account, revenue_account)
        reversal = self._post_entry(company, user, revenue_account, cash_account)

        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
            aggregate_type="JournalEntry",
            aggregate_id=str(original.public_id),
            data={
                "original_entry_public_id": str(original.public_id),
                "reversal_entry_public_id": str(reversal.public_id),
                "reversed_at": "2026-01-02T12:00:00",
                "reversed_by_id": user.id,
                "reversed_by_email": user.email,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:reversed:{original.public_id}",
        )
        JournalEntryProjection().process_pending(company)

        original.refresh_from_db()
        assert original.status == JournalEntry.Status.REVERSED

    def test_delete_of_posted_entry_quarantines(self, company, user, cash_account, revenue_account):
        entry = self._post_entry(company, user, cash_account, revenue_account)

        event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_DELETED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry.public_id),
            data={
                "entry_public_id": str(entry.public_id),
                "date": date.today().isoformat(),
                "memo": "hostile delete",
                "status": "POSTED",
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:deleted:{entry.public_id}",
        )
        JournalEntryProjection().process_pending(company)

        # The posted entry SURVIVES in the read model; the event quarantined.
        assert JournalEntry.objects.filter(company=company, public_id=entry.public_id).exists()
        log = ProjectionFailureLog.objects.get(
            company=company, projection_name="journal_entry_read_model", event_id=event.id
        )
        assert APPLY_DELETE_TARGET_POSTED in log.message

    def test_delete_of_draft_entry_applies(self, company, user):
        entry_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={
                "entry_public_id": str(entry_id),
                "date": date.today().isoformat(),
                "memo": "draft",
                "status": "INCOMPLETE",
                "period": 1,
                "line_count": 0,
                "created_by_id": user.id,
                "created_by_email": user.email,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:created:{entry_id}",
        )
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_DELETED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={
                "entry_public_id": str(entry_id),
                "date": date.today().isoformat(),
                "memo": "draft",
                "status": "INCOMPLETE",
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:deleted:{entry_id}",
        )
        JournalEntryProjection().process_pending(company)

        assert not JournalEntry.objects.filter(company=company, public_id=entry_id).exists()
        assert not ProjectionFailureLog.objects.filter(company=company).exists()

    def test_chunk_event_quarantines_in_both_balance_projections(self, company, user, cash_account):
        event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_LINES_CHUNK_ADDED,
            aggregate_type="JournalEntry",
            aggregate_id=str(uuid4()),
            data={
                "journal_entry_id": str(uuid4()),
                "company_public_id": str(company.public_id),
                "chunk_index": 0,
                "total_chunks": 1,
                "lines": [
                    {
                        "line_no": 1,
                        "account_public_id": str(cash_account.public_id),
                        "debit": "10.00",
                        "credit": "0.00",
                    }
                ],
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:chunk:{uuid4()}",
        )

        AccountBalanceProjection().process_pending(company)
        SubledgerBalanceProjection().process_pending(company)

        assert not AccountBalance.objects.filter(company=company).exists()
        for name in ("account_balance", "subledger_balance"):
            log = ProjectionFailureLog.objects.get(company=company, projection_name=name, event_id=event.id)
            assert APPLY_CHUNKED_JOURNAL_UNSUPPORTED in log.message


# --------------------------------------------------------------------------- #
# Line identity (D4)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestPostedLineIdentity:
    def test_payload_line_no_owns_identity(self, company, user, cash_account, revenue_account):
        entry_id = uuid4()
        payload = _posted_payload(entry_id, user, cash_account, revenue_account)
        payload["lines"][0]["line_no"] = 7
        payload["lines"][1]["line_no"] = 3
        _emit_posted(company, user, payload)

        JournalEntryProjection().process_pending(company)

        entry = JournalEntry.objects.get(company=company, public_id=entry_id)
        lines = {line.line_no: line for line in entry.lines.all()}
        assert set(lines) == {7, 3}
        assert lines[7].public_id == derive_journal_line_public_id(entry.public_id, 7)
        assert lines[3].public_id == derive_journal_line_public_id(entry.public_id, 3)
        assert lines[7].debit == Decimal("100.00")
        assert lines[3].credit == Decimal("100.00")

    def test_missing_line_no_falls_back_to_sequential(self, company, user, cash_account, revenue_account):
        entry_id = uuid4()
        payload = _posted_payload(entry_id, user, cash_account, revenue_account)
        del payload["lines"][1]["line_no"]
        event = _emit_posted(company, user, payload)
        # emit_event schema tolerates the missing key only via corruption
        _corrupt(event, payload)

        JournalEntryProjection().process_pending(company)

        entry = JournalEntry.objects.get(company=company, public_id=entry_id)
        assert sorted(line.line_no for line in entry.lines.all()) == [1, 2]

    def test_draft_events_keep_tolerant_contract(self, company, user, cash_account):
        """CREATED carries no invariant: incomplete lines still skip, lines
        still renumber sequentially — unchanged draft behavior."""
        entry_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={
                "entry_public_id": str(entry_id),
                "date": date.today().isoformat(),
                "memo": "draft",
                "status": "INCOMPLETE",
                "period": 1,
                "line_count": 2,
                "created_by_id": user.id,
                "created_by_email": user.email,
                "lines": [
                    {"line_no": 5, "account_public_id": "", "debit": "1.00", "credit": "0.00"},  # no account: skipped
                    {
                        "line_no": 9,
                        "account_public_id": str(cash_account.public_id),
                        "debit": "1.00",
                        "credit": "0.00",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:draft:{entry_id}",
        )
        JournalEntryProjection().process_pending(company)

        entry = JournalEntry.objects.get(company=company, public_id=entry_id)
        line = entry.lines.get()
        assert line.line_no == 1  # renumbered, not payload 9
        assert not ProjectionFailureLog.objects.filter(company=company).exists()

    def test_validated_impossible_state_raises_loudly(self, company, user, cash_account, revenue_account):
        """Defense-in-depth: _replace_lines(validated_posted=True) on a payload
        that somehow bypassed validation raises instead of dropping lines."""
        entry = JournalEntry.objects.projection().create(
            company=company,
            public_id=uuid4(),
            date=date.today(),
            period=1,
            status=JournalEntry.Status.POSTED,
        )
        with pytest.raises(ValueError, match="A3-PR3 internal invariant"):
            JournalEntryProjection()._replace_lines(
                entry,
                [{"line_no": 1, "account_public_id": str(uuid4()), "debit": "1.00", "credit": "0.00"}],
                validated_posted=True,
            )


# --------------------------------------------------------------------------- #
# Rebuild over corrupt history (D2: quarantine advances, no stall)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestRebuildOverCorruptHistory:
    def test_rebuild_completes_and_quarantines(self, company, user, cash_account, revenue_account):
        good1 = uuid4()
        _emit_posted(company, user, _posted_payload(good1, user, cash_account, revenue_account, amount="10.00"))

        bad_payload = _posted_payload(uuid4(), user, cash_account, revenue_account, amount="20.00")
        bad_event = _emit_posted(company, user, bad_payload)
        corrupted = dict(bad_payload)
        corrupted["lines"] = [dict(bad_payload["lines"][0]), dict(bad_payload["lines"][1], credit="19.00")]
        _corrupt(bad_event, corrupted)

        good2 = uuid4()
        _emit_posted(company, user, _posted_payload(good2, user, cash_account, revenue_account, amount="30.00"))

        projection = JournalEntryProjection()
        projection.process_pending(company)
        # Now rebuild from scratch — the corrupt event must not stall the drain.
        projection.rebuild(company)

        assert projection.get_lag(company) == 0
        assert JournalEntry.objects.filter(company=company, public_id=good1).exists()
        assert JournalEntry.objects.filter(company=company, public_id=good2).exists()
        assert not JournalEntry.objects.filter(company=company, public_id=bad_payload["entry_public_id"]).exists()
        log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name, event_id=bad_event.id)
        assert "JE_UNBALANCED" in log.message


# --------------------------------------------------------------------------- #
# Evaluator unit behavior
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestEvaluator:
    def test_facts_cache_is_shared_across_events(self, company, user, cash_account, revenue_account):
        payload1 = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload2 = _posted_payload(uuid4(), user, cash_account, revenue_account)
        e1 = _emit_posted(company, user, payload1)
        e2 = _emit_posted(company, user, payload2)

        cache: dict = {}
        assert evaluate_posted_journal_for_apply(e1, facts_cache=cache) == []
        cached_keys = set(cache)
        assert evaluate_posted_journal_for_apply(e2, facts_cache=cache) == []
        assert set(cache) == cached_keys  # nothing re-queried / re-added

    def test_validator_exception_carries_codes(self, company, user, cash_account, revenue_account):
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        _corrupt(event, dict(payload, lines=1))

        from accounting.posted_journal_apply import validate_posted_journal_apply

        with pytest.raises(PostedJournalApplyInvalid) as exc_info:
            validate_posted_journal_apply(BusinessEvent.objects.get(pk=event.pk))
        assert exc_info.value.codes == ["JE_AMOUNT_INVALID"]
        assert exc_info.value.fix_hint

    def test_evaluator_verdict_includes_identity_guards(self, company, user, cash_account, revenue_account):
        """The identity guards live in the EVALUATOR, so restore verification
        and audit_event_first apply the exact verdict the choke point applies
        — no weaker batch verdict."""
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)

        no_id = dict(payload)
        del no_id["entry_public_id"]
        assert evaluate_posted_journal_for_apply(_corrupt(event, no_id)) == [APPLY_ENTRY_REF_INVALID]

        bad_line_no = dict(payload)
        bad_line_no["lines"] = [dict(payload["lines"][0], line_no=-1), dict(payload["lines"][1])]
        assert evaluate_posted_journal_for_apply(_corrupt(event, bad_line_no)) == [APPLY_ENTRY_REF_INVALID]

        overflow = dict(payload)
        overflow["lines"] = [dict(payload["lines"][0], line_no=2**31), dict(payload["lines"][1])]
        assert evaluate_posted_journal_for_apply(_corrupt(event, overflow)) == [APPLY_ENTRY_REF_INVALID]

    def test_transient_db_error_propagates_as_retryable(self):
        """A transient database error during the lazy payload fetch must NOT
        be misclassified as at-rest corruption — it propagates and takes the
        ordinary retryable-halt path, never terminal quarantine. Only the
        documented get_data() failure contract (IntegrityError/ValueError)
        maps to APPLY_UNREADABLE_PAYLOAD."""
        from django.db import IntegrityError, OperationalError

        class _RaisingEvent:
            company_id = 0

            def __init__(self, exc):
                self._exc = exc

            def get_data(self):
                raise self._exc

        with pytest.raises(OperationalError):
            evaluate_posted_journal_for_apply(_RaisingEvent(OperationalError("statement timeout")))
        assert evaluate_posted_journal_for_apply(_RaisingEvent(IntegrityError("hash mismatch"))) == [
            APPLY_UNREADABLE_PAYLOAD
        ]
        assert evaluate_posted_journal_for_apply(_RaisingEvent(ValueError("chunk misuse"))) == [
            APPLY_UNREADABLE_PAYLOAD
        ]


# --------------------------------------------------------------------------- #
# Out-of-range line_no: quarantine, never an IntegrityError head-of-line halt
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestLineNoStorabilityGuard:
    def test_out_of_range_line_no_quarantines_instead_of_halting(self, company, user, cash_account, revenue_account):
        """D4 made payload line_no the line-identity input; an unstorable
        value (negative / beyond the 32-bit column) previously would have
        reached bulk_create and IntegrityError-halted the whole stream."""
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        bad = dict(payload)
        bad["lines"] = [dict(payload["lines"][0], line_no=-1), dict(payload["lines"][1], line_no=2)]
        _corrupt(event, bad)

        good_id = uuid4()
        _emit_posted(company, user, _posted_payload(good_id, user, cash_account, revenue_account))

        projection = JournalEntryProjection()
        projection.process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, projection_name=projection.name, event_id=event.id)
        assert APPLY_ENTRY_REF_INVALID in log.message
        assert not JournalEntry.objects.filter(company=company, public_id=payload["entry_public_id"]).exists()
        assert JournalEntry.objects.filter(company=company, public_id=good_id).exists()  # stream flowed


# --------------------------------------------------------------------------- #
# JE_ACCOUNT_UNKNOWN with prior in-stream account evidence: defer, not terminal
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestAccountUnknownDefersOnReadModelLag:
    def test_pending_materialization_defers_then_applies(self, company, user, cash_account, revenue_account):
        """A limit-bounded drain can validate a posted event before the
        Account read model materialized a referenced account's row. With the
        account.created event earlier in the stream, the verdict is
        DeferEvent (invisible retry) — never terminal quarantine of a valid
        event. Once the account read model catches up, the event applies."""
        from accounting.models import Account
        from projections.accounting import AccountProjection

        new_account_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(new_account_id),
            data={
                "account_public_id": str(new_account_id),
                "code": "1099",
                "name": "Late-materialized cash",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:acct:{new_account_id}",
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(new_account_id)
        event = _emit_posted(company, user, payload)

        # Balance projection runs FIRST — the account row does not exist yet.
        balances = AccountBalanceProjection()
        balances.process_pending(company)

        assert not ProjectionFailureLog.objects.filter(company=company, event_id=event.id).exists()
        assert not ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=balances.name, event_id=event.id
        ).exists()  # deferred: not consumed, not quarantined
        assert not AccountBalance.objects.filter(company=company).exists()

        # The account read model catches up; the deferred event now applies.
        AccountProjection().process_pending(company)
        assert Account.objects.filter(company=company, public_id=new_account_id).exists()
        balances.process_pending(company)

        new_balance = AccountBalance.objects.get(company=company, account__public_id=new_account_id)
        assert new_balance.debit_total == Decimal("100.00")

    def test_pending_account_found_by_payload_identity(self, company, user, cash_account, revenue_account):
        """Codex round-8 P2: an account event whose payload creates X but
        whose aggregate metadata names another id still counts as pending
        evidence for a posted journal referencing X — an aggregate-only
        query would terminally quarantine a valid event that a retry after
        the account projection would apply."""
        from accounting.models import Account
        from projections.accounting import AccountProjection

        new_account_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(uuid4()),  # mismatched aggregate metadata
            data={
                "account_public_id": str(new_account_id),
                "code": "1086",
                "name": "Payload-identity account",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:mismatch-acct:{new_account_id}",
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(new_account_id)
        event = _emit_posted(company, user, payload)

        balances = AccountBalanceProjection()
        balances.process_pending(company)

        # Deferred (found via payload identity), never quarantined.
        assert not ProjectionFailureLog.objects.filter(company=company, event_id=event.id).exists()
        assert not ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=balances.name, event_id=event.id
        ).exists()

        AccountProjection().process_pending(company)
        assert Account.objects.filter(company=company, public_id=new_account_id).exists()
        balances.process_pending(company)

        assert AccountBalance.objects.get(company=company, account__public_id=new_account_id).debit_total == Decimal(
            "100.00"
        )

    def test_genuinely_unknown_account_stays_terminal(self, company, user, cash_account, revenue_account):
        """No prior account event in the stream = a genuine unknown reference:
        terminal quarantine, exactly as before the defer refinement."""
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        bad = dict(payload)
        bad["lines"] = [dict(payload["lines"][0], account_public_id=str(uuid4())), dict(payload["lines"][1])]
        _corrupt(event, bad)

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, event_id=event.id)
        assert "JE_ACCOUNT_UNKNOWN" in log.message

    def test_aggregate_id_without_creating_payload_stays_terminal(self, company, user, cash_account, revenue_account):
        """Codex round-1 P2: an account event whose aggregate_id matches the
        reference but whose PAYLOAD creates a different id is not evidence —
        the AccountProjection materializes the payload id, so the reference
        would never resolve and trusting aggregate metadata would defer it
        forever. Payload-verified: this stays terminal quarantine."""
        phantom_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(phantom_id),  # metadata claims phantom_id...
            data={
                "account_public_id": str(uuid4()),  # ...payload creates a DIFFERENT id
                "code": "1098",
                "name": "Mismatched aggregate",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:phantom:{phantom_id}",
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(phantom_id)
        event = _emit_posted(company, user, payload)
        _corrupt(event, payload)  # bypass any emit-side normalization concerns

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, event_id=event.id)
        assert "JE_ACCOUNT_UNKNOWN" in log.message

    def test_batch_callers_share_the_defer_verdict(self, company, user, cash_account, revenue_account):
        """Codex round-1 P2 (verdict symmetry): the audit must not report the
        replayable-lag state as critically corrupt — the shared predicate
        gives every consumer the choke point's disposition."""
        from accounting.management.commands.audit_event_first import Command as AuditCommand
        from accounting.posted_journal_apply import is_deferrable_apply_verdict

        new_account_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(new_account_id),
            data={
                "account_public_id": str(new_account_id),
                "code": "1097",
                "name": "Lagging account",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:lag:{new_account_id}",
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(new_account_id)
        event = _emit_posted(company, user, payload)

        codes = evaluate_posted_journal_for_apply(event)
        assert codes == ["JE_ACCOUNT_UNKNOWN"]
        assert is_deferrable_apply_verdict(event, codes)

        check = AuditCommand()._check_event_payload_integrity(company)
        assert check["count"] == 0, check["details"]


# --------------------------------------------------------------------------- #
# Lifecycle ordering under defer (Codex round-5 P1): sibling events wait for
# their pending posted referents instead of no-op-consuming
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestLifecycleOrderingUnderDefer:
    def _emit_lagging_account(self, company, user, code="1091"):
        lag_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(lag_id),
            data={
                "account_public_id": str(lag_id),
                "code": code,
                "name": f"Lagging {code}",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:lifecycle-acct:{lag_id}",
        )
        return lag_id

    def test_reversal_defers_until_posted_pair_materializes(self, company, user, cash_account, revenue_account):
        """Both POSTED events of a reversal pair defer on a lagging account;
        the REVERSED event must defer WITH them — a silent no-op consume
        would lose the reversal relationship permanently once the posts
        retry."""
        from projections.accounting import AccountProjection

        lag_id = self._emit_lagging_account(company, user)

        original_id, reversal_id = uuid4(), uuid4()
        for entry_id, flip in ((original_id, False), (reversal_id, True)):
            payload = _posted_payload(entry_id, user, cash_account, revenue_account)
            payload["lines"][0]["account_public_id"] = str(lag_id)
            if flip:  # the reversal carries swapped sides
                payload["lines"][0], payload["lines"][1] = (
                    dict(payload["lines"][1], line_no=1),
                    dict(payload["lines"][0], line_no=2),
                )
            _emit_posted(company, user, payload)
        reversed_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
            aggregate_type="JournalEntry",
            aggregate_id=str(original_id),
            data={
                "original_entry_public_id": str(original_id),
                "reversal_entry_public_id": str(reversal_id),
                "reversed_at": "2026-01-03T12:00:00",
                "reversed_by_id": user.id,
                "reversed_by_email": user.email,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:lifecycle-rev:{original_id}",
        )

        projection = JournalEntryProjection()
        projection.process_pending(company)

        # Everything deferred: nothing consumed, nothing quarantined.
        assert not ProjectionAppliedEvent.objects.filter(company=company, projection_name=projection.name).exists()
        assert not ProjectionFailureLog.objects.filter(company=company).exists()

        AccountProjection().process_pending(company)
        projection.process_pending(company)

        original = JournalEntry.objects.get(company=company, public_id=original_id)
        assert original.status == JournalEntry.Status.REVERSED
        reversal = JournalEntry.objects.get(company=company, public_id=reversal_id)
        assert reversal.reverses_entry_id == original.id
        assert ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=projection.name, event=reversed_event
        ).exists()

    def test_line_analysis_defers_behind_pending_post(self, company, user, cash_account, revenue_account):
        """Codex round-6 P1: the analysis event must not no-op-consume while
        its journal's own posted event is deferred — the retried post would
        otherwise create permanently untagged lines."""
        from projections.accounting import AccountProjection

        lag_id = self._emit_lagging_account(company, user, code="1089")
        entry_id = uuid4()
        payload = _posted_payload(entry_id, user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(lag_id)
        _emit_posted(company, user, payload)
        analysis_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_LINE_ANALYSIS_SET,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={"entry_public_id": str(entry_id), "line_no": 1, "analysis_tags": []},
            caused_by_user=user,
            idempotency_key=f"apply-test:analysis:{entry_id}",
        )

        projection = JournalEntryProjection()
        projection.process_pending(company)
        assert not ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=projection.name, event=analysis_event
        ).exists()
        assert not ProjectionFailureLog.objects.filter(company=company).exists()

        AccountProjection().process_pending(company)
        projection.process_pending(company)

        assert JournalEntry.objects.get(company=company, public_id=entry_id).lines.count() == 2
        assert ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=projection.name, event=analysis_event
        ).exists()

    def test_pending_post_found_by_payload_identity(self, company, user, cash_account, revenue_account):
        """Codex round-6 P2: a posted event whose aggregate metadata names a
        DIFFERENT id (external ingest legitimately does this) must still be
        found as the pending post for a lifecycle event targeting the
        payload's entry id."""
        from projections.accounting import AccountProjection

        lag_id = self._emit_lagging_account(company, user, code="1088")
        original_id = uuid4()
        payload = _posted_payload(original_id, user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(lag_id)
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(uuid4()),  # mismatched aggregate metadata
            data=payload,
            caused_by_user=user,
            idempotency_key=f"apply-test:mismatch:{original_id}",
        )
        reversal_id = uuid4()
        _emit_posted(company, user, _posted_payload(reversal_id, user, revenue_account, cash_account))
        reversed_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
            aggregate_type="JournalEntry",
            aggregate_id=str(original_id),
            data={
                "original_entry_public_id": str(original_id),
                "reversal_entry_public_id": str(reversal_id),
                "reversed_at": "2026-01-04T12:00:00",
                "reversed_by_id": user.id,
                "reversed_by_email": user.email,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:mismatch-rev:{original_id}",
        )

        projection = JournalEntryProjection()
        projection.process_pending(company)
        # The reversal deferred (its original's post is pending, found via
        # PAYLOAD identity despite the mismatched aggregate metadata).
        assert not ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=projection.name, event=reversed_event
        ).exists()

        AccountProjection().process_pending(company)
        projection.process_pending(company)

        original = JournalEntry.objects.get(company=company, public_id=original_id)
        assert original.status == JournalEntry.Status.REVERSED

    def test_pending_post_found_in_external_payload_storage(self, company, user, cash_account, revenue_account):
        """Codex round-7 P2: a >64KiB ingested post stores {} inline — the
        pending-post lookup must reach the EXTERNAL payload store
        (payload_ref__payload) when the aggregate metadata also mismatches,
        or the lifecycle event no-op-consumes past a genuinely pending
        post."""
        from events.models import EventPayload
        from projections.accounting import AccountProjection

        lag_id = self._emit_lagging_account(company, user, code="1087")
        original_id = uuid4()
        payload = _posted_payload(original_id, user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(lag_id)
        posted = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(uuid4()),  # mismatched aggregate metadata
            data=payload,
            caused_by_user=user,
            idempotency_key=f"apply-test:ext:{original_id}",
        )
        # Convert to external storage: {} inline, payload in the store.
        ep = EventPayload.objects.create(
            content_hash=uuid4().hex + uuid4().hex,
            payload=payload,
            size_bytes=70000,
        )
        BusinessEvent.objects.filter(pk=posted.pk).update(
            payload_storage="external", payload_ref=ep, data={}, payload_hash=""
        )

        reversal_id = uuid4()
        _emit_posted(company, user, _posted_payload(reversal_id, user, revenue_account, cash_account))
        reversed_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_REVERSED,
            aggregate_type="JournalEntry",
            aggregate_id=str(original_id),
            data={
                "original_entry_public_id": str(original_id),
                "reversal_entry_public_id": str(reversal_id),
                "reversed_at": "2026-01-05T12:00:00",
                "reversed_by_id": user.id,
                "reversed_by_email": user.email,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:ext-rev:{original_id}",
        )

        projection = JournalEntryProjection()
        projection.process_pending(company)
        assert not ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=projection.name, event=reversed_event
        ).exists()  # deferred — found via the external payload store

        AccountProjection().process_pending(company)
        projection.process_pending(company)

        original = JournalEntry.objects.get(company=company, public_id=original_id)
        assert original.status == JournalEntry.Status.REVERSED

    def test_delete_defers_then_guard_decides_on_the_row(self, company, user, cash_account, revenue_account):
        """A delete racing a deferred post must not slip through as a no-op —
        it defers, and once the post materializes, the posted-target guard
        quarantines it against the real row."""
        from accounting.posted_journal_apply import APPLY_DELETE_TARGET_POSTED
        from projections.accounting import AccountProjection

        lag_id = self._emit_lagging_account(company, user, code="1090")
        entry_id = uuid4()
        payload = _posted_payload(entry_id, user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(lag_id)
        _emit_posted(company, user, payload)
        delete_event = emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_DELETED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={
                "entry_public_id": str(entry_id),
                "date": date.today().isoformat(),
                "memo": "racing delete",
                "status": "POSTED",
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:lifecycle-del:{entry_id}",
        )

        projection = JournalEntryProjection()
        projection.process_pending(company)
        assert not ProjectionAppliedEvent.objects.filter(company=company, projection_name=projection.name).exists()

        AccountProjection().process_pending(company)
        projection.process_pending(company)

        # The post materialized; the delete quarantined against the real row.
        assert JournalEntry.objects.filter(company=company, public_id=entry_id).exists()
        log = ProjectionFailureLog.objects.get(company=company, event_id=delete_event.id)
        assert APPLY_DELETE_TARGET_POSTED in log.message


# --------------------------------------------------------------------------- #
# Memo classification at apply: the resolved account is authoritative in the
# CONSUMERS too, not only in the validator (Codex round-2 P1)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestMemoClassificationAtApply:
    def test_financial_line_flagged_memo_still_reaches_balances(self, company, user, cash_account, revenue_account):
        """A historical/foreign payload flagging a FINANCIAL-account line as
        memo passes the invariant (the flag is non-authoritative — the line's
        amounts count), so the balance consumers must count it too; trusting
        the raw flag silently dropped the money from balances."""
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        flagged = dict(payload)
        flagged["lines"] = [dict(payload["lines"][0], is_memo_line=True), dict(payload["lines"][1])]
        _corrupt(event, flagged)

        AccountBalanceProjection().process_pending(company)

        cash = AccountBalance.objects.get(company=company, account=cash_account)
        assert cash.debit_total == Decimal("100.00")
        assert not ProjectionFailureLog.objects.filter(company=company).exists()

    def test_memo_account_line_flagged_false_stays_out_of_balances(self, company, user, cash_account, revenue_account):
        """The inverse: a MEMO-account line whose flag says False is memo by
        the account's authority — it must not leak into financial balances."""
        from accounting.models import Account

        memo_account = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="9000",
            name="Statistical units",
            account_type=Account.AccountType.MEMO,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        with_memo = dict(payload)
        with_memo["lines"] = payload["lines"] + [
            {
                "line_no": 3,
                "account_public_id": str(memo_account.public_id),
                "debit": "50.00",
                "credit": "0.00",
                "is_memo_line": False,  # lying flag — the account says memo
            }
        ]
        _corrupt(event, with_memo)

        AccountBalanceProjection().process_pending(company)

        assert not ProjectionFailureLog.objects.filter(company=company).exists()
        assert AccountBalance.objects.get(company=company, account=cash_account).debit_total == Decimal("100.00")
        assert not AccountBalance.objects.filter(company=company, account=memo_account).exists()

    def test_consumed_account_event_without_row_stays_terminal(self, company, user, cash_account, revenue_account):
        """Codex round-3 P2 (the closing rule): once the ACCOUNT read model
        has consumed the prior account event (marker exists) and the row
        still does not resolve, draining can never materialize it — the
        reference is permanently unknown and must quarantine, not defer
        forever behind an uninsertable payload (None values, uniqueness
        collisions, or any other write failure)."""
        from projections.accounting import AccountProjection
        from projections.models import ProjectionAppliedEvent
        from projections.write_barrier import projection_writes_allowed

        ghost_id = uuid4()
        acct_event = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(ghost_id),
            data={
                "account_public_id": str(ghost_id),
                "code": "1094",
                "name": "Consumed but rowless",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:ghost:{ghost_id}",
        )
        # The account projection consumed the event but no row exists
        # (applied-without-row / terminal-skip shape).
        with projection_writes_allowed():
            ProjectionAppliedEvent.objects.create(
                company=company,
                projection_name=AccountProjection().name,
                event=acct_event,
            )

        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(ghost_id)
        event = _emit_posted(company, user, payload)
        _corrupt(event, payload)

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, event_id=event.id)
        assert "JE_ACCOUNT_UNKNOWN" in log.message

    def test_empty_creation_values_stay_terminal(self, company, user, cash_account, revenue_account):
        """Codex round-3 P2 (static layer): present-but-empty creation values
        (code=None / '') are statically-evident garbage, never evidence."""
        broken_id = uuid4()
        acct_event = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(broken_id),
            data={
                "account_public_id": str(broken_id),
                "code": "1093",
                "name": "Will be nulled",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:nulled:{broken_id}",
        )
        nulled = dict(acct_event.get_data())
        nulled["code"] = None
        _corrupt(acct_event, nulled)

        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(broken_id)
        event = _emit_posted(company, user, payload)
        _corrupt(event, payload)

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, event_id=event.id)
        assert "JE_ACCOUNT_UNKNOWN" in log.message

    def test_marker_observed_after_concurrent_commit_defers(
        self, company, user, cash_account, revenue_account, monkeypatch
    ):
        """Codex round-4 P1: the account projection may COMMIT (row + marker)
        between the probe's facts query and its marker query — observing the
        marker must trigger a re-resolution, and a row that exists now means
        defer (the retry succeeds), never terminal. Simulated by making the
        probe's FIRST facts read stale (account absent) while the DB holds
        the committed row + marker."""
        from accounting import journal_invariant
        from accounting.models import Account
        from accounting.posted_journal_apply import _unknown_accounts_are_pending_materialization
        from projections.accounting import AccountProjection
        from projections.models import ProjectionAppliedEvent

        lag_id = uuid4()
        acct_event = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(lag_id),
            data={
                "account_public_id": str(lag_id),
                "code": "1092",
                "name": "Concurrently committed",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:race:{lag_id}",
        )
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(lag_id)
        event = _emit_posted(company, user, payload)

        # The concurrent commit: row AND marker both exist in the DB.
        AccountProjection().process_pending(company)
        assert Account.objects.filter(company=company, public_id=lag_id).exists()
        assert ProjectionAppliedEvent.objects.filter(
            company=company, projection_name=AccountProjection().name, event=acct_event
        ).exists()

        # Stale first read: the probe's initial facts query misses the row.
        real_load = journal_invariant.load_account_facts
        calls = {"n": 0}

        def _stale_first(company_arg, ids):
            calls["n"] += 1
            facts = real_load(company_arg, ids)
            if calls["n"] == 1:
                facts = {k: v for k, v in facts.items() if k != str(lag_id)}
            return facts

        monkeypatch.setattr(journal_invariant, "load_account_facts", _stale_first)

        assert _unknown_accounts_are_pending_materialization(event) is True
        assert calls["n"] >= 2  # the re-resolution after the marker ran

    def test_unmaterializable_account_evidence_stays_terminal(self, company, user, cash_account, revenue_account):
        """Codex round-2 P2: a prior account event whose payload matches the
        id but lacks a required creation field (AccountProjection subscripts
        'code' unconditionally) can never materialize the row — deferring on
        it would retry forever; it must stay terminal quarantine."""
        broken_id = uuid4()
        acct_event = emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(broken_id),
            data={
                "account_public_id": str(broken_id),
                "code": "1095",
                "name": "Will be broken",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"apply-test:broken:{broken_id}",
        )
        broken_payload = dict(acct_event.get_data())
        del broken_payload["code"]
        _corrupt(acct_event, broken_payload)

        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        payload["lines"][0]["account_public_id"] = str(broken_id)
        event = _emit_posted(company, user, payload)
        _corrupt(event, payload)

        JournalEntryProjection().process_pending(company)

        log = ProjectionFailureLog.objects.get(company=company, event_id=event.id)
        assert "JE_ACCOUNT_UNKNOWN" in log.message


# --------------------------------------------------------------------------- #
# The subset-property precondition: account_type frozen once history posts
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestAccountTypeFrozenOncePosted:
    def test_type_change_refused_on_posted_history(self, company, user, actor_context, cash_account, revenue_account):
        """Memo classification at apply derives from the CURRENT account type,
        so the type must be immutable once posted history references the
        account — otherwise an ordinary CoA edit would make valid history
        fail the apply invariant (quarantined on rebuild, refused on
        restore). The old guard only checked Status.LOCKED, which nothing
        ever sets — a dead guard."""
        from accounting.commands import update_account
        from accounting.models import Account

        entry_id = uuid4()
        _emit_posted(company, user, _posted_payload(entry_id, user, cash_account, revenue_account))
        JournalEntryProjection().process_pending(company)
        assert cash_account.journal_lines.filter(entry__status=JournalEntry.Status.POSTED).exists()

        result = update_account(actor_context, cash_account.id, account_type=Account.AccountType.MEMO)
        assert not result.success
        assert "posted transactions" in result.error

        # History still evaluates clean — the subset property holds.
        event = BusinessEvent.objects.get(company=company, event_type=EventTypes.JOURNAL_ENTRY_POSTED)
        assert evaluate_posted_journal_for_apply(event) == []

    def test_type_change_fails_closed_on_unmaterialized_history(
        self, company, user, actor_context, cash_account, revenue_account
    ):
        """Codex round-1 P1: the JournalLine rows are projection-owned — a
        posted event that committed but has not materialized yet (async
        drain) must fail the guard CLOSED, not slip past a stale read model
        (the drain would otherwise apply the type change before validating
        the earlier journal, quarantining legitimate history)."""
        from accounting.commands import update_account
        from accounting.models import Account

        _emit_posted(company, user, _posted_payload(uuid4(), user, cash_account, revenue_account))
        # No drain: the event is committed but unmaterialized — the durable
        # posted-event scan must find the reference anyway.
        assert not cash_account.journal_lines.exists()

        result = update_account(actor_context, cash_account.id, account_type=Account.AccountType.MEMO)
        assert not result.success
        assert "posted transactions" in result.error

    def test_type_change_refused_on_marker_with_partial_application(
        self, company, user, actor_context, cash_account, revenue_account
    ):
        """Codex round-3 P1: an applied-marker is NOT proof of complete
        materialization — a pre-boundary projector could mark the event
        applied after silently skipping this account's line (no row). The
        durable posted-event payload scan must refuse regardless of
        projection state."""
        from accounting.commands import update_account
        from accounting.models import Account
        from projections.models import ProjectionAppliedEvent
        from projections.write_barrier import projection_writes_allowed

        event = _emit_posted(company, user, _posted_payload(uuid4(), user, cash_account, revenue_account))
        with projection_writes_allowed():
            ProjectionAppliedEvent.objects.create(
                company=company,
                projection_name=JournalEntryProjection().name,
                event=event,
            )
        assert not cash_account.journal_lines.exists()  # the partial-application shape

        result = update_account(actor_context, cash_account.id, account_type=Account.AccountType.MEMO)
        assert not result.success
        assert "posted transactions" in result.error

    def test_type_change_fails_closed_on_unreadable_posted_event(
        self, company, user, actor_context, cash_account, revenue_account
    ):
        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)
        BusinessEvent.objects.filter(pk=event.pk).update(payload_storage="external")  # get_data raises

        from accounting.commands import update_account
        from accounting.models import Account

        result = update_account(actor_context, cash_account.id, account_type=Account.AccountType.MEMO)
        assert not result.success
        assert "cannot be verified" in result.error

    def test_type_change_still_allowed_without_posted_history(self, company, actor_context, cash_account):
        from accounting.commands import update_account
        from accounting.models import Account

        result = update_account(actor_context, cash_account.id, account_type=Account.AccountType.EXPENSE)
        assert result.success, result.error

    def test_type_change_fails_closed_on_malformed_posted_shapes(
        self, company, user, actor_context, cash_account, revenue_account
    ):
        """Codex round-4 P2: malformed payload SHAPES (non-dict payload,
        non-list lines, non-dict line entry) fail the freeze scan closed —
        'no readable references' is not 'no references'."""
        from accounting.commands import update_account
        from accounting.models import Account

        payload = _posted_payload(uuid4(), user, cash_account, revenue_account)
        event = _emit_posted(company, user, payload)

        for corruption in ([1, 2, 3], dict(payload, lines=1), dict(payload, lines=["junk", payload["lines"][1]])):
            _corrupt(event, corruption)
            result = update_account(actor_context, cash_account.id, account_type=Account.AccountType.MEMO)
            assert not result.success
            assert "cannot be verified" in result.error
