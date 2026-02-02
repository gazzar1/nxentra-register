"""
Data migration to seed TenantDirectory for all existing companies.

This ensures every Company has a corresponding TenantDirectory entry,
which is required for the database routing to work correctly.

All companies without an entry are created with:
- mode: SHARED (use RLS on default database)
- db_alias: "default"
- status: ACTIVE

This migration is idempotent and can be run multiple times safely.
"""
from django.db import migrations


def seed_tenant_directory(apps, schema_editor):
    """Create TenantDirectory entries for all companies that don't have one."""
    Company = apps.get_model("accounts", "Company")
    TenantDirectory = apps.get_model("tenant", "TenantDirectory")

    # Get companies that already have entries
    existing_company_ids = set(
        TenantDirectory.objects.values_list("company_id", flat=True)
    )

    # Create entries for companies without one
    companies_to_create = Company.objects.exclude(id__in=existing_company_ids)

    entries_to_create = [
        TenantDirectory(
            company_id=company.id,
            mode="SHARED",
            db_alias="default",
            status="ACTIVE",
        )
        for company in companies_to_create
    ]

    if entries_to_create:
        TenantDirectory.objects.bulk_create(entries_to_create)
        print(f"\n  Created {len(entries_to_create)} TenantDirectory entries")
    else:
        print("\n  All companies already have TenantDirectory entries")


def reverse_seed(apps, schema_editor):
    """
    Reverse migration - delete only SHARED entries created by this migration.

    Note: This only deletes entries that are in SHARED mode with default db_alias,
    preserving any that have been migrated to dedicated databases.
    """
    TenantDirectory = apps.get_model("tenant", "TenantDirectory")

    # Only delete shared entries (don't touch dedicated ones)
    deleted, _ = TenantDirectory.objects.filter(
        mode="SHARED",
        db_alias="default",
    ).delete()

    if deleted:
        print(f"\n  Deleted {deleted} TenantDirectory entries")


class Migration(migrations.Migration):

    dependencies = [
        ("tenant", "0002_add_import_verification_fields"),
        ("accounts", "0001_initial"),  # Ensure Company table exists
    ]

    operations = [
        migrations.RunPython(
            seed_tenant_directory,
            reverse_code=reverse_seed,
        ),
    ]
