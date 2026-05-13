# Generated manually for Phase 1 — seed auth Group for curriculum managers.

from django.db import migrations


def forwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct_program = ContentType.objects.filter(app_label="pos", model="program").first()
    ct_version = ContentType.objects.filter(app_label="pos", model="curriculumversion").first()
    if not ct_program or not ct_version:
        return

    codenames = [
        "manage_curriculum_versions",
        "change_program",
        "view_program",
        "add_curriculumversion",
        "change_curriculumversion",
        "view_curriculumversion",
        "delete_curriculumversion",
    ]
    perms = list(
        Permission.objects.filter(
            content_type__in=[ct_program, ct_version],
            codename__in=codenames,
        )
    )
    if not perms:
        return

    group, _ = Group.objects.get_or_create(name="Curriculum managers")
    group.permissions.add(*perms)


def backwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct_program = ContentType.objects.filter(app_label="pos", model="program").first()
    ct_version = ContentType.objects.filter(app_label="pos", model="curriculumversion").first()
    if not ct_program or not ct_version:
        return

    codenames = [
        "manage_curriculum_versions",
        "change_program",
        "view_program",
        "add_curriculumversion",
        "change_curriculumversion",
        "view_curriculumversion",
        "delete_curriculumversion",
    ]
    perms = Permission.objects.filter(
        content_type__in=[ct_program, ct_version],
        codename__in=codenames,
    )
    group = Group.objects.filter(name="Curriculum managers").first()
    if group:
        group.permissions.remove(*list(perms))


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0001_phase1_curriculum"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
