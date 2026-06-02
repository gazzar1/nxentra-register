# A122 (2026-06-02): rotating offline tokens.
# Adds refresh_token / token_expires_at / refresh_token_expires_at to
# ShopifyStore. Existing rows get empty refresh_token + NULL expiry; their
# permanent legacy tokens continue to work until 2027-01-01 (or sooner for
# specific endpoints Shopify has already cut over), at which point the
# merchant must re-OAuth to receive a fresh rotating token pair.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shopify_connector", "0014_drop_webhooks_registered"),
    ]

    operations = [
        migrations.AddField(
            model_name="shopifystore",
            name="refresh_token",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Shopify refresh token (shprt_*). Used to obtain a new "
                    "access token before the current one expires. Empty for "
                    "legacy non-expiring tokens issued before A122."
                ),
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="shopifystore",
            name="token_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When the current access_token expires. NULL for legacy "
                    "non-expiring tokens; future timestamp for A122 rotating "
                    "tokens."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="shopifystore",
            name="refresh_token_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When the refresh_token itself expires (typically 90 days "
                    "from issue). After this point, the merchant must "
                    "re-authorize."
                ),
                null=True,
            ),
        ),
    ]
