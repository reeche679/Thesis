from ortools.sat.python import cp_model

from courses import courses




model = cp_model.CpModel()

semester = {c: model.NewIntVar(1, 12, f"sem_{c}") for c in courses}

# Prerequisite constraints
for c, info in courses.items():
    for prereq in info["pre-requisites"]:
        # Filter out empty strings and validate prerequisite exists
        if prereq and prereq in courses:
            model.Add(semester[c] > semester[prereq])
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