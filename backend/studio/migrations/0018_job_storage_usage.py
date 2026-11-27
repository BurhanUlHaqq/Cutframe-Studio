from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("studio", "0017_job_source_range"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="storage_bytes",
            field=models.PositiveBigIntegerField(default=0, db_default=0),
        ),
        migrations.AddField(
            model_name="job",
            name="work_bytes",
            field=models.PositiveBigIntegerField(default=0, db_default=0),
        ),
        migrations.AddField(
            model_name="job",
            name="storage_measured_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
