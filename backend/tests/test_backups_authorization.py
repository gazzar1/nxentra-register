# tests/test_backups_authorization.py
"""
A160 — the backups app had ZERO authorization: list/export/download/
restore-overwrite/delete were gated only by IsAuthenticated + company
scoping, so any member (including VIEWER) could export or OVERWRITE the
entire company's books.

Now every endpoint requires an explicit permission:
- backups.view      (list + detail)         OWNER implicit, ADMIN default
- backups.export                            OWNER implicit, ADMIN default
- backups.download                          OWNER implicit, ADMIN default
- backups.delete                            OWNER implicit
- backups.restore   SENSITIVE — explicit grant required even for OWNER
"""

import io
import zipfile
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from accounts.models import CompanyMembership
from backups.models import BackupRecord

pytestmark = pytest.mark.django_db

User = get_user_model()


def _member(company, role, tag):
    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"{tag}-{uuid4().hex[:6]}@test.com",
        password="x",
        name=tag,
    )
    user.active_company = company
    user.save()
    membership = CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=role,
        is_active=True,
    )
    return user, membership


def _grant(membership, code):
    from accounts.models import CompanyMembershipPermission, NxPermission
    from projections.write_barrier import command_writes_allowed

    with command_writes_allowed():
        perm, _ = NxPermission.objects.get_or_create(code=code, defaults={"name": code, "module": "backups"})
        CompanyMembershipPermission.objects.get_or_create(
            membership=membership, company=membership.company, permission=perm
        )


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _tiny_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
    return buf.getvalue()


@pytest.fixture
def completed_backup(company, user):
    return BackupRecord.objects.create(
        company=company,
        backup_type=BackupRecord.BackupType.MANUAL,
        status=BackupRecord.Status.COMPLETED,
        created_by=user,
    )


class TestViewerDenied:
    def test_viewer_cannot_list_backups(self, company, owner_membership):
        viewer, _ = _member(company, CompanyMembership.Role.VIEWER, "viewer")
        resp = _client_for(viewer).get("/api/backups/")
        assert resp.status_code == 403, resp.content

    def test_viewer_cannot_export_and_no_record_is_created(self, company, owner_membership):
        viewer, _ = _member(company, CompanyMembership.Role.VIEWER, "viewer")
        resp = _client_for(viewer).post("/api/backups/export/")
        assert resp.status_code == 403, resp.content
        assert BackupRecord.objects.filter(company=company).count() == 0, (
            "the gate must run before any BackupRecord is created"
        )

    def test_viewer_cannot_download(self, company, owner_membership, completed_backup):
        viewer, _ = _member(company, CompanyMembership.Role.VIEWER, "viewer")
        resp = _client_for(viewer).get(f"/api/backups/{completed_backup.public_id}/download/")
        assert resp.status_code == 403, resp.content

    def test_viewer_cannot_delete(self, company, owner_membership, completed_backup):
        viewer, _ = _member(company, CompanyMembership.Role.VIEWER, "viewer")
        resp = _client_for(viewer).delete(f"/api/backups/{completed_backup.public_id}/")
        assert resp.status_code == 403, resp.content
        assert BackupRecord.objects.filter(pk=completed_backup.pk).exists()


class TestRestoreIsSensitive:
    def test_user_role_cannot_restore(self, company, owner_membership):
        member, _ = _member(company, CompanyMembership.Role.USER, "user")
        resp = _client_for(member).post("/api/backups/restore/", {"file": io.BytesIO(_tiny_zip())})
        assert resp.status_code == 403, resp.content
        assert BackupRecord.objects.filter(company=company).count() == 0

    def test_owner_without_explicit_grant_cannot_restore(self, company):
        """backups.restore is in SENSITIVE_PERMISSIONS — OWNER implicit
        allow does NOT apply. (New memberships get the explicit row via
        ROLE_DEFAULTS; this owner deliberately has no grants.)"""
        owner, _ = _member(company, CompanyMembership.Role.OWNER, "bareowner")
        resp = _client_for(owner).post("/api/backups/restore/", {"file": io.BytesIO(_tiny_zip())})
        assert resp.status_code == 403, resp.content

    def test_owner_with_grant_passes_the_gate(self, company):
        owner, membership = _member(company, CompanyMembership.Role.OWNER, "grantedowner")
        _grant(membership, "backups.restore")
        resp = _client_for(owner).post("/api/backups/restore/", {"file": io.BytesIO(_tiny_zip())})
        # Past authorization: the tiny ZIP fails validation (400), not 403.
        assert resp.status_code == 400, resp.content


class TestAdminScope:
    def test_admin_with_defaults_can_list_and_export_but_not_delete(self, company, completed_backup):
        from accounts.permissions import grant_role_defaults
        from projections.write_barrier import command_writes_allowed

        admin, membership = _member(company, CompanyMembership.Role.ADMIN, "admin")
        with command_writes_allowed():
            grant_role_defaults(membership)
        client = _client_for(admin)

        assert client.get("/api/backups/").status_code == 200
        assert client.post("/api/backups/export/").status_code in (201, 409, 500)
        assert client.delete(f"/api/backups/{completed_backup.public_id}/").status_code == 403
        assert client.post("/api/backups/restore/", {"file": io.BytesIO(_tiny_zip())}).status_code == 403

    def test_owner_implicit_allow_covers_non_sensitive_endpoints(self, company, completed_backup):
        owner, _ = _member(company, CompanyMembership.Role.OWNER, "implicitowner")
        client = _client_for(owner)
        assert client.get("/api/backups/").status_code == 200
        assert client.get(f"/api/backups/{completed_backup.public_id}/").status_code == 200
