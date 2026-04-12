"""Add Terms of Service and Privacy Policy consent fields to User model."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_add_business_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="tos_accepted_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the user accepted the Terms of Service",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="tos_version",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Version of ToS the user accepted (e.g. '1.0')",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="privacy_accepted_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the user accepted the Privacy Policy",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="privacy_version",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Version of Privacy Policy the user accepted (e.g. '1.0')",
                max_length=20,
            ),
        ),
    ]
