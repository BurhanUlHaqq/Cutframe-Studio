from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0006_job_min_viral_score_job_selection_mode_and_more")]

    operations = [
        migrations.AlterField(
            model_name=model,
            name="framing",
            field=models.CharField(
                choices=[
                    ("auto", "Smart framing"), ("stream", "Screen + streamer"),
                    ("center", "Center crop"), ("fit", "Fit with blur"),
                ],
                default="auto",
                max_length=8,
            ),
        )
        for model in ("job", "workspacepreferences")
    ]
