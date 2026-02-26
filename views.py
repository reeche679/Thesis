"""
Views for POS templates and auth (register, login, session).
"""
import io
import json
import re
import os
import threading
import time
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
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
    """True if session has at least a valid academic_year OR (year_level + semester).

    Academic Year is no longer required to generate POS; we accept either:
    - academic_year alone, or
    - a valid combination of year_level + semester.
    """
    ay = request.session.get('academic_year')
    sem = request.session.get('semester')
    yl = request.session.get('year_level')
    valid_ays = {c[0] for c in ACADEMIC_YEAR_CHOICES}
    valid_sems = {c[0] for c in SEMESTER_CHOICES}
    valid_yl = {c[0] for c in YEAR_LEVEL_CHOICES}
    has_ay = ay in valid_ays
    has_yl_sem = (yl in valid_yl) and (sem in valid_sems)
    return has_ay or has_yl_sem


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
    """Home page - Student info and academic term. Choose Regular/Irregular (if first time) and Current semester."""
    student = get_logged_in_student(request)
    if request.method == 'POST' and request.FILES.get('profile_photo'):
        student.profile_photo = request.FILES['profile_photo']
        student.save()
        return redirect('pos_home')
    has_generated_pos = POSRecord.objects.filter(student=student).exists()
    current_section_index = _year_semester_to_section_index(
        request.session.get('year_level'), request.session.get('semester')
    )
    if current_section_index is None:
        current_section_index = 0
    context = {
        'student': student,
        'academic_year_choices': ACADEMIC_YEAR_CHOICES,
        'current_semester_choices': CURRENT_SEMESTER_CHOICES_YEAR4_ONLY,
        'session_academic_year': request.session.get('academic_year'),
        'session_current_semester_index': current_section_index,
        # For new users, don't default the path; let them choose explicitly
        'session_pos_path': request.session.get('pos_path'),
        'has_generated_pos': has_generated_pos,
        'has_academic_term': _has_academic_term(request),
        'has_complete_info': _has_academic_term(request) and _has_profile_photo(request),
        'required_term': request.GET.get('required') == '1',
    }
    return render(request, "pos/home.html", context)


@_require_student
@require_POST
def set_academic_term(request):
    """Set session and student: academic_year, pos_path (regular/irregular),
    and (for Regular path) the current semester (year_level + semester)."""
    ay = (request.POST.get('academic_year') or '').strip()
    valid_ays = {c[0] for c in ACADEMIC_YEAR_CHOICES}
    if ay in valid_ays:
        request.session['academic_year'] = ay
    pos_path = (request.POST.get('pos_path') or '').strip()
    if pos_path in ('regular', 'irregular'):
        request.session['pos_path'] = pos_path
    # For Regular path, capture the chosen current semester index and map to (year_level, semester)
    if request.session.get('pos_path') == 'regular':
        current_sem_index_raw = request.POST.get('current_semester_index')
        try:
            current_sem_index = int(current_sem_index_raw) if current_sem_index_raw not in (None, '') else None
        except (TypeError, ValueError):
            current_sem_index = None
        if current_sem_index is not None and 0 <= current_sem_index < len(_INDEX_TO_YEAR_SEM):
            yl, sem = _INDEX_TO_YEAR_SEM[current_sem_index]
            request.session['year_level'] = yl
            request.session['semester'] = sem
            student = get_logged_in_student(request)
            if student is not None:
                # Persist on the Student record as well
                student.year_level = yl
                student.semester = sem
                student.save(update_fields=['year_level', 'semester'])
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('pos_home')


def _curriculum_for_irregular():
    """Build curriculum sections list for irregular POS picker (year, semester, section_label, courses)."""
    from pos_pdf import POS_CURRICULUM
    sections = []
    for title1, title2, _tu, _lec, _lab, rows in POS_CURRICULUM:
        section_label = "{} - {}".format(
            title1.replace(" ", " ").title(),
            title2.replace(" ", " ").title()
        )
        courses = [
            {"code": r[0], "title": r[1], "units": r[2], "lec": r[3], "lab": r[4], "prereq": r[5], "coreq": r[6]}
            for r in rows
        ]
        sections.append({"year": title1, "semester": title2, "label": section_label, "courses": courses})
    return sections


# Order matches POS_CURRICULUM: 0=1st Y 1st Sem, 1=1st Y 2nd Sem, 2=1st Y Midterm, 3=2nd Y 1st Sem, ...
# Section index 0-9: 1st-4th year; 10-27: 5th-10th year (each year: First Sem, Second Sem, Midterm)
_INDEX_TO_YEAR_SEM = [
    ("1st Year", "First Semester"),
    ("1st Year", "Second Semester"),
    ("1st Year", "Midterm"),
    ("2nd Year", "First Semester"),
    ("2nd Year", "Second Semester"),
    ("3rd Year", "First Semester"),
    ("3rd Year", "Second Semester"),
    ("3rd Year", "Midterm"),
    ("4th Year", "First Semester"),
    ("4th Year", "Second Semester"),
    ("5th Year", "First Semester"),
    ("5th Year", "Second Semester"),
    ("5th Year", "Midterm"),
    ("6th Year", "First Semester"),
    ("6th Year", "Second Semester"),
    ("6th Year", "Midterm"),
    ("7th Year", "First Semester"),
    ("7th Year", "Second Semester"),
    ("7th Year", "Midterm"),
    ("8th Year", "First Semester"),
    ("8th Year", "Second Semester"),
    ("8th Year", "Midterm"),
    ("9th Year", "First Semester"),
    ("9th Year", "Second Semester"),
    ("9th Year", "Midterm"),
    ("10th Year", "First Semester"),
    ("10th Year", "Second Semester"),
    ("10th Year", "Midterm"),
]
_YEAR_SEM_TO_INDEX = {(yl, sem): i for i, (yl, sem) in enumerate(_INDEX_TO_YEAR_SEM)}

# For home: current semester dropdown (section_index -> label)
CURRENT_SEMESTER_CHOICES = [
    (i, "{} - {}".format(yl, sem)) for i, (yl, sem) in enumerate(_INDEX_TO_YEAR_SEM)
]
# Only show 1st–4th year in semester dropdowns (home + add courses); 5th+ added via + Add courses on Generate POS
SECTION_INDEX_MAX_DROPDOWN = 9  # 0..9 = 1st Year through 4th Year Second Semester
CURRENT_SEMESTER_CHOICES_YEAR4_ONLY = [c for c in CURRENT_SEMESTER_CHOICES if c[0] <= SECTION_INDEX_MAX_DROPDOWN]

# For Regular path on Generate POS: allow selection up to 5th Year Second Semester (index 11)
CURRENT_SEMESTER_CHOICES_UPTO_5YEAR2 = [c for c in CURRENT_SEMESTER_CHOICES if c[0] <= 11]


def _year_semester_to_section_index(year_level, semester):
    """Return curriculum section index for (year_level, semester), or None if invalid."""
    if not year_level or not semester:
        return None
    yl = (year_level or "").strip()
    sem = (semester or "").strip()
    return _YEAR_SEM_TO_INDEX.get((yl, sem))


def _saved_grades_by_section(student):
    """Return {section_index: {course_code: grade}} so the same course can have different grades per semester."""
    by_section = {}
    for g in student.grades.all():
        si = getattr(g, 'section_index', 0)
        by_section.setdefault(si, {})[g.course_code] = g.grade
    return by_section


def _is_passing_grade(grade):
    """Return True if grade is a passing grade (not failed 5.00). Treat 5, 5.0, '5', '5.0', '5.00' as failed."""
    if grade is None or (isinstance(grade, str) and not grade.strip()):
        return False
    s = str(grade).strip()
    if s in ("5", "5.0", "5.00"):
        return False
    try:
        return float(grade) < 5.0
    except (TypeError, ValueError):
        return True


def _flexible_courses_for_section(section_index):
    """Return list of course dicts for flexible subjects offered in this section (with EE Dept note if applicable)."""
    from pos_pdf import FLEXIBLE_SUBJECTS
    out = []
    for row in FLEXIBLE_SUBJECTS:
        code, title, units, lec, lab, prereq, coreq = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        offerings = row[7] if len(row) > 7 else []
        for si, dept_note in offerings:
            if si != section_index:
                continue
            display_title = title + (" (" + dept_note + ")" if dept_note else "")
            out.append({
                "code": code,
                "title": display_title,
                "units": units,
                "lec": lec,
                "lab": lab,
                "prereq": prereq or "",
                "coreq": coreq or "",
            })
            break
    return out


def _picker_available_for_next_section(next_section_index, curriculum_sections, saved_grades_by_section):
    """
    Courses available to add: this and previous semesters where the user has no grade in that section
    or has failed (5.00) in that section. Plus flexible subjects offered in the current section.
    Failed 2nd or 3rd time still appears (only exclude when passing).
    """
    by_section = saved_grades_by_section or {}
    candidates = []
    curriculum_codes_current = {c["code"] for c in curriculum_sections[next_section_index]["courses"]}
    for si in range(next_section_index + 1):
        sec = curriculum_sections[si]
        section_grades = by_section.get(si, {})
        for c in sec["courses"]:
            code = c["code"]
            grade = section_grades.get(code)
            if _is_passing_grade(grade):
                continue
            candidates.append(c)
    for flex in _flexible_courses_for_section(next_section_index):
        if flex["code"] not in curriculum_codes_current:
            candidates.append(flex)
    return candidates


def _course_lookup_from_curriculum():
    """Build code -> (title, units, lec, lab, prereq, coreq) from first occurrence in POS_CURRICULUM."""
    from pos_pdf import POS_CURRICULUM
    lookup = {}
    for _section_index, (_t1, _t2, _tu, _lec_t, _lab_t, rows) in enumerate(POS_CURRICULUM):
        for r in rows:
            code = r[0]
            if code not in lookup:
                lookup[code] = (r[1], r[2], r[3], r[4], r[5] or "", r[6] or "")
    return lookup


def _preview_sections_from_curriculum(grades_by_section):
    """Build sections for POS preview from grades_by_section. Each section shows all courses with a grade in that section (same course can appear in multiple sections, e.g. MATH 102 failed in 1st sem and midterm). User-added courses (e.g. MATH 102 in midterm) replace curriculum-only slots in display."""
    from pos_pdf import POS_CURRICULUM
    course_lookup = _course_lookup_from_curriculum()
    sections = []
    for section_index, (title1, title2, total_units, _lec_t, _lab_t, rows) in enumerate(POS_CURRICULUM):
        section_grades = (grades_by_section or {}).get(section_index, {})
        section_label = "{} - {}".format(
            title1.replace(" ", " ").title(),
            title2.replace(" ", " ").title()
        )
        curriculum_codes_this_section = {r[0] for r in rows}
        course_rows = []
        seen_codes = set()
        for r in rows:
            code, title, units, lec, lab, prereq, coreq = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            grade = section_grades.get(code)
            if grade is not None and grade != "":
                seen_codes.add(code)
                try:
                    g = float(grade)
                    remark = "Passed" if g <= 3.00 else ("Failed" if g == 5.00 else "")
                except (ValueError, TypeError):
                    remark = ""
                course_rows.append({
                    "code": code,
                    "title": title,
                    "units": units,
                    "lec": lec,
                    "lab": lab,
                    "prereq": prereq or "",
                    "coreq": coreq or "",
                    "grade": grade,
                    "remark": remark,
                })
        for code, grade in (section_grades or {}).items():
            if not code or not grade:
                continue
            if code in seen_codes:
                continue
            seen_codes.add(code)
            info = course_lookup.get(code)
            if info:
                title, units, lec, lab, prereq, coreq = info
            else:
                title, units, lec, lab, prereq, coreq = code, 0, 0, 0, "", ""
            try:
                g = float(grade)
                remark = "Passed" if g <= 3.00 else ("Failed" if g == 5.00 else "")
            except (ValueError, TypeError):
                remark = ""
            course_rows.append({
                "code": code,
                "title": title,
                "units": units,
                "lec": lec,
                "lab": lab,
                "prereq": prereq,
                "coreq": coreq,
                "grade": grade,
                "remark": remark,
            })
        total_units_shown = sum(row.get("units", 0) for row in course_rows) if course_rows else 0
        if course_rows:
            sections.append({
                "label": section_label,
                "total_units": total_units_shown or total_units,
                "rows": course_rows,
            })
    return sections


@_require_student
def pos_generate(request):
    """Generate POS page. Current semester is set by ?section=N or from session (year_level + semester)."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    saved_grades_by_section = _saved_grades_by_section(student)
    curriculum_sections = _curriculum_for_irregular()

    section_param = request.GET.get('section')
    if section_param is not None:
        try:
            idx = int(section_param)
            if 0 <= idx < len(_INDEX_TO_YEAR_SEM):
                yl, sem = _INDEX_TO_YEAR_SEM[idx]
                request.session['year_level'] = yl
                request.session['semester'] = sem
                if hasattr(student, 'year_level'):
                    student.year_level = yl
                    student.semester = sem
                    student.save(update_fields=['year_level', 'semester'])
                next_section_index = idx
            else:
                next_section_index = _year_semester_to_section_index(
                    request.session.get('year_level'), request.session.get('semester')
                )
        except (ValueError, TypeError):
            next_section_index = _year_semester_to_section_index(
                request.session.get('year_level'), request.session.get('semester')
            )
    else:
        year_level = request.session.get('year_level') or ''
        semester = request.session.get('semester') or ''
        next_section_index = _year_semester_to_section_index(year_level, semester)

    if next_section_index is None:
        next_section_index = 0
        if next_section_index < len(_INDEX_TO_YEAR_SEM):
            yl, sem = _INDEX_TO_YEAR_SEM[next_section_index]
            request.session['year_level'] = yl
            request.session['semester'] = sem
    year_level = request.session.get('year_level') or ''
    semester = request.session.get('semester') or ''
    pos_path = request.session.get('pos_path') or 'irregular'
    is_regular = pos_path == 'regular'

    # Build helpers shared by both Regular and Irregular paths
    course_lookup = _course_lookup_from_curriculum()
    preview_sections = []
    display_sections = []
    editable_section_index = None
    selected_has_grades = False
    # For every section, precompute courses available to add (this + previous semesters, failed or not yet taken, plus flexible)
    picker_available_by_section = {
        si: _picker_available_for_next_section(si, curriculum_sections, saved_grades_by_section)
        for si in range(len(curriculum_sections))
    }

    if is_regular:
        # Regular path: display curriculum sections from 0 up to the selected current semester,
        # automatically listing all curriculum courses for each section.
        # The user simply enters grades; no per-semester "Add courses" picker.
        for si in range(next_section_index + 1):
            sec = curriculum_sections[si]
            section_grades = saved_grades_by_section.get(si, {})
            courses_with_grades = []
            for c in sec["courses"]:
                code = c["code"]
                grade = section_grades.get(code)
                courses_with_grades.append({'course': c, 'grade': grade})
            total_units_display = sum(c.get('units', 0) or 0 for c in sec["courses"])
            display_sections.append({
                'section': sec, 'courses_with_grades': courses_with_grades, 'section_index': si,
                'total_units': total_units_display,
            })
    else:
        # Irregular: preview sections that have grades; one editable section (first without grades or selected).
        for si in range(len(curriculum_sections)):
            sec = curriculum_sections[si]
            section_grades = saved_grades_by_section.get(si, {})
            courses_with_grades = []
            seen = set()
            for c in sec['courses']:
                if c['code'] in section_grades and section_grades[c['code']]:
                    seen.add(c['code'])
                    courses_with_grades.append({'course': c, 'grade': section_grades[c['code']]})
            for code, grade in (section_grades or {}).items():
                if not code or not grade or code in seen:
                    continue
                seen.add(code)
                info = course_lookup.get(code)
                if info:
                    title, units, lec, lab, prereq, coreq = info
                else:
                    title, units, lec, lab, prereq, coreq = code, 0, 0, 0, "", ""
                courses_with_grades.append({
                    'course': {'code': code, 'title': title, 'units': units, 'lec': lec, 'lab': lab, 'prereq': prereq, 'coreq': coreq},
                    'grade': grade,
                })
            if courses_with_grades:
                total_units = sum(cg['course'].get('units', 0) or 0 for cg in courses_with_grades)
                preview_sections.append({
                    'section': sec, 'courses_with_grades': courses_with_grades, 'section_index': si,
                    'total_units': total_units,
                })

        selected_has_grades = bool(saved_grades_by_section.get(next_section_index))
        editable_section_index = next_section_index
        if selected_has_grades:
            for si in range(len(curriculum_sections)):
                if not saved_grades_by_section.get(si):
                    editable_section_index = si
                    break
            else:
                editable_section_index = None

        if editable_section_index is not None:
            sec = curriculum_sections[editable_section_index]
            section_grades = saved_grades_by_section.get(editable_section_index, {})
            courses_with_grades = [
                {'course': c, 'grade': section_grades.get(c['code'])}
                for c in sec['courses']
                if c['code'] in section_grades
            ]
            total_units_display = sum(cg['course'].get('units', 0) or 0 for cg in courses_with_grades)
            display_sections = [
                {
                    'section': sec, 'courses_with_grades': courses_with_grades, 'section_index': editable_section_index,
                    'total_units': total_units_display,
                }
            ]
        else:
            display_sections = []

    context = {
        'student': student,
        'saved_grades_by_section': saved_grades_by_section,
        'saved_only': saved_grades_by_section,
        'session_academic_year': request.session.get('academic_year', ''),
        'session_semester': request.session.get('semester', ''),
        'session_year_level': year_level,
        'curriculum_sections': curriculum_sections,
        'curriculum_sections_dropdown': curriculum_sections[:SECTION_INDEX_MAX_DROPDOWN + 1],
        'current_semester_choices_regular': CURRENT_SEMESTER_CHOICES_UPTO_5YEAR2,
        'current_semester_index': next_section_index,
        'preview_sections': preview_sections,
        'display_sections': display_sections,
        'next_section_index': next_section_index,
        'editable_section_index': editable_section_index,
        'selected_section_has_grades': selected_has_grades,
        'picker_available_by_section': picker_available_by_section,
        'is_regular': is_regular,
        'pos_path': pos_path,
    }
    return render(request, "pos/generate_pos.html", context)


@_require_student
@require_POST
def save_pos_grades(request):
    """Save grades by section. POST body: { "grades_by_section": { "0": {"MATH 101": "5.00"}, "2": {"MATH 101": "2.00"} }, "generated": true }.
    Same course can have different grades in different sections (e.g. failed in one, passed in another)."""
    student = get_logged_in_student(request)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'ok': False, 'error': 'Expected object'}, status=400)
    grades_by_section = data.get('grades_by_section')
    if grades_by_section is None:
        grades_by_section = data.get('grades')
        if isinstance(grades_by_section, dict) and grades_by_section and not any(isinstance(v, dict) for v in grades_by_section.values()):
            grades_by_section = {'0': grades_by_section}
    record_generated = data.get('generated') is True
    if not isinstance(grades_by_section, dict):
        grades_by_section = {}
    StudentGrade.objects.filter(student=student).delete()
    for section_key, section_dict in grades_by_section.items():
        if not isinstance(section_dict, dict):
            continue
        try:
            si = int(section_key)
        except (ValueError, TypeError):
            continue
        for course_code, grade in section_dict.items():
            if course_code and grade:
                StudentGrade.objects.create(
                    student=student,
                    course_code=str(course_code).strip(),
                    grade=str(grade).strip(),
                    section_index=si,
                )
    if record_generated:
        ay = request.session.get('academic_year') or ''
        sem = request.session.get('semester') or ''
        yl = request.session.get('year_level') or ''
        POSRecord.objects.create(
            student=student,
            generated_at=timezone.now(),
            academic_year=ay,
            semester=sem,
            year_level=yl,
            grades_snapshot=grades_by_section,
        )
        _prune_old_pos_records(student)
        request.session['pos_path'] = 'irregular'
    return JsonResponse({'ok': True})


@_require_student
@require_POST
def upload_pos_grades(request):
    """Upload a PDF; extract grades with real progress. Returns streaming response: progress lines (0-100) then JSON."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return JsonResponse({'ok': False, 'errors': ['Complete your profile and set academic term first.']}, status=400)
    student = get_logged_in_student(request)
    pdf_file = request.FILES.get('pos_pdf') or request.FILES.get('file')
    if not pdf_file:
        return JsonResponse({'ok': False, 'errors': ['No file uploaded.']}, status=400)
    name = (pdf_file.name or '').lower()
    if not name.endswith('.pdf'):
        return JsonResponse({'ok': False, 'errors': ['Only PDF files are accepted.']}, status=400)

    raw = pdf_file.read()
    progress_list = [0]
    result_holder = {}

    def run_extraction():
        try:
            from pos_extract import extract_pos_from_pdf
            g, e = extract_pos_from_pdf(io.BytesIO(raw), progress_list=progress_list)
            result_holder['grades'] = g
            result_holder['errors'] = e
        except Exception as ex:
            result_holder['grades'] = {}
            result_holder['errors'] = ['Failed to read PDF: {}'.format(str(ex))]

    def stream():
        t = threading.Thread(target=run_extraction)
        t.start()
        while t.is_alive():
            yield str(progress_list[0]) + "\n"
            # Send progress updates more frequently so the frontend bar moves smoothly
            time.sleep(0.1)
        yield "100\n"
        grades_by_section = result_holder.get('grades') or {}
        errors = result_holder.get('errors') or []
        if errors:
            yield json.dumps({'ok': False, 'errors': errors}) + "\n"
            return
        if not grades_by_section:
            yield json.dumps({'ok': False, 'errors': ['No valid grades found in PDF. Ensure course codes and grades (1.00–3.00, 5.00) are readable.']}) + "\n"
            return
        StudentGrade.objects.filter(student=student).delete()
        for section_key, section_dict in grades_by_section.items():
            try:
                si = int(section_key)
            except (ValueError, TypeError):
                continue
            for course_code, grade in section_dict.items():
                if course_code and grade:
                    StudentGrade.objects.create(
                        student=student,
                        course_code=str(course_code).strip(),
                        grade=str(grade).strip(),
                        section_index=si,
                    )
        request.session['pos_path'] = 'irregular'
        yield json.dumps({'ok': True, 'redirect': reverse('pos_preview')}) + "\n"

    return StreamingHttpResponse(stream(), content_type='application/x-ndjson')


@_require_student
@require_POST
def validate_pos(request):
    """Validate POS: check grades_by_section for failed-unit ratio and completeness. Returns JSON { ok, message, errors }."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'Invalid JSON', 'errors': []}, status=400)
    grades_by_section = data.get('grades_by_section') or {}
    if not isinstance(grades_by_section, dict):
        grades_by_section = {}
    errors = []
    course_lookup = _course_lookup_from_curriculum()
    from pos_pdf import POS_CURRICULUM
    for section_key, section_grades in grades_by_section.items():
        if not isinstance(section_grades, dict):
            continue
        try:
            si = int(section_key)
        except (ValueError, TypeError):
            continue
        if si < 0 or si >= len(POS_CURRICULUM):
            continue
        _t1, _t2, total_units, _lec, _lab, rows = POS_CURRICULUM[si]
        section_label = "{} - {}".format(_t1.title(), _t2.title())
        total_u = 0
        failed_u = 0
        for code, grade in section_grades.items():
            if not code:
                continue
            info = course_lookup.get(code)
            units = info[1] if info else 0
            total_u += units
            if _is_passing_grade(grade):
                continue
            failed_u += units
        if total_u <= 0:
            continue
        ratio = failed_u / total_u
        if ratio >= 0.75:
            errors.append("{}: 75% or more of units failed. Cannot generate POS.".format(section_label))
        elif ratio >= 0.50:
            errors.append("{}: 50% or more of units failed. Please proceed to the department for manual evaluation.".format(section_label))
    ok = len(errors) == 0
    return JsonResponse({
        'ok': ok,
        'message': 'POS is valid.' if ok else 'Validation failed.',
        'errors': errors,
    })


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
    """Preview POS page - Read-only POS table matching PDF layout; each section shows that section's grades (same course can differ per section)."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    grades_by_section = _saved_grades_by_section(student)
    preview_sections = _preview_sections_from_curriculum(grades_by_section)
    context = {
        'student': student,
        'preview_sections': preview_sections,
        'session_academic_year': request.session.get('academic_year', ''),
        'session_semester': request.session.get('semester', ''),
    }
    return render(request, "pos/preview_pos.html", context)


@_require_student
def download_pos_pdf(request):
    """Generate and download POS PDF with student info and grades per section (BSU BSCpE layout)."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    grades_by_section = _saved_grades_by_section(student)
    academic_year = request.session.get('academic_year', '') or student.academic_year or ''

    from pos_pdf import build_pos_pdf
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None
    pdf_bytes = build_pos_pdf(
        student_name=student.full_name or '',
        sr_code=student.sr_code or '',
        academic_year=academic_year,
        grades_by_section=grades_by_section,
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
    snapshot = getattr(record, 'grades_snapshot', None) or {}
    if isinstance(snapshot, dict) and snapshot and not any(isinstance(v, dict) for v in snapshot.values()):
        grades_by_section = {'0': snapshot}
    else:
        grades_by_section = snapshot
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
        grades_by_section=grades_by_section,
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
