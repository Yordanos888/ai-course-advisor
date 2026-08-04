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
  - special_requirement ALL_STREAM_COURSES (FYP-II) capstone handling --
    ALL_COURSES (Exit Exam) is now handled; ALL_STREAM_COURSES is a required-set
    assembly concern on the caller side, not scheduling logic, and isn't built yet
  - multiple ranked solutions (only the single optimal plan for now)
  - stream-choice comparison (Step 4)
"""
def _effective_year_level(current_year_level, current_semester, slot_offset):
    """Maps a slot offset (0 = current semester) to an approximate year_level,
    purely for credit-cap lookup purposes -- NOT used for course eligibility,
    since course availability is governed by parity alone (see module docstring)."""
    total_semester_index = (current_year_level - 1) * 2 + (current_semester - 1) + slot_offset
    return total_semester_index // 2 + 1


def _slot_parity(current_semester, slot_offset):
    """1 or 2 -- which semester_offered value this slot corresponds to."""
    return ((current_semester - 1 + slot_offset) % 2) + 1


def _last_valid_slot(current_year_level, current_semester, max_years=5):
    """Last slot_offset (inclusive, 0 = current semester) that still falls
    within max_years (Rule E). -1 means the student is already past the
    horizon. Without this, horizon_semesters alone doesn't stop the solver
    from placing a course in year 6+ -- it would just apply the year5_credit_cap
    to a semester that shouldn't legally exist."""
    current_total = (current_year_level - 1) * 2 + (current_semester - 1)
    last_total = (max_years - 1) * 2 + 1  # (max_years, semester 2), 0-indexed
    return last_total - current_total


def solve_recovery_plan(
    graph,                      # NetworkX DiGraph from prerequisite_graph.py
    required_course_ids,        # course_ids still needed (not yet PASSED)
    already_passed_ids,         # course_ids already PASSED (used as prerequisite anchors, not scheduled)
    current_year_level,
    current_semester,
    student_stream_id,          # None if not yet chosen
    horizon_semesters=10,       # planning horizon in semester slots (upper bound; clamped to max_years)
    general_credit_cap=18,
    year5_credit_cap=22,
    max_years=5,
    flipped_parity_course_ids=frozenset(),  # Rule J: common_courses schedulable via
                                             # either parity (partner dept offers the
                                             # opposite parity), so no parity restriction
    exhausted_course_ids=frozenset(),       # Rule D: courses at the 3-graded-attempt
                                             # retake cap with no PASSED attempt --
                                             # can never be completed, ever
):
    # Rule D: retake-limit exhaustion makes the whole plan infeasible outright,
    # no CP-SAT run needed (checked before importing ortools -- pure data check).
    # This must never invent a workaround (e.g. silently dropping the course, or
    # substituting another) -- course substitution is explicitly a human/
    # department decision, never automated.
    blocked = set(required_course_ids) & set(exhausted_course_ids)
    if blocked:
        blocked_codes = sorted(graph.nodes[cid].get('course_code', str(cid)) for cid in blocked)
        return {
            "feasible": False,
            "reason": (
                "No path exists: retake limit exhausted (3 graded attempts, no pass) "
                f"for: {', '.join(blocked_codes)}. Course substitution is a department "
                "decision, not something this solver can resolve."
            ),
            "plan": None,
        }

    from ortools.sat.python import cp_model  # deferred: keeps helpers unit-testable without ortools installed

    last_valid = _last_valid_slot(current_year_level, current_semester, max_years)
    if last_valid < 0:
        return {"feasible": False, "reason": "Student is already past the max-years-to-graduate horizon.", "plan": None}
    # Clamp: never offer a slot beyond year `max_years`, regardless of horizon_semesters.
    horizon_semesters = min(horizon_semesters, last_valid + 1)

    model = cp_model.CpModel()

    # Rule B / ALL_COURSES: the Exit Exam (credit_hours=0) requires literally
    # everything -- including same-semester peers like FYP-II -- and is
    # evaluated after the final scheduled semester, detached from normal slot
    # competition. It gets no parity domain and no credit-cap contribution
    # (0 credits does that automatically); instead its slot is pinned to
    # equal the max slot of everything else via AddMaxEquality below, so it
    # never adds an extra semester on its own.
    capstone_exam_ids = {
        cid for cid in required_course_ids
        if graph.nodes[cid].get('special_requirement') == 'ALL_COURSES'
    }

    # --- Decision variables: which slot (0..horizon-1) each required course lands in ---
    slot_var = {}
    for cid in required_course_ids:
        slot_var[cid] = model.NewIntVar(0, horizon_semesters - 1, f"slot_{cid}")

    # --- Domain restriction: each course only valid in slots matching its parity,
    # UNLESS it's a Rule J flipped-parity-eligible common course, in which case
    # both parities are open (the partner department offers the flip). The
    # capstone exam skips this entirely -- its slot is pinned below instead. ---
    for cid in required_course_ids:
        if cid in capstone_exam_ids:
            continue
        node = graph.nodes[cid]
        if cid in flipped_parity_course_ids:
            valid_slots = list(range(horizon_semesters))
        else:
            offered_parity = node['semester_offered']  # 1 or 2
            valid_slots = [
                s for s in range(horizon_semesters)
                if _slot_parity(current_semester, s) == offered_parity
            ]
        model.AddAllowedAssignments([slot_var[cid]], [(s,) for s in valid_slots])

    # --- Capstone pin: exam slot == max slot of everything else required
    # (or slot 0 if the exam is the only thing left) ---
    for cid in capstone_exam_ids:
        others = [slot_var[c] for c in required_course_ids if c != cid]
        if others:
            model.AddMaxEquality(slot_var[cid], others)
        else:
            model.Add(slot_var[cid] == 0)

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
            else:
                # predecessor isn't passed AND isn't in required_course_ids --
                # required_course_ids isn't transitively closed. Silently
                # skipping this would drop a real prerequisite constraint and
                # let the solver produce an illegal schedule. Fail loud instead;
                # this is a caller data-assembly bug, not a "no path exists" case.
                raise ValueError(
                    f"required_course_ids is not transitively closed: "
                    f"{graph.nodes[cid].get('course_code', cid)} requires "
                    f"{graph.nodes[pred].get('course_code', pred)}, which is "
                    f"neither in already_passed_ids nor required_course_ids."
                )

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

    if status == cp_model.INFEASIBLE:
        return {
            "feasible": False,
            "reason": (
                "No path exists: the required courses cannot all be scheduled "
                "within the prerequisite ordering, credit caps, and the "
                "max-years-to-graduate horizon."
            ),
            "plan": None,
        }
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # UNKNOWN / MODEL_INVALID -- the solver couldn't determine feasibility
        # either way (e.g. search limits). Distinct from a genuine "no path
        # exists" so the caller doesn't misreport a real answer as one.
        return {
            "feasible": False,
            "reason": (
                "Could not determine feasibility within search limits -- this is "
                "not the same as confirming no path exists. Try again with a "
                "larger time budget."
            ),
            "plan": None,
        }

    plan = {}
    for cid in required_course_ids:
        s = solver.Value(slot_var[cid])
        plan.setdefault(s, []).append(cid)

    return {
        "feasible": True,
        "total_semesters_used": solver.Value(max_slot) + 1,
        "plan": plan,  # {slot_offset: [course_ids]}
    }