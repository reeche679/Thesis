# Thesis
Constraint Aware Program of Study Generator

## Run the project (Windows)

1. Clone the repo.
2. Open PowerShell in the project folder.
3. Run:
   ```powershell
   .\start.ps1
   ```
   On first run, the script will create a virtual environment, install dependencies (Django), run migrations, then start the server. Later runs just start the server.
4. Open http://127.0.0.1:8000/ in your browser.

**Requirements:** Python 3.10+ installed and on your PATH.

## Curriculum (Phase 1)

- **Models:** `pos.Program`, `pos.CurriculumVersion` (draft / approved / archived), optional `source_pdf`, `draft_json`, `approved_json`. `Program.default_curriculum_version` (nullable FK) may point at an **approved** version of the **same** program for solver/student fallback. `Student` has optional `program` and `selected_curriculum_version`; `POSRecord` has optional `curriculum_version` (POS generation can key off the student’s selected version via the solver bridge; storing the version on each history record is still optional follow-up work).
- **Admin:** `/admin/` — register `Program` and versions (paste JSON into `approved_json` for smoke tests). Approved bundles are validated against the Phase 0 schema at `../Solver-Side/curriculum_schema/approved_curriculum.schema.json` when that path exists.
- **Permissions:** Auth group **Curriculum managers** (created by migration) includes `pos.manage_curriculum_versions` plus program/version change permissions. Add staff users to **Program.curriculum_managers** so they only manage assigned programs. Only **superusers** can add new programs.
- **Seed fixture:** From `Django-Back-Front` after repo clone:
  ```powershell
  python manage.py seed_curriculum_fixture
  ```
  Loads `../Solver-Side/curriculum_schema/fixtures/batstateu_bscpe_static_v1.approved.json` into an approved `CurriculumVersion`. Use `--replace` to overwrite an existing same-label version.

### Phase 2 (student picker + REST, basic)

- **UI:** After login, open **Curriculum** in the nav (`/curriculum/`). Lists approved `CurriculumVersion` rows for the student’s `program`; **Save** sets `Student.selected_curriculum_version` (same effect as choosing it in admin).
- **REST (session cookie, same as the rest of the POS app):**
  - `GET /api/curriculum/me/` — `{ program, selected_curriculum_version, approved_versions[] }`
  - `POST /api/curriculum/set-active/` — JSON `{ "curriculum_version_id": <int> | null }` (requires CSRF token header for browser `fetch`, or use the Curriculum page which sends it)
- **Prerequisites:** `Student.program` must point at the same `Program` as your approved versions (admin). Use `seed_curriculum_fixture` or admin to create programs and versions. In **Student** admin, **Program** and **Approved curriculum** use autocomplete: type part of the program **name** or **slug** (for example `bscpe` or `batstate`), not only the slug in a raw-ID box (older setups expected the numeric **primary key** there).

### Who can manage curricula?

- **Staff:** Managing catalogue data is still primarily **Django admin** (`/admin/`): edit `Program`, `CurriculumVersion`, paste JSON into `draft_json` / `approved_json` (validated when status is Approved). Optional `source_pdf` is a model file field, not a separate upload pipeline yet.
- **Curriculum managers** (auth group + listed on **Program → curriculum managers**) can create/edit **versions only for programs they are assigned to**. They do **not** create new programs (that is **superuser** only).
- **Superusers** can do everything in admin, including creating programs and any version, without being on the M2M list.

### Can normal (student) users upload or see an upload button?

- **No upload for students.** Students pick among **already approved** versions for their program on the **Curriculum** page (`/curriculum/`). Uploading or editing JSON bundles remains a **staff / curriculum manager** task (admin today; dedicated staff UI can come later).

### One-command dev bootstrap (staff + group + program assignment)

After `migrate` and optional `seed_curriculum_fixture`:

```powershell
$env:CURRICULUM_STAFF_PASSWORD = "choose-a-dev-password"
python manage.py bootstrap_curriculum_staff --username curriculum_editor
```

Then open `/admin/`, log in as `curriculum_editor`, and confirm **Programs** and **Curriculum versions** appear and are editable for `bscpe_batstateu`.

If the **Curriculum managers** group is missing (empty auth tables), `bootstrap_curriculum_staff` creates it and attaches the same permissions as migration `0002_curriculum_manager_group`.

### Phase 3 (solver + bridge)

- **Solver** (`generate_pos`): optional payload key **`curriculum_bundle`** — full approved JSON (Phase 0 schema). When omitted, behavior matches the static `courses.py` + unit caps.
- **Django bridge**: optional **`curriculum_version_id`** on the solver payload is resolved to `curriculum_bundle` from the database before `generate_pos` runs (solver stays ORM-free). Reloading the cached `runpy` namespace also keys off the latest `CurriculumVersion.updated_at` so curriculum edits take effect without touching `courses.py` mtimes.
- **Students**: if **`Student.selected_curriculum_version`** is set, that id is attached to generate/validate payloads and used for **`get_solver_metadata`** in prereq validation and feasible-move helpers. If it is **not** set but **`Student.program.default_curriculum_version`** is set to an approved version, that default id is used the same way.

### Phase 4 (POS persistence + audit)

- After a successful **Generate** (`generate_pos_api`), **`last_generated_payload`** in the session includes **`curriculum_version_id`** and **`approved_json_sha256`** (canonical SHA-256 of `approved_json` for that version) for consistent **feasible** / **preview** reuse.
- **`POSRecord`** stores **`curriculum_version`** (FK) and **`approved_json_sha256`** when the user **saves to history** or **downloads preview PDF** (history row). Loading a record from history merges those fields back into **`last_generated_payload`** when the snapshot omits them (older rows).

### Phase 5 (PDF → draft rows, staff)

- **Auth:** Django **`User`** with **`pos.manage_curriculum_versions`** (Curriculum managers group) **or superuser**. Use the dedicated portal: **`/staff/login/`** (not the student SR-code login). After sign-in, open **`/staff/curriculum/upload/`**. **Superusers** can pick **any** program; managers only see programs where they appear under **Program → curriculum managers**.
- **Sign out:** **`/staff/logout/`** (or the link on the upload page). Django admin remains at **`/admin/`** if you prefer that UI for edits.
- **Upload:** `POST` multipart to **`/staff/curriculum/api/upload-pdf/`** with **`program_id`**, **`file`** (`.pdf`), optional **`version_label`**. Creates a **`CurriculumVersion`** in **`draft`**, saves **`source_pdf`**, **`source_pdf_sha256`**, and **`draft_json`** (`extracted_rows`, `warnings`, `extraction_meta`, **`suggested_graph`: null**).
- **OCR:** If pdfplumber finds **no rows**, **very little text** (fewer than 500 characters), or **few rows** with sparse text, the pipeline **rasterizes pages (PyMuPDF)** and runs **Tesseract**. Install the [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) binary and ensure it is on **`PATH`** (on Windows, add the install folder). Only the **first 40 pages** are OCR’d for performance. OCR lines reuse the same **term band** state and **post-processing** as native text (with **lower confidence** and more **warnings** when heuristics are weak).
- **Read / edit draft:** `GET` or `PATCH` **`/staff/api/curriculum/version/<id>/draft/`** (same portal permission rules; **draft** status only). **`PATCH`** accepts JSON `{ "extracted_rows": [...] }` (stable **`draft_row_id`** on each row); the server re-runs the analyzer after each save. **`POST /staff/api/curriculum/version/<id>/analyze/`** recomputes **`draft_analysis`** only. **`POST …/approve/`** validates and sets **`approved_json`** + **`status=approved`**. Minimal HTML review: **`/staff/curriculum/version/<id>/review/`** (see Phase 6 below).

#### `draft_json.extracted_rows` (BSU-style tables)

- **`term_index` / `term_label`:** **`term_index`** is a **positional band index** (1-based): the first **distinct** structural heading encountered in top-to-bottom reading order is `1`, the next new heading is `2`, and so on. It is **not** a calendar-normalized semester from a fixed lookup table. Repeated **identical** heading text (e.g. the same banner on the next page) does **not** advance the counter. **`term_label`** holds the cleaned heading line used for that band (human-readable). **`term`** mirrors **`term_label`** when `term_index > 0`. Midterm / summer blocks are bands when matched by the heading patterns.
- **Term backfill from `extract_text()`:** When pdfplumber **tables** omit section-heading rows but the PDF’s **text layer** still contains those headings, the extractor builds a **single document-level course timeline** from all pages’ text (in order, optionally `layout=True` when supported) and **sequence-aligns** `pdf_table` rows. **`term_index` / `term_label` / `term` are only filled from the timeline when they are still empty** (`term_index == 0`); rows that already got a band from the table path are left unchanged. Successful fills set `source.term_backfill` to **`document_text_timeline`** (native PDF) or **`ocr_text_timeline`** (OCR). **OCR** band detection is **noisier** than native text (line breaks, errors) and can mis-order vs tables; confidence is reduced for backfilled rows. Two-column reading order can still misalign edge cases; see **`draft_json.warnings`** for alignment misses. Loose `_TERM_LINE` matches are only used for lines **≤ 140** characters (after normalizing `|` cell separators in tables). Some BSU-style PDFs emit **run-on** headings (`FIRSTYEAR`, `FirstSemester`) with **no spaces**; those are paired into one band label (e.g. `FIRSTYEAR FirstSemester`).
- **Column mapping:** For each pdfplumber table, the extractor scans the **first few rows** for header-like cells and maps columns by synonym (case-insensitive), including **course / code**, **title / description**, **LEC**, **LAB**, **UNITS / credits**, **prerequisite**, **corequisite**, and optional **max load** text. The map is **rebuilt for every table** (handles repeated headers across pages).
- **`units`:** Taken from the **UNITS** (or synonym) column when detected. If that column is missing, a **fallback** looks for a small numeric (≤24) in columns **after** LEC/LAB. Values **greater than 24**, or large numbers on **OJT / internship**-style rows, are treated as **hours**, not credits, and cleared with a **warning**.
- **`lec` / `lab`:** Optional **verbatim** cell text from those columns when present; they are **not** used to compute units or anything else.
- **`prerequisite_codes` / `corequisite_codes`:** Parsed from the **mapped** prerequisite and corequisite columns via a course-code regex; **`raw_prerequisites`** / **`raw_corequisites`** keep the **original cell text** for audit.

#### Manual re-upload verification (after extractor changes)

1. Re-upload the same curriculum PDF used for regression (e.g. BSU BSCpE).
2. Spot-check rows that previously had wrong **`units`** (e.g. LEC hours picked as units): values should align with the PDF **UNITS** column when present.
3. Confirm **`term_index`** / **`term_label`** on course rows under each semester and **midterm** band.
4. Confirm **`prerequisite_codes`** / **`corequisite_codes`** list real codes (e.g. `MATH 101`), not prose from the title column.
5. Open **`draft_json.warnings`** for OJT or ambiguous rows.
6. On a **text-native** PDF (no OCR), confirm behavior is unchanged aside from improved fields.

### Phase 6 (draft analyze, review UI, approve, program default)

- **`draft_row_id`:** Every `extracted_rows[]` entry gets a stable UUID at **upload** (and any still-missing id is filled on **PATCH**). The analyzer and review UI key per-row output by this id.
- **`orig_sem` (hybrid):** Default maps **positional term bands** to `1..N` in order of first appearance when rows are sorted by **`term_index`**, then **`source.page`**, **`source.table`**, **`source.row_index`**. Per-row override: set **`orig_sem_override`** (or **`orig_sem`**) on the row before approve.
- **`draft_analysis`:** Written by `pos/curriculum_draft_analyze.py` after upload, after each **PATCH** save, and on **`POST …/analyze/`**. Holds `run_at`, `analyzer_version`, per-row hints (`orig_sem_suggested`, `resolved_pre` / `resolved_co`, `unresolved`, …), and optional bundle-level **`year_standing_rules_suggested`** / **`prereq_aliases_suggested`**.
- **`category`:** On approve, every course is set to **`Rigid`** (schema enum placeholder for v1).
- **`max_units_by_semester_index`:** v1 builds an array of length **`max(orig_sem)`** with each entry **`24`** (`MAX_UNITS_PER_SEMESTER_V1` in `pos/curriculum_draft_to_approved.py`).
- **Approve:** `POST /staff/api/curriculum/version/<id>/approve/` runs `pos/curriculum_draft_to_approved.py` → **`validate_approved_bundle`** (same schema as admin). On success: **`approved_json`** is set and **`status=approved`**. Policy: do not edit `approved_json` in place for further changes; create a new **`CurriculumVersion`**. Multiple approved rows per program are allowed; **`Program.default_curriculum_version`** disambiguates the program-wide default.
- **Staff APIs:** `GET` / `PATCH` **`/staff/api/curriculum/version/<id>/draft/`**, **`POST …/analyze/`**, **`POST …/approve/`** (same auth rules as Phase 5). **`GET`** includes `draft_analysis` when present.
- **Review UI:** **`/staff/curriculum/version/<id>/review/`** (linked from upload success as **`review_url`**) — minimal table + **Save** / **Re-analyze** / **Approve** via `fetch`.
- **Program default:** Set **`Program.default_curriculum_version`** in Django admin (filtered to approved versions for that program). Validation enforces same program + approved status.
