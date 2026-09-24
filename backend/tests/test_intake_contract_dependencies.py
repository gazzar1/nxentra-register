# tests/test_intake_contract_dependencies.py
"""
The fresh-isolated-pilot runbook's intake contract (§I definition,
`TASK_RECEIVED_AT`) reads `django_celery_results.TaskResult.date_started`,
a field that django-celery-results added in 2.6.0 (migration
`0012_taskresult_date_started`). PR #153 raised the dependency floor to
match; this test pins the field (and the migration) so a future
requirements edit cannot silently reopen the gap Codex flagged on that PR.
"""

import pytest
from django.db.migrations.recorder import MigrationRecorder
from django_celery_results.models import TaskResult

pytestmark = pytest.mark.django_db


def test_taskresult_carries_date_started():
    field = TaskResult._meta.get_field("date_started")
    assert field.get_internal_type() == "DateTimeField"


def test_date_started_migration_is_applied():
    applied = {(m.app, m.name) for m in MigrationRecorder.Migration.objects.all()}
    assert ("django_celery_results", "0012_taskresult_date_started") in applied, (
        "django-celery-results >= 2.6.0 is the pinned floor: its 0012 migration must be applied"
    )
