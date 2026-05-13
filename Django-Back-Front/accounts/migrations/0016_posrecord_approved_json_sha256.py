from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_phase1_curriculum"),
    ]

    operations = [
        migrations.AddField(
            model_name="posrecord",
            name="approved_json_sha256",
            field=models.CharField(
                blank=True,
                default="",
                help_text="SHA-256 (hex) of canonical JSON for approved_json at save time; FK may be null if version was removed.",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="posrecord",
            name="curriculum_version",
            field=models.ForeignKey(
                blank=True,
                help_text="Approved curriculum version tied to this saved POS run (Phase 4 audit).",
                null=True,
                on_delete=models.SET_NULL,
                related_name="pos_records",
                to="pos.curriculumversion",
            ),
        ),
    ]
