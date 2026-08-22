# tests/e2e/test_a5_pr3a_import_rejected_row_rls.py
"""A5-PR3a — prove import_rejected_row joined the RLS tenant-isolation regime.

Codex (PR #128 round-4) flagged that the new per-company evidence table
``import_rejected_row`` was created without row-level security, so any importer or
maintenance query run under a tenant context without an explicit company predicate
could read another company's preserved raw financial rows. Migration
``accounting/0041_import_rejected_row_rls`` attaches the same ENABLE+FORCE RLS and
``rls_tenant_isolation`` policy every other company-scoped table carries. RLS is
Postgres-only, so these assertions skip on the SQLite test backend.

Two proofs:
1. Schema state — the table is ROW LEVEL SECURITY + FORCE, and the
   ``rls_tenant_isolation`` policy exists with the canonical tenant predicate.
2. Behavioral isolation — with RLS genuinely enforced (a NOBYPASSRLS role, bypass
   OFF) and ``app.current_company_id`` set to company A, company B's reject row is
   invisible, and a write for B is refused.
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


def _make_reject(company):
    from accounting.models import ImportRejectedRow

    with rls.rls_bypass():
        return ImportRejectedRow.objects.create(
            company=company,
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


def test_import_rejected_row_table_is_force_rls_with_tenant_policy():
    """Schema proof: the migration enabled+forced RLS and created the canonical
    rls_tenant_isolation policy with the tenant GUC predicate."""
    with connection.cursor() as cur:
        cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'import_rejected_row'")
        row = cur.fetchone()
        assert row is not None, "import_rejected_row table not found"
        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity and relforcerowsecurity, (
            f"import_rejected_row must be ROW LEVEL SECURITY + FORCE; got {row}"
        )

        cur.execute(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE tablename = 'import_rejected_row' AND policyname = 'rls_tenant_isolation'"
        )
        pol = cur.fetchone()
        assert pol is not None, "rls_tenant_isolation policy missing on import_rejected_row"
        _, qual, with_check = pol
        for clause in (qual, with_check):
            assert "company_id" in clause, f"policy predicate must scope on company_id; got {clause}"
            assert "current_company_id" in clause, f"policy predicate must read the tenant GUC; got {clause}"


def test_import_rejected_row_is_tenant_isolated_under_enforced_rls():
    """Behavioral proof: with bypass OFF and company A's context, company B's
    reject row is invisible and a write for B is refused by WITH CHECK."""
    from accounting.models import ImportRejectedRow

    role_applied = _make_role_enforce_rls()
    try:
        if _role_bypasses_rls():
            pytest.skip("effective role bypasses RLS — cannot prove isolation")

        a = _make_company()
        b = _make_company()
        a_reject = _make_reject(a)
        _make_reject(b)

        # Enforce: bypass OFF, tenant context = company A.
        rls.set_rls_bypass(False)
        rls.set_current_company_id(a.id)

        visible = set(ImportRejectedRow.objects.values_list("id", flat=True))
        assert a_reject.id in visible, "company A must see its own reject row"
        assert all(ImportRejectedRow.objects.get(pk=rid).company_id == a.id for rid in visible), (
            "only company A's rows may be visible under A's tenant context"
        )

        # A write scoped to company B must be refused by the policy's WITH CHECK.
        with pytest.raises(Exception):
            ImportRejectedRow.objects.create(
                company=b,
                source_kind=ImportRejectedRow.SourceKind.BANK,
                import_batch_id=uuid.uuid4(),
                row_index=9,
                raw_row={"x": 1},
                reason_code=ImportRejectedRow.ReasonCode.UNPARSEABLE_DATE,
                dedup_hash=uuid.uuid4().hex,
            )
    finally:
        rls.set_current_company_id(None)
        rls.set_rls_bypass(True)
        if role_applied:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")
