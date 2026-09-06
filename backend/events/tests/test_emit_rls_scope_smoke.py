# events/tests/test_emit_rls_scope_smoke.py
"""Battery (SQLite) smoke for the emit boundary's RLS-scope refactor.

On a non-Postgres backend the RLS session parameters are no-ops, so the
contract here is only that ``emit_event_no_actor`` inside ``rls_bypass()``
still emits, and ``rls_scope()`` behaves as a plain reentrant context
manager. The real restoration proofs (GUC values, the least-privilege
register reproduction) live in ``tests/e2e/test_emit_rls_context_restore.py``.
"""

import uuid

import pytest

from accounts import rls
from accounts.models import Company
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes


@pytest.fixture
def company(db):
    uid = uuid.uuid4().hex[:8]
    return Company.objects.create(
        public_id=uuid.uuid4(),
        name=f"Scope Co {uid}",
        slug=f"scope-{uid}",
        default_currency="EGP",
        functional_currency="EGP",
        fiscal_year_start_month=1,
        is_active=True,
    )


def test_emit_inside_rls_bypass_still_emits(company):
    with rls.rls_bypass():
        event = emit_event_no_actor(
            company=company,
            event_type=EventTypes.COMPANY_UPDATED,
            aggregate_type="Company",
            aggregate_id=str(company.public_id),
            idempotency_key=f"scope-smoke-{uuid.uuid4().hex}",
            data={"company_public_id": str(company.public_id), "changes": {}},
        )
    assert BusinessEvent.objects.filter(pk=event.pk).exists()


def test_rls_scope_yields_and_restores_on_exception(company):
    with rls.rls_scope(company_id=company.id, bypass=False):
        pass
    with pytest.raises(RuntimeError, match="boom"):
        with rls.rls_scope(company_id=company.id, bypass=True):
            raise RuntimeError("boom")
