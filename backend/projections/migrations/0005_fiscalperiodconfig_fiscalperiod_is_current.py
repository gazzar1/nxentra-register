from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_fix_rls_policy"),
        ("projections", "0004_fiscalperiod_fiscalperiod_uniq_fiscal_period"),
    ]

    operations = [
        migrations.AddField(
            model_name="fiscalperiod",
            name="is_current",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="FiscalPeriodConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fiscal_year", models.PositiveIntegerField()),
                ("period_count", models.PositiveSmallIntegerField(default=12)),
                ("current_period", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("open_from_period", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("open_to_period", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fiscal_period_configs",
                        to="accounts.company",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=["company", "fiscal_year"],
                        name="uniq_fiscal_period_config",
                    ),
                ],
            },
        ),
    ]
