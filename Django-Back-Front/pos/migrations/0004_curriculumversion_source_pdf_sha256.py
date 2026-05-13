from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0003_curriculum_manager_group_trim_add_program"),
    ]

    operations = [
        migrations.AddField(
            model_name="curriculumversion",
            name="source_pdf_sha256",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA-256 (hex) of uploaded source PDF bytes for dedup/audit.",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="curriculumversion",
            name="source_pdf",
            field=models.FileField(
                blank=True,
                help_text="Source curriculum PDF (Phase 5: upload pipeline).",
                null=True,
                upload_to="curriculum_pdfs/%Y/%m/",
            ),
        ),
    ]
