from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_signatoryoption"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignatoryPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("adviser_name", models.CharField(blank=True, max_length=160)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "student",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="signatory_preference",
                        to="accounts.student",
                    ),
                ),
            ],
            options={
                "ordering": ["student_id"],
            },
        ),
    ]
