# A33 — Repoint PAYMENT_PROCESSING_FEES mapping from 52000 to 53000.
#
# The Shopify auto-seed at accounts.commands._setup_shopify_accounts originally
# tried to create `52000 — Payment Processing Fees` via get_or_create, but the
# retail chart-of-accounts template at accounting/seeds.py:185 had already
# created `52000 — Shipping Expense` during onboarding. get_or_create returned
# the existing wrong-named account and the ModuleAccountMapping pointed at it.
# Result: every Shopify merchant onboarded so far has PPF mapped to
# `52000 — Shipping Expense` and sees that on their P&L when settlement fees
# post.
#
# Fix: re-point every existing mapping to `53000 — Payment Processing Fees`
# (which the retail template already creates with the correct name). For
# companies whose chart doesn't have 53000 yet (e.g. services-template
# merchants, though we don't ship Shopify on services today), the migration
# creates 53000 with the correct name.
#
# Historical JEs that already debited 52000 are NOT moved — they're preserved
# as-is and the merchant can reclassify via journal entry if reporting matters.

from django.db import migrations


def repoint_ppf_mapping(apps, schema_editor):
    Company = apps.get_model("accounts", "Company")
    Account = apps.get_model("accounting", "Account")
    ModuleAccountMapping = apps.get_model("accounting", "ModuleAccountMapping")

    repointed = 0
    created = 0
    for company in Company.objects.all():
        try:
            mapping = ModuleAccountMapping.objects.get(
                company=company,
                module="shopify_connector",
                role="PAYMENT_PROCESSING_FEES",
            )
        except ModuleAccountMapping.DoesNotExist:
            continue

        # Only repoint mappings that currently point at the wrong account.
        # Anything already on 53000 (re-onboarded after the fix shipped) or
        # on a custom account the merchant manually set is left alone.
        if mapping.account.code != "52000":
            continue

        target = Account.objects.filter(company=company, code="53000").first()
        if not target:
            target = Account.objects.create(
                company=company,
                code="53000",
                name="Payment Processing Fees",
                account_type="EXPENSE",
                role="OPERATING_EXPENSE",
                ledger_domain="FINANCIAL",
                status="ACTIVE",
                normal_balance="DEBIT",
            )
            created += 1

        mapping.account = target
        mapping.save(update_fields=["account"])
        repointed += 1

    if repointed:
        print(
            f"  A33: repointed PAYMENT_PROCESSING_FEES on {repointed} companies "
            f"({created} new 53000 accounts created)."
        )


def reverse_repoint(apps, schema_editor):
    """No-op reverse — re-pointing back to 52000 would re-introduce the bug."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0030_a17_bankstatementline_dedup_hash"),
        ("accounts", "0026_add_tos_consent_fields"),
    ]

    operations = [
        migrations.RunPython(repoint_ppf_mapping, reverse_repoint),
    ]
