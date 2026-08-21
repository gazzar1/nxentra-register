# tests/test_backups_restore_failclosed.py
"""
A161 — restore must fail closed.

Before the fix, backups/importer.py:
- silently SKIPPED model files missing from the ZIP and swallowed
  malformed JSON into stats['errors'] — then COMMITTED the partial
  restore over the already-cleared company;
- never verified export_hash or manifest counts;
- never checked the backup belonged to the target company (and
  overwrote the target's name/currency from the manifest AFTER commit);
- ran no post-restore financial invariants.

Every tampered-archive case below must raise RestoreError AND leave the
original books untouched (the clear rolls back).
"""

import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.models import Account, JournalEntry, JournalLine
from backups.exporter import export_company
from backups.importer import RestoreError, restore_company
from events.models import BusinessEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def booked_company(company, user, cash_account, revenue_account):
    """Company with one posted JE + one backing event, exported cleanly."""
    entry = JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=date(2026, 3, 10),
        period=3,
        memo="restore-fixture entry",
        entry_number="JE-BK-1",
        status=JournalEntry.Status.POSTED,
        created_by=user,
    )
    JournalLine.objects.create(
        entry=entry, company=company, line_no=1, account=cash_account, debit=Decimal("100.00"), credit=Decimal("0")
    )
    JournalLine.objects.create(
        entry=entry, company=company, line_no=2, account=revenue_account, debit=Decimal("0"), credit=Decimal("100.00")
    )
    BusinessEvent.objects.create(
        company=company,
        event_type="journal_entry.posted",
        aggregate_type="JournalEntry",
        aggregate_id=str(entry.public_id),
        idempotency_key=f"test.backup:{entry.public_id}",
        # A3-PR3 (D6): restore verification now runs the canonical apply
        # invariant over every restored posted-JE event, so the fixture's
        # backing event must be a REAL invariant-valid payload (a real
        # stream's posted events always are — the emit boundary guarantees
        # it), not a stub.
        data={
            "entry_public_id": str(entry.public_id),
            "date": "2026-03-10",
            "period": 3,
            "memo": "restore-fixture entry",
            "total_debit": "100.00",
            "total_credit": "100.00",
            "lines": [
                {
                    "line_no": 1,
                    "account_public_id": str(cash_account.public_id),
                    "debit": "100.00",
                    "credit": "0.00",
                },
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "debit": "0.00",
                    "credit": "100.00",
                },
            ],
        },
    )
    return company


def _snapshot(company):
    return {
        "journal_entries": JournalEntry.objects.filter(company=company).count(),
        "journal_lines": JournalLine.objects.filter(company=company).count(),
        "events": BusinessEvent.objects.filter(company=company).count(),
        "accounts": Account.objects.filter(company=company).count(),
        "name": company.name,
        "currency": company.default_currency,
    }


def _tamper(zip_bytes, *, drop=None, replace=None, edit_manifest=None):
    """Rebuild the ZIP, dropping/replacing members or editing the manifest."""
    src = zipfile.ZipFile(io.BytesIO(zip_bytes))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name in src.namelist():
            if drop and name == drop:
                continue
            data = src.read(name)
            if replace and name in replace:
                data = replace[name]
            if edit_manifest and name == "manifest.json":
                manifest = json.loads(data)
                edit_manifest(manifest)
                data = json.dumps(manifest).encode("utf-8")
            out.writestr(name, data)
    return buf.getvalue()


class TestTamperedArchivesFailClosed:
    def test_missing_model_file_rejected_and_books_survive(self, booked_company):
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        tampered = _tamper(zip_bytes, drop="models/accounting.JournalLine.json")
        with pytest.raises(RestoreError):
            restore_company(company, tampered)

        assert _snapshot(company) == before, "a rejected restore must leave the original books untouched"

    def test_malformed_model_file_rejected(self, booked_company):
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        tampered = _tamper(zip_bytes, replace={"models/accounting.JournalEntry.json": b"{invalid json"})
        with pytest.raises(RestoreError):
            restore_company(company, tampered)
        assert _snapshot(company) == before

    def test_edited_record_fails_export_hash(self, booked_company):
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        src = zipfile.ZipFile(io.BytesIO(zip_bytes))
        lines = json.loads(src.read("models/accounting.JournalLine.json"))
        lines[0]["debit"] = "999999.00"
        tampered = _tamper(
            zip_bytes,
            replace={"models/accounting.JournalLine.json": json.dumps(lines).encode("utf-8")},
        )
        with pytest.raises(RestoreError, match=r"export_hash|integrity"):
            restore_company(company, tampered)
        assert _snapshot(company) == before

    def test_manifest_count_mismatch_rejected(self, booked_company):
        company = booked_company
        zip_bytes, _ = export_company(company)

        def _bump(manifest):
            manifest["model_counts"]["accounting.JournalLine"] += 1
            # keep export_hash untouched — the count check must fire even
            # when the hash matches the (unmodified) model files

        tampered = _tamper(zip_bytes, edit_manifest=_bump)
        with pytest.raises(RestoreError, match="count mismatch"):
            restore_company(company, tampered)

    def test_missing_export_hash_rejected(self, booked_company):
        company = booked_company
        zip_bytes, _ = export_company(company)

        def _strip(manifest):
            manifest.pop("export_hash", None)

        tampered = _tamper(zip_bytes, edit_manifest=_strip)
        with pytest.raises(RestoreError, match="export_hash"):
            restore_company(company, tampered)

    def test_cross_company_backup_rejected_and_settings_survive(self, booked_company, second_company):
        company = booked_company
        original_name = company.name
        original_currency = company.default_currency

        other_zip, _ = export_company(second_company)
        with pytest.raises(RestoreError, match="different company"):
            restore_company(company, other_zip)

        company.refresh_from_db()
        assert company.name == original_name, "a rejected restore must not rename the target company"
        assert company.default_currency == original_currency


class TestHappyPathAndInvariants:
    def test_untampered_roundtrip_restores_and_verifies(self, booked_company):
        company = booked_company
        zip_bytes, metadata = export_company(company)
        before = _snapshot(company)

        result = restore_company(company, zip_bytes)

        after = _snapshot(company)
        assert after["journal_entries"] == before["journal_entries"]
        assert after["journal_lines"] == before["journal_lines"]
        assert after["events"] == before["events"]
        assert result["imported"]["events.BusinessEvent"] == metadata["event_count"]
        # Trial balance holds post-restore (verified inside the transaction).
        from django.db.models import Sum

        agg = JournalLine.objects.filter(company=company, entry__status=JournalEntry.Status.POSTED).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        assert agg["dr"] == agg["cr"]

    def test_unbalanced_books_trip_the_invariant(self, booked_company):
        """Hand-craft an archive that passes hash+count checks but carries
        unbalanced POSTED lines — only the post-restore trial-balance
        invariant can catch it, and it must roll everything back."""
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        src = zipfile.ZipFile(io.BytesIO(zip_bytes))
        lines = json.loads(src.read("models/accounting.JournalLine.json"))
        lines[0]["debit"] = "150.00"  # unbalances DR vs CR
        new_lines_bytes = json.dumps(lines, ensure_ascii=False).encode("utf-8")

        # Recompute export_hash over the tampered members in registry order
        # so ONLY the invariant can catch the corruption.
        import hashlib

        from backups.model_registry import get_export_registry

        replaced = {"models/accounting.JournalLine.json": new_lines_bytes}
        hasher = hashlib.sha256()
        names = set(src.namelist())
        for label in get_export_registry():
            member = f"models/{label}.json"
            if member in names:
                hasher.update(replaced.get(member, src.read(member)))
        new_hash = hasher.hexdigest()

        def _fix_hash(manifest):
            manifest["export_hash"] = new_hash

        tampered = _tamper(zip_bytes, replace=replaced, edit_manifest=_fix_hash)

        with pytest.raises(RestoreError, match="trial balance"):
            restore_company(company, tampered)
        assert _snapshot(company) == before, "the invariant failure must roll back the whole restore"


def _retamper_member(zip_bytes, member, new_bytes):
    """Replace one model member AND recompute the manifest export_hash so the
    archive stays internally consistent — only a post-restore invariant can
    catch the corruption (export_hash proves internal consistency, not
    provenance)."""
    import hashlib

    from backups.model_registry import get_export_registry

    src = zipfile.ZipFile(io.BytesIO(zip_bytes))
    replaced = {member: new_bytes}
    hasher = hashlib.sha256()
    names = set(src.namelist())
    for label in get_export_registry():
        m = f"models/{label}.json"
        if m in names:
            hasher.update(replaced.get(m, src.read(m)))
    new_hash = hasher.hexdigest()

    def _fix_hash(manifest):
        manifest["export_hash"] = new_hash

    return _tamper(zip_bytes, replace=replaced, edit_manifest=_fix_hash)


class TestApplyInvariantOnRestore:
    """A3-PR3 (D6): restore re-inserts the event stream wholesale and never
    replays it, so the projection-apply choke point cannot see restored
    history. `_verify_restore` therefore runs the canonical apply invariant
    over every restored JOURNAL_ENTRY_POSTED event, fail-closed inside the
    restore transaction."""

    def _tamper_posted_event(self, zip_bytes, mutate):
        src = zipfile.ZipFile(io.BytesIO(zip_bytes))
        events = json.loads(src.read("models/events.BusinessEvent.json"))
        touched = 0
        for record in events:
            if record.get("event_type") == "journal_entry.posted":
                mutate(record)
                touched += 1
        assert touched, "fixture must contain a posted-JE event"
        return _retamper_member(
            zip_bytes,
            "models/events.BusinessEvent.json",
            json.dumps(events, ensure_ascii=False).encode("utf-8"),
        )

    def test_unbalanced_posted_event_fails_restore(self, booked_company):
        """The event payload is corrupted but the materialized ROWS still
        balance — the trial-balance check passes and ONLY the event-corpus
        apply invariant can refuse the archive."""
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        def _unbalance(record):
            record["data"]["lines"][1]["credit"] = "90.00"

        tampered = self._tamper_posted_event(zip_bytes, _unbalance)

        with pytest.raises(RestoreError, match="apply invariant"):
            restore_company(company, tampered)
        assert _snapshot(company) == before, "the failed corpus check must roll back the whole restore"

    def test_missing_entry_id_fails_restore_same_as_apply(self, booked_company):
        """The identity guard lives in the shared evaluator, so restore
        applies the EXACT verdict apply-time enforcement applies — an
        invariant-clean event without a materializable entry_public_id must
        not be certified by restore only to quarantine at the next rebuild."""
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        def _strip_id(record):
            del record["data"]["entry_public_id"]

        tampered = self._tamper_posted_event(zip_bytes, _strip_id)

        with pytest.raises(RestoreError, match="apply invariant"):
            restore_company(company, tampered)
        assert _snapshot(company) == before

    def test_unreadable_posted_event_payload_fails_restore(self, booked_company):
        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        def _break(record):
            record["data"] = [1, 2, 3]  # non-dict payload → APPLY_UNREADABLE_PAYLOAD

        tampered = self._tamper_posted_event(zip_bytes, _break)

        with pytest.raises(RestoreError, match="apply invariant"):
            restore_company(company, tampered)
        assert _snapshot(company) == before

    def test_backup_of_replayable_lag_state_restores(self, company, user, cash_account, revenue_account):
        """Codex round-1 P2 (verdict symmetry): a backup captured while a
        posted event's referenced account row was not yet materialized — with
        the creating account event earlier in the stream — is REPLAYABLE, and
        the apply choke point would DEFER it, not quarantine it. Restore must
        share that disposition instead of refusing a legitimate backup."""
        from events.emitter import emit_event
        from events.types import EventTypes

        lag_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="Account",
            aggregate_id=str(lag_id),
            data={
                "account_public_id": str(lag_id),
                "code": "1096",
                "name": "Lagging restore account",
                "account_type": "ASSET",
                "normal_balance": "DEBIT",
                "is_header": False,
            },
            caused_by_user=user,
            idempotency_key=f"restore-lag:acct:{lag_id}",
        )
        entry_id = uuid4()
        emit_event(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={
                "entry_public_id": str(entry_id),
                "entry_number": "JE-LAG-1",
                "date": "2026-03-11",
                "memo": "lagging entry",
                "kind": "NORMAL",
                "period": 3,
                "posted_at": "2026-03-11T12:00:00",
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": "50.00",
                "total_credit": "50.00",
                "lines": [
                    {"line_no": 1, "account_public_id": str(lag_id), "debit": "50.00", "credit": "0.00"},
                    {
                        "line_no": 2,
                        "account_public_id": str(revenue_account.public_id),
                        "debit": "0.00",
                        "credit": "50.00",
                    },
                ],
            },
            caused_by_user=user,
            idempotency_key=f"restore-lag:posted:{entry_id}",
        )
        # No drain: the lagging account row does not exist at export time.
        zip_bytes, _ = export_company(company)
        result = restore_company(company, zip_bytes)
        assert result["company"] == company.slug

    def test_skip_invariants_break_glass_bypasses_corpus_check(self, booked_company):
        """--skip-invariants is the ONLY way past the corpus check — the same
        documented break-glass as the trial-balance/subledger invariants,
        for deliberate recovery of known-bad history."""
        company = booked_company
        zip_bytes, _ = export_company(company)

        def _unbalance(record):
            record["data"]["lines"][1]["credit"] = "90.00"

        tampered = self._tamper_posted_event(zip_bytes, _unbalance)

        result = restore_company(company, tampered, skip_invariants=True)
        assert result["company"] == company.slug


# =============================================================================
# A4 control-plane: in-app restore is BLOCKED while a constrained pilot is active.
# =============================================================================
ISO = "ISOLATED_SHADOW_LEDGER_V1"


def _activate(company):
    """Set the constrained-pilot profile directly (tests the runtime GATE, not the
    activation ceremony — activate_pilot_profile's serialization is proven in the
    e2e concurrency suite)."""
    company.pilot_profile = ISO
    company.save()
    return company


class TestRestoreBlockedUnderActivePilot:
    def test_direct_restore_refused_under_active_pilot(self, booked_company):
        from accounts.pilot_policy import PilotScopeBlocked

        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)
        _activate(company)

        with pytest.raises(PilotScopeBlocked) as exc:
            restore_company(company, zip_bytes)
        assert exc.value.capability == "backup_restore"
        # Refused BEFORE the clear — the books are untouched.
        company.refresh_from_db()
        assert _snapshot(company) == {**before, "name": company.name, "currency": company.default_currency}
        assert BusinessEvent.objects.filter(company=company).count() == before["events"]

    def test_break_glass_flags_do_not_bypass_pilot(self, booked_company):
        """allow_company_mismatch / skip_invariants are import break-glass — they
        must NEVER bypass the pilot restriction."""
        from accounts.pilot_policy import PilotScopeBlocked

        company = booked_company
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)
        _activate(company)

        with pytest.raises(PilotScopeBlocked):
            restore_company(company, zip_bytes, allow_company_mismatch=True, skip_invariants=True)
        company.refresh_from_db()
        assert BusinessEvent.objects.filter(company=company).count() == before["events"], "no clear may have occurred"

    def test_profile_none_restore_still_succeeds(self, booked_company):
        """The gate is a no-op for a NONE-profile company — an ordinary valid
        restore is unaffected."""
        company = booked_company  # NONE profile by default
        zip_bytes, _ = export_company(company)
        before = _snapshot(company)

        result = restore_company(company, zip_bytes)
        assert result["company"] == company.slug
        assert _snapshot(company) == before, "round-trip restore must reproduce the books exactly"
