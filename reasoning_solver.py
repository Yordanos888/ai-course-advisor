"""
reasoning_solver.py -- Phase 6, Step 3
"""
import statistics

def _effective_year_level(current_year_level, current_semester, slot_offset):
    total_semester_index = (current_year_level - 1) * 2 + (current_semester - 1) + slot_offset
    return total_semester_index // 2 + 1

def _slot_parity(current_semester, slot_offset):
    return ((current_semester - 1 + slot_offset) % 2) + 1

def _last_valid_slot(current_year_level, current_semester, max_years=5):
    current_total = (current_year_level - 1) * 2 + (current_semester - 1)
    last_total = (max_years - 1) * 2 + 1  
    return last_total - current_total

def _precheck(graph, required_course_ids, exhausted_course_ids, current_year_level,
              current_semester, max_years, horizon_semesters):
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

    return None, min(horizon_semesters, last_valid + 1)

def _build_model(cp_model, graph, required_course_ids, already_passed_ids,
                  current_year_level, current_semester, student_stream_id,
                  horizon_semesters, general_credit_cap, year5_credit_cap,
                  flipped_parity_course_ids):
    model = cp_model.CpModel()

    capstone_exam_ids = {
        cid for cid in required_course_ids
        if graph.nodes[cid].get('special_requirement') == 'ALL_COURSES'
    }

    slot_var = {}
    for cid in required_course_ids:
        slot_var[cid] = model.NewIntVar(0, horizon_semesters - 1, f"slot_{cid}")

    for cid in required_course_ids:
        if cid in capstone_exam_ids:
            continue
        node = graph.nodes[cid]
        
        offered_parity = node['semester_offered']
        
        # 1. Parity Logic (Including Flipped Parity and Summer bypass)
        if cid in flipped_parity_course_ids or offered_parity == 3:
            valid_slots = list(range(horizon_semesters))
        else:
            valid_slots = [
                s for s in range(horizon_semesters)
                if _slot_parity(current_semester, s) == offered_parity
            ]
            
        # 2. Non-Droppable Anchor Logic (Cannot be pulled forward)
        # If a course is fixed, its scheduled slot must represent a year >= its canonical year
        if not node.get('is_droppable', True):
            canonical_year = node['year_level']
            valid_slots = [
                s for s in valid_slots
                if _effective_year_level(current_year_level, current_semester, s) >= canonical_year
            ]

        model.AddAllowedAssignments([slot_var[cid]], [(s,) for s in valid_slots])

    for cid in capstone_exam_ids:
        others = [slot_var[c] for c in required_course_ids if c != cid]
        if others:
            model.AddMaxEquality(slot_var[cid], others)
        else:
            model.Add(slot_var[cid] == 0)

    for cid in required_course_ids:
        for pred in graph.predecessors(cid):
            edge = graph[pred][cid]
            applicable = edge['applicable_streams']
            if applicable is not None and (student_stream_id is None or student_stream_id not in applicable):
                continue  

            if pred in already_passed_ids:
                continue  
            elif pred in slot_var:
                model.Add(slot_var[pred] < slot_var[cid])
            else:
                raise ValueError(
                    f"required_course_ids is not transitively closed: "
                    f"{graph.nodes[cid].get('course_code', cid)} requires "
                    f"{graph.nodes[pred].get('course_code', pred)}."
                )

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
        
        # 3. Summer Bypass Logic: Exclude semester_offered == 3 from the Spring credit crush
        model.Add(
            sum(assigned[(cid, s)] * graph.nodes[cid]['credit_hours'] 
                for cid in required_course_ids 
                if graph.nodes[cid]['semester_offered'] != 3) <= cap
        )

    max_slot = model.NewIntVar(0, horizon_semesters - 1, "max_slot")
    for cid in required_course_ids:
        model.Add(max_slot >= slot_var[cid])
    model.Minimize(max_slot)

    return model, slot_var, max_slot

def _status_infeasible_result(cp_model, status):
    if status == cp_model.INFEASIBLE:
        return {
            "feasible": False,
            "reason": (
                "No path exists: the required courses cannot all be scheduled "
                "within the prerequisite ordering, credit caps, and the "
                "max-years-to-graduate horizon."
            ),
        }
    return {
        "feasible": False,
        "reason": (
            "Could not determine feasibility within search limits. Try again with a "
            "larger time budget."
        ),
    }

def solve_recovery_plan(
    graph, required_course_ids, already_passed_ids, current_year_level, current_semester,
    student_stream_id, horizon_semesters=10, general_credit_cap=22, year5_credit_cap=22, max_years=5,
    flipped_parity_course_ids=frozenset(), exhausted_course_ids=frozenset(),
):
    error, horizon_semesters = _precheck(
        graph, required_course_ids, exhausted_course_ids,
        current_year_level, current_semester, max_years, horizon_semesters,
    )
    if error is not None:
        error["plan"] = None
        return error

    from ortools.sat.python import cp_model 

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
        "plan": plan, 
    }

def _credit_load_by_slot(graph, plan):
    return {
        s: sum(graph.nodes[cid]['credit_hours'] for cid in cids)
        for s, cids in plan.items()
    }

def solve_ranked_recovery_plans(
    graph, required_course_ids, already_passed_ids, current_year_level, current_semester, student_stream_id,
    horizon_semesters=10, general_credit_cap=22, year5_credit_cap=22, max_years=5,
    flipped_parity_course_ids=frozenset(), exhausted_course_ids=frozenset(), top_n=3,
):
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
            break  

        total_used = solver.Value(max_slot) + 1
        if best_total is None:
            best_total = total_used
        elif total_used != best_total:
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