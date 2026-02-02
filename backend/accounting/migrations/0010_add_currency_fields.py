from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0009_add_company_to_detail_tables"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalentry",
            name="currency",
            field=models.CharField(default="USD", help_text="Transaction currency for this entry", max_length=3),
        ),
        migrations.AddField(
            model_name="journalentry",
            name="exchange_rate",
            field=models.DecimalField(decimal_places=6, default=Decimal("1.0"), help_text="Rate to convert entry currency to company base currency", max_digits=18),
        ),
        migrations.AddField(
            model_name="journalline",
            name="amount_currency",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="journalline",
            name="currency",
            field=models.CharField(blank=True, default="", max_length=3),
        ),
        migrations.AddField(
            model_name="journalline",
            name="exchange_rate",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
    ]
