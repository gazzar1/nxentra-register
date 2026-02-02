#accounts/tests/test_permissions_defaults.py

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.apps import apps


from accounts.models import Company, CompanyMembership
from accounts.authz import ActorContext
from accounts.permissions import grant_role_defaults
from accounts.commands import add_user_to_company
from accounts.models import CompanyMembershipPermission, NxPermission
#from events.models import BusinessEvent
from events.types import EventTypes



EventModel = apps.get_model("events", "BusinessEvent")  # <-- replace BusinessEvent with your real model class name



User = get_user_model()


class TestPermissionDefaults(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="C1", slug="c1")
        self.owner = User.objects.create_user(email="o@test.com", password="pass12345")
        self.user = User.objects.create_user(email="u@test.com", password="pass12345")
        self.admin = User.objects.create_user(email="a@test.com", password="pass12345")

        self.owner_m = CompanyMembership.objects.create(user=self.owner, company=self.company, role="OWNER", is_active=True)
        self.user_m = CompanyMembership.objects.create(user=self.user, company=self.company, role="USER", is_active=True)
        self.admin_m = CompanyMembership.objects.create(user=self.admin, company=self.company, role="ADMIN", is_active=True)

        grant_role_defaults(self.owner_m, granted_by=self.owner)
        grant_role_defaults(self.user_m, granted_by=self.owner)
        grant_role_defaults(self.admin_m, granted_by=self.owner)

    def test_user_cannot_manage_users(self):
        perms = frozenset(self.user_m.permissions.values_list("code", flat=True))
        actor = ActorContext(user=self.user, company=self.company, membership=self.user_m, perms=perms)
        self.assertFalse(actor.has("company.manage_users"))

    def test_owner_can_invite_users(self):
        perms = frozenset(self.owner_m.permissions.values_list("code", flat=True))
        actor = ActorContext(user=self.owner, company=self.company, membership=self.owner_m, perms=perms)
        self.assertTrue(actor.has("company.invite_users"))

    def test_admin_permissions_are_real(self):
        """
        This test is the decision point:
        - If ADMIN is god-mode, this passes even if permission isn't granted.
        - If ADMIN is permission-based, this should depend on defaults/grants.
        """
        perms = frozenset(self.admin_m.permissions.values_list("code", flat=True))
        actor = ActorContext(user=self.admin, company=self.company, membership=self.admin_m, perms=perms)
        self.assertTrue(actor.has("company.manage_users"))
        
    def test_admin_revocation_actually_blocks(self):
    # Remove a permission explicitly
        perm = NxPermission.objects.get(code="company.manage_users")
        CompanyMembershipPermission.objects.filter(membership=self.admin_m, permission=perm).delete()

        perms = frozenset(self.admin_m.permissions.values_list("code", flat=True))
        actor = ActorContext(user=self.admin, company=self.company, membership=self.admin_m, perms=perms)

        self.assertFalse(actor.has("company.manage_users"))


class TestAddUserToCompanyGrantsDefaults(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="C1", slug="c1")

        self.owner = User.objects.create_user(email="o@test.com", password="pass12345")
        self.owner_m = CompanyMembership.objects.create(
            user=self.owner, company=self.company, role="OWNER", is_active=True
        )
        grant_role_defaults(self.owner_m, granted_by=self.owner)

        perms = frozenset(self.owner_m.permissions.values_list("code", flat=True))
        self.actor = ActorContext(user=self.owner, company=self.company, membership=self.owner_m, perms=perms)

        self.other = User.objects.create_user(email="u@test.com", password="pass12345")

    def test_add_user_grants_defaults(self):
        res = add_user_to_company(self.actor, user_id=self.other.id, role="USER")
        self.assertTrue(res.success)

        m = CompanyMembership.objects.get(user=self.other, company=self.company)
        codes = set(m.permissions.values_list("code", flat=True))

        self.assertIn("company.switch", codes)
        self.assertIn("company.view", codes)
        self.assertIn("journal.create", codes)
    def test_reactivate_emits_membership_reactivated(self):
        # 1) Create an inactive membership first
        m = CompanyMembership.objects.create(
            user=self.other,
            company=self.company,
            role="USER",
            is_active=False,
        )

        # 2) Call add_user_to_company -> should reactivate
        res = add_user_to_company(self.actor, user_id=self.other.id, role="USER")
        self.assertTrue(res.success)

        # 3) Reload membership and assert reactivated
        m.refresh_from_db()
        self.assertTrue(m.is_active)

        # 4) Assert the emitted event type
        ev = EventModel.objects.filter(
            aggregate_type="CompanyMembership",
            aggregate_id=str(m.public_id),
            event_type=EventTypes.MEMBERSHIP_REACTIVATED,
        ).order_by("-id").first()

        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, EventTypes.MEMBERSHIP_REACTIVATED)
        self.assertEqual(ev.aggregate_type, "CompanyMembership")
        self.assertEqual(ev.aggregate_id, str(m.public_id))


