"""
Staff curriculum portal auth helpers.

Staff sign in through the main SR-code login page; Django ``User`` accounts use
``username`` equal to the manager's SR-code.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods


def user_may_use_staff_curriculum_portal(user) -> bool:
    """Curriculum managers (auth perm) or superusers may use staff curriculum tools."""
    if not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.has_perm("pos.manage_curriculum_versions")


def staff_login_url(*, next_path: str = "") -> str:
    """Main login page URL, optionally preserving a post-login redirect."""
    url = reverse("login")
    nxt = (next_path or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//"):
        url += "?" + urlencode({"next": nxt})
    return url


@require_http_methods(["GET"])
def staff_login_redirect(request):
    """Legacy /staff/login/ — send users to the main SR-code login page."""
    return redirect(staff_login_url(next_path=(request.GET.get("next") or "").strip()))


@login_required
@require_http_methods(["GET", "POST"])
def staff_logout_view(request):
    logout(request)
    request.session.flush()
    return redirect("login")
