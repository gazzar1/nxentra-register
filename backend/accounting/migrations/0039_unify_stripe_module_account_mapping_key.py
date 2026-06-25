# ADR-0002 module-key unify (S1 PR-A): move any ModuleAccountMapping rows that
# were written under the stale "stripe_connector" key onto the canonical
# "platform_stripe" key the JE projections actually read. Idempotent + safe to
# re-run; a no-op on a DB that never seeded the stale key.

from django.db import migrations

OLD_KEY = "stripe_connector"
NEW_KEY = "platform_stripe"


def unify_key(apps, schema_editor):
    from accounts.rls import rls_bypass

    ModuleAccountMapping = apps.get_model("accounting", "ModuleAccountMapping")
    # Historical model from apps.get_model has no custom save() write-barrier.
    with rls_bypass(conn=schema_editor.connection):
        for row in ModuleAccountMapping.objects.filter(module=OLD_KEY):
            canonical = ModuleAccountMapping.objects.filter(
                company_id=row.company_id, module=NEW_KEY, role=row.role
            ).first()
            if canonical is not None:
                # A canonical row already owns this (company, role). But if it's
                # only an unmapped placeholder (account is null) while the stale
                # row carries the merchant's configured account, copy the account
                # over before dropping the stale duplicate — otherwise the rename
                # would leave the role unmapped (Codex review P2).
                if canonical.account_id is None and row.account_id is not None:
                    canonical.account_id = row.account_id
                    canonical.save(update_fields=["account"])
                row.delete()
            else:
                row.module = NEW_KEY
                row.save(update_fields=["module"])


def noop_reverse(apps, schema_editor):
    # One-way consolidation: NEW_KEY rows may have always been platform_stripe,
    # so we cannot safely split them back out. Leaving them under the canonical
    # key is the correct rolled-back state.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0038_a86_7b_drop_bank_statement_line_shadow_fields"),
    ]

    operations = [
        migrations.RunPython(unify_key, noop_reverse),
    ]
