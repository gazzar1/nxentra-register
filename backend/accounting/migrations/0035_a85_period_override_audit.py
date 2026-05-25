# A85 chunk 3 (2026-05-26): PeriodOverrideAudit append-only audit log.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_add_tos_consent_fields"),
        ("accounting", "0034_a84_journalentry_period_not_null"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PeriodOverrideAudit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "user_email_snapshot",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "Snapshotted at write time so the trail survives "
                            "user deletion."
                        ),
                        max_length=255,
                    ),
                ),
                (
                    "user_name_snapshot",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("SETTLEMENT_IMPORT", "Settlement CSV import"),
                            ("BANK_IMPORT", "Bank statement CSV import"),
                            ("MANUAL_JE", "Manual journal entry"),
                            ("RECON_MATCH", "Reconciliation match/unmatch"),
                            ("OTHER", "Other"),
                        ],
                        db_index=True,
                        default="OTHER",
                        max_length=30,
                    ),
                ),
                (
                    "source_document_ref",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "Free-text identifier of the override target."
                        ),
                        max_length=255,
                    ),
                ),
                (
                    "original_date",
                    models.DateField(
                        help_text=(
                            "The date that drove the would-be auto-resolved "
                            "period."
                        )
                    ),
                ),
                (
                    "original_period",
                    models.PositiveSmallIntegerField(
                        help_text=(
                            "The period the date naturally resolves to (1-13)."
                        )
                    ),
                ),
                ("original_fiscal_year", models.PositiveIntegerField()),
                (
                    "override_period",
                    models.PositiveSmallIntegerField(
                        help_text="The period the operator chose (1-13)."
                    ),
                ),
                ("override_fiscal_year", models.PositiveIntegerField()),
                (
                    "reason",
                    models.TextField(
                        help_text=(
                            "Required at the API layer. Min 10 chars enforced "
                            "upstream."
                        )
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="period_override_audits",
                        to="accounts.company",
                    ),
                ),
                (
                    "journal_entry",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="period_override_audits",
                        to="accounting.journalentry",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Period override audit",
                "verbose_name_plural": "Period override audits",
            },
        ),
        migrations.AddIndex(
            model_name="periodoverrideaudit",
            index=models.Index(
                fields=["company", "-created_at"],
                name="idx_poa_company_created",
            ),
        ),
        migrations.AddIndex(
            model_name="periodoverrideaudit",
            index=models.Index(
                fields=["company", "source", "-created_at"],
                name="idx_poa_company_source",
            ),
        ),
    ]
