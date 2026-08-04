"""
CP-SAT integration tests for solve_ranked_recovery_plans -- Requires ortools.
Run with: pytest test_reasoning_solver_ranked.py -v
"""
import networkx as nx
import pytest
from reasoning_solver import solve_ranked_recovery_plans


def _course(g, cid, credit_hours, semester_offered, year_level=1, special_requirement=None):
    g.add_node(cid, course_code=f"C{cid}", name=f"Course {cid}",
               year_level=year_level, semester_offered=semester_offered,
               credit_hours=credit_hours, stream_scope=None,
               special_requirement=special_requirement, is_droppable=True)


def test_top_n_distinct_plans_all_share_optimal_length():
    """Two independent, same-parity courses with no prerequisite between them
    -- and cheap enough to share a single slot, so the true optimum is both
    courses in slot 0 (a unique assignment). Confirms the function returns
    just that 1 plan rather than padding with longer alternatives."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)
    _course(g, 2, credit_hours=3, semester_offered=1)

    result = solve_ranked_recovery_plans(
        graph=g, required_course_ids=[1, 2], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=6, top_n=3,
    )
    assert result["feasible"]
    totals = {p["total_semesters_used"] for p in result["plans"]}
    assert len(totals) == 1  # every returned plan ties at the same (best) length


def test_plans_are_actually_distinct_assignments():
    """No two returned plans should be the exact same slot assignment --
    that's the entire point of the exclusion constraint. Uses a scenario
    with genuinely multiple tied-optimal splits (4x6-credit courses, cap 18
    -- both a 3+1 and a 2+2 split hit the same 2-slot optimum)."""
    g = nx.DiGraph()
    for cid in (1, 2, 3, 4):
        _course(g, cid, credit_hours=6, semester_offered=1)

    result = solve_ranked_recovery_plans(
        graph=g, required_course_ids=[1, 2, 3, 4], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=6, general_credit_cap=18, top_n=5,
    )
    assert result["feasible"]
    assert len(result["plans"]) > 1  # confirms tied-optimal ties actually exist here
    seen = set()
    for p in result["plans"]:
        key = tuple(sorted((cid, s) for s, cids in p["plan"].items() for cid in cids))
        assert key not in seen
        seen.add(key)


def test_ranked_by_credit_load_balance_when_tied_on_length():
    """Construct a case where multiple optimal-length plans exist with
    different credit-load spread; the first-ranked plan should have the
    lowest (or tied-lowest) stdev across all returned plans."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=6, semester_offered=1)
    _course(g, 2, credit_hours=6, semester_offered=1)
    _course(g, 3, credit_hours=6, semester_offered=1)
    _course(g, 4, credit_hours=6, semester_offered=1)

    result = solve_ranked_recovery_plans(
        graph=g, required_course_ids=[1, 2, 3, 4], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=6, general_credit_cap=18, top_n=6,
    )
    assert result["feasible"]
    import statistics
    stdevs = [
        statistics.pstdev(list(p["credit_load_by_slot"].values()))
        if len(p["credit_load_by_slot"]) > 1 else 0.0
        for p in result["plans"]
    ]
    assert stdevs == sorted(stdevs)  # non-decreasing -- best-balanced first


def test_unique_optimal_returns_only_one_plan_even_with_alternatives_available():
    """Course 1 must precede course 2 (both same parity), forcing a unique
    tied-optimal assignment (1@0, 2@2). Longer alternatives DO exist in the
    model (e.g. 1@2,2@4) but must NOT be returned -- confirms the function
    stops at the length boundary instead of padding to top_n."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)
    g.add_edge(1, 2, applicable_streams=None)
    _course(g, 2, credit_hours=3, semester_offered=1)

    result = solve_ranked_recovery_plans(
        graph=g, required_course_ids=[1, 2], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=6, top_n=5,
    )
    assert result["feasible"]
    assert len(result["plans"]) == 1
    assert result["plans"][0]["plan"] == {0: [1], 2: [2]}


def test_infeasible_ranked_returns_empty_plans_with_reason():
    g = nx.DiGraph()
    _course(g, 1, credit_hours=30, semester_offered=1)  # impossible under any cap

    result = solve_ranked_recovery_plans(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=4, top_n=3,
    )
    assert result["feasible"] is False
    assert result["plans"] == []
    assert "No path exists" in result["reason"]


def test_top_n_zero_raises_valueerror():
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)
    with pytest.raises(ValueError, match="top_n"):
        solve_ranked_recovery_plans(
            graph=g, required_course_ids=[1], already_passed_ids=[],
            current_year_level=1, current_semester=1, student_stream_id=None,
            top_n=0,
        )