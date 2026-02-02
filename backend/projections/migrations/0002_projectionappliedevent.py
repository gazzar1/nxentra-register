from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projections", "0001_initial"),
        ("events", "0001_initial"),
        ("accounts", "0007_add_public_id_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectionAppliedEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("projection_name", models.CharField(max_length=100)),
                ("applied_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="applied_projection_events", to="accounts.company")),
                ("event", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="+", to="events.businessevent")),
            ],
            options={
                "indexes": [models.Index(fields=["company", "projection_name"], name="projections_proj_company_1c913f_idx")],
                "constraints": [models.UniqueConstraint(fields=["company", "projection_name", "event"], name="uniq_projection_event")],
            },
        ),
    ]
