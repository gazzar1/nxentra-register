"""
Import events into a tenant database.

This command imports events from a JSON file (created by export_tenant_events)
into a target database.

Usage:
    python manage.py import_tenant_events --db-alias tenant_acme --in events.json
    python manage.py import_tenant_events --db-alias tenant_acme --in events.json --skip-existing
    python manage.py import_tenant_events --db-alias tenant_acme --in events.json --dry-run

Features:
- Idempotent: --skip-existing prevents duplicate imports
- Preserves event IDs and sequences for replay consistency
- Validates export version and company matching
- Computes import hash for verification against export hash
"""
import hashlib
import json
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from accounts.models import Company
from accounts.rls import rls_bypass
from tenant.context import tenant_context


class Command(BaseCommand):
    help = "Import events into a tenant database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--db-alias",
            type=str,
            required=True,
            help="Target database alias (e.g., tenant_acme)",
        )
        parser.add_argument(
            "--in",
            dest="input_file",
            type=str,
            required=True,
            help="Input JSON file (from export_tenant_events)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate without importing (no database changes)",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip events that already exist (idempotency check by idempotency_key)",
        )
        parser.add_argument(
            "--company-id",
            type=int,
            help="Override company ID in target database (if different from export)",
        )

    def handle(self, *args, **options):
        from events.models import BusinessEvent, EventPayload, CompanyEventCounter
        from uuid import UUID

        db_alias = options["db_alias"]

        # Verify database exists
        if db_alias not in settings.DATABASES:
            raise CommandError(
                f"Database alias '{db_alias}' not found in settings.DATABASES. "
                f"Available: {list(settings.DATABASES.keys())}"
            )

        # Load export file
        self.stdout.write(f"Loading export file: {options['input_file']}")
        try:
            with open(options["input_file"], "r", encoding="utf-8") as f:
                export_data = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"File not found: {options['input_file']}")
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid JSON file: {e}")

        # Validate export format
        if export_data.get("version") != "1.0":
            raise CommandError(
                f"Unsupported export version: {export_data.get('version')}. Expected: 1.0"
            )

        company_data = export_data.get("company", {})
        events = export_data.get("events", [])
        export_hash = export_data.get("export_hash", "")

        self.stdout.write(f"Export contains {len(events)} events")
        self.stdout.write(f"Source company: {company_data.get('name')} (ID: {company_data.get('id')})")
        self.stdout.write(f"Export hash: {export_hash}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        # Resolve company in target database
        target_company_id = options["company_id"] or company_data.get("id")

        with rls_bypass():
            try:
                company = Company.objects.get(id=target_company_id)
            except Company.DoesNotExist:
                raise CommandError(
                    f"Company with ID {target_company_id} not found. "
                    "The company must exist in the system database before importing events."
                )

        self.stdout.write(f"Target database: {db_alias}")
        self.stdout.write(f"Target company: {company.name} (ID: {company.id})")

        # Set up import context
        with tenant_context(company.id, db_alias, is_shared=False):
            imported = 0
            skipped = 0
            errors = []
            hasher = hashlib.sha256()

            # Use atomic transaction
            try:
                with transaction.atomic(using=db_alias):
                    # Get or create event counter for the company
                    if not options["dry_run"]:
                        counter, created = CompanyEventCounter.objects.using(db_alias).get_or_create(
                            company_id=company.id,
                            defaults={"last_sequence": 0},
                        )
                        max_sequence = counter.last_sequence
                    else:
                        max_sequence = 0

                    # Process each event
                    for i, event_data in enumerate(events):
                        if (i + 1) % 1000 == 0:
                            self.stdout.write(f"  Processing {i + 1}/{len(events)}...")

                        try:
                            # Update hash for verification
                            hasher.update(
                                json.dumps(event_data, sort_keys=True).encode()
                            )

                            if options["dry_run"]:
                                imported += 1
                                continue

                            # Check for existing event (idempotency)
                            if options["skip_existing"]:
                                exists = BusinessEvent.objects.using(db_alias).filter(
                                    company_id=company.id,
                                    idempotency_key=event_data.get("idempotency_key"),
                                ).exists()
                                if exists:
                                    skipped += 1
                                    continue

                            # Handle external payloads
                            payload_ref = None
                            if event_data.get("payload_storage") == "external":
                                if "data" in event_data and event_data["data"]:
                                    # Store payload and get reference
                                    payload_ref = EventPayload.store_payload(
                                        event_data["data"],
                                        using=db_alias,
                                    )

                            # Parse dates
                            occurred_at = None
                            if event_data.get("occurred_at"):
                                occurred_at = datetime.fromisoformat(event_data["occurred_at"])

                            recorded_at = None
                            if event_data.get("recorded_at"):
                                recorded_at = datetime.fromisoformat(event_data["recorded_at"])

                            # Create event - preserve original ID and sequences
                            event = BusinessEvent(
                                id=UUID(event_data["id"]),
                                company_id=company.id,
                                event_type=event_data["event_type"],
                                aggregate_type=event_data["aggregate_type"],
                                aggregate_id=event_data["aggregate_id"],
                                sequence=event_data["sequence"],
                                company_sequence=event_data["company_sequence"],
                                idempotency_key=event_data.get("idempotency_key", ""),
                                data=event_data.get("data", {}),
                                metadata=event_data.get("metadata", {}),
                                occurred_at=occurred_at,
                                payload_storage=event_data.get("payload_storage", "inline"),
                                payload_hash=event_data.get("payload_hash", ""),
                                payload_ref=payload_ref,
                                origin=event_data.get("origin", "human"),
                                schema_version=event_data.get("schema_version", 1),
                                caused_by_user_id=event_data.get("caused_by_user_id"),
                            )

                            # Handle caused_by_event (may not exist yet in target)
                            if event_data.get("caused_by_event_id"):
                                event.caused_by_event_id = UUID(event_data["caused_by_event_id"])

                            # Direct insert to preserve IDs
                            event.save(using=db_alias, force_insert=True)

                            # Track max sequence
                            if event_data["company_sequence"] > max_sequence:
                                max_sequence = event_data["company_sequence"]

                            imported += 1

                        except Exception as e:
                            errors.append({
                                "event_id": event_data.get("id"),
                                "event_type": event_data.get("event_type"),
                                "error": str(e),
                            })
                            if len(errors) >= 10:
                                self.stdout.write(
                                    self.style.ERROR("Too many errors, stopping import")
                                )
                                raise

                    # Update event counter
                    if not options["dry_run"] and imported > 0:
                        counter.last_sequence = max_sequence
                        counter.save(using=db_alias, update_fields=["last_sequence"])

                    # If there were errors, roll back
                    if errors:
                        raise Exception(f"Import had {len(errors)} errors")

            except Exception as e:
                if errors:
                    self.stdout.write(self.style.ERROR(f"\nImport failed with errors:"))
                    for err in errors:
                        self.stdout.write(
                            f"  Event {err['event_id']} ({err['event_type']}): {err['error']}"
                        )
                raise CommandError(f"Import failed: {e}")

            # Compute import hash
            import_hash = hasher.hexdigest()

            # Report results
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Import completed successfully"))
            self.stdout.write(f"  Imported: {imported}")
            self.stdout.write(f"  Skipped: {skipped}")
            self.stdout.write(f"  Errors: {len(errors)}")
            self.stdout.write(f"  Import hash: {import_hash}")

            # Verify hash
            if export_hash:
                if import_hash == export_hash:
                    self.stdout.write(
                        self.style.SUCCESS("  Hash verification: PASSED (export matches import)")
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Hash verification: MISMATCH\n"
                            f"    Export: {export_hash}\n"
                            f"    Import: {import_hash}"
                        )
                    )

            # Update TenantDirectory if migrating (for orchestrator verification)
            if not options["dry_run"]:
                from tenant.models import TenantDirectory

                tenant_entry = TenantDirectory.objects.filter(
                    company_id=company.id,
                    status=TenantDirectory.Status.MIGRATING,
                ).first()

                if tenant_entry:
                    tenant_entry.migration_import_hash = import_hash
                    tenant_entry.migration_import_count = imported
                    tenant_entry.save(
                        update_fields=["migration_import_hash", "migration_import_count", "updated_at"]
                    )
                    self.stdout.write("Updated TenantDirectory with import metadata")
