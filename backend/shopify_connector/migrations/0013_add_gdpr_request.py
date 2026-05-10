# A44 — Add GdprRequest audit table for Shopify GDPR mandatory compliance webhooks.
#
# Shopify requires every app to handle customers/data_request, customers/redact,
# and shop/redact and respond 200 within ~5s. We log every request to this audit
# table for our own records; the actual data work runs asynchronously.
#
# Idempotency via (topic, payload_signature) — Shopify retries on non-200 and we
# must not double-count.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shopify_connector", "0012_shopifystore_default_cod_settlement_provider"),
    ]

    operations = [
        migrations.CreateModel(
            name="GdprRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "topic",
                    models.CharField(
                        choices=[
                            ("customers/data_request", "Customer Data Request"),
                            ("customers/redact", "Customer Redact"),
                            ("shop/redact", "Shop Redact"),
                        ],
                        max_length=40,
                    ),
                ),
                ("shop_domain", models.CharField(db_index=True, max_length=255)),
                ("shop_id", models.BigIntegerField(blank=True, null=True)),
                ("customer_id", models.BigIntegerField(blank=True, null=True)),
                ("customer_email", models.EmailField(blank=True, max_length=254)),
                ("payload", models.JSONField(default=dict)),
                ("payload_signature", models.CharField(db_index=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("COMPLETED", "Completed"),
                            ("FAILED", "Failed"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("processing_notes", models.TextField(blank=True)),
            ],
            options={
                "db_table": "shopify_gdpr_request",
                "ordering": ["-received_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="gdprrequest",
            constraint=models.UniqueConstraint(
                fields=("topic", "payload_signature"),
                name="uniq_gdpr_request_idempotent",
            ),
        ),
    ]
