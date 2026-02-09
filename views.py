"""
Views for POS templates and auth (register, login, session).
"""
import json
import re
import os
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from django.utils import timezone
from accounts.models import Student, StudentGrade, POSRecord

SR_CODE_REGEX = re.compile(r'^\d{2}-\d{5}$')
EMAIL_REGEX = re.compile(r'^\d{2}-\d{5}@g\.batstate-u\.edu\.ph$')


def _is_password_strong(password):
    if len(password) < 6:
        return False
    if not re.search(r'[a-z]', password):
        return False
    if not re.search(r'[A-Z]', password):
        return False
    if not re.search(r'[^A-Za-z0-9]', password):
        return False
    return True


def get_logged_in_student(request):
    """Return the Student for the current session or None."""
    sid = request.session.get('student_id')
    if not sid:
        return None
    try:
        return Student.objects.get(pk=sid)
    except Student.DoesNotExist:
        return None


def _require_student(view_func):
    """Redirect to login if no student in session."""
    def wrapper(request, *args, **kwargs):
        if get_logged_in_student(request) is None:
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return wrapper


ACADEMIC_YEAR_CHOICES = [
    ('A.Y. 2025 – 2026', 'A.Y. 2025 – 2026'),
    ('A.Y. 2026 – 2027', 'A.Y. 2026 – 2027'),
    ('A.Y. 2027 – 2028', 'A.Y. 2027 – 2028'),
    ('A.Y. 2028 – 2029', 'A.Y. 2028 – 2029'),
    ('A.Y. 2029 – 2030', 'A.Y. 2029 – 2030'),
    ('A.Y. 2030 – 2031', 'A.Y. 2030 – 2031'),
    ('A.Y. 2031 – 2032', 'A.Y. 2031 – 2032'),
    ('A.Y. 2032 – 2033', 'A.Y. 2032 – 2033'),
    ('A.Y. 2033 – 2034', 'A.Y. 2033 – 2034'),
    ('A.Y. 2034 – 2035', 'A.Y. 2034 – 2035'),
    ('A.Y. 2035 – 2036', 'A.Y. 2035 – 2036'),
]
SEMESTER_CHOICES = [
    ('First Semester', 'First Semester'),
    ('Second Semester', 'Second Semester'),
    ('Midterm', 'Midterm'),
]
YEAR_LEVEL_CHOICES = [
    ('1st Year', '1st Year'),
    ('2nd Year', '2nd Year'),
    ('3rd Year', '3rd Year'),
    ('4th Year', '4th Year'),
    ('5th Year', '5th Year'),
]


def _has_academic_term(request):
    """True if session has valid academic_year, semester, and year_level set."""
    ay = request.session.get('academic_year')
    sem = request.session.get('semester')
    yl = request.session.get('year_level')
    valid_ays = {c[0] for c in ACADEMIC_YEAR_CHOICES}
    valid_sems = {c[0] for c in SEMESTER_CHOICES}
    valid_yl = {c[0] for c in YEAR_LEVEL_CHOICES}
    return ay in valid_ays and sem in valid_sems and yl in valid_yl


def _has_profile_photo(request):
    """True if logged-in student has a profile photo uploaded."""
    student = get_logged_in_student(request)
    return bool(student and student.profile_photo)


def academic_term_context(request):
    """Context processor: expose has_academic_term and has_complete_info (term + photo) for nav."""
    has_ay = _has_academic_term(request)
    has_photo = _has_profile_photo(request)
    return {
        'has_academic_term': has_ay,
        'has_complete_info': has_ay and has_photo,
    }


def pos_main(request):
    """Main Window - Landing page with Create Account/Login buttons"""
    return render(request, "pos/main.html")


@_require_student
def pos_home(request):
    """Home page - Student info and academic term display; user must set AY and Semester before Generate/Preview."""
    student = get_logged_in_student(request)
    if request.method == 'POST' and request.FILES.get('profile_photo'):
        student.profile_photo = request.FILES['profile_photo']
        student.save()
        return redirect('pos_home')
    context = {
        'student': student,
        'academic_year_choices': ACADEMIC_YEAR_CHOICES,
        'semester_choices': SEMESTER_CHOICES,
        'year_level_choices': YEAR_LEVEL_CHOICES,
        'session_academic_year': request.session.get('academic_year'),
        'session_semester': request.session.get('semester'),
        'session_year_level': request.session.get('year_level'),
        'has_academic_term': _has_academic_term(request),
        'has_complete_info': _has_academic_term(request) and _has_profile_photo(request),
        'required_term': request.GET.get('required') == '1',
    }
    return render(request, "pos/home.html", context)


@_require_student
@require_POST
def set_academic_term(request):
    """Set session and student academic_year, semester, and year_level (persists across sign-out)."""
    ay = (request.POST.get('academic_year') or '').strip()
    sem = (request.POST.get('semester') or '').strip()
    yl = (request.POST.get('year_level') or '').strip()
    valid_ays = {c[0] for c in ACADEMIC_YEAR_CHOICES}
    valid_sems = {c[0] for c in SEMESTER_CHOICES}
    valid_yl = {c[0] for c in YEAR_LEVEL_CHOICES}
    if ay in valid_ays and sem in valid_sems and yl in valid_yl:
        request.session['academic_year'] = ay
        request.session['semester'] = sem
        request.session['year_level'] = yl
        student = get_logged_in_student(request)
        if student:
            student.academic_year = ay
            student.semester = sem
            student.year_level = yl
            student.save(update_fields=['academic_year', 'semester', 'year_level'])
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('pos_home')


@_require_student
def pos_generate(request):
    """Generate POS page - Course table with grade dropdowns; pass saved grades for this user."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    saved_grades = {g.course_code: g.grade for g in student.grades.all()}
    context = {
        'student': student,
        'saved_grades': saved_grades,
        'session_academic_year': request.session.get('academic_year', ''),
        'session_semester': request.session.get('semester', ''),
    }
    return render(request, "pos/generate_pos.html", context)


@_require_student
@require_POST
def save_pos_grades(request):
    """Save grades for the logged-in student. POST body: JSON object { "course_code": "grade", ... }
    or { "grades": { ... }, "generated": true } to also record a POS generation for history."""
    student = get_logged_in_student(request)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'ok': False, 'error': 'Expected object'}, status=400)
    if 'grades' in data:
        grades_dict = data.get('grades') or {}
        record_generated = data.get('generated') is True
    else:
        grades_dict = data
        record_generated = False
    StudentGrade.objects.filter(student=student).delete()
    for course_code, grade in grades_dict.items():
        if course_code and grade:
            StudentGrade.objects.create(student=student, course_code=str(course_code).strip(), grade=str(grade).strip())
    if record_generated:
        ay = request.session.get('academic_year') or ''
        sem = request.session.get('semester') or ''
        yl = request.session.get('year_level') or ''
        snapshot = {str(k).strip(): str(v).strip() for k, v in grades_dict.items() if k and v}
        POSRecord.objects.create(
            student=student,
            generated_at=timezone.now(),
            academic_year=ay,
            semester=sem,
            year_level=yl,
            grades_snapshot=snapshot,
        )
        _prune_old_pos_records(student)
    return JsonResponse({'ok': True})


MAX_POS_RECORDS_PER_USER = 50


def _prune_old_pos_records(student):
    """Delete oldest POSRecord for this student if over MAX_POS_RECORDS_PER_USER."""
    ids_to_keep = (
        POSRecord.objects.filter(student=student)
        .order_by('-generated_at')
        .values_list('pk', flat=True)[:MAX_POS_RECORDS_PER_USER]
    )
    POSRecord.objects.filter(student=student).exclude(pk__in=ids_to_keep).delete()


@_require_student
def pos_preview(request):
    """Preview POS page - Read-only POS table; uses current user's saved grades only."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    saved_grades = {g.course_code: g.grade for g in student.grades.all()}
    context = {
        'student': student,
        'saved_grades': saved_grades,
        'session_academic_year': request.session.get('academic_year', ''),
        'session_semester': request.session.get('semester', ''),
    }
    return render(request, "pos/preview_pos.html", context)


@_require_student
def download_pos_pdf(request):
    """Generate and download POS PDF with student info and grades (BSU BSCpE layout)."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    grades_dict = {g.course_code: g.grade for g in student.grades.all()}
    academic_year = request.session.get('academic_year', '') or student.academic_year or ''

    from pos_pdf import build_pos_pdf
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None
    pdf_bytes = build_pos_pdf(
        student_name=student.full_name or '',
        sr_code=student.sr_code or '',
        academic_year=academic_year,
        grades_dict=grades_dict,
        logo_path=logo_path,
    )
    filename = f"POS_{student.sr_code or 'POS'}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@_require_student
def download_pos_pdf_record(request, record_id):
    """Download the POS PDF for a specific history record (uses that record's saved grades snapshot)."""
    student = get_logged_in_student(request)
    record = get_object_or_404(POSRecord, pk=record_id, student=student)
    grades_dict = getattr(record, 'grades_snapshot', None) or {}
    eval_date = record.generated_at.date()
    academic_year = record.academic_year or ''

    from pos_pdf import build_pos_pdf
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None
    pdf_bytes = build_pos_pdf(
        student_name=student.full_name or '',
        sr_code=student.sr_code or '',
        academic_year=academic_year,
        grades_dict=grades_dict,
        eval_date=eval_date,
        logo_path=logo_path,
    )
    date_str = eval_date.strftime('%Y-%m-%d')
    filename = f"POS_{student.sr_code or 'POS'}_{date_str}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@_require_student
def pos_history(request):
    """POS History page - Table of previously generated POS. Requires profile photo and academic term."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    pos_records = POSRecord.objects.filter(student=student).order_by('-generated_at')
    return render(request, "pos/history.html", {'student': student, 'pos_records': pos_records})


@_require_student
@require_POST
def delete_pos_record(request, record_id):
    """Delete a POS history record; only if it belongs to the logged-in student."""
    student = get_logged_in_student(request)
    record = get_object_or_404(POSRecord, pk=record_id, student=student)
    record.delete()
    return redirect('pos_history')


def pos_about(request):
    """About page - Thesis information and developer names"""
    return render(request, "pos/about.html")


def sign_out(request):
    """Clear session and redirect to landing page."""
    request.session.flush()
    return redirect('pos_main')


def login_view(request):
    """Login page - validate SR-Code + password and set session."""
    if request.method == 'POST':
        sr_code = (request.POST.get('sr_code') or '').strip()
        password = request.POST.get('password') or ''

        if not sr_code:
            return render(request, "auth/login.html", {'error': 'Please enter your SR-Code.'})
        if not SR_CODE_REGEX.match(sr_code):
            return render(request, "auth/login.html", {'error': 'Please enter a valid SR-Code (e.g. 23-00000).'})
        if not password:
            return render(request, "auth/login.html", {'error': 'Please enter your password.'})

        try:
            student = Student.objects.get(sr_code=sr_code)
        except Student.DoesNotExist:
            return render(request, "auth/login.html", {'error': 'Invalid SR-Code or password.'})

        if not student.check_password(password):
            return render(request, "auth/login.html", {'error': 'Invalid SR-Code or password.'})

        request.session['student_id'] = student.pk
        if student.academic_year and student.semester:
            request.session['academic_year'] = student.academic_year
            request.session['semester'] = student.semester
        if student.year_level:
            request.session['year_level'] = student.year_level
        return redirect('pos_home')

    return render(request, "auth/login.html")


def register_view(request):
    """Register page - validate and create Student, then redirect to login."""
    if request.method == 'POST':
        first_name = (request.POST.get('first_name') or '').strip()
        middle_name = (request.POST.get('middle_name') or '').strip()
        last_name = (request.POST.get('last_name') or '').strip()
        sr_code = (request.POST.get('sr_code') or '').strip()
        email = (request.POST.get('email') or '').strip()
        password = request.POST.get('password') or ''
        confirm_password = request.POST.get('confirm_password') or ''

        errors = []
        if not first_name:
            errors.append('First name is required.')
        if not middle_name:
            errors.append('Middle name is required.')
        if not last_name:
            errors.append('Last name is required.')
        if not sr_code:
            errors.append('SR-Code is required.')
        elif not SR_CODE_REGEX.match(sr_code):
            errors.append('Please enter a valid SR-Code (e.g. 23-00000).')
        if not email:
            errors.append('Email is required.')
        elif not EMAIL_REGEX.match(email):
            errors.append('Please enter a valid email (e.g. 23-00000@g.batstate-u.edu.ph).')
        if not password:
            errors.append('Password is required.')
        elif not _is_password_strong(password):
            errors.append('Password must be at least 6 characters with uppercase, lowercase, and at least one symbol.')
        if password != confirm_password:
            errors.append('Both passwords must match.')

        if errors:
            return render(request, "auth/register.html", {'errors': errors})

        if Student.objects.filter(sr_code=sr_code).exists():
            return render(request, "auth/register.html", {'errors': ['An account with this SR-Code already exists.']})

        student = Student(
            sr_code=sr_code,
            first_name=first_name,
            middle_name=middle_name,
            last_name=last_name,
            email=email,
            year_level='',  # Set on home page with academic term
        )
        student.set_password(password)
        student.save()
        return redirect('login')

    return render(request, "auth/register.html")


def contact_us(request):
    """Contact Us page"""
    return render(request, "pos/contact_us.html")


def help_page(request):
    """Help page"""
    return render(request, "pos/help.html")
