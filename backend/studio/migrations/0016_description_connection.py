from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0015_job_source_render")]

    operations = [
        migrations.AddField(
            model_name="workspaceaisettings",
            name="description_connection",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
