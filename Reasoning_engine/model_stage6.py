"""
STAGE 6 MODEL
=============
Everything from Stage 5 (parity flip, stream filtering), PLUS the two
"special_requirement" structural rules. These are NOT prerequisite graph
edges -- nobody writes "FYP2 requires SC1, SC1b, SC2, FINAL_PEER..." as
individual edges in the curriculum data. Instead a course is flagged with
what it dynamically depends on, and the model builds the actual
constraints at solve time from whatever the OTHER courses turn out to be.

  ALL_STREAM_COURSES (FYP-II-style):
    Stays a real decision variable -- it has real credit hours and
    competes for real cap room. After every other course's slot_of[] is
    built, add: slot_of[this course] >= slot_of[every other course in
    the input]. Non-strict (>=), not strict (>), because courses running
    in the SAME final term as this one are legally concurrent -- >=
    naturally allows that without needing to separately identify "which
    courses happen to land in the same slot" (that's not even knowable
    before solving).

  ALL_COURSES (NEE-style):
    Excluded from the main decision-variable loop entirely -- it has 0
    credit hours (no cap interaction) and gates on literally everything,
    so per the project's own design it's cheaper and simpler to compute
    AFTER the main solve, not inside it. Computed as: the earliest slot,
    matching this course's own parity (home or alt), at or after the
    true maximum slot among every other course the student takes.
"""

from ortools.sat.python import cp_model
from model_stage2 import slot_to_year_sem, cap_for_slot
from model_stage4 import (
    validate_retake_limits, summarize_course_history, MAX_ATTEMPTS_DEFAULT,
)
from model_stage5 import valid_future_slots_for_course, filter_courses_for_student  # noqa: F401 (re-exported)


def earliest_slot_at_or_after(min_slot, allowed_parities, horizon_slots):
    """
    Smallest slot >= min_slot (inclusive) whose parity is in
    allowed_parities, or None if none exists within the horizon.
    Used for the post-hoc ALL_COURSES (NEE) placement.
    """
    for s in range(min_slot, horizon_slots + 1):
        if slot_to_year_sem(s)[1] in allowed_parities:
            return s
    return None


def build_and_solve(courses, horizon_slots, normal_caps, completed_courses=None,
                     now_slot=0, max_attempts=MAX_ATTEMPTS_DEFAULT, verbose=False):
    completed_courses = completed_courses or {}

    violations = validate_retake_limits(courses, completed_courses, max_attempts)
    if violations:
        return {
            "feasible": False,
            "status": "RETAKE_LIMIT_EXCEEDED",
            "violations": violations,
        }

    # --- Split out ALL_COURSES-scope courses (NEE-style) up front. They
    # never enter the CP-SAT model at all. ---
    post_hoc_codes = [
        c for c, info in courses.items()
        if info.get("special_requirement") == "ALL_COURSES"
    ]
    main_codes = [c for c in courses if c not in post_hoc_codes]

    model = cp_model.CpModel()

    slot_of = {}
    assign = {}
    valid_slots = {}
    decided_codes = []

    for c in main_codes:
        records = completed_courses.get(c, [])
        summary = summarize_course_history(records) if records else None

        if summary and summary["passed_slot"] is not None:
            slot_of[c] = model.NewConstant(summary["passed_slot"])
            continue

        decided_codes.append(c)
        valid_slots[c] = valid_future_slots_for_course(courses[c], horizon_slots, now_slot)
        if not valid_slots[c]:
            raise ValueError(f"{c} has no legal future slot left before horizon ends")

        for s in valid_slots[c]:
            assign[c, s] = model.NewBoolVar(f"assign_{c}_{s}")
        model.Add(sum(assign[c, s] for s in valid_slots[c]) == 1)

        slot_of[c] = sum(s * assign[c, s] for s in valid_slots[c])

    # --- Ordinary prerequisite edges ---
    for c in main_codes:
        for p in courses[c]["prereqs"]:
            model.Add(slot_of[c] > slot_of[p])

    # --- ALL_STREAM_COURSES dynamic requirement (FYP-II-style) ---
    for c in main_codes:
        if courses[c].get("special_requirement") == "ALL_STREAM_COURSES":
            for c2 in main_codes:
                if c2 != c:
                    model.Add(slot_of[c] >= slot_of[c2])

    # --- Credit caps ---
    for s in range(now_slot + 1, horizon_slots + 1):
        cap = cap_for_slot(s, normal_caps)
        contributors = [c for c in decided_codes if s in valid_slots[c]]
        if contributors:
            model.Add(
                sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= cap
            )

    # --- Objective (only over main_codes -- post-hoc courses aren't optimized) ---
    grad_slot = model.NewIntVar(1, horizon_slots, "grad_slot")
    for c in main_codes:
        model.Add(grad_slot >= slot_of[c])

    sum_of_slots = sum(slot_of[c] for c in main_codes)
    max_possible_sum = horizon_slots * max(len(main_codes), 1)

    sorted_codes = sorted(main_codes)
    code_index = {c: i for i, c in enumerate(sorted_codes)}
    max_index = max(len(main_codes), 1)
    tie_break_term = sum((max_index - code_index[c]) * slot_of[c] for c in main_codes)
    max_possible_tie_break = max_index * horizon_slots * max(len(main_codes), 1)

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

    schedule = {c: solver.Value(slot_of[c]) for c in main_codes}

    # --- Post-hoc ALL_COURSES placement (NEE-style) ---
    true_max_slot = max(schedule.values()) if schedule else now_slot
    for c in post_hoc_codes:
        allowed_parities = {courses[c]["semester_offered"]}
        if courses[c].get("alt_parity"):
            allowed_parities.add(courses[c]["alt_parity"])
        placed = earliest_slot_at_or_after(true_max_slot, allowed_parities, horizon_slots)
        if placed is None:
            return {
                "feasible": False,
                "status": "CAPSTONE_UNSCHEDULABLE",
                "violations": [
                    f"{c}: no slot at or after {true_max_slot} matches its required "
                    f"parity {allowed_parities} within the {horizon_slots}-slot horizon."
                ],
            }
        schedule[c] = placed

    graduation_slot = max(schedule.values()) if schedule else now_slot

    return {
        "feasible": True,
        "status": solver.StatusName(status),
        "schedule": schedule,
        "graduation_slot": graduation_slot,
    }