from django.db import connection, migrations


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
                "OR id = NULLIF(current_setting('app.current_company_id', true), '')::integer"
            )
        elif table == "events_eventbookmark":
            predicate = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR company_id IS NULL "
                "OR company_id = NULLIF(current_setting('app.current_company_id', true), '')::integer"
            )
        else:
            predicate = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR company_id = NULLIF(current_setting('app.current_company_id', true), '')::integer"
            )

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

        statements.append(f"DROP POLICY IF EXISTS rls_tenant_isolation ON {table};")
        statements.append(
            "CREATE POLICY rls_tenant_isolation ON {table} "
            "USING ({predicate}) WITH CHECK ({predicate});".format(
                table=table,
                predicate=predicate,
            )
        )
    return "\n".join(statements)


def apply_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(_build_rls_sql())


def reverse_rls(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(_build_rls_reverse_sql())


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_update_rls_for_new_tables"),
    ]

    operations = [
        migrations.RunPython(apply_rls, reverse_rls),
    ]
