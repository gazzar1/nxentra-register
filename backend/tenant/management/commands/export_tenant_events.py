"""
Export all events for a tenant to a JSON file.

This command exports the event stream for a tenant, which can then be
imported into a dedicated database using import_tenant_events.

Usage:
    python manage.py export_tenant_events --tenant-id 123 --out events.json
    python manage.py export_tenant_events --tenant-slug acme-corp --out events.json
    python manage.py export_tenant_events --tenant-slug acme-corp --out events.json --include-payloads

Output format:
    JSON file with:
    - version: Export format version
    - exported_at: Timestamp
    - company: Company metadata
    - source_db_alias: Where events were exported from
    - event_count: Total events
    - events: Array of event objects

For migration verification, the export includes a SHA-256 hash of the
event stream that can be compared after import.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Company
from accounts.rls import rls_bypass
from tenant.context import tenant_context
from tenant.models import TenantDirectory


class TenantExportEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal, datetime, and UUID types."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


class Command(BaseCommand):
    help = "Export all events for a tenant to JSON"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--tenant-id", type=int, help="Company ID")
        group.add_argument("--tenant-slug", type=str, help="Company slug")

        parser.add_argument(
            "--out", type=str, required=True, help="Output file path"
        )
        parser.add_argument(
            "--include-payloads",
            action="store_true",
            help="Include external payloads inline (larger file, but self-contained)",
        )
        parser.add_argument(
            "--after-sequence",
            type=int,
            default=0,
            help="Only export events after this company_sequence (for incremental exports)",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print JSON output (larger file)",
        )

    def handle(self, *args, **options):
        # Import here to avoid circular imports
        from events.models import BusinessEvent

        # Resolve company
        if options["tenant_id"]:
            try:
                with rls_bypass():
                    company = Company.objects.get(id=options["tenant_id"])
            except Company.DoesNotExist:
                raise CommandError(f"Company with ID {options['tenant_id']} not found")
        else:
            try:
                with rls_bypass():
                    company = Company.objects.get(slug=options["tenant_slug"])
            except Company.DoesNotExist:
                raise CommandError(
                    f"Company with slug '{options['tenant_slug']}' not found"
                )

        # Get tenant config to determine source database
        tenant_entry = TenantDirectory.get_for_company(company.id)
        if tenant_entry:
            db_alias = tenant_entry.db_alias
            is_shared = tenant_entry.is_shared
        else:
            db_alias = "default"
            is_shared = True

        self.stdout.write(
            f"Exporting events from '{db_alias}' for company: {company.name} (ID: {company.id})"
        )

        # Set up export context
        with tenant_context(company.id, db_alias, is_shared):
            with rls_bypass():
                # Query events
                events_qs = BusinessEvent.objects.filter(
                    company=company,
                    company_sequence__gt=options["after_sequence"],
                ).order_by("company_sequence")

                total_count = events_qs.count()
                self.stdout.write(f"Found {total_count} events to export")

                if total_count == 0:
                    self.stdout.write(
                        self.style.WARNING("No events to export. Creating empty export file.")
                    )

                # Build export data
                export_data = {
                    "version": "1.0",
                    "exported_at": datetime.now().isoformat(),
                    "company": {
                        "id": company.id,
                        "public_id": str(company.public_id),
                        "slug": company.slug,
                        "name": company.name,
                    },
                    "source_db_alias": db_alias,
                    "after_sequence": options["after_sequence"],
                    "event_count": total_count,
                    "events": [],
                }

                # Hash for verification
                hasher = hashlib.sha256()

                # Cache for external payloads
                payload_cache = {}

                # Export events
                for i, event in enumerate(events_qs.iterator(chunk_size=1000)):
                    if (i + 1) % 1000 == 0:
                        self.stdout.write(f"  Processed {i + 1}/{total_count} events...")

                    event_dict = {
                        "id": str(event.id),
                        "event_type": event.event_type,
                        "aggregate_type": event.aggregate_type,
                        "aggregate_id": event.aggregate_id,
                        "sequence": event.sequence,
                        "company_sequence": event.company_sequence,
                        "idempotency_key": event.idempotency_key,
                        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                        "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
                        "payload_storage": event.payload_storage,
                        "payload_hash": event.payload_hash,
                        "origin": event.origin,
                        "metadata": event.metadata or {},
                        "schema_version": event.schema_version,
                        "caused_by_user_id": event.caused_by_user_id,
                        "caused_by_event_id": str(event.caused_by_event_id) if event.caused_by_event_id else None,
                    }

                    # Handle payload based on storage type
                    if event.payload_storage == "inline":
                        event_dict["data"] = event.data
                    elif event.payload_storage == "external":
                        if options["include_payloads"]:
                            # Include external payload inline
                            if event.payload_ref_id:
                                if event.payload_ref_id not in payload_cache:
                                    payload_cache[event.payload_ref_id] = event.payload_ref.payload
                                event_dict["data"] = payload_cache[event.payload_ref_id]
                            else:
                                event_dict["data"] = {}
                        else:
                            # Reference only
                            event_dict["payload_ref_id"] = event.payload_ref_id
                            if event.payload_ref:
                                event_dict["payload_content_hash"] = event.payload_ref.content_hash
                    elif event.payload_storage == "chunked":
                        if options["include_payloads"]:
                            # Reconstruct chunked payload
                            try:
                                event_dict["data"] = event.get_data()
                            except Exception as e:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"  Could not reconstruct chunked payload for event {event.id}: {e}"
                                    )
                                )
                                event_dict["data"] = event.data  # Header only
                        else:
                            event_dict["data"] = event.data  # Header only

                    # Update hash for verification
                    hasher.update(
                        json.dumps(event_dict, cls=TenantExportEncoder, sort_keys=True).encode()
                    )

                    export_data["events"].append(event_dict)

                # Add verification hash
                export_data["export_hash"] = hasher.hexdigest()

                # Write to file
                indent = 2 if options["pretty"] else None
                with open(options["out"], "w", encoding="utf-8") as f:
                    json.dump(export_data, f, cls=TenantExportEncoder, indent=indent)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"\nExported {len(export_data['events'])} events to {options['out']}"
                    )
                )
                self.stdout.write(f"Export hash: {export_data['export_hash']}")

                # Update TenantDirectory if migrating
                if tenant_entry and tenant_entry.status == TenantDirectory.Status.MIGRATING:
                    last_seq = export_data["events"][-1]["company_sequence"] if export_data["events"] else 0
                    tenant_entry.migration_event_sequence = last_seq
                    tenant_entry.migration_export_hash = export_data["export_hash"]
                    tenant_entry.save(
                        update_fields=["migration_event_sequence", "migration_export_hash", "updated_at"]
                    )
                    self.stdout.write("Updated TenantDirectory with export metadata")
