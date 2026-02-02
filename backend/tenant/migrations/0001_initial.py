"""
Initial migration for tenant app.

Creates:
- tenant_directory: Maps companies to their database configuration
- tenant_migration_log: Audit log for tenant migrations
"""
import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantDirectory",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("SHARED", "Shared Database (RLS)"),
                            ("DEDICATED_DB", "Dedicated Database"),
                        ],
                        default="SHARED",
                        help_text="Isolation mode: SHARED uses RLS, DEDICATED_DB uses separate database.",
                        max_length=20,
                    ),
                ),
                (
                    "db_alias",
                    models.CharField(
                        default="default",
                        help_text="Database alias. Maps to DATABASE_URL_TENANT_{alias} env var for dedicated DBs.",
                        max_length=100,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ACTIVE", "Active"),
                            ("MIGRATING", "Migrating (Write Freeze)"),
                            ("READ_ONLY", "Read Only"),
                            ("SUSPENDED", "Suspended"),
                        ],
                        default="ACTIVE",
                        help_text="Current status. MIGRATING enables write freeze.",
                        max_length=20,
                    ),
                ),
                (
                    "migrated_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When migration to dedicated DB completed.",
                        null=True,
                    ),
                ),
                (
                    "migration_event_sequence",
                    models.BigIntegerField(
                        blank=True,
                        help_text="Last company_sequence exported during migration.",
                        null=True,
                    ),
                ),
                (
                    "migration_export_hash",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="SHA-256 hash of exported event stream for verification.",
                        max_length=64,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "notes",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Operator notes about this tenant configuration.",
                    ),
                ),
                (
                    "company",
                    models.OneToOneField(
                        help_text="The company this tenant configuration applies to.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tenant_config",
                        to="accounts.company",
                    ),
                ),
            ],
            options={
                "verbose_name": "Tenant Directory Entry",
                "verbose_name_plural": "Tenant Directory",
                "db_table": "tenant_directory",
            },
        ),
        migrations.CreateModel(
            name="MigrationLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "from_mode",
                    models.CharField(
                        choices=[
                            ("SHARED", "Shared Database (RLS)"),
                            ("DEDICATED_DB", "Dedicated Database"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "to_mode",
                    models.CharField(
                        choices=[
                            ("SHARED", "Shared Database (RLS)"),
                            ("DEDICATED_DB", "Dedicated Database"),
                        ],
                        max_length=20,
                    ),
                ),
                ("from_db_alias", models.CharField(max_length=100)),
                ("to_db_alias", models.CharField(max_length=100)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("export_event_count", models.BigIntegerField(default=0)),
                ("import_event_count", models.BigIntegerField(default=0)),
                (
                    "export_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "import_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("hashes_match", models.BooleanField(default=False)),
                (
                    "result",
                    models.CharField(
                        choices=[
                            ("SUCCESS", "Success"),
                            ("FAILED", "Failed"),
                            ("ROLLED_BACK", "Rolled Back"),
                            ("IN_PROGRESS", "In Progress"),
                        ],
                        default="IN_PROGRESS",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "initiated_by",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Username or system identifier that initiated migration.",
                        max_length=255,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="migration_logs",
                        to="tenant.tenantdirectory",
                    ),
                ),
            ],
            options={
                "verbose_name": "Migration Log",
                "verbose_name_plural": "Migration Logs",
                "db_table": "tenant_migration_log",
                "ordering": ["-started_at"],
            },
        ),
        migrations.AddIndex(
            model_name="tenantdirectory",
            index=models.Index(
                fields=["db_alias"], name="tenant_dir_db_alias_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="tenantdirectory",
            index=models.Index(fields=["status"], name="tenant_dir_status_idx"),
        ),
        migrations.AddIndex(
            model_name="tenantdirectory",
            index=models.Index(fields=["mode"], name="tenant_dir_mode_idx"),
        ),
    ]
