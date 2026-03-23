from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_add_section_index_to_studentgrade"),
    ]

    operations = [
        migrations.AddField(
            model_name="posrecord",
            name="preview_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
