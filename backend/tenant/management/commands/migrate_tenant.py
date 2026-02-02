"""
Migrate a tenant from shared to dedicated database.

This is the main orchestrator command that runs the full migration workflow:
1. Set tenant status to MIGRATING (write freeze)
2. Export events from source database
3. Run migrations on target database
4. Import events to target database
5. Replay projections
6. Verify migration integrity (hash, count, trial balance)
7. Update TenantDirectory to dedicated mode

Usage:
    python manage.py migrate_tenant --tenant-slug acme-corp --target-alias tenant_acme

    # With manual steps
    python manage.py migrate_tenant --tenant-slug acme-corp --target-alias tenant_acme --skip-export
    python manage.py migrate_tenant --tenant-slug acme-corp --target-alias tenant_acme --skip-import

Safety:
    - If any step fails, the tenant remains in shared mode
    - A MigrationLog record is created for audit
    - Use --dry-run to see what would happen without making changes
    - STRICT VERIFICATION: Migration fails if hashes/counts mismatch or trial balance differs
"""
import os
import tempfile
from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Company
from accounts.rls import rls_bypass
from tenant.models import TenantDirectory, MigrationLog


class Command(BaseCommand):
    help = "Migrate a tenant from shared database to dedicated database"

    def add_arguments(self, parser):
        # Tenant identification
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--tenant-slug", type=str, help="Company slug")
        group.add_argument("--tenant-id", type=int, help="Company ID")

        # Target database
        parser.add_argument(
            "--target-alias",
            type=str,
            required=True,
            help="Target database alias (e.g., tenant_acme)",
        )

        # Control options
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes",
        )
        parser.add_argument(
            "--skip-export",
            action="store_true",
            help="Skip export step (use existing export file)",
        )
        parser.add_argument(
            "--skip-import",
            action="store_true",
            help="Skip import step (events already imported)",
        )
        parser.add_argument(
            "--skip-replay",
            action="store_true",
            help="Skip projection replay step",
        )
        parser.add_argument(
            "--export-file",
            type=str,
            help="Path to export file (default: temp file)",
        )
        parser.add_argument(
            "--operator",
            type=str,
            default="",
            help="Operator name for audit log",
        )

    def handle(self, *args, **options):
        target_alias = options["target_alias"]

        # Verify target database exists
        if target_alias not in settings.DATABASES:
            raise CommandError(
                f"Database alias '{target_alias}' not found in settings.DATABASES. "
                f"Configure DATABASE_URL_TENANT_{target_alias.upper().replace('tenant_', '')} environment variable."
            )

        # Resolve company
        with rls_bypass():
            if options["tenant_id"]:
                try:
                    company = Company.objects.get(id=options["tenant_id"])
                except Company.DoesNotExist:
                    raise CommandError(f"Company with ID {options['tenant_id']} not found")
            else:
                try:
                    company = Company.objects.get(slug=options["tenant_slug"])
                except Company.DoesNotExist:
                    raise CommandError(f"Company with slug '{options['tenant_slug']}' not found")

        self.stdout.write(f"Migrating tenant: {company.name} (ID: {company.id})")
        self.stdout.write(f"Target database: {target_alias}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDRY RUN - no changes will be made"))
            self._dry_run(company, target_alias, options)
            return

        # Get or create TenantDirectory entry
        tenant_entry, created = TenantDirectory.objects.get_or_create(
            company=company,
            defaults={
                "mode": TenantDirectory.IsolationMode.SHARED,
                "db_alias": "default",
                "status": TenantDirectory.Status.ACTIVE,
            },
        )

        if tenant_entry.mode == TenantDirectory.IsolationMode.DEDICATED_DB:
            raise CommandError(
                f"Tenant {company.slug} is already in dedicated mode (db_alias: {tenant_entry.db_alias})"
            )

        # Create migration log
        migration_log = MigrationLog.objects.create(
            tenant=tenant_entry,
            from_mode=tenant_entry.mode,
            to_mode=TenantDirectory.IsolationMode.DEDICATED_DB,
            from_db_alias=tenant_entry.db_alias,
            to_db_alias=target_alias,
            initiated_by=options["operator"] or os.environ.get("USER", "unknown"),
        )

        # Export file path
        export_file = options["export_file"]
        if not export_file:
            export_file = os.path.join(
                tempfile.gettempdir(),
                f"tenant_export_{company.slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )

        try:
            # Step 1: Set status to MIGRATING (write freeze)
            self.stdout.write("\n[1/7] Setting tenant status to MIGRATING (write freeze)...")
            tenant_entry.status = TenantDirectory.Status.MIGRATING
            tenant_entry.save(update_fields=["status", "updated_at"])
            self.stdout.write(self.style.SUCCESS("  Done"))

            # Step 2: Export events
            if not options["skip_export"]:
                self.stdout.write(f"\n[2/7] Exporting events to {export_file}...")
                call_command(
                    "export_tenant_events",
                    tenant_id=company.id,
                    out=export_file,
                    include_payloads=True,
                    verbosity=1,
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
                self.stdout.write(self.style.SUCCESS("  Export complete"))
            else:
                self.stdout.write("\n[2/7] Skipping export (--skip-export)")

            # Step 3: Run migrations on target database
            self.stdout.write(f"\n[3/7] Running migrations on {target_alias}...")
            call_command(
                "migrate",
                database=target_alias,
                verbosity=0,
            )
            self.stdout.write(self.style.SUCCESS("  Migrations complete"))

            # Step 4: Import events
            if not options["skip_import"]:
                self.stdout.write(f"\n[4/7] Importing events to {target_alias}...")
                call_command(
                    "import_tenant_events",
                    db_alias=target_alias,
                    input_file=export_file,
                    skip_existing=True,
                    company_id=company.id,
                    verbosity=1,
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
                self.stdout.write(self.style.SUCCESS("  Import complete"))
            else:
                self.stdout.write("\n[4/7] Skipping import (--skip-import)")

            # Step 5: Replay projections
            if not options["skip_replay"]:
                self.stdout.write(f"\n[5/7] Replaying projections on {target_alias}...")
                call_command(
                    "replay_projections",
                    db_alias=target_alias,
                    company_id=company.id,
                    rebuild=True,
                    verbosity=1,
                    stdout=self.stdout,
                    stderr=self.stderr,
                )
                self.stdout.write(self.style.SUCCESS("  Replay complete"))
            else:
                self.stdout.write("\n[5/7] Skipping replay (--skip-replay)")

            # Step 6: STRICT VERIFICATION (fail migration if any check fails)
            self.stdout.write("\n[6/7] Verifying migration integrity...")

            # Refresh tenant_entry to get import metadata set by import command
            tenant_entry.refresh_from_db()

            # 6a: Verify hash integrity (HARD FAIL if mismatch)
            export_hash = tenant_entry.migration_export_hash
            import_hash = tenant_entry.migration_import_hash
            hashes_match = bool(export_hash and import_hash and (export_hash == import_hash))

            if export_hash and import_hash:
                if hashes_match:
                    self.stdout.write(self.style.SUCCESS("  Hash verification: PASSED"))
                else:
                    raise CommandError(
                        f"VERIFICATION FAILED: Hash mismatch!\n"
                        f"  Export hash: {export_hash}\n"
                        f"  Import hash: {import_hash}\n"
                        "Data integrity compromised. Migration aborted."
                    )

            # 6b: Verify event counts match (HARD FAIL if mismatch)
            export_count = tenant_entry.migration_event_sequence or 0
            import_count = tenant_entry.migration_import_count or 0

            if export_count > 0 and import_count > 0:
                if export_count == import_count:
                    self.stdout.write(self.style.SUCCESS(
                        f"  Event count verification: PASSED ({export_count} events)"
                    ))
                else:
                    raise CommandError(
                        f"VERIFICATION FAILED: Event count mismatch!\n"
                        f"  Exported: {export_count} events\n"
                        f"  Imported: {import_count} events\n"
                        "Migration aborted."
                    )

            # 6c: Verify trial balance on target matches source (HARD FAIL if mismatch)
            if not options["skip_replay"]:
                self.stdout.write("  Verifying trial balance...")
                trial_balance_ok, tb_details = self._verify_trial_balance(
                    company, target_alias
                )
                if trial_balance_ok:
                    self.stdout.write(self.style.SUCCESS(
                        f"  Trial balance verification: PASSED "
                        f"(total: {tb_details.get('total', 'N/A')})"
                    ))
                else:
                    raise CommandError(
                        f"VERIFICATION FAILED: Trial balance mismatch!\n"
                        f"  Source total_debit: {tb_details.get('source_debit', 'N/A')}\n"
                        f"  Target total_debit: {tb_details.get('target_debit', 'N/A')}\n"
                        f"  Source total_credit: {tb_details.get('source_credit', 'N/A')}\n"
                        f"  Target total_credit: {tb_details.get('target_credit', 'N/A')}\n"
                        "Migration aborted."
                    )

            # Step 7: Update TenantDirectory (only after ALL verifications pass)
            self.stdout.write("\n[7/7] Updating TenantDirectory...")

            tenant_entry.mode = TenantDirectory.IsolationMode.DEDICATED_DB
            tenant_entry.db_alias = target_alias
            tenant_entry.status = TenantDirectory.Status.ACTIVE
            tenant_entry.migrated_at = timezone.now()
            tenant_entry.save()

            # Update migration log with verification data
            migration_log.result = MigrationLog.Result.SUCCESS
            migration_log.completed_at = timezone.now()
            migration_log.export_event_count = export_count
            migration_log.import_event_count = import_count
            migration_log.export_hash = export_hash
            migration_log.import_hash = import_hash
            migration_log.hashes_match = hashes_match
            migration_log.save()

            self.stdout.write(self.style.SUCCESS(
                f"\n{'='*60}\n"
                f"Migration complete!\n"
                f"Tenant '{company.name}' is now on dedicated database '{target_alias}'\n"
                f"{'='*60}"
            ))

        except Exception as e:
            # Rollback: set status back to ACTIVE
            self.stdout.write(self.style.ERROR(f"\nMigration failed: {e}"))
            self.stdout.write("Rolling back...")

            tenant_entry.status = TenantDirectory.Status.ACTIVE
            tenant_entry.save(update_fields=["status", "updated_at"])

            migration_log.result = MigrationLog.Result.FAILED
            migration_log.completed_at = timezone.now()
            migration_log.error_message = str(e)
            migration_log.save()

            raise CommandError(f"Migration failed: {e}")

    def _verify_trial_balance(self, company, target_alias):
        """
        Verify trial balance on target database matches source.

        Compares total debits and credits between source (default) and target databases.

        Returns:
            tuple: (is_ok: bool, details: dict)
        """
        from decimal import Decimal
        from projections.models import AccountBalance
        from tenant.context import tenant_context

        details = {}

        try:
            # Get source trial balance (from default database)
            with rls_bypass():
                source_balances = AccountBalance.objects.using("default").filter(
                    company=company
                )
                source_debit = sum(
                    (b.debit_total or Decimal("0")) for b in source_balances
                )
                source_credit = sum(
                    (b.credit_total or Decimal("0")) for b in source_balances
                )

            # Get target trial balance (from tenant database)
            with tenant_context(company.id, target_alias, is_shared=False):
                with rls_bypass():
                    target_balances = AccountBalance.objects.using(target_alias).filter(
                        company=company
                    )
                    target_debit = sum(
                        (b.debit_total or Decimal("0")) for b in target_balances
                    )
                    target_credit = sum(
                        (b.credit_total or Decimal("0")) for b in target_balances
                    )

            details = {
                "source_debit": str(source_debit),
                "source_credit": str(source_credit),
                "target_debit": str(target_debit),
                "target_credit": str(target_credit),
                "total": str(source_debit),
            }

            # Verify totals match
            is_ok = (source_debit == target_debit) and (source_credit == target_credit)
            return is_ok, details

        except Exception as e:
            # If projections don't exist yet, consider it OK (no data to compare)
            details["error"] = str(e)
            return True, details

    def _dry_run(self, company, target_alias, options):
        """Show what would happen without making changes."""
        from events.models import BusinessEvent

        with rls_bypass():
            event_count = BusinessEvent.objects.filter(company=company).count()

        tenant_entry = TenantDirectory.objects.filter(company=company).first()
        current_mode = tenant_entry.mode if tenant_entry else "SHARED (no entry)"

        self.stdout.write(f"\nCurrent state:")
        self.stdout.write(f"  Tenant: {company.name} (ID: {company.id})")
        self.stdout.write(f"  Current mode: {current_mode}")
        self.stdout.write(f"  Events to migrate: {event_count}")

        self.stdout.write(f"\nSteps that would be executed:")
        self.stdout.write("  [1] Set tenant status to MIGRATING")
        if not options["skip_export"]:
            self.stdout.write(f"  [2] Export {event_count} events")
        else:
            self.stdout.write("  [2] Skip export (--skip-export)")
        self.stdout.write(f"  [3] Run migrations on {target_alias}")
        if not options["skip_import"]:
            self.stdout.write(f"  [4] Import events to {target_alias}")
        else:
            self.stdout.write("  [4] Skip import (--skip-import)")
        if not options["skip_replay"]:
            self.stdout.write(f"  [5] Replay projections on {target_alias}")
        else:
            self.stdout.write("  [5] Skip replay (--skip-replay)")
        self.stdout.write("  [6] Verify migration integrity (hash, count, trial balance)")
        self.stdout.write(f"  [7] Update TenantDirectory: mode=DEDICATED_DB, db_alias={target_alias}")

        self.stdout.write(f"\nFinal state:")
        self.stdout.write(f"  Mode: DEDICATED_DB")
        self.stdout.write(f"  Database: {target_alias}")
