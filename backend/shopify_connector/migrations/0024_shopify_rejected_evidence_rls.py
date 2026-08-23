"""Enable Postgres row-level security on shopify_rejected_evidence.

ShopifyRejectedEvidence (A5-PR2b) is a per-company durable evidence table — it
preserves the complete authenticated malformed provider payload (financial
evidence with no event, carrying customer PII). Founder decision (2026-08-23,
sub-decision 1 option b): it joins the RLS tenant-isolation regime NOW with the
canonical ENABLE+FORCE policy — not the historical no-policy posture of its
shopify_* siblings — so a query run under a tenant context without an explicit
company predicate cannot read or mutate another company's preserved payloads.
Normal evidence writes pass the policy's company predicate via the writer's own
``app.current_company_id`` establishment (rejected_evidence.py), never a broad
bypass. RLS is Postgres-only; this migration is a no-op on SQLite (the test
backend). Pattern mirrors accounting/0041_import_rejected_row_rls (DDL through
``schema_editor.connection`` — never the global default connection, so a
per-alias tenant migrate applies the policy on the database actually migrated).
"""

from django.db import migrations

RLS_TABLES = [
    "shopify_rejected_evidence",
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
    # connection — otherwise a dedicated-tenant migration records 0024 as applied
    # on the target while creating the policy on `default`, leaving the target's
    # shopify_rejected_evidence without RLS. Matches accounting/0041.
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
        ("shopify_connector", "0023_shopify_rejected_evidence"),
    ]

    operations = [
        migrations.RunPython(apply_rls, reverse_rls),
    ]
