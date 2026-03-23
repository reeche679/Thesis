"""
Views for POS templates and auth (register, login, session).
"""
import json
import re
import os
import copy
import math
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt

from django.utils import timezone
from accounts.models import Student, StudentGrade, POSRecord
from .solver_bridge import generate_pos_via_solver, validate_pos_via_solver, get_solver_metadata

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
    """Set session academic year and default POS path configuration."""
    ay = (request.POST.get('academic_year') or '').strip()
    valid_ays = {c[0] for c in ACADEMIC_YEAR_CHOICES}
    if ay in valid_ays:
        request.session['academic_year'] = ay
    # Single-path flow: always use irregular configuration.
    request.session['pos_path'] = 'irregular'
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
    from .pos_pdf import POS_CURRICULUM, display_pos_course_code
    sections = []
    for title1, title2, _tu, _lec, _lab, rows in POS_CURRICULUM:
        section_label = "{} - {}".format(
            title1.replace(" ", " ").title(),
            title2.replace(" ", " ").title()
        )
        courses = [
            {
                "code": r[0],
                "display_code": display_pos_course_code(r[0]),
                "title": r[1],
                "units": r[2],
                "lec": r[3],
                "lab": r[4],
                "prereq": r[5],
                "coreq": r[6],
            }
            for r in rows
        ]
        sections.append({"year": title1, "semester": title2, "label": section_label, "courses": courses})
    return sections


# Order matches POS_CURRICULUM: 0=1st Y 1st Sem, 1=1st Y 2nd Sem, 2=1st Y Midterm, 3=2nd Y 1st Sem, ...
# Section index 0-11: 1st-4th year; 12-29: 5th-10th year (each year: First Sem, Second Sem, Midterm)
_INDEX_TO_YEAR_SEM = [
    ("1st Year", "First Semester"),
    ("1st Year", "Second Semester"),
    ("1st Year", "Midterm"),
    ("2nd Year", "First Semester"),
    ("2nd Year", "Second Semester"),
    ("2nd Year", "Midterm"),
    ("3rd Year", "First Semester"),
    ("3rd Year", "Second Semester"),
    ("3rd Year", "Midterm"),
    ("4th Year", "First Semester"),
    ("4th Year", "Second Semester"),
    ("4th Year", "Midterm"),
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
SECTION_INDEX_MAX_DROPDOWN = 10  # 0..10 = 1st Year through 4th Year Second Semester (incl. 2nd Year Midterm)
CURRENT_SEMESTER_CHOICES_YEAR4_ONLY = [c for c in CURRENT_SEMESTER_CHOICES if c[0] <= SECTION_INDEX_MAX_DROPDOWN]

# Regular POS dropdown may include 1st Year - First Semester, but the page should
# show no semester tables until the user explicitly selects a value.
FIRST_YEAR_FIRST_SEM_INDEX = 0

# For Regular path on Generate POS: allow selection up to 5th Year Second Semester.
# With 3 semesters per year:
# - 5th Year First Sem = 12
# - 5th Year Second Sem = 13
CURRENT_SEMESTER_CHOICES_UPTO_5YEAR2 = [c for c in CURRENT_SEMESTER_CHOICES if c[0] <= 13]
# Regular path dropdown: exclude 2nd Year Midterm (index 5) - add-course only, not a valid "current semester" for regular
SECOND_YEAR_MIDTERM_INDEX = 5
CURRENT_SEMESTER_CHOICES_REGULAR = [
    c
    for c in CURRENT_SEMESTER_CHOICES_UPTO_5YEAR2
    if c[0] != SECOND_YEAR_MIDTERM_INDEX
]

# Irregular path dropdowns (Generate POS): allow selection up to 5th Year Second Semester.
# This dropdown is used for both "LAST SEMESTER ENROLLED" and "WHEN YOU BECAME IRREGULAR"
# in the Irregular section of the Generate POS page.
SECTION_INDEX_MAX_DROPDOWN_GENERATE = 13  # includes 5th Year First Sem (12) + 5th Year Second Sem (13)


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
    from .pos_pdf import FLEXIBLE_SUBJECTS, display_pos_course_code
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
                "display_code": display_pos_course_code(code),
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
    from .pos_pdf import POS_CURRICULUM
    lookup = {}
    for _section_index, (_t1, _t2, _tu, _lec_t, _lab_t, rows) in enumerate(POS_CURRICULUM):
        for r in rows:
            code = r[0]
            if code not in lookup:
                lookup[code] = (r[1], r[2], r[3], r[4], r[5] or "", r[6] or "")
    return lookup


def _preview_sections_from_curriculum(grades_by_section):
    """Build sections for POS preview from grades_by_section. Each section shows all courses with a grade in that section (same course can appear in multiple sections, e.g. MATH 102 failed in 1st sem and midterm). User-added courses (e.g. MATH 102 in midterm) replace curriculum-only slots in display."""
    from .pos_pdf import POS_CURRICULUM, display_pos_course_code
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
                    "display_code": display_pos_course_code(code),
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
                "display_code": display_pos_course_code(code),
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


@ensure_csrf_cookie
@_require_student
def pos_generate(request):
    """Generate POS page. Current semester is set by ?section=N or from session (year_level + semester)."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    saved_grades_by_section = _saved_grades_by_section(student)
    curriculum_sections = _curriculum_for_irregular()

    section_param = request.GET.get('section')
    pos_reset = bool(request.session.get('pos_reset', False))
    section_param_provided = section_param is not None
    if section_param is not None:
        # If user clicked Regular OK after a POS delete, they are intentionally resetting the form.
        if pos_reset:
            request.session.pop('pos_reset', None)
            pos_reset = False
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

    # If the user hasn't selected a semester yet (no ?section=...), do not
    # default to 1st Year - First Semester. New users should see no semester
    # tables until they choose a value. After POS deletion we already set
    # `pos_reset`, which keeps this logic inactive.
    has_any_grades = any(bool(gdict) for gdict in (saved_grades_by_section or {}).values())
    # If the user didn't explicitly choose a semester in the URL and we're not
    # in a reset state, only show "no selection" when there are no saved grades.
    # This preserves valid index 0 (1st Year - First Semester) when grades exist.
    if (not section_param_provided) and (not pos_reset) and (not has_any_grades):
        next_section_index = -1

    if next_section_index is None:
        # After POS delete, we want "no input" state (no default to 1st semester).
        # When reset is active, avoid writing default year/semester into the session.
        if pos_reset:
            next_section_index = -1
        else:
            next_section_index = 0
            if next_section_index < len(_INDEX_TO_YEAR_SEM):
                yl, sem = _INDEX_TO_YEAR_SEM[next_section_index]
                request.session['year_level'] = yl
                request.session['semester'] = sem
    year_level = request.session.get('year_level') or ''
    semester = request.session.get('semester') or ''
    pos_path = request.session.get('pos_path') or 'irregular'
    is_regular = pos_path == 'regular'

    # Regular path: persist "With Petition?" selection across reloads (Regular OK button reloads).
    regular_with_petition = request.session.get('regular_with_petition', 'yes')
    if is_regular and section_param is not None:
        wp = (request.GET.get('with_petition') or '').strip().lower()
        if wp in ('yes', 'no'):
            request.session['regular_with_petition'] = wp
            regular_with_petition = wp

    # Build helpers shared by both Regular and Irregular paths
    from .pos_pdf import display_pos_course_code
    course_lookup = _course_lookup_from_curriculum()
    preview_sections = []
    display_sections = []
    editable_section_index = None
    selected_has_grades = False
    irreg_configured = True
    irreg_start_index = None
    irreg_current_sem = None
    pos_reload_pending = False
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
        # Irregular path: two key params from GET (or session fallback)
        # irreg_start = semester index when user became irregular (curriculum listed up to here)
        # current_sem = user's current semester (add-course sections stop here)
        def _safe_int(val, default=None):
            # Important for dropdowns that intentionally send an empty string:
            # treat '' as an explicit "unset" (None) rather than falling back to session.
            if val == '':
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        irreg_start_param = request.GET.get('irreg_start')
        current_sem_param = request.GET.get('current_sem')
        with_petition = request.GET.get('with_petition', '')

        became_irregular_explicit = (
            irreg_start_param is not None and str(irreg_start_param).strip() != ""
        )

        if current_sem_param is not None:
            request.session.pop('pos_reload_pending', None)

        pos_reload_pending = bool(request.session.get('pos_reload_pending'))

        irreg_start_index = _safe_int(irreg_start_param, _safe_int(request.session.get('irreg_start_index')))
        irreg_current_sem = _safe_int(current_sem_param, _safe_int(request.session.get('irreg_current_sem')))

        # Irregular generation only needs CURRENT semester. If IRREG_START is left empty,
        # we'll treat it as "effective start = current semester" for curriculum display/constraints.
        irreg_configured = irreg_current_sem is not None
        use_reload_full_tables = pos_reload_pending and not irreg_configured

        if pos_reset and irreg_configured:
            request.session.pop('pos_reset', None)
            pos_reset = False

        effective_irreg_start_index = None
        table_scope_sem = None

        if irreg_configured:
            # If user explicitly set IRREG_START via the URL (including leaving it blank),
            # update/clear the session accordingly so the dropdown doesn't revert later.
            if irreg_start_param is not None:
                if irreg_start_index is not None:
                    request.session['irreg_start_index'] = irreg_start_index
                else:
                    request.session.pop('irreg_start_index', None)
            if irreg_current_sem is not None:
                request.session['irreg_current_sem'] = irreg_current_sem
            if with_petition in ('yes', 'no'):
                request.session['irreg_with_petition'] = with_petition

            max_si = len(curriculum_sections) - 1
            # Effective start: if user left it unset, it starts at current semester.
            effective_irreg_start_index = irreg_start_index
            if effective_irreg_start_index is None:
                effective_irreg_start_index = irreg_current_sem

            # Defense-in-depth:
            # If WHEN YOU BECAME IRREGULAR is explicitly selected, honor that exact dropdown value.
            # This prevents any accidental fallback to "effective start = current semester".
            if became_irregular_explicit:
                try:
                    explicit_si = int(irreg_start_param)
                    effective_irreg_start_index = explicit_si
                except (TypeError, ValueError):
                    pass

            effective_irreg_start_index = max(0, min(effective_irreg_start_index, max_si))
            irreg_current_sem = max(0, min(irreg_current_sem, max_si))
            if irreg_current_sem < effective_irreg_start_index:
                irreg_current_sem = effective_irreg_start_index

            table_scope_sem = irreg_current_sem

            yl, sem = _INDEX_TO_YEAR_SEM[irreg_current_sem]
            request.session['year_level'] = yl
            request.session['semester'] = sem
            if hasattr(student, 'year_level'):
                student.year_level = yl
                student.semester = sem
                student.save(update_fields=['year_level', 'semester'])

        elif use_reload_full_tables:
            # Loaded from history: show full saved POS + grades until user picks Last Semester Enrolled + OK.
            gen_pos = request.session.get("last_generated_pos") or []
            max_si = len(curriculum_sections) - 1

            loaded_preview_ui_sections = request.session.get("pos_loaded_preview_ui_sections") or {}
            table_scope_sem = -1
            if isinstance(loaded_preview_ui_sections, dict) and loaded_preview_ui_sections:
                for k, courses in loaded_preview_ui_sections.items():
                    try:
                        si = int(k)
                    except (TypeError, ValueError):
                        continue
                    if si < 0 or si > max_si:
                        continue
                    if isinstance(courses, list) and any(
                        (c.get("code") if isinstance(c, dict) else None) for c in courses
                    ):
                        table_scope_sem = max(table_scope_sem, si)

            if table_scope_sem < 0:
                table_scope_sem = _max_ui_section_from_grades_and_pos(saved_grades_by_section, gen_pos)
            table_scope_sem = max(0, min(table_scope_sem, max_si))
            effective_irreg_start_index = irreg_start_index
            if effective_irreg_start_index is None:
                effective_irreg_start_index = table_scope_sem
            effective_irreg_start_index = max(0, min(effective_irreg_start_index, max_si))
            if table_scope_sem < effective_irreg_start_index:
                table_scope_sem = effective_irreg_start_index

        if irreg_configured or use_reload_full_tables:
            # Collect all passed course codes across all sections
            all_passed_codes = set()
            for si_check in range(len(curriculum_sections)):
                for code, grade in (saved_grades_by_section.get(si_check) or {}).items():
                    if _is_passing_grade(grade):
                        all_passed_codes.add(code)

            # When the user explicitly picked "WHEN YOU BECAME IRREGULAR",
            # the semester tables after that boundary should be manual-add (empty),
            # even if we previously loaded a POS from history.
            # If we loaded a POS from history, we should continue using the saved Preview tables
            # even when both dropdowns are selected (OK redirect includes both params).
            # Otherwise we would incorrectly switch to "manual add" mode and wipe courses.
            use_loaded_schedule = bool(request.session.get('pos_loaded_from_history'))
            loaded_preview_ui_sections = request.session.get("pos_loaded_preview_ui_sections") or {}
            use_loaded_preview_ui_sections = (
                use_loaded_schedule and isinstance(loaded_preview_ui_sections, dict) and bool(loaded_preview_ui_sections)
            )

            # Use the solver/tuned schedule stored in session so we can show
            # ungraded semester tables (courses only) when loading/editing.
            generated_pos = request.session.get("last_generated_pos") or []
            gen_pos_sem_meta_by_solver = {}
            selected_codes_by_ui_sem = {}
            for sem in (generated_pos or []):
                if not isinstance(sem, dict):
                    continue
                try:
                    solver_sem_idx = int(sem.get("semester_index"))
                except (TypeError, ValueError):
                    continue
                ui_si = solver_sem_idx - 1
                if ui_si < 0:
                    continue

                sem_courses = sem.get("courses") or []
                code_to_course_info = {}
                codes_set = set()
                if isinstance(sem_courses, list):
                    for c in sem_courses:
                        if not isinstance(c, dict):
                            continue
                        code = str(c.get("code") or "").strip()
                        if not code:
                            continue
                        code_to_course_info[code] = c
                        codes_set.add(code)

                selected_codes_by_ui_sem[ui_si] = codes_set
                gen_pos_sem_meta_by_solver[solver_sem_idx] = {
                    "code_to_course_info": code_to_course_info,
                    "codes_set": codes_set,
                }

            # Sections 0..effective_irreg_start_index: show all curriculum courses (like regular path)
            for si in range(effective_irreg_start_index + 1):
                sec = curriculum_sections[si]
                section_grades = saved_grades_by_section.get(si, {})
                courses_with_grades = []

                # When loading from Preview history, use the exact course table
                # the user saved (including ungraded rows), not the static curriculum list.
                loaded_key = str(si)
                if use_loaded_preview_ui_sections:
                    loaded_courses = loaded_preview_ui_sections.get(loaded_key) or []
                    if not isinstance(loaded_courses, list) or not loaded_courses:
                        continue
                    for entry in loaded_courses:
                        if not isinstance(entry, dict):
                            continue
                        code = str(entry.get("code") or "").strip()
                        if not code:
                            continue
                        grade = section_grades.get(code)

                        # Prefer saved metadata from preview; otherwise fall back to curriculum lookup.
                        title = entry.get("title") or code
                        units = entry.get("units", 0) or 0
                        lec = entry.get("lec", "") or ""
                        lab = entry.get("lab", "") or ""
                        prereq = entry.get("prereq") or entry.get("prerequisites", "") or ""
                        coreq = entry.get("coreq") or entry.get("corequisites", "") or ""
                        try:
                            units_f = float(units) if units not in ("", None) else 0
                        except (TypeError, ValueError):
                            units_f = 0

                        if code in course_lookup and (not entry.get("title") or units_f == 0):
                            _title, _units, _lec, _lab, _prereq, _coreq = course_lookup.get(code)
                            title = title or _title
                            if units_f == 0:
                                units_f = _units
                            lec = lec or _lec
                            lab = lab or _lab
                            prereq = prereq or _prereq
                            coreq = coreq or _coreq

                        courses_with_grades.append({
                            "course": {
                                "code": code,
                                "display_code": display_pos_course_code(code),
                                "title": title,
                                "units": units_f,
                                "lec": lec,
                                "lab": lab,
                                "prereq": prereq,
                                "coreq": coreq,
                            },
                            "grade": grade,
                        })
                else:
                    for c in sec["courses"]:
                        code = c["code"]
                        grade = section_grades.get(code)
                        courses_with_grades.append({'course': c, 'grade': grade})

                if not courses_with_grades and not became_irregular_explicit:
                    continue

                total_units_display = sum(cg['course'].get('units', 0) or 0 for cg in courses_with_grades)
                display_sections.append({
                    'section': sec, 'courses_with_grades': courses_with_grades, 'section_index': si,
                    'total_units': total_units_display,
                    'is_curriculum_locked': True,
                })

            # Sections (effective_irreg_start_index+1)..table_scope_sem: add-course sections
            for si in range(effective_irreg_start_index + 1, table_scope_sem + 1):
                sec = curriculum_sections[si]
                section_grades = saved_grades_by_section.get(si, {})
                courses_with_grades = []
                seen = set()

                # When loading from Preview history, use the exact course tables
                # the user saved (including ungraded rows).
                loaded_key = str(si)
                if use_loaded_preview_ui_sections:
                    loaded_courses = loaded_preview_ui_sections.get(loaded_key) or []
                    if not isinstance(loaded_courses, list) or not loaded_courses:
                        continue
                    for entry in loaded_courses:
                        if not isinstance(entry, dict):
                            continue
                        code = str(entry.get("code") or "").strip()
                        if not code:
                            continue
                        grade = section_grades.get(code)

                        title = entry.get("title") or code
                        units = entry.get("units", 0) or 0
                        lec = entry.get("lec", "") or ""
                        lab = entry.get("lab", "") or ""
                        prereq = entry.get("prereq") or entry.get("prerequisites", "") or ""
                        coreq = entry.get("coreq") or entry.get("corequisites", "") or ""
                        try:
                            units_f = float(units) if units not in ("", None) else 0
                        except (TypeError, ValueError):
                            units_f = 0

                        if code in course_lookup and (not entry.get("title") or units_f == 0):
                            _title, _units, _lec, _lab, _prereq, _coreq = course_lookup.get(code)
                            title = title or _title
                            if units_f == 0:
                                units_f = _units
                            lec = lec or _lec
                            lab = lab or _lab
                            prereq = prereq or _prereq
                            coreq = coreq or _coreq

                        courses_with_grades.append({
                            "course": {
                                "code": code,
                                "display_code": display_pos_course_code(code),
                                "title": title,
                                "units": units_f,
                                "lec": lec,
                                "lab": lab,
                                "prereq": prereq,
                                "coreq": coreq,
                            },
                            "grade": grade,
                        })

                    if not courses_with_grades:
                        continue

                    total_units_display = sum(cg['course'].get('units', 0) or 0 for cg in courses_with_grades)
                    display_sections.append({
                        'section': sec,
                        'courses_with_grades': courses_with_grades,
                        'section_index': si,
                        'total_units': total_units_display,
                        # When loading from history, semesters are already decided
                        # in the saved Preview, so we hide "+ Add courses".
                        'is_curriculum_locked': bool(use_loaded_schedule),
                    })
                    continue

                selected_codes = selected_codes_by_ui_sem.get(si) or set()
                sem_meta = gen_pos_sem_meta_by_solver.get(si + 1) or {}
                code_to_course_info = sem_meta.get("code_to_course_info") or {}

                # Prefer solver-tuned course list so we show ungraded semesters too.
                # Only do this when loading from history; otherwise keep original
                # behavior for the normal workflow.
                if use_loaded_schedule and selected_codes:
                    for code in selected_codes:
                        grade = section_grades.get(code)
                        info = code_to_course_info.get(code)
                        if isinstance(info, dict) and info:
                            title = info.get("title") or code
                            try:
                                units = info.get("units", 0) or 0
                                units = float(units) if units is not None else 0
                            except (TypeError, ValueError):
                                units = 0
                            lec = info.get("lec", "") or ""
                            lab = info.get("lab", "") or ""
                            prereq = info.get("prereq", "") or info.get("prerequisites", "") or ""
                            coreq = info.get("coreq", "") or info.get("corequisites", "") or ""
                        else:
                            _info = course_lookup.get(code)
                            if _info:
                                title, units, lec, lab, prereq, coreq = _info
                            else:
                                title, units, lec, lab, prereq, coreq = code, 0, 0, 0, "", ""

                        courses_with_grades.append({
                            'course': {
                                'code': code,
                                'display_code': display_pos_course_code(code),
                                'title': title,
                                'units': units,
                                'lec': lec,
                                'lab': lab,
                                'prereq': prereq,
                                'coreq': coreq,
                            },
                            'grade': grade,
                        })
                        seen.add(code)

                    # Also include any graded courses that somehow aren't in the solver list.
                    for code, grade in (section_grades or {}).items():
                        if not code or code in seen or not grade:
                            continue
                        _info = course_lookup.get(code)
                        if _info:
                            title, units, lec, lab, prereq, coreq = _info
                        else:
                            title, units, lec, lab, prereq, coreq = code, 0, 0, 0, "", ""
                        courses_with_grades.append({
                            'course': {
                                'code': code,
                                'display_code': display_pos_course_code(code),
                                'title': title,
                                'units': units,
                                'lec': lec,
                                'lab': lab,
                                'prereq': prereq,
                                'coreq': coreq,
                            },
                            'grade': grade,
                        })
                        seen.add(code)
                else:
                    # Fallback: old behavior based only on saved grades.
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
                            'course': {
                                'code': code,
                                'display_code': display_pos_course_code(code),
                                'title': title,
                                'units': units,
                                'lec': lec,
                                'lab': lab,
                                'prereq': prereq,
                                'coreq': coreq,
                            },
                            'grade': grade,
                        })

                if not courses_with_grades:
                    # If user explicitly configured when they became irregular,
                    # we still need to render the unlocked semesters as empty tables
                    # so the UI can show "+ Add courses" and let the user add courses.
                    if became_irregular_explicit:
                        total_units = 0
                        display_sections.append({
                            'section': sec,
                            'courses_with_grades': [],
                            'section_index': si,
                            'total_units': total_units,
                            # If we're loading from history, keep it "locked"
                            # so we don't show "+ Add courses" and don't trigger re-add.
                            'is_curriculum_locked': bool(use_loaded_schedule),
                        })
                    continue

                total_units = sum(cg['course'].get('units', 0) or 0 for cg in courses_with_grades)
                display_sections.append({
                    'section': sec, 'courses_with_grades': courses_with_grades, 'section_index': si,
                    'total_units': total_units,
                    'is_curriculum_locked': False,
                })

            editable_section_index = (
                table_scope_sem if table_scope_sem > effective_irreg_start_index else None
            )

            # Rebuild picker_available_by_section filtering out passed courses
            picker_available_by_section = {}
            for si in range(effective_irreg_start_index + 1, table_scope_sem + 1):
                candidates = _picker_available_for_next_section(si, curriculum_sections, saved_grades_by_section)
                if use_loaded_schedule:
                    if use_loaded_preview_ui_sections:
                        loaded_list = loaded_preview_ui_sections.get(str(si)) or []
                        selected_in_sem = {
                            str(e.get("code")).strip()
                            for e in loaded_list
                            if isinstance(e, dict) and (e.get("code") is not None) and str(e.get("code")).strip() != ""
                        }
                    else:
                        selected_in_sem = selected_codes_by_ui_sem.get(si) or set()
                    candidates = [
                        c for c in candidates
                        if c.get('code') not in all_passed_codes and c.get('code') not in selected_in_sem
                    ]
                else:
                    candidates = [c for c in candidates if c.get('code') not in all_passed_codes]
                picker_available_by_section[si] = candidates

    has_generated_pos = POSRecord.objects.filter(student=student).exists()
    # POS delete should reset Generate/Preview back to "no input" even if History exists.
    if pos_reset:
        has_generated_pos = False

    semester_tables_visible = len(display_sections) > 0
    pos_reload_banner = bool(pos_reload_pending and not is_regular and semester_tables_visible)

    context = {
        'student': student,
        'has_generated_pos': has_generated_pos,
        'semester_tables_visible': semester_tables_visible,
        'pos_reload_banner': pos_reload_banner,
        'pos_config': {
            'is_regular': is_regular,
            'has_generated_pos': has_generated_pos,
            'irreg_configured': irreg_configured if not is_regular else True,
            'pos_reload_pending': bool(pos_reload_pending and not is_regular),
        },
        'saved_grades_by_section': saved_grades_by_section,
        'saved_only': saved_grades_by_section,
        'session_academic_year': request.session.get('academic_year', ''),
        'session_semester': request.session.get('semester', ''),
        'session_year_level': year_level,
        'curriculum_sections': curriculum_sections,
        'curriculum_sections_dropdown': curriculum_sections[:SECTION_INDEX_MAX_DROPDOWN_GENERATE + 1],
        'current_semester_choices_regular': CURRENT_SEMESTER_CHOICES_REGULAR,
        'current_semester_index': next_section_index,
        'preview_sections': preview_sections,
        'display_sections': display_sections,
        'next_section_index': next_section_index,
        'editable_section_index': editable_section_index,
        'selected_section_has_grades': selected_has_grades,
        'picker_available_by_section': picker_available_by_section,
        'is_regular': is_regular,
        'pos_path': pos_path,
        'regular_with_petition': regular_with_petition,
        'irreg_configured': irreg_configured if not is_regular else True,
        'irreg_start_index': irreg_start_index if not is_regular else 0,
        'irreg_current_sem': irreg_current_sem if not is_regular else 0,
        'irreg_start_unset': (irreg_start_index is None) if not is_regular else False,
        'irreg_current_unset': (irreg_current_sem is None) if not is_regular else False,
        'irreg_with_petition': request.session.get('irreg_with_petition', 'yes') if not is_regular else '',
    }
    return render(request, "pos/generate_pos.html", context)


@_require_student
@require_POST
def save_pos_grades(request):
    """Save grades by section. POST body: { "grades_by_section": { "0": {"MATH 101": "5.00"}, "2": {"MATH 101": "2.00"} } }.
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
    return JsonResponse({'ok': True})


@_require_student
@require_POST
def delete_pos_data(request):
    """Delete current POS grades/courses for the logged-in student (keeps History)."""
    student = get_logged_in_student(request)

    StudentGrade.objects.filter(student=student).delete()

    # Reset session selections so Generate/Preview return to "no input" state
    request.session['pos_reset'] = True
    for k in (
        'year_level',
        'semester',
        'irreg_start_index',
        'irreg_current_sem',
        'irreg_with_petition',
    ):
        try:
            request.session.pop(k, None)
        except Exception:
            pass

    # Clear cached solver output so Preview/PDF won't show future semesters again
    # after the user deletes current POS data.
    try:
        request.session.pop('last_generated_pos', None)
        request.session.pop('last_generated_payload', None)
        request.session.pop('last_generated_pos_options', None)
        request.session.pop('pos_reload_pending', None)
        request.session.pop('pos_loaded_from_history', None)
    except Exception:
        pass

    return JsonResponse({'ok': True})


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
    from .pos_pdf import POS_CURRICULUM
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



# Elective course code translation: the solver uses "Elective Course 1/2/3" as its
# internal course keys, while the Django UI uses "CpEE 401/402/403". These maps
# convert between the two representations at the API boundary.
_UI_TO_SOLVER_ELECTIVE = {
    "CpEE 401": "Elective Course 1",
    "CpEE 402": "Elective Course 2",
    "CpEE 403": "Elective Course 3",
}
_SOLVER_TO_UI_ELECTIVE = {v: k for k, v in _UI_TO_SOLVER_ELECTIVE.items()}


@csrf_exempt
def generate_pos_api(request):
    """
    API endpoint: call the CP-SAT solver and return a structured POS.

    Expects JSON body with at least:
      - current_sem (int)
      - passed_courses (list of course codes)
      - failed_courses (list of course codes)
      - with_petition (bool, optional)
      - configuration (str, optional)
      - alternative_algorithm (str|null, optional)

    Response is the solver's result dict:
      - status: "ok" | "probation" | "infeasible"
      - message: optional explanation string
      - pos: list of per-semester dicts when status == "ok"
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"error": "Expected JSON object"}, status=400)

    # Translate UI elective codes (CpEE 401/402/403) → solver course keys
    # (Elective Course 1/2/3) so the solver correctly recognises passed/failed electives.
    raw_passed = data.get("passed_courses") or []
    raw_failed = data.get("failed_courses") or []
    payload = {
        "current_sem": data.get("current_sem"),
        "passed_courses": [_UI_TO_SOLVER_ELECTIVE.get(c, c) for c in raw_passed],
        "failed_courses": [_UI_TO_SOLVER_ELECTIVE.get(c, c) for c in raw_failed],
        "with_petition": data.get("with_petition", False),
    }

    # Defensive validation: avoid null/invalid current_sem crashes.
    try:
        cur_sem = payload.get("current_sem")
        if cur_sem is None or cur_sem == "":
            return JsonResponse({"status": "infeasible", "message": "Please select your current semester to proceed.", "pos": []}, status=400)
        payload["current_sem"] = int(cur_sem)
    except (TypeError, ValueError):
        return JsonResponse({"status": "infeasible", "message": "Invalid semester selection. Please check your dropdowns.", "pos": []}, status=400)
    if "configuration" in data:
        payload["configuration"] = data.get("configuration")
    if "alternative_algorithm" in data:
        payload["alternative_algorithm"] = data.get("alternative_algorithm")

    try:
        result = generate_pos_via_solver(payload)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=500)

    # Translate solver course keys back to UI codes so Django course_lookup,
    # display_code, and PDF generation work correctly.
    def _translate_pos_courses(pos_list):
        if not isinstance(pos_list, list):
            return
        for sem in pos_list:
            for course in (sem.get("courses") or []):
                solver_code = course.get("code", "")
                if solver_code in _SOLVER_TO_UI_ELECTIVE:
                    course["code"] = _SOLVER_TO_UI_ELECTIVE[solver_code]

    if isinstance(result, dict):
        _translate_pos_courses(result.get("pos"))
        pos_options = result.get("pos_options")
        if isinstance(pos_options, dict):
            for _mode, opt in pos_options.items():
                if isinstance(opt, dict) and opt.get("pos") is not None:
                    _translate_pos_courses(opt.get("pos"))

    # If called from the authenticated app UI, cache the last generated solver output in session
    # so we can generate a PDF from it on demand.
    try:
        student = get_logged_in_student(request)
        if student is not None and isinstance(result, dict):
            # Once the user generates a brand-new POS, it is no longer coming from a history-load state.
            request.session.pop('pos_loaded_from_history', None)
            request.session["last_generated_pos"] = result.get("pos") or []
            if isinstance(result.get("pos_options"), dict):
                request.session["last_generated_pos_options"] = result.get("pos_options")
            if result.get("default_petition_mode"):
                request.session["last_selected_petition_mode"] = result.get("default_petition_mode")
            request.session["last_generated_payload"] = payload
    except Exception:
        pass

    return JsonResponse(result)


@csrf_exempt
@require_POST
def set_generated_pos_option(request):
    """
    Set which solver-generated POS option (no-petition vs with-petition) should be used
    for Preview/PDF downloads. This only updates session state; it does not re-run the solver.

    Expected JSON body:
      - mode: "no_petition" | "with_petition"
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Expected JSON object"}, status=400)

    mode = data.get("mode")
    if mode not in ("no_petition", "with_petition"):
        return JsonResponse({"ok": False, "error": "Invalid mode"}, status=400)

    options = request.session.get("last_generated_pos_options")
    if not isinstance(options, dict) or not options:
        return JsonResponse({"ok": False, "error": "No generated POS options found in session."}, status=400)

    opt = options.get(mode)
    if not isinstance(opt, dict):
        return JsonResponse({"ok": False, "error": "Requested mode is not available."}, status=400)

    pos = opt.get("pos") or []
    request.session["last_generated_pos"] = pos
    request.session["last_selected_petition_mode"] = mode
    return JsonResponse({"ok": True, "mode": mode, "pos": pos})


def _rebuild_tuned_pos_and_forced_map(base_pos, pos_overrides, preview_boundary_section_index):
    """
    Apply UI pos_overrides onto the solver-generated base_pos and return:
      - updated_pos: list suitable for session/PDF
      - forced_semester_map: { "<solver_course_code>": <semester_index_int> }
    """
    # Work on a deep copy so validation failures don't mutate cached session options.
    updated_pos = copy.deepcopy(base_pos)

    course_lookup = _course_lookup_from_curriculum()

    def _fallback_course_obj(code):
        if not code:
            return {"code": "", "title": "", "units": ""}
        info = course_lookup.get(code)
        if not info:
            return {"code": code, "title": "", "units": ""}
        title, units, _lec, _lab, _prereq, _coreq = info
        return {"code": code, "title": title, "units": units}

    sem_by_idx = {}
    for sem in updated_pos:
        try:
            si = int(sem.get("semester_index"))
        except (ValueError, TypeError):
            continue
        sem_by_idx[si] = sem

    modified_any = False
    for sem_idx_raw, codes in pos_overrides.items():
        try:
            solver_sem_idx = int(sem_idx_raw)
        except (ValueError, TypeError):
            continue

        sem = sem_by_idx.get(solver_sem_idx)
        if not sem:
            continue

        # Enforce "future/ungraded only" based on preview boundary.
        # In the preview/PDF mapping: ui_section_index = solver_sem_idx - 1
        if preview_boundary_section_index is not None:
            ui_section_index = solver_sem_idx - 1
            if ui_section_index <= preview_boundary_section_index:
                continue

        if not isinstance(codes, list):
            continue

        # Keep existing course objects when possible (preserve any extra fields solver returned).
        code_to_course = {}
        for c in (sem.get("courses") or []):
            if isinstance(c, dict):
                c_code = c.get("code")
                if c_code:
                    code_to_course[str(c_code).strip()] = c

        new_courses = []
        for code in codes:
            code_s = str(code).strip()
            if not code_s:
                continue
            course_obj = code_to_course.get(code_s) or _fallback_course_obj(code_s)
            new_courses.append(course_obj)

        sem["courses"] = new_courses

        # Recompute total units for edited semester(s).
        total_units = 0.0
        for c in new_courses:
            if not isinstance(c, dict):
                continue
            u = c.get("units")
            try:
                total_units += float(u)
            except (ValueError, TypeError):
                code_s = c.get("code")
                if code_s and code_s in course_lookup:
                    total_units += float(course_lookup[code_s][1])

        sem["total_units"] = int(round(total_units))
        modified_any = True

    # Build forced_semester_map for all non-passed courses in tuned schedule.
    forced_semester_map = {}
    for sem in updated_pos:
        try:
            solver_sem_idx = int(sem.get("semester_index"))
        except (ValueError, TypeError):
            continue
        for c in (sem.get("courses") or []):
            if not isinstance(c, dict):
                continue
            code = c.get("code") or ""
            code_s = str(code).strip()
            if not code_s:
                continue
            solver_code = _UI_TO_SOLVER_ELECTIVE.get(code_s, code_s)
            forced_semester_map[solver_code] = solver_sem_idx

    return updated_pos, forced_semester_map, modified_any


@csrf_exempt
@require_POST
def feasible_move_marks(request):
    """
    Compute which movable course rows should get the green feasible-move border.

    Response:
      - ok: bool
      - feasible_courses: [<UI course code strings>] (when request has no `course_code`)
      - feasible_targets: [<solver semester indices>] (when request has `course_code`)
      - debug: optional details when ok=false
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Expected JSON object"}, status=400)

    mode = data.get("mode")
    if mode not in ("no_petition", "with_petition"):
        return JsonResponse({"ok": False, "error": "Invalid mode"}, status=400)

    pos_overrides = data.get("pos_overrides") or {}
    if not isinstance(pos_overrides, dict):
        return JsonResponse({"ok": False, "error": "pos_overrides must be an object"}, status=400)

    course_code_ui = data.get("course_code")
    if course_code_ui is not None:
        course_code_ui = str(course_code_ui).strip()
        if course_code_ui == "":
            course_code_ui = None

    options = request.session.get("last_generated_pos_options")
    if not isinstance(options, dict) or not options:
        return JsonResponse({"ok": False, "error": "No generated POS options found in session."}, status=400)

    opt = options.get(mode)
    if not isinstance(opt, dict):
        return JsonResponse({"ok": False, "error": "Requested mode is not available."}, status=400)

    base_pos = opt.get("pos") or []
    if not isinstance(base_pos, list):
        return JsonResponse({"ok": False, "error": "Base POS is not a list."}, status=400)

    base_payload = request.session.get("last_generated_payload") or {}
    if not isinstance(base_payload, dict) or not base_payload.get("current_sem"):
        return JsonResponse({"ok": False, "error": "No solver payload found in session."}, status=400)

    preview_boundary_section_index = _year_semester_to_section_index(
        request.session.get("year_level"),
        request.session.get("semester"),
    )

    updated_pos, forced_semester_map_base, _modified_any = _rebuild_tuned_pos_and_forced_map(
        base_pos=base_pos,
        pos_overrides=pos_overrides,
        preview_boundary_section_index=preview_boundary_section_index,
    )

    def _ui_to_solver_code(code_ui: str) -> str:
        return _UI_TO_SOLVER_ELECTIVE.get(code_ui, code_ui)

    # Build semester/course maps from the tuned schedule currently shown in the DOM.
    sem_by_idx_tuned = {}
    sem_courses_ui = {}
    course_units = {}
    course_to_sem_ui = {}

    for sem in (updated_pos or []):
        try:
            si = int(sem.get("semester_index"))
        except (ValueError, TypeError):
            continue

        sem_by_idx_tuned[si] = sem
        sem_courses_ui.setdefault(si, set())

        for c in (sem.get("courses") or []):
            if not isinstance(c, dict):
                continue
            code_ui = str(c.get("code") or "").strip()
            if not code_ui:
                continue
            sem_courses_ui[si].add(code_ui)
            course_to_sem_ui[code_ui] = si
            try:
                course_units[code_ui] = float(c.get("units", 0) or 0)
            except (ValueError, TypeError):
                course_units[code_ui] = 0.0

    # Editable semester indices are the semesters allowed to drag (UI sends pos_overrides keys).
    editable_sem_indices = []
    candidate_codes = set()
    for sem_idx_raw, codes in (pos_overrides or {}).items():
        try:
            si = int(sem_idx_raw)
        except (ValueError, TypeError):
            continue
        if si not in sem_by_idx_tuned:
            continue
        editable_sem_indices.append(si)
        if isinstance(codes, list):
            for code in codes:
                code_ui = str(code or "").strip()
                if code_ui:
                    candidate_codes.add(code_ui)

    editable_sem_indices = sorted(set(editable_sem_indices))
    if not editable_sem_indices:
        # Nothing is draggable in the current DOM state.
        if course_code_ui:
            return JsonResponse({"ok": True, "feasible_targets": []})
        return JsonResponse({"ok": True, "feasible_courses": []})

    tuned_sem_map_ui = dict(course_to_sem_ui)
    tuned_sem_map_solver = {
        _ui_to_solver_code(code_ui): si for code_ui, si in tuned_sem_map_ui.items()
    }

    # Base passed courses are already solver-side keys in the stored payload.
    passed_courses_solver = []
    try:
        passed_courses_solver = list(base_payload.get("passed_courses") or [])
    except Exception:
        passed_courses_solver = []

    with_petition = mode == "with_petition"
    solver_payload = dict(base_payload)

    # Memoize solver calls within this request.
    feasibility_memo = {}

    def _is_solver_feasible_dest(solver_code: str, dest_si: int) -> bool:
        key = (solver_code, int(dest_si))
        if key in feasibility_memo:
            return feasibility_memo[key]

        forced_map = dict(forced_semester_map_base or {})
        forced_map[solver_code] = int(dest_si)

        result = validate_pos_via_solver(
            payload=solver_payload,
            with_petition=with_petition,
            forced_semester_map=forced_map,
        )
        ok = (result.get("status") == "ok")
        feasibility_memo[key] = bool(ok)
        return feasibility_memo[key]

    if course_code_ui:
        src_si = course_to_sem_ui.get(course_code_ui)
        if src_si is None:
            return JsonResponse({"ok": True, "feasible_targets": []})

        solver_code = _ui_to_solver_code(course_code_ui)
        feasible_targets = set()

        for dest_si in editable_sem_indices:
            if dest_si == src_si:
                continue
            if course_code_ui in sem_courses_ui.get(dest_si, set()):
                continue

            if _is_solver_feasible_dest(solver_code, dest_si):
                feasible_targets.add(int(dest_si))

        return JsonResponse({"ok": True, "feasible_targets": sorted(feasible_targets)})

    # Default mode: compute feasible row highlights for all candidate courses.
    feasible_courses = set()

    if not candidate_codes:
        return JsonResponse({"ok": True, "feasible_courses": []})

    for code_ui in candidate_codes:
        src_si = course_to_sem_ui.get(code_ui)
        if src_si is None:
            continue
        solver_code = _ui_to_solver_code(code_ui)

        # Try destinations in ascending solver-semester index; first feasible wins.
        for dest_si in editable_sem_indices:
            if dest_si == src_si:
                continue
            if code_ui in sem_courses_ui.get(dest_si, set()):
                continue

            if _is_solver_feasible_dest(solver_code, dest_si):
                feasible_courses.add(code_ui)
                break

    return JsonResponse({"ok": True, "feasible_courses": sorted(feasible_courses)})


@csrf_exempt
@require_POST
def validate_tuned_pos(request):
    """
    Validate the user-edited tuned POS (manual fine-tuning) for feasibility using CP-SAT.

    Expected JSON body:
      - mode: "no_petition" | "with_petition"
      - pos_overrides: { "<solver_semester_index>": ["COURSE_CODE", ...], ... }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Expected JSON object"}, status=400)

    mode = data.get("mode")
    if mode not in ("no_petition", "with_petition"):
        return JsonResponse({"ok": False, "error": "Invalid mode"}, status=400)

    pos_overrides = data.get("pos_overrides") or {}
    if not isinstance(pos_overrides, dict):
        return JsonResponse({"ok": False, "error": "pos_overrides must be an object"}, status=400)

    options = request.session.get("last_generated_pos_options")
    if not isinstance(options, dict) or not options:
        return JsonResponse({"ok": False, "error": "No generated POS options found in session."}, status=400)

    opt = options.get(mode)
    if not isinstance(opt, dict):
        return JsonResponse({"ok": False, "error": "Requested mode is not available."}, status=400)

    base_pos = opt.get("pos") or []
    if not isinstance(base_pos, list):
        return JsonResponse({"ok": False, "error": "Base POS is not a list."}, status=400)

    base_payload = request.session.get("last_generated_payload") or {}
    if not isinstance(base_payload, dict) or not base_payload.get("current_sem"):
        return JsonResponse({"ok": False, "error": "No solver payload found in session."}, status=400)

    preview_boundary_section_index = _year_semester_to_section_index(
        request.session.get("year_level"),
        request.session.get("semester"),
    )

    updated_pos, forced_semester_map, _modified_any = _rebuild_tuned_pos_and_forced_map(
        base_pos=base_pos,
        pos_overrides=pos_overrides,
        preview_boundary_section_index=preview_boundary_section_index,
    )

    with_petition = mode == "with_petition"
    solver_payload = dict(base_payload)
    # Ensure the solver matches the requested mode.
    solver_payload["with_petition"] = bool(with_petition)

    result = validate_pos_via_solver(
        payload=solver_payload,
        with_petition=with_petition,
        forced_semester_map=forced_semester_map,
    )

    status = result.get("status")
    if status == "ok":
        return JsonResponse({"ok": True})

    # Treat probation/infeasible as invalid for UI editing.
    # Provide best-effort diagnostics explaining HOW moved courses likely made it infeasible.
    message = result.get("message") or "Schedule is infeasible."

    MAX_MOVED_COURSES = 8

    # Prefer solver-provided cap function to avoid mismatches.
    solver_meta = get_solver_metadata() or {}
    max_units_for_sem_fn = solver_meta.get("max_units_for_sem")

    def _max_units_for_sem(sem_index: int) -> int:
        try:
            if callable(max_units_for_sem_fn):
                return int(max_units_for_sem_fn(int(sem_index)))
        except Exception:
            pass

        # Fallback caps (should match solver-side units_and_year_standings).
        MAX_UNITS_BY_YEAR_TERM = {
            (1, 1): 24,
            (1, 2): 26,
            (1, 3): 9,
            (2, 1): 27,
            (2, 2): 23,
            (2, 3): 9,
            (3, 1): 21,
            (3, 2): 23,
            (3, 3): 5,
            (4, 1): 19,
            (4, 2): 21,
            (4, 3): 9,
            (5, 1): 21,
            (5, 2): 23,
            (5, 3): 9,
            (6, 1): 21,
            (6, 2): 23,
            (6, 3): 9,
        }

        year_level = (int(sem_index) - 1) // 3 + 1
        term_in_year = ((int(sem_index) - 1) % 3) + 1
        return MAX_UNITS_BY_YEAR_TERM.get((year_level, term_in_year), 0)

    def _build_sem_map(pos_list):
        out = {}
        for sem in (pos_list or []):
            try:
                si = int(sem.get("semester_index"))
            except (ValueError, TypeError):
                continue
            for c in (sem.get("courses") or []):
                if isinstance(c, dict):
                    code = c.get("code") or ""
                    code_s = str(code).strip()
                    if code_s:
                        out[code_s] = si
        return out

    moved_input_codes = set()
    for _sem_idx_raw, codes in (pos_overrides or {}).items():
        if not isinstance(codes, list):
            continue
        for code in codes:
            code_s = str(code).strip()
            if code_s:
                moved_input_codes.add(code_s)

    base_sem_map_ui = _build_sem_map(base_pos)
    tuned_sem_map_ui = _build_sem_map(updated_pos)
    sem_by_idx_tuned = {}
    for sem in (updated_pos or []):
        try:
            si = int(sem.get("semester_index"))
        except (ValueError, TypeError):
            continue
        sem_by_idx_tuned[si] = sem

    # Solver-side course metadata for category/orig_sem and prerequisites.
    solver_courses = solver_meta.get("courses") if isinstance(solver_meta.get("courses"), dict) else {}
    YEAR_STANDING_RULES = solver_meta.get("year_standing_rules") if isinstance(solver_meta.get("year_standing_rules"), dict) else {}

    metadata_loaded = bool(solver_courses)

    # Solver-semester map keyed by solver course codes (not UI codes)
    def _ui_to_solver_code(code_ui: str) -> str:
        return _UI_TO_SOLVER_ELECTIVE.get(code_ui, code_ui)

    tuned_sem_map_solver = {
        _ui_to_solver_code(code_ui): si for code_ui, si in tuned_sem_map_ui.items()
    }
    base_sem_map_solver = {
        _ui_to_solver_code(code_ui): si for code_ui, si in base_sem_map_ui.items()
    }

    passed_courses_solver = []
    try:
        passed_courses_solver = list(base_payload.get("passed_courses") or [])
    except Exception:
        passed_courses_solver = []

    moved_courses_details = []

    def _first_semester_from_solver_info(info: dict):
        if not isinstance(info, dict):
            return None
        if info.get("orig_sem") is not None:
            try:
                return int(info.get("orig_sem"))
            except (ValueError, TypeError):
                return None
        offered = info.get("offered_sems")
        if offered is not None:
            if isinstance(offered, (list, tuple)):
                try:
                    vals = [int(x) for x in offered if x is not None]
                    return min(vals) if vals else None
                except Exception:
                    return None
            try:
                return int(offered)
            except (ValueError, TypeError):
                return None
        return None

    for code_ui in moved_input_codes:
        base_si = base_sem_map_ui.get(code_ui)
        tuned_si = tuned_sem_map_ui.get(code_ui)
        if not base_si or not tuned_si or base_si == tuned_si:
            continue

        solver_code = _ui_to_solver_code(code_ui)
        violations = []

        VIOLATION_PRIORITY = {
            "unit_cap": 0,
            "offering_window": 1,
            "prereq": 2,
            "co_req": 3,
            "year_standing": 4,
            "unknown": 99,
        }

        def _add_violation(vtype: str, label: str, evidence: dict):
            violations.append(
                {
                    "type": vtype,
                    "label": label,
                    "evidence": evidence or {},
                }
            )

        # 1) Unit cap hint for destination semester
        tuned_sem = sem_by_idx_tuned.get(tuned_si)
        total_units_dest = 0
        if isinstance(tuned_sem, dict):
            try:
                total_units_dest = float(tuned_sem.get("total_units", 0) or 0)
            except (ValueError, TypeError):
                total_units_dest = 0
        cap = _max_units_for_sem(tuned_si)
        if cap and total_units_dest > cap:
            _add_violation(
                "unit_cap",
                "Unit cap",
                {
                    "semester_index": tuned_si,
                    "total_units": total_units_dest,
                    "cap": cap,
                },
            )

        # 2) Offering-window / term-type hint using solver category + orig_sem
        cinfo = solver_courses.get(solver_code) if isinstance(solver_courses, dict) else None
        if cinfo:
            category = cinfo.get("category", "Rigid")
            orig_sem = _first_semester_from_solver_info(cinfo)
            if orig_sem:
                sem_type = tuned_si % 3
                orig_type = int(orig_sem) % 3

                if category == "Rigid":
                    if sem_type != orig_type:
                        _add_violation(
                            "offering_window",
                            "Offering window",
                            {
                                "course_category": category,
                                "orig_semester_index": orig_sem,
                                "orig_term_type": orig_type,
                                "destination_term_type": sem_type,
                                "destination_semester_index": tuned_si,
                                "with_petition": bool(mode == "with_petition"),
                            },
                        )
                elif category == "Flexible":
                    allowed = (
                        (orig_type in [1, 2] and sem_type in [1, 2])
                        or (orig_type == 0 and sem_type == 0)
                    )
                    if not allowed:
                        _add_violation(
                            "offering_window",
                            "Offering window",
                            {
                                "course_category": category,
                                "orig_semester_index": orig_sem,
                                "orig_term_type": orig_type,
                                "destination_term_type": sem_type,
                                "destination_semester_index": tuned_si,
                                "with_petition": bool(mode == "with_petition"),
                            },
                        )
                elif category == "Bottleneck":
                    if not bool(mode == "with_petition") and sem_type != orig_type:
                        _add_violation(
                            "offering_window",
                            "Offering window",
                            {
                                "course_category": category,
                                "orig_semester_index": orig_sem,
                                "orig_term_type": orig_type,
                                "destination_term_type": sem_type,
                                "destination_semester_index": tuned_si,
                                "with_petition": bool(mode == "with_petition"),
                            },
                        )

        # 3) Prerequisite & co-requisite ordering hints
        if cinfo:
            pre_list = cinfo.get("pre-requisites") or []
            for pre in pre_list:
                if not isinstance(pre, str):
                    continue
                if pre in YEAR_STANDING_RULES:
                    continue
                if pre in tuned_sem_map_solver:
                    if tuned_si <= tuned_sem_map_solver.get(pre, 0):
                        _add_violation(
                            "prereq",
                            "Prerequisite ordering",
                            {
                                "prereq_course": pre,
                                "prereq_semester_index": tuned_sem_map_solver.get(pre, 0),
                                "destination_semester_index": tuned_si,
                            },
                        )

            co_list = cinfo.get("co-requisites") or []
            for co in co_list:
                if not isinstance(co, str):
                    continue
                if co in tuned_sem_map_solver:
                    # solver: semester[c] >= semester[coreq]
                    if tuned_si < tuned_sem_map_solver.get(co, 0):
                        _add_violation(
                            "co_req",
                            "Co-requisite ordering",
                            {
                                "coreq_course": co,
                                "coreq_semester_index": tuned_sem_map_solver.get(co, 0),
                                "destination_semester_index": tuned_si,
                            },
                        )

            # 4) Year-standing hint (if course has any year-standing prerequisite token)
            for pre in pre_list:
                if not isinstance(pre, str):
                    continue
                if pre not in YEAR_STANDING_RULES:
                    continue

                orig_sem_c = _first_semester_from_solver_info(cinfo)
                if not orig_sem_c:
                    continue

                prefix_codes = []
                total_units = 0.0
                earned_fixed = 0.0
                for pc, pinfo in solver_courses.items():
                    p_first = _first_semester_from_solver_info(pinfo)
                    if p_first is None or p_first >= orig_sem_c:
                        continue
                    units = float(pinfo.get("units", 0) or 0)
                    prefix_codes.append((pc, units))
                    total_units += units
                    if pc in passed_courses_solver:
                        earned_fixed += units

                if total_units <= 0:
                    continue

                req_units = int(math.ceil(0.9 * total_units))
                remaining_needed = req_units - earned_fixed
                if remaining_needed <= 0:
                    break

                scheduled_before_units = 0.0
                for pc, units in prefix_codes:
                    if pc in passed_courses_solver:
                        continue
                    pc_sem = tuned_sem_map_solver.get(pc)
                    if pc_sem is None:
                        continue
                    if pc_sem < tuned_si:
                        scheduled_before_units += units

                if scheduled_before_units < remaining_needed:
                    _add_violation(
                        "year_standing",
                        "Year standing",
                        {
                            "course_prefix_requirement": "90% prefix-units",
                            "required_prefix_units": req_units,
                            "earned_fixed_prefix_units": earned_fixed,
                            "remaining_needed_units": remaining_needed,
                            "scheduled_before_units": scheduled_before_units,
                            "destination_semester_index": tuned_si,
                        },
                    )
                break

        if not violations:
            _add_violation(
                "unknown",
                "Constraint conflict",
                {
                    "destination_semester_index": tuned_si,
                    "reason_detail": (
                        "Could not compute typed reasons (unit-cap/offering/prereq/co-req/year-standing)."
                        + (" Solver metadata missing." if not metadata_loaded else "")
                    ),
                },
            )

        # Determine primary violation type using a fixed priority order.
        primary_violation_type = sorted(
            violations,
            key=lambda v: VIOLATION_PRIORITY.get(v.get("type"), 999),
        )[0].get("type")

        # Truncate violations for readability/perf (keep best priorities).
        violations = sorted(
            violations,
            key=lambda v: VIOLATION_PRIORITY.get(v.get("type"), 999),
        )[:3]

        moved_courses_details.append(
            {
                "code": code_ui,
                "from_semester_index": base_si,
                "to_semester_index": tuned_si,
                "primary_violation_type": primary_violation_type,
                "violations": violations,
            }
        )

    # Cap/truncate for performance + readability.
    moved_courses_details = moved_courses_details[:MAX_MOVED_COURSES]

    # Heuristic likely-culprits: top moved courses by hint count.
    likely_culprits = [
        mc.get("code")
        for mc in sorted(
            moved_courses_details,
            key=lambda x: len(x.get("violations") or []),
            reverse=True,
        )[:3]
    ]

    return JsonResponse(
        {
            "ok": False,
            "status": status,
            "message": message,
            "moved_courses": moved_courses_details,
            "likely_culprits": likely_culprits,
        }
    )


@csrf_exempt
@require_POST
def save_tuned_pos(request):
    """
    Persist POS course order overrides from the Preview page edit mode.

    Expected JSON body:
      - mode: "no_petition" | "with_petition"
      - pos_overrides: { "<solver_semester_index>": ["COURSE_CODE", ...], ... }

    This updates request.session['last_generated_pos'] so Download PDF uses the tuned schedule.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Expected JSON object"}, status=400)

    mode = data.get("mode")
    if mode not in ("no_petition", "with_petition"):
        return JsonResponse({"ok": False, "error": "Invalid mode"}, status=400)

    pos_overrides = data.get("pos_overrides") or {}
    if not isinstance(pos_overrides, dict):
        return JsonResponse({"ok": False, "error": "pos_overrides must be an object"}, status=400)

    options = request.session.get("last_generated_pos_options")
    if not isinstance(options, dict) or not options:
        return JsonResponse({"ok": False, "error": "No generated POS options found in session."}, status=400)

    opt = options.get(mode)
    if not isinstance(opt, dict):
        return JsonResponse({"ok": False, "error": "Requested mode is not available."}, status=400)

    base_pos = opt.get("pos") or []
    if not isinstance(base_pos, list):
        return JsonResponse({"ok": False, "error": "Base POS is not a list."}, status=400)

    # Allow editing only "future/ungraded" solver semesters (past semesters should keep saved grades).
    preview_boundary_section_index = _year_semester_to_section_index(
        request.session.get("year_level"),
        request.session.get("semester"),
    )

    updated_pos, forced_semester_map, modified_any = _rebuild_tuned_pos_and_forced_map(
        base_pos=base_pos,
        pos_overrides=pos_overrides,
        preview_boundary_section_index=preview_boundary_section_index,
    )

    base_payload = request.session.get("last_generated_payload") or {}
    with_petition = mode == "with_petition"
    solver_payload = dict(base_payload)
    solver_payload["with_petition"] = bool(with_petition)

    # Defense-in-depth: reject persistence if CP-SAT says infeasible.
    result = validate_pos_via_solver(
        payload=solver_payload,
        with_petition=with_petition,
        forced_semester_map=forced_semester_map,
    )
    status = result.get("status")
    if status != "ok":
        return JsonResponse(
            {
                "ok": False,
                "status": status,
                "message": result.get("message") or "Schedule is infeasible.",
            }
        )

    if modified_any:
        request.session["last_generated_pos"] = updated_pos
        request.session["last_selected_petition_mode"] = mode

    return JsonResponse({"ok": True, "mode": mode, "pos": updated_pos})


MAX_POS_RECORDS_PER_USER = 50


def _prune_old_pos_records(student):
    """Delete oldest POSRecord for this student if over MAX_POS_RECORDS_PER_USER."""
    ids_to_keep = (
        POSRecord.objects.filter(student=student)
        .order_by('-generated_at')
        .values_list('pk', flat=True)[:MAX_POS_RECORDS_PER_USER]
    )
    POSRecord.objects.filter(student=student).exclude(pk__in=ids_to_keep).delete()


def _grades_dict_string_keys(grades_by_section):
    """Normalize grades_by_section for JSON storage (string keys, section ints as str)."""
    out = {}
    for k, v in (grades_by_section or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            sk = str(int(k))
        except (ValueError, TypeError):
            sk = str(k)
        out[sk] = {str(c).strip(): str(g).strip() for c, g in v.items() if c and g}
    return out


def _max_ui_section_from_grades_and_pos(grades_by_section, generated_pos):
    """Largest curriculum section index (0-based) referenced by saved grades or solver POS."""
    max_si = -1
    for k in (grades_by_section or {}):
        try:
            max_si = max(max_si, int(k))
        except (ValueError, TypeError):
            try:
                max_si = max(max_si, int(str(k)))
            except (ValueError, TypeError):
                pass
    for sem in (generated_pos or []):
        if not isinstance(sem, dict):
            continue
        try:
            sidx = int(sem.get("semester_index"))
            sem_courses = sem.get("courses") or []
            has_courses = False
            if isinstance(sem_courses, list):
                for c in sem_courses:
                    if not isinstance(c, dict):
                        continue
                    if (c.get("code") or "").strip():
                        has_courses = True
                        break
            if has_courses:
                max_si = max(max_si, sidx - 1)
        except (ValueError, TypeError):
            pass
    return max(0, max_si)


def _snapshot_for_pos_history(request, student):
    """Build JSON-serializable preview snapshot from session + DB grades."""
    grades = _saved_grades_by_section(student)
    grades_snap = _grades_dict_string_keys(grades)
    pos_options = request.session.get("last_generated_pos_options")
    if not isinstance(pos_options, dict):
        pos_options = {}
    mode = (request.GET.get("petition_mode") or "").strip()
    if mode not in ("no_petition", "with_petition"):
        mode = request.session.get("last_selected_petition_mode") or "no_petition"
    if mode not in ("no_petition", "with_petition"):
        mode = "no_petition"
    # IMPORTANT:
    # Use the *current edited/tuned* schedule stored in session.
    # `last_generated_pos_options` holds only the base solver schedules
    # (un-edited). Users drag/edit in Preview and we must reflect that.
    generated_pos = copy.deepcopy(request.session.get("last_generated_pos") or [])
    base_payload = copy.deepcopy(request.session.get("last_generated_payload") or {})
    if not isinstance(base_payload, dict):
        base_payload = {}
    base_payload["with_petition"] = mode == "with_petition"
    boundary_param = request.GET.get("boundary_ui_index")
    boundary = None
    try:
        if boundary_param is not None and str(boundary_param).strip() != "":
            boundary = int(boundary_param)
    except (TypeError, ValueError):
        boundary = None
    if boundary is None:
        boundary = _year_semester_to_section_index(
            request.session.get("year_level"),
            request.session.get("semester"),
        )
    irreg_si = request.session.get("irreg_start_index")
    return {
        "pos_options": copy.deepcopy(pos_options),
        "generated_pos": generated_pos,
        "petition_mode": mode,
        "last_generated_payload": base_payload,
        "grades_by_section": grades_snap,
        "preview_boundary_section_index": boundary,
        "irreg_start_index": irreg_si,
        "irreg_with_petition": request.session.get("irreg_with_petition", "yes"),
    }


def _persist_pos_history_on_download(request, student):
    """Append a history row when the user downloads Preview PDF (selected petition variant)."""
    snap = _snapshot_for_pos_history(request, student)
    request.session["last_generated_pos"] = snap["generated_pos"]
    request.session["last_selected_petition_mode"] = snap["petition_mode"]
    request.session["last_generated_payload"] = snap["last_generated_payload"]
    yl = request.session.get("year_level") or ""
    sem = request.session.get("semester") or ""
    POSRecord.objects.create(
        student=student,
        academic_year=request.session.get("academic_year") or "",
        semester=sem,
        year_level=yl,
        grades_snapshot=snap["grades_by_section"],
        preview_snapshot=snap,
    )
    _prune_old_pos_records(student)


def _apply_grades_snapshot_to_student(student, grades_by_section):
    """Replace StudentGrade rows from a {section_index: {code: grade}} snapshot."""
    StudentGrade.objects.filter(student=student).delete()
    if not isinstance(grades_by_section, dict):
        return
    for sk, sec in grades_by_section.items():
        try:
            si = int(sk)
        except (ValueError, TypeError):
            continue
        if not isinstance(sec, dict):
            continue
        for course_code, grade in sec.items():
            if course_code and grade:
                StudentGrade.objects.create(
                    student=student,
                    course_code=str(course_code).strip(),
                    grade=str(grade).strip(),
                    section_index=si,
                )


@_require_student
def pos_preview(request):
    """Preview POS page - Read-only POS table matching PDF layout; each section shows that section's grades (same course can differ per section)."""
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    grades_by_section = _saved_grades_by_section(student)
    preview_sections = _preview_sections_from_curriculum(grades_by_section)
    # For solver-generated future semesters we only get course codes back.
    # Provide a lookup so the preview tables can still show lec/lab/pre/core fields.
    course_lookup_tuple = _course_lookup_from_curriculum()
    course_lookup = {
        code: {
            "title": info[0],
            "units": info[1],
            "lec": info[2],
            "lab": info[3],
            "prereq": info[4],
            "coreq": info[5],
        }
        for code, info in course_lookup_tuple.items()
    }
    context = {
        'student': student,
        'preview_sections': preview_sections,
        'course_lookup': course_lookup,
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
    # Determine currently selected petition mode (passed from Preview Download click).
    mode = (request.GET.get("petition_mode") or "").strip()
    if mode not in ("no_petition", "with_petition"):
        mode = request.session.get("last_selected_petition_mode") or "no_petition"
    request.session["last_selected_petition_mode"] = mode

    grades_by_section = _saved_grades_by_section(student)
    academic_year = request.session.get('academic_year', '') or student.academic_year or ''

    # Preview cut-off determines where grades stop (and where solver future semesters begin).
    boundary_param = request.GET.get("boundary_ui_index")
    preview_boundary_section_index = None
    try:
        if boundary_param is not None and str(boundary_param).strip() != "":
            preview_boundary_section_index = int(boundary_param)
    except (TypeError, ValueError):
        preview_boundary_section_index = None

    if preview_boundary_section_index is None:
        preview_boundary_section_index = _year_semester_to_section_index(
            request.session.get('year_level'),
            request.session.get('semester'),
        )

    # Match Preview exactly: past/current semesters come from `preview_sections`,
    # while future semesters are appended from the last solver-generated POS.
    preview_sections = _preview_sections_from_curriculum(grades_by_section)
    generated_pos = request.session.get("last_generated_pos") or []

    from .pos_pdf import build_preview_pos_pdf
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None
    academic_year_safe = (academic_year or '').replace("–", "-").replace(" ", "_")
    sr_code_safe = student.sr_code or 'SR'
    pdf_bytes = build_preview_pos_pdf(
        student_name=student.full_name or '',
        sr_code=student.sr_code or '',
        academic_year=academic_year,
        boundary_section_index=preview_boundary_section_index,
        preview_sections=preview_sections,
        future_pos=generated_pos,
        logo_path=logo_path,
    )
    # Keep filename format requested: `academic_year sr code`.
    filename = f"{academic_year_safe} {sr_code_safe}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@csrf_exempt
@_require_student
@require_POST
def save_preview_pos_to_history(request):
    """
    Save the currently edited Preview POS state into POS history.

    The request must include:
      - `petition_mode` (no_petition | with_petition) as a GET query param
      - `boundary_ui_index` as a GET query param (0-based index used by Preview)
    """
    student = get_logged_in_student(request)
    if student is None:
        return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)

    # Save only when the user has the needed configuration.
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return JsonResponse({"ok": False, "error": "Missing academic term/profile photo"}, status=400)

    snap = _snapshot_for_pos_history(request, student)

    # Capture the exact semester course tables the user sees on the Preview page
    # (including ungraded rows), so Load -> Generate POS can reproduce it.
    try:
        body_raw = request.body.decode("utf-8") if request.body else ""
        body = json.loads(body_raw) if body_raw.strip() else {}
    except Exception:
        body = {}
    preview_ui_sections = body.get("preview_ui_sections") or body.get("preview_sections") or {}
    if isinstance(preview_ui_sections, dict):
        # Keys should be UI section indices; JSON forces them to be strings.
        snap["preview_ui_sections"] = preview_ui_sections

    last = POSRecord.objects.filter(student=student).order_by('-generated_at').first()
    if last is not None and last.preview_snapshot == snap:
        return JsonResponse({"ok": True, "saved": False, "record_id": last.pk})

    yl = request.session.get('year_level') or ''
    sem = request.session.get('semester') or ''
    record = POSRecord.objects.create(
        student=student,
        academic_year=request.session.get('academic_year') or '',
        semester=sem,
        year_level=yl,
        grades_snapshot=snap.get("grades_by_section") or {},
        preview_snapshot=snap,
    )
    _prune_old_pos_records(student)

    return JsonResponse({"ok": True, "saved": True, "record_id": record.pk})


@_require_student
def download_generated_pos_pdf(request):
    """
    Download a PDF built from the last solver-generated POS (stored in session).

    This is distinct from `download_pos_pdf` (which is based on grades_by_section).
    """
    if not _has_academic_term(request) or not _has_profile_photo(request):
        return redirect(reverse('pos_home') + '?required=1')
    student = get_logged_in_student(request)
    academic_year = request.session.get('academic_year', '') or getattr(student, 'academic_year', '') or ''
    generated_pos = request.session.get("last_generated_pos") or []
    if not isinstance(generated_pos, list) or not generated_pos:
        return HttpResponseBadRequest("No generated POS found. Please generate a POS first.")

    # Use the current preview selection (year_level + semester) as the cut-off:
    # - For semesters up to this point: show grade + remarks from saved grades.
    # - For future semesters: show course metadata only (lec/lab/pre/co), with blank grade/remarks.
    preview_boundary_section_index = _year_semester_to_section_index(
        request.session.get('year_level'),
        request.session.get('semester'),
    )
    grades_by_section = _saved_grades_by_section(student)

    from .pos_pdf import build_generated_pos_pdf
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None
    pdf_bytes = build_generated_pos_pdf(
        student_name=student.full_name or '',
        sr_code=student.sr_code or '',
        academic_year=academic_year,
        pos=generated_pos,
        grades_by_section=grades_by_section,
        boundary_section_index=preview_boundary_section_index,
        logo_path=logo_path,
    )
    filename = f"POS_Generated_{student.sr_code or 'POS'}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@_require_student
def download_pos_pdf_record(request, record_id):
    """Download the POS PDF for a specific history record (uses that record's saved grades snapshot)."""
    student = get_logged_in_student(request)
    record = get_object_or_404(POSRecord, pk=record_id, student=student)
    snap = getattr(record, "preview_snapshot", None) or {}
    eval_date = record.generated_at.date()
    academic_year = record.academic_year or ''
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None

    if isinstance(snap, dict) and snap.get("grades_by_section"):
        grades_raw = snap.get("grades_by_section") or {}
        grades_by_section = {}
        for k, v in grades_raw.items():
            if not isinstance(v, dict):
                continue
            try:
                grades_by_section[int(k)] = dict(v)
            except (ValueError, TypeError):
                continue
        generated_pos = snap.get("generated_pos") if isinstance(snap.get("generated_pos"), list) else []
        boundary = snap.get("preview_boundary_section_index")
        preview_sections = _preview_sections_from_curriculum(grades_by_section)
        from .pos_pdf import build_preview_pos_pdf
        pdf_bytes = build_preview_pos_pdf(
            student_name=student.full_name or '',
            sr_code=student.sr_code or '',
            academic_year=academic_year,
            boundary_section_index=boundary,
            preview_sections=preview_sections,
            future_pos=generated_pos,
            logo_path=logo_path,
        )
    else:
        snapshot = getattr(record, 'grades_snapshot', None) or {}
        if isinstance(snapshot, dict) and snapshot and not any(isinstance(v, dict) for v in snapshot.values()):
            grades_by_section = {'0': snapshot}
        else:
            grades_by_section = snapshot
        from .pos_pdf import build_pos_pdf
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
def load_pos_record(request, record_id):
    """Restore a history record into Generate POS (full grades + preview POS); user must re-select Last Semester Enrolled."""
    student = get_logged_in_student(request)
    record = get_object_or_404(POSRecord, pk=record_id, student=student)
    snap = record.preview_snapshot if isinstance(record.preview_snapshot, dict) else {}
    grades_src = snap.get("grades_by_section") if snap else None
    if not isinstance(grades_src, dict) or not grades_src:
        grades_src = record.grades_snapshot or {}
    if isinstance(grades_src, dict) and grades_src and not any(isinstance(v, dict) for v in grades_src.values()):
        grades_src = {"0": grades_src}
    _apply_grades_snapshot_to_student(student, grades_src)

    if snap:
        request.session["last_generated_pos_options"] = copy.deepcopy(snap.get("pos_options") or {})
        request.session["last_generated_pos"] = copy.deepcopy(snap.get("generated_pos") or [])
        request.session["last_selected_petition_mode"] = snap.get("petition_mode") or "no_petition"
        lp = snap.get("last_generated_payload")
        if isinstance(lp, dict) and lp:
            request.session["last_generated_payload"] = copy.deepcopy(lp)
        irr = snap.get("irreg_start_index")
        if irr is not None:
            try:
                request.session["irreg_start_index"] = int(irr)
            except (TypeError, ValueError):
                request.session.pop("irreg_start_index", None)
        else:
            request.session.pop("irreg_start_index", None)
        iw = snap.get("irreg_with_petition")
        if iw in ("yes", "no"):
            request.session["irreg_with_petition"] = iw

        pu = snap.get("preview_ui_sections")
        if isinstance(pu, dict) and pu:
            request.session["pos_loaded_preview_ui_sections"] = copy.deepcopy(pu)
        else:
            request.session.pop("pos_loaded_preview_ui_sections", None)
    else:
        request.session.pop("last_generated_pos_options", None)
        request.session["last_generated_pos"] = []
        request.session.pop("last_generated_payload", None)
        request.session.pop("pos_loaded_preview_ui_sections", None)

    request.session.pop("irreg_current_sem", None)
    request.session.pop("year_level", None)
    request.session.pop("semester", None)
    request.session["pos_reload_pending"] = True
    request.session["pos_loaded_from_history"] = True
    request.session.pop("pos_reset", None)
    return redirect("pos_generate")


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


@_require_student
@require_POST
def delete_all_pos_records(request):
    """Delete ALL POS history records for the logged-in student (keeps current grades)."""
    student = get_logged_in_student(request)
    POSRecord.objects.filter(student=student).delete()
    return redirect('pos_history')


def pos_about(request):
    """About page - Thesis information and developer names"""
    return render(request, "pos/about.html")


def sign_out(request):
    """Clear session and redirect to landing page."""
    request.session.flush()
    return redirect('login')


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
