from django.contrib import admin

from pos.models import CurriculumVersion, Program

from .models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "sr_code",
        "last_name",
        "first_name",
        "year_level",
        "program",
        "selected_curriculum_version",
        "email",
    )
    search_fields = ("sr_code", "last_name", "first_name", "email")
    ordering = ("sr_code",)
    # Autocomplete searches Program / CurriculumVersion by name and slug (not raw numeric PKs).
    autocomplete_fields = ("program", "selected_curriculum_version")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "program":
            kwargs["queryset"] = Program.objects.order_by("slug")
        if db_field.name == "selected_curriculum_version":
            kwargs["queryset"] = CurriculumVersion.objects.filter(
                status=CurriculumVersion.STATUS_APPROVED
            ).select_related("program")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
