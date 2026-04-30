# A12 — Add default_cod_settlement_provider FK to ShopifyStore.
#
# Shopify webhooks for `cash_on_delivery` orders carry no courier identity,
# so the projection routes via this FK. NULL by default; merchants set it
# via the onboarding wizard (new) or settings page (existing). When NULL,
# a COD order lazy-creates a `pending_cod_setup` SettlementProvider with
# needs_review=True — order still posts via fallback profile but is
# operator-visible. Multi-courier-per-store routing ships in A15.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("shopify_connector", "0011_alter_shopifyorder_status"),
        ("accounting", "0027_settlementprovider_dimension_value"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopifystore",
            name="default_cod_settlement_provider",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Default COD courier for this store. Set in the onboarding wizard "
                    "or via Shopify Settings. Drives JE tagging for orders with "
                    "gateway='cash_on_delivery'. NULL → lazy-create needs_review row."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="accounting.settlementprovider",
            ),
        ),
    ]
