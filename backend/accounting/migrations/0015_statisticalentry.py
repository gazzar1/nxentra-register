# Generated manually for statistical ledger support

from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    """
    Add StatisticalEntry model for quantity tracking.

    This is Phase 4 of the account model refactor - implementing
    statistical ledger entries that track quantities separately from
    financial accounting.

    Key design decisions:
    - Uses direction enum (INCREASE/DECREASE) not signed quantities
    - Only works with STATISTICAL or OFF_BALANCE ledger domain accounts
    - Never affects trial balance or debit/credit validation
    """

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounting', '0014_customer_vendor_counterparty'),
    ]

    operations = [
        migrations.CreateModel(
            name='StatisticalEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('date', models.DateField()),
                ('memo', models.CharField(blank=True, default='', max_length=255)),
                ('memo_ar', models.CharField(blank=True, default='', max_length=255)),
                ('quantity', models.DecimalField(decimal_places=4, help_text='Positive quantity (direction indicates increase/decrease)', max_digits=18)),
                ('direction', models.CharField(choices=[('INCREASE', 'Increase'), ('DECREASE', 'Decrease')], help_text='INCREASE or DECREASE', max_length=10)),
                ('unit', models.CharField(help_text="Unit of measure: 'units', 'kg', 'L', 'hours', 'sqm', etc.", max_length=20)),
                ('status', models.CharField(choices=[('DRAFT', 'Draft'), ('POSTED', 'Posted'), ('REVERSED', 'Reversed')], default='DRAFT', max_length=12)),
                ('source_module', models.CharField(blank=True, default='', help_text="Module that created this entry (e.g., 'inventory', 'production')", max_length=50)),
                ('source_document', models.CharField(blank=True, default='', help_text='Reference to source document', max_length=100)),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statistical_entries', to='accounts.company')),
                ('account', models.ForeignKey(help_text='Must be a statistical or off-balance account', on_delete=django.db.models.deletion.PROTECT, related_name='statistical_entries', to='accounting.account')),
                ('related_journal_entry', models.ForeignKey(blank=True, help_text='Related financial journal entry (optional)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='statistical_entries', to='accounting.journalentry')),
                ('reverses_entry', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reversal_entry', to='accounting.statisticalentry')),
                ('posted_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='posted_statistical_entries', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_statistical_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Statistical Entry',
                'verbose_name_plural': 'Statistical Entries',
                'ordering': ['-date', '-created_at'],
            },
        ),
        # Add constraint: quantity must be positive
        migrations.AddConstraint(
            model_name='statisticalentry',
            constraint=models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name='chk_stat_quantity_positive',
            ),
        ),
        # Add indexes
        migrations.AddIndex(
            model_name='statisticalentry',
            index=models.Index(fields=['company', 'account', 'date'], name='accounting__stat_acct_date_idx'),
        ),
        migrations.AddIndex(
            model_name='statisticalentry',
            index=models.Index(fields=['company', 'status'], name='accounting__stat_status_idx'),
        ),
        migrations.AddIndex(
            model_name='statisticalentry',
            index=models.Index(fields=['company', 'related_journal_entry'], name='accounting__stat_je_idx'),
        ),
    ]
