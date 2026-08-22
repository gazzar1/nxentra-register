"""Enable Postgres row-level security on import_rejected_row.

ImportRejectedRow (A5-PR3a) is a per-company durable evidence table — it preserves
the raw dropped settlement/bank CSV row (financial evidence with no event). Like
every other company-scoped table it joins the same RLS tenant-isolation regime
(provider_payout / provider_payout_line / reconciliation_link) so a query run under
a tenant context without an explicit company predicate cannot read or mutate another
company's preserved rows. RLS is Postgres-only; this migration is a no-op on SQLite
(the test backend). Pattern mirrors
platform_connectors/migrations/0008_provider_payout_rls.py.
"""

from django.db import migrations

RLS_TABLES = [
    "import_rejected_row",
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
            "CREATE POLICY rls_tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate});".format(
                table=table, predicate=predicate
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


def apply_rls(apps, schema_editor):
    # Run the DDL through the connection Django is actually migrating (the
    # per-alias `migrate database=<alias>` connection), NOT the global default
    # connection — otherwise a dedicated-tenant migration records 0041 as applied
    # on the target while creating the policy on `default`, leaving the target's
    # import_rejected_row without RLS. Matches accounting/0039 / shopify/0017.
    conn = schema_editor.connection
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cursor:
        cursor.execute(_build_rls_sql())


def reverse_rls(apps, schema_editor):
    conn = schema_editor.connection
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cursor:
        cursor.execute(_build_rls_reverse_sql())


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0040_importrejectedrow_and_more"),
    ]

    operations = [
        migrations.RunPython(apply_rls, reverse_rls),
    ]
