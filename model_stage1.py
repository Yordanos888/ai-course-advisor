"""
STAGE 1 MODEL
=============
Encodes exactly two campus rules:
    - prerequisite ordering
    - per-semester credit cap
plus the structural rule that's "free" (baked into variable domains):
    - semester parity (a course can only sit in a slot whose parity matches
      its semester_offered)
 
No failures, no retakes, no streams, no drops yet. Every course is taken
exactly once, and must be taken (completed_courses is unused this stage but
the parameter exists now so Stage 2 doesn't require a function-signature
rewrite).
 
ENCODING
--------
For each course c, and each slot s whose parity matches c's
semester_offered, a boolean variable assign[c, s] = "course c is taken in
slot s".
 
Exactly one such variable is true per course (it's taken exactly once,
in exactly one legal slot).
 
slot_of[c] is not a real solver variable -- it's a *linear expression*
built from the assign[] booleans: sum(s * assign[c, s] for s in valid
slots). CP-SAT treats this as a linear term, so it can be compared,
bounded, and put in the objective just like a real IntVar, without us
having to maintain an equality link by hand.
"""

from ortools.sat.python import cp_model

def slot_to_year_sem(slot: int):
    """ slot 1,2,3,4,5,6,7,8 -> (year, semester_in_year 1 or 2)"""
    year = (slot + 1) // 2
    sem = 1 if slot % 2 == 1 else 2
    return year, sem

def valid_slots_for_course(course, horizon_slots):
    """slots whose parity matches this course's semester_offered"""
    parity = course['semester_offered']  # 1 or 2
    return [s for s in range(1, horizon_slots + 1) if slot_to_year_sem(s)[1] == parity]

def cap_for_slot(slot, normal_caps, default_cap=21):
    """ look up the credit cap for a slot via its(year, semester) key """
    year, sem = slot_to_year_sem(slot)
    return normal_caps.get((year,sem), default_cap)

def build_and_solve(courses, horizon_slots, normal_caps, completed_courses=None, verbose=False):
    completed_courses = completed_courses or {}

    model = cp_model.CpModel()

    course_codes = list(courses.keys())

    # assign[c, s] = true if course c is scheduled in slot s
    assign = {}
    valid_slots = {}
    for c in course_codes:
        valid_slots[c] = valid_slots_for_course(courses[c], horizon_slots)
        for s in valid_slots[c]:
            assign[c, s] = model.NewBoolVar(f"assign_{c}_{s}")

    # constraint-1: every course taken exactly once
    for c in course_codes:
        model.Add(sum(assign[c,s] for s in valid_slots[c]) == 1)

    # slot_of[c] as a linear expression not a separate variable
    slot_of = {
        c: sum(s* assign[c,s] for s in valid_slots[c])
        for c in course_codes
    }

    # constraint-2: prerequisites must be taken in a strictly earlier slot
    for c in course_codes:
        for p in courses[c]['prereqs']:
            model.Add(slot_of[c] > slot_of[p])

    # constraint-3: per-slot credit cap
    for s in range(1, horizon_slots +1):
        cap = cap_for_slot(s, normal_caps)
        courses_that_could_land_here = [c for c in course_codes if s in valid_slots[c]]
        if courses_that_could_land_here:
            model.Add(
                sum(courses[c]['credit_hours'] * assign[c, s]
                    for c in courses_that_could_land_here)
                    <= cap
            ) 

    # objective: graduate as early as possible: primary goal
    grad_slot = model.NewIntVar(1, horizon_slots, "grad_slot")
    for c in course_codes:
        model.Add(grad_slot >= slot_of[c])

    # objective-2: tie breaker goal: fornt_load courses as early as legally possible
    # among schedules that already graduate on the same slot.

    # Weighted single objective: primary goal dominates by using a
    # multiplier bigger than the max possible value of the secondary term.   
    sum_of_slots = sum(slot_of[c] for c in course_codes)
    max_possible_sum = horizon_slots * len(course_codes)

    # objective-3: among ties, prefer placing lower-indexed course codes into
    # earlier slots.
    sorted_codes = sorted(course_codes)
    code_index = {c: i for i, c in enumerate(sorted_codes)}
    max_index = len(course_codes)

    tie_break_term = sum((max_index - code_index[c]) * slot_of[c] for c in course_codes)
    max_possible_tie_break = max_index * horizon_slots * len(course_codes)

    model.Minimize(
        grad_slot * (max_possible_sum +1) * (max_possible_tie_break + 1)
        + sum_of_slots * (max_possible_tie_break + 1) + tie_break_term
    )

    solver = cp_model.CpSolver()
    if verbose:
        solver.parameters.log_search_progress = True
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": False, 'status': solver.StatusName(status)}

    schedule = {}
    for c in course_codes:
        for s in valid_slots[c]:
            if solver.Value(assign[c, s]) == 1:
                schedule[c] = s
                break

    return {
        "feasible": True,
        "status": solver.StatusName(status),
        "schedule": schedule,
        "graduation_slot": solver.Value(grad_slot),
    }