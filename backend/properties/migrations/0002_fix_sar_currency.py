# Generated migration to fix hardcoded SAR currency on existing records.
# Updates all property records where currency=SAR to use the company's default_currency.

from django.db import migrations


def fix_sar_currency(apps, schema_editor):
    """Update SAR currency to company default for all property module records."""
    Company = apps.get_model("accounts", "Company")
    Lease = apps.get_model("properties", "Lease")
    PaymentReceipt = apps.get_model("properties", "PaymentReceipt")
    SecurityDepositTransaction = apps.get_model("properties", "SecurityDepositTransaction")
    PropertyExpense = apps.get_model("properties", "PropertyExpense")

    for company in Company.objects.all():
        if company.default_currency and company.default_currency != "SAR":
            currency = company.default_currency
            Lease.objects.filter(company=company, currency="SAR").update(currency=currency)
            PaymentReceipt.objects.filter(company=company, currency="SAR").update(currency=currency)
            SecurityDepositTransaction.objects.filter(company=company, currency="SAR").update(currency=currency)
            PropertyExpense.objects.filter(company=company, currency="SAR").update(currency=currency)


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(fix_sar_currency, migrations.RunPython.noop),
    ]
