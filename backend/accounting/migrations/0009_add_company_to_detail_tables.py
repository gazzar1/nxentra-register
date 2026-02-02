from django.db import migrations, models
import django.db.models.deletion


def backfill_company(apps, schema_editor):
    JournalLine = apps.get_model("accounting", "JournalLine")
    AnalysisDimensionValue = apps.get_model("accounting", "AnalysisDimensionValue")
    JournalLineAnalysis = apps.get_model("accounting", "JournalLineAnalysis")
    AccountAnalysisDefault = apps.get_model("accounting", "AccountAnalysisDefault")

    for line in JournalLine.objects.select_related("entry").all().iterator():
        if line.company_id is None and line.entry_id:
            line.company_id = line.entry.company_id
            line.save(update_fields=["company"])

    for value in AnalysisDimensionValue.objects.select_related("dimension").all().iterator():
        if value.company_id is None and value.dimension_id:
            value.company_id = value.dimension.company_id
            value.save(update_fields=["company"])

    for tag in JournalLineAnalysis.objects.select_related(
        "journal_line", "dimension", "dimension_value"
    ).all().iterator():
        if tag.company_id is None and tag.journal_line_id:
            tag.company_id = tag.journal_line.company_id
            tag.save(update_fields=["company"])

    for default in AccountAnalysisDefault.objects.select_related("account").all().iterator():
        if default.company_id is None and default.account_id:
            default.company_id = default.account.company_id
            default.save(update_fields=["company"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0008_rename_accounting_company_1c1d3d_idx_accounting__company_e1319e_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalline",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="journal_lines",
                to="accounts.company",
            ),
        ),
        migrations.AddField(
            model_name="analysisdimensionvalue",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="analysis_dimension_values",
                to="accounts.company",
            ),
        ),
        migrations.AddField(
            model_name="journallineanalysis",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="journal_line_analysis",
                to="accounts.company",
            ),
        ),
        migrations.AddField(
            model_name="accountanalysisdefault",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="account_analysis_defaults",
                to="accounts.company",
            ),
        ),
        migrations.RunPython(backfill_company, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="journalline",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="journal_lines",
                to="accounts.company",
            ),
        ),
        migrations.AlterField(
            model_name="analysisdimensionvalue",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="analysis_dimension_values",
                to="accounts.company",
            ),
        ),
        migrations.AlterField(
            model_name="journallineanalysis",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="journal_line_analysis",
                to="accounts.company",
            ),
        ),
        migrations.AlterField(
            model_name="accountanalysisdefault",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="account_analysis_defaults",
                to="accounts.company",
            ),
        ),
        migrations.AddIndex(
            model_name="journalline",
            index=models.Index(fields=["company", "entry"], name="accounting__company_8ff07d_idx"),
        ),
        migrations.AddIndex(
            model_name="analysisdimensionvalue",
            index=models.Index(fields=["company", "dimension"], name="accounting__company_e66864_idx"),
        ),
        migrations.AddIndex(
            model_name="journallineanalysis",
            index=models.Index(fields=["company", "journal_line"], name="accounting__company_23fecc_idx"),
        ),
        migrations.AddIndex(
            model_name="accountanalysisdefault",
            index=models.Index(fields=["company", "account"], name="accounting__company_cf9d53_idx"),
        ),
    ]
