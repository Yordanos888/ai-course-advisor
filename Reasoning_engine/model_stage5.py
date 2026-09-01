"""
STAGE 5 MODEL
=============
Everything from Stage 4 (retake limits, drop legality), PLUS:

  - PARITY FLIP: a course can declare course["alt_parity"] = the OTHER
    parity it's also legally schedulable under (cross-department common
    courses, per campus rules: "schedulable via their home parity OR the
    flipped parity from the partner department"). valid_future_slots_for_course
    now checks membership in a SET of allowed parities, not equality
    against a single one.

  - STREAM FILTERING: courses can declare course["stream"] = None (common
    to all streams) or a specific stream name. filter_courses_for_student()
    returns only the subset relevant to one student -- a stream-B-only
    course should never even be considered for a stream-A student, not
    just "correctly scheduled out of the way."

  - STREAM CHOICE: compare_streams() runs build_and_solve once per
    candidate stream and returns all results side by side. An undecided
    student's stream is NOT picked by the solver -- that's a human
    decision the solver informs, not makes.

validate_retake_limits / summarize_course_history are imported unchanged
from model_stage4 -- the retake-limit rule has nothing to do with streams
or parity, so there's no reason for that logic to be touched here.
"""

from ortools.sat.python import cp_model
from model_stage2 import slot_to_year_sem, cap_for_slot
from model_stage4 import (
    validate_retake_limits, summarize_course_history, MAX_ATTEMPTS_DEFAULT,
)


def valid_future_slots_for_course(course, horizon_slots, now_slot):
    """
    Slots strictly after now_slot whose parity is EITHER the course's
    home semester_offered OR its alt_parity (if it has one).
    """
    allowed_parities = {course["semester_offered"]}
    if course.get("alt_parity"):
        allowed_parities.add(course["alt_parity"])
    return [
        s for s in range(now_slot + 1, horizon_slots + 1)
        if slot_to_year_sem(s)[1] in allowed_parities
    ]


def filter_courses_for_student(all_courses, student_stream):
    """
    Returns the subset of all_courses this student actually needs to plan
    for: courses with stream=None (common to everyone) plus courses whose
    stream matches this student's stream. Courses belonging to OTHER
    streams are excluded entirely -- not scheduled, not considered, not
    present in the output at all.
    """
    return {
        c: info for c, info in all_courses.items()
        if info.get("stream") in (None, student_stream)
    }


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

    model = cp_model.CpModel()
    course_codes = list(courses.keys())

    slot_of = {}
    assign = {}
    valid_slots = {}
    decided_codes = []

    for c in course_codes:
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

    for c in course_codes:
        for p in courses[c]["prereqs"]:
            model.Add(slot_of[c] > slot_of[p])

    for s in range(now_slot + 1, horizon_slots + 1):
        cap = cap_for_slot(s, normal_caps)
        contributors = [c for c in decided_codes if s in valid_slots[c]]
        if contributors:
            model.Add(
                sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= cap
            )

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


def compare_streams(all_courses, streams, horizon_slots, normal_caps,
                     completed_courses=None, now_slot=0, max_attempts=MAX_ATTEMPTS_DEFAULT):
    """
    For an undecided student: solve once per candidate stream, return all
    results side by side. Does NOT pick a stream -- that's for the
    student/advisor to decide, informed by this comparison.
    """
    results = {}
    for stream in streams:
        filtered = filter_courses_for_student(all_courses, stream)
        results[stream] = build_and_solve(
            courses=filtered,
            horizon_slots=horizon_slots,
            normal_caps=normal_caps,
            completed_courses=completed_courses,
            now_slot=now_slot,
            max_attempts=max_attempts,
        )
    return results