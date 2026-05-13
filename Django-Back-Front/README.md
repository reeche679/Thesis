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

- **Models:** `pos.Program`, `pos.CurriculumVersion` (draft / approved / archived), optional `source_pdf`, `draft_json`, `approved_json`. `Student` has optional `program` and `selected_curriculum_version`; `POSRecord` has optional `curriculum_version` (POS generation can key off the student’s selected version via the solver bridge; storing the version on each history record is still optional follow-up work).
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
- **Students**: if **`Student.selected_curriculum_version`** is set, that id is attached to generate/validate payloads and used for **`get_solver_metadata`** in prereq validation and feasible-move helpers.

### Phase 4 (POS persistence + audit)

- After a successful **Generate** (`generate_pos_api`), **`last_generated_payload`** in the session includes **`curriculum_version_id`** and **`approved_json_sha256`** (canonical SHA-256 of `approved_json` for that version) for consistent **feasible** / **preview** reuse.
- **`POSRecord`** stores **`curriculum_version`** (FK) and **`approved_json_sha256`** when the user **saves to history** or **downloads preview PDF** (history row). Loading a record from history merges those fields back into **`last_generated_payload`** when the snapshot omits them (older rows).

### Phase 5 (PDF → draft rows, staff)

- **Auth:** Django **`User`** (curriculum manager or superuser), **not** the student SR-code session. Log in at **`/admin/login/`** (same browser session), then open **`/staff/curriculum/upload/`**.
- **Upload:** `POST` multipart to **`/staff/curriculum/api/upload-pdf/`** with fields **`program_id`** (integer), **`file`** (`.pdf`), optional **`version_label`**. Creates a **`CurriculumVersion`** in **`draft`**, saves **`source_pdf`**, sets **`source_pdf_sha256`**, and fills **`draft_json`** with **`extracted_rows`**, **`warnings`**, **`extraction_meta`** (`library: pdfplumber`), and **`suggested_graph`: null** (Phase 6).
- **Read draft:** `GET /staff/api/curriculum/version/<id>/draft/` (same permission rules; **draft** status only).
- **Quality:** Extraction is heuristic (tables + text lines); expect warnings and imperfect rows until you tune parsers on real samples in Phase 6.
