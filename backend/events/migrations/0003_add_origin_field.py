# Generated migration for Ledger Survivability - Origin field

from django.db import migrations, models


def backfill_origin(apps, schema_editor):
    """
    Backfill origin for existing events based on heuristics:
    - Events with external_source set -> 'api'
    - EDIM batch events -> 'batch'
    - LEPH chunked journal events -> 'batch'
    - Default -> 'human'
    """
    BusinessEvent = apps.get_model('events', 'BusinessEvent')

    # Mark events with external source as API
    BusinessEvent.objects.filter(
        external_source__isnull=False,
    ).exclude(external_source='').update(origin='api')

    # Mark EDIM batch events
    BusinessEvent.objects.filter(
        event_type__startswith='edim_batch.',
    ).update(origin='batch')

    # Mark LEPH chunked journal events as batch
    BusinessEvent.objects.filter(
        event_type__in=[
            'journal.created',
            'journal.lines_chunk_added',
            'journal.finalized',
        ]
    ).update(origin='batch')


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0002_add_leph_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='businessevent',
            name='origin',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('human', 'Human (Manual UI)'),
                    ('batch', 'System Batch Import'),
                    ('api', 'External API'),
                    ('system', 'Internal System Process'),
                ],
                default='human',
                db_index=True,
                help_text='Origin of this event (human, batch import, API, or system)',
            ),
        ),
        migrations.RunPython(backfill_origin, migrations.RunPython.noop),
    ]
