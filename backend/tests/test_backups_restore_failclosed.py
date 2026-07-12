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
        data={"entry_public_id": str(entry.public_id)},
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
