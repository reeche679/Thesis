# CP-SAT.py — Line-by-Line Explanation

## Imports (1–5)
- **1** `math`: used for `math.ceil` in year-standing (90% rule).
- **2** `cp_model`: OR-Tools CP-SAT solver (variables, constraints, solve).
- **4** Curriculum data: course list, prerequisite aliases, year-standing rules.
- **5** Unit caps and semester indexing (e.g. `max_units_for_sem`, `MAX_SEMESTER`).

---

## Helper: `_first_semester` (7–13)
- **Purpose:** Get the first semester a course is offered (for “past semesters” and offering rules).
- **9–10:** If `orig_sem` exists, return it.
- **11–12:** Else if `offered_sems` exists, return its minimum (if list) or the value (if int).
- **13:** Else `None` (course not in curriculum semester scheme).

---

## Helper: `_sem_label` (16–20)
- **Purpose:** Turn semester index (1–18) into a label like "1st Year 1st Sem", "2nd Year Summer".
- **18:** Map sem → (year_level, term_in_year) via `units_and_year_standings`.
- **19–20:** Ordinals for year (1st–6th) and term (1st Sem, 2nd Sem, Summer).

---

## Helper: `_normalize_code` (24–26)
- **Purpose:** Normalize course/standing codes (trim, collapse spaces, uppercase) for matching.

---

## Helper: `_get_req_list` (29–40)
- **Purpose:** Get prereq/co-req list from a course dict, supporting several key names.
- **31–32:** Try keys in order (`pre-requisites`, `pre_requisites`, etc.).
- **33–39:** Return empty list if None; list/tuple/set as list; single string as one-element list.

---

## Helper: `_log_infeasible` (44–92)
- **Purpose:** When the solver says “no solution”, print why and what to check.
- **46–53:** Map solver status enum to string; print header and failed courses.
- **64–72:** Optionally print solver `ResponseStats()`.
- **74–84:** Fixed “possible reasons” (unit cap, offering, prereqs, year standing, co-reqs).
- **87–91:** Print unit cap per semester from current to `MAX_SEMESTER`.

---

## Main: `run_pos_generator` (95–301)

### 1. Input & data (98–146)
- **99–102:** Read current semester index (1–MAX_SEMESTER); default 1 on invalid input.
- **104–105:** Lists for passed and failed courses (from grades).
- **107–113:** Build `past_sem_courses`: courses with `first_sem < current_sem`, sorted by semester then code.
- **115–118:** If no past courses (e.g. semester 1), pause with message.
- **119–146:** Else, for each past-semester course: print semester header when it changes, prompt for grade; 1–3 = pass, 5/INC = fail; empty = not taken.

### 2. Model setup & year aggregates (148–169)
- **141:** Create empty CP-SAT model.
- **143–154:** Precompute per-year: total units, list of courses, and earned units (from passed only).
- **156–169:** Build normalized lookup: code → course, alias → list, standing name → year index; define `resolve_course` and `resolve_year_standing` for prereq/co-req matching.

### 3. Variables (171–177)
- **172–176:** For each course: if passed, `semester[code] = 0` (fixed); else `semester[code]` in `[current_sem, MAX_SEMESTER]` (when it can be taken).

### 4. Constraints (179–266)

**A. Offering (181–194)**  
- For each unscheduled course, restrict its semester to “allowed” ones by term type.  
- **187:** `s % 3` = term type (1=1st, 2=2nd, 0=summer); same for `orig % 3`.  
- **188–193:** Rigid = same type only; Flexible = regular↔regular or summer↔summer; Bottleneck = same or summer.  
- **194:** Add table constraint: `semester[c]` in `valid_sems`.

**B. Unit load (196–206)**  
- For each semester `s`, for each unscheduled course, a Bool “course in sem s”; link to `semester[c] == s`.  
- Sum of (units × in_sem) over courses ≤ `max_units_for_sem(s)`.

**C. Prereqs & year standing (208–256)**  
- **209–223:** `add_year_standing(course, req_year, enforce_if)`: require at least 90% of that year’s units “before” the course (via auxiliary Bools and linear constraint); skip if already earned or req_units ≤ 0.  
- **225–256:** For each course and each prereq (after resolving aliases):  
  - If prereq is a **list** (alternatives): at least one alternative must be satisfied (course before or year standing); each gets a Bool and “sum ≥ 1”.  
  - If **single** prereq: resolve to course or year standing; add “semester[c] > semester[prereq]” or `add_year_standing`.  
  - Unknown prereq → warning.

**D. Co-requisites (258–266)**  
- For each co-req: `semester[c] >= semester[coreq_course]` (same or later). Unknown → warning.

### 5. Objective (268–278)
- **270–274:** `max_sem_used` ≥ every unscheduled course’s semester.
- **276–278:** Minimize `max_sem_used` so the plan ends as early as possible; if there are failed courses, secondary term is sum of their semesters (earlier retakes preferred when max semester is the same).

### 6. Solve & output (280–301)
- **281–282:** Create solver, solve model.
- **284–299:** If OPTIMAL or FEASIBLE: build lines (header + per semester: label, then each course with code, title, units); print as one block.
- **300–301:** Else call `_log_infeasible`.
- **303–304:** Entry point: run generator when script is executed.

---

## Summary flow
1. Get current semester and grades for past semesters (passed/failed).
2. Build CP-SAT model: variables = semester per course; constraints = offering windows, unit caps, prereqs, year standing, co-reqs.
3. Minimize “latest semester used” (and retake semesters if any).
4. Print POS with full course names and “1st Year 1st Sem” style labels, or infeasibility log.
