from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_signatorypreference"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="current_pos_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
