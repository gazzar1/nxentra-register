# A86.7b (2026-05-26): drop the 5 shadow fields added by 0037.
#
# After A86.7a proved replay convergence between the event-driven
# projection writes and the legacy direct-mutation path, A86.7b made the
# projection the sole writer of match_status / matched_journal_line /
# match_confidence on BankStatementLine and removed the legacy paths in
# accounting/bank_reconciliation.py + bank_connector/matching.py.
#
# The event_* shadow fields are no longer read or written by any code,
# so this migration drops them.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0037_a86_3_bank_statement_line_shadow_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='bankstatementline',
            name='event_confirmed_at',
        ),
        migrations.RemoveField(
            model_name='bankstatementline',
            name='event_last_match_event_id',
        ),
        migrations.RemoveField(
            model_name='bankstatementline',
            name='event_match_confidence',
        ),
        migrations.RemoveField(
            model_name='bankstatementline',
            name='event_match_status',
        ),
        migrations.RemoveField(
            model_name='bankstatementline',
            name='event_matched_journal_line',
        ),
    ]
