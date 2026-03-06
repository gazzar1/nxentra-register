# properties/management/commands/check_lease_expiry.py
"""
Management command to check lease expiry alerts.

Usage:
    python manage.py check_lease_expiry
"""

from django.core.management.base import BaseCommand

from properties.tasks import check_lease_expiry


class Command(BaseCommand):
    help = "Check for lease expiry alerts (90/60/30 days) and emit events."

    def handle(self, *args, **options):
        self.stdout.write("Checking lease expiry alerts...")
        check_lease_expiry()
        self.stdout.write(self.style.SUCCESS("Lease expiry check complete."))
