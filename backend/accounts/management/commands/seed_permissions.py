# accounts/management/commands/seed_permissions.py


from django.core.management.base import BaseCommand
from accounts.models import NxPermission
from projections.write_barrier import bootstrap_writes_allowed
from accounts.permission_defaults import all_permission_codes

class Command(BaseCommand):
    help = "Seed default permissions to the database"

    def handle(self, *args, **options):
        created = 0
        updated = 0

        with bootstrap_writes_allowed():
            for code in sorted(all_permission_codes()):
                _, was_created = NxPermission.objects.update_or_create(
                    code=code,
                    defaults={
                        "name": code,       # placeholder
                        "name_ar": "",
                        "module": code.split(".")[0],
                        "description": "",
                        "default_for_roles": [],
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done! Created {created}, updated {updated}."))
