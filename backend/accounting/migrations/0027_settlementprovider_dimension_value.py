# A12 — Add dimension_value FK to SettlementProvider so the clearing JE
# line can be tagged with the resolved settlement_provider AnalysisDimensionValue.
#
# Schema-only migration. Population of the FK (and creation of the
# `SETTLEMENT_PROVIDER` AnalysisDimension + its values) happens in the
# bootstrap function `_bootstrap_shopify_settlement_providers` and the
# `backfill_settlement_providers` management command — both idempotent.
# This split keeps the migration cheap and the bootstrap as the canonical
# "make this state correct" function.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0026_rename_payment_gateway_to_settlement_provider"),
    ]

    operations = [
        migrations.AddField(
            model_name="settlementprovider",
            name="dimension_value",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "A12: AnalysisDimensionValue applied to the clearing JE line "
                    "when this provider routes an order. The reconciliation engine "
                    "pivots on (clearing_account, dimension_value) to surface "
                    "per-provider balances. Nullable to allow incremental population "
                    "during the A12 rollout; bootstrap and lazy-create paths fill "
                    "this FK so production rows are never missing it."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="accounting.analysisdimensionvalue",
            ),
        ),
    ]
