"""
Phase 5 — staff-only curriculum PDF upload and draft extraction (Django User auth).
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST

from .curriculum_pdf_extract import extract_curriculum_rows_from_pdf
from .models import CurriculumVersion, Program
from .permissions import user_can_manage_program_curriculum

MAX_PDF_BYTES = 25 * 1024 * 1024


def _programs_for_staff(user) -> list[Program]:
    if getattr(user, "is_superuser", False):
        return list(Program.objects.order_by("slug"))
    return list(
        Program.objects.filter(curriculum_managers=user, is_active=True).distinct().order_by("slug")
    )


@ensure_csrf_cookie
@login_required(login_url="/admin/login/")
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


@login_required(login_url="/admin/login/")
@require_POST
def staff_api_upload_curriculum_pdf(request):
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
            "row_count": len((draft_body or {}).get("extracted_rows") or []),
            "warnings": (draft_body or {}).get("warnings") or [],
            "draft_url": f"/staff/api/curriculum/version/{cv.pk}/draft/",
        }
    )


@login_required(login_url="/admin/login/")
@require_http_methods(["GET"])
def staff_api_curriculum_draft(request, pk: int):
    cv = get_object_or_404(CurriculumVersion.objects.select_related("program"), pk=pk)
    if not user_can_manage_program_curriculum(request.user, cv.program):
        return JsonResponse({"ok": False, "error": "Not allowed for this program."}, status=403)
    if cv.status != CurriculumVersion.STATUS_DRAFT:
        return JsonResponse({"ok": False, "error": "Version is not a draft (read-only policy for Phase 5)."}, status=400)

    dj = cv.draft_json if isinstance(cv.draft_json, dict) else {}
    return JsonResponse(
        {
            "ok": True,
            "curriculum_version_id": cv.pk,
            "program_slug": cv.program.slug,
            "version_label": cv.version_label,
            "status": cv.status,
            "source_pdf_sha256": getattr(cv, "source_pdf_sha256", "") or "",
            "rows": dj.get("extracted_rows") or [],
            "warnings": dj.get("warnings") or [],
            "extraction_meta": dj.get("extraction_meta") or {},
            "suggested_graph": dj.get("suggested_graph"),
        }
    )
