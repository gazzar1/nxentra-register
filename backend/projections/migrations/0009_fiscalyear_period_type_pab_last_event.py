# Generated manually for fiscal year management + period type + PAB last_event

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0019_add_membership_voice_fields'),
        ('events', '0004_alter_businessevent_caused_by_user'),
        ('projections', '0008_add_customer_vendor_balance'),
    ]

    operations = [
        # 1. Create FiscalYear model
        migrations.CreateModel(
            name='FiscalYear',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fiscal_year', models.PositiveIntegerField()),
                ('status', models.CharField(choices=[('OPEN', 'Open'), ('CLOSED', 'Closed')], default='OPEN', max_length=10)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('close_reason', models.TextField(blank=True, default='')),
                ('retained_earnings_entry_public_id', models.CharField(blank=True, default='', max_length=36)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('closed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='fiscal_years', to='accounts.company')),
            ],
            options={
                'verbose_name': 'Fiscal Year',
                'verbose_name_plural': 'Fiscal Years',
            },
        ),
        migrations.AddConstraint(
            model_name='fiscalyear',
            constraint=models.UniqueConstraint(fields=('company', 'fiscal_year'), name='uniq_fiscal_year'),
        ),
        migrations.AddIndex(
            model_name='fiscalyear',
            index=models.Index(fields=['company', 'fiscal_year'], name='projections_company_fy_idx'),
        ),
        migrations.AddIndex(
            model_name='fiscalyear',
            index=models.Index(fields=['company', 'status'], name='projections_fy_status_idx'),
        ),

        # 2. Add period_type to FiscalPeriod
        migrations.AddField(
            model_name='fiscalperiod',
            name='period_type',
            field=models.CharField(choices=[('NORMAL', 'Normal'), ('ADJUSTMENT', 'Adjustment')], default='NORMAL', max_length=12),
        ),

        # 3. Add last_event FK to PeriodAccountBalance
        migrations.AddField(
            model_name='periodaccountbalance',
            name='last_event',
            field=models.ForeignKey(blank=True, help_text='Last event that updated this balance', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='events.businessevent'),
        ),

        # 4. Add additional index to PeriodAccountBalance
        migrations.AddIndex(
            model_name='periodaccountbalance',
            index=models.Index(fields=['company', 'account', 'fiscal_year'], name='projections_company_acct_fy_idx'),
        ),

        # 5. Change FiscalPeriodConfig.period_count default to 13
        migrations.AlterField(
            model_name='fiscalperiodconfig',
            name='period_count',
            field=models.PositiveSmallIntegerField(default=13, help_text='Total periods including adjustment period (always 13 for standard ERP)'),
        ),
    ]
