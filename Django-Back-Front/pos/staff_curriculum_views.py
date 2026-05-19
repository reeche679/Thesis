"""
Phase 5–6 — staff-only curriculum PDF upload, draft extraction, analyze, review APIs (Django User auth).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST

from pos.admin import validate_approved_bundle
from pos.curriculum_draft_analyze import analyze_draft_json
from pos.curriculum_draft_to_approved import draft_to_approved_json
from pos.curriculum_pdf_extract import _canonical_course_code, extract_curriculum_rows_from_pdf
from pos.models import CurriculumVersion, Program
from pos.permissions import user_can_manage_program_curriculum
from pos.staff_auth_views import staff_login_url, user_may_use_staff_curriculum_portal

MAX_PDF_BYTES = 25 * 1024 * 1024


def _programs_for_staff(user) -> list[Program]:
    if getattr(user, "is_superuser", False):
        return list(Program.objects.order_by("slug"))
    return list(
        Program.objects.filter(curriculum_managers=user, is_active=True).distinct().order_by("slug")
    )


def _curriculum_versions_queryset(user):
    """All CurriculumVersions the user is allowed to manage, newest first."""
    qs = CurriculumVersion.objects.select_related("program").order_by("-updated_at")
    if getattr(user, "is_superuser", False):
        return qs
    return qs.filter(program__curriculum_managers=user).distinct()


def _require_cv_staff(request, pk: int):
    """Auth + program access check without requiring draft status. Returns (cv, error_response)."""
    if not request.user.is_authenticated:
        return None, JsonResponse({"ok": False, "error": "Not authenticated."}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return None, JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)
    cv = get_object_or_404(CurriculumVersion.objects.select_related("program"), pk=pk)
    if not user_can_manage_program_curriculum(request.user, cv.program):
        return None, JsonResponse({"ok": False, "error": "Not allowed for this program."}, status=403)
    return cv, None


def _assign_draft_row_ids(rows: list) -> None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not str(r.get("draft_row_id") or "").strip():
            r["draft_row_id"] = str(uuid.uuid4())


def _validate_extracted_rows(rows: list) -> None:
    if not isinstance(rows, list):
        raise ValidationError("extracted_rows must be a list.")
    seen: set[str] = set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ValidationError(f"extracted_rows[{i}] must be an object.")
        code = str(r.get("code") or "").strip()
        if not code:
            raise ValidationError(f"extracted_rows[{i}].code is required.")
        canon = _canonical_course_code(code)
        if canon in seen:
            raise ValidationError(f"Duplicate course code: {canon}")
        seen.add(canon)
        rid = str(r.get("draft_row_id") or "").strip()
        if not rid:
            raise ValidationError(f"extracted_rows[{i}].draft_row_id is required.")
        cat = str(r.get("category") or "").strip()
        if cat and cat not in ("Rigid", "Bottleneck", "Flexible"):
            raise ValidationError(
                f"extracted_rows[{i}].category must be Rigid, Bottleneck, or Flexible when set."
            )
        for key in ("units", "year_level", "orig_sem", "orig_sem_override"):
            if key not in r or r[key] in (None, ""):
                continue
            if key == "units":
                try:
                    float(r[key])
                except (TypeError, ValueError):
                    raise ValidationError(f"extracted_rows[{i}].{key} must be numeric.") from None
            else:
                try:
                    int(r[key])
                except (TypeError, ValueError):
                    raise ValidationError(f"extracted_rows[{i}].{key} must be an integer.") from None


def _merge_draft_json(cv: CurriculumVersion, body: dict) -> dict:
    dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
    dj = dict(dj)
    rows = body.get("extracted_rows")
    if rows is not None:
        if not isinstance(rows, list):
            raise ValidationError("extracted_rows must be a list.")
        _assign_draft_row_ids(rows)
        _validate_extracted_rows(rows)
        dj["extracted_rows"] = rows
    if "max_units_by_year_term" in body:
        caps = body["max_units_by_year_term"]
        if not isinstance(caps, dict):
            raise ValidationError("max_units_by_year_term must be an object.")
        dj["max_units_by_year_term"] = caps
    if "warnings" in body and isinstance(body["warnings"], list):
        dj["warnings"] = body["warnings"]
    if "extraction_meta" in body and isinstance(body["extraction_meta"], dict):
        dj["extraction_meta"] = body["extraction_meta"]
    if "suggested_graph" in body:
        dj["suggested_graph"] = body["suggested_graph"]
    return dj


def _draft_json_response(cv: CurriculumVersion) -> dict:
    dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
    rows = dj.get("extracted_rows") or []
    return {
        "ok": True,
        "curriculum_version_id": cv.pk,
        "program_slug": cv.program.slug,
        "version_label": cv.version_label,
        "status": cv.status,
        "phase": dj.get("phase"),
        "source_pdf_sha256": getattr(cv, "source_pdf_sha256", "") or "",
        "rows": rows,
        "extracted_rows": rows,
        "max_units_by_year_term": dj.get("max_units_by_year_term") or {},
        "warnings": dj.get("warnings") or [],
        "extraction_meta": dj.get("extraction_meta") or {},
        "suggested_graph": dj.get("suggested_graph"),
        "draft_analysis": dj.get("draft_analysis"),
    }


@ensure_csrf_cookie
@login_required
@require_http_methods(["GET"])
def staff_curriculum_upload_page(request):
    programs = _programs_for_staff(request.user)
    return render(
        request,
        "pos/staff_curriculum_upload.html",
        {
            "programs": programs,
        },
    )


@login_required
@require_http_methods(["GET"])
def staff_curriculum_review_page(request, pk: int):
    """Legacy URL — redirect to the unified edit page."""
    return redirect("staff_curriculum_edit_detail", pk=pk)


@require_POST
def staff_api_upload_curriculum_pdf(request):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)
    programs = _programs_for_staff(request.user)
    if not programs and not getattr(request.user, "is_superuser", False):
        return JsonResponse(
            {"ok": False, "error": "No programs assigned for curriculum management."},
            status=403,
        )

    try:
        pid = int(request.POST.get("program_id") or "")
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "program_id is required (integer)."}, status=400)

    program = Program.objects.filter(pk=pid).first()
    if program is None:
        return JsonResponse({"ok": False, "error": "Program not found."}, status=404)
    if not user_can_manage_program_curriculum(request.user, program):
        return JsonResponse({"ok": False, "error": "Not allowed for this program."}, status=403)

    f = request.FILES.get("file")
    if not f or not isinstance(f, UploadedFile):
        return JsonResponse({"ok": False, "error": "Missing multipart field 'file'."}, status=400)
    if f.size > MAX_PDF_BYTES:
        return JsonResponse({"ok": False, "error": "PDF exceeds size limit."}, status=400)
    if not str(f.name).lower().endswith(".pdf"):
        return JsonResponse({"ok": False, "error": "Only .pdf uploads are allowed."}, status=400)

    head = f.read(5)
    f.seek(0)
    if len(head) < 4 or head[:4] != b"%PDF":
        return JsonResponse({"ok": False, "error": "File does not look like a PDF."}, status=400)

    version_label = (request.POST.get("version_label") or "").strip()
    if not version_label:
        version_label = f"pdf-import-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

    sha = hashlib.sha256()
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            for chunk in f.chunks():
                sha.update(chunk)
                tmp.write(chunk)
            tmp.flush()
            tmp_path = Path(tmp.name)

        digest = sha.hexdigest()
        draft_body = extract_curriculum_rows_from_pdf(tmp_path)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    rows = list((draft_body or {}).get("extracted_rows") or [])
    _assign_draft_row_ids(rows)
    if isinstance(draft_body, dict):
        draft_body["extracted_rows"] = rows
    draft_body["draft_analysis"] = analyze_draft_json(draft_body)

    f.seek(0)
    cv = CurriculumVersion(
        program=program,
        status=CurriculumVersion.STATUS_DRAFT,
        version_label=version_label[:128],
        draft_json=draft_body,
        created_by=request.user if request.user.is_authenticated else None,
        source_pdf_sha256=digest,
    )
    cv.source_pdf.save(f.name, f, save=False)
    cv.save()

    return JsonResponse(
        {
            "ok": True,
            "curriculum_version_id": cv.pk,
            "program_slug": program.slug,
            "version_label": cv.version_label,
            "status": cv.status,
            "source_pdf_sha256": digest,
            "row_count": len(rows),
            "warnings": (draft_body or {}).get("warnings") or [],
            "draft_url": f"/staff/api/curriculum/version/{cv.pk}/draft/",
            "review_url": f"/staff/curriculum/version/{cv.pk}/review/",
            "edit_url": f"/staff/curriculum/edit/{cv.pk}/",
        }
    )


def _require_cv_draft_staff(request, pk: int) -> tuple[CurriculumVersion | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse({"ok": False, "error": "Not authenticated"}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return None, JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)
    cv = get_object_or_404(CurriculumVersion.objects.select_related("program"), pk=pk)
    if not user_can_manage_program_curriculum(request.user, cv.program):
        return None, JsonResponse({"ok": False, "error": "Not allowed for this program."}, status=403)
    if cv.status != CurriculumVersion.STATUS_DRAFT:
        return None, JsonResponse({"ok": False, "error": "Version is not a draft."}, status=400)
    return cv, None


@require_http_methods(["GET", "PATCH"])
def staff_api_curriculum_draft(request, pk: int):
    cv, err = _require_cv_draft_staff(request, pk)
    if err:
        return err
    assert cv is not None

    if request.method == "GET":
        return JsonResponse(_draft_json_response(cv))

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"ok": False, "error": "Body must be a JSON object."}, status=400)

    try:
        merged = _merge_draft_json(cv, body)
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    merged["draft_analysis"] = analyze_draft_json(merged)
    cv.draft_json = merged
    cv.save(update_fields=["draft_json", "updated_at"])
    return JsonResponse({"ok": True, **_draft_json_response(cv)})


@require_POST
def staff_api_curriculum_analyze(request, pk: int):
    cv, err = _require_cv_draft_staff(request, pk)
    if err:
        return err
    assert cv is not None

    dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
    merged = dict(dj)
    merged["draft_analysis"] = analyze_draft_json(merged)
    cv.draft_json = merged
    cv.save(update_fields=["draft_json", "updated_at"])
    return JsonResponse({"ok": True, **_draft_json_response(cv)})


@require_POST
def staff_api_curriculum_approve(request, pk: int):
    cv, err = _require_cv_draft_staff(request, pk)
    if err:
        return err
    assert cv is not None

    dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
    try:
        approved = draft_to_approved_json(
            dj,
            program_slug=cv.program.slug,
            curriculum_version_label=cv.version_label or f"version-{cv.pk}",
            source_pdf_sha256=(cv.source_pdf_sha256 or "").strip() or None,
        )
        validate_approved_bundle(approved)
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    cv.approved_json = approved
    cv.status = CurriculumVersion.STATUS_APPROVED
    cv.save(update_fields=["approved_json", "status", "updated_at"])
    return JsonResponse(
        {
            "ok": True,
            "curriculum_version_id": cv.pk,
            "status": cv.status,
            "course_count": len((approved.get("courses") or {})),
        }
    )


# ---------------------------------------------------------------------------
# Edit portal: list + detail
# ---------------------------------------------------------------------------

@ensure_csrf_cookie
@login_required
@require_http_methods(["GET"])
def staff_curriculum_edit_list_page(request):
    if not user_may_use_staff_curriculum_portal(request.user):
        return redirect(staff_login_url(next_path=request.get_full_path()))
    programs = _programs_for_staff(request.user)
    return render(
        request,
        "pos/staff_curriculum_edit.html",
        {"programs": programs},
    )


@ensure_csrf_cookie
@login_required
@require_http_methods(["GET"])
def staff_curriculum_edit_detail_page(request, pk: int):
    if not user_may_use_staff_curriculum_portal(request.user):
        return render(
            request,
            "pos/staff_curriculum_edit_detail.html",
            {"forbidden": True, "cv_id": pk},
        )
    cv = get_object_or_404(CurriculumVersion.objects.select_related("program"), pk=pk)
    if not user_can_manage_program_curriculum(request.user, cv.program):
        return render(
            request,
            "pos/staff_curriculum_edit_detail.html",
            {"forbidden": True, "cv_id": pk},
        )

    is_default = cv.program.default_curriculum_version_id == cv.pk

    if cv.status == CurriculumVersion.STATUS_DRAFT:
        ctx = {
            "forbidden": False,
            "cv_id": pk,
            "program_slug": cv.program.slug,
            "program_name": cv.program.name,
            "version_label": cv.version_label,
            "status": cv.status,
            "can_edit": True,
            "can_reopen": False,
            "can_delete": True,
            "is_default": is_default,
        }
    elif cv.status == CurriculumVersion.STATUS_APPROVED:
        dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
        has_draft_data = bool((dj.get("extracted_rows") or []))
        ctx = {
            "forbidden": False,
            "cv_id": pk,
            "program_slug": cv.program.slug,
            "program_name": cv.program.name,
            "version_label": cv.version_label,
            "status": cv.status,
            "can_edit": False,
            "can_reopen": True,
            "has_draft_data": has_draft_data,
            "can_delete": True,
            "is_default": is_default,
        }
    else:
        ctx = {
            "forbidden": False,
            "cv_id": pk,
            "program_slug": cv.program.slug,
            "program_name": cv.program.name,
            "version_label": cv.version_label,
            "status": cv.status,
            "can_edit": False,
            "can_reopen": False,
            "can_delete": True,
            "is_default": is_default,
        }

    return render(request, "pos/staff_curriculum_edit_detail.html", ctx)


@require_http_methods(["GET"])
def staff_api_curriculum_versions_list(request):
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Not authenticated."}, status=401)
    if not user_may_use_staff_curriculum_portal(request.user):
        return JsonResponse({"ok": False, "error": "Curriculum staff portal only."}, status=403)

    qs = _curriculum_versions_queryset(request.user)

    program_id = request.GET.get("program_id")
    if program_id:
        try:
            qs = qs.filter(program_id=int(program_id))
        except (TypeError, ValueError):
            pass

    status_filter = request.GET.get("status")
    if status_filter in (
        CurriculumVersion.STATUS_DRAFT,
        CurriculumVersion.STATUS_APPROVED,
        CurriculumVersion.STATUS_ARCHIVED,
    ):
        qs = qs.filter(status=status_filter)

    default_ids = set(
        Program.objects.filter(default_curriculum_version__isnull=False)
        .values_list("default_curriculum_version_id", flat=True)
    )

    versions = []
    for cv in qs.select_related("program")[:200]:
        dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
        row_count = len(dj.get("extracted_rows") or [])
        versions.append(
            {
                "id": cv.pk,
                "program_slug": cv.program.slug,
                "program_name": cv.program.name,
                "version_label": cv.version_label or "",
                "status": cv.status,
                "row_count": row_count,
                "updated_at": cv.updated_at.isoformat() if cv.updated_at else None,
                "created_at": cv.created_at.isoformat() if cv.created_at else None,
                "is_program_default": cv.pk in default_ids,
                "edit_url": f"/staff/curriculum/edit/{cv.pk}/",
                "can_reopen": cv.status == CurriculumVersion.STATUS_APPROVED,
                "can_delete": True,
            }
        )

    return JsonResponse({"ok": True, "versions": versions})


@require_POST
def staff_api_curriculum_reopen(request, pk: int):
    cv, err = _require_cv_staff(request, pk)
    if err:
        return err
    assert cv is not None

    if cv.status != CurriculumVersion.STATUS_APPROVED:
        return JsonResponse(
            {"ok": False, "error": "Only approved versions can be reopened as draft."},
            status=400,
        )

    dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
    if not (dj.get("extracted_rows") or []):
        return JsonResponse(
            {
                "ok": False,
                "error": "No draft data available — re-upload the PDF or edit in admin before reopening.",
            },
            status=400,
        )

    cv.status = CurriculumVersion.STATUS_DRAFT
    cv.save(update_fields=["status", "updated_at"])

    return JsonResponse(
        {
            "ok": True,
            "curriculum_version_id": cv.pk,
            "status": cv.status,
            "edit_url": f"/staff/curriculum/edit/{cv.pk}/",
        }
    )


@require_POST
def staff_api_curriculum_delete(request, pk: int):
    cv, err = _require_cv_staff(request, pk)
    if err:
        return err
    assert cv is not None

    program = cv.program
    was_default = program.default_curriculum_version_id == cv.pk
    if was_default:
        program.default_curriculum_version = None
        program.save(update_fields=["default_curriculum_version", "updated_at"])

    cv_id = cv.pk
    version_label = cv.version_label or ""
    if cv.source_pdf:
        try:
            cv.source_pdf.delete(save=False)
        except OSError:
            pass
    cv.delete()

    return JsonResponse(
        {
            "ok": True,
            "deleted_id": cv_id,
            "version_label": version_label,
            "cleared_program_default": was_default,
        }
    )
