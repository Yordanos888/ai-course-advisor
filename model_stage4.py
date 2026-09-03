"""
STAGE 4a/4b MODEL -- retake limit, non-droppable courses, drop legality
==========================================================================
STAGE 4a (unchanged from before): completed_courses as attempt-history
lists, pre-solve retake-limit validation with a diagnosed reason instead
of a bare INFEASIBLE.

STAGE 4b (new): check_drop_legality() -- a separate, one-off check for a
SPECIFIC proposed action ("can the student drop course X out of their
CURRENT in-progress semester right now"), not part of the main multi-year
forward solve. Three rules, checked in order:

  1. The course must be droppable (courses[c]["droppable"], mirroring
     Course.is_droppable in the real schema). Internship, FYP-I, FYP-II,
     and the National Exit Exam are never droppable.
  2. Dropping it must not leave zero courses in the current semester.
  3. A future semester-slot must actually exist with enough credit-hour
     room to retake it ("a future credit gap"). This is checked as: does
     any legal future slot (right parity, within the horizon) have a cap
     large enough to fit this course's credit hours at all. It's a
     necessary check, not a guarantee that the FULL eventual schedule
     will have room once every other course is also accounted for --
     that full guarantee only comes from actually re-running the solver,
     which step 4 below does.

If all three pass, we simulate the drop: the dropped course gets a
DROPPED record at the current in-progress slot (which does NOT count
against the retake limit -- see Stage 4a), every OTHER course currently
in progress this semester gets treated as an assumed PASS at that same
slot (we don't predict grades; we assume success, same philosophy as
every future attempt elsewhere in this engine), and then we re-run
build_and_solve treating that slot as now-historical. This proves the
drop doesn't just satisfy the three rules in isolation, but that a
complete, still-feasible plan exists afterward.
"""

import copy
from ortools.sat.python import cp_model
from model_stage2 import slot_to_year_sem, valid_future_slots_for_course, cap_for_slot

MAX_ATTEMPTS_DEFAULT = 3

GRADED_STATUSES = {"PASS", "FAILED"}  # DROPPED is deliberately excluded


def summarize_course_history(records):
    """
    records: list of {"status": "PASS"|"FAILED"|"DROPPED", "slot": int}
    Returns a summary dict:
        passed_slot: int or None (the slot it was passed at, if ever)
        graded_attempt_count: int (PASS + FAILED, NOT DROPPED)
        last_slot: int or None (most recent attempt of any kind, for now_slot bookkeeping)
    """
    passed_slot = None
    graded_attempt_count = 0
    last_slot = None
    for r in records:
        if r["status"] == "PASS":
            passed_slot = r["slot"]
        if r["status"] in GRADED_STATUSES:
            graded_attempt_count += 1
        if last_slot is None or r["slot"] > last_slot:
            last_slot = r["slot"]
    return {
        "passed_slot": passed_slot,
        "graded_attempt_count": graded_attempt_count,
        "last_slot": last_slot,
    }


def validate_retake_limits(courses, completed_courses, max_attempts=MAX_ATTEMPTS_DEFAULT):
    """
    Returns a list of human-readable violation strings. Empty list means
    no known-in-advance impossibility from the retake limit.
    """
    violations = []
    for c in courses:
        records = completed_courses.get(c, [])
        if not records:
            continue
        summary = summarize_course_history(records)
        if summary["passed_slot"] is not None:
            continue  # already passed, retake limit is irrelevant
        if summary["graded_attempt_count"] >= max_attempts:
            violations.append(
                f"{c}: {summary['graded_attempt_count']} graded attempts already used "
                f"(limit is {max_attempts}), course still not passed, and no attempts remain."
            )
    return violations


def build_and_solve(courses, horizon_slots, normal_caps, completed_courses=None,
                     now_slot=0, max_attempts=MAX_ATTEMPTS_DEFAULT, verbose=False):
    completed_courses = completed_courses or {}

    # --- PRE-SOLVE CHECK: known-in-advance impossibilities ---
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

        # Not yet passed (never attempted, or FAILED/DROPPED some number of
        # times under the limit, already confirmed above) -- needs exactly
        # one more future attempt.
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


def check_drop_legality(drop_course, courses, current_semester_courses,
                         completed_courses, now_slot, horizon_slots, normal_caps,
                         max_attempts=MAX_ATTEMPTS_DEFAULT):
    """
    drop_course: the course code the student wants to drop
    current_semester_courses: ALL course codes the student is currently
        registered for this in-progress semester (including drop_course)
    completed_courses: PAST graded history only (does not need to include
        anything about the current in-progress semester)
    now_slot: last slot that's fully historical/graded. The current
        in-progress semester is therefore now_slot + 1.

    Returns:
        {"legal": False, "reasons": [...]}          if any rule fails
        {"legal": True, "reasons": [], "resulting_plan": <build_and_solve output>}
                                                       if the drop is legal
    """
    reasons = []
    in_progress_slot = now_slot + 1

    # Rule 1: must be droppable
    if not courses[drop_course].get("droppable", True):
        reasons.append(f"{drop_course} is not droppable (e.g. Internship, FYP, or the National Exit Exam).")

    # Rule 2: must not leave zero courses this semester
    remaining_this_semester = [c for c in current_semester_courses if c != drop_course]
    if len(remaining_this_semester) < 1:
        reasons.append(
            f"Dropping {drop_course} would leave zero courses in the current semester; "
            "at least one course must remain."
        )

    # Rule 3: a future credit gap must exist
    future_slots = valid_future_slots_for_course(courses[drop_course], horizon_slots, in_progress_slot)
    credit_gap_exists = any(
        cap_for_slot(s, normal_caps) >= courses[drop_course]["credit_hours"]
        for s in future_slots
    )
    if not credit_gap_exists:
        reasons.append(
            f"No future semester within the planning horizon has enough credit-hour "
            f"room to retake {drop_course} ({courses[drop_course]['credit_hours']} credit hours)."
        )

    if reasons:
        return {"legal": False, "reasons": reasons}

    # All three rules pass -- simulate the drop and confirm a full plan
    # still exists afterward.
    simulated_completed = copy.deepcopy(completed_courses)
    simulated_completed.setdefault(drop_course, []).append(
        {"status": "DROPPED", "slot": in_progress_slot}
    )
    for c in remaining_this_semester:
        # Assumed pass -- same philosophy as every other future attempt in
        # this engine: we don't predict grades, we plan assuming success.
        simulated_completed.setdefault(c, []).append(
            {"status": "PASS", "slot": in_progress_slot}
        )

    resulting_plan = build_and_solve(
        courses=courses,
        horizon_slots=horizon_slots,
        normal_caps=normal_caps,
        completed_courses=simulated_completed,
        now_slot=in_progress_slot,
        max_attempts=max_attempts,
    )

    return {"legal": True, "reasons": [], "resulting_plan": resulting_plan}