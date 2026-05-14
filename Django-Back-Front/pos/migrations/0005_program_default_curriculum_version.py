from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0004_curriculumversion_source_pdf_sha256"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="default_curriculum_version",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, solver/student flows may use this approved version if none is selected.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="programs_as_default_curriculum",
                to="pos.curriculumversion",
            ),
        ),
    ]
