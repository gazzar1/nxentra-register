# tests/e2e/test_emit_rls_context_restore.py
"""The event-emit boundary RESTORES the caller's RLS session context (G1
shakedown finding, 2026-09-05).

``emit_event_no_actor`` runs the emit under its own tenant/bypass posture. It
used to clean up with ``clear_rls_context()`` — a bare RESET of both session
parameters — which destroyed any context an ENCLOSING caller had established.
``register_signup`` runs inside ``rls_bypass()``, drains projections (creating
the CompanyMembership read-model row), emits USER_PASSWORD_CHANGED, then reads
that membership back — and under real RLS enforcement found nothing:
``CompanyMembership.DoesNotExist`` → HTTP 500 at ``/api/auth/register/``.

Why no test ever saw it: on SQLite the RLS session parameters are no-ops, and
CI's Postgres role is the docker superuser, which ignores RLS entirely (even
FORCE). (settings.py's ``-c app.rls_bypass=on`` connection option is NOT in
play here — test_settings.py rebuilds DATABASES from TEST_DATABASE_URL without
it, so a RESET reads ''.) These proofs therefore (1) assert the restoration
contract on the GUC values themselves, with the ambient state set to exactly
what a RESET destroys ('on' + a company id) so BOTH halves discriminate for
any role, and (2) reproduce the production failure under a NOBYPASSRLS role
driving the real ``register_signup``. RLS is Postgres-only; skips on SQLite.
"""

import uuid

import pytest
from django.db import connection

from accounts import rls
from accounts.models import Company

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="row-level security is only provable on PostgreSQL",
    ),
]

# Same ephemeral role as the sibling RLS proofs (idempotent CREATE).
EPHEMERAL_RLS_ROLE = "nxentra_rls_e2e_role"


def _role_bypasses_rls() -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),"
            " (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        )
        rolsuper, rolbypassrls = cur.fetchone()
    return bool(rolsuper or rolbypassrls)


def _make_role_enforce_rls() -> bool:
    """SET ROLE to an ephemeral NOBYPASSRLS role when the bootstrap role bypasses
    RLS (the CI postgres superuser). Returns True if a RESET ROLE is owed."""
    if not _role_bypasses_rls():
        return False
    with connection.cursor() as cur:
        cur.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{EPHEMERAL_RLS_ROLE}') "
            f"THEN CREATE ROLE {EPHEMERAL_RLS_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$;"
        )
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {EPHEMERAL_RLS_ROLE}")
        cur.execute(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {EPHEMERAL_RLS_ROLE}")
        cur.execute(f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {EPHEMERAL_RLS_ROLE}")
        cur.execute(f"SET ROLE {EPHEMERAL_RLS_ROLE}")
    return True


def _make_company():
    uid = uuid.uuid4().hex[:8]
    with rls.rls_bypass():
        return Company.objects.create(
            public_id=uuid.uuid4(),
            name=f"RLS Co {uid}",
            slug=f"rls-{uid}",
            default_currency="EGP",
            functional_currency="EGP",
            fiscal_year_start_month=1,
            is_active=True,
        )


def _emit_for(company, tag: str):
    """A no-op-projection event (COMPANY_UPDATED with empty changes)."""
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes

    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.COMPANY_UPDATED,
        aggregate_type="Company",
        aggregate_id=str(company.public_id),
        idempotency_key=f"rls-restore-{tag}-{uuid.uuid4().hex}",
        data={"company_public_id": str(company.public_id), "changes": {}},
    )


# =============================================================================
# 1. The restoration contract — on the GUC values, discriminating for any role
# =============================================================================


def test_emit_restores_the_callers_ambient_rls_values():
    """The register_signup posture: an enclosing rls_bypass() ('on') plus a
    company id that DIFFERS from the emitting company. A RESET reads '' for
    both (not 'on', not the id), so each assertion fails on the pre-fix tree
    and passes only when the emit restores what it found."""
    emitting = _make_company()
    ambient = _make_company()
    try:
        with rls.rls_bypass():
            rls.set_current_company_id(ambient.id)
            assert rls.is_rls_bypassed()
            assert rls.get_current_company_id() == ambient.id

            _emit_for(emitting, "restore")

            assert rls.is_rls_bypassed(), "emit RESET app.rls_bypass (now '') instead of restoring the enclosing 'on'"
            assert rls.get_current_company_id() == ambient.id, (
                "emit RESET the ambient company id instead of restoring it"
            )
    finally:
        rls.set_current_company_id(None)
        rls.set_rls_bypass(True)


def test_rls_scope_is_reentrant_inside_rls_bypass_and_restores_on_exception():
    company = _make_company()
    with rls.rls_bypass():
        before_company = rls.get_current_company_id()
        assert rls.is_rls_bypassed()

        with rls.rls_scope(company_id=company.id, bypass=False):
            assert rls.get_current_company_id() == company.id
            assert not rls.is_rls_bypassed()
        assert rls.is_rls_bypassed()
        assert rls.get_current_company_id() == before_company

        with pytest.raises(RuntimeError, match="boom"):
            with rls.rls_scope(company_id=company.id, bypass=False):
                raise RuntimeError("boom")
        assert rls.is_rls_bypassed()
        assert rls.get_current_company_id() == before_company


# =============================================================================
# 2. The production failure, reproduced end-to-end
# =============================================================================


def test_register_signup_succeeds_under_a_least_privilege_rls_role():
    """The G1 shakedown posture, in CI: RLS actually enforced (a NOBYPASSRLS
    role — CI's own postgres role is a superuser, the one reason the suite never
    saw this), no bypass in the session, the real register_signup. Before the
    fix this raised CompanyMembership.DoesNotExist from commands.py — the
    /api/auth/register/ 500 — because the emit boundary had reset the enclosing
    rls_bypass()."""
    with connection.cursor() as cur:
        cur.execute("RESET app.rls_bypass")
    # Premise guard: a RESET must NOT land on a bypass default. The test settings
    # carry none; if one is ever introduced, this proof goes blind and must say so.
    assert not rls.is_rls_bypassed(), (
        "the test connection carries a bypass reset-default; this proof cannot discriminate"
    )

    owed_reset_role = _make_role_enforce_rls()
    try:
        assert not _role_bypasses_rls(), "the probing role must be subject to RLS"

        from accounts.commands import register_signup

        uid = uuid.uuid4().hex[:8]
        result = register_signup(
            email=f"rls-probe-{uid}@example.com",
            password="Rls-Pr0be-Passw0rd!",
            company_name=f"RLS Reg {uid}",
            name="RLS Probe",
            phone="",
            default_currency="EGP",
            tos_accepted=True,
        )
        assert result.success, f"register_signup failed under enforced RLS: {result.error}"

        membership = result.data["membership"]
        with rls.rls_bypass():
            from accounts.models import CompanyMembership

            assert CompanyMembership.objects.filter(public_id=membership.public_id).exists()
    finally:
        if owed_reset_role:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")
        rls.set_rls_bypass(True)
