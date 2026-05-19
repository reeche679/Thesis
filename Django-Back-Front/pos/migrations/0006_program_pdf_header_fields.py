from django.db import migrations, models

import pos.pdf_header_fields as pdf_header_fields


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0005_program_default_curriculum_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="pdf_department_label",
            field=models.CharField(
                default=pdf_header_fields.DEFAULT_PDF_DEPARTMENT_LABEL,
                help_text="Left-aligned line under the BSU letterhead on every PDF page.",
                max_length=300,
            ),
        ),
        migrations.AddField(
            model_name="program",
            name="pdf_program_curriculum",
            field=models.CharField(
                default=pdf_header_fields.DEFAULT_PDF_PROGRAM_CURRICULUM,
                help_text="Bold curriculum title on the PDF (below “CURRICULUM”).",
                max_length=300,
            ),
        ),
        migrations.AddField(
            model_name="program",
            name="pdf_program_shortcut",
            field=models.CharField(
                default=pdf_header_fields.DEFAULT_PDF_PROGRAM_SHORTCUT,
                help_text="Short program label above “PROGRAM OF STUDY EVALUATION” on the PDF.",
                max_length=120,
            ),
        ),
    ]
