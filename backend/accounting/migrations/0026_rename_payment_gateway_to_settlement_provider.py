# A2.5 — Rename PaymentGateway → SettlementProvider; add provider_type field.
# Pure rename + additive field. No data shape changes.

from django.db import migrations, models
import django.db.models.deletion


def populate_provider_types(apps, schema_editor):
    """Backfill provider_type for the seven default rows seeded by A2's bootstrap.

    Anything else (e.g. lazy-created from an unknown gateway) defaults to
    'manual' which is the model default, but we set it explicitly so audits
    don't see a NULL-then-default transition.
    """
    SettlementProvider = apps.get_model("accounting", "SettlementProvider")
    type_by_normalized_code = {
        "paymob": "gateway",
        "paypal": "gateway",
        "shopify_payments": "gateway",
        "manual": "manual",
        "cash_on_delivery": "manual",  # Transitional. A12 deactivates this row and adds bosta(courier).
        "bank_transfer": "bank_transfer",
        "unknown": "manual",
    }
    for row in SettlementProvider.objects.all():
        row.provider_type = type_by_normalized_code.get(row.normalized_code, "manual")
        row.save(update_fields=["provider_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0025_add_payment_gateway"),
    ]

    operations = [
        # 1. Rename the model. RenameModel renames the underlying table from
        #    accounting_paymentgateway -> accounting_settlementprovider and
        #    rewires foreign keys.
        migrations.RenameModel(
            old_name="PaymentGateway",
            new_name="SettlementProvider",
        ),
        # 2. Update related_names on the FKs (model state, not data).
        #    payment_gateways -> settlement_providers.
        migrations.AlterField(
            model_name="settlementprovider",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="settlement_providers",
                to="accounts.company",
            ),
        ),
        migrations.AlterField(
            model_name="settlementprovider",
            name="posting_profile",
            field=models.ForeignKey(
                help_text="Routing target. The clearing/control account used by the JE is this profile's control_account.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="settlement_providers",
                to="sales.postingprofile",
            ),
        ),
        # 3. Rename the unique constraint to match the new model name.
        migrations.RemoveConstraint(
            model_name="settlementprovider",
            name="uniq_payment_gateway_per_company_system_code",
        ),
        migrations.AddConstraint(
            model_name="settlementprovider",
            constraint=models.UniqueConstraint(
                fields=("company", "external_system", "normalized_code"),
                name="uniq_settlement_provider_per_company_system_code",
            ),
        ),
        # 4. Add provider_type field with TextChoices. Default 'manual' is
        #    safe for unknown rows; the data migration below sets sensible
        #    values for the seven bootstrap codes.
        migrations.AddField(
            model_name="settlementprovider",
            name="provider_type",
            field=models.CharField(
                choices=[
                    ("gateway", "Payment Gateway"),
                    ("courier", "Courier (COD)"),
                    ("bank_transfer", "Bank Transfer"),
                    ("manual", "Manual / Other"),
                    ("marketplace", "Marketplace"),
                ],
                default="manual",
                help_text=(
                    "Kind of settlement entity. Drives UI iconography, analytics "
                    "slicing, and (later) divergent reconciliation logic."
                ),
                max_length=20,
            ),
        ),
        # 5. Update verbose_name / verbose_name_plural / ordering on Meta.
        migrations.AlterModelOptions(
            name="settlementprovider",
            options={
                "ordering": ("external_system", "normalized_code"),
                "verbose_name": "Settlement Provider",
                "verbose_name_plural": "Settlement Providers",
            },
        ),
        # 6. Backfill provider_type for the existing seven default rows.
        migrations.RunPython(populate_provider_types, migrations.RunPython.noop),
    ]
