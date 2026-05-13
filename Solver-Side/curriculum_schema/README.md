# Approved curriculum JSON (Phase 0)

This folder defines the **canonical approved curriculum bundle** used by later phases (Django `CurriculumVersion.approved_json`, solver `CurriculumContext`).

## Files

| File | Purpose |
|------|---------|
| [approved_curriculum.schema.json](approved_curriculum.schema.json) | JSON Schema (draft 2020-12) for validating bundles |
| [fixtures/batstateu_bscpe_static_v1.approved.json](fixtures/batstateu_bscpe_static_v1.approved.json) | Golden fixture exported from static `courses.py` + `units_and_year_standings.py` |

## Regenerate the golden fixture

From the repository root (with `Solver-Side` importable via the script’s `sys.path` logic):

```powershell
pip install -r requirements.txt
python Solver-Side/tools/export_static_curriculum.py
```

Re-run whenever [`courses.py`](../courses_and_year_standings/courses.py) or [`units_and_year_standings.py`](../courses_and_year_standings/units_and_year_standings.py) changes, then commit the updated JSON.

### Validate only

```powershell
python Solver-Side/tools/export_static_curriculum.py --validate-only Solver-Side/curriculum_schema/fixtures/batstateu_bscpe_static_v1.approved.json
```

This uses the `jsonschema` library (declared in the repo root [`requirements.txt`](../../requirements.txt)).

## Bundle semantics

- **`max_units_by_semester_index`**: array of length `N`; index `i` is the unit cap for global semester index `i + 1`. **`N` is `MAX_SEMESTER`** for that bundle. Every `orig_sem` in `courses` must be ≤ `N` (the export script asserts this).
- **`metadata.source_pdf_sha256`**: hex SHA-256 of the source PDF, or `null` for static seeds (as in the golden fixture).
- **`prereq_aliases`** / **`year_standing_rules`**: optional at the schema level; the BatStateU fixture includes them so multi-program bundles can diverge later.
- **`graduation_trio`**: optional (length 3 if present). **Omitted** from the golden seed; trio enforcement may stay solver-default until a later phase.

## Solver compatibility

- **Requirement keys**: the canonical JSON uses **`pre-requisites`** and **`co-requisites`** only. The solver’s [`_get_req_list`](../algorithms_and_helpers/pos_helpers.py) also accepts alternate spellings when reading legacy dicts; approved bundles should not rely on those aliases.
- **Prerequisite shape**: each entry in `pre-requisites` is either a **string** (single course code or year-standing token, e.g. `"3rd Year Standing"`) or an **array of strings** (OR group: at least one alternative). Co-requisites are always a flat list of strings.
- **Normalization**: [`_normalize_code`](../algorithms_and_helpers/pos_helpers.py) collapses internal whitespace and uppercases. Course codes with punctuation (e.g. electives with colons) remain distinct; do not rely on case-only differences between codes.
- **Probation**: [`check_probation`](../Main-Solver/CP-SAT.py) buckets graded courses by `_first_semester` over the same `courses` dict loaded for the run. Changing `orig_sem` in a bundle changes probation grouping for the same transcript.

## Schema versioning

Bump **`schema_version`** (integer) when you make incompatible contract changes so loaders can branch safely.
