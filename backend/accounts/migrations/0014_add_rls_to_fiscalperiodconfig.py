"""
Add RLS policy to tables that were missed by earlier migrations.

These tables were created AFTER the original RLS migration (0008_enable_rls.py)
and were missed. Without RLS, a forgotten queryset filter could leak data.
"""
from django.db import migrations


RLS_TABLES = [
    # Projection tables
    "projections_fiscalperiodconfig",
    "projections_fiscalperiod",  # Has policy but RLS not enabled - fix it
    # EDIM tables (data import/mapping)
    "edim_sourcesystem",
    "edim_mappingprofile",
    "edim_ingestionbatch",
    "edim_stagedrecord",
    "edim_identitycrosswalk",
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
        ("accounts", "0013_add_email_verification_approval"),
        ("projections", "0005_fiscalperiodconfig_fiscalperiod_is_current"),
        ("edim", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=_build_rls_sql(),
            reverse_sql=_build_rls_reverse_sql(),
        ),
    ]
