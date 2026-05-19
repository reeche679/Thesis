"""
Staff portal home page and student assignment APIs.

GET  /staff/                       — homepage (staff_home)
GET  /staff/api/students/          — list all students (search, program filter)
PATCH /staff/api/students/<pk>/    — assign program + approved curriculum version
GET  /staff/api/programs/          — list programs (GET) or create (POST)
PATCH /staff/api/programs/<pk>/    — update program name
POST /staff/api/programs/<pk>/delete/ — delete program
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.text import slugify
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from accounts.models import Student
from .models import CurriculumVersion, Program
from .staff_auth_views import staff_login_url, user_may_use_staff_curriculum_portal


def _portal_forbidden(request) -> JsonResponse | None:
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Not authenticated."}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)
    return None


def _programs_all() -> list[Program]:
    return list(
        Program.objects.annotate(
            student_count=Count("students", distinct=True),
            curriculum_version_count=Count("curriculum_versions", distinct=True),
        ).order_by("name")
    )


def _program_row(p: Program) -> dict[str, Any]:
    student_count = getattr(p, "student_count", None)
    if student_count is None:
        student_count = p.students.count()
    cv_count = getattr(p, "curriculum_version_count", None)
    if cv_count is None:
        cv_count = p.curriculum_versions.count()
    return {
        "id": p.pk,
        "slug": p.slug,
        "name": p.name,
        "description": p.description or "",
        "is_active": p.is_active,
        "student_count": student_count,
        "curriculum_version_count": cv_count,
    }


def _unique_program_slug(base: str) -> str:
    root = slugify(base)[:64] or "program"
    if not Program.objects.filter(slug=root).exists():
        return root
    n = 2
    while True:
        candidate = f"{root}-{n}"[:64]
        if not Program.objects.filter(slug=candidate).exists():
            return candidate
        n += 1


def _student_row(s: Student) -> dict[str, Any]:
    cv = s.selected_curriculum_version
    prog = s.program
    mi = (s.middle_name.strip() or "")[:1]
    name = f"{s.last_name}, {s.first_name}" + (f" {mi}." if mi else "")
    return {
        "id": s.pk,
        "sr_code": s.sr_code,
        "name": name,
        "email": s.email,
        "year_level": s.year_level or "",
        "program_id": prog.pk if prog else None,
        "program_slug": prog.slug if prog else "",
        "program_name": prog.name if prog else "",
        "curriculum_version_id": cv.pk if cv else None,
        "curriculum_version_label": (cv.version_label or f"version {cv.pk}") if cv else "",
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@ensure_csrf_cookie
@login_required
@require_http_methods(["GET"])
def staff_home_page(request):
    if not user_may_use_staff_curriculum_portal(request.user):
        return redirect(staff_login_url(next_path=request.get_full_path()))
    programs = _programs_all()
    return render(request, "pos/staff_home.html", {"programs": programs})


# ---------------------------------------------------------------------------
# APIs
# ---------------------------------------------------------------------------

@require_http_methods(["GET"])
def staff_api_students_list(request):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Not authenticated."}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)

    qs = Student.objects.select_related("program", "selected_curriculum_version").order_by("sr_code")

    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(sr_code__icontains=q)
            | Q(last_name__icontains=q)
            | Q(first_name__icontains=q)
            | Q(email__icontains=q)
        )

    program_id = request.GET.get("program_id")
    if program_id:
        try:
            qs = qs.filter(program_id=int(program_id))
        except (TypeError, ValueError):
            pass

    students = [_student_row(s) for s in qs[:500]]
    programs = [_program_row(p) for p in _programs_all()]
    return JsonResponse({"ok": True, "students": students, "programs": programs})


@require_http_methods(["PATCH"])
def staff_api_student_patch(request, pk: int):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Not authenticated."}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)

    try:
        student = Student.objects.select_related("program", "selected_curriculum_version").get(pk=pk)
    except Student.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Student not found."}, status=404)

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)

    update_fields: list[str] = []

    # --- program ---
    if "program_id" in body:
        pid = body["program_id"]
        if pid is None or pid == "":
            student.program = None
            student.selected_curriculum_version = None
            update_fields += ["program", "selected_curriculum_version"]
        else:
            try:
                prog = Program.objects.get(pk=int(pid))
            except (Program.DoesNotExist, TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "Program not found."}, status=400)
            student.program = prog
            update_fields.append("program")
            # If version is set but belongs to a different program, clear it
            if student.selected_curriculum_version_id:
                if student.selected_curriculum_version.program_id != prog.pk:
                    student.selected_curriculum_version = None
                    update_fields.append("selected_curriculum_version")

    # --- curriculum version ---
    if "curriculum_version_id" in body:
        cvid = body["curriculum_version_id"]
        if cvid is None or cvid == "":
            student.selected_curriculum_version = None
            if "selected_curriculum_version" not in update_fields:
                update_fields.append("selected_curriculum_version")
        else:
            try:
                cv = CurriculumVersion.objects.select_related("program").get(pk=int(cvid))
            except (CurriculumVersion.DoesNotExist, TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "Curriculum version not found."}, status=400)
            if cv.status != CurriculumVersion.STATUS_APPROVED:
                return JsonResponse(
                    {"ok": False, "error": "Only approved curriculum versions can be assigned."},
                    status=400,
                )
            if student.program_id and cv.program_id != student.program_id:
                return JsonResponse(
                    {"ok": False, "error": "Curriculum version does not belong to the student's program."},
                    status=400,
                )
            student.selected_curriculum_version = cv
            if "selected_curriculum_version" not in update_fields:
                update_fields.append("selected_curriculum_version")

    if update_fields:
        student.save(update_fields=update_fields)

    return JsonResponse({"ok": True, "student": _student_row(student)})


@require_http_methods(["GET", "POST"])
def staff_api_programs_list(request):
    denied = _portal_forbidden(request)
    if denied is not None:
        return denied

    if request.method == "GET":
        programs = [_program_row(p) for p in _programs_all()]
        return JsonResponse({"ok": True, "programs": programs})

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)

    name = (body.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Program name is required."}, status=400)
    if len(name) > 200:
        return JsonResponse({"ok": False, "error": "Program name is too long (max 200 characters)."}, status=400)

    raw_slug = (body.get("slug") or "").strip()
    if raw_slug:
        slug = slugify(raw_slug)[:64]
        if not slug:
            return JsonResponse({"ok": False, "error": "Slug is invalid."}, status=400)
        if not re.match(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", slug):
            return JsonResponse(
                {"ok": False, "error": "Slug must use lowercase letters, numbers, hyphens, and underscores only."},
                status=400,
            )
        if Program.objects.filter(slug=slug).exists():
            return JsonResponse({"ok": False, "error": f"A program with slug “{slug}” already exists."}, status=400)
    else:
        slug = _unique_program_slug(name)

    description = (body.get("description") or "").strip()
    program = Program.objects.create(name=name, slug=slug, description=description)
    return JsonResponse({"ok": True, "program": _program_row(program)}, status=201)


@require_http_methods(["PATCH"])
def staff_api_program_patch(request, pk: int):
    denied = _portal_forbidden(request)
    if denied is not None:
        return denied

    try:
        program = Program.objects.get(pk=pk)
    except Program.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Program not found."}, status=404)

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)

    name = (body.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Program name is required."}, status=400)
    if len(name) > 200:
        return JsonResponse({"ok": False, "error": "Program name is too long (max 200 characters)."}, status=400)

    program.name = name
    program.save(update_fields=["name"])
    return JsonResponse({"ok": True, "program": _program_row(program)})


@require_http_methods(["POST"])
def staff_api_program_delete(request, pk: int):
    denied = _portal_forbidden(request)
    if denied is not None:
        return denied

    try:
        program = Program.objects.get(pk=pk)
    except Program.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Program not found."}, status=404)

    student_count = program.students.count()
    cv_count = program.curriculum_versions.count()
    program_name = program.name
    program.delete()
    return JsonResponse(
        {
            "ok": True,
            "deleted_program": program_name,
            "deleted_curriculum_versions": cv_count,
            "unassigned_students": student_count,
        }
    )
