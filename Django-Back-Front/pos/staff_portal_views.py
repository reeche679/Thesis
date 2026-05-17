"""
Staff portal home page and student assignment APIs.

GET  /staff/                       — homepage (staff_home)
GET  /staff/api/students/          — list all students (search, program filter)
PATCH /staff/api/students/<pk>/    — assign program + approved curriculum version
GET  /staff/api/programs/          — list all programs (for dropdowns)
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from accounts.models import Student
from .models import CurriculumVersion, Program
from .staff_auth_views import user_may_use_staff_curriculum_portal


def _programs_all() -> list[Program]:
    return list(Program.objects.order_by("name"))


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
        return redirect("staff_login")
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
    programs = [
        {"id": p.pk, "slug": p.slug, "name": p.name}
        for p in _programs_all()
    ]
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


@require_http_methods(["GET"])
def staff_api_programs_list(request):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Not authenticated."}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)
    programs = [
        {"id": p.pk, "slug": p.slug, "name": p.name}
        for p in _programs_all()
    ]
    return JsonResponse({"ok": True, "programs": programs})
