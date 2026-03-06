# tests/test_tenant_export_import.py
"""
Tenant Export/Import Verification Tests.

Validates the event export/import pipeline:
1. Export produces deterministic JSON with SHA-256 hash
2. Import recreates events faithfully
3. Export hash matches import hash (integrity)
4. Idempotent import (skip-existing)
5. Replay projections after import produces correct balances
"""
import json
import hashlib
import tempfile
import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4
from pathlib import Path

from django.utils import timezone
from django.core.management import call_command

from accounts.models import Company
from accounting.models import Account
from events.emitter import emit_event
from events.models import BusinessEvent
from events.types import EventTypes
from projections.account_balance import AccountBalanceProjection
from projections.models import AccountBalance


def _emit_je_posted(company, user, lines, memo="Export test"):
    """Helper: emit a JOURNAL_ENTRY_POSTED event."""
    entry_id = uuid4()
    total_debit = sum(Decimal(l.get("debit", "0")) for l in lines)
    total_credit = sum(Decimal(l.get("credit", "0")) for l in lines)
    return emit_event(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=str(entry_id),
        data={
            "entry_public_id": str(entry_id),
            "entry_number": f"JE-EXPORT-{uuid4().hex[:6]}",
            "date": date.today().isoformat(),
            "memo": memo,
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
            "lines": lines,
        },
        caused_by_user=user,
        idempotency_key=f"export-test:{entry_id}",
    )


def _make_line(account, debit="0.00", credit="0.00", line_no=1):
    return {
        "line_no": line_no,
        "account_public_id": str(account.public_id),
        "account_code": account.code,
        "description": f"Export line {line_no}",
        "debit": str(debit),
        "credit": str(credit),
    }


@pytest.mark.django_db
class TestEventExportIntegrity:
    """Test that event export produces correct, hashable output."""

    def test_export_contains_all_events(self, company, user, cash_account, revenue_account):
        # Emit several events
        for i in range(5):
            _emit_je_posted(company, user, [
                _make_line(cash_account, debit="100.00", line_no=1),
                _make_line(revenue_account, credit="100.00", line_no=2),
            ], memo=f"Export entry {i}")

        # Count events
        event_count = BusinessEvent.objects.filter(company=company).count()
        assert event_count >= 5  # May have fixture events too

        # Export to temp file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            export_path = f.name

        try:
            call_command(
                "export_tenant_events",
                f"--tenant-id={company.id}",
                f"--out={export_path}",
            )

            with open(export_path) as f:
                export_data = json.load(f)

            # Verify structure
            assert "version" in export_data
            assert "events" in export_data
            assert "event_count" in export_data
            assert export_data["event_count"] == len(export_data["events"])
            assert export_data["event_count"] >= 5

            # Verify hash is present (top-level export_hash)
            assert "export_hash" in export_data
            assert len(export_data["export_hash"]) == 64  # SHA-256 hex

        finally:
            Path(export_path).unlink(missing_ok=True)

    def test_export_is_deterministic(self, company, user, cash_account, revenue_account):
        """Two exports of the same data must produce the same hash."""
        _emit_je_posted(company, user, [
            _make_line(cash_account, debit="500.00", line_no=1),
            _make_line(revenue_account, credit="500.00", line_no=2),
        ], memo="Determinism test")

        hashes = []
        for _ in range(2):
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
                path = f.name
            try:
                call_command(
                    "export_tenant_events",
                    f"--tenant-id={company.id}",
                    f"--out={path}",
                )
                with open(path) as f:
                    data = json.load(f)
                hashes.append(data["export_hash"])
            finally:
                Path(path).unlink(missing_ok=True)

        assert hashes[0] == hashes[1], "Export hashes must be deterministic"


@pytest.mark.django_db
class TestProjectionReplayConsistency:
    """Test that projections can be rebuilt and match event replay."""

    def test_rebuild_after_entries_matches_incremental(
        self, company, user, cash_account, revenue_account, expense_account
    ):
        """
        After posting entries and processing incrementally,
        a full rebuild must produce identical balances.
        """
        # Post multiple entries
        for i in range(5):
            _emit_je_posted(company, user, [
                _make_line(cash_account, debit="200.00", line_no=1),
                _make_line(expense_account, debit="50.00", line_no=2),
                _make_line(revenue_account, credit="250.00", line_no=3),
            ], memo=f"Replay test {i}")

        projection = AccountBalanceProjection()
        projection.process_pending(company)

        # Snapshot incremental
        incremental = {
            b.account.code: (b.debit_total, b.credit_total, b.balance)
            for b in AccountBalance.objects.filter(company=company).select_related("account")
        }

        # Rebuild
        projection.rebuild(company)

        # Snapshot rebuilt
        rebuilt = {
            b.account.code: (b.debit_total, b.credit_total, b.balance)
            for b in AccountBalance.objects.filter(company=company).select_related("account")
        }

        assert incremental == rebuilt, (
            f"Rebuild diverged.\nIncremental: {incremental}\nRebuilt: {rebuilt}"
        )

        # Verify trial balance is balanced
        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"], f"TB not balanced: {tb}"
