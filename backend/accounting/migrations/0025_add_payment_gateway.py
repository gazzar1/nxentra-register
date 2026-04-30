# Generated for A2 — PaymentGateway routing primitive.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0024_add_fx_rounding_account_role'),
        ('accounts', '0026_add_tos_consent_fields'),
        ('sales', '0009_add_source_tracking_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentGateway',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_system', models.CharField(help_text="Connector source, e.g. 'shopify', 'woocommerce', 'stripe_direct'.", max_length=50)),
                ('source_code', models.CharField(help_text='Raw gateway code from the connector payload (preserved for audit).', max_length=100)),
                ('normalized_code', models.CharField(db_index=True, help_text='Lookup key derived by normalize_gateway_code().', max_length=100)),
                ('display_name', models.CharField(help_text='Human-readable label shown in UI / reports.', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('needs_review', models.BooleanField(default=False, help_text='True for lazy-created rows from unknown gateway codes. Operator must confirm or re-route before reconciliation.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payment_gateways', to='accounts.company')),
                ('posting_profile', models.ForeignKey(help_text='Routing target. The clearing/control account used by the JE is this profile\'s control_account.', on_delete=django.db.models.deletion.PROTECT, related_name='payment_gateways', to='sales.postingprofile')),
            ],
            options={
                'verbose_name': 'Payment Gateway',
                'verbose_name_plural': 'Payment Gateways',
                'ordering': ('external_system', 'normalized_code'),
            },
        ),
        migrations.AddConstraint(
            model_name='paymentgateway',
            constraint=models.UniqueConstraint(fields=('company', 'external_system', 'normalized_code'), name='uniq_payment_gateway_per_company_system_code'),
        ),
        migrations.AddIndex(
            model_name='paymentgateway',
            index=models.Index(fields=['company', 'external_system', 'is_active'], name='accounting__company_8b7ee8_idx'),
        ),
        migrations.AddIndex(
            model_name='paymentgateway',
            index=models.Index(fields=['company', 'needs_review'], name='accounting__company_204d43_idx'),
        ),
    ]
