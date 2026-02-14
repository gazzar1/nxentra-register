# Generated manually for AR/AP subledger models

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    """
    Add Customer and Vendor models for AR/AP subledgers.
    Add counterparty fields to JournalLine.

    This is Phase 2 of the account model refactor - implementing
    counterparty tracking for control accounts.
    """

    dependencies = [
        ('accounts', '0012_add_company_logo'),
        ('accounting', '0013_migrate_account_types_to_roles'),
    ]

    operations = [
        # Create Customer model
        migrations.CreateModel(
            name='Customer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('code', models.CharField(help_text='Customer code (e.g., CUST001)', max_length=20)),
                ('name', models.CharField(max_length=255)),
                ('name_ar', models.CharField(blank=True, default='', max_length=255)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=50)),
                ('address', models.TextField(blank=True, default='')),
                ('address_ar', models.TextField(blank=True, default='')),
                ('credit_limit', models.DecimalField(blank=True, decimal_places=2, help_text='Maximum credit allowed (null = unlimited)', max_digits=18, null=True)),
                ('payment_terms_days', models.PositiveIntegerField(default=30, help_text='Default payment terms in days')),
                ('currency', models.CharField(default='USD', help_text='Preferred transaction currency', max_length=3)),
                ('tax_id', models.CharField(blank=True, default='', help_text='Tax identification number (VAT, TIN, etc.)', max_length=50)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive'), ('BLOCKED', 'Blocked')], default='ACTIVE', max_length=20)),
                ('notes', models.TextField(blank=True, default='')),
                ('notes_ar', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='customers', to='accounts.company')),
                ('default_ar_account', models.ForeignKey(blank=True, help_text='Default AR control account. Must have role=RECEIVABLE_CONTROL', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='customers', to='accounting.account')),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        # Add unique constraint for Customer
        migrations.AddConstraint(
            model_name='customer',
            constraint=models.UniqueConstraint(fields=['company', 'code'], name='uniq_customer_code_per_company'),
        ),
        # Add indexes for Customer
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['company', 'status'], name='accounting__cust_status_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['company', 'name'], name='accounting__cust_name_idx'),
        ),

        # Create Vendor model
        migrations.CreateModel(
            name='Vendor',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('code', models.CharField(help_text='Vendor code (e.g., VEND001)', max_length=20)),
                ('name', models.CharField(max_length=255)),
                ('name_ar', models.CharField(blank=True, default='', max_length=255)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=50)),
                ('address', models.TextField(blank=True, default='')),
                ('address_ar', models.TextField(blank=True, default='')),
                ('payment_terms_days', models.PositiveIntegerField(default=30, help_text='Default payment terms in days')),
                ('currency', models.CharField(default='USD', help_text='Preferred transaction currency', max_length=3)),
                ('tax_id', models.CharField(blank=True, default='', help_text='Tax identification number (VAT, TIN, etc.)', max_length=50)),
                ('bank_name', models.CharField(blank=True, default='', max_length=255)),
                ('bank_account', models.CharField(blank=True, default='', max_length=100)),
                ('bank_iban', models.CharField(blank=True, default='', max_length=50)),
                ('bank_swift', models.CharField(blank=True, default='', max_length=20)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive'), ('BLOCKED', 'Blocked')], default='ACTIVE', max_length=20)),
                ('notes', models.TextField(blank=True, default='')),
                ('notes_ar', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vendors', to='accounts.company')),
                ('default_ap_account', models.ForeignKey(blank=True, help_text='Default AP control account. Must have role=PAYABLE_CONTROL', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='vendors', to='accounting.account')),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        # Add unique constraint for Vendor
        migrations.AddConstraint(
            model_name='vendor',
            constraint=models.UniqueConstraint(fields=['company', 'code'], name='uniq_vendor_code_per_company'),
        ),
        # Add indexes for Vendor
        migrations.AddIndex(
            model_name='vendor',
            index=models.Index(fields=['company', 'status'], name='accounting__vend_status_idx'),
        ),
        migrations.AddIndex(
            model_name='vendor',
            index=models.Index(fields=['company', 'name'], name='accounting__vend_name_idx'),
        ),

        # Add counterparty fields to JournalLine
        migrations.AddField(
            model_name='journalline',
            name='customer',
            field=models.ForeignKey(blank=True, help_text='Required when posting to AR control accounts', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='journal_lines', to='accounting.customer'),
        ),
        migrations.AddField(
            model_name='journalline',
            name='vendor',
            field=models.ForeignKey(blank=True, help_text='Required when posting to AP control accounts', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='journal_lines', to='accounting.vendor'),
        ),

        # Add constraint: cannot have both customer and vendor
        migrations.AddConstraint(
            model_name='journalline',
            constraint=models.CheckConstraint(
                check=~(models.Q(customer__isnull=False) & models.Q(vendor__isnull=False)),
                name='chk_line_not_both_counterparty',
            ),
        ),

        # Add indexes for counterparty queries
        migrations.AddIndex(
            model_name='journalline',
            index=models.Index(fields=['company', 'customer'], name='accounting__line_cust_idx'),
        ),
        migrations.AddIndex(
            model_name='journalline',
            index=models.Index(fields=['company', 'vendor'], name='accounting__line_vend_idx'),
        ),
    ]
