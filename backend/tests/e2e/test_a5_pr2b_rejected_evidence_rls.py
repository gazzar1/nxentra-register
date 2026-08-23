# tests/e2e/test_a5_pr2b_rejected_evidence_rls.py
"""A5-PR2b — prove shopify_rejected_evidence joined the RLS tenant-isolation regime.

Founder decision (sub-decision 1, option b): the new evidence table carries the
canonical ENABLE+FORCE ``rls_tenant_isolation`` policy from birth — not the
historical no-policy posture of its shopify_* siblings — because it preserves
complete authenticated provider payloads (financial evidence + customer PII).
Migration ``shopify_connector/0024_shopify_rejected_evidence_rls`` (DDL through
``schema_editor.connection``, the accounting/0041 pattern). RLS is
Postgres-only, so these assertions skip on the SQLite test backend.

Three proofs (the PR #128 pattern + the founder's no-bypass writer requirement):
1. Schema state — ROW LEVEL SECURITY + FORCE and the canonical policy predicate.
2. Behavioral isolation — under a NOBYPASSRLS role with bypass OFF and company
   A's context, company B's evidence is invisible and a write for B is refused.
3. The evidence WRITER passes the policy on the company predicate alone — it
   establishes ``app.current_company_id`` itself and is granted NO bypass
   (the webhook session has no tenant context of its own).
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


def _make_store(company):
    from shopify_connector.models import ShopifyStore

    uid = uuid.uuid4().hex[:8]
    with rls.rls_bypass():
        return ShopifyStore.objects.create(
            company=company,
            shop_domain=f"rls-{uid}.myshopify.com",
            access_token="t",
            status=ShopifyStore.Status.ACTIVE,
        )


def _make_evidence(company, store, probe: str):
    from shopify_connector import rejected_evidence as re_mod
    from shopify_connector.models import ShopifyRejectedEvidence

    with rls.rls_bypass():
        row, _created = re_mod.record_rejected_evidence(
            store=store,
            resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
            rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
            rejection_message=f"probe {probe}",
            validation_errors=[],
            ingress=re_mod.poller_ingress("poller/orders-paid"),
            parsed_payload={"id": 1, "total_price": "abc", "probe": probe},
        )
    return row


def test_shopify_rejected_evidence_table_is_force_rls_with_tenant_policy():
    """Schema proof: 0024 enabled+forced RLS and created the canonical
    rls_tenant_isolation policy with the tenant GUC predicate."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'shopify_rejected_evidence'"
        )
        row = cur.fetchone()
        assert row is not None, "shopify_rejected_evidence table not found"
        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity and relforcerowsecurity, (
            f"shopify_rejected_evidence must be ROW LEVEL SECURITY + FORCE; got {row}"
        )

        cur.execute(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE tablename = 'shopify_rejected_evidence' AND policyname = 'rls_tenant_isolation'"
        )
        pol = cur.fetchone()
        assert pol is not None, "rls_tenant_isolation policy missing on shopify_rejected_evidence"
        _, qual, with_check = pol
        for clause in (qual, with_check):
            assert "company_id" in clause, f"policy predicate must scope on company_id; got {clause}"
            assert "current_company_id" in clause, f"policy predicate must read the tenant GUC; got {clause}"


def test_shopify_rejected_evidence_is_tenant_isolated_under_enforced_rls():
    """Behavioral proof (founder test #10): with bypass OFF and company A's
    context, company B's evidence is invisible and a write for B is refused by
    WITH CHECK — cross-company access is impossible."""
    from shopify_connector.models import ShopifyRejectedEvidence

    role_applied = _make_role_enforce_rls()
    try:
        if _role_bypasses_rls():
            pytest.skip("effective role bypasses RLS — cannot prove isolation")

        a = _make_company()
        b = _make_company()
        store_a = _make_store(a)
        store_b = _make_store(b)
        a_row = _make_evidence(a, store_a, "company-a")
        _make_evidence(b, store_b, "company-b")

        # Enforce: bypass OFF, tenant context = company A.
        rls.set_rls_bypass(False)
        rls.set_current_company_id(a.id)

        visible = set(ShopifyRejectedEvidence.objects.values_list("id", flat=True))
        assert a_row.id in visible, "company A must see its own evidence"
        assert all(ShopifyRejectedEvidence.objects.get(pk=rid).company_id == a.id for rid in visible), (
            "only company A's evidence may be visible under A's tenant context"
        )

        # A write scoped to company B must be refused by the policy's WITH CHECK.
        with pytest.raises(Exception):
            ShopifyRejectedEvidence.objects.create(
                company=b,
                store=store_b,
                store_public_id=store_b.public_id,
                shop_domain=store_b.shop_domain,
                resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
                ingress_kind=ShopifyRejectedEvidence.IngressKind.WEBHOOK,
                source_topic="orders/paid",
                parsed_payload={"x": 1},
                payload_hash=uuid.uuid4().hex,
                rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
                rejection_message="cross-tenant probe",
                validation_errors=[],
                dedup_hash=uuid.uuid4().hex,
            )
    finally:
        rls.set_current_company_id(None)
        rls.set_rls_bypass(True)
        if role_applied:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")


def test_evidence_writer_passes_policy_without_bypass():
    """The founder's no-broad-bypass requirement: record_rejected_evidence
    establishes app.current_company_id itself and the INSERT lands under a
    NOBYPASSRLS role with bypass OFF — the normal evidence write never needs a
    bypass. Supersession (same-company UPDATE) also passes under that context."""
    from shopify_connector import rejected_evidence as re_mod
    from shopify_connector.models import ShopifyRejectedEvidence

    role_applied = _make_role_enforce_rls()
    try:
        if _role_bypasses_rls():
            pytest.skip("effective role bypasses RLS — cannot prove the no-bypass write")

        a = _make_company()
        store_a = _make_store(a)

        rls.set_rls_bypass(False)
        rls.set_current_company_id(None)  # the webhook session starts with no tenant context

        row, created = re_mod.record_rejected_evidence(
            store=store_a,
            resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
            rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
            rejection_message="no-bypass write",
            validation_errors=[],
            ingress=re_mod.poller_ingress("poller/orders-paid"),
            parsed_payload={"id": 7, "total_price": "abc"},
            external_id="7",
        )
        assert created
        # Visible under the company context the writer established.
        assert ShopifyRejectedEvidence.objects.filter(pk=row.pk).exists()

        # The re-sight bump also passes without bypass.
        row2, created2 = re_mod.record_rejected_evidence(
            store=store_a,
            resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
            rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
            rejection_message="no-bypass write",
            validation_errors=[],
            ingress=re_mod.poller_ingress("poller/orders-paid"),
            parsed_payload={"id": 7, "total_price": "abc"},
            external_id="7",
        )
        assert not created2
        assert row2.pk == row.pk
        assert row2.occurrence_count == 2
    finally:
        rls.set_current_company_id(None)
        rls.set_rls_bypass(True)
        if role_applied:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")


def test_supersession_survives_cleared_rls_session():
    """The P1 the pre-push review confirmed: the healing paths call
    supersede_open_evidence AFTER emit_event_no_actor, whose `finally` CLEARS
    the connection's RLS session settings — under the FORCED policy the UPDATE
    would then silently match ZERO rows. supersede_open_evidence must
    re-establish the company context itself, so supersession is recorded even
    from a cleared session with no bypass."""
    from shopify_connector import rejected_evidence as re_mod
    from shopify_connector.models import ShopifyOrder, ShopifyRejectedEvidence

    role_applied = _make_role_enforce_rls()
    try:
        if _role_bypasses_rls():
            pytest.skip("effective role bypasses RLS — cannot prove the cleared-session supersession")

        a = _make_company()
        store_a = _make_store(a)
        row = _make_evidence(a, store_a, "to-be-superseded")
        # Give the evidence a trustworthy identity the healer will match.
        with rls.rls_bypass():
            ShopifyRejectedEvidence.objects.filter(pk=row.pk).update(external_id="1")
            from django.utils import timezone as tz

            order = ShopifyOrder.objects.create(
                company=a,
                store=store_a,
                shopify_order_id=1,
                shopify_order_number="1",
                total_price=1,
                subtotal_price=1,
                currency="EGP",
                order_date=tz.now().date(),
                shopify_created_at=tz.now(),
            )

        # Simulate the post-emit state: RLS session completely CLEARED, no
        # bypass — exactly what emit_event_no_actor's finally leaves behind.
        rls.set_rls_bypass(False)
        rls.set_current_company_id(None)

        updated = re_mod.supersede_open_evidence(
            store=store_a,
            resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
            external_id="1",
            order=order,
        )
        assert updated == 1, "supersession must not silently no-op from a cleared RLS session"
        with rls.rls_bypass():
            row.refresh_from_db()
        assert row.superseded_at is not None
        assert row.superseded_target_public_id == order.public_id
    finally:
        rls.set_current_company_id(None)
        rls.set_rls_bypass(True)
        if role_applied:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")
