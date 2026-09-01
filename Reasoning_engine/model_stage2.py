"""
STAGE 2 MODEL
=============
Everything from Stage 1 (prerequisites, credit caps, parity), PLUS:

  - a NOW_SLOT boundary: slots <= now_slot are history, not decisions.
    Every course still to be scheduled can only land in a slot > now_slot.

  - completed_courses handling:
      status "PASS"   -> course's slot is a FIXED CONSTANT (its real
                         historical slot). It is not a decision variable,
                         but it still participates in prerequisite
                         constraints for anything that depends on it.
      status "FAILED" -> the historical attempt already happened (we know
                         it consumed a slot in the past), but the course
                         still needs exactly one more attempt scheduled in
                         the future -- treated the same as an unstarted
                         course for scheduling purposes, EXCEPT its domain
                         is future slots (like everything else), same as
                         if it had never been attempted. Stage 4 will use
                         this same history to enforce the 3-attempt limit;
                         for now we just need the domino effect to work.
      (not present)    -> unstarted course, exactly one future attempt,
                         same as Stage 1 but restricted to future slots.

FIXED-CONSTANT TRICK: rather than mixing plain Python ints with CP-SAT
linear expressions (which gets error-prone across comparisons), every
PASSED course gets a real IntVar pinned to a single value via
model.NewConstant(). This keeps every prerequisite constraint uniform --
"model.Add(slot_of[c] > slot_of[p])" works identically whether p is a
live decision or a historical fact.
"""

from ortools.sat.python import cp_model


def slot_to_year_sem(slot: int):
    year = (slot + 1) // 2
    sem = 1 if slot % 2 == 1 else 2
    return year, sem


def valid_future_slots_for_course(course, horizon_slots, now_slot):
    """Slots with matching parity AND strictly after now_slot."""
    parity = course["semester_offered"]
    return [
        s for s in range(now_slot + 1, horizon_slots + 1)
        if slot_to_year_sem(s)[1] == parity
    ]


def cap_for_slot(slot, normal_caps, default_cap=21):
    year, sem = slot_to_year_sem(slot)
    return normal_caps.get((year, sem), default_cap)


def build_and_solve(courses, horizon_slots, normal_caps, completed_courses=None,
                     now_slot=0, verbose=False):
    completed_courses = completed_courses or {}

    model = cp_model.CpModel()
    course_codes = list(courses.keys())

    slot_of = {}          # course_code -> IntVar (real decision or pinned constant)
    assign = {}           # (course_code, slot) -> BoolVar, only for courses being decided
    valid_slots = {}       # course_code -> list of slots it could still land in

    decided_codes = []     # courses the solver actually chooses a slot for

    for c in course_codes:
        record = completed_courses.get(c)

        if record and record["status"] == "PASS":
            # Historical fact: pin to its real slot, not a decision.
            slot_of[c] = model.NewConstant(record["slot"])
            continue

        # Either FAILED-before (needs a retake) or never attempted --
        # both are decided the same way: exactly one future attempt.
        decided_codes.append(c)
        valid_slots[c] = valid_future_slots_for_course(courses[c], horizon_slots, now_slot)
        if not valid_slots[c]:
            raise ValueError(f"{c} has no legal future slot left before horizon ends")

        for s in valid_slots[c]:
            assign[c, s] = model.NewBoolVar(f"assign_{c}_{s}")
        model.Add(sum(assign[c, s] for s in valid_slots[c]) == 1)

        slot_of[c] = sum(s * assign[c, s] for s in valid_slots[c])

    # --- Constraint: prerequisites must be taken in a strictly earlier slot ---
    for c in course_codes:
        for p in courses[c]["prereqs"]:
            model.Add(slot_of[c] > slot_of[p])

    # --- Constraint: per-FUTURE-slot credit cap (past slots are history,
    # not something the solver controls or needs to re-validate) ---
    for s in range(now_slot + 1, horizon_slots + 1):
        cap = cap_for_slot(s, normal_caps)
        contributors = [c for c in decided_codes if s in valid_slots[c]]
        if contributors:
            model.Add(
                sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= cap
            )

    # --- Objective: same 3-tier priority as Stage 1 ---
    # 1) graduate as early as possible
    # 2) among ties, front-load total slot-sum as early as possible
    # 3) among remaining ties, break deterministically by course code order
    grad_slot = model.NewIntVar(1, horizon_slots, "grad_slot")
    for c in course_codes:
        model.Add(grad_slot >= slot_of[c])

    sum_of_slots = sum(slot_of[c] for c in course_codes)
    max_possible_sum = horizon_slots * len(course_codes)

    sorted_codes = sorted(course_codes)
    code_index = {c: i for i, c in enumerate(sorted_codes)}
    max_index = len(course_codes)
    tie_break_term = sum((max_index - code_index[c]) * slot_of[c] for c in course_codes)
    max_possible_tie_break = max_index * horizon_slots * len(course_codes)

    model.Minimize(
        grad_slot * (max_possible_sum + 1) * (max_possible_tie_break + 1)
        + sum_of_slots * (max_possible_tie_break + 1)
        + tie_break_term
    )

    solver = cp_model.CpSolver()
    if verbose:
        solver.parameters.log_search_progress = True
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": False, "status": solver.StatusName(status)}

    schedule = {c: solver.Value(slot_of[c]) for c in course_codes}

    return {
        "feasible": True,
        "status": solver.StatusName(status),
        "schedule": schedule,
        "graduation_slot": solver.Value(grad_slot),
    }