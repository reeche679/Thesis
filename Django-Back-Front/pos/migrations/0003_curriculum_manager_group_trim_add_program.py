# Remove add_program from Curriculum managers (only superusers add programs).

from django.db import migrations


def forwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct_program = ContentType.objects.filter(app_label="pos", model="program").first()
    if not ct_program:
        return
    p = Permission.objects.filter(content_type=ct_program, codename="add_program").first()
    if not p:
        return
    g = Group.objects.filter(name="Curriculum managers").first()
    if g:
        g.permissions.remove(p)


def backwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct_program = ContentType.objects.filter(app_label="pos", model="program").first()
    if not ct_program:
        return
    p = Permission.objects.filter(content_type=ct_program, codename="add_program").first()
    if not p:
        return
    g = Group.objects.filter(name="Curriculum managers").first()
    if g:
        g.permissions.add(p)


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0002_curriculum_manager_group"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
