"""
STAGE 8a-FIX MODEL
====================
Fixes the behavioral finding from the first real-data run: the objective
inherited from Stage 1 (minimize total earliness / front-load everything
the explicit prerequisite chain allows) produced unrealistic plans on
real data, because the real curriculum's prerequisite edges are sparser
than our fake data's -- many later-year courses have NO explicit
prerequisite, so nothing stopped the old objective from pulling them
years early (e.g. the Industry Internship landing in Year 1).

TWO CHANGES FROM STAGE 6/7/8a's ORIGINAL OBJECTIVE:

  1. SECONDARY OBJECTIVE CHANGED: "minimize sum_of_slots" (front-load
     everything) -> "minimize total |deviation| from each course's own
     labeled (year_level, semester_offered)". A fresh, on-track student
     should get back exactly the normal curriculum. Deviation is still
     minimized when a real constraint forces it (a failure, a genuine
     credit-cap gap worth exploiting) -- the domino effect still works,
     it's just no longer the DEFAULT behavior for everyone.

  2. NEW HARD CONSTRAINT for non-droppable courses (per explicit
     confirmation): a non-droppable course (Internship, FYP-I, FYP-II,
     NEE) can NEVER be scheduled EARLIER than its own labeled slot --
     "fixed" means a hard floor, not just a soft preference. It CAN
     still be delayed later (blocked prereq, retake), just never pulled
     forward. This is stricter than the soft deviation preference alone
     would guarantee, which is why it's a separate hard constraint, not
     just reliance on tier 2 of the objective.

BACKWARD-COMPATIBILITY DESIGN: semester_types is now a PARAMETER, not a
hardcoded (1,2,3) constant. This lets Stage 1-7's existing fake data (all
built around a 2-semester-type system) be re-run through THIS model with
semester_types=(1,2) and produce IDENTICAL slot numbers to their original
hand-computed expected answers -- a real validation, not just an
assertion that "it should still work."
"""

from ortools.sat.python import cp_model
from model_stage4 import validate_retake_limits, summarize_course_history, MAX_ATTEMPTS_DEFAULT

CAP_FALLBACK_UNCONSTRAINED = 999


def slot_to_year_sem(slot, semester_types=(1, 2, 3)):
    types_per_year = len(semester_types)
    year = (slot - 1) // types_per_year + 1
    sem = semester_types[(slot - 1) % types_per_year]
    return year, sem


def natural_slot_for_course(course, semester_types=(1, 2, 3)):
    """The slot corresponding to this course's own labeled (year_level,
    semester_offered) -- its 'normal' position in an on-track plan."""
    types_per_year = len(semester_types)
    idx = semester_types.index(course["semester_offered"])
    return (course["year_level"] - 1) * types_per_year + idx + 1


def compute_normal_caps(filtered_courses):
    caps = {}
    for info in filtered_courses.values():
        key = (info["year_level"], info["semester_offered"])
        caps[key] = caps.get(key, 0) + info["credit_hours"]
    return caps


def cap_for_slot(slot, normal_caps, semester_types=(1, 2, 3), year5_override=22):
    year, sem = slot_to_year_sem(slot, semester_types)
    normal_sum = normal_caps.get((year, sem), 0)
    if normal_sum == 0:
        return CAP_FALLBACK_UNCONSTRAINED
    if year == 5:
        return year5_override
    return normal_sum


def filter_courses_for_student(all_courses, student_stream):
    """
    Checks both "streams" (real data: a SET, supports multi-stream
    courses) and "stream" (Stage 5's original fake data: a single
    string) for backward compatibility during re-validation.
    """
    result = {}
    for c, info in all_courses.items():
        if "streams" in info:
            scope = info["streams"]
            include = scope is None or student_stream in scope
        else:
            scope = info.get("stream")
            include = scope is None or scope == student_stream
        if include:
            result[c] = info
    return result


def valid_future_slots_for_course(course, horizon_slots, now_slot, semester_types=(1, 2, 3)):
    allowed_parities = {course["semester_offered"]}
    if course.get("alt_parity"):
        allowed_parities.add(course["alt_parity"])
    return [
        s for s in range(now_slot + 1, horizon_slots + 1)
        if slot_to_year_sem(s, semester_types)[1] in allowed_parities
    ]


def earliest_slot_at_or_after(min_slot, allowed_parities, horizon_slots, semester_types=(1, 2, 3)):
    for s in range(min_slot, horizon_slots + 1):
        if slot_to_year_sem(s, semester_types)[1] in allowed_parities:
            return s
    return None


def build_and_solve(courses, horizon_slots, policy_horizon_slots=None, completed_courses=None, now_slot=0,
                     max_attempts=MAX_ATTEMPTS_DEFAULT, semester_types=(1, 2, 3),
                     normal_caps_override=None, verbose=False):
    """
    horizon_slots: the actual ceiling the solver is allowed to search
        within (variable domains are bounded here). Pass a generous value
        for real-data use (e.g. 10 years' worth of slots) so the solver
        can always find a genuine plan rather than reporting a bare
        infeasibility when a student's ACTUAL best path just takes longer
        than the target.

    policy_horizon_slots: the "official" limit to check compliance
        against (e.g. 15 for AASTU's 5-year policy). If None, defaults to
        horizon_slots itself -- this preserves the OLD strict behavior
        used by Stages 1-7's fake-data tests (where horizon_slots WAS the
        hard ceiling and genuine infeasibility was the correct, intended
        outcome for scenarios designed to exceed it).

    The result always includes a real schedule when one exists within
    horizon_slots, plus exceeds_policy_horizon: True/False so the caller
    (bot/advisor) can report honestly -- "here's your plan, but note it
    takes longer than the standard 5 years" -- instead of hiding a real,
    findable plan behind a blunt "no solution."
    """
    if policy_horizon_slots is None:
        policy_horizon_slots = horizon_slots

    completed_courses = completed_courses or {}

    violations = validate_retake_limits(courses, completed_courses, max_attempts)
    if violations:
        return {"feasible": False, "status": "RETAKE_LIMIT_EXCEEDED", "violations": violations}

    normal_caps = normal_caps_override if normal_caps_override is not None else compute_normal_caps(courses)

    post_hoc_codes = [c for c, info in courses.items() if info.get("special_requirement") == "ALL_COURSES"]
    main_codes = [c for c in courses if c not in post_hoc_codes]

    model = cp_model.CpModel()
    slot_of, assign, valid_slots, decided_codes = {}, {}, {}, []

    for c in main_codes:
        records = completed_courses.get(c, [])
        summary = summarize_course_history(records) if records else None
        if summary and summary["passed_slot"] is not None:
            slot_of[c] = model.NewConstant(summary["passed_slot"])
            continue
        decided_codes.append(c)
        valid_slots[c] = valid_future_slots_for_course(courses[c], horizon_slots, now_slot, semester_types)
        if not valid_slots[c]:
            raise ValueError(f"{c} has no legal future slot left before horizon ends")
        for s in valid_slots[c]:
            assign[c, s] = model.NewBoolVar(f"assign_{c}_{s}")
        model.Add(sum(assign[c, s] for s in valid_slots[c]) == 1)
        slot_of[c] = sum(s * assign[c, s] for s in valid_slots[c])

    # --- Ordinary prerequisites ---
    for c in main_codes:
        for p in courses[c]["prereqs"]:
            if p in slot_of:
                model.Add(slot_of[c] > slot_of[p])

    # --- ALL_STREAM_COURSES dynamic requirement (FYP-II-style) ---
    for c in main_codes:
        if courses[c].get("special_requirement") == "ALL_STREAM_COURSES":
            for c2 in main_codes:
                if c2 != c:
                    model.Add(slot_of[c] >= slot_of[c2])

    # --- NEW: hard floor for non-droppable courses. Checked against both
    # possible field-name spellings used across the project so far
    # ("is_droppable" in real data / models.py, "droppable" in some
    # earlier fake data) -- defaults to droppable (no floor) if neither
    # is present. ---
    natural_slots = {}
    for c in decided_codes:
        natural_slots[c] = natural_slot_for_course(courses[c], semester_types)
        is_droppable = courses[c].get("is_droppable", courses[c].get("droppable", True))
        if not is_droppable:
            model.Add(slot_of[c] >= natural_slots[c])

    # --- Credit caps ---
    for s in range(now_slot + 1, horizon_slots + 1):
        cap = cap_for_slot(s, normal_caps, semester_types)
        contributors = [c for c in decided_codes if s in valid_slots[c]]
        if contributors:
            model.Add(sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= cap)

    # --- Objective ---
    grad_slot = model.NewIntVar(1, horizon_slots, "grad_slot")
    for c in main_codes:
        model.Add(grad_slot >= slot_of[c])

    # Tier 2 (CHANGED): total absolute deviation from each course's own
    # natural/labeled slot, instead of raw sum_of_slots.
    deviation_vars = []
    for c in decided_codes:
        signed = model.NewIntVar(-horizon_slots, horizon_slots, f"signeddev_{c}")
        model.Add(signed == slot_of[c] - natural_slots[c])
        absdev = model.NewIntVar(0, horizon_slots, f"absdev_{c}")
        model.AddAbsEquality(absdev, signed)
        deviation_vars.append(absdev)
    deviation_sum = sum(deviation_vars) if deviation_vars else 0
    max_possible_deviation = horizon_slots * max(len(decided_codes), 1)

    sorted_codes = sorted(main_codes)
    code_index = {c: i for i, c in enumerate(sorted_codes)}
    max_index = max(len(main_codes), 1)
    tie_break_term = sum((max_index - code_index[c]) * slot_of[c] for c in main_codes)
    max_possible_tie_break = max_index * horizon_slots * max(len(main_codes), 1)

    model.Minimize(
        grad_slot * (max_possible_deviation + 1) * (max_possible_tie_break + 1)
        + deviation_sum * (max_possible_tie_break + 1)
        + tie_break_term
    )

    solver = cp_model.CpSolver()
    if verbose:
        solver.parameters.log_search_progress = True
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": False, "status": solver.StatusName(status)}

    schedule = {c: solver.Value(slot_of[c]) for c in main_codes}

    true_max_slot = max(schedule.values()) if schedule else now_slot
    for c in post_hoc_codes:
        allowed_parities = {courses[c]["semester_offered"]}
        if courses[c].get("alt_parity"):
            allowed_parities.add(courses[c]["alt_parity"])
        placed = earliest_slot_at_or_after(true_max_slot, allowed_parities, horizon_slots, semester_types)
        if placed is None:
            return {"feasible": False, "status": "CAPSTONE_UNSCHEDULABLE",
                     "violations": [f"{c}: no slot at or after {true_max_slot} within horizon."]}
        schedule[c] = placed

    graduation_slot = max(schedule.values()) if schedule else now_slot

    return {
        "feasible": True,
        "status": solver.StatusName(status),
        "schedule": schedule,
        "graduation_slot": graduation_slot,
        "normal_caps_used": normal_caps,
        "policy_horizon_slots": policy_horizon_slots,
        "exceeds_policy_horizon": graduation_slot > policy_horizon_slots,
    }


def solve_with_horizon_extension(courses, base_horizon, completed_courses=None, now_slot=0,
                                   max_attempts=MAX_ATTEMPTS_DEFAULT, semester_types=(1, 2, 3),
                                   normal_caps_override=None, extension_step=3, max_horizon=60):
    """
    Tries the mandated horizon (e.g. 5 years = 15 slots) first. If that's
    genuinely infeasible FOR A TIME REASON (ran out of horizon room --
    status INFEASIBLE or CAPSTONE_UNSCHEDULABLE), extends the horizon in
    steps and retries until a real plan is found or max_horizon is hit.

    Does NOT extend for RETAKE_LIMIT_EXCEEDED -- that's not a time
    problem, more time can't fix a student who is genuinely out of legal
    attempts on a required course, so extending would just waste solves
    and produce a misleading "just wait longer" implication.

    Always returns a usable result: either a plan within the mandated
    horizon (within_mandated_horizon=True), a plan that required more
    time than mandated (within_mandated_horizon=False, with an explicit
    message and how much extra time was needed), or a genuine
    non-time-related infeasibility (unchanged, no schedule to give).
    """
    result = build_and_solve(
        courses=courses, horizon_slots=base_horizon, completed_courses=completed_courses,
        now_slot=now_slot, max_attempts=max_attempts, semester_types=semester_types,
        normal_caps_override=normal_caps_override,
    )
    if result["feasible"]:
        result["within_mandated_horizon"] = True
        result["message"] = None
        return result

    if result.get("status") not in ("INFEASIBLE", "CAPSTONE_UNSCHEDULABLE"):
        # Not a time problem (e.g. retake limit exceeded) -- extending
        # the horizon cannot help. Report as-is.
        result["within_mandated_horizon"] = False
        result["message"] = (
            "This is not a time problem -- extending the horizon would not help. "
            "See the reported violations for the actual blocker."
        )
        return result

    horizon = base_horizon
    while horizon < max_horizon:
        horizon += extension_step
        result = build_and_solve(
            courses=courses, horizon_slots=horizon, completed_courses=completed_courses,
            now_slot=now_slot, max_attempts=max_attempts, semester_types=semester_types,
            normal_caps_override=normal_caps_override,
        )
        if result["feasible"]:
            base_years = base_horizon / len(semester_types)
            actual_years = result["graduation_slot"] / len(semester_types)
            result["within_mandated_horizon"] = False
            result["message"] = (
                f"5-year completion is NOT achievable given this student's history. "
                f"The earliest feasible completion found requires extending the plan to "
                f"approximately year {actual_years:.1f} -- "
                f"{actual_years - base_years:.1f} years beyond the mandated {base_years:.0f}-year limit. "
                f"This schedule is provided as the best achievable plan, not a guarantee "
                f"of on-time graduation."
            )
            return result

    return {
        "feasible": False,
        "status": "INFEASIBLE_EVEN_EXTENDED",
        "within_mandated_horizon": False,
        "message": f"No feasible completion found even extending to {max_horizon / len(semester_types):.0f} years.",
    }