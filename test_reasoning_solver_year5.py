"""
CP-SAT integration tests for the year-5 credit-cap override + the max-years
horizon clamp. Requires ortools -- run with: pytest test_reasoning_solver_year5.py -v

Uses tiny synthetic graphs (not the real curriculum) so each test isolates
one behavior.
"""
import networkx as nx
import pytest
from reasoning_solver import solve_recovery_plan


def _course(g, cid, credit_hours, semester_offered, year_level=1):
    g.add_node(cid, course_code=f"C{cid}", name=f"Course {cid}",
               year_level=year_level, semester_offered=semester_offered,
               credit_hours=credit_hours, stream_scope=None,
               special_requirement=None, is_droppable=True)


def test_general_cap_enforced_before_year5():
    """3 independent 8-credit, same-parity courses, cap=18 -> can't fit all
    3 (24 credits) in one slot; must spread across >=2 semesters."""
    g = nx.DiGraph()
    for cid in (1, 2, 3):
        _course(g, cid, credit_hours=8, semester_offered=1)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2, 3], already_passed_ids=[],
        current_year_level=2, current_semester=1, student_stream_id=None,
        horizon_semesters=6, general_credit_cap=18, year5_credit_cap=22,
    )
    assert result["feasible"]
    assert result["total_semesters_used"] >= 2  # 24 credits can't fit in one 18-cap slot


def test_year5_override_allows_load_general_cap_would_block():
    """Same 24-credit load, but the student is already in year5 sem1 --
    should fit in ONE semester under the 22-cap... wait 24>22 still doesn't
    fit in one. Use 22 credits exactly (3 courses: 8+8+6) to confirm it fits
    in one year5 semester (22 <= year5_credit_cap) but would NOT fit under
    the general 18 cap."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=8, semester_offered=1)
    _course(g, 2, credit_hours=8, semester_offered=1)
    _course(g, 3, credit_hours=6, semester_offered=1)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2, 3], already_passed_ids=[],
        current_year_level=5, current_semester=1, student_stream_id=None,
        horizon_semesters=2, general_credit_cap=18, year5_credit_cap=22,
    )
    assert result["feasible"]
    assert result["total_semesters_used"] == 1  # all 22 credits fit in the single year5 slot


def test_year5_cap_does_not_leak_backward_into_year4():
    """Same 22-credit load, but starting at year4 sem2 (one slot before
    year5). Slot 0 (year4 sem2) must use the general 18 cap, not 22 --
    only year5+ slots get the override."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=8, semester_offered=2)
    _course(g, 2, credit_hours=8, semester_offered=2)
    _course(g, 3, credit_hours=6, semester_offered=2)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2, 3], already_passed_ids=[],
        current_year_level=4, current_semester=2, student_stream_id=None,
        horizon_semesters=4, general_credit_cap=18, year5_credit_cap=22,
    )
    assert result["feasible"]
    # slot 0 = year4 sem2 (general cap, 22 credits illegal there) so it must
    # spill into slot 2 (next matching-parity slot, which lands in year5,
    # where the 22-credit load is legal).
    assert result["total_semesters_used"] > 1


def test_horizon_clamped_at_max_years_produces_infeasible_not_illegal_year6():
    """A course only offered in a parity slot that would fall in year6+
    should come back infeasible, never silently scheduled past year5."""
    g = nx.DiGraph()
    # Opposite parity to the student's current semester, so slot 0 (the only
    # slot left within the horizon) can't hold it -- the next matching-parity
    # slot would be slot 1, which falls in year6 and must be excluded.
    _course(g, 1, credit_hours=3, semester_offered=1)

    # Student in year5 sem2 (the last legal semester).
    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=5, current_semester=2, student_stream_id=None,
        horizon_semesters=10,  # deliberately large; must be clamped internally
        general_credit_cap=18, year5_credit_cap=22,
    )
    assert result["feasible"] is False
    assert result["plan"] is None