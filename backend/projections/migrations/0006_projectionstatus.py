# Generated manually for ProjectionStatus model

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0012_add_company_logo"),
        ("projections", "0005_fiscalperiodconfig_fiscalperiod_is_current"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectionStatus",
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
                    "projection_name",
                    models.CharField(
                        help_text="Name of the projection (e.g., 'account_balance')",
                        max_length=100,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("READY", "Ready"),
                            ("REBUILDING", "Rebuilding"),
                            ("ERROR", "Error"),
                            ("PAUSED", "Paused"),
                        ],
                        default="READY",
                        max_length=20,
                    ),
                ),
                (
                    "events_total",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Total events to process during rebuild",
                    ),
                ),
                (
                    "events_processed",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Events processed so far during rebuild",
                    ),
                ),
                (
                    "last_rebuild_started_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the last rebuild started",
                        null=True,
                    ),
                ),
                (
                    "last_rebuild_completed_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the last rebuild completed",
                        null=True,
                    ),
                ),
                (
                    "last_rebuild_duration_seconds",
                    models.FloatField(
                        blank=True,
                        help_text="Duration of last rebuild in seconds",
                        null=True,
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Last error message if status is ERROR",
                    ),
                ),
                (
                    "error_count",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Number of errors during current/last rebuild",
                    ),
                ),
                (
                    "last_event_sequence",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Last processed event sequence (for lag calculation)",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projection_statuses",
                        to="accounts.company",
                    ),
                ),
                (
                    "rebuild_requested_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who requested the rebuild",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Projection Status",
                "verbose_name_plural": "Projection Statuses",
            },
        ),
        migrations.AddConstraint(
            model_name="projectionstatus",
            constraint=models.UniqueConstraint(
                fields=("company", "projection_name"),
                name="uniq_projection_status",
            ),
        ),
        migrations.AddIndex(
            model_name="projectionstatus",
            index=models.Index(
                fields=["company", "status"],
                name="projections_company_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="projectionstatus",
            index=models.Index(
                fields=["projection_name", "status"],
                name="projections_name_status_idx",
            ),
        ),
    ]
