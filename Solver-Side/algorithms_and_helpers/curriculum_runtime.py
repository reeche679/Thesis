"""
Per-request curriculum data for the solver (Phase 3).

When ``curriculum_context_scope`` is active (set from ``generate_pos`` payload),
``courses``, ``u``, ``PREREQ_ALIASES``, ``YEAR_STANDING_RULES``, and
``GRADUATION_TRIO`` resolve to that bundle. Otherwise they resolve to the
static modules ``courses.py`` and ``units_and_year_standings.py``.

Django should pass ``curriculum_bundle`` (approved JSON shape from Phase 0) on
the solver payload; ``curriculum_version_id`` is resolved in ``solver_bridge``
before calling the solver.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

_CURRICULUM_CTX: contextvars.ContextVar["CurriculumContext | None"] = contextvars.ContextVar(
    "curriculum_ctx", default=None
)

_STATIC_CACHE: "CurriculumContext | None" = None

DEFAULT_GRADUATION_TRIO = ("CpE 405", "CpE 406", "ENGG 117")


@dataclass(frozen=True)
class CurriculumContext:
    courses: dict[str, Any]
    prereq_aliases: dict[str, Any]
    year_standing_rules: dict[str, int]
    max_semester: int
    graduation_trio: tuple[str, ...]
    _caps: tuple[int, ...]

    def max_units_for_sem(self, sem: int) -> int:
        if 1 <= sem <= self.max_semester:
            return int(self._caps[sem - 1])
        return 0


def _static_context() -> CurriculumContext:
    global _STATIC_CACHE
    if _STATIC_CACHE is not None:
        return _STATIC_CACHE
    from courses_and_year_standings.courses import (
        PREREQ_ALIASES as _pa,
        YEAR_STANDING_RULES as _ysr,
        courses as _c,
    )
    import courses_and_year_standings.units_and_year_standings as _u

    caps = tuple(int(_u.max_units_for_sem(s)) for s in range(1, int(_u.MAX_SEMESTER) + 1))
    _STATIC_CACHE = CurriculumContext(
        courses=dict(_c),
        prereq_aliases=dict(_pa),
        year_standing_rules={str(k): int(v) for k, v in _ysr.items()},
        max_semester=int(_u.MAX_SEMESTER),
        graduation_trio=DEFAULT_GRADUATION_TRIO,
        _caps=caps,
    )
    return _STATIC_CACHE


_VALID_OFFERING_CATEGORIES = frozenset({"Rigid", "Bottleneck", "Flexible"})


def normalize_offering_category_value(info: Any) -> str:
    """
    Canonical offering category for CP-SAT (Rigid / Flexible / Bottleneck).

    Staff JSON sometimes stores non-strings (numbers, booleans). Those must not
    flow into string-only comparisons in offering constraints — unknown values
    would otherwise yield **no** allowed semesters (immediate INFEASIBLE).
    """
    if not isinstance(info, dict):
        return "Rigid"
    raw = info.get("category", "Rigid")
    if isinstance(raw, bool) or raw is None:
        cat = ""
    elif isinstance(raw, str):
        cat = raw.strip()
    else:
        cat = str(raw).strip()
    if cat in _VALID_OFFERING_CATEGORIES:
        return cat
    return "Rigid"


def _normalize_categories_from_static(courses: dict[str, Any]) -> None:
    """Fill missing/invalid ``category`` on each course from static ``courses.py`` when the code matches."""
    try:
        from courses_and_year_standings.courses import courses as static_courses
    except Exception:
        static_courses = {}

    def _lookup_static(code: str) -> dict | None:
        if code in static_courses and isinstance(static_courses.get(code), dict):
            return static_courses[code]
        cu = str(code).strip().upper()
        for sk, sv in static_courses.items():
            if str(sk).strip().upper() == cu and isinstance(sv, dict):
                return sv
        return None

    for code, rec in courses.items():
        if not isinstance(rec, dict):
            continue
        raw = rec.get("category")
        if isinstance(raw, bool) or raw is None:
            cat = ""
        elif isinstance(raw, str):
            cat = raw.strip()
        else:
            cat = str(raw).strip()
        if cat in _VALID_OFFERING_CATEGORIES:
            if cat == "Rigid":
                info = _lookup_static(str(code))
                if info:
                    sc = str(info.get("category", "")).strip()
                    if sc in ("Bottleneck", "Flexible"):
                        rec["category"] = sc
            continue
        info = _lookup_static(str(code))
        if info:
            sc = str(info.get("category", "")).strip()
            if sc in _VALID_OFFERING_CATEGORIES:
                rec["category"] = sc
                continue
        rec["category"] = "Rigid"

    for code, rec in courses.items():
        if isinstance(rec, dict):
            rec["category"] = normalize_offering_category_value(rec)


_OVERFLOW_SEMESTERS = 6
_DEFAULT_OVERFLOW_CAP = 24


def _year6_overflow_cap(caps: tuple[int, ...], sem: int) -> int:
    """Return an overflow cap for ``sem`` by reusing the Year 6 value for the matching term type."""
    term_in_year = ((sem - 1) % 3) + 1
    y6_sem = (6 - 1) * 3 + term_in_year  # global semester index for Year 6
    if y6_sem <= len(caps):
        return caps[y6_sem - 1]
    return _DEFAULT_OVERFLOW_CAP


def context_from_approved_bundle(bundle: dict[str, Any]) -> CurriculumContext:
    """Build a context from Phase 0 / Django ``approved_json``."""
    raw_courses = bundle.get("courses")
    raw_caps = bundle.get("max_units_by_semester_index")
    if not isinstance(raw_courses, dict) or not isinstance(raw_caps, list) or not raw_caps:
        raise ValueError("curriculum_bundle must include non-empty 'courses' and 'max_units_by_semester_index'.")

    caps = tuple(int(x) for x in raw_caps)
    courses = dict(raw_courses)
    for _code, rec in courses.items():
        if isinstance(rec, dict):
            u = rec.get("units", 0)
            try:
                rec["units"] = int(round(float(u if u is not None else 0)))
            except (TypeError, ValueError):
                rec["units"] = 0
    _normalize_categories_from_static(courses)

    max_orig = max(
        (int(rec.get("orig_sem", 1)) for rec in courses.values() if isinstance(rec, dict)),
        default=1,
    )
    min_horizon = max_orig + _OVERFLOW_SEMESTERS
    if len(caps) < min_horizon:
        overflow = tuple(_year6_overflow_cap(caps, s) for s in range(len(caps) + 1, min_horizon + 1))
        caps = caps + overflow

    pa = dict(bundle.get("prereq_aliases") or {})
    ysr_raw = bundle.get("year_standing_rules")
    if isinstance(ysr_raw, dict) and ysr_raw:
        year_standing_rules = {str(k): int(v) for k, v in ysr_raw.items()}
    else:
        year_standing_rules = dict(_static_context().year_standing_rules)

    trio_raw = bundle.get("graduation_trio")
    if isinstance(trio_raw, (list, tuple)) and len(trio_raw) == 3:
        graduation_trio = tuple(str(x) for x in trio_raw)
    else:
        graduation_trio = DEFAULT_GRADUATION_TRIO

    return CurriculumContext(
        courses=courses,
        prereq_aliases=pa,
        year_standing_rules=year_standing_rules,
        max_semester=len(caps),
        graduation_trio=graduation_trio,
        _caps=caps,
    )


def resolve_curriculum_context_from_payload(payload: dict | None) -> CurriculumContext:
    if not payload:
        return _static_context()
    bundle = payload.get("curriculum_bundle")
    if isinstance(bundle, dict) and isinstance(bundle.get("courses"), dict) and isinstance(
        bundle.get("max_units_by_semester_index"), list
    ):
        try:
            return context_from_approved_bundle(bundle)
        except (TypeError, ValueError):
            return _static_context()
    return _static_context()


def get_curriculum_context() -> CurriculumContext:
    v = _CURRICULUM_CTX.get()
    return v if v is not None else _static_context()


@contextmanager
def curriculum_context_scope(ctx: CurriculumContext):
    token = _CURRICULUM_CTX.set(ctx)
    try:
        yield
    finally:
        _CURRICULUM_CTX.reset(token)


class _CoursesView(Mapping[str, Any]):
    __slots__ = ()

    def __getitem__(self, key: str) -> Any:
        return get_curriculum_context().courses[key]

    def __iter__(self) -> Iterator[str]:
        return iter(get_curriculum_context().courses)

    def __len__(self) -> int:
        return len(get_curriculum_context().courses)


class _AliasesView(Mapping[str, Any]):
    __slots__ = ()

    def __getitem__(self, key: str) -> Any:
        return get_curriculum_context().prereq_aliases[key]

    def __iter__(self) -> Iterator[str]:
        return iter(get_curriculum_context().prereq_aliases)

    def __len__(self) -> int:
        return len(get_curriculum_context().prereq_aliases)


class _StandingView(Mapping[str, int]):
    __slots__ = ()

    def __getitem__(self, key: str) -> int:
        return get_curriculum_context().year_standing_rules[key]

    def __iter__(self) -> Iterator[str]:
        return iter(get_curriculum_context().year_standing_rules)

    def __len__(self) -> int:
        return len(get_curriculum_context().year_standing_rules)


class _UnitsProxy:
    __slots__ = ()

    @property
    def MAX_SEMESTER(self) -> int:
        return get_curriculum_context().max_semester

    def max_units_for_sem(self, sem: int) -> int:
        return get_curriculum_context().max_units_for_sem(int(sem))


class _GraduationTrioView:
    """Supports ``x in GRADUATION_TRIO`` and ``for x in GRADUATION_TRIO``."""

    __slots__ = ()

    def __contains__(self, item: object) -> bool:
        return item in get_curriculum_context().graduation_trio

    def __iter__(self) -> Iterator[str]:
        return iter(get_curriculum_context().graduation_trio)


courses: Mapping[str, Any] = _CoursesView()
PREREQ_ALIASES: Mapping[str, Any] = _AliasesView()
YEAR_STANDING_RULES: Mapping[str, int] = _StandingView()
u = _UnitsProxy()
GRADUATION_TRIO = _GraduationTrioView()
