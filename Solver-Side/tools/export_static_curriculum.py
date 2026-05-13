"""
Export static BatStateU curriculum from courses.py + units_and_year_standings.py
into an approved-curriculum JSON bundle (Phase 0 canonical format).

Run from repository root or any cwd:
  python Solver-Side/tools/export_static_curriculum.py

Requires Solver-Side on sys.path (script adds it automatically).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _solver_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_import_path() -> None:
    root = str(_solver_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _canonical_course_record(info: dict[str, Any]) -> dict[str, Any]:
    """Emit only schema-approved keys with canonical pre-requisites / co-requisites."""
    pre = info.get("pre-requisites", info.get("pre_requisites", info.get("prerequisites", [])))
    co = info.get("co-requisites", info.get("co_requisites", info.get("corequisites", [])))
    if not isinstance(pre, (list, tuple)):
        pre = [pre] if pre else []
    if not isinstance(co, (list, tuple)):
        co = [co] if co else []

    pre_out: list[Any] = []
    for item in pre:
        if isinstance(item, (list, tuple)):
            pre_out.append([str(x) for x in item])
        else:
            pre_out.append(str(item))

    co_out = [str(x) for x in co]

    raw_units = info.get("units", 0)
    if isinstance(raw_units, bool):
        raise TypeError("units must be numeric")
    units_out: int | float
    if isinstance(raw_units, int):
        units_out = raw_units
    elif isinstance(raw_units, float):
        units_out = int(raw_units) if raw_units.is_integer() else raw_units
    else:
        units_out = float(raw_units)

    return {
        "course_title": str(info.get("course_title", "")),
        "units": units_out,
        "orig_sem": int(info["orig_sem"]),
        "category": str(info["category"]),
        "year_level": int(info["year_level"]),
        "pre-requisites": pre_out,
        "co-requisites": co_out,
    }


def build_bundle() -> dict[str, Any]:
    _ensure_import_path()
    from courses_and_year_standings.courses import (
        PREREQ_ALIASES,
        YEAR_STANDING_RULES,
        courses,
    )
    from courses_and_year_standings import units_and_year_standings as u

    max_sem = int(u.MAX_SEMESTER)
    caps = [int(u.max_units_for_sem(s)) for s in range(1, max_sem + 1)]

    out_courses: dict[str, Any] = {}
    max_orig = 0
    for code, info in courses.items():
        if not isinstance(info, dict):
            continue
        out_courses[code] = _canonical_course_record(info)
        max_orig = max(max_orig, out_courses[code]["orig_sem"])

    if max_orig > max_sem:
        raise ValueError(
            f"orig_sem {max_orig} exceeds MAX_SEMESTER {max_sem} from unit caps horizon"
        )

    aliases: dict[str, Any] = {}
    for k, v in PREREQ_ALIASES.items():
        if isinstance(v, (list, tuple)):
            aliases[k] = [str(x) for x in v]
        else:
            aliases[k] = str(v)

    standing = {str(k): int(v) for k, v in YEAR_STANDING_RULES.items()}

    return {
        "schema_version": 1,
        "courses": dict(sorted(out_courses.items(), key=lambda kv: kv[0])),
        "max_units_by_semester_index": caps,
        "metadata": {
            "program_slug": "bscpe_batstateu",
            "curriculum_version_label": "static-export-v1",
            "source_pdf_sha256": None,
        },
        "prereq_aliases": dict(sorted(aliases.items(), key=lambda kv: kv[0])),
        "year_standing_rules": dict(sorted(standing.items(), key=lambda kv: kv[0])),
    }


def validate_against_schema(bundle: dict[str, Any], schema_path: Path) -> None:
    try:
        import jsonschema
    except ImportError as e:
        raise SystemExit(
            "Install jsonschema to validate: pip install jsonschema"
        ) from e

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(bundle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        help="Output JSON path (default: Solver-Side/curriculum_schema/fixtures/batstateu_bscpe_static_v1.approved.json)",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        help="Schema path for validation (default: Solver-Side/curriculum_schema/approved_curriculum.schema.json)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip jsonschema validation after export",
    )
    parser.add_argument(
        "--validate-only",
        type=Path,
        metavar="BUNDLE_JSON",
        help="Validate an existing bundle against the schema and exit",
    )
    args = parser.parse_args()

    solver_root = _solver_root()
    default_out = (
        solver_root
        / "curriculum_schema"
        / "fixtures"
        / "batstateu_bscpe_static_v1.approved.json"
    )
    default_schema = solver_root / "curriculum_schema" / "approved_curriculum.schema.json"

    schema_path = args.schema or default_schema

    if args.validate_only:
        bundle = json.loads(args.validate_only.read_text(encoding="utf-8"))
        validate_against_schema(bundle, schema_path)
        print(f"OK: {args.validate_only} validates against {schema_path}")
        return

    bundle = build_bundle()
    out_path = args.out or default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")

    if not args.no_validate:
        validate_against_schema(bundle, schema_path)
        print(f"Validated against {schema_path}")


if __name__ == "__main__":
    main()
