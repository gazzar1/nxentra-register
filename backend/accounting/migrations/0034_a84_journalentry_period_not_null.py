# A84 (2026-05-25): JournalEntry.period must be NOT NULL.
#
# Defense-in-depth for posting-period enforcement. Pre-A84 the field was
# nullable, allowing a JE to exist with period=NULL — which combined with
# the silent `(True, "")` early-return in can_post_to_period() let such an
# entry post without period validation.
#
# This migration:
#   1. Backfills NULL periods from each entry's date (month component)
#   2. Adds NOT NULL constraint at the database level
#
# Per docs/finance_event_first_policy.md §1 (event log is source of truth)
# and §8 (loud failures), defense-in-depth means the schema, the validator,
# AND the command layer should all refuse a broken state — not just one.

from django.db import migrations, models


def backfill_periods_from_date(apps, schema_editor):
    """For any JournalEntry with period=NULL but a valid date, set the
    period to the date's month (1-12). This matches the auto-resolution
    fallback in create_journal_entry() so we don't invent values that
    diverge from what the command layer would have set.

    If a row has period=NULL AND no date... that's already impossible
    (date is NOT NULL in the model), but if production has corrupted rows
    we leave them — the schema migration that follows will raise and the
    operator can investigate manually.
    """
    JournalEntry = apps.get_model("accounting", "JournalEntry")
    null_period_qs = JournalEntry.objects.filter(period__isnull=True)
    count = null_period_qs.count()
    if count == 0:
        return
    # Use iterator + bulk update to be safe on large rowsets.
    updated = 0
    for entry in null_period_qs.iterator(chunk_size=500):
        if entry.date:
            entry.period = entry.date.month
            entry.save(update_fields=["period"])
            updated += 1
    print(f"  A84 backfill: filled period on {updated}/{count} JournalEntry rows")


def noop_reverse(apps, schema_editor):
    """Reversing the backfill would mean re-setting period to NULL, which
    is destructive and pointless (the AlterField below would also need to
    be reversed first). Keep it a no-op."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0033_customer_vendor_default_posting_profile"),
    ]

    operations = [
        # Step 1: backfill any NULL periods from the date.
        migrations.RunPython(
            backfill_periods_from_date,
            reverse_code=noop_reverse,
        ),
        # Step 2: enforce NOT NULL at the schema level. Will raise if any
        # rows still have period=NULL after the backfill — operator must
        # investigate (likely a corrupted row that needs manual review).
        migrations.AlterField(
            model_name="journalentry",
            name="period",
            field=models.PositiveSmallIntegerField(
                help_text="Fiscal period (1-12 or custom)",
            ),
        ),
    ]
