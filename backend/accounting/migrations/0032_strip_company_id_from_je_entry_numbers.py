# Strip the company-id prefix from JournalEntry.entry_number.
#
# Pre-existing format: ``JE-{company_id}-{seq:06d}`` (e.g. ``JE-35-000024``).
# New format: ``JE-{seq:06d}`` (e.g. ``JE-000024``).
#
# Why: the company-id segment leaked Nxentra-internal tenant identity into
# every merchant-facing document. The user complained about it on
# 2026-05-04: "i dont like the JE showing the company number 35 for each
# user." JE numbers are scoped per-company by the database constraint
# anyway — the company id in the string was redundant.
#
# Sequence values are NOT reset; only the prefix changes. JE-35-000024
# becomes JE-000024 in place. The (company, entry_number) unique
# constraint stays intact because sequences were already per-company.
#
# Edge cases handled:
# - Reversal entries follow the same pattern (e.g. JE-35-000025 reversing
#   JE-35-000021) — both get rewritten in the same pass.
# - Entries with non-conforming numbers (manual one-offs, legacy data
#   from before the standard format) are left untouched.

from __future__ import annotations

import re

from django.db import migrations

# Match exactly ``JE-{integer}-{6+ digit}``. Anchored on both sides so we
# don't accidentally rewrite something like ``JE-2026-001`` (a possible
# future date-based format) or strings inside the memo field if someone
# routes this regex against the wrong column.
_JE_PATTERN = re.compile(r"^JE-\d+-(\d{6,})$")


def strip_company_id_prefix(apps, schema_editor):
    JournalEntry = apps.get_model("accounting", "JournalEntry")

    rewritten = 0
    skipped = 0
    for entry in JournalEntry.objects.exclude(entry_number="").iterator():
        match = _JE_PATTERN.match(entry.entry_number or "")
        if not match:
            skipped += 1
            continue
        new_number = f"JE-{match.group(1)}"
        if new_number == entry.entry_number:
            continue
        entry.entry_number = new_number
        entry.save(update_fields=["entry_number"])
        rewritten += 1

    if rewritten or skipped:
        print(
            f"  Stripped company-id prefix from {rewritten} JE numbers "
            f"({skipped} entries left untouched — non-matching format)."
        )


def reverse_strip(apps, schema_editor):
    """No reverse — the company id is recoverable from entry.company_id
    if anyone really needs it, but rewriting is destructive in the
    forward direction (we cannot tell from `JE-000024` alone what the
    original company id was without the FK lookup, and even then the FK
    has not changed). Treat this as a one-way cosmetic migration."""


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0031_a33_repoint_payment_processing_fees_mapping"),
    ]

    operations = [
        migrations.RunPython(strip_company_id_prefix, reverse_strip),
    ]
