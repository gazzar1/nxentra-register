# tests/test_a5_pr3a_import_rejected_row.py
"""A5-PR3a — the durable per-row import-reject record (ImportRejectedRow).

Foundation PR: the model + migration + operator visibility (the /finance/exceptions
sibling list + the /_health/alerts fold). Production writers land in A5-PR3b
(settlement) / A5-PR3c (bank); these tests exercise the model + visibility directly.
"""

import uuid

import pytest

pytestmark = pytest.mark.django_db


def _make_reject(company, **kw):
    from accounting.models import ImportRejectedRow

    defaults = dict(
        source_kind=ImportRejectedRow.SourceKind.SETTLEMENT,
        provider_code="paymob",
        source_filename="payout.csv",
        import_batch_id=uuid.uuid4(),
        row_index=1,
        raw_row={"gross": "abc"},
        reason_code=ImportRejectedRow.ReasonCode.MALFORMED_NUMERIC,
        reason_message="gross 'abc' is not a number",
        dedup_hash=uuid.uuid4().hex,
    )
    defaults.update(kw)
    return ImportRejectedRow.objects.create(company=company, **defaults)


def test_model_create_and_idempotent_dedup(company):
    """Re-importing the same file (same dedup_hash) must NOT duplicate the reject —
    update_or_create on (company, dedup_hash) is the idempotency contract."""
    from accounting.models import ImportRejectedRow

    h = "deadbeef" * 8
    row, created = ImportRejectedRow.objects.update_or_create(
        company=company,
        dedup_hash=h,
        defaults=dict(
            source_kind=ImportRejectedRow.SourceKind.BANK,
            import_batch_id=uuid.uuid4(),
            row_index=3,
            reason_code=ImportRejectedRow.ReasonCode.UNPARSEABLE_DATE,
            raw_row={"date": "??"},
        ),
    )
    assert created
    row2, created2 = ImportRejectedRow.objects.update_or_create(
        company=company, dedup_hash=h, defaults=dict(occurrence_count=row.occurrence_count + 1)
    )
    assert not created2 and row2.id == row.id
    assert ImportRejectedRow.objects.filter(company=company, dedup_hash=h).count() == 1


def test_mark_resolved_is_an_ack(company, user):
    row = _make_reject(company)
    assert not row.resolved
    row.mark_resolved(user, note="booked manually via journal")
    row.refresh_from_db()
    assert row.resolved
    assert row.resolved_by_id == user.id
    assert "booked" in row.resolution_note
    assert row.resolved_at is not None


def test_registered_in_backup_registry():
    """K#3 durable evidence must survive a company backup/restore (CI:
    test_backup_registry_completeness also enforces this)."""
    from backups.model_registry import get_export_registry

    assert "accounting.ImportRejectedRow" in get_export_registry()


def test_health_fold_unresolved_reject_makes_unhealthy(company, settings):
    """A dropped settlement/bank row is a real financial exception with no event —
    /_health/alerts must go unhealthy so the pinger pages a human."""
    from ops.health import compute_alert_state

    settings.ALERT_UNRESOLVED_FAILURES_MAX = 0
    _make_reject(company)
    state = compute_alert_state()
    assert state["unresolved_import_rejects"] >= 1
    assert state["status"] == "unhealthy"


def test_list_view_and_filters(company, user, owner_membership, api_client):
    _make_reject(company, source_kind="SETTLEMENT")
    _make_reject(company, source_kind="BANK", provider_code="", dedup_hash=uuid.uuid4().hex)

    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/reports/import-rejected-rows/")
    assert resp.status_code == 200, resp.data
    assert resp.data["total_count"] == 2

    resp_bank = api_client.get("/api/reports/import-rejected-rows/?source_kind=BANK")
    assert resp_bank.data["total_count"] == 1


def test_statement_fk_is_set_null_not_cascade():
    """Codex P1: deleting a bank statement must NOT erase its reject evidence.
    The statement FK is SET_NULL (nullable) so the reject row survives a
    statement delete, clearing only the link."""
    from django.db.models import deletion

    from accounting.models import ImportRejectedRow

    field = ImportRejectedRow._meta.get_field("statement")
    assert field.null is True
    assert field.remote_field.on_delete is deletion.SET_NULL


def test_list_view_clamps_negative_limit(company, user, owner_membership, api_client):
    """Codex P2: a negative ?limit must not produce a negative queryset slice
    (which 500s with 'Negative indexing not supported')."""
    _make_reject(company)
    api_client.force_authenticate(user=user)
    resp = api_client.get("/api/reports/import-rejected-rows/?limit=-1")
    assert resp.status_code == 200
    assert resp.data["total_count"] == 1


def test_resolve_view_owner_ok(company, user, owner_membership, api_client):
    row = _make_reject(company)
    api_client.force_authenticate(user=user)
    resp = api_client.post(
        f"/api/reports/import-rejected-rows/{row.id}/resolve/",
        {"resolution_note": "corrected via re-upload"},
        format="json",
    )
    assert resp.status_code == 200, resp.data
    row.refresh_from_db()
    assert row.resolved
    # Resolved rows drop out of the default (open) queue.
    assert api_client.get("/api/reports/import-rejected-rows/").data["total_count"] == 0
