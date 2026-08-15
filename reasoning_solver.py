"""
reasoning_solver.py -- Phase 6, Step 3

Builds a CP-SAT model that schedules a student's remaining required courses
across future semesters, respecting:
  - prerequisite ordering (via the NetworkX graph from Step 1)
  - per-semester credit-hour caps (with the year-5 overload override, Rule C)
  - each course only schedulable into a semester slot matching its
    semester_offered PARITY (not literal calendar-year alignment -- a course
    recurs every year in its fixed parity, so from the student's perspective
    it's available in every future slot of the matching parity) (Rule F),
    UNLESS it's a Rule J flipped-parity-eligible common course (either parity)
  - the max-years-to-graduate horizon (Rule E)
  - retake-limit exhaustion (Rule D) -- returns a clean infeasible result,
    never a workaround
  - the Exit Exam / ALL_COURSES capstone (Rule B) -- detached from normal
    slot competition, pinned to land with the last scheduled course
  - minimizing total semesters to completion

Two entry points:
  - solve_recovery_plan(...)         -> single optimal plan
  - solve_ranked_recovery_plans(...) -> top-N distinct plans, solve-then-
                                         exclude-and-resolve, ranked by
                                         total semesters then credit-load balance
"""
import statistics


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


def _precheck(graph, required_course_ids, exhausted_course_ids, current_year_level,
              current_semester, max_years, horizon_semesters):
    """Shared by both entry points. Returns (error_dict, None) if either check
    fails, or (None, clamped_horizon_semesters) if clear to build a model."""
    # Rule D: retake-limit exhaustion makes the whole plan infeasible outright,
    # no CP-SAT run needed. Never invent a workaround (e.g. silently dropping
    # the course, or substituting another) -- substitution is explicitly a
    # human/department decision, never automated.
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
        }, None

    last_valid = _last_valid_slot(current_year_level, current_semester, max_years)
    if last_valid < 0:
        return {
            "feasible": False,
            "reason": "Student is already past the max-years-to-graduate horizon.",
        }, None

    # Clamp: never offer a slot beyond year `max_years`, regardless of horizon_semesters.
    return None, min(horizon_semesters, last_valid + 1)


def _build_model(cp_model, graph, required_course_ids, already_passed_ids,
                  current_year_level, current_semester, student_stream_id,
                  horizon_semesters, general_credit_cap, year5_credit_cap,
                  flipped_parity_course_ids):
    """Builds the CP-SAT model shared by both entry points. Assumes
    horizon_semesters has already been clamped by _precheck. Returns
    (model, slot_var, max_slot)."""
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

    return model, slot_var, max_slot


def _status_infeasible_result(cp_model, status):
    """Shared status -> clean-message mapping for both entry points."""
    if status == cp_model.INFEASIBLE:
        return {
            "feasible": False,
            "reason": (
                "No path exists: the required courses cannot all be scheduled "
                "within the prerequisite ordering, credit caps, and the "
                "max-years-to-graduate horizon."
            ),
        }
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
    }


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
    """Single optimal plan (fewest total semesters). See solve_ranked_recovery_plans
    for multiple ranked alternatives."""
    error, horizon_semesters = _precheck(
        graph, required_course_ids, exhausted_course_ids,
        current_year_level, current_semester, max_years, horizon_semesters,
    )
    if error is not None:
        error["plan"] = None
        return error

    from ortools.sat.python import cp_model  # deferred: keeps helpers unit-testable without ortools installed

    model, slot_var, max_slot = _build_model(
        cp_model, graph, required_course_ids, already_passed_ids,
        current_year_level, current_semester, student_stream_id,
        horizon_semesters, general_credit_cap, year5_credit_cap,
        flipped_parity_course_ids,
    )

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result = _status_infeasible_result(cp_model, status)
        result["plan"] = None
        return result

    plan = {}
    for cid in required_course_ids:
        s = solver.Value(slot_var[cid])
        plan.setdefault(s, []).append(cid)

    return {
        "feasible": True,
        "total_semesters_used": solver.Value(max_slot) + 1,
        "plan": plan,  # {slot_offset: [course_ids]}
    }


def _credit_load_by_slot(graph, plan):
    """{slot_offset: total_credit_hours} for a plan, used as a risk/balance
    signal when ranking multiple plans with the same total_semesters_used."""
    return {
        s: sum(graph.nodes[cid]['credit_hours'] for cid in cids)
        for s, cids in plan.items()
    }


def solve_ranked_recovery_plans(
    graph,
    required_course_ids,
    already_passed_ids,
    current_year_level,
    current_semester,
    student_stream_id,
    horizon_semesters=10,
    general_credit_cap=18,
    year5_credit_cap=22,
    max_years=5,
    flipped_parity_course_ids=frozenset(),
    exhausted_course_ids=frozenset(),
    top_n=3,
):
    """Multiple distinct plans, all tied at the minimum total_semesters_used --
    via solve-then-exclude-and-resolve: solve once, forbid that EXACT full
    slot assignment, resolve, repeat up to top_n times. Stops early, and may
    return FEWER than top_n plans, if either (a) the model runs out of
    distinct feasible assignments, or (b) the next distinct assignment found
    is strictly longer than the first (best) one -- this function never pads
    results with slower alternatives just to hit top_n.

    CP-SAT's objective (minimize total semesters) still applies on every
    resolve against the same model, so plans are discovered best-first: all
    optimal-total-semesters assignments get exhausted before the loop stops.
    Among the (tied-length) plans returned, ranking is by credit-load balance
    (lower stdev across used semesters = less cramming into any single
    semester = lower risk).

    Returns {"feasible": bool, "reason": str (only if infeasible),
             "plans": [{"total_semesters_used": int, "plan": {...},
                        "credit_load_by_slot": {...}}, ...]} -- best first,
             all sharing the same total_semesters_used.
    """
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1, got {top_n}")

    error, horizon_semesters = _precheck(
        graph, required_course_ids, exhausted_course_ids,
        current_year_level, current_semester, max_years, horizon_semesters,
    )
    if error is not None:
        error["plans"] = []
        return error

    from ortools.sat.python import cp_model

    model, slot_var, max_slot = _build_model(
        cp_model, graph, required_course_ids, already_passed_ids,
        current_year_level, current_semester, student_stream_id,
        horizon_semesters, general_credit_cap, year5_credit_cap,
        flipped_parity_course_ids,
    )

    solver = cp_model.CpSolver()
    found = []
    best_total = None

    for i in range(top_n):
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break  # exhausted all distinct feasible assignments (or none existed)

        total_used = solver.Value(max_slot) + 1
        if best_total is None:
            best_total = total_used
        elif total_used != best_total:
            # Every subsequent resolve is >= the previous (we only ever
            # exclude solutions, never relax constraints), so the moment
            # we see something longer than the best, nothing further this
            # loop could find will be tied-optimal either -- stop here
            # rather than padding results with slower alternatives.
            break

        plan = {}
        values = {}
        for cid, var in slot_var.items():
            s = solver.Value(var)
            values[cid] = s
            plan.setdefault(s, []).append(cid)

        found.append({
            "total_semesters_used": total_used,
            "plan": plan,
            "credit_load_by_slot": _credit_load_by_slot(graph, plan),
        })

        # Exclusion constraint: forbid this exact full assignment from
        # recurring -- at least one course must land somewhere different.
        same_bools = []
        for cid, var in slot_var.items():
            b = model.NewBoolVar(f"same_{cid}_{i}")
            model.Add(var == values[cid]).OnlyEnforceIf(b)
            model.Add(var != values[cid]).OnlyEnforceIf(b.Not())
            same_bools.append(b)
        model.Add(sum(same_bools) < len(same_bools))

    if not found:
        result = _status_infeasible_result(cp_model, status)
        result["plans"] = []
        return result

    def _risk(entry):
        loads = list(entry["credit_load_by_slot"].values())
        return statistics.pstdev(loads) if len(loads) > 1 else 0.0

    ranked = sorted(found, key=lambda e: (e["total_semesters_used"], _risk(e)))

    return {"feasible": True, "plans": ranked}