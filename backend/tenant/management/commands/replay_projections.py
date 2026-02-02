"""
Replay projections for a tenant database.

This command rebuilds all projections (read models) from the event store
for a specific database. Used after importing events to a dedicated
tenant database.

Usage:
    python manage.py replay_projections --db-alias tenant_acme --company-id 123
    python manage.py replay_projections --db-alias tenant_acme --company-id 123 --projection account_balance
    python manage.py replay_projections --db-alias tenant_acme --company-id 123 --rebuild

Options:
    --rebuild: Clear existing projection data and replay from scratch
    --projection: Only replay a specific projection
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from accounts.models import Company
from accounts.rls import rls_bypass
from tenant.context import tenant_context


class Command(BaseCommand):
    help = "Replay projections for a tenant database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--db-alias",
            type=str,
            required=True,
            help="Database alias to replay projections in",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            required=True,
            help="Company ID to replay projections for",
        )
        parser.add_argument(
            "--projection",
            type=str,
            help="Specific projection to replay (e.g., account_balance)",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Clear projection data and rebuild from scratch",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )

    def handle(self, *args, **options):
        from projections.base import projection_registry

        db_alias = options["db_alias"]
        company_id = options["company_id"]

        # Verify database exists
        if db_alias not in settings.DATABASES:
            raise CommandError(
                f"Database alias '{db_alias}' not found in settings.DATABASES. "
                f"Available: {list(settings.DATABASES.keys())}"
            )

        # Verify company exists
        with rls_bypass():
            try:
                company = Company.objects.get(id=company_id)
            except Company.DoesNotExist:
                raise CommandError(f"Company with ID {company_id} not found")

        self.stdout.write(
            f"Replaying projections for {company.name} (ID: {company.id}) on database '{db_alias}'"
        )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        # Get projections to process
        all_projections = projection_registry.all()

        if options["projection"]:
            projection = projection_registry.get(options["projection"])
            if not projection:
                available = [p.name for p in all_projections]
                raise CommandError(
                    f"Unknown projection: {options['projection']}. "
                    f"Available: {available}"
                )
            projections_to_run = [projection]
        else:
            projections_to_run = all_projections

        self.stdout.write(f"Projections to process: {[p.name for p in projections_to_run]}")

        # Set up tenant context
        with tenant_context(company_id, db_alias, is_shared=False):
            with rls_bypass():
                total_processed = 0

                for projection in projections_to_run:
                    self.stdout.write(f"\nProcessing: {projection.name}")
                    self.stdout.write(f"  Consumes: {projection.consumes}")

                    if options["dry_run"]:
                        # Count events that would be processed
                        from events.models import BusinessEvent, EventBookmark

                        bookmark = EventBookmark.objects.using(db_alias).filter(
                            consumer_name=projection.name,
                            company_id=company_id,
                        ).first()

                        if options["rebuild"] or not bookmark:
                            # Would process all events
                            count = BusinessEvent.objects.using(db_alias).filter(
                                company_id=company_id,
                                event_type__in=projection.consumes,
                            ).count()
                        else:
                            # Would process events after bookmark
                            count = BusinessEvent.objects.using(db_alias).filter(
                                company_id=company_id,
                                event_type__in=projection.consumes,
                                company_sequence__gt=bookmark.last_event.company_sequence if bookmark.last_event else 0,
                            ).count()

                        self.stdout.write(f"  Would process: {count} events")
                        total_processed += count
                        continue

                    # Actually run the projection
                    try:
                        if options["rebuild"]:
                            processed = projection.rebuild(company, using=db_alias)
                            self.stdout.write(
                                self.style.SUCCESS(f"  Rebuilt: {processed} events")
                            )
                        else:
                            processed = projection.process_pending(company, using=db_alias)
                            self.stdout.write(f"  Processed: {processed} events")

                        total_processed += processed

                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"  ERROR: {e}")
                        )
                        raise CommandError(f"Projection {projection.name} failed: {e}")

                self.stdout.write("")
                self.stdout.write(
                    self.style.SUCCESS(f"Total events processed: {total_processed}")
                )
