"""
Create a staff user for curriculum admin smoke tests (development / local only).

  set CURRICULUM_STAFF_PASSWORD=your-secret
  python manage.py bootstrap_curriculum_staff --username curriculum_editor

Or pass --password (avoid in shell history in production).

The user is added to the "Curriculum managers" group and to curriculum_managers
for Program slug bscpe_batstateu (program is created if missing).
"""

from __future__ import annotations

import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from pos.models import Program

DEFAULT_PROGRAM_SLUG = "bscpe_batstateu"

CURRICULUM_MANAGER_PERMS = [
    "manage_curriculum_versions",
    "change_program",
    "view_program",
    "add_curriculumversion",
    "change_curriculumversion",
    "view_curriculumversion",
    "delete_curriculumversion",
]


def _ensure_curriculum_managers_group() -> Group:
    """Create group and permissions if missing (idempotent; fixes empty DB after migrations)."""
    group, _ = Group.objects.get_or_create(name="Curriculum managers")
    ct_program = ContentType.objects.filter(app_label="pos", model="program").first()
    ct_version = ContentType.objects.filter(app_label="pos", model="curriculumversion").first()
    if ct_program and ct_version:
        perms = list(
            Permission.objects.filter(
                content_type__in=[ct_program, ct_version],
                codename__in=CURRICULUM_MANAGER_PERMS,
            )
        )
        if perms:
            group.permissions.add(*perms)
    return group


class Command(BaseCommand):
    help = "Create staff user, add to Curriculum managers, assign to default Program (dev bootstrap)."

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, required=True)
        parser.add_argument(
            "--password",
            type=str,
            default="",
            help="If omitted, uses env CURRICULUM_STAFF_PASSWORD or prompts.",
        )
        parser.add_argument("--email", type=str, default="")
        parser.add_argument(
            "--program-slug",
            type=str,
            default=DEFAULT_PROGRAM_SLUG,
            help=f"Program to attach user to (default: {DEFAULT_PROGRAM_SLUG}).",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"].strip()
        if not username:
            raise CommandError("--username is required.")

        password = (options["password"] or "").strip() or os.environ.get(
            "CURRICULUM_STAFF_PASSWORD", ""
        ).strip()
        if not password:
            password = getpass.getpass("Password for new staff user: ")
        if not password:
            raise CommandError("Password is required (use --password or CURRICULUM_STAFF_PASSWORD).")

        email = (options["email"] or "").strip() or f"{username}@localhost.invalid"
        program_slug = options["program_slug"].strip()

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": False,
            },
        )
        user.email = email
        user.is_staff = True
        user.set_password(password)
        user.save()

        group = _ensure_curriculum_managers_group()
        user.groups.add(group)

        program, _ = Program.objects.get_or_create(
            slug=program_slug,
            defaults={"name": program_slug.replace("_", " ").title(), "is_active": True},
        )
        program.curriculum_managers.add(user)

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} staff user {username!r}; "
                f"group Curriculum managers; manager on Program {program_slug!r}. "
                f"Log in at /admin/"
            )
        )
