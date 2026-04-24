"""
Management command to manually verify a user's email.

Usage:
    python manage.py verify_user --email user@example.com
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User


class Command(BaseCommand):
    help = "Manually verify a user's email address"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            required=True,
            help="Email of the user to verify",
        )

    def handle(self, *args, **options):
        email = options["email"].lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User with email '{email}' not found"))
            return

        if user.email_verified:
            self.stdout.write(self.style.WARNING(f"User '{email}' is already verified"))
            return

        # Use update() to bypass the model save() guard
        User.objects.filter(pk=user.pk).update(
            email_verified=True,
            email_verified_at=timezone.now(),
            is_approved=True,
            approved_at=timezone.now(),
        )

        self.stdout.write(self.style.SUCCESS(f"User '{email}' has been verified and approved!"))
