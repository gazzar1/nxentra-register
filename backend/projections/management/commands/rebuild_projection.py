# projections/management/commands/rebuild_projection.py
"""
Management command to rebuild projections from events.

This is the core disaster recovery / maintenance tool for the projection system.
Events are the source of truth; projections can always be rebuilt.

Usage:
    # Rebuild a specific projection for a specific tenant
    python manage.py rebuild_projection --projection account_balance --tenant acme

    # Rebuild a specific projection for ALL tenants
    python manage.py rebuild_projection --projection account_balance --all-tenants

    # Rebuild ALL projections for a tenant
    python manage.py rebuild_projection --all --tenant acme

    # Dry run - show what would happen without writing
    python manage.py rebuild_projection --projection account_balance --tenant acme --dry-run

    # Verify integrity before rebuild
    python manage.py rebuild_projection --projection account_balance --tenant acme --verify-first

    # Force rebuild even if another rebuild is in progress
    python manage.py rebuild_projection --projection account_balance --tenant acme --force

    # List all available projections
    python manage.py rebuild_projection --list

    # Show status of all projections for a tenant
    python manage.py rebuild_projection --status --tenant acme
"""

import time
import logging
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Company
from events.models import BusinessEvent
from projections.base import projection_registry
from projections.models import ProjectionStatus, ProjectionAppliedEvent
from projections.write_barrier import projection_writes_allowed
from accounts.rls import rls_bypass

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Rebuild projections from events."""

    help = "Rebuild projections from the event store"

    def add_arguments(self, parser):
        # Target selection
        parser.add_argument(
            "--projection",
            type=str,
            help="Name of the projection to rebuild",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_projections",
            help="Rebuild ALL projections (use with caution)",
        )
        parser.add_argument(
            "--tenant",
            type=str,
            help="Company slug to rebuild for",
        )
        parser.add_argument(
            "--all-tenants",
            action="store_true",
            help="Rebuild for all active tenants",
        )

        # Operation modes
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without making changes",
        )
        parser.add_argument(
            "--verify-first",
            action="store_true",
            help="Verify event integrity before rebuilding",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force rebuild even if another is in progress",
        )

        # Progress options
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of events to process before logging progress (default: 500)",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress progress output",
        )

        # Information commands
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all available projections",
        )
        parser.add_argument(
            "--status",
            action="store_true",
            help="Show projection status for tenant",
        )

    def handle(self, *args, **options):
        # Handle information commands first
        if options["list"]:
            return self._list_projections()

        if options["status"]:
            return self._show_status(options)

        # Validate arguments
        self._validate_arguments(options)

        # Get projections and companies
        projections = self._get_projections(options)
        companies = self._get_companies(options)

        if not projections:
            raise CommandError("No projections to rebuild.")
        if not companies:
            raise CommandError("No companies to rebuild.")

        # Show plan
        self._show_plan(projections, companies, options)

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] No changes made."))
            return

        # Verify integrity first if requested
        if options["verify_first"]:
            self._verify_integrity(companies)

        # Execute rebuild
        self._rebuild_all(projections, companies, options)

    def _validate_arguments(self, options):
        """Validate command arguments."""
        has_projection = options["projection"] is not None
        has_all_projections = options["all_projections"]
        has_tenant = options["tenant"] is not None
        has_all_tenants = options["all_tenants"]

        # Must specify projection target
        if not has_projection and not has_all_projections:
            raise CommandError(
                "Must specify --projection <name> or --all"
            )

        if has_projection and has_all_projections:
            raise CommandError(
                "Cannot use --projection and --all together"
            )

        # Must specify tenant target
        if not has_tenant and not has_all_tenants:
            raise CommandError(
                "Must specify --tenant <slug> or --all-tenants"
            )

        if has_tenant and has_all_tenants:
            raise CommandError(
                "Cannot use --tenant and --all-tenants together"
            )

        # Warn about dangerous operations
        if has_all_projections and has_all_tenants:
            self.stdout.write(
                self.style.WARNING(
                    "\nWARNING: Rebuilding ALL projections for ALL tenants."
                )
            )
            self.stdout.write(
                "This is a major operation that may take a long time.\n"
            )

    def _list_projections(self):
        """List all available projections."""
        self.stdout.write("\nAvailable projections:\n")

        for name in projection_registry.names():
            projection = projection_registry.get(name)
            consumes = ", ".join(projection.consumes) if projection.consumes else "none"
            self.stdout.write(f"  {name}")
            self.stdout.write(f"    Events: {consumes}\n")

        self.stdout.write(f"\nTotal: {len(projection_registry.names())} projections")

    def _show_status(self, options):
        """Show projection status for a tenant."""
        if not options["tenant"]:
            raise CommandError("--status requires --tenant <slug>")

        try:
            company = Company.objects.get(slug=options["tenant"], is_active=True)
        except Company.DoesNotExist:
            raise CommandError(f"Company not found or inactive: {options['tenant']}")

        self.stdout.write(f"\nProjection status for: {company.name}\n")

        for name in projection_registry.names():
            projection = projection_registry.get(name)

            # Get or create status
            with rls_bypass():
                status, _ = ProjectionStatus.objects.get_or_create(
                    company=company,
                    projection_name=name,
                )

            # Get lag
            lag = projection.get_lag(company)

            # Format status
            if status.status == ProjectionStatus.Status.REBUILDING:
                status_str = self.style.WARNING(
                    f"REBUILDING ({status.progress_percent:.1f}%)"
                )
            elif status.status == ProjectionStatus.Status.ERROR:
                status_str = self.style.ERROR("ERROR")
            elif lag > 0:
                status_str = self.style.WARNING(f"READY (lag: {lag})")
            else:
                status_str = self.style.SUCCESS("READY")

            self.stdout.write(f"  {name}: {status_str}")

            if status.last_rebuild_completed_at:
                self.stdout.write(
                    f"    Last rebuild: {status.last_rebuild_completed_at} "
                    f"({status.last_rebuild_duration_seconds:.1f}s)"
                )

            if status.error_message:
                self.stdout.write(
                    self.style.ERROR(f"    Error: {status.error_message}")
                )

    def _get_projections(self, options):
        """Get projections to rebuild."""
        if options["all_projections"]:
            return projection_registry.all()

        name = options["projection"]
        projection = projection_registry.get(name)

        if not projection:
            available = ", ".join(projection_registry.names())
            raise CommandError(
                f"Unknown projection: {name}\nAvailable: {available}"
            )

        return [projection]

    def _get_companies(self, options):
        """Get companies to rebuild."""
        if options["all_tenants"]:
            return list(Company.objects.filter(is_active=True))

        slug = options["tenant"]
        try:
            return [Company.objects.get(slug=slug, is_active=True)]
        except Company.DoesNotExist:
            raise CommandError(f"Company not found or inactive: {slug}")

    def _show_plan(self, projections, companies, options):
        """Show the rebuild plan."""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("PROJECTION REBUILD PLAN")
        self.stdout.write("=" * 60)

        self.stdout.write(f"\nProjections to rebuild:")
        for p in projections:
            self.stdout.write(f"  - {p.name}")

        self.stdout.write(f"\nCompanies to process:")
        for c in companies:
            self.stdout.write(f"  - {c.name} ({c.slug})")

        # Count events
        total_events = 0
        for company in companies:
            with rls_bypass():
                count = BusinessEvent.objects.filter(company=company).count()
                total_events += count

        self.stdout.write(f"\nTotal events to process: {total_events:,}")
        self.stdout.write(f"Batch size: {options['batch_size']}")
        self.stdout.write("=" * 60)

    def _verify_integrity(self, companies):
        """Verify event integrity before rebuild."""
        from events.verification import full_integrity_check

        self.stdout.write("\nVerifying event integrity...\n")

        all_valid = True
        for company in companies:
            result = full_integrity_check(company, verbose=False)

            if result["is_valid"]:
                self.stdout.write(
                    self.style.SUCCESS(f"  {company.name}: OK")
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"  {company.name}: FAILED "
                        f"({len(result['payload_errors'])} payload errors, "
                        f"{len(result['sequence_gaps'])} gaps)"
                    )
                )
                all_valid = False

        if not all_valid:
            raise CommandError(
                "Event integrity check failed. Fix issues before rebuilding."
            )

        self.stdout.write(self.style.SUCCESS("\nIntegrity check passed.\n"))

    def _rebuild_all(self, projections, companies, options):
        """Execute rebuild for all projections and companies."""
        start_time = time.time()
        total_events = 0

        for company in companies:
            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(f"Rebuilding for: {company.name}")
            self.stdout.write("=" * 60)

            for projection in projections:
                events_processed = self._rebuild_projection(
                    projection,
                    company,
                    options
                )
                total_events += events_processed

        # Summary
        elapsed = time.time() - start_time
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("REBUILD COMPLETE"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"Total events processed: {total_events:,}")
        self.stdout.write(f"Total time: {elapsed:.2f} seconds")
        if total_events > 0:
            self.stdout.write(f"Rate: {total_events / elapsed:.0f} events/second")

    def _rebuild_projection(self, projection, company, options):
        """Rebuild a single projection for a single company."""
        name = projection.name
        batch_size = options["batch_size"]
        quiet = options["quiet"]
        force = options["force"]

        self.stdout.write(f"\n  Projection: {name}")

        # Get or create status
        with rls_bypass():
            status, _ = ProjectionStatus.objects.get_or_create(
                company=company,
                projection_name=name,
            )

        # Check if already rebuilding
        if status.is_rebuilding and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"    Already rebuilding (use --force to override)"
                )
            )
            return 0

        # Count events
        with rls_bypass():
            event_types = projection.consumes
            total_events = BusinessEvent.objects.filter(
                company=company,
                event_type__in=event_types,
            ).count() if event_types else BusinessEvent.objects.filter(
                company=company
            ).count()

        self.stdout.write(f"    Events to process: {total_events:,}")

        if total_events == 0:
            self.stdout.write("    No events to process.")
            return 0

        # Mark as rebuilding
        status.mark_rebuild_started(total_events)

        start_time = time.time()

        try:
            with rls_bypass():
                # Step 1: Clear existing projection data
                self.stdout.write("    Clearing existing data...")

                # Clear applied events tracking
                ProjectionAppliedEvent.objects.filter(
                    company=company,
                    projection_name=name,
                ).delete()

                # Clear the projection's own data
                if hasattr(projection, "_clear_projected_data"):
                    with projection_writes_allowed():
                        projection._clear_projected_data(company)

                # Reset bookmark
                from events.models import EventBookmark
                EventBookmark.objects.filter(
                    consumer_name=name,
                    company=company,
                ).delete()

                # Step 2: Replay events
                self.stdout.write("    Replaying events...")

                events = BusinessEvent.objects.filter(
                    company=company,
                    event_type__in=event_types,
                ).order_by("company_sequence") if event_types else BusinessEvent.objects.filter(
                    company=company
                ).order_by("company_sequence")

                processed = 0
                last_sequence = None

                for event in events.iterator(chunk_size=batch_size):
                    with transaction.atomic():
                        with projection_writes_allowed():
                            projection.handle(event)

                    processed += 1
                    last_sequence = event.company_sequence

                    # Update progress
                    if processed % batch_size == 0:
                        status.update_progress(processed)
                        if not quiet:
                            percent = (processed / total_events) * 100
                            self.stdout.write(
                                f"      Progress: {processed:,}/{total_events:,} ({percent:.1f}%)"
                            )

                # Mark as complete
                status.mark_rebuild_completed(last_event_sequence=last_sequence)

                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0

                self.stdout.write(
                    self.style.SUCCESS(
                        f"    Complete: {processed:,} events in {elapsed:.2f}s "
                        f"({rate:.0f} events/sec)"
                    )
                )

                return processed

        except Exception as e:
            status.mark_rebuild_error(str(e))
            self.stdout.write(
                self.style.ERROR(f"    Error: {e}")
            )
            logger.exception(f"Projection rebuild failed: {name} @ {company.slug}")
            raise
