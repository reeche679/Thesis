"""
Let Django superusers open the student POS portal (Main Site) without registering.

A placeholder Student row is created or reused so they can pick program/curriculum,
academic year, and generate POS. Profile fields show as “-”.
"""

from __future__ import annotations

import re

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractBaseUser

from accounts.models import Student

SR_CODE_REGEX = re.compile(r"^\d{2}-\d{5}$")

SESSION_SUPERUSER_DEMO = "superuser_demo_student"


def superuser_demo_sr_code(user: AbstractBaseUser) -> str:
    """Stable SR-style code for a superuser's sandbox student row."""
    uname = (getattr(user, "username", None) or "").strip()
    if SR_CODE_REGEX.match(uname):
        return uname
    return f"99-{int(user.pk):05d}"


def is_superuser_demo_session(request) -> bool:
    return bool(request.session.get(SESSION_SUPERUSER_DEMO))


def get_or_create_superuser_demo_student(user: AbstractBaseUser) -> Student:
    """Placeholder student for superuser POS trials (display fields are “-”)."""
    uname = (getattr(user, "username", None) or "").strip()

    if SR_CODE_REGEX.match(uname):
        existing = Student.objects.filter(sr_code=uname).first()
        if existing is not None:
            return existing

    sr = superuser_demo_sr_code(user)
    student, _created = Student.objects.get_or_create(
        sr_code=sr,
        defaults={
            "first_name": "-",
            "middle_name": "",
            "last_name": "-",
            "email": f"superuser-demo-{user.pk}@staff.local",
            "password": make_password("unused-superuser-demo"),
        },
    )
    return student


def ensure_superuser_student_session(request) -> Student | None:
    """
    If the request is an authenticated superuser without a student session,
    attach a demo student so student portal views work.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None
    if not getattr(request.user, "is_superuser", False):
        return None

    sid = request.session.get("student_id")
    if sid:
        try:
            return Student.objects.get(pk=sid)
        except Student.DoesNotExist:
            request.session.pop("student_id", None)
            request.session.pop(SESSION_SUPERUSER_DEMO, None)

    student = get_or_create_superuser_demo_student(request.user)
    request.session["student_id"] = student.pk
    request.session[SESSION_SUPERUSER_DEMO] = True
    request.session.modified = True
    return student
