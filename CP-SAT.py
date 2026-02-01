from ortools.sat.python import cp_model

import math

from courses import PREREQ_ALIASES, YEAR_STANDING_RULES, courses

import units_and_year_standings as u


model = cp_model.CpModel() ##Model Init##

year_units = {}
year_courses = {}
for code, info in courses.items():
    year_level = info.get("year_level")
    units = info.get("units", 0)
    if year_level is None:
        continue
    year_units[year_level] = year_units.get(year_level, 0) + units
    year_courses.setdefault(year_level, []).append(code)

semester = {c: model.NewIntVar(1, 12, f"sem_{c}") for c in courses} ##Semester Init##

# for each global semester s:
total_units_in_semester = {s: model.NewIntVar(0, u.max_units_for_sem(s), f"total_units_in_sem_{s}") for s in range(1, 13)}

for s in range(1, 13):
    model.Add(total_units_in_semester[s] <= u.max_units_for_sem(s))

# Prerequisite constraints
def add_year_standing_requirement(course: str, required_year: int, enforce_if=None) -> None:
    required_total = year_units.get(required_year, 0)
    required_units = math.ceil(0.9 * required_total)
    if required_units <= 0:
        return

    before_vars = []
    for yc in year_courses.get(required_year, []):
        b = model.NewBoolVar(f"{yc}_before_{course}")
        model.Add(semester[yc] <= semester[course] - 1).OnlyEnforceIf(b)
        model.Add(semester[yc] >= semester[course]).OnlyEnforceIf(b.Not())
        before_vars.append((b, courses[yc]["units"]))

    ct = model.Add(sum(units * b for b, units in before_vars) >= required_units)
    if enforce_if is not None:
        ct.OnlyEnforceIf(enforce_if)


for c, info in courses.items():
    for prereq in info["pre-requisites"]:
        if not isinstance(prereq, (list, tuple)) and prereq in PREREQ_ALIASES:
            prereq = PREREQ_ALIASES[prereq]
        # Support "any-of" prerequisites via list/tuple entries.
        if isinstance(prereq, (list, tuple)):
            choices = []
            for alt in prereq:
                if not alt:
                    continue
                choice = model.NewBoolVar(f"{c}_prereq_{alt}")
                if alt in courses:
                    model.Add(semester[c] > semester[alt]).OnlyEnforceIf(choice)
                elif alt in YEAR_STANDING_RULES:
                    add_year_standing_requirement(c, YEAR_STANDING_RULES[alt], enforce_if=choice)
                else:
                    print(f"Warning: Prerequisite '{alt}' for '{c}' not found in courses dictionary")
                    continue
                choices.append(choice)
            if choices:
                model.Add(sum(choices) >= 1)
        else:
            # Filter out empty strings and validate prerequisite exists
            if prereq and prereq in courses:
                model.Add(semester[c] > semester[prereq])
            elif prereq in YEAR_STANDING_RULES:
                add_year_standing_requirement(c, YEAR_STANDING_RULES[prereq])
            elif prereq:  # Non-empty but doesn't exist in courses
                print(f"Warning: Prerequisite '{prereq}' for '{c}' not found in courses dictionary")

# Offered semester constraints
for c, info in courses.items():
    offered = info["offered_sems"]
    if isinstance(offered, int):
        model.Add(semester[c] == offered)
    else:
        model.AddAllowedAssignments([semester[c]], [[s] for s in offered])

solver = cp_model.CpSolver()
status = solver.Solve(model)

if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
    print("Feasible POS Schedule:")
    for c, var in semester.items():
        print(f"{c} → Semester {solver.Value(var)}")
else:
    print("No valid schedule found.")


