from django.db import migrations, models
class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0006_add_public_id_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanySequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("next_value", models.BigIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="sequences", to="accounts.company")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["company", "name"], name="accounting_company_1c1d3d_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="companysequence",
            constraint=models.UniqueConstraint(fields=("company", "name"), name="uniq_company_sequence_name"),
        ),
    ]
