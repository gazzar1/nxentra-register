# Generated migration for user-level voice permissions
# Adds voice settings to CompanyMembership for per-user voice access control

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0018_company_allow_negative_inventory'),
    ]

    operations = [
        migrations.AddField(
            model_name='companymembership',
            name='voice_enabled',
            field=models.BooleanField(
                default=False,
                help_text='User has permission to use voice input (granted by admin)',
            ),
        ),
        migrations.AddField(
            model_name='companymembership',
            name='voice_quota',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="User's voice row quota (null = no quota set, must be set to use voice)",
            ),
        ),
        migrations.AddField(
            model_name='companymembership',
            name='voice_rows_used',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Voice rows used by this user',
            ),
        ),
        migrations.AddField(
            model_name='companymembership',
            name='voice_quota_reset_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the user's quota was last reset/refilled",
            ),
        ),
    ]
