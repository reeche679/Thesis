# Generated for per-user grades

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_student_profile_photo'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentGrade',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('course_code', models.CharField(max_length=32)),
                ('grade', models.CharField(max_length=16)),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grades', to='accounts.student')),
            ],
            options={
                'ordering': ['course_code'],
                'unique_together': {('student', 'course_code')},
            },
        ),
    ]
