from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("studio", "0014_render_cancellation")]

    operations = [
        migrations.AddField(
            model_name="job",
            name="source_render",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="alternate_renders", to="studio.job"),
        ),
    ]
