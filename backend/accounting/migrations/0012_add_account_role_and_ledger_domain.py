# Generated manually for account model refactor

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add role, ledger_domain, and derived behavior fields to Account model.

    This is Phase 1 of the 5-type + role + ledger domain refactor.
    Data migration (0013) will populate the new fields based on existing
    account_type values.
    """

    dependencies = [
        ('accounting', '0011_alter_journalentry_created_by_and_more'),
    ]

    operations = [
        # Add role field
        migrations.AddField(
            model_name='account',
            name='role',
            field=models.CharField(
                blank=True,
                choices=[
                    # Asset roles
                    ('ASSET_GENERAL', 'General Asset'),
                    ('LIQUIDITY', 'Cash/Bank'),
                    ('RECEIVABLE_CONTROL', 'Accounts Receivable Control'),
                    ('INVENTORY_VALUE', 'Inventory Value'),
                    ('PREPAID', 'Prepaid Expense'),
                    ('FIXED_ASSET_COST', 'Fixed Asset Cost'),
                    ('ACCUM_DEPRECIATION', 'Accumulated Depreciation'),
                    ('OTHER_ASSET', 'Other Asset'),
                    # Liability roles
                    ('LIABILITY_GENERAL', 'General Liability'),
                    ('PAYABLE_CONTROL', 'Accounts Payable Control'),
                    ('ACCRUED_EXPENSE', 'Accrued Expense'),
                    ('DEFERRED_REVENUE', 'Deferred Revenue'),
                    ('TAX_PAYABLE', 'Tax Payable'),
                    ('LOAN', 'Loan/Borrowing'),
                    ('OTHER_LIABILITY', 'Other Liability'),
                    # Equity roles
                    ('CAPITAL', 'Capital'),
                    ('RETAINED_EARNINGS', 'Retained Earnings'),
                    ('CURRENT_YEAR_EARNINGS', 'Current Year Earnings'),
                    ('DRAWINGS', 'Drawings/Distributions'),
                    ('RESERVE', 'Reserve'),
                    ('OTHER_EQUITY', 'Other Equity'),
                    # Revenue roles
                    ('SALES', 'Sales Revenue'),
                    ('SERVICE', 'Service Revenue'),
                    ('OTHER_INCOME', 'Other Income'),
                    ('FINANCIAL_INCOME', 'Financial Income'),
                    ('CONTRA_REVENUE', 'Contra Revenue'),
                    # Expense roles
                    ('COGS', 'Cost of Goods Sold'),
                    ('OPERATING_EXPENSE', 'Operating Expense'),
                    ('ADMIN_EXPENSE', 'Administrative Expense'),
                    ('FINANCIAL_EXPENSE', 'Financial Expense'),
                    ('DEPRECIATION_EXPENSE', 'Depreciation Expense'),
                    ('TAX_EXPENSE', 'Tax Expense'),
                    ('OTHER_EXPENSE', 'Other Expense'),
                    # Statistical/Off-balance roles
                    ('STAT_GENERAL', 'Statistical General'),
                    ('STAT_INVENTORY_QTY', 'Inventory Quantity'),
                    ('STAT_PRODUCTION_QTY', 'Production Quantity'),
                    ('OBS_GENERAL', 'Off-Balance General'),
                    ('OBS_CONTINGENT', 'Contingent Liability'),
                ],
                default='',
                help_text='Behavioral role that determines derived properties',
                max_length=30,
            ),
        ),

        # Add ledger_domain field
        migrations.AddField(
            model_name='account',
            name='ledger_domain',
            field=models.CharField(
                choices=[
                    ('FINANCIAL', 'Financial'),
                    ('STATISTICAL', 'Statistical'),
                    ('OFF_BALANCE', 'Off-Balance Sheet'),
                ],
                default='FINANCIAL',
                help_text='Financial, Statistical, or Off-Balance ledger',
                max_length=15,
            ),
        ),

        # Add requires_counterparty field (derived)
        migrations.AddField(
            model_name='account',
            name='requires_counterparty',
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text='True for AR/AP control accounts (derived from role)',
            ),
        ),

        # Add counterparty_kind field (derived)
        migrations.AddField(
            model_name='account',
            name='counterparty_kind',
            field=models.CharField(
                blank=True,
                default='',
                editable=False,
                help_text='CUSTOMER or VENDOR for control accounts (derived from role)',
                max_length=10,
            ),
        ),

        # Add allow_manual_posting field
        migrations.AddField(
            model_name='account',
            name='allow_manual_posting',
            field=models.BooleanField(
                default=True,
                help_text='False for control accounts (system-only posting by default)',
            ),
        ),

        # Add indexes for new fields
        migrations.AddIndex(
            model_name='account',
            index=models.Index(fields=['company', 'role'], name='accounting__company_role_idx'),
        ),
        migrations.AddIndex(
            model_name='account',
            index=models.Index(fields=['company', 'ledger_domain'], name='accounting__company_ledger_idx'),
        ),
        migrations.AddIndex(
            model_name='account',
            index=models.Index(fields=['company', 'requires_counterparty'], name='accounting__company_cp_idx'),
        ),
    ]
