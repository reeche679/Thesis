"""
Load the Phase 0 golden approved JSON into Program + CurriculumVersion (approved).

Example:
  python manage.py seed_curriculum_fixture
  python manage.py seed_curriculum_fixture --slug bscpe_batstateu --fixture C:/path/to/batstateu_bscpe_static_v1.approved.json
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pos.models import CurriculumVersion, Program


def _default_fixture_path() -> Path:
    return (
        Path(settings.BASE_DIR).parent
        / "Solver-Side"
        / "curriculum_schema"
        / "fixtures"
        / "batstateu_bscpe_static_v1.approved.json"
    )


def _default_schema_path() -> Path:
    return (
        Path(settings.BASE_DIR).parent
        / "Solver-Side"
        / "curriculum_schema"
        / "approved_curriculum.schema.json"
    )


class Command(BaseCommand):
    help = "Upsert Program and an approved CurriculumVersion from a Phase 0 approved JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--slug",
            default="bscpe_batstateu",
            help="Program slug (default: bscpe_batstateu).",
        )
        parser.add_argument(
            "--name",
            default="",
            help="Program display name (default: derived from slug).",
        )
        parser.add_argument(
            "--fixture",
            type=str,
            default="",
            help="Path to approved JSON (default: Solver-Side golden fixture next to repo).",
        )
        parser.add_argument(
            "--version-label",
            type=str,
            default="",
            help="CurriculumVersion.version_label (default: metadata.curriculum_version_label or static-export-v1).",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="If a matching approved version exists (same program + label), replace approved_json.",
        )

    def handle(self, *args, **options):
        slug = options["slug"].strip()
        if not slug:
            raise CommandError("--slug must be non-empty.")

        fixture_path = Path(options["fixture"]).resolve() if options["fixture"] else _default_fixture_path()
        if not fixture_path.is_file():
            raise CommandError(f"Fixture not found: {fixture_path}")

        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        try:
            import jsonschema
        except ImportError as e:
            raise CommandError("Install jsonschema: pip install jsonschema") from e

        schema_path = _default_schema_path()
        if not schema_path.is_file():
            raise CommandError(f"Schema not found: {schema_path}")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(raw)

        meta = raw.get("metadata") or {}
        version_label = (options["version_label"] or meta.get("curriculum_version_label") or "static-export-v1").strip()

        program_name = options["name"].strip() or slug.replace("_", " ").title()

        program, created = Program.objects.update_or_create(
            slug=slug,
            defaults={"name": program_name, "is_active": True},
        )
        self.stdout.write(
            self.style.NOTICE(f"Program {'created' if created else 'updated'}: {program.slug}")
        )

        existing = CurriculumVersion.objects.filter(
            program=program,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label=version_label,
        ).first()

        if existing and not options["replace"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Approved version already exists (id={existing.pk}). Use --replace to overwrite approved_json."
                )
            )
            return

        if existing and options["replace"]:
            existing.approved_json = raw
            existing.draft_json = {}
            existing.save(update_fields=["approved_json", "draft_json", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Updated CurriculumVersion id={existing.pk}"))
            return

        v = CurriculumVersion.objects.create(
            program=program,
            status=CurriculumVersion.STATUS_APPROVED,
            version_label=version_label,
            approved_json=raw,
            draft_json={},
        )
        self.stdout.write(self.style.SUCCESS(f"Created CurriculumVersion id={v.pk} (approved)"))
