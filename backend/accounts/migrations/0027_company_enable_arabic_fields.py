"""A138: add Company.enable_arabic_fields (optional Arabic data-entry field visibility).

ALL companies — new and existing — default to False (English-first). Existing
Arabic data (name_ar, etc.) is never touched: it stays in the DB and reappears
if a company re-enables Arabic fields from Settings. (No data backfill: the
AddField default of False applies to existing rows too.)
"""

from django.db import migrations, models


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
    ]
