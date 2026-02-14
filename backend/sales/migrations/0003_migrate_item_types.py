# Data migration to convert old item_type values to new values
# PRODUCT -> INVENTORY
# EXPENSE -> NON_STOCK
# SERVICE -> SERVICE (no change)

from django.db import migrations


def migrate_item_types_forward(apps, schema_editor):
    Item = apps.get_model('sales', 'Item')

    # Migrate PRODUCT -> INVENTORY
    Item.objects.filter(item_type='PRODUCT').update(item_type='INVENTORY')

    # Migrate EXPENSE -> NON_STOCK
    Item.objects.filter(item_type='EXPENSE').update(item_type='NON_STOCK')


def migrate_item_types_backward(apps, schema_editor):
    Item = apps.get_model('sales', 'Item')

    # Reverse: INVENTORY -> PRODUCT
    Item.objects.filter(item_type='INVENTORY').update(item_type='PRODUCT')

    # Reverse: NON_STOCK -> EXPENSE
    Item.objects.filter(item_type='NON_STOCK').update(item_type='EXPENSE')


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0002_item_average_cost_item_cogs_account_and_more'),
    ]

    operations = [
        migrations.RunPython(
            migrate_item_types_forward,
            migrate_item_types_backward,
        ),
    ]
