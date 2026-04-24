"""
Management command to assign a user to a company.

Usage:
    python manage.py assign_company --email user@example.com --company-id 1
    python manage.py assign_company --email user@example.com --create "My Company"
    python manage.py assign_company --list  # List all companies
"""

from django.core.management.base import BaseCommand

from accounts.models import Company, CompanyMembership, User


class Command(BaseCommand):
    help = "Assign a user to a company or create a new company for them"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email of the user",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            help="ID of the company to assign",
        )
        parser.add_argument(
            "--create",
            type=str,
            help="Create a new company with this name",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all companies",
        )

    def handle(self, *args, **options):
        if options["list"]:
            self.list_companies()
            return

        email = options.get("email")
        if not email:
            self.stdout.write(self.style.ERROR("Please provide --email"))
            return

        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User '{email}' not found"))
            return

        # Show current memberships
        current_memberships = CompanyMembership.objects.filter(user=user, is_active=True)
        if current_memberships.exists():
            self.stdout.write(f"\nUser '{email}' current memberships:")
            for m in current_memberships:
                self.stdout.write(f"  - {m.company.name} (ID: {m.company.id}, Role: {m.role})")

        if options.get("create"):
            self.create_company_for_user(user, options["create"])
        elif options.get("company_id"):
            self.assign_to_company(user, options["company_id"])
        else:
            self.stdout.write(self.style.WARNING("\nProvide --company-id or --create"))
            self.list_companies()

    def list_companies(self):
        companies = Company.objects.all().order_by("-created_at")
        self.stdout.write("\n=== COMPANIES ===")
        if companies.exists():
            for c in companies:
                member_count = CompanyMembership.objects.filter(company=c, is_active=True).count()
                self.stdout.write(f"  ID: {c.id} | {c.name} | Members: {member_count}")
        else:
            self.stdout.write(self.style.WARNING("  No companies found"))

    def assign_to_company(self, user, company_id):
        from accounts.authz import Actor
        from accounts.commands import add_user_to_company

        try:
            company = Company.objects.get(id=company_id)
        except Company.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Company with ID {company_id} not found"))
            return

        # Check if membership already exists
        existing = CompanyMembership.objects.filter(user=user, company=company).first()
        if existing:
            if existing.is_active:
                self.stdout.write(self.style.WARNING(f"User already has active membership in '{company.name}'"))
                return
            else:
                # Reactivate using direct SQL update
                CompanyMembership.objects.filter(pk=existing.pk).update(
                    is_active=True,
                    role=CompanyMembership.Role.OWNER,
                )
                User.objects.filter(pk=user.pk).update(active_company=company)
                self.stdout.write(self.style.SUCCESS(f"Reactivated membership for '{user.email}' in '{company.name}'"))
                return

        # Use the command to add user to company
        # Create a fake actor for the command (use the user themselves)
        actor = Actor(user=user, company=company, membership=None, permissions=set())

        result = add_user_to_company(
            actor=actor,
            user_id=user.id,
            role=CompanyMembership.Role.OWNER,
        )

        if not result.success:
            self.stdout.write(self.style.ERROR(f"Failed: {result.error}"))
            return

        # Set as active company
        User.objects.filter(pk=user.pk).update(active_company=company)

        self.stdout.write(self.style.SUCCESS(f"User '{user.email}' added to '{company.name}' as OWNER"))

    def create_company_for_user(self, user, company_name):
        from accounts.commands import create_company

        result = create_company(user, company_name, "USD")

        if not result.success:
            self.stdout.write(self.style.ERROR(f"Failed to create company: {result.error}"))
            return

        company = result.data["company"]
        self.stdout.write(
            self.style.SUCCESS(f"Created company '{company_name}' (ID: {company.id}) with '{user.email}' as OWNER")
        )
