"""Enable Postgres row-level security on provider_payout.

ProviderPayout is a per-company financial read-model (the canonical payout header),
so it joins the same RLS tenant-isolation regime as its peers (provider_payout_line /
provider_raw_object / reconciliation_link). RLS is Postgres-only; this migration is a
no-op on SQLite (the test backend). Pattern mirrors
platform_connectors/migrations/0005_provider_payout_line_rls.py.
"""

from django.db import connection, migrations

RLS_TABLES = [
    "provider_payout",
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
            "USING ({predicate}) WITH CHECK ({predicate});".format(table=table, predicate=predicate)
        )
    return "\n".join(statements)


def _build_rls_reverse_sql() -> str:
    statements = []
    for table in RLS_TABLES:
        statements.append(f"DROP POLICY IF EXISTS rls_tenant_isolation ON {table};")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
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
        ("platform_connectors", "0007_providerpayout_providerpayout_uniq_provider_payout"),
    ]

    operations = [
        migrations.RunPython(apply_rls, reverse_rls),
    ]
