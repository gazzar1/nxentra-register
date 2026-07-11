# accounts/management/commands/backfill_role_permissions.py
"""
A160: backfill newly-added ROLE_DEFAULTS permissions onto existing
memberships.

When a permission code is added to ROLE_DEFAULTS (e.g. the backups.*
family), memberships created BEFORE the change have no explicit row for
it. OWNERs mostly don't notice (implicit allow) — except for
SENSITIVE_PERMISSIONS like backups.restore, which require the explicit
row even for OWNER. ADMIN/USER/VIEWER need explicit rows for everything.

Run after deploying a ROLE_DEFAULTS change:

    python manage.py backfill_role_permissions

Idempotent: grant_role_defaults only adds missing codes and never
removes explicit grants.
"""

from django.core.management.base import BaseCommand

from accounts.permissions import grant_defaults_to_all_memberships
from projections.write_barrier import command_writes_allowed


class Command(BaseCommand):
    help = "Grant missing ROLE_DEFAULTS permissions to every existing membership (idempotent)."

    def handle(self, *args, **options):
        with command_writes_allowed():
            result = grant_defaults_to_all_memberships(only_if_empty=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete: {result['memberships_updated']} membership(s) updated, "
                f"{result['permissions_granted']} permission(s) granted."
            )
        )
