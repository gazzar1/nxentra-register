"""
Management command to promote a user to superuser.

Usage:
    python manage.py make_superuser --email user@example.com
    python manage.py make_superuser --list  # List all superusers
"""

from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = "Promote a user to superuser or list existing superusers"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            help="Email of the user to promote to superuser",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all superusers",
        )
        parser.add_argument(
            "--demote",
            action="store_true",
            help="Demote user from superuser (use with --email)",
        )

    def handle(self, *args, **options):
        if options["list"]:
            self.list_superusers()
            return

        email = options.get("email")
        if not email:
            self.stdout.write(
                self.style.ERROR("Please provide --email or use --list")
            )
            return

        try:
            user = User.objects.get(email=email.lower().strip())
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"User with email '{email}' not found")
            )
            self.stdout.write("\nAvailable users:")
            for u in User.objects.all()[:10]:
                self.stdout.write(f"  - {u.email}")
            return

        if options["demote"]:
            user.is_superuser = False
            user.is_staff = False
            user.save()
            self.stdout.write(
                self.style.SUCCESS(f"User '{email}' has been demoted from superuser")
            )
        else:
            user.is_superuser = True
            user.is_staff = True
            user.save()
            self.stdout.write(
                self.style.SUCCESS(f"User '{email}' is now a superuser!")
            )

    def list_superusers(self):
        superusers = User.objects.filter(is_superuser=True)
        staff = User.objects.filter(is_staff=True, is_superuser=False)

        self.stdout.write("\n=== SUPERUSERS ===")
        if superusers.exists():
            for u in superusers:
                self.stdout.write(f"  - {u.email} (name: {u.name or '-'})")
        else:
            self.stdout.write(self.style.WARNING("  No superusers found!"))

        self.stdout.write("\n=== STAFF (not superuser) ===")
        if staff.exists():
            for u in staff:
                self.stdout.write(f"  - {u.email} (name: {u.name or '-'})")
        else:
            self.stdout.write("  No staff-only users")

        self.stdout.write(f"\nTotal users: {User.objects.count()}")
