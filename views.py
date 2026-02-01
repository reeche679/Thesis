"""
Views for POS templates and auth (register, login, session).
"""
import json
import re
from django.shortcuts import render, redirect
from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.models import Student, StudentGrade

# Validation (match frontend)
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


def pos_main(request):
    """Main Window - Landing page with Create Account/Login buttons"""
    return render(request, "pos/main.html")


@_require_student
def pos_home(request):
    """Home page - Student info and academic term display"""
    student = get_logged_in_student(request)
    if request.method == 'POST' and request.FILES.get('profile_photo'):
        student.profile_photo = request.FILES['profile_photo']
        student.save()
        return redirect('pos_home')
    return render(request, "pos/home.html", {'student': student})


@_require_student
def pos_generate(request):
    """Generate POS page - Course table with grade dropdowns; pass saved grades for this user."""
    student = get_logged_in_student(request)
    saved_grades = {g.course_code: g.grade for g in student.grades.all()}
    return render(request, "pos/generate_pos.html", {'student': student, 'saved_grades': saved_grades})


@_require_student
@require_POST
def save_pos_grades(request):
    """Save grades for the logged-in student. POST body: JSON object { "course_code": "grade", ... }."""
    student = get_logged_in_student(request)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'ok': False, 'error': 'Expected object'}, status=400)
    # Replace all grades for this student
    StudentGrade.objects.filter(student=student).delete()
    for course_code, grade in data.items():
        if course_code and grade:
            StudentGrade.objects.create(student=student, course_code=str(course_code).strip(), grade=str(grade).strip())
    return JsonResponse({'ok': True})


@_require_student
def pos_preview(request):
    """Preview POS page - Read-only POS table"""
    student = get_logged_in_student(request)
    return render(request, "pos/preview_pos.html", {'student': student})


def pos_history(request):
    """POS History page - Table of previously generated POS"""
    student = get_logged_in_student(request)
    return render(request, "pos/history.html", {'student': student})


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
        year_level = (request.POST.get('year_level') or '').strip()
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
        if not year_level:
            errors.append('Year level is required.')
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
            year_level=year_level,
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
