from django.contrib import admin
from .models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('sr_code', 'last_name', 'first_name', 'year_level', 'email')
    search_fields = ('sr_code', 'last_name', 'first_name', 'email')
    ordering = ('sr_code',)
