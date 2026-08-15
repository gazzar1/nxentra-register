# tests/e2e/test_a4_control_plane_serialization.py
"""A4 control-plane integrity — restore vs. pilot activation, serialized on the
Company admission lock (real PostgreSQL, two connections).

``backups.importer.restore_company`` is the canonical restore boundary: it validates
the archive (side-effect-free) BEFORE admission, then opens
``serialized_company_admission`` (Company admission lock, FOR NO KEY UPDATE) and
checks ``require_supported(locked_company, Capability.BACKUP_RESTORE)`` on the FRESH
locked row, holding the lock through the whole authoritative restore transaction.
``activate_pilot_profile`` keeps its own Company ``FOR UPDATE``. So exactly one
ordering can occur:

* activation-first  — activation owns the Company row; restore waits, then re-reads
  the ACTIVE profile and REFUSES (``PilotScopeBlocked``) before clearing anything;
* restore-first     — restore owns admission through its transaction; activation
  waits until restore commits, then evaluates the resulting durable state (a clean
  round-trip restore leaves the company activation-clean, so activation succeeds —
  serialization + post-restore preflight truth, never a forced refusal).

The two-connection threading harness is reused verbatim from the merged admission-
serialization suite; the restore boundary is exercised through the REAL production
``restore_company`` — a test never manufactures the lock.
"""

import threading

import pytest
from django.db import connection

from accounts.pilot_policy import PilotScopeBlocked
from backups.exporter import export_company
from backups.importer import restore_company

from .test_a4_runtime_admission_serialization import (
    _activation_clean,
    _activation_first,
    _fresh_profile,
    _pause_hook,
    _run_activation,
    _someone_is_lock_waiting,
    _wait,
    _Worker,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="restore/activation admission serialization is only provable on PostgreSQL",
    ),
]

ISO = "ISOLATED_SHADOW_LEDGER_V1"


@pytest.fixture(autouse=True)
def _suppress_projection_dispatch(monkeypatch):
    import events.emitter as emitter

    monkeypatch.setattr(emitter, "_schedule_projection_processing", lambda *a, **k: None)


def _events(company):
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent

    with rls_bypass():
        return BusinessEvent.objects.filter(company=company).count()


def test_activation_first_restore_refuses_before_clearing(company, owner_membership, monkeypatch):
    """Activation owns the Company row; the restore observably waits on the admission
    lock, then — reading the ACTIVE profile on the locked row — refuses with
    PilotScopeBlocked BEFORE any clear/import (the books are untouched)."""
    _activation_clean(company)
    backup, _ = export_company(company)
    events_before = _events(company)

    m = _activation_first(monkeypatch, company, lambda: restore_company(company, backup))

    assert isinstance(m.error, PilotScopeBlocked), f"restore must refuse under the active pilot: {m.error or m.result}"
    assert m.error.capability == "backup_restore"
    assert _fresh_profile(company) == ISO
    # Refused before Phase 1 clear — the event stream (and everything else) survives.
    assert _events(company) == events_before, "an activation-first restore must clear nothing"


def test_restore_first_activation_waits_then_evaluates_post_restore(company, owner_membership, monkeypatch):
    """Restore owns admission through its transaction; activation observably waits on
    the Company row until restore commits, then evaluates the (clean, round-tripped)
    post-restore state and succeeds — serialization, not a forced refusal."""
    import backups.importer as importer

    _activation_clean(company)
    backup, _ = export_company(company)

    admitted, release = threading.Event(), threading.Event()
    # Pause INSIDE the admission transaction (the capability check on the NONE profile
    # has already passed; the Company admission lock is held) so activation blocks.
    _pause_hook(monkeypatch, importer, "_clear_company_data", admitted, release)

    m = _Worker(lambda: restore_company(company, backup), "restore").start()
    _wait(admitted.is_set, "restore admitted (Company admission lock held) and paused inside its transaction")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")
    assert a.thread.is_alive(), "activation must block while restore holds the admission lock"

    release.set()
    m.join()
    a.join()

    assert m.error is None, f"restore worker crashed: {m.error}"
    refusal, output = a.result
    assert refusal is None, f"activation must succeed after a clean restore committed (serialized): {refusal}\n{output}"
    assert _fresh_profile(company) == ISO
