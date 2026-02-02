from django.db import migrations


RLS_TABLES = [
    "accounts_companymembershippermission",
    "accounting_accountanalysisdefault",
    "accounting_journalline",
    "accounting_analysisdimensionvalue",
    "accounting_journallineanalysis",
]


def _build_rls_sql() -> str:
    statements = []
    for table in RLS_TABLES:
        predicate = (
            "current_setting('app.rls_bypass', true) = 'on' "
            "OR company_id = (NULLIF(current_setting('app.current_company_id', true), ''))::integer"
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
        ("accounts", "0009_add_company_to_membership_permission"),
        ("accounting", "0009_add_company_to_detail_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_build_rls_sql(),
            reverse_sql=_build_rls_reverse_sql(),
        ),
    ]
