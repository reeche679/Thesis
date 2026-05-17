"""
Dedicated staff (Django User) login for curriculum tools — separate from /admin/ and student login.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods


def user_may_use_staff_curriculum_portal(user) -> bool:
    """Curriculum managers (auth perm) or superusers may use staff curriculum tools."""
    if not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_perm("pos.manage_curriculum_versions")


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def staff_login_view(request):
    if request.user.is_authenticated and user_may_use_staff_curriculum_portal(request.user):
        nxt = (request.GET.get("next") or request.POST.get("next") or "").strip()
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect("staff_home")

    if request.user.is_authenticated and not user_may_use_staff_curriculum_portal(request.user):
        messages.info(request, "Signed out — this portal is only for curriculum managers or superusers.")
        logout(request)

    next_value = (request.GET.get("next") or "").strip()

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        next_value = (request.POST.get("next") or "").strip()
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Invalid username or password.")
        elif not user_may_use_staff_curriculum_portal(user):
            messages.error(
                request,
                "This portal is only for curriculum managers or superusers. "
                "Ask an administrator to add you to the Curriculum managers group and assign your program.",
            )
        else:
            login(request, user)
            if next_value.startswith("/") and not next_value.startswith("//"):
                return redirect(next_value)
            return redirect("staff_home")

    return render(
        request,
        "pos/staff_login.html",
        {
            "next": next_value,
            "upload_url": reverse("staff_curriculum_upload"),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def staff_logout_view(request):
    logout(request)
    return redirect("staff_login")
