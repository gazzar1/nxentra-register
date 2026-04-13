"""Ensure a Shopify store can only be active in one company at a time."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shopify_connector", "0008_add_shopify_product_model"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="shopifystore",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="ACTIVE"),
                fields=["shop_domain"],
                name="uniq_active_shop_domain",
            ),
        ),
    ]
