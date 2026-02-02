# Generated migration for LEPH (Large Event Payload Handling)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0001_initial'),
    ]

    operations = [
        # Create EventPayload model for external payload storage
        migrations.CreateModel(
            name='EventPayload',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('content_hash', models.CharField(
                    db_index=True,
                    help_text='SHA-256 hash of canonical JSON representation',
                    max_length=64,
                    unique=True
                )),
                ('payload', models.JSONField(help_text='The actual payload data')),
                ('size_bytes', models.PositiveIntegerField(
                    help_text='Size of the canonical JSON in bytes'
                )),
                ('compression', models.CharField(
                    choices=[
                        ('none', 'No compression'),
                        ('gzip', 'Gzip compression'),
                        ('zstd', 'Zstandard compression')
                    ],
                    default='none',
                    help_text='Compression algorithm used (for future use)',
                    max_length=20
                )),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'verbose_name': 'Event Payload',
                'verbose_name_plural': 'Event Payloads',
                'db_table': 'events_payload',
            },
        ),

        # Add LEPH fields to BusinessEvent
        migrations.AddField(
            model_name='businessevent',
            name='payload_storage',
            field=models.CharField(
                choices=[
                    ('inline', 'Inline'),
                    ('external', 'External'),
                    ('chunked', 'Chunked')
                ],
                default='inline',
                help_text="Storage strategy: inline (data field), external (EventPayload), or chunked (multi-event)",
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='businessevent',
            name='payload_hash',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                help_text='SHA-256 hash of canonical JSON payload for integrity verification',
                max_length=64
            ),
        ),
        migrations.AddField(
            model_name='businessevent',
            name='payload_ref',
            field=models.ForeignKey(
                blank=True,
                help_text="Reference to external payload (when payload_storage='external')",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='events',
                to='events.eventpayload'
            ),
        ),
    ]
