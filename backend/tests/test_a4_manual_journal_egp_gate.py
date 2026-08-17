# tests/test_a4_manual_journal_egp_gate.py
"""A4 Manual-Journal EGP Admission — behavioral proofs (SQLite battery).

The MANUAL general-journal process boundary (`*_manual_journal_entry` wrappers)
owns serialized Company admission and switches on the canonical EGP validator
(`require_pilot_journal_currency`) inside the shared journal commands via the
module-private sentinel. This file proves:

  A. the supported EGP manual lifecycle is UNCHANGED under the pilot
     (create → update → save-complete → post → reverse), blank = home currency,
     profile-NONE behavior untouched, kind surface untouched;
  B. every foreign shape is rejected at every manual door with ZERO side
     effects (no row, no event, no sequence/counter consumption): foreign
     header, foreign line under EGP header, foreign amount_currency leg,
     non-1 exchange rate, derived (company-default) foreign currency, foreign
     reversal, cutover retry of a pre-activation foreign create;
  C. the dormant serializer write path stays closed (NotImplementedError) and
     DELETE remains available as cleanup;
  D. the three new preflight codes each fire on seeded residue, are disjoint
     from the header code, run in activation mode, and a clean supported EGP
     pilot stays clean (blank/rate=1 stamps never false-positive).

Activation-race orderings are proven on real PostgreSQL in
tests/e2e/test_a4_manual_journal_serialization.py.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.commands import (
    create_journal_entry,
    create_manual_journal_entry,
    delete_journal_entry,
    post_manual_journal_entry,
    reverse_manual_journal_entry,
    save_manual_journal_entry_complete,
    update_manual_journal_entry,
)
from accounting.models import JournalEntry, JournalLine
from accounts.models import Company
from accounts.pilot_policy import NON_EGP_INGESTION, PilotScopeBlocked
from events.models import BusinessEvent, CompanyEventCounter
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_pilot(company, *, currency="EGP"):
    company.pilot_profile = ISO
    company.default_currency = currency
    company.functional_currency = currency
    company.fiscal_year_start_month = 1
    company.save()
    return company


def _make_none_egp(company):
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.save()
    return company


def _lines(debit_acc, credit_acc, amount="100.00", **extra):
    return [
        {"account_id": debit_acc.id, "description": "d", "debit": Decimal(amount), "credit": Decimal("0"), **extra},
        {"account_id": credit_acc.id, "description": "c", "debit": Decimal("0"), "credit": Decimal(amount), **extra},
    ]


def _state(company):
    """Side-effect probe: (JE rows, event rows, counter value, JE-number seq)."""
    from accounting.models import CompanySequence

    counter = CompanyEventCounter.objects.filter(company=company).first()
    seq = CompanySequence.objects.filter(company=company, name="journal_entry_number").first()
    return (
        JournalEntry.objects.filter(company=company).count(),
        BusinessEvent.objects.filter(company=company).count(),
        counter.last_sequence if counter else None,
        seq.next_value if seq else None,
    )


def _seed_row_entry(company, *, currency="EGP", exchange_rate="1.0", status=JournalEntry.Status.INCOMPLETE):
    with projection_writes_allowed():
        return JournalEntry.objects.projection().create(
            company=company,
            public_id=uuid4(),
            date=date(2026, 3, 10),
            period=3,
            status=status,
            currency=currency,
            exchange_rate=Decimal(str(exchange_rate)),
        )


def _seed_row_line(entry, account, *, currency="", amount_currency=None, exchange_rate=None, line_no=1):
    with projection_writes_allowed():
        return JournalLine.objects.projection().create(
            entry=entry,
            company=entry.company,
            public_id=uuid4(),
            line_no=line_no,
            account=account,
            debit=Decimal("10.00"),
            credit=Decimal("0"),
            currency=currency,
            amount_currency=amount_currency,
            exchange_rate=exchange_rate,
        )


def _preflight_codes(company, **kwargs):
    from accounts.pilot_preflight import run_preflight

    return {v.code for v in run_preflight(company, **kwargs)}


NEW_CODES = {"non_egp_journal_line_data", "fx_line_residue", "fx_header_rate_residue"}


# --------------------------------------------------------------------------- #
# A. Supported EGP manual lifecycle — unchanged under the pilot
# --------------------------------------------------------------------------- #
class TestSupportedEgpLifecycle:
    def test_full_lifecycle_under_pilot(self, actor_context, company, cash_account, revenue_account):
        _make_pilot(company)
        created = create_manual_journal_entry(
            actor_context,
            date=date.today(),
            memo="egp manual",
            currency="EGP",
            lines=_lines(cash_account, revenue_account),
        )
        assert created.success, created.error
        assert created.data.kind == JournalEntry.Kind.NORMAL

        updated = update_manual_journal_entry(
            actor_context,
            created.data.id,
            lines=_lines(cash_account, revenue_account, amount="150.00"),
        )
        assert updated.success, updated.error

        completed = save_manual_journal_entry_complete(actor_context, created.data.id)
        assert completed.success, completed.error
        assert completed.data.status == JournalEntry.Status.DRAFT

        posted = post_manual_journal_entry(actor_context, created.data.id)
        assert posted.success, posted.error
        assert posted.data.status == JournalEntry.Status.POSTED
        assert posted.data.currency == "EGP"
        assert Decimal(posted.data.exchange_rate) == Decimal("1.0")

        reversed_ = reverse_manual_journal_entry(actor_context, created.data.id)
        assert reversed_.success, reversed_.error
        assert reversed_.data["original"].status == JournalEntry.Status.REVERSED
        assert reversed_.data["reversal"].kind == JournalEntry.Kind.REVERSAL

    def test_blank_currency_books_at_home(self, actor_context, company, cash_account, revenue_account):
        """Blank/absent currency keeps the established home-currency semantics."""
        _make_pilot(company)
        r = create_manual_journal_entry(
            actor_context, date=date.today(), memo="blank", lines=_lines(cash_account, revenue_account)
        )
        assert r.success, r.error
        assert r.data.currency == "EGP"  # resolved to functional

    def test_none_profile_foreign_journal_unchanged(self, actor_context, company, cash_account, revenue_account):
        """Profile NONE keeps existing behavior through the SAME wrappers: a
        foreign journal is created/completed/posted exactly as before (the
        company's functional currency is USD, so no conversion occurs)."""
        assert company.pilot_profile == Company.PilotProfile.NONE
        company.functional_currency = "USD"
        company.save()
        r = create_manual_journal_entry(
            actor_context, date=date.today(), memo="usd", currency="USD", lines=_lines(cash_account, revenue_account)
        )
        assert r.success, r.error
        assert save_manual_journal_entry_complete(actor_context, r.data.id).success
        posted = post_manual_journal_entry(actor_context, r.data.id)
        assert posted.success, posted.error
        assert posted.data.currency == "USD"

    def test_egp_lifecycle_parity_vs_none_profile(self, actor_context, company, cash_account, revenue_account):
        """Field-level parity proof: the same EGP fixture produces the same
        event sequence and the same posted-entry facts under NONE and pilot."""

        def run():
            r = create_manual_journal_entry(
                actor_context,
                date=date(2026, 2, 10),
                memo="parity",
                currency="EGP",
                lines=_lines(cash_account, revenue_account),
            )
            assert r.success, r.error
            assert save_manual_journal_entry_complete(actor_context, r.data.id).success
            p = post_manual_journal_entry(actor_context, r.data.id)
            assert p.success, p.error
            events = list(
                BusinessEvent.objects.filter(company=company, aggregate_id=str(p.data.public_id))
                .order_by("company_sequence")
                .values_list("event_type", flat=True)
            )
            facts = (
                p.data.currency,
                Decimal(p.data.exchange_rate),
                p.data.kind,
                p.data.status,
                p.data.lines.count(),
                sum(line.debit for line in p.data.lines.all()),
            )
            return events, facts, p.data.id

        _make_none_egp(company)
        events_none, facts_none, _je_id = run()
        # Reset: delete nothing — a SECOND identical entry under the pilot must
        # behave identically (A177: identical legitimate entries never collide).
        _make_pilot(company)
        events_pilot, facts_pilot, _ = run()

        assert events_none == events_pilot
        assert facts_none == facts_pilot

    def test_delete_stays_available_under_pilot(self, actor_context, company, cash_account, revenue_account):
        """DELETE is cleanup: deliberately unwrapped, still available."""
        _make_pilot(company)
        r = create_manual_journal_entry(
            actor_context, date=date.today(), memo="cleanup", lines=_lines(cash_account, revenue_account)
        )
        assert r.success, r.error
        d = delete_journal_entry(actor_context, r.data.id)
        assert d.success, d.error


# --------------------------------------------------------------------------- #
# B. Foreign rejection — every manual door, zero side effects
# --------------------------------------------------------------------------- #
class TestForeignRejection:
    def test_foreign_header_rejected_zero_side_effects(self, actor_context, company, cash_account, revenue_account):
        _make_pilot(company)
        before = _state(company)
        with pytest.raises(PilotScopeBlocked) as exc:
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo="usd",
                currency="USD",
                lines=_lines(cash_account, revenue_account),
            )
        assert exc.value.capability == NON_EGP_INGESTION
        assert _state(company) == before

    def test_egp_header_foreign_line_rejected_at_create(self, actor_context, company, cash_account, revenue_account):
        """The C8 closure: a foreign LINE under an EGP header is rejected."""
        _make_pilot(company)
        before = _state(company)
        with pytest.raises(PilotScopeBlocked):
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo="mixed",
                currency="EGP",
                lines=_lines(cash_account, revenue_account, currency="USD"),
            )
        assert _state(company) == before

    def test_egp_header_foreign_line_rejected_at_update_and_complete(
        self, actor_context, company, cash_account, revenue_account
    ):
        _make_pilot(company)
        seeded = create_manual_journal_entry(
            actor_context, date=date.today(), memo="egp", currency="EGP", lines=_lines(cash_account, revenue_account)
        )
        assert seeded.success, seeded.error
        before = _state(company)

        with pytest.raises(PilotScopeBlocked):
            update_manual_journal_entry(
                actor_context, seeded.data.id, lines=_lines(cash_account, revenue_account, currency="USD")
            )
        assert _state(company) == before

        with pytest.raises(PilotScopeBlocked):
            save_manual_journal_entry_complete(
                actor_context, seeded.data.id, lines=_lines(cash_account, revenue_account, currency="USD")
            )
        assert _state(company) == before

    def test_preexisting_foreign_draft_rejected_at_post(self, actor_context, company, cash_account, revenue_account):
        """A foreign DRAFT seeded BEFORE activation (through the ungated shared
        command, as any pre-pilot state would be) cannot be posted through the
        manual door once the pilot is active — and posting consumes nothing."""
        _make_none_egp(company)
        seeded = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="pre-pilot usd line",
            currency="EGP",
            lines=_lines(cash_account, revenue_account, currency="USD", exchange_rate=Decimal("48")),
        )
        assert seeded.success, seeded.error
        from accounting.commands import save_journal_entry_complete

        assert save_journal_entry_complete(actor_context, seeded.data.id).success
        _make_pilot(company)
        before = _state(company)
        with pytest.raises(PilotScopeBlocked):
            post_manual_journal_entry(actor_context, seeded.data.id)
        assert _state(company) == before
        seeded.data.refresh_from_db()
        assert seeded.data.status == JournalEntry.Status.DRAFT  # untouched

    def test_amount_currency_leg_rejected(self, actor_context, company, cash_account, revenue_account):
        _make_pilot(company)
        before = _state(company)
        with pytest.raises(PilotScopeBlocked):
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo="leg",
                currency="EGP",
                lines=_lines(cash_account, revenue_account, amount_currency=Decimal("50.00")),
            )
        assert _state(company) == before

    def test_non1_exchange_rate_rejected_header_and_line(self, actor_context, company, cash_account, revenue_account):
        _make_pilot(company)
        before = _state(company)
        with pytest.raises(PilotScopeBlocked):
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo="hdr rate",
                currency="EGP",
                exchange_rate="48",
                lines=_lines(cash_account, revenue_account),
            )
        assert _state(company) == before
        with pytest.raises(PilotScopeBlocked):
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo="line rate",
                currency="EGP",
                lines=_lines(cash_account, revenue_account, exchange_rate=Decimal("48")),
            )
        assert _state(company) == before

    def test_derived_company_default_foreign_cannot_bypass(self, actor_context, company, cash_account, revenue_account):
        """No explicit currency anywhere: the resolution chain lands on the
        company default. On a (mis-configured) USD-default pilot the RESOLVED
        value is what the gate sees — derived foreign cannot slip through."""
        _make_pilot(company, currency="USD")
        before = _state(company)
        with pytest.raises(PilotScopeBlocked):
            create_manual_journal_entry(
                actor_context, date=date.today(), memo="derived", lines=_lines(cash_account, revenue_account)
            )
        assert _state(company) == before

    def test_foreign_reversal_rejected_dead_path(self, actor_context, company, cash_account, revenue_account):
        """Defense-in-depth: a manual reversal may never mint a NEW foreign
        journal. (Under a real pilot this is unreachable — activation preflight
        refuses foreign posted residue — so this pins the semantics.)"""
        company.functional_currency = "USD"
        company.default_currency = "USD"
        company.save()
        seeded = create_manual_journal_entry(
            actor_context, date=date.today(), memo="usd", currency="USD", lines=_lines(cash_account, revenue_account)
        )
        assert seeded.success
        assert save_manual_journal_entry_complete(actor_context, seeded.data.id).success
        assert post_manual_journal_entry(actor_context, seeded.data.id).success

        _make_pilot(company)  # drift-style cutover with foreign posted residue
        before = _state(company)
        with pytest.raises(PilotScopeBlocked):
            reverse_manual_journal_entry(actor_context, seeded.data.id)
        assert _state(company) == before
        seeded.data.refresh_from_db()
        assert seeded.data.status == JournalEntry.Status.POSTED  # not reversed

    def test_cutover_retry_of_foreign_create_rejected(self, actor_context, company, cash_account, revenue_account):
        """A177 × A4 cutover: a pre-activation foreign create retried (same
        request_id) AFTER activation is REJECTED, not idempotently replayed —
        the gate runs before the idempotency short-circuit."""
        _make_none_egp(company)
        first = create_manual_journal_entry(
            actor_context,
            date=date.today(),
            memo="retry",
            currency="USD",
            lines=_lines(cash_account, revenue_account),
            request_id="cutover-retry-1",
        )
        assert first.success, first.error
        _make_pilot(company)
        with pytest.raises(PilotScopeBlocked):
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo="retry",
                currency="USD",
                lines=_lines(cash_account, revenue_account),
                request_id="cutover-retry-1",
            )


# --------------------------------------------------------------------------- #
# C. The dormant serializer door stays closed
# --------------------------------------------------------------------------- #
class TestSerializerDoorClosed:
    def test_autosave_serializer_write_paths_refuse(self):
        from accounting.serializers import JournalEntryAutoSaveSerializer, JournalEntrySaveCompleteSerializer

        with pytest.raises(NotImplementedError):
            JournalEntryAutoSaveSerializer().create({})
        with pytest.raises(NotImplementedError):
            JournalEntryAutoSaveSerializer().update(object(), {})
        with pytest.raises(NotImplementedError):
            JournalEntrySaveCompleteSerializer().update(object(), {})


# --------------------------------------------------------------------------- #
# D. Preflight — new residue codes
# --------------------------------------------------------------------------- #
class TestJournalCurrencyPreflight:
    def test_non_egp_journal_line_data_detected_and_disjoint(self, company, cash_account):
        _make_pilot(company)
        egp_header = _seed_row_entry(company, currency="EGP")
        _seed_row_line(egp_header, cash_account, currency="USD")
        codes = _preflight_codes(company, for_activation=True)
        assert "non_egp_journal_line_data" in codes
        assert "non_egp_journal_data" not in codes  # header itself is EGP

        # Disjointness: a fully-foreign entry is the HEADER code's row only.
        usd_header = _seed_row_entry(company, currency="USD")
        _seed_row_line(usd_header, cash_account, currency="USD", line_no=1)
        from accounts.pilot_preflight import run_preflight

        line_violations = [
            v for v in run_preflight(company, for_activation=True) if v.code == "non_egp_journal_line_data"
        ]
        assert len(line_violations) == 1
        assert line_violations[0].message.startswith("1 ")  # still only the EGP-header line

    def test_fx_line_residue_detected_with_false_positive_guards(self, company, cash_account):
        _make_pilot(company)
        entry = _seed_row_entry(company, currency="EGP")
        # Normal EGP/default stamps must NOT fire: blank currency, NULL
        # amount_currency, and both NULL and 1.0 rates are legitimate.
        _seed_row_line(entry, cash_account, currency="", exchange_rate=None, line_no=1)
        _seed_row_line(entry, cash_account, currency="EGP", exchange_rate=Decimal("1.0"), line_no=2)
        assert "fx_line_residue" not in _preflight_codes(company, for_activation=True)

        _seed_row_line(entry, cash_account, currency="EGP", amount_currency=Decimal("70.00"), line_no=3)
        assert "fx_line_residue" in _preflight_codes(company, for_activation=True)

    def test_fx_line_residue_non1_rate(self, company, cash_account):
        _make_pilot(company)
        entry = _seed_row_entry(company, currency="EGP")
        _seed_row_line(entry, cash_account, currency="", exchange_rate=Decimal("48"), line_no=1)
        assert "fx_line_residue" in _preflight_codes(company, for_activation=True)

    def test_fx_header_rate_residue(self, company):
        _make_pilot(company)
        _seed_row_entry(company, currency="EGP", exchange_rate="1.0")
        assert "fx_header_rate_residue" not in _preflight_codes(company, for_activation=True)
        _seed_row_entry(company, currency="EGP", exchange_rate="48")
        codes = _preflight_codes(company, for_activation=True)
        assert "fx_header_rate_residue" in codes
        assert "non_egp_journal_data" not in codes  # EGP header: disjoint from header code

    def test_clean_supported_egp_pilot_stays_clean(self, actor_context, company, cash_account, revenue_account):
        """A full supported manual EGP lifecycle leaves ZERO new-code residue,
        in both ordinary and activation preflight modes."""
        _make_pilot(company)
        r = create_manual_journal_entry(
            actor_context, date=date.today(), memo="clean", currency="EGP", lines=_lines(cash_account, revenue_account)
        )
        assert r.success, r.error
        assert save_manual_journal_entry_complete(actor_context, r.data.id).success
        assert post_manual_journal_entry(actor_context, r.data.id).success

        for kwargs in ({"for_activation": True}, {}):
            codes = _preflight_codes(company, **kwargs)
            assert not (codes & NEW_CODES), f"clean EGP books false-positived: {codes & NEW_CODES} ({kwargs})"
            assert "non_egp_journal_data" not in codes
