"""
Views for POS templates and auth (register, login, session).
"""
import json
import re
import os
import copy
import math
import hashlib
import logging
from collections import OrderedDict
from pathlib import Path
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.db.utils import OperationalError, ProgrammingError
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt

from django.utils import timezone
from accounts.models import Student, StudentGrade, POSRecord, SignatoryOption, SignatoryPreference
from .models import CurriculumVersion, Program
from .solver_bridge import (
    generate_pos_via_solver,
    validate_pos_via_solver,
    get_solver_metadata,
    compute_placement_diagnostics_via_solver,
)
from .constraint_trace_summary import build_constraint_trace_summary_by_course
from .standing_parity import build_standing_logic_mismatch_rows
from .curriculum_static_feasibility import static_prereq_horizon_diagnosis
from .pdf_header_fields import merge_pdf_header_fields, pdf_header_fields_for_program
from .semester_warnings import (
    LOW_UNITS_WARNING_MESSAGE,
    low_units_warning_for_solver_pos,
)
from .staff_auth_views import user_may_use_staff_curriculum_portal
from .superuser_student_portal import (
    ensure_superuser_student_session,
    is_superuser_demo_session,
)

SR_CODE_REGEX = re.compile(r'^\d{2}-\d{5}$')
# BSU student email: local part may be SR-style (23-00000) or name-style (e.g. first.last@...); no spaces.
BSU_STUDENT_EMAIL_SUFFIX = "@g.batstate-u.edu.ph"
BSU_STUDENT_EMAIL_LOCAL_MAX_LEN = 254 - len(BSU_STUDENT_EMAIL_SUFFIX)
EMAIL_REGEX = re.compile(
    rf"^[A-Za-z0-9._-]{{1,{BSU_STUDENT_EMAIL_LOCAL_MAX_LEN}}}{re.escape(BSU_STUDENT_EMAIL_SUFFIX)}$"
)


_DEBUG_41755C_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-41755c.log"


def _dbg41755c(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "run1"):
    # #region agent log
    try:
        with _DEBUG_41755C_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "41755c",
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(__import__("time").time() * 1000),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


_DEBUG_488397_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-488397.log"


def _dbg488397(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "validate_pos"):
    # #region agent log
    line_obj = {
        "sessionId": "488397",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(__import__("time").time() * 1000),
    }
    line = json.dumps(line_obj, ensure_ascii=True) + "\n"
    try:
        logging.getLogger("pos.validate_pos").info("%s", line.strip())
    except Exception:
        pass
    paths = [_DEBUG_488397_LOG_PATH]
    try:
        bd = getattr(settings, "BASE_DIR", None)
        if bd is not None:
            paths.append(Path(bd) / "debug-488397.log")
    except Exception:
        pass
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            continue
    # #endregion


# #region feasible-move-speed cache (cross-request)
# Module-level LRU cache to speed up exact feasibility checks for feasible move highlights.
_FEASIBLE_DEST_CACHE_MAXSIZE = 4096
_FEASIBLE_DEST_CACHE: "OrderedDict[tuple, bool]" = OrderedDict()


def _feasible_dest_cache_get(cache_key: tuple) -> bool | None:
    if cache_key in _FEASIBLE_DEST_CACHE:
        _FEASIBLE_DEST_CACHE.move_to_end(cache_key)
        return _FEASIBLE_DEST_CACHE[cache_key]
    return None


def _feasible_dest_cache_put(cache_key: tuple, value: bool) -> None:
    _FEASIBLE_DEST_CACHE[cache_key] = value
    _FEASIBLE_DEST_CACHE.move_to_end(cache_key)
    if len(_FEASIBLE_DEST_CACHE) > _FEASIBLE_DEST_CACHE_MAXSIZE:
        _FEASIBLE_DEST_CACHE.popitem(last=False)


# #endregion


# #region feasible-add-courses cache (cross-request)
_FEASIBLE_ADD_COURSES_CACHE_MAXSIZE = 2048
_FEASIBLE_ADD_COURSES_CACHE: "OrderedDict[tuple, bool]" = OrderedDict()


def _feasible_add_courses_cache_get(cache_key: tuple) -> bool | None:
    if cache_key in _FEASIBLE_ADD_COURSES_CACHE:
        _FEASIBLE_ADD_COURSES_CACHE.move_to_end(cache_key)
        return _FEASIBLE_ADD_COURSES_CACHE[cache_key]
    return None


def _feasible_add_courses_cache_put(cache_key: tuple, value: bool) -> None:
    _FEASIBLE_ADD_COURSES_CACHE[cache_key] = value
    _FEASIBLE_ADD_COURSES_CACHE.move_to_end(cache_key)
    if len(_FEASIBLE_ADD_COURSES_CACHE) > _FEASIBLE_ADD_COURSES_CACHE_MAXSIZE:
        _FEASIBLE_ADD_COURSES_CACHE.popitem(last=False)


# #endregion


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


def _curriculum_version_id_for_student(student) -> int | None:
    """Prefer explicit selection; else program default approved version when configured."""
    if student is None:
        return None
    sel = getattr(student, "selected_curriculum_version_id", None)
    if sel:
        return int(sel)
    pid = getattr(student, "program_id", None)
    if not pid:
        return None
    dcv = Program.objects.filter(pk=pid).values_list("default_curriculum_version_id", flat=True).first()
    return int(dcv) if dcv else None


def _attach_student_curriculum_to_payload(request, payload: dict) -> dict:
    """Add curriculum_version_id from the logged-in student when the payload omits it."""
    p = dict(payload or {})
    if p.get("curriculum_version_id") is not None:
        return p
    student = get_logged_in_student(request)
    vid = _curriculum_version_id_for_student(student)
    if vid:
        p["curriculum_version_id"] = int(vid)
    return p


def _solver_meta_for_request(request) -> dict:
    student = get_logged_in_student(request)
    vid = _curriculum_version_id_for_student(student)
    return get_solver_metadata(vid) or {}


def _curriculum_horizon_debug_snapshot(request, payload: dict) -> dict:
    """Lightweight curriculum vs horizon check for DEBUG infeasibility responses."""
    meta = _solver_meta_for_request(request) or {}
    raw_courses = meta.get("courses") if isinstance(meta.get("courses"), dict) else {}
    try:
        mh = int(meta.get("max_semester") or 0)
    except (TypeError, ValueError):
        mh = 0
    max_orig = 0
    beyond: list[tuple[str, int]] = []
    for code, info in raw_courses.items():
        if not isinstance(info, dict):
            continue
        o = info.get("orig_sem")
        try:
            if o is None or (isinstance(o, str) and not str(o).strip()):
                continue
            oi = int(o)
        except (TypeError, ValueError):
            continue
        max_orig = max(max_orig, oi)
        if mh >= 1 and oi > mh:
            beyond.append((str(code), int(oi)))
    beyond.sort(key=lambda t: t[0])
    return {
        "max_semester_horizon": mh,
        "max_orig_sem_among_courses": max_orig,
        "courses_with_orig_sem_strictly_beyond_horizon": beyond[:50],
        "current_sem": payload.get("current_sem"),
        "n_passed": len(payload.get("passed_courses") or []),
        "n_failed": len(payload.get("failed_courses") or []),
    }


def _clamp_solver_current_sem_to_horizon(current_sem: int, request) -> int:
    """
    CP-SAT uses IntVar domains [current_sem, MAX_SEMESTER]. The UI maps
    "last semester enrolled" (0-based curriculum row) to the first **future**
    planning semester: ``(section_index + 1)`` as the next row, then +1 again to
    convert that row to 1-based solver index → ``section_index + 2`` (same as
    ``generate_pos.html``). DB-backed curricula may shorten ``MAX_SEMESTER``;
    if ``current_sem`` exceeds that horizon, OR-Tools raises and Django returns 500.
    """
    try:
        cs = int(current_sem)
    except (TypeError, ValueError):
        return 1
    meta = _solver_meta_for_request(request) or {}
    raw = meta.get("max_semester")
    try:
        mx = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        mx = 0
    if mx < 1:
        return max(1, cs)
    return max(1, min(cs, mx))


def _canonical_approved_json_sha256(approved_json: dict) -> str:
    """Stable digest for audit (same logical JSON → same hex)."""
    raw = json.dumps(approved_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _payload_with_curriculum_audit(payload: dict | None, request) -> dict:
    """
    Session copy of the solver input: adds curriculum_version_id and approved_json_sha256
    so validate / feasible / preview reuse the same curriculum context (not passed into ORM-free CP-SAT).
    """
    base = dict(payload or {})
    student = get_logged_in_student(request)
    vid = base.get("curriculum_version_id")
    if vid is not None:
        try:
            vid = int(vid)
            base["curriculum_version_id"] = vid
        except (TypeError, ValueError):
            vid = None
            base.pop("curriculum_version_id", None)
    if vid is None and student is not None:
        c_vid = _curriculum_version_id_for_student(student)
        if c_vid is not None:
            vid = c_vid
            base["curriculum_version_id"] = vid

    if vid is not None:
        cv = (
            CurriculumVersion.objects.filter(pk=int(vid), status=CurriculumVersion.STATUS_APPROVED)
            .only("approved_json")
            .first()
        )
        if cv and isinstance(cv.approved_json, dict) and cv.approved_json:
            base["approved_json_sha256"] = _canonical_approved_json_sha256(cv.approved_json)
        else:
            base.pop("approved_json_sha256", None)
    else:
        base.pop("curriculum_version_id", None)
        base.pop("approved_json_sha256", None)
    return base


def _session_curriculum_audit_for_pos_record(request) -> tuple[int | None, str]:
    """Read curriculum_version_id and approved_json_sha256 from session last_generated_payload."""
    lp = request.session.get("last_generated_payload")
    if not isinstance(lp, dict):
        return None, ""
    vid_raw = lp.get("curriculum_version_id")
    sha_raw = lp.get("approved_json_sha256")
    sha = sha_raw.strip()[:64] if isinstance(sha_raw, str) else ""
    if vid_raw is None:
        return None, sha
    try:
        return int(vid_raw), sha
    except (TypeError, ValueError):
        return None, sha


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

_VALID_ACADEMIC_YEAR_VALUES = frozenset(c[0] for c in ACADEMIC_YEAR_CHOICES)


def _ensure_session_academic_year_from_student(request, student):
    """If session has no valid academic year, restore from Student (saved when using Generate POS on Home)."""
    if student is None:
        return
    current = (request.session.get('academic_year') or '').strip()
    if current in _VALID_ACADEMIC_YEAR_VALUES:
        return
    stored = (getattr(student, 'academic_year', None) or '').strip()
    if stored in _VALID_ACADEMIC_YEAR_VALUES:
        request.session['academic_year'] = stored
        request.session.modified = True


def _require_student(view_func):
    """Redirect to login if no student in session."""
    def wrapper(request, *args, **kwargs):
        student = get_logged_in_student(request)
        if student is None:
            ensure_superuser_student_session(request)
            student = get_logged_in_student(request)
        if student is None:
            return redirect('login')
        _ensure_session_academic_year_from_student(request, student)
        return view_func(request, *args, **kwargs)
    return wrapper


def _require_student_api(view_func):
    """JSON API: 401 if not logged in as a student."""
    def wrapper(request, *args, **kwargs):
        if get_logged_in_student(request) is None:
            ensure_superuser_student_session(request)
        if get_logged_in_student(request) is None:
            return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
        return view_func(request, *args, **kwargs)
    return wrapper


def _curriculum_me_payload(student: Student) -> dict:
    """Serialize program, selected version, and approved versions for the student's program."""
    program = student.program
    program_data = None
    approved: list[dict] = []
    if program is not None:
        program_data = {"id": program.pk, "slug": program.slug, "name": program.name}
        qs = (
            CurriculumVersion.objects.filter(program=program, status=CurriculumVersion.STATUS_APPROVED)
            .only("id", "version_label", "updated_at")
            .order_by("-updated_at")
        )
        for v in qs:
            approved.append(
                {
                    "id": v.pk,
                    "version_label": v.version_label or "",
                    "updated_at": v.updated_at.isoformat() if v.updated_at else "",
                }
            )
    sel = student.selected_curriculum_version
    selected_data = None
    if sel is not None:
        selected_data = {
            "id": sel.pk,
            "version_label": sel.version_label or "",
            "program_id": sel.program_id,
        }
    return {
        "ok": True,
        "program": program_data,
        "selected_curriculum_version": selected_data,
        "approved_versions": approved,
    }


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

SIGNATORY_ROLES = (
    SignatoryOption.ROLE_ADVISER,
    SignatoryOption.ROLE_DEPARTMENT_CHAIR,
    SignatoryOption.ROLE_PROGRAM_CHAIR,
)
SIGNATORY_ADMIN_PASSWORD = "Admin123"

DEFAULT_SIGNATORY_SEED = {
    SignatoryOption.ROLE_ADVISER: [
        "Engr. LAILA C. HERNANDEZ",
        "Engr. ANTHONY G. HERNANDEZ",
        "Engr. JOYCE ANN G. ACOB",
        "Engr. PHILIP A. CAGUETE",
        "Engr. RENZ RANIEL V. SERRANO",
        "Engr. MARK JOHN FEL T. RAYOS",
        "Engr. ANACIETO N. MERCADO",
    ],
    SignatoryOption.ROLE_DEPARTMENT_CHAIR: [
        "Engr. RICMART V. GARBIN",
    ],
    SignatoryOption.ROLE_PROGRAM_CHAIR: [
        "Engr. MARK JOHN FEL T. RAYOS",
    ],
}


def _ensure_signatory_options_seeded():
    try:
        for role, names in DEFAULT_SIGNATORY_SEED.items():
            # Seed defaults only when a role has no entries yet.
            # This prevents deleted names from being re-created on every request.
            if SignatoryOption.objects.filter(role=role).exists():
                continue
            for name in names:
                SignatoryOption.objects.create(role=role, name=name)
    except (OperationalError, ProgrammingError):
        # Table likely not migrated yet; Preview should still work with defaults.
        return


def _get_signatory_options_map():
    options_map = {role: [] for role in SIGNATORY_ROLES}
    try:
        _ensure_signatory_options_seeded()
        qs = SignatoryOption.objects.filter(role__in=SIGNATORY_ROLES).order_by("role", "name", "id")
        for opt in qs:
            options_map.setdefault(opt.role, []).append({"id": opt.id, "name": opt.name})
    except (OperationalError, ProgrammingError):
        # Fallback defaults when SignatoryOption table is unavailable.
        for role, names in DEFAULT_SIGNATORY_SEED.items():
            options_map[role] = [{"id": None, "name": nm} for nm in names]
    return options_map


def _get_saved_adviser_for_student(student):
    if student is None:
        return ""
    try:
        pref = SignatoryPreference.objects.filter(student=student).only("adviser_name").first()
        return (pref.adviser_name or "").strip() if pref else ""
    except (OperationalError, ProgrammingError):
        return ""


def _set_saved_adviser_for_student(student, adviser_name):
    if student is None or not adviser_name:
        return
    try:
        pref, _created = SignatoryPreference.objects.get_or_create(student=student)
        if pref.adviser_name != adviser_name:
            pref.adviser_name = adviser_name
            pref.save(update_fields=["adviser_name", "updated_at"])
    except (OperationalError, ProgrammingError):
        return


def _resolve_signatories(request, body=None, required=False):
    body = body if isinstance(body, dict) else {}
    body_signatories = body.get("signatories") if isinstance(body.get("signatories"), dict) else {}
    options_map = _get_signatory_options_map()

    def _pick(role, payload_key):
        allowed_names = [item["name"] for item in options_map.get(role, [])]
        if role in (SignatoryOption.ROLE_DEPARTMENT_CHAIR, SignatoryOption.ROLE_PROGRAM_CHAIR):
            return allowed_names[0] if allowed_names else ""
        candidate = (request.GET.get(payload_key) or body_signatories.get(payload_key) or "").strip()
        if candidate and candidate in allowed_names:
            return candidate
        if required:
            return ""
        if role == SignatoryOption.ROLE_ADVISER:
            return ""
        return allowed_names[0] if allowed_names else ""

    return {
        "adviser": _pick(SignatoryOption.ROLE_ADVISER, "adviser"),
        "department_chair": _pick(SignatoryOption.ROLE_DEPARTMENT_CHAIR, "department_chair"),
        "program_chair": _pick(SignatoryOption.ROLE_PROGRAM_CHAIR, "program_chair"),
    }


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


def _has_program_and_curriculum(student: Student | None) -> bool:
    """True when the student has an approved curriculum selection for their program."""
    if student is None:
        return False
    pid = getattr(student, "program_id", None)
    vid = getattr(student, "selected_curriculum_version_id", None)
    if not pid or not vid:
        return False
    return CurriculumVersion.objects.filter(
        pk=vid,
        program_id=pid,
        status=CurriculumVersion.STATUS_APPROVED,
    ).exists()


def _program_curriculum_locked_for_request(request, student: Student | None) -> bool:
    """Students lock program/curriculum after first save; superuser trial profiles stay editable."""
    if is_superuser_demo_session(request):
        return False
    return _has_program_and_curriculum(student)


def _approved_curricula_by_program() -> dict[str, list[dict]]:
    """Map program id (str) -> approved curriculum options for home-page dropdowns."""
    out: dict[str, list[dict]] = {}
    for pid in Program.objects.filter(is_active=True).values_list("pk", flat=True):
        versions = (
            CurriculumVersion.objects.filter(
                program_id=pid,
                status=CurriculumVersion.STATUS_APPROVED,
            )
            .order_by("-updated_at")
            .values("id", "version_label")
        )
        out[str(pid)] = [
            {
                "id": v["id"],
                "label": (v["version_label"] or "").strip() or f"Version #{v['id']}",
            }
            for v in versions
        ]
    return out


def _parse_positive_int(raw: str | None) -> int | None:
    if raw in (None, ""):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _save_student_program_and_curriculum(
    student: Student,
    program_id_raw: str | None,
    curriculum_id_raw: str | None,
) -> bool:
    """Persist program + approved curriculum on the student. Returns False if invalid."""
    program_id = _parse_positive_int((program_id_raw or "").strip() or None)
    curriculum_id = _parse_positive_int((curriculum_id_raw or "").strip() or None)
    if not program_id or not curriculum_id:
        return False
    program = Program.objects.filter(pk=program_id, is_active=True).first()
    if program is None:
        return False
    cv = CurriculumVersion.objects.filter(
        pk=curriculum_id,
        program=program,
        status=CurriculumVersion.STATUS_APPROVED,
    ).first()
    if cv is None:
        return False
    student.program = program
    student.selected_curriculum_version = cv
    student.save(update_fields=["program", "selected_curriculum_version"])
    return True


def _has_profile_photo(request):
    """True if logged-in student has a profile photo uploaded."""
    if is_superuser_demo_session(request):
        return True
    student = get_logged_in_student(request)
    return bool(student and student.profile_photo)


def _student_with_program_curriculum(student: Student | None) -> Student | None:
    """Reload student with program and selected curriculum for profile display."""
    if student is None:
        return None
    try:
        return Student.objects.select_related("program", "selected_curriculum_version").get(pk=student.pk)
    except Student.DoesNotExist:
        return None


def pos_main(request):
    """Main Window - Landing page with Create Account/Login buttons"""
    return render(request, "pos/main.html")


@_require_student
def pos_home(request):
    """Home page - Student info and academic term. Choose Regular/Irregular (if first time) and Current semester."""
    student = get_logged_in_student(request)
    if student is not None:
        try:
            student = Student.objects.select_related("program", "selected_curriculum_version").get(pk=student.pk)
        except Student.DoesNotExist:
            student = None
    program_curriculum_locked = _program_curriculum_locked_for_request(request, student)
    selected_curriculum_label = ""
    if student and student.selected_curriculum_version_id:
        cv = student.selected_curriculum_version
        selected_curriculum_label = (cv.version_label or "").strip() or f"Version #{cv.pk}"
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
    programs = list(
        Program.objects.filter(is_active=True).order_by("name").values("id", "name", "slug")
    )
    context = {
        'student': student,
        'programs': programs,
        'approved_curricula_by_program': _approved_curricula_by_program(),
        'selected_program_id': student.program_id if student else None,
        'selected_curriculum_id': student.selected_curriculum_version_id if student else None,
        'selected_curriculum_label': selected_curriculum_label,
        'program_curriculum_locked': program_curriculum_locked,
        'home_page_config': {
            'selected_curriculum_id': student.selected_curriculum_version_id if student else None,
            'program_curriculum_locked': program_curriculum_locked,
        },
        'academic_year_choices': ACADEMIC_YEAR_CHOICES,
        'current_semester_choices': CURRENT_SEMESTER_CHOICES_YEAR4_ONLY,
        'session_academic_year': request.session.get('academic_year'),
        'session_current_semester_index': current_section_index,
        # For new users, don't default the path; let them choose explicitly
        'session_pos_path': request.session.get('pos_path'),
        'has_generated_pos': has_generated_pos,
        'has_academic_term': _has_academic_term(request),
        'has_program_curriculum': _has_program_and_curriculum(student),
        'has_complete_info': (
            _has_academic_term(request)
            and _has_profile_photo(request)
            and _has_program_and_curriculum(student)
        ),
        'required_term': request.GET.get('required') == '1',
        'is_superuser_demo': is_superuser_demo_session(request),
    }
    return render(request, "pos/home.html", context)


@_require_student
@require_POST
def set_academic_term(request):
    """Set session academic year, program/curriculum, and default POS path configuration."""
    ay = (request.POST.get('academic_year') or '').strip()
    valid_ays = {c[0] for c in ACADEMIC_YEAR_CHOICES}
    student = get_logged_in_student(request)
    program_curriculum_ok = True
    if student is not None:
        if is_superuser_demo_session(request):
            program_curriculum_ok = _save_student_program_and_curriculum(
                student,
                request.POST.get("program_id"),
                request.POST.get("curriculum_version_id"),
            )
            if not program_curriculum_ok:
                program_curriculum_ok = _has_program_and_curriculum(student)
        elif _has_program_and_curriculum(student):
            program_curriculum_ok = True
        else:
            program_curriculum_ok = _save_student_program_and_curriculum(
                student,
                request.POST.get("program_id"),
                request.POST.get("curriculum_version_id"),
            )
    if ay in valid_ays:
        request.session['academic_year'] = ay
        if student is not None:
            student.academic_year = ay
            student.save(update_fields=['academic_year'])
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
            if student is not None:
                # Persist on the Student record as well
                student.year_level = yl
                student.semester = sem
                student.save(update_fields=['year_level', 'semester'])
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/'):
        if (
            not program_curriculum_ok
            or not _has_program_and_curriculum(student)
            or not _has_academic_term(request)
            or not _has_profile_photo(request)
        ):
            return redirect(reverse('pos_home') + '?required=1')
        return redirect(next_url)
    return redirect('pos_home')


def _curriculum_for_irregular():
    """Build curriculum sections list for irregular POS picker (year, semester, section_label, courses)."""
    from .pos_pdf import POS_CURRICULUM, display_pos_course_code
    sections = []
    for section_index, (title1, title2, _tu, _lec, _lab, rows) in enumerate(POS_CURRICULUM):
        if section_index < len(_INDEX_TO_YEAR_SEM):
            _yl, _sem, section_label = section_meta_for_index(section_index)
        else:
            section_label = format_legacy_pos_section_label(title1, title2)
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


# Order matches POS_CURRICULUM (see pos/curriculum_section_labels.py).
from .curriculum_section_labels import (
    INDEX_TO_YEAR_SEM as _INDEX_TO_YEAR_SEM,
    YEAR_SEM_TO_INDEX as _YEAR_SEM_TO_INDEX,
    format_legacy_pos_section_label,
    section_meta_for_index,
)

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
# This dropdown is used for both "LAST SEMESTER ENROLLED" and "FIRST FAILED SEMESTER"
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


def _merged_grades_by_section_for_policy(request, body_grades):
    """
    Merge saved DB grades with JSON body grades for policy checks (same-course fail cap).
    Per section, request body overwrites saved grades for each course code.
    """
    body = body_grades if isinstance(body_grades, dict) else {}
    saved = {}
    try:
        student = get_logged_in_student(request)
        if student is not None:
            saved = _saved_grades_by_section(student)
    except Exception:
        pass
    section_keys = set()
    for k in saved:
        section_keys.add(str(k))
    for k in body:
        section_keys.add(str(k))
    merged = {}
    for sk in section_keys:
        d_saved = {}
        for kk, vv in saved.items():
            if str(kk) == sk and isinstance(vv, dict):
                d_saved = dict(vv)
                break
        d_body = {}
        for kk, vv in body.items():
            if str(kk) == sk and isinstance(vv, dict):
                d_body = dict(vv)
                break
        merged[sk] = {**d_saved, **d_body}
    return merged


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


def _is_failing_grade(grade):
    """True only for an explicit failing grade (5.00), not blank rows."""
    if grade is None or (isinstance(grade, str) and not str(grade).strip()):
        return False
    s = str(grade).strip()
    if s in ("5", "5.0", "5.00"):
        return True
    try:
        return float(grade) >= 5.0
    except (TypeError, ValueError):
        return False


# Block automatic POS when the same course shows grade 5.00 in this many different semester rows.
MAX_FAILED_ATTEMPTS_SAME_COURSE = 4

# Per semester table only: 50% and 75% failed-unit rules apply only when the student entered
# grades for at least this many units in that table (graded rows only; blanks excluded).
MIN_GRADED_UNITS_PER_SEMESTER_TABLE_FOR_FAIL_RATIO = 10


def _units_for_policy_grade(
    code: str,
    section_index: int,
    gctx: dict,
    course_lookup: dict,
) -> float:
    """
    Units for per-semester-table failed-unit ratio checks.

    Prefer ``course_lookup`` (program catalog). If the grade key does not match the
    lookup (whitespace, legacy saves, version skew), fall back to that section's
    curriculum row so missing keys do not shrink the denominator to a few units.
    """
    if not code:
        return 0.0
    code_s = str(code).strip()
    if not code_s:
        return 0.0
    cl = course_lookup or {}
    for key in (code_s, code):
        info = cl.get(key)
        if info is not None:
            try:
                return float(info[1])
            except (TypeError, ValueError, IndexError):
                break
    mode = (gctx or {}).get("mode")
    if mode == "db":
        secs = (gctx or {}).get("curriculum_sections") or []
        try:
            sec = secs[int(section_index)]
        except (IndexError, TypeError, ValueError):
            sec = None
        if isinstance(sec, dict):
            for c in sec.get("courses") or []:
                if not isinstance(c, dict):
                    continue
                cc = str(c.get("code") or "").strip()
                if cc == code_s:
                    u = c.get("units", 0)
                    try:
                        if u is None or (isinstance(u, str) and not str(u).strip()):
                            return 0.0
                        return float(u)
                    except (TypeError, ValueError):
                        return 0.0
        # Do not fall back to legacy POS_CURRICULUM in db mode: section index and course
        # codes are for the student's approved bundle, not the static BSCpE grid.
        return 0.0
    try:
        from .pos_pdf import POS_CURRICULUM

        si = int(section_index)
        if 0 <= si < len(POS_CURRICULUM):
            _t1, _t2, _tu, _lec, _lab, rows = POS_CURRICULUM[si]
            for r in rows:
                rc = str(r[0]).strip() if r[0] is not None else ""
                if rc == code_s:
                    try:
                        return float(r[2])
                    except (TypeError, ValueError):
                        return 0.0
    except (TypeError, ValueError):
        pass
    return 0.0


def _failed_attempt_counts_by_course(grades_by_section):
    counts: dict[str, int] = {}
    if not isinstance(grades_by_section, dict):
        return counts
    for _sk, section_grades in grades_by_section.items():
        if not isinstance(section_grades, dict):
            continue
        for code, grade in section_grades.items():
            if not code or not _is_failing_grade(grade):
                continue
            code_s = str(code).strip()
            if code_s:
                counts[code_s] = counts.get(code_s, 0) + 1
    return counts


def _first_excessive_failure_violation(grades_by_section, course_lookup=None):
    """Return (code, count, title) if any course has >= MAX_FAILED_ATTEMPTS_SAME_COURSE fails; else None."""
    counts = _failed_attempt_counts_by_course(grades_by_section)
    if not counts:
        return None
    if course_lookup is None:
        course_lookup = _course_lookup_from_curriculum()
    for code, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        if n >= MAX_FAILED_ATTEMPTS_SAME_COURSE:
            info = course_lookup.get(code)
            title = (info[0] if info else "") or ""
            return (code, n, title)
    return None


def _pdf_header_for_student(student, snapshot=None):
    """PDF header text from the student's program (staff-configured). Snapshot optional for history."""
    program = getattr(student, "program", None) if student is not None else None
    layers = [pdf_header_fields_for_program(program)]
    if isinstance(snapshot, dict) and isinstance(snapshot.get("pdf_header"), dict):
        layers.append(snapshot["pdf_header"])
    return merge_pdf_header_fields(*layers)


def _message_excessive_same_course_failures(code: str, count: int, title: str) -> str:
    title_part = f" ({title})" if title else ""
    return (
        f"You have failed {code}{title_part} {count} times across the semester tables you entered. "
        "You cannot generate a Program of Study automatically after four or more failures in the same course. "
        "Please refer to an academic adviser for assistance."
    )


def _flexible_courses_for_section(section_index, gen_mode="static"):
    """Return list of course dicts for flexible subjects offered in this section (BSCpE static only in v1)."""
    if gen_mode == "db":
        return []
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


def _picker_available_for_next_section(
    next_section_index, curriculum_sections, saved_grades_by_section, gen_mode="static"
):
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
    for flex in _flexible_courses_for_section(next_section_index, gen_mode):
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
        if section_index < len(_INDEX_TO_YEAR_SEM):
            _yl, _sem, section_label = section_meta_for_index(section_index)
        else:
            section_label = format_legacy_pos_section_label(title1, title2)
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


def _preview_sections_from_resolved_sections(curriculum_sections, course_lookup, grades_by_section):
    """Preview rows from DB-resolved curriculum_sections (same output shape as _preview_sections_from_curriculum)."""
    from .pos_pdf import display_pos_course_code

    sections = []
    for section_index, sec in enumerate(curriculum_sections or []):
        section_grades = (grades_by_section or {}).get(section_index, {})
        section_label = sec.get("label") or ""
        course_rows = []
        seen_codes: set[str] = set()
        for c in sec.get("courses") or []:
            code = c.get("code")
            if not code:
                continue
            title = c.get("title") or code
            units = c.get("units", 0)
            lec = c.get("lec", 0)
            lab = c.get("lab", 0)
            prereq = c.get("prereq") or ""
            coreq = c.get("coreq") or ""
            grade = section_grades.get(code)
            if grade is not None and grade != "":
                seen_codes.add(code)
                try:
                    g = float(grade)
                    remark = "Passed" if g <= 3.00 else ("Failed" if g == 5.00 else "")
                except (ValueError, TypeError):
                    remark = ""
                course_rows.append(
                    {
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
                    }
                )
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
            course_rows.append(
                {
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
                }
            )
        total_units_cur = sum(float(c.get("units") or 0) for c in (sec.get("courses") or []))
        total_units_shown = sum(row.get("units", 0) for row in course_rows) if course_rows else 0
        if course_rows:
            sections.append(
                {
                    "label": section_label,
                    "total_units": total_units_shown or total_units_cur,
                    "rows": course_rows,
                }
            )
    return sections


def _session_year_sem_from_section_index(
    gen_mode: str,
    curriculum_sections: list,
    section_index: int,
) -> tuple[str, str]:
    """Persist year_level/semester from DB-mapped section labels when in db mode; else legacy index table."""
    if (
        gen_mode == "db"
        and isinstance(curriculum_sections, list)
        and 0 <= section_index < len(curriculum_sections)
    ):
        sec = curriculum_sections[section_index]
        yl = sec.get("year")
        sem = sec.get("semester")
        if yl and sem:
            return str(yl), str(sem)
    if 0 <= section_index < len(_INDEX_TO_YEAR_SEM):
        return _INDEX_TO_YEAR_SEM[section_index]
    return _INDEX_TO_YEAR_SEM[0] if _INDEX_TO_YEAR_SEM else ("", "")


@ensure_csrf_cookie
@_require_student
def pos_generate(request):
    """Generate POS page. Current semester is set by ?section=N or from session (year_level + semester)."""
    student = _student_with_program_curriculum(get_logged_in_student(request))
    if (
        not _has_academic_term(request)
        or not _has_profile_photo(request)
        or not _has_program_and_curriculum(student)
    ):
        return redirect(reverse('pos_home') + '?required=1')
    saved_grades_by_section = _saved_grades_by_section(student)
    from .generate_curriculum_context import resolve_generate_curriculum_context

    gen_curriculum_ctx = resolve_generate_curriculum_context(student)
    curriculum_sections = gen_curriculum_ctx["curriculum_sections"]
    course_lookup = gen_curriculum_ctx["course_lookup"]
    gen_curriculum_mode = gen_curriculum_ctx["mode"]

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
            if 0 <= idx < len(curriculum_sections):
                yl, sem = _session_year_sem_from_section_index(gen_curriculum_mode, curriculum_sections, idx)
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
            if next_section_index < len(curriculum_sections):
                yl, sem = _session_year_sem_from_section_index(
                    gen_curriculum_mode, curriculum_sections, next_section_index
                )
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
        si: _picker_available_for_next_section(
            si, curriculum_sections, saved_grades_by_section, gen_curriculum_mode
        )
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
        # irreg_start = semester index for first failed semester (optional; curriculum boundary)
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
            # If FIRST FAILED SEMESTER is explicitly selected, honor that exact dropdown value.
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

            yl, sem = _session_year_sem_from_section_index(
                gen_curriculum_mode, curriculum_sections, irreg_current_sem
            )
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
                    if si < 0:
                        continue
                    if isinstance(courses, list) and any(
                        (c.get("code") if isinstance(c, dict) else None) for c in courses
                    ):
                        table_scope_sem = max(table_scope_sem, si)

            if table_scope_sem < 0:
                table_scope_sem = _max_ui_section_from_grades_and_pos(saved_grades_by_section, gen_pos)

            # The saved POS may contain semesters beyond the natural program length
            # (e.g. 5th-year sections for a 4-year program due to petition / overload).
            # Synthesise the extra section entries so the display loop can reach them.
            if table_scope_sem > max_si:
                for _ext_si in range(max_si + 1, table_scope_sem + 1):
                    _yl, _sem, _lbl = section_meta_for_index(_ext_si)
                    curriculum_sections.append({"year": _yl, "semester": _sem, "label": _lbl, "courses": []})
                max_si = len(curriculum_sections) - 1

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

            # When the user explicitly picked "FIRST FAILED SEMESTER",
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
                    # If user explicitly configured first failed semester,
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
                candidates = _picker_available_for_next_section(
                    si, curriculum_sections, saved_grades_by_section, gen_curriculum_mode
                )
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

    # "Last Semester Enrolled" dropdown cap on Generate page:
    # - default: up to 5th Year 2nd Sem (index 13)
    # - extend to 6th+ only when a loaded POS from history actually contains those semesters
    dropdown_max_generate = SECTION_INDEX_MAX_DROPDOWN_GENERATE
    if bool(request.session.get('pos_loaded_from_history')):
        loaded_max_si = -1
        loaded_preview_ui_sections = request.session.get("pos_loaded_preview_ui_sections") or {}
        if isinstance(loaded_preview_ui_sections, dict) and loaded_preview_ui_sections:
            for k, courses in loaded_preview_ui_sections.items():
                try:
                    si = int(k)
                except (TypeError, ValueError):
                    continue
                if si < 0:
                    continue
                if isinstance(courses, list) and any(
                    (c.get("code") if isinstance(c, dict) else None) for c in courses
                ):
                    loaded_max_si = max(loaded_max_si, si)
        if loaded_max_si < 0:
            loaded_max_si = _max_ui_section_from_grades_and_pos(
                saved_grades_by_section,
                request.session.get("last_generated_pos") or [],
            )
        if loaded_max_si > dropdown_max_generate:
            dropdown_max_generate = loaded_max_si
    dropdown_max_generate = min(dropdown_max_generate, len(curriculum_sections) - 1)

    current_semester_choices_regular = [
        c for c in CURRENT_SEMESTER_CHOICES
        if c[0] <= dropdown_max_generate and c[0] != SECOND_YEAR_MIDTERM_INDEX
    ]

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
        'curriculum_display_title': gen_curriculum_ctx.get("display_title") or "",
        'curriculum_source': gen_curriculum_ctx.get("mode") or "static",
        'curriculum_subtitle': gen_curriculum_ctx.get("subtitle") or "",
        'curriculum_version_id': gen_curriculum_ctx.get("curriculum_version_id"),
        'curriculum_truncated': bool(gen_curriculum_ctx.get("curriculum_truncated")),
        'curriculum_sections': curriculum_sections,
        'curriculum_sections_dropdown': curriculum_sections[:dropdown_max_generate + 1],
        'current_semester_choices_regular': current_semester_choices_regular,
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
    """
    Validate POS feasibility using CP-SAT, based on the student's current passed/failed grades.

    This is used by the `/generate` page "Validate POS" button, and should fail when
    prerequisite validity is violated (e.g., failed Differential Calculus must block
    schedules that depend on Integral Calculus).

    Returns JSON { ok, status, message, errors }.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'message': 'Invalid JSON', 'errors': []}, status=400)
    grades_by_section = data.get('grades_by_section') or {}
    if not isinstance(grades_by_section, dict):
        grades_by_section = {}

    student = get_logged_in_student(request)
    from .generate_curriculum_context import resolve_generate_curriculum_context

    try:
        gctx = resolve_generate_curriculum_context(student) if student is not None else {}
    except Exception as e:
        _dbg488397(
            "H0",
            "views.py:validate_pos",
            "resolve_generate_curriculum_context raised",
            {"exc_type": type(e).__name__, "exc_msg": str(e)[:800]},
        )
        raise
    if not gctx:
        gctx = {"mode": "static", "curriculum_sections": [], "course_lookup": _course_lookup_from_curriculum()}
    g_course_lookup = gctx.get("course_lookup") or _course_lookup_from_curriculum()

    _dbg488397(
        "H1",
        "views.py:validate_pos",
        "after_gctx",
        {
            "mode": gctx.get("mode"),
            "cv_id": gctx.get("curriculum_version_id"),
            "n_curriculum_sections": len(gctx.get("curriculum_sections") or []),
            "grade_section_keys_sample": [str(k) for k in list(grades_by_section.keys())[:12]],
        },
    )

    viol = _first_excessive_failure_violation(grades_by_section, g_course_lookup)
    if viol:
        code, n, title = viol
        msg = _message_excessive_same_course_failures(code, n, title)
        return JsonResponse(
            {
                'ok': False,
                'status': 'infeasible',
                'message': msg,
                'errors': [msg],
            }
        )

    errors = []
    course_lookup = g_course_lookup
    from .pos_pdf import POS_CURRICULUM

    num_sections = len(POS_CURRICULUM)
    if gctx.get("mode") == "db" and gctx.get("curriculum_sections"):
        num_sections = len(gctx["curriculum_sections"])
    for section_key, section_grades in grades_by_section.items():
        if not isinstance(section_grades, dict):
            continue
        try:
            si = int(section_key)
        except (ValueError, TypeError):
            continue
        if si < 0 or si >= num_sections:
            continue
        if gctx.get("mode") == "db" and gctx.get("curriculum_sections"):
            sec = gctx["curriculum_sections"][si]
            section_label = sec.get("label") or ""
        else:
            if si < len(_INDEX_TO_YEAR_SEM):
                _yl, _sem, section_label = section_meta_for_index(si)
            else:
                _t1, _t2, _total_units_cur, _lec, _lab, rows = POS_CURRICULUM[si]
                section_label = format_legacy_pos_section_label(_t1, _t2)
        # Per this semester table only (not pooled with other tables). Denominator: units for rows
        # with a non-empty grade only. Neither the 50% nor the 75% rule runs if graded units < minimum.
        graded_u = 0.0
        failed_u = 0.0
        n_graded = 0
        n_failed = 0
        for code, grade in section_grades.items():
            if not code:
                continue
            if grade is None or (isinstance(grade, str) and not str(grade).strip()):
                continue
            units = _units_for_policy_grade(str(code), si, gctx, course_lookup)
            graded_u += units
            n_graded += 1
            if _is_failing_grade(grade):
                failed_u += units
                n_failed += 1
        if graded_u < MIN_GRADED_UNITS_PER_SEMESTER_TABLE_FOR_FAIL_RATIO:
            continue
        ratio = failed_u / graded_u if graded_u else 0.0
        pct = int(round(100.0 * ratio))
        if ratio >= 0.75:
            errors.append(
                'In "{}" (this semester table only — grades here are not combined with other semesters): '
                "you graded {} course(s) ({} units total); {} course(s) failed ({} units), about {}% of graded units in this table. "
                "When 75% or more of graded units in one semester table are failures, automatic POS generation is not allowed. "
                "Please consult your program adviser.".format(
                    section_label, n_graded, int(round(graded_u)), n_failed, int(round(failed_u)), pct
                )
            )
        elif ratio >= 0.50:
            errors.append(
                'In "{}" (this semester table only — grades here are not combined with other semesters): '
                "you graded {} course(s) ({} units total); {} course(s) failed ({} units), about {}% of graded units in this table. "
                "When 50% or more of graded units in one semester table are failures, manual evaluation is required before your Program of Study can be finalized. "
                "Please consult your program adviser.".format(
                    section_label, n_graded, int(round(graded_u)), n_failed, int(round(failed_u)), pct
                )
            )
    # Fast fail: unit-failure ratio guard (per semester table, graded rows only).
    if errors:
        main_message = errors[0] if len(errors) == 1 else errors[0] + "\n\n" + "\n\n".join(errors[1:])
        return JsonResponse(
            {
                'ok': False,
                'status': 'infeasible',
                'message': main_message,
                'errors': errors,
            }
        )

    # Build passed/failed sets from the user grade profile and also keep the semester
    # where each course was passed so we can enforce "prereq must be passed earlier".
    passed_ui: set[str] = set()
    failed_ui: set[str] = set()
    passed_sem_by_solver: dict[str, set[int]] = {}
    passed_occurrences: list[tuple[str, int]] = []  # (solver_code, solver_sem)

    solver_meta = _solver_meta_for_request(request) or {}
    solver_courses = solver_meta.get("courses") if isinstance(solver_meta.get("courses"), dict) else {}
    year_standing_rules = solver_meta.get("year_standing_rules") if isinstance(solver_meta.get("year_standing_rules"), dict) else {}
    _grade_keys = set(solver_courses.keys()) if solver_courses else _curriculum_course_keys_for_request(request)

    for section_key, section_grades in (grades_by_section or {}).items():
        try:
            ui_si = int(section_key)
        except (TypeError, ValueError):
            continue

        # UI semester index is 0-based; solver semester index is 1-based.
        solver_si = ui_si + 1

        if not isinstance(section_grades, dict):
            continue

        for code_ui, grade in (section_grades or {}).items():
            if not code_ui:
                continue
            if grade is None:
                continue
            if isinstance(grade, str) and not grade.strip():
                continue
            code_s = str(code_ui).strip()
            if not code_s:
                continue

            solver_code = _to_solver_course_code(code_s, _grade_keys)

            if _is_passing_grade(grade):
                passed_ui.add(code_s)
                passed_sem_by_solver.setdefault(solver_code, set()).add(solver_si)
                passed_occurrences.append((solver_code, solver_si))
            else:
                failed_ui.add(code_s)

    # If a course appears in both passed and failed (retake), treat it as passed.
    for c in list(failed_ui):
        if c in passed_ui:
            failed_ui.discard(c)

    # 1) Strict prereq validation based on your rule:
    #    if a course was FAILED, it cannot be used as a prerequisite until the student
    #    inputs a PASS for that prerequisite in an earlier semester.
    for solver_code, course_sem in passed_occurrences:
        cinfo = solver_courses.get(solver_code) if isinstance(solver_courses, dict) else None
        if not isinstance(cinfo, dict):
            continue

        pre_list = cinfo.get("pre-requisites") or []
        if not isinstance(pre_list, list) or not pre_list:
            continue

        orig_sem_c = cinfo.get("orig_sem")

        for pre in pre_list:
            if not isinstance(pre, str) or not pre.strip():
                continue
            pre_code = pre.strip()
            # Course prerequisite (exact course code)
            if pre_code in solver_courses:
                pre_passes = passed_sem_by_solver.get(pre_code) or set()
                if not any(pre_sem < course_sem for pre_sem in pre_passes):
                    return JsonResponse(
                        {
                            'ok': False,
                            'status': 'infeasible',
                            'message': f"Schedule is infeasible: prerequisite not satisfied ({solver_code} requires {pre_code} to be passed before the selected semester).",
                            'errors': [],
                        }
                    )
            # Year-standing prerequisite token
            elif pre_code in year_standing_rules:
                try:
                    if orig_sem_c is None:
                        continue
                    orig_sem_c_i = int(orig_sem_c)
                except (TypeError, ValueError):
                    continue

                total_units_prefix = 0.0
                earned_units_prefix = 0.0
                for pc, pinfo in solver_courses.items():
                    if not isinstance(pinfo, dict):
                        continue
                    p_orig_sem = pinfo.get("orig_sem")
                    if p_orig_sem is None:
                        continue
                    try:
                        if int(p_orig_sem) >= orig_sem_c_i:
                            continue
                    except (TypeError, ValueError):
                        continue

                    units = float(pinfo.get("units", 0) or 0)
                    total_units_prefix += units

                    # Earned only if passed before this course's semester.
                    pre_passes = passed_sem_by_solver.get(pc) or set()
                    if any(pre_sem < course_sem for pre_sem in pre_passes):
                        earned_units_prefix += units

                required_units_prefix = float(math.ceil(0.9 * total_units_prefix)) if total_units_prefix > 0 else 0.0
                if earned_units_prefix < required_units_prefix:
                    return JsonResponse(
                        {
                            'ok': False,
                            'status': 'infeasible',
                            'message': "Schedule is infeasible: year-standing prerequisite not satisfied for the selected semester.",
                            'errors': [],
                        }
                    )
            else:
                # Unknown prereq token: don't block on it here.
                continue

    _dbg488397(
        "H1b",
        "views.py:validate_pos",
        "prereq_loop_done",
        {
            "n_passed_ui": len(passed_ui),
            "n_failed_ui": len(failed_ui),
            "n_solver_courses_meta": len(solver_courses) if isinstance(solver_courses, dict) else -1,
        },
    )

    # 2) CP-SAT check for offering windows / other constraints.
    #    Note: CP-SAT itself does not treat `failed_courses` as prerequisite blockers, so we
    #    keep the strict grade-table prereq validation above. CP-SAT is still valuable for
    #    other constraints (offering windows, unit caps, etc.).
    #
    #    IMPORTANT: Generate POS sends `current_sem` from the same convention as the
    #    Last Semester Enrolled dropdown (see generate_pos.html `buildSolverPayloadFromGrades`).
    #    Historically this view used only session year_level/semester, which can drift if
    #    the session was not updated on the last navigation — then validate says INFEASIBLE
    #    while generate would use the correct anchor. Prefer explicit `current_sem` when valid.
    year_level = request.session.get('year_level')
    semester = request.session.get('semester')
    current_section_index = _year_semester_to_section_index(year_level, semester)

    current_sem_solver = None
    current_sem_source = "session"
    body_cs = data.get("current_sem")
    if body_cs is not None and body_cs != "":
        try:
            current_sem_solver = _clamp_solver_current_sem_to_horizon(int(body_cs), request)
            current_sem_source = "body"
        except (TypeError, ValueError):
            current_sem_solver = None

    if current_sem_solver is None:
        if current_section_index is None:
            return JsonResponse(
                {
                    'ok': False,
                    'status': 'infeasible',
                    'message': 'Please select your Last Semester Enrolled to proceed.',
                    'errors': [],
                }
            )

        _raw_current_sem = int(current_section_index) + 2
        current_sem_solver = _clamp_solver_current_sem_to_horizon(
            _raw_current_sem, request
        )  # UI convention: section_index + 2; clamp to active curriculum MAX_SEMESTER
        current_sem_source = "session"

    _cv_keys = _curriculum_course_keys_for_request(request)
    passed_solver = _to_solver_pass_fail_codes(passed_ui, _cv_keys)
    failed_solver = _to_solver_pass_fail_codes(failed_ui, _cv_keys)

    trace_raw = data.get("cp_sat_constraint_trace_courses")
    trace_solver: list[str] | None = None
    if trace_raw is not None:
        if isinstance(trace_raw, str) and trace_raw.strip():
            trace_parts = [p.strip() for p in trace_raw.split(",") if p.strip()]
        elif isinstance(trace_raw, (list, tuple)):
            trace_parts = [str(x).strip() for x in trace_raw if str(x).strip()]
        else:
            trace_parts = []
        if trace_parts:
            trace_solver = [_to_solver_course_code(c, _cv_keys) for c in trace_parts]

    payload = {
        'current_sem': current_sem_solver,
        'passed_courses': passed_solver,
        'failed_courses': failed_solver,
        'with_petition': False,
        'alternative_algorithm': None,  # enforce CP-SAT exactness
    }
    if trace_solver:
        payload["cp_sat_constraint_trace_courses"] = trace_solver

    _meta_pre_solve = _solver_meta_for_request(request) or {}
    _dbg488397(
        "H2",
        "views.py:validate_pos",
        "before_solver",
        {
            "year_level": str(year_level or ""),
            "semester": str(semester or ""),
            "current_sem_source": current_sem_source,
            "current_section_index": int(current_section_index) if current_section_index is not None else None,
            "clamped_current_sem": int(current_sem_solver),
            "max_semester_meta": _meta_pre_solve.get("max_semester"),
            "n_passed_solver": len(passed_solver),
            "n_failed_solver": len(failed_solver),
            "failed_solver_sample": failed_solver[:15],
            "curriculum_vid": _curriculum_version_id_for_student(student),
        },
    )

    try:
        solver_result = validate_pos_via_solver(
            payload=_attach_student_curriculum_to_payload(request, payload),
            with_petition=False,
            forced_semester_map=None,
        )
    except Exception as e:
        _dbg488397(
            "H4",
            "views.py:validate_pos",
            "validate_pos_via_solver raised",
            {"exc_type": type(e).__name__, "exc_msg": str(e)[:1200]},
        )
        raise
    _dbg488397(
        "H3",
        "views.py:validate_pos",
        "solver_returned",
        {"status": str(solver_result.get("status") or "")},
    )
    status = solver_result.get('status')
    message = _user_facing_solver_message(solver_result.get('message') or 'Schedule is infeasible.')
    ok = status == 'ok'

    resp: dict = {
        'ok': ok,
        'status': status,
        'message': 'POS is valid.' if ok else message,
        'errors': [] if ok else [message],
    }
    if settings.DEBUG:
        tr = solver_result.get("constraint_build_trace")
        if tr is not None:
            if isinstance(tr, list) and len(tr) > 600:
                resp["constraint_build_trace"] = tr[:600]
                resp["constraint_build_trace_truncated"] = True
            else:
                resp["constraint_build_trace"] = tr
    if settings.DEBUG and not ok:
        resp["solver_status"] = status
        raw = solver_result.get('message')
        if raw is not None:
            resp['solver_message_raw'] = str(raw)[:8000]
        resp['curriculum_horizon_debug'] = _curriculum_horizon_debug_snapshot(request, payload)
        meta_dbg = _meta_pre_solve or {}
        try:
            resp["static_prereq_diagnosis"] = static_prereq_horizon_diagnosis(
                courses=meta_dbg.get("courses") if isinstance(meta_dbg.get("courses"), dict) else {},
                passed_courses=passed_solver,
                failed_courses=failed_solver,
                current_sem=int(current_sem_solver),
                max_semester=int(meta_dbg.get("max_semester") or 0),
                prereq_aliases=meta_dbg.get("prereq_aliases")
                if isinstance(meta_dbg.get("prereq_aliases"), dict)
                else {},
                year_standing_rules=meta_dbg.get("year_standing_rules")
                if isinstance(meta_dbg.get("year_standing_rules"), dict)
                else {},
            )
        except Exception as e:
            resp["static_prereq_diagnosis_error"] = str(e)[:500]

        try:
            diag_payload = _attach_student_curriculum_to_payload(request, dict(payload))
            diag = compute_placement_diagnostics_via_solver(diag_payload)
            cap = diag.get("capacity_feasibility_snapshot")
            if isinstance(cap, dict):
                sc = cap.get("suffix_checks")
                if isinstance(sc, list) and len(sc) > 30:
                    cap = {**cap, "suffix_checks": sc[:30], "suffix_checks_truncated": True}
                resp["capacity_feasibility_snapshot"] = cap
            tb = diag.get("term_bottleneck_report")
            if isinstance(tb, list):
                resp["term_bottleneck_report"] = tb[:20]
            resp["placement_diagnostics_meta"] = {
                "diagnostics_version": diag.get("diagnostics_version"),
                "configuration": diag.get("configuration"),
                "notes": diag.get("notes"),
            }
            mc = diag.get("most_constrained_courses") or []
            if isinstance(mc, list):
                resp["most_constrained_courses"] = mc[:15]

            meta_courses = meta_dbg.get("courses") if isinstance(meta_dbg.get("courses"), dict) else {}
            pss = {str(k): set(v) for k, v in (passed_sem_by_solver or {}).items()}
            resp["standing_logic_mismatches"] = build_standing_logic_mismatch_rows(
                courses=meta_courses,
                passed_courses=passed_solver,
                failed_courses=failed_solver,
                current_sem=int(current_sem_solver),
                max_semester=int(meta_dbg.get("max_semester") or 0),
                year_standing_rules=meta_dbg.get("year_standing_rules")
                if isinstance(meta_dbg.get("year_standing_rules"), dict)
                else {},
                prereq_aliases=meta_dbg.get("prereq_aliases")
                if isinstance(meta_dbg.get("prereq_aliases"), dict)
                else {},
                passed_sem_by_solver=pss,
                most_constrained_courses=mc[:15] if isinstance(mc, list) else [],
            )

            tr = solver_result.get("constraint_build_trace")
            if isinstance(tr, list) and tr:
                terms_map = {
                    str(r.get("course")): (r.get("final_candidate_terms") or [])[:25]
                    for r in mc
                    if isinstance(r, dict) and r.get("course")
                }
                resp["constraint_build_summary_by_course"] = build_constraint_trace_summary_by_course(
                    tr[:400], terms_map
                )
        except Exception as e:
            resp["placement_diagnostics_error"] = str(e)[:800]
    return JsonResponse(resp)



# Elective course code translation: the solver uses "Elective Course 1/2/3" as its
# internal course keys, while the Django UI uses "CpEE 401/402/403". These maps
# convert between the two representations at the API boundary.
_UI_TO_SOLVER_ELECTIVE = {
    "CpEE 401": "Elective Course 1",
    "CpEE 402": "Elective Course 2",
    "CpEE 403": "Elective Course 3",
}
_SOLVER_TO_UI_ELECTIVE = {v: k for k, v in _UI_TO_SOLVER_ELECTIVE.items()}


def _curriculum_course_keys_for_request(request) -> set[str]:
    """Course-code keys from the student's active approved curriculum (or static solver grid)."""
    meta = _solver_meta_for_request(request) or {}
    courses = meta.get("courses")
    if isinstance(courses, dict):
        return {str(k) for k in courses}
    return set()


def _to_solver_course_code(ui_code: str, course_keys: set[str] | None = None) -> str:
    """
    Map a UI grade/POS code to the key used in the active curriculum bundle.

    Staff-uploaded curricula use CpEE 401/402/403; the legacy static solver grid uses
    Elective Course 1/2/3. Blindly mapping CpEE → Elective breaks passed-course detection
    for DB curricula and causes duplicate elective rows in generated POS.
    """
    c = str(ui_code or "").strip()
    if not c:
        return c
    keys = course_keys if course_keys is not None else set()
    if keys:
        if c in keys:
            return c
        alt = _UI_TO_SOLVER_ELECTIVE.get(c)
        if alt and alt in keys:
            return alt
        rev = _SOLVER_TO_UI_ELECTIVE.get(c)
        if rev and rev in keys:
            return rev
        return c
    return _UI_TO_SOLVER_ELECTIVE.get(c, c)


def _to_solver_pass_fail_codes(ui_codes, course_keys: set[str] | None = None) -> list[str]:
    """Resolve passed/failed lists to curriculum bundle keys (deduplicated, sorted)."""
    keys = course_keys if course_keys is not None else set()
    out: set[str] = set()
    for raw in ui_codes or []:
        c = str(raw).strip()
        if not c:
            continue
        out.add(_to_solver_course_code(c, keys))
    return sorted(out)


# CP-SAT pipes `_log_infeasible` stdout into `message`; students should not see that block.
_SOLVER_VERBOSE_DIAGNOSTIC_MARKERS = (
    "NO VALID SCHEDULE (Infeasible)",
    "NO VALID SCHEDULE",
    "Solver status:",
    "CpSolverResponse summary:",
    "Unit caps by semester (from current onward):",
    "Possible reasons (check your curriculum and grades):",
)

_HORIZON_GAP_USER_MESSAGE = (
    "Automatic scheduling is not possible with your current grades and curriculum rules "
    "(for example: where failed courses may be retaken, term placement, unit limits, or prerequisites). "
    "Please consult your academic adviser so they can review your situation and approve a manual Program of Study."
)


def _user_facing_solver_message(message):
    """Replace internal solver diagnostic dumps with a short student-facing prompt."""
    if message is None:
        return message
    text = str(message).strip()
    if not text:
        return message
    for marker in _SOLVER_VERBOSE_DIAGNOSTIC_MARKERS:
        if marker in text:
            return _HORIZON_GAP_USER_MESSAGE
    return str(message)


def _sanitize_solver_result_messages(result):
    if not isinstance(result, dict):
        return
    if result.get("message") is not None:
        result["message"] = _user_facing_solver_message(result.get("message"))
    pos_options = result.get("pos_options")
    if isinstance(pos_options, dict):
        for opt in pos_options.values():
            if isinstance(opt, dict) and opt.get("message") is not None:
                opt["message"] = _user_facing_solver_message(opt["message"])


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

    # Map UI elective codes to keys in the active curriculum (CpEE vs Elective Course N).
    raw_passed = data.get("passed_courses") or []
    raw_failed = data.get("failed_courses") or []
    _cv_keys = _curriculum_course_keys_for_request(request)
    payload = {
        "current_sem": data.get("current_sem"),
        "passed_courses": _to_solver_pass_fail_codes(raw_passed, _cv_keys),
        "failed_courses": _to_solver_pass_fail_codes(raw_failed, _cv_keys),
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
    payload["current_sem"] = _clamp_solver_current_sem_to_horizon(payload["current_sem"], request)
    if "configuration" in data:
        payload["configuration"] = data.get("configuration")
    if "alternative_algorithm" in data:
        payload["alternative_algorithm"] = data.get("alternative_algorithm")

    merged_for_policy = _merged_grades_by_section_for_policy(request, data.get("grades_by_section"))
    student = get_logged_in_student(request)
    from .generate_curriculum_context import resolve_generate_curriculum_context

    gctx = resolve_generate_curriculum_context(student) if student is not None else {}
    g_lookup = (gctx.get("course_lookup") if gctx else None) or _course_lookup_from_curriculum()
    viol = _first_excessive_failure_violation(merged_for_policy, g_lookup)
    if viol:
        code, n, title = viol
        msg = _message_excessive_same_course_failures(code, n, title)
        return JsonResponse({"status": "infeasible", "message": msg, "pos": [], "errors": [msg]})

    try:
        result = generate_pos_via_solver(_attach_student_curriculum_to_payload(request, payload))
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
        _sanitize_solver_result_messages(result)
        _translate_pos_courses(result.get("pos"))
        pos_options = result.get("pos_options")
        if isinstance(pos_options, dict):
            for _mode, opt in pos_options.items():
                if isinstance(opt, dict) and opt.get("pos") is not None:
                    _translate_pos_courses(opt.get("pos"))
        warn_pos = result.get("pos")
        if isinstance(pos_options, dict):
            default_mode = result.get("default_petition_mode") or "no_petition"
            opt = pos_options.get(default_mode)
            if isinstance(opt, dict) and opt.get("pos"):
                warn_pos = opt.get("pos")
        result["low_units_warning"] = low_units_warning_for_solver_pos(warn_pos)

    # If called from the authenticated app UI, cache the last generated solver output in session
    # so we can generate a PDF from it on demand.
    try:
        student = get_logged_in_student(request)
        if student is not None and isinstance(result, dict):
            status = str(result.get("status") or "").strip().lower()
            # Keep loaded-table context intact across Generate attempts.
            # This preserves user-edited/loaded semester rows on Generate page.
            if status == "ok":
                request.session["last_generated_pos"] = result.get("pos") or []
                if isinstance(result.get("pos_options"), dict):
                    request.session["last_generated_pos_options"] = result.get("pos_options")
                if result.get("default_petition_mode"):
                    request.session["last_selected_petition_mode"] = result.get("default_petition_mode")
                request.session["last_generated_payload"] = _payload_with_curriculum_audit(payload, request)
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
    return JsonResponse({
        "ok": True,
        "mode": mode,
        "pos": pos,
        "low_units_warning": low_units_warning_for_solver_pos(pos),
    })


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
    _cv_keys = set(course_lookup.keys())
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
            solver_code = _to_solver_course_code(code_s, _cv_keys)
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
    # #region debug-32a60e: show_feasible_targets (backend feasibility) instrumentation
    def _dbg32a60e(hypothesis_id: str, location: str, message: str, data: dict) -> None:
        try:
            log_path = Path(__file__).resolve().parents[2] / "debug-32a60e.log"
            payload = {
                "sessionId": "32a60e",
                "id": f"dbg32a60e_{int(__import__('time').time() * 1000)}",
                "timestamp": int(__import__("time").time() * 1000),
                "runId": "run1",
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
            }
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")
        except Exception:
            # Never break POS preview UX because of debug logging.
            pass

    # #endregion
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    # #region agent log
    _dbg41755c(
        "H2",
        "views.py:feasible_move_marks:parsedBody",
        "request parsed for feasible marks",
        {
            "hasDataDict": isinstance(data, dict),
            "keys": sorted(list(data.keys())) if isinstance(data, dict) else [],
            "hasCourseCode": bool(isinstance(data, dict) and data.get("course_code")),
        },
    )
    # #endregion

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

    _cv_keys = _curriculum_course_keys_for_request(request)

    def _ui_to_solver_code(code_ui: str) -> str:
        return _to_solver_course_code(code_ui, _cv_keys)

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
    # #region agent log
    _dbg41755c(
        "H4",
        "views.py:feasible_move_marks:editableSemesters",
        "computed editable semesters and candidates",
        {
            "editableSemCount": len(editable_sem_indices),
            "candidateCodeCount": len(candidate_codes),
            "courseCodeUi": course_code_ui,
        },
    )
    # #endregion
    if not editable_sem_indices:
        # Nothing is draggable in the current DOM state.
        if course_code_ui:
            return JsonResponse({"ok": True, "feasible_targets": []})
        return JsonResponse({"ok": True, "feasible_courses": []})

    tuned_sem_map_ui = dict(course_to_sem_ui)
    tuned_sem_map_solver = {
        _ui_to_solver_code(code_ui): si for code_ui, si in tuned_sem_map_ui.items()
    }

    # Solver metadata needed for safe, fast pre-pruning (unit caps, offering windows).
    solver_meta = _solver_meta_for_request(request) or {}
    solver_courses = solver_meta.get("courses") if isinstance(solver_meta.get("courses"), dict) else {}
    max_units_for_sem_fn = solver_meta.get("max_units_for_sem")
    config_key: str | None = None

    # Base passed courses are already solver-side keys in the stored payload.
    passed_courses_solver = []
    try:
        passed_courses_solver = list(base_payload.get("passed_courses") or [])
    except Exception:
        passed_courses_solver = []

    with_petition = mode == "with_petition"
    solver_payload = _attach_student_curriculum_to_payload(request, dict(base_payload))
    # Feasible-move highlighting must use exact CP-SAT validity, not heuristic status.
    solver_payload["alternative_algorithm"] = None
    config_key = str(solver_payload.get("configuration") or "")

    # Memoize solver calls within this request.
    feasibility_memo = {}
    # Precompute fingerprints for cross-request caching.
    _base_payload_fp = hashlib.sha256(
        repr(
            (
                int(solver_payload.get("current_sem", 0) or 0),
                tuple(sorted((solver_payload.get("passed_courses") or []))),
                tuple(sorted((solver_payload.get("failed_courses") or []))),
                str(solver_payload.get("configuration") or ""),
                str(solver_payload.get("curriculum_version_id") or ""),
                str(solver_payload.get("approved_json_sha256") or ""),
            )
        ).encode("utf-8")
    ).hexdigest()
    _forced_base_fp = hashlib.sha256(
        repr(tuple(sorted(((forced_semester_map_base or {}).items())))).encode("utf-8")
    ).hexdigest()
    # #region debug-32a60e counter (sample first few solver checks only)
    _dbg32_solver_check_samples = 0
    # #endregion
    _dbg32_cache_hit_logged = 0
    _dbg32_cache_miss_logged = 0

    def _is_solver_feasible_dest(solver_code: str, dest_si: int) -> bool:
        nonlocal _dbg32_cache_hit_logged, _dbg32_cache_miss_logged, _dbg32_solver_check_samples
        key = (solver_code, int(dest_si))
        if key in feasibility_memo:
            return feasibility_memo[key]

        # Cross-request cache lookup: exact CP-SAT feasibility for forced assignments.
        cache_key = (bool(with_petition), _base_payload_fp, _forced_base_fp, solver_code, int(dest_si))
        cached = _feasible_dest_cache_get(cache_key)
        if cached is not None:
            feasibility_memo[key] = bool(cached)
            if _dbg32_cache_hit_logged < 3:
                _dbg32a60e(
                    "H3",
                    "views.py:feasible_move_marks:cache_hit",
                    "feasible dest cache hit",
                    {
                        "solver_code": solver_code,
                        "dest_si": int(dest_si),
                        "cacheValue": bool(cached),
                    },
                )
                _dbg32_cache_hit_logged += 1
            return feasibility_memo[key]

        # --- Validity-preserving pruning (safe rejections only) ---
        # 1) Unit cap pruning for destination semester.
        moved_code_ui = _SOLVER_TO_UI_ELECTIVE.get(solver_code, solver_code)
        moved_units = float(course_units.get(moved_code_ui, 0.0) or 0.0)
        dest_courses_ui = sem_courses_ui.get(int(dest_si), set())
        dest_units_existing = sum(float(course_units.get(code_ui, 0.0) or 0.0) for code_ui in dest_courses_ui)
        if callable(max_units_for_sem_fn):
            cap = int(max_units_for_sem_fn(int(dest_si)))
            # If the moved course is not already in dest, it's being added for this check.
            if cap and (dest_units_existing + moved_units) > cap:
                _feasible_dest_cache_put(cache_key, False)
                feasibility_memo[key] = False
                return feasibility_memo[key]

        # 2) Offering-window pruning (only for config1_baseline to match solver logic).
        if config_key == "config1_baseline":
            cinfo = solver_courses.get(solver_code) or {}
            category = cinfo.get("category", "Rigid")
            orig_sem = cinfo.get("orig_sem")
            if orig_sem is not None:
                try:
                    orig_sem_i = int(orig_sem)
                    sem_type = int(dest_si) % 3
                    orig_type = orig_sem_i % 3
                    allowed = True
                    if category == "Rigid":
                        allowed = sem_type == orig_type
                    elif category == "Bottleneck":
                        allowed = bool(with_petition) or (sem_type == orig_type)
                    elif category == "Flexible":
                        allowed = (
                            (orig_type in [1, 2] and sem_type in [1, 2])
                            or (orig_type == 0 and sem_type == 0)
                        )
                    else:
                        allowed = True
                    if not allowed:
                        _feasible_dest_cache_put(cache_key, False)
                        feasibility_memo[key] = False
                        return feasibility_memo[key]
                except Exception:
                    # If metadata parsing fails, fall back to exact solver check.
                    pass

        forced_map = dict(forced_semester_map_base or {})
        forced_map[solver_code] = int(dest_si)

        result = validate_pos_via_solver(
            payload=solver_payload,
            with_petition=with_petition,
            forced_semester_map=forced_map,
        )
        ok = (result.get("status") == "ok")
        _feasible_dest_cache_put(cache_key, bool(ok))
        if _dbg32_cache_miss_logged < 3:
            _dbg32a60e(
                "H4",
                "views.py:feasible_move_marks:cache_miss",
                "feasible dest cache miss; ran solver",
                {
                    "solver_code": solver_code,
                    "dest_si": int(dest_si),
                    "result_status": result.get("status"),
                },
            )
            _dbg32_cache_miss_logged += 1
        # #region debug-32a60e: log solver return shape
        if _dbg32_solver_check_samples < 3:
            msg = result.get("message")
            try:
                msg_s = (str(msg) if msg is not None else "")[:160]
            except Exception:
                msg_s = ""
            _dbg32a60e(
                "H1",
                "views.py:feasible_move_marks:solver_result_sample",
                "solver returned status/message for forced dest",
                {
                    "solver_code": solver_code,
                    "dest_si": int(dest_si),
                    "result_status": result.get("status"),
                    "message_prefix": msg_s,
                },
            )
            _dbg32_solver_check_samples += 1
        # #endregion
        # #region agent log
        _dbg41755c(
            "H5",
            "views.py:feasible_move_marks:solverCheck",
            "solver feasibility check finished",
            {
                "solverCode": solver_code,
                "destSemester": int(dest_si),
                "status": result.get("status"),
                "ok": bool(ok),
            },
        )
        # #endregion
        feasibility_memo[key] = bool(ok)
        return feasibility_memo[key]

    if course_code_ui:
        if isinstance(course_code_ui, str):
            _dbg32a60e(
                "H0",
                "views.py:feasible_move_marks:course_code_branch",
                "course_code present; logging feasibility targets computation",
                {
                    "mode": mode,
                    "course_code_ui": course_code_ui,
                    "editable_sem_indices": editable_sem_indices,
                    "candidate_codes_count": len(candidate_codes),
                },
            )
        src_si = course_to_sem_ui.get(course_code_ui)
        if src_si is None:
            return JsonResponse({"ok": True, "feasible_targets": []})

        solver_code = _ui_to_solver_code(course_code_ui)
        feasible_targets = set()
        _checked_dest_count = 0
        _logged_dest_samples = 0

        for dest_si in editable_sem_indices:
            if dest_si == src_si:
                continue
            if course_code_ui in sem_courses_ui.get(dest_si, set()):
                continue

            if _is_solver_feasible_dest(solver_code, dest_si):
                if _logged_dest_samples < 3:
                    _dbg32a60e(
                        "H2",
                        "views.py:feasible_move_marks:dest_check_sample",
                        "solver returned status ok for this forced destination",
                        {
                            "solver_code": solver_code,
                            "dest_si": int(dest_si),
                            "check_index": _checked_dest_count,
                        },
                    )
                    _logged_dest_samples += 1
                feasible_targets.add(int(dest_si))
            _checked_dest_count += 1

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
def feasible_add_courses_for_section(request):
    """
    Backend endpoint used by the `/generate` page to filter the `+ Add courses`
    modal by CP-SAT feasibility (exact validity) given the student's current
    passed/failed grade profile.

    Request JSON:
      - section_index: int (UI semester index, 0-based)
      - with_petition: bool (optional)
      - solver_payload: { current_sem, passed_courses, failed_courses, ... }
      - candidate_codes: [<UI course code strings>] to test

    Response JSON:
      - { ok: bool, feasible_courses: [<UI course code strings>] }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"ok": False, "error": "Expected JSON object"}, status=400)

    section_index_raw = data.get("section_index")
    try:
        section_index = int(section_index_raw)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid section_index"}, status=400)

    with_petition = bool(data.get("with_petition", True))
    solver_payload = data.get("solver_payload") or {}
    if not isinstance(solver_payload, dict):
        return JsonResponse({"ok": False, "error": "solver_payload must be an object"}, status=400)

    candidate_codes = data.get("candidate_codes") or []
    if not isinstance(candidate_codes, list):
        return JsonResponse({"ok": False, "error": "candidate_codes must be an array"}, status=400)

    # Translate UI-elective codes (CpEE 401/402/403) into solver course keys.
    raw_passed = solver_payload.get("passed_courses") or []
    raw_failed = solver_payload.get("failed_courses") or []
    if not isinstance(raw_passed, list):
        raw_passed = []
    if not isinstance(raw_failed, list):
        raw_failed = []

    _cv_keys = _curriculum_course_keys_for_request(request)
    passed_courses_solver = _to_solver_pass_fail_codes(raw_passed, _cv_keys)
    failed_courses_solver = _to_solver_pass_fail_codes(raw_failed, _cv_keys)

    try:
        current_sem = int(solver_payload.get("current_sem"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "solver_payload.current_sem must be an integer"}, status=400)

    # UI section_index is 0-based, solver semesters are 1-based.
    forced_solver_sem = section_index + 1

    # Base payload for validation checks. We only add the forced assignment map.
    payload_current = {
        "current_sem": current_sem,
        "passed_courses": passed_courses_solver,
        "failed_courses": failed_courses_solver,
        # Ensure we run exact CP-SAT for feasibility decisions.
        "alternative_algorithm": None,
    }

    # Preserve optional configuration if supplied by the client.
    if "configuration" in solver_payload:
        payload_current["configuration"] = solver_payload.get("configuration")
    if "curriculum_version_id" in solver_payload:
        payload_current["curriculum_version_id"] = solver_payload.get("curriculum_version_id")
    if "approved_json_sha256" in solver_payload:
        payload_current["approved_json_sha256"] = solver_payload.get("approved_json_sha256")
    payload_current = _attach_student_curriculum_to_payload(request, payload_current)

    # Fingerprint base payload for stable cache keys.
    _base_payload_fp = hashlib.sha256(
        repr(
            (
                current_sem,
                tuple(sorted(passed_courses_solver)),
                tuple(sorted(failed_courses_solver)),
                str(payload_current.get("curriculum_version_id") or ""),
                str(payload_current.get("approved_json_sha256") or ""),
            )
        ).encode("utf-8")
    ).hexdigest()

    feasibility_memo: dict[str, bool] = {}
    feasible_courses: set[str] = set()

    for code_ui in candidate_codes:
        if code_ui is None:
            continue
        code_ui_s = str(code_ui).strip()
        if not code_ui_s:
            continue
        if code_ui_s in feasibility_memo:
            if feasibility_memo[code_ui_s]:
                feasible_courses.add(code_ui_s)
            continue

        solver_code = _to_solver_course_code(code_ui_s, _cv_keys)

        cache_key = (bool(with_petition), forced_solver_sem, _base_payload_fp, code_ui_s)
        cached = _feasible_add_courses_cache_get(cache_key)
        if cached is not None:
            feasibility_memo[code_ui_s] = bool(cached)
            if cached:
                feasible_courses.add(code_ui_s)
            continue

        forced_map = {solver_code: forced_solver_sem}
        try:
            result = validate_pos_via_solver(
                payload=payload_current,
                with_petition=with_petition,
                forced_semester_map=forced_map,
            )
            ok = (result.get("status") == "ok")
        except Exception:
            ok = False

        feasibility_memo[code_ui_s] = bool(ok)
        _feasible_add_courses_cache_put(cache_key, bool(ok))
        if ok:
            feasible_courses.add(code_ui_s)

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
    solver_payload = _attach_student_curriculum_to_payload(request, dict(base_payload))
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
    message = _user_facing_solver_message(result.get("message") or "Schedule is infeasible.")

    MAX_MOVED_COURSES = 8

    # Prefer solver-provided cap function to avoid mismatches.
    solver_meta = _solver_meta_for_request(request) or {}
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

    _cv_keys_diag = set(solver_courses.keys()) if solver_courses else set()

    # Solver-semester map keyed by solver course codes (not UI codes)
    def _ui_to_solver_code(code_ui: str) -> str:
        return _to_solver_course_code(code_ui, _cv_keys_diag)

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
    solver_payload = _attach_student_curriculum_to_payload(request, dict(base_payload))
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
                "message": _user_facing_solver_message(result.get("message") or "Schedule is infeasible."),
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
        "pdf_header": _pdf_header_for_student(student),
    }


def _persist_pos_history_on_download(request, student):
    """Append a history row when the user downloads Preview PDF (selected petition variant)."""
    snap = _snapshot_for_pos_history(request, student)
    request.session["last_generated_pos"] = snap["generated_pos"]
    request.session["last_selected_petition_mode"] = snap["petition_mode"]
    request.session["last_generated_payload"] = snap["last_generated_payload"]
    yl = request.session.get("year_level") or ""
    sem = request.session.get("semester") or ""
    cv_pk, sha = _session_curriculum_audit_for_pos_record(request)
    cv_obj = CurriculumVersion.objects.filter(pk=cv_pk).first() if cv_pk is not None else None
    POSRecord.objects.create(
        student=student,
        academic_year=request.session.get("academic_year") or "",
        semester=sem,
        year_level=yl,
        grades_snapshot=snap["grades_by_section"],
        preview_snapshot=snap,
        curriculum_version=cv_obj,
        approved_json_sha256=sha,
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
    student = _student_with_program_curriculum(get_logged_in_student(request))
    if (
        not _has_academic_term(request)
        or not _has_profile_photo(request)
        or not _has_program_and_curriculum(student)
    ):
        return redirect(reverse('pos_home') + '?required=1')
    grades_by_section = _saved_grades_by_section(student)
    from .generate_curriculum_context import resolve_generate_curriculum_context

    gctx = resolve_generate_curriculum_context(student)
    if gctx.get("mode") == "db":
        preview_sections = _preview_sections_from_resolved_sections(
            gctx["curriculum_sections"], gctx["course_lookup"], grades_by_section
        )
    else:
        preview_sections = _preview_sections_from_curriculum(grades_by_section)
    generated_pos = request.session.get("last_generated_pos") or []

    def _has_generated_schedule(pos_rows):
        if not isinstance(pos_rows, list):
            return False
        for sem in pos_rows:
            if not isinstance(sem, dict):
                continue
            for c in (sem.get("courses") or []):
                if isinstance(c, dict) and (c.get("code") or "").strip():
                    return True
        return False

    has_generated_preview = _has_generated_schedule(generated_pos)
    if not has_generated_preview:
        # New users (or users who have not generated POS in this session) should
        # see an empty-state prompt instead of curriculum preview tables.
        preview_sections = []
    # For solver-generated future semesters we only get course codes back.
    # Provide a lookup so the preview tables can still show lec/lab/pre/core fields.
    if gctx.get("mode") == "db":
        course_lookup_tuple = gctx["course_lookup"]
    else:
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
    signatory_options = _get_signatory_options_map()
    session_signatories = request.session.get("preview_signatories") or {}
    student_adviser_key = f"adviser_name_student_{student.pk}"
    stored_adviser = _get_saved_adviser_for_student(student) or (request.session.get(student_adviser_key) or "").strip()
    if stored_adviser:
        session_signatories = dict(session_signatories)
        session_signatories["adviser"] = stored_adviser
    selected_signatories = _resolve_signatories(
        request,
        body={"signatories": session_signatories},
    )
    adviser_names = [item["name"] for item in signatory_options.get(SignatoryOption.ROLE_ADVISER, [])]
    if selected_signatories.get("adviser") not in adviser_names:
        selected_signatories["adviser"] = ""

    # Department/Program chairs are global fixed defaults.
    dep_options = signatory_options.get(SignatoryOption.ROLE_DEPARTMENT_CHAIR, [])
    prog_options = signatory_options.get(SignatoryOption.ROLE_PROGRAM_CHAIR, [])
    selected_signatories["department_chair"] = dep_options[0]["name"] if dep_options else ""
    selected_signatories["program_chair"] = prog_options[0]["name"] if prog_options else ""
    low_units_warning = (
        low_units_warning_for_solver_pos(generated_pos) if has_generated_preview else None
    )
    context = {
        'student': student,
        'curriculum_display_title': gctx.get("display_title") or "",
        'preview_sections': preview_sections,
        'course_lookup': course_lookup,
        'session_academic_year': request.session.get('academic_year', ''),
        'session_semester': request.session.get('semester', ''),
        'signatory_options': signatory_options,
        'selected_signatories': selected_signatories,
        'has_generated_preview': has_generated_preview,
        'low_units_warning': low_units_warning,
        'low_units_warning_message': LOW_UNITS_WARNING_MESSAGE,
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
    signatories = _resolve_signatories(request, required=True)
    if not all(signatories.values()):
        return HttpResponseBadRequest("Please select Adviser, Department Chair, and Program Chair before downloading PDF.")
    if signatories.get("adviser"):
        request.session[f"adviser_name_student_{student.pk}"] = signatories["adviser"]
        _set_saved_adviser_for_student(student, signatories["adviser"])
    request.session["preview_signatories"] = dict(signatories)

    # Match Preview exactly: past/current semesters come from `preview_sections`,
    # while future semesters are appended from the last solver-generated POS.
    from .generate_curriculum_context import resolve_generate_curriculum_context

    gctx = resolve_generate_curriculum_context(student)
    if gctx.get("mode") == "db":
        preview_sections = _preview_sections_from_resolved_sections(
            gctx["curriculum_sections"], gctx["course_lookup"], grades_by_section
        )
    else:
        preview_sections = _preview_sections_from_curriculum(grades_by_section)
    generated_pos = request.session.get("last_generated_pos") or []

    from .pos_pdf import build_preview_pos_pdf
    logo_path = os.path.abspath(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'static', 'img', 'BSU_Logo.png')))
    if not os.path.isfile(logo_path):
        logo_path = None
    requested_name_raw = (request.GET.get("pos_file_name") or "").strip()
    requested_name = re.sub(r'[\\/:*?"<>|]+', "_", requested_name_raw).strip().strip(".")
    if not requested_name:
        academic_year_safe = (academic_year or '').replace("–", "-").replace(" ", "_")
        sr_code_safe = student.sr_code or 'SR'
        requested_name = f"{academic_year_safe} {sr_code_safe}"
    pdf_header = _pdf_header_for_student(student)
    pdf_bytes = build_preview_pos_pdf(
        student_name=student.full_name or '',
        sr_code=student.sr_code or '',
        academic_year=academic_year,
        boundary_section_index=preview_boundary_section_index,
        preview_sections=preview_sections,
        future_pos=generated_pos,
        logo_path=logo_path,
        adviser_name=signatories["adviser"],
        department_chair_name=signatories["department_chair"],
        program_chair_name=signatories["program_chair"],
        pdf_department_label=pdf_header["department"],
        pdf_program_curriculum=pdf_header["program_curriculum"],
        pdf_program_shortcut=pdf_header["program_shortcut"],
    )
    filename = f"{requested_name}.pdf"
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
    pos_file_name = (body.get("pos_file_name") or "").strip()
    if not pos_file_name:
        return JsonResponse({"ok": False, "error": "POS file name is required."}, status=400)
    signatories = _resolve_signatories(request, body=body, required=True)
    if not all(signatories.values()):
        return JsonResponse({"ok": False, "error": "Please select Adviser, Department Chair, and Program Chair before saving."}, status=400)
    if signatories.get("adviser"):
        request.session[f"adviser_name_student_{student.pk}"] = signatories["adviser"]
        _set_saved_adviser_for_student(student, signatories["adviser"])
    request.session["preview_signatories"] = dict(signatories)
    preview_ui_sections = body.get("preview_ui_sections") or body.get("preview_sections") or {}
    if isinstance(preview_ui_sections, dict):
        # Keys should be UI section indices; JSON forces them to be strings.
        snap["preview_ui_sections"] = preview_ui_sections
    snap["pos_file_name"] = pos_file_name
    snap["signatories"] = signatories
    last = POSRecord.objects.filter(student=student).order_by('-generated_at').first()
    if last is not None and last.preview_snapshot == snap:
        return JsonResponse({"ok": True, "saved": False, "record_id": last.pk, "pos_file_name": pos_file_name})

    yl = request.session.get('year_level') or ''
    sem = request.session.get('semester') or ''
    cv_pk, sha = _session_curriculum_audit_for_pos_record(request)
    cv_obj = CurriculumVersion.objects.filter(pk=cv_pk).first() if cv_pk is not None else None
    record = POSRecord.objects.create(
        student=student,
        academic_year=request.session.get('academic_year') or '',
        semester=sem,
        year_level=yl,
        grades_snapshot=snap.get("grades_by_section") or {},
        preview_snapshot=snap,
        curriculum_version=cv_obj,
        approved_json_sha256=sha,
    )
    _prune_old_pos_records(student)

    return JsonResponse({"ok": True, "saved": True, "record_id": record.pk, "pos_file_name": pos_file_name})


@_require_student
def get_signatory_options(request):
    options_map = _get_signatory_options_map()
    return JsonResponse({"ok": True, "options": options_map})


@csrf_exempt
@_require_student
@require_POST
def add_signatory_option(request):
    try:
        body_raw = request.body.decode("utf-8") if request.body else ""
        body = json.loads(body_raw) if body_raw.strip() else {}
    except Exception:
        body = {}

    role = (body.get("role") or "").strip()
    name = (body.get("name") or "").strip()
    admin_password = (body.get("admin_password") or "").strip()
    if admin_password != SIGNATORY_ADMIN_PASSWORD:
        return JsonResponse({"ok": False, "error": "Invalid admin password."}, status=403)
    if role not in SIGNATORY_ROLES:
        return JsonResponse({"ok": False, "error": "Invalid role."}, status=400)
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    try:
        option, created = SignatoryOption.objects.get_or_create(role=role, name=name)
    except (OperationalError, ProgrammingError):
        return JsonResponse({"ok": False, "error": "Signatory storage is not ready yet. Please run migrations."}, status=500)
    return JsonResponse(
        {
            "ok": True,
            "created": created,
            "option": {"id": option.id, "name": option.name, "role": option.role},
            "options": _get_signatory_options_map(),
        }
    )


@csrf_exempt
@_require_student
@require_POST
def delete_signatory_option(request):
    try:
        body_raw = request.body.decode("utf-8") if request.body else ""
        body = json.loads(body_raw) if body_raw.strip() else {}
    except Exception:
        body = {}

    role = (body.get("role") or "").strip()
    option_id = body.get("id")
    admin_password = (body.get("admin_password") or "").strip()
    if admin_password != SIGNATORY_ADMIN_PASSWORD:
        return JsonResponse({"ok": False, "error": "Invalid admin password."}, status=403)
    if role not in SIGNATORY_ROLES:
        return JsonResponse({"ok": False, "error": "Invalid role."}, status=400)
    if option_id in (None, ""):
        return JsonResponse({"ok": False, "error": "Option id is required."}, status=400)

    try:
        option = SignatoryOption.objects.get(pk=int(option_id), role=role)
    except (ValueError, TypeError, SignatoryOption.DoesNotExist):
        return JsonResponse({"ok": False, "error": "Signatory option not found."}, status=404)
    except (OperationalError, ProgrammingError):
        return JsonResponse({"ok": False, "error": "Signatory storage is not ready yet. Please run migrations."}, status=500)

    try:
        option.delete()
    except (OperationalError, ProgrammingError):
        return JsonResponse({"ok": False, "error": "Signatory storage is not ready yet. Please run migrations."}, status=500)
    return JsonResponse({"ok": True, "options": _get_signatory_options_map()})


@csrf_exempt
@_require_student
@require_POST
def update_signatory_option(request):
    try:
        body_raw = request.body.decode("utf-8") if request.body else ""
        body = json.loads(body_raw) if body_raw.strip() else {}
    except Exception:
        body = {}

    role = (body.get("role") or "").strip()
    option_id = body.get("id")
    new_name = (body.get("name") or "").strip()
    admin_password = (body.get("admin_password") or "").strip()
    if admin_password != SIGNATORY_ADMIN_PASSWORD:
        return JsonResponse({"ok": False, "error": "Invalid admin password."}, status=403)
    if role not in SIGNATORY_ROLES:
        return JsonResponse({"ok": False, "error": "Invalid role."}, status=400)
    if option_id in (None, ""):
        return JsonResponse({"ok": False, "error": "Option id is required."}, status=400)
    if not new_name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    try:
        option = SignatoryOption.objects.get(pk=int(option_id), role=role)
    except (ValueError, TypeError, SignatoryOption.DoesNotExist):
        return JsonResponse({"ok": False, "error": "Signatory option not found."}, status=404)
    except (OperationalError, ProgrammingError):
        return JsonResponse({"ok": False, "error": "Signatory storage is not ready yet. Please run migrations."}, status=500)

    try:
        duplicate = SignatoryOption.objects.filter(role=role, name=new_name).exclude(pk=option.pk).exists()
        if duplicate:
            return JsonResponse({"ok": False, "error": "That name already exists."}, status=400)
        old_name = option.name
        option.name = new_name
        option.save(update_fields=["name"])

        # Keep per-account saved adviser names in sync when a name is edited.
        try:
            SignatoryPreference.objects.filter(adviser_name=old_name).update(adviser_name=new_name)
        except Exception:
            pass
    except (OperationalError, ProgrammingError):
        return JsonResponse({"ok": False, "error": "Signatory storage is not ready yet. Please run migrations."}, status=500)

    return JsonResponse({"ok": True, "options": _get_signatory_options_map()})


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
        signatories_raw = snap.get("signatories") if isinstance(snap.get("signatories"), dict) else {}
        signatories = _resolve_signatories(
            request,
            body={"signatories": signatories_raw},
            required=False,
        )
        from .generate_curriculum_context import resolve_generate_curriculum_context

        gctx = resolve_generate_curriculum_context(student)
        if gctx.get("mode") == "db":
            preview_sections = _preview_sections_from_resolved_sections(
                gctx["curriculum_sections"], gctx["course_lookup"], grades_by_section
            )
        else:
            preview_sections = _preview_sections_from_curriculum(grades_by_section)
        from .pos_pdf import build_preview_pos_pdf
        pdf_header = _pdf_header_for_student(student, snapshot=snap)
        pdf_bytes = build_preview_pos_pdf(
            student_name=student.full_name or '',
            sr_code=student.sr_code or '',
            academic_year=academic_year,
            boundary_section_index=boundary,
            preview_sections=preview_sections,
            future_pos=generated_pos,
            logo_path=logo_path,
            adviser_name=signatories["adviser"],
            department_chair_name=signatories["department_chair"],
            program_chair_name=signatories["program_chair"],
            pdf_department_label=pdf_header["department"],
            pdf_program_curriculum=pdf_header["program_curriculum"],
            pdf_program_shortcut=pdf_header["program_shortcut"],
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
    date_str = eval_date.strftime('%d-%m-%Y')
    saved_name_raw = (snap.get("pos_file_name") if isinstance(snap, dict) else "") or ""
    saved_name = re.sub(r'[\\/:*?"<>|]+', "_", str(saved_name_raw)).strip().strip(".")
    if not saved_name:
        saved_name = f"POS_{student.sr_code or 'POS'}"
    filename = f"{saved_name}_{date_str}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@_require_student
def load_pos_record(request, record_id):
    """Restore a history record into Generate POS (full grades + preview POS); user must re-select Last Semester Enrolled (and First Failed Semester if used)."""
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
            merged = copy.deepcopy(lp)
            if record.curriculum_version_id and merged.get("curriculum_version_id") is None:
                merged["curriculum_version_id"] = int(record.curriculum_version_id)
            if getattr(record, "approved_json_sha256", None) and not merged.get("approved_json_sha256"):
                merged["approved_json_sha256"] = str(record.approved_json_sha256).strip()[:64]
            request.session["last_generated_payload"] = merged
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
    """Clear student session and Django auth, then return to login."""
    logout(request)
    request.session.flush()
    return redirect('login')


def _safe_login_next(raw: str | None) -> str:
    nxt = (raw or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return ""


def login_view(request):
    """Login page — SR-code + password for students and curriculum staff."""
    next_value = _safe_login_next(request.GET.get("next") or request.POST.get("next"))

    if request.method == "GET":
        if request.user.is_authenticated and user_may_use_staff_curriculum_portal(request.user):
            if next_value:
                return redirect(next_value)
            return redirect("staff_home")
        if get_logged_in_student(request) is not None:
            return redirect("pos_home")
        return render(request, "auth/login.html", {"next": next_value})

    sr_code = (request.POST.get("sr_code") or "").strip()
    password = request.POST.get("password") or ""
    next_value = _safe_login_next(request.POST.get("next"))

    if not sr_code:
        return render(request, "auth/login.html", {"error": "Please enter your SR-Code.", "next": next_value})
    if not SR_CODE_REGEX.match(sr_code):
        return render(
            request,
            "auth/login.html",
            {"error": "Please enter a valid SR-Code (e.g. 23-00000).", "next": next_value},
        )
    if not password:
        return render(request, "auth/login.html", {"error": "Please enter your password.", "next": next_value})

    staff_user = authenticate(request, username=sr_code, password=password)
    if staff_user is not None and not user_may_use_staff_curriculum_portal(staff_user):
        staff_user = None

    student = None
    try:
        candidate = Student.objects.get(sr_code=sr_code)
        if candidate.check_password(password):
            student = candidate
    except Student.DoesNotExist:
        pass

    if staff_user is None and student is None:
        return render(
            request,
            "auth/login.html",
            {"error": "Invalid SR-Code or password.", "next": next_value},
        )

    if staff_user is not None:
        login(request, staff_user)
        if next_value:
            return redirect(next_value)
        return redirect("staff_home")

    request.session["student_id"] = student.pk
    ay_stored = (student.academic_year or "").strip()
    if ay_stored in _VALID_ACADEMIC_YEAR_VALUES:
        request.session["academic_year"] = ay_stored
    if student.semester:
        request.session["semester"] = student.semester
    if student.year_level:
        request.session["year_level"] = student.year_level
    return redirect("pos_home")


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
            errors.append(
                'Please enter a valid BSU email local part (letters, numbers, dots, _ or -; no spaces), '
                'e.g. 23-00000 or knudcen.gradly.de.villa, with domain @g.batstate-u.edu.ph.'
            )
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


@ensure_csrf_cookie
@_require_student
def curriculum_choice(request):
    """List approved curricula for the student's program and persist selected_curriculum_version (basic Phase 2 UI)."""
    return render(request, "pos/curriculum_choice.html", {})


@require_http_methods(["GET"])
@_require_student_api
def api_curriculum_me(request):
    student = get_logged_in_student(request)
    if student is None:
        return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
    try:
        student = Student.objects.select_related("program", "selected_curriculum_version").get(pk=student.pk)
    except Student.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
    try:
        return JsonResponse(_curriculum_me_payload(student))
    except (OperationalError, ProgrammingError):
        return JsonResponse(
            {"ok": False, "error": "Curriculum storage is not ready (run migrations)."},
            status=500,
        )


@require_POST
@_require_student_api
def api_curriculum_set_active(request):
    """Students cannot change their active curriculum; staff assign via admin or staff portal."""
    return JsonResponse(
        {
            "ok": False,
            "error": "Please coordinate with your academic supervisor to change your current active Program of Study.",
        },
        status=403,
    )


def contact_us(request):
    """Contact Us page"""
    return render(request, "pos/contact_us.html")


def help_page(request):
    """Help page"""
    return render(request, "pos/help.html")
