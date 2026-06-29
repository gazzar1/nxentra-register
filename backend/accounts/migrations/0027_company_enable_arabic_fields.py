"""A138: add Company.enable_arabic_fields (optional Arabic data-entry field visibility).

New companies default to False (English-first). Pre-existing companies are
backfilled to True so their current "Arabic fields always shown" behavior is
preserved — no Arabic data is deleted, only the UI visibility preference is set.
"""

from django.db import migrations, models


def enable_for_existing_companies(apps, schema_editor):
    """Preserve current behavior for companies that existed before this flag:
    they were always shown Arabic fields, so set the flag True for them."""
    Company = apps.get_model("accounts", "Company")
    # Bulk SQL UPDATE via the historical model — bypasses the read-model save
    # guard and emits no events (this is a backfill, not a settings change).
    Company.objects.all().update(enable_arabic_fields=True)


def noop_reverse(apps, schema_editor):
    """Reverse is a no-op: dropping the column (handled by AddField reverse)
    is sufficient; we don't flip values back."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_add_tos_consent_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="enable_arabic_fields",
            field=models.BooleanField(
                default=False,
                help_text="Show optional Arabic data-entry fields on forms. Does not delete Arabic data when off.",
            ),
        ),
        migrations.RunPython(enable_for_existing_companies, noop_reverse),
    ]
