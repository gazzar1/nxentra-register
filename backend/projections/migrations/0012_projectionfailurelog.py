# A80 (2026-05-25): operator-visible projection failures
# See projections/models.py ProjectionFailureLog for context.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_add_tos_consent_fields"),
        ("events", "0005_add_external_api_key"),
        ("projections", "0011_dimension_balance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectionFailureLog",
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
                ("projection_name", models.CharField(max_length=100)),
                (
                    "event_type",
                    models.CharField(
                        help_text="Denormalized from event for fast filtering",
                        max_length=100,
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("MISSING_CONFIG", "Missing configuration"),
                            ("INVALID_DATA", "Invalid event data"),
                            ("DOWNSTREAM_FAILED", "Downstream command failed"),
                            ("UNEXPECTED", "Unexpected error"),
                        ],
                        default="UNEXPECTED",
                        max_length=30,
                    ),
                ),
                (
                    "message",
                    models.TextField(
                        help_text="Exception message captured at handler raise time",
                    ),
                ),
                (
                    "fix_hint",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Optional operator-facing hint from ProjectionStateError.fix_hint",
                    ),
                ),
                (
                    "occurrence_count",
                    models.PositiveIntegerField(
                        default=1,
                        help_text="Incremented when the same event fails again before resolution",
                    ),
                ),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("resolved", models.BooleanField(db_index=True, default=False)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "resolution_note",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Operator note when manually marking resolved",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projection_failures",
                        to="accounts.company",
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projection_failures",
                        to="events.businessevent",
                    ),
                ),
                (
                    "resolved_by",
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
                "verbose_name": "Projection failure",
                "verbose_name_plural": "Projection failures",
            },
        ),
        migrations.AddConstraint(
            model_name="projectionfailurelog",
            constraint=models.UniqueConstraint(
                fields=("company", "projection_name", "event"),
                name="uniq_projection_failure_per_event",
            ),
        ),
        migrations.AddIndex(
            model_name="projectionfailurelog",
            index=models.Index(
                fields=["company", "resolved", "-last_seen_at"],
                name="idx_pfl_company_unresolved",
            ),
        ),
        migrations.AddIndex(
            model_name="projectionfailurelog",
            index=models.Index(
                fields=["company", "projection_name", "resolved"],
                name="idx_pfl_company_proj_resolved",
            ),
        ),
    ]
