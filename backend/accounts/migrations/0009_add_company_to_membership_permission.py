from django.db import migrations, models
import django.db.models.deletion


def backfill_company(apps, schema_editor):
    CompanyMembershipPermission = apps.get_model("accounts", "CompanyMembershipPermission")

    for grant in CompanyMembershipPermission.objects.select_related("membership").all().iterator():
        if grant.company_id is None and grant.membership_id:
            grant.company_id = grant.membership.company_id
            grant.save(update_fields=["company"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_enable_rls"),
    ]

    operations = [
        migrations.AddField(
            model_name="companymembershippermission",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="membership_permission_grants",
                to="accounts.company",
            ),
        ),
        migrations.RunPython(backfill_company, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="companymembershippermission",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="membership_permission_grants",
                to="accounts.company",
            ),
        ),
        migrations.AddIndex(
            model_name="companymembershippermission",
            index=models.Index(fields=["company", "membership"], name="accounts_co_company_f9495f_idx"),
        ),
    ]
