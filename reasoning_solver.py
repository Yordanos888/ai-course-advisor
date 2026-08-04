"""
reasoning_solver.py -- Phase 6, Step 3 (first pass / core mechanics)

Builds a CP-SAT model that schedules a student's remaining required courses
across future semesters, respecting:
  - prerequisite ordering (via the NetworkX graph from Step 1)
  - per-semester credit-hour caps (with the year-5 overload override)
  - each course only schedulable into a semester slot matching its
    semester_offered PARITY (not literal calendar-year alignment -- a course
    recurs every year in its fixed parity, so from the student's perspective
    it's available in every future slot of the matching parity)
  - minimizing total semesters to completion

NOT YET INCLUDED in this first pass (deliberately, to verify core mechanics
before layering on complexity):
  - special_requirement (ALL_STREAM_COURSES / ALL_COURSES) capstone handling
  - cross-department semester-flip expanded slots (Rule J)
  - multiple ranked solutions (only the single optimal plan for now)
  - stream-choice comparison (Step 4)
"""
from ortools.sat.python import cp_model


def _effective_year_level(current_year_level, current_semester, slot_offset):
    """Maps a slot offset (0 = current semester) to an approximate year_level,
    purely for credit-cap lookup purposes -- NOT used for course eligibility,
    since course availability is governed by parity alone (see module docstring)."""
    total_semester_index = (current_year_level - 1) * 2 + (current_semester - 1) + slot_offset
    return total_semester_index // 2 + 1


def _slot_parity(current_semester, slot_offset):
    """1 or 2 -- which semester_offered value this slot corresponds to."""
    return ((current_semester - 1 + slot_offset) % 2) + 1


def solve_recovery_plan(
    graph,                      # NetworkX DiGraph from prerequisite_graph.py
    required_course_ids,        # course_ids still needed (not yet PASSED)
    already_passed_ids,         # course_ids already PASSED (used as prerequisite anchors, not scheduled)
    current_year_level,
    current_semester,
    student_stream_id,          # None if not yet chosen
    horizon_semesters=10,       # planning horizon in semester slots
    general_credit_cap=18,
    year5_credit_cap=22,
):
    model = cp_model.CpModel()

    # --- Decision variables: which slot (0..horizon-1) each required course lands in ---
    slot_var = {}
    for cid in required_course_ids:
        slot_var[cid] = model.NewIntVar(0, horizon_semesters - 1, f"slot_{cid}")

    # --- Domain restriction: each course only valid in slots matching its parity ---
    for cid in required_course_ids:
        node = graph.nodes[cid]
        offered_parity = node['semester_offered']  # 1 or 2
        valid_slots = [
            s for s in range(horizon_semesters)
            if _slot_parity(current_semester, s) == offered_parity
        ]
        model.AddAllowedAssignments([slot_var[cid]], [(s,) for s in valid_slots])

    # --- Prerequisite ordering ---
    for cid in required_course_ids:
        for pred in graph.predecessors(cid):
            edge = graph[pred][cid]
            applicable = edge['applicable_streams']
            if applicable is not None and (student_stream_id is None or student_stream_id not in applicable):
                continue  # this prerequisite doesn't apply to this student's stream

            if pred in already_passed_ids:
                continue  # already satisfied, no ordering constraint needed
            elif pred in slot_var:
                model.Add(slot_var[pred] < slot_var[cid])
            # else: predecessor isn't passed AND isn't in our required set --
            # this means the model is missing a course that should have been
            # included. Caller's responsibility to ensure required_course_ids
            # is transitively closed; flagged in the wrapper function below.

    # --- Credit-hour cap per slot (channeling: assigned[cid][s] <-> slot_var[cid] == s) ---
    assigned = {}
    for cid in required_course_ids:
        for s in range(horizon_semesters):
            b = model.NewBoolVar(f"assigned_{cid}_{s}")
            model.Add(slot_var[cid] == s).OnlyEnforceIf(b)
            model.Add(slot_var[cid] != s).OnlyEnforceIf(b.Not())
            assigned[(cid, s)] = b

    for s in range(horizon_semesters):
        effective_year = _effective_year_level(current_year_level, current_semester, s)
        cap = year5_credit_cap if effective_year >= 5 else general_credit_cap
        model.Add(
            sum(assigned[(cid, s)] * graph.nodes[cid]['credit_hours'] for cid in required_course_ids) <= cap
        )

    # --- Objective: minimize the last semester used ---
    max_slot = model.NewIntVar(0, horizon_semesters - 1, "max_slot")
    for cid in required_course_ids:
        model.Add(max_slot >= slot_var[cid])
    model.Minimize(max_slot)

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": False, "reason": "No valid schedule found within the planning horizon.", "plan": None}

    plan = {}
    for cid in required_course_ids:
        s = solver.Value(slot_var[cid])
        plan.setdefault(s, []).append(cid)

    return {
        "feasible": True,
        "total_semesters_used": solver.Value(max_slot) + 1,
        "plan": plan,  # {slot_offset: [course_ids]}
    }