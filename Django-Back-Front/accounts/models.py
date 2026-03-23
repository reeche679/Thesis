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
    year_level = models.CharField(max_length=20, blank=True)  # e.g. 1st Year; set on home with AY/semester
    academic_year = models.CharField(max_length=32, blank=True)   # e.g. A.Y. 2025 – 2026 (persists across sign-out)
    semester = models.CharField(max_length=32, blank=True)         # e.g. First Semester (persists across sign-out)

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
    """Stores a grade for one course for one student in a specific semester (section). Same course can have different grades in different semesters (e.g. failed then passed)."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='grades')
    course_code = models.CharField(max_length=32)  # e.g. ENGG 101, GEd 108
    grade = models.CharField(max_length=16)        # e.g. 1.00, 2.50, 5.00
    section_index = models.IntegerField(default=0)  # curriculum section 0-9; same course can appear in multiple sections

    class Meta:
        unique_together = [['student', 'course_code', 'section_index']]
        ordering = ['section_index', 'course_code']


class POSRecord(models.Model):
    """One record per generated POS for history; stores when and for which term the user generated it."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='pos_records')
    generated_at = models.DateTimeField(auto_now_add=True)
    academic_year = models.CharField(max_length=32, blank=True)   # e.g. A.Y. 2025 – 2026
    semester = models.CharField(max_length=32, blank=True)         # e.g. First Semester
    year_level = models.CharField(max_length=32, blank=True)       # e.g. 3rd Year (per generated POS)
    grades_snapshot = models.JSONField(default=dict)  # {course_code: grade} at time of generation for this POS
    # Full preview/solver state at PDF download (grades, both POS options, petition mode, irregular start, boundary).
    preview_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-generated_at']
