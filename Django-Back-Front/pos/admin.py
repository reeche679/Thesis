import json
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import CurriculumVersion, Program


class ProgramAdminForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = "__all__"
from .permissions import user_can_manage_program_curriculum


def _approved_curriculum_schema_path() -> Path:
    return Path(settings.BASE_DIR).parent / "Solver-Side" / "curriculum_schema" / "approved_curriculum.schema.json"


def validate_approved_bundle(data: dict) -> None:
    """Raise ValidationError if data does not match the Phase 0 JSON schema."""
    if not data:
        raise ValidationError("approved_json is empty.")
    schema_path = _approved_curriculum_schema_path()
    if not schema_path.is_file():
        raise ValidationError(
            f"Schema file not found at {schema_path}. Cannot validate approved_json."
        )
    try:
        import jsonschema
    except ImportError as e:
        raise ValidationError(
            "Install jsonschema to validate curriculum bundles (pip install jsonschema)."
        ) from e
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as e:
        raise ValidationError(f"approved_json failed schema validation: {e.message}") from e


class CurriculumVersionAdminForm(forms.ModelForm):
    class Meta:
        model = CurriculumVersion
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        approved = cleaned.get("approved_json") or {}
        if status == CurriculumVersion.STATUS_APPROVED:
            validate_approved_bundle(approved)
        return cleaned


class CurriculumVersionInline(admin.TabularInline):
    model = CurriculumVersion
    extra = 0
    show_change_link = True
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "status",
        "version_label",
        "source_pdf",
        "created_by",
        "created_at",
        "updated_at",
    )


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    form = ProgramAdminForm
    list_display = (
        "slug",
        "name",
        "pdf_program_shortcut",
        "is_active",
        "default_curriculum_version",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("slug", "name", "description", "is_active")}),
        (
            "PDF header defaults",
            {
                "fields": (
                    "pdf_department_label",
                    "pdf_program_curriculum",
                    "pdf_program_shortcut",
                ),
            },
        ),
        ("Curriculum", {"fields": ("default_curriculum_version", "curriculum_managers")}),
    )
    list_filter = ("is_active",)
    search_fields = ("slug", "name")
    filter_horizontal = ("curriculum_managers",)
    inlines = (CurriculumVersionInline,)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if request.user.has_perm("pos.manage_curriculum_versions"):
            return qs.filter(curriculum_managers=request.user).distinct()
        return qs.none()

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "pos.manage_curriculum_versions"
        )

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return request.user.has_perm("pos.manage_curriculum_versions")
        return user_can_manage_program_curriculum(request.user, obj)

    def has_add_permission(self, request):
        """Only superusers create new programs; managers edit assigned programs only."""
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return request.user.has_perm("pos.manage_curriculum_versions")
        return user_can_manage_program_curriculum(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "default_curriculum_version":
            obj = kwargs.get("obj")
            if obj is not None and getattr(obj, "pk", None):
                kwargs["queryset"] = CurriculumVersion.objects.filter(
                    program=obj,
                    status=CurriculumVersion.STATUS_APPROVED,
                ).order_by("-created_at")
            else:
                kwargs["queryset"] = CurriculumVersion.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(CurriculumVersion)
class CurriculumVersionAdmin(admin.ModelAdmin):
    form = CurriculumVersionAdminForm
    list_display = (
        "id",
        "program",
        "status",
        "version_label",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "program")
    search_fields = ("version_label", "program__slug", "program__name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("created_by",)

    fieldsets = (
        (None, {"fields": ("program", "status", "version_label", "source_pdf", "created_by")}),
        ("JSON", {"fields": ("draft_json", "approved_json")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(program__curriculum_managers=request.user).distinct()

    def has_module_permission(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "pos.manage_curriculum_versions"
        )

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return request.user.has_perm("pos.manage_curriculum_versions")
        return user_can_manage_program_curriculum(request.user, obj.program)

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "pos.manage_curriculum_versions"
        )

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if obj is None:
            return request.user.has_perm("pos.manage_curriculum_versions")
        return user_can_manage_program_curriculum(request.user, obj.program)

    def has_delete_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change or "created_by" not in form.changed_data:
            if obj.created_by_id is None and request.user.is_authenticated:
                obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Mark selected as approved (validates approved_json)")
    def mark_approved(self, request, queryset):
        for obj in queryset:
            try:
                validate_approved_bundle(obj.approved_json or {})
                obj.status = CurriculumVersion.STATUS_APPROVED
                obj.full_clean()
                obj.save(update_fields=["status", "updated_at"])
            except ValidationError as e:
                self.message_user(request, f"{obj}: {e}", level=messages.ERROR)
            else:
                self.message_user(request, f"Approved: {obj}", level=messages.SUCCESS)

    actions = ("mark_approved",)
