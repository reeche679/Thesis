from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class Student(models.Model):
    """Student account for POS system. SR-Code is the unique login identifier."""
    sr_code = models.CharField(max_length=20, unique=True)  # e.g. 23-00000
    password = models.CharField(max_length=128)  # hashed
    profile_photo = models.FileField(upload_to='student_photos/', blank=True, null=True)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    year_level = models.CharField(max_length=20)  # e.g. 1st Year, 2nd Year

    class Meta:
        ordering = ['last_name', 'first_name']

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    @property
    def surname(self):
        return self.last_name

    @property
    def first_names(self):
        """First name + middle initial (e.g. Joriel Laurence S.)"""
        mi = (self.middle_name.strip() or '')[:1]
        if mi:
            return f"{self.first_name} {mi}."
        return self.first_name

    @property
    def full_name(self):
        """e.g. Joriel Laurence S. Singh"""
        return f"{self.first_names} {self.last_name}".strip()

    def __str__(self):
        return f"{self.sr_code} – {self.full_name}"


class StudentGrade(models.Model):
    """Stores a grade for one course for one student. Per-user, persists across logins."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='grades')
    course_code = models.CharField(max_length=32)  # e.g. ENGG 101, GEd 108
    grade = models.CharField(max_length=16)        # e.g. 1.00, 2.50, 5.00

    class Meta:
        unique_together = [['student', 'course_code']]
        ordering = ['course_code']
