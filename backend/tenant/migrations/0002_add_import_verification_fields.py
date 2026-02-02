# Generated migration for import verification fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenantdirectory',
            name='migration_import_hash',
            field=models.CharField(
                blank=True,
                default='',
                help_text='SHA-256 hash of imported event stream for verification.',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='tenantdirectory',
            name='migration_import_count',
            field=models.BigIntegerField(
                blank=True,
                help_text='Number of events imported during migration.',
                null=True,
            ),
        ),
    ]
