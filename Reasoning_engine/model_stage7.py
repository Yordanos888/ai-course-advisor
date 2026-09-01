"""
STAGE 7 MODEL
=============
Everything from Stage 6 (special requirements) is reused via
model_stage6.build_and_solve for the baseline "fastest possible"
graduation slot. Stage 7 adds MULTI-PATH ENUMERATION on top of that:

  1. Find the optimal (fastest) graduation slot -- same as every prior
     stage.
  2. Re-solve with graduation slot FIXED at that optimum (as a hard
     constraint, objective removed) to find OTHER, structurally
     different plans that finish equally fast.
  3. Enumeration technique: solve, record the exact (course, slot)
     assignment found, add a "blocking" constraint forbidding that EXACT
     combination from recurring, solve again. Repeat until infeasible
     (no more distinct optimal plans exist) or max_solutions is reached.
  4. Rank the found plans by LOAD EVENNESS: population variance of
     credit-hours-per-slot across the slots actually used in that plan.
     Lower variance = more evenly spread course load = ranked higher.

NOTE ON SCOPE: the original plan mentioned ranking by "risk" as well
(e.g. avoiding stacking historically difficult courses together). That
is deliberately NOT implemented here -- it needs a difficulty/pass-rate
signal this fake-data stage doesn't have, and inventing an arbitrary
placeholder metric now would just be noise to remove later. Ranking
here is (graduation_slot, then evenness) only.
"""

import statistics
from ortools.sat.python import cp_model
from model_stage2 import slot_to_year_sem, cap_for_slot
from model_stage4 import (
    validate_retake_limits, summarize_course_history, MAX_ATTEMPTS_DEFAULT,
)
from model_stage5 import valid_future_slots_for_course, filter_courses_for_student  # noqa: F401
from model_stage6 import build_and_solve, earliest_slot_at_or_after  # reused unchanged


def _load_evenness_variance(schedule, courses):
    """
    Population variance of credit-hours-per-slot, computed ONLY over
    slots actually used in this schedule. A slot the curriculum never
    needs (e.g. no course ever offered in a given semester) shouldn't
    count as "imbalance" -- we're measuring whether the load across the
    semesters that ARE used is spread evenly, not penalizing plans for
    not using every theoretically possible slot.
    """
    slot_totals = {}
    for c, s in schedule.items():
        slot_totals[s] = slot_totals.get(s, 0) + courses[c]["credit_hours"]
    values = list(slot_totals.values())
    if len(values) < 2:
        return 0.0
    return statistics.pvariance(values)


def find_ranked_plans(courses, horizon_slots, normal_caps, completed_courses=None,
                       now_slot=0, max_attempts=MAX_ATTEMPTS_DEFAULT, max_solutions=5):
    completed_courses = completed_courses or {}

    # --- Step 1: baseline optimal solve (reuses Stage 6 unchanged) ---
    baseline = build_and_solve(
        courses=courses, horizon_slots=horizon_slots, normal_caps=normal_caps,
        completed_courses=completed_courses, now_slot=now_slot, max_attempts=max_attempts,
    )
    if not baseline["feasible"]:
        return {"feasible": False, "status": baseline.get("status"),
                "violations": baseline.get("violations"), "plans": []}

    best_grad_slot = baseline["graduation_slot"]

    # Also pin the SECONDARY objective (total slot-sum, i.e. "as
    # front-loaded as legitimately possible") at its own optimal value.
    # Fixing graduation slot alone is not enough -- it only caps the
    # ceiling, which still allows degenerate plans where a course sits
    # needlessly late as long as it's under that ceiling. Pinning both
    # tiers narrows enumeration to plans that are genuinely tied for
    # best under the ORIGINAL objective, so evenness only ever breaks
    # a tie that was previously resolved arbitrarily (alphabetically).
    baseline_sum_of_slots = sum(
        v for c, v in baseline["schedule"].items()
        if courses[c].get("special_requirement") != "ALL_COURSES"
    )

    post_hoc_codes = [
        c for c, info in courses.items()
        if info.get("special_requirement") == "ALL_COURSES"
    ]
    main_codes = [c for c in courses if c not in post_hoc_codes]

    # --- Step 2: build a fresh model, same constraints, but with
    # graduation slot FIXED at the proven optimum instead of minimized ---
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
        for s in valid_slots[c]:
            assign[c, s] = model.NewBoolVar(f"assign_{c}_{s}")
        model.Add(sum(assign[c, s] for s in valid_slots[c]) == 1)
        slot_of[c] = sum(s * assign[c, s] for s in valid_slots[c])

    for c in main_codes:
        for p in courses[c]["prereqs"]:
            model.Add(slot_of[c] > slot_of[p])

    for c in main_codes:
        if courses[c].get("special_requirement") == "ALL_STREAM_COURSES":
            for c2 in main_codes:
                if c2 != c:
                    model.Add(slot_of[c] >= slot_of[c2])

    for s in range(now_slot + 1, horizon_slots + 1):
        cap = cap_for_slot(s, normal_caps)
        contributors = [c for c in decided_codes if s in valid_slots[c]]
        if contributors:
            model.Add(
                sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= cap
            )

    # HARD constraints: pin BOTH tiers of the original objective at their
    # proven-optimal values (see comment above on why grad_slot alone is
    # insufficient).
    for c in main_codes:
        model.Add(slot_of[c] <= best_grad_slot)
    model.Add(sum(slot_of[c] for c in main_codes) == baseline_sum_of_slots)

    # No minimization objective -- we want to ENUMERATE feasible points,
    # not find one best one. Any assignment satisfying the constraints
    # above is, by construction, an equally-fast plan.

    plans = []
    solver = cp_model.CpSolver()
    for _ in range(max_solutions):
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break  # no more distinct plans at this graduation speed

        schedule = {c: solver.Value(slot_of[c]) for c in main_codes}

        # Post-hoc placement (NEE-style), same as Stage 6.
        true_max_slot = max(schedule.values()) if schedule else now_slot
        for c in post_hoc_codes:
            allowed_parities = {courses[c]["semester_offered"]}
            if courses[c].get("alt_parity"):
                allowed_parities.add(courses[c]["alt_parity"])
            placed = earliest_slot_at_or_after(true_max_slot, allowed_parities, horizon_slots)
            schedule[c] = placed

        graduation_slot = max(schedule.values()) if schedule else now_slot
        evenness_variance = _load_evenness_variance(
            {c: s for c, s in schedule.items() if c not in post_hoc_codes}, courses
        )
        plans.append({
            "schedule": schedule,
            "graduation_slot": graduation_slot,
            "load_evenness_variance": evenness_variance,
        })

        # --- Block this EXACT combination so the next solve must differ
        # in at least one course's slot ---
        chosen_true_vars = [
            assign[c, s] for c in decided_codes for s in valid_slots[c]
            if solver.Value(assign[c, s]) == 1
        ]
        if not chosen_true_vars:
            break  # nothing to block (all courses were pre-passed constants)
        model.Add(sum(chosen_true_vars) <= len(chosen_true_vars) - 1)

    # Rank: fastest first (all tied here by construction), then lowest
    # load-evenness variance first.
    plans.sort(key=lambda p: (p["graduation_slot"], p["load_evenness_variance"]))

    return {
        "feasible": True,
        "best_graduation_slot": best_grad_slot,
        "plans": plans,
    }