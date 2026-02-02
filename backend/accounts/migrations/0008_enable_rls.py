from django.db import migrations


RLS_TABLES = [
    "accounts_company",
    "accounts_companymembership",
    "accounting_account",
    "accounting_journalentry",
    "accounting_analysisdimension",
    "accounting_companysequence",
    "events_businessevent",
    "events_companyeventcounter",
    "events_eventbookmark",
    "projections_accountbalance",
    "projections_fiscalperiod",
    "projections_periodaccountbalance",
    "projections_projectionappliedevent",
]


def _build_rls_sql() -> str:
    statements = []
    for table in RLS_TABLES:
        if table == "accounts_company":
            predicate = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR id = current_setting('app.current_company_id', true)::integer"
            )
        elif table == "events_eventbookmark":
            predicate = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR company_id IS NULL "
                "OR company_id = current_setting('app.current_company_id', true)::integer"
            )
        else:
            predicate = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR company_id = current_setting('app.current_company_id', true)::integer"
            )

        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        statements.append(f"DROP POLICY IF EXISTS rls_tenant_isolation ON {table};")
        statements.append(
            "CREATE POLICY rls_tenant_isolation ON {table} "
            "USING ({predicate}) WITH CHECK ({predicate});".format(
                table=table,
                predicate=predicate,
            )
        )
    return "\n".join(statements)


def _build_rls_reverse_sql() -> str:
    statements = []
    for table in RLS_TABLES:
        statements.append(f"DROP POLICY IF EXISTS rls_tenant_isolation ON {table};")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(statements)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_add_public_id_fields"),
        ("accounting", "0007_companysequence"),
        ("events", "0001_initial"),
        ("projections", "0004_fiscalperiod_fiscalperiod_uniq_fiscal_period"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_build_rls_sql(),
            reverse_sql=_build_rls_reverse_sql(),
        ),
    ]
