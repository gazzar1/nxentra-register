# Generated manually
# accounting/migrations/0017_add_is_system_protected.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0016_auto_index_rename'),
    ]

    operations = [
        # Add is_system_protected field to Account
        migrations.AddField(
            model_name='account',
            name='is_system_protected',
            field=models.BooleanField(
                default=False,
                help_text='Protected accounts: type/role/domain locked, cannot delete once has transactions',
            ),
        ),
        # Add index for efficient queries on protected accounts
        migrations.AddIndex(
            model_name='account',
            index=models.Index(
                fields=['company', 'is_system_protected'],
                name='accounting__company_sys_prot_idx',
            ),
        ),
    ]
