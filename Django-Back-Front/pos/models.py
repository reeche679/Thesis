"""
Curriculum storage (Phase 1). Solver integration comes in a later phase.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Program(models.Model):
    """
    Academic program (e.g. BSCpE). Curricula are scoped per program.
    """

    slug = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Stable id, e.g. bscpe_batstateu (align with approved JSON metadata.program_slug).",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    curriculum_managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="managed_curriculum_programs",
        help_text="Staff users who may create and edit draft curriculum versions for this program.",
    )
    default_curriculum_version = models.ForeignKey(
        "CurriculumVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="programs_as_default_curriculum",
        help_text="When set, solver/student flows may use this approved version if none is selected.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]
        permissions = [
            (
                "manage_curriculum_versions",
                "Can manage curriculum versions (draft/approve) for assigned programs",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def user_can_manage_versions(self, user) -> bool:
        """Object-level: superuser, or perm + listed as manager for this program."""
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if not user.has_perm("pos.manage_curriculum_versions"):
            return False
        return self.curriculum_managers.filter(pk=user.pk).exists()

    def clean(self) -> None:
        super().clean()
        dcv = self.default_curriculum_version
        if dcv is None:
            return
        if dcv.status != CurriculumVersion.STATUS_APPROVED:
            raise ValidationError(
                {
                    "default_curriculum_version": "Default curriculum must be an approved version.",
                }
            )
        if self.pk is not None and dcv.program_id != self.pk:
            raise ValidationError(
                {
                    "default_curriculum_version": "Default curriculum must belong to this program.",
                }
            )


class CurriculumVersion(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_APPROVED = "approved"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    program = models.ForeignKey(
        Program,
        on_delete=models.CASCADE,
        related_name="curriculum_versions",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )
    version_label = models.CharField(
        max_length=128,
        blank=True,
        help_text="Human label; should match metadata.curriculum_version_label in approved JSON when set.",
    )
    source_pdf = models.FileField(
        upload_to="curriculum_pdfs/%Y/%m/",
        blank=True,
        null=True,
        help_text="Source curriculum PDF (Phase 5: upload pipeline).",
    )
    source_pdf_sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="SHA-256 (hex) of uploaded source PDF bytes for dedup/audit.",
    )
    draft_json = models.JSONField(default=dict, blank=True)
    approved_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_curriculum_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        label = self.version_label or f"id {self.pk}"
        return f"{self.program.slug} / {label} ({self.get_status_display()})"

    def clean(self) -> None:
        if self.status == self.STATUS_APPROVED and not (self.approved_json or {}):
            raise ValidationError(
                {"approved_json": "Approved versions must have non-empty approved_json."}
            )
