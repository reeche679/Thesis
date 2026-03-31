from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_posrecord_preview_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignatoryOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("adviser", "Adviser"),
                            ("department_chair", "Department Chair"),
                            ("program_chair", "Program Chair"),
                        ],
                        max_length=32,
                    ),
                ),
                ("name", models.CharField(max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["role", "name"],
                "unique_together": {("role", "name")},
            },
        ),
    ]
