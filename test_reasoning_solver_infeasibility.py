"""
Tests for infeasibility handling (Rule D retake exhaustion + general clarity).
Split into two groups:
  - Pure-Python tests (no ortools) for the exhausted_course_ids short-circuit
    and the ValueError validation -- these run before any CP-SAT model is
    even built, so they don't need the solver installed.
  - CP-SAT tests (need ortools) confirming a genuine INFEASIBLE case still
    returns the clean "no path exists" message via the real solver.
Run with: pytest test_reasoning_solver_infeasibility.py -v
"""
import networkx as nx
import pytest
from reasoning_solver import solve_recovery_plan


def _course(g, cid, credit_hours, semester_offered, year_level=1, special_requirement=None):
    g.add_node(cid, course_code=f"C{cid}", name=f"Course {cid}",
               year_level=year_level, semester_offered=semester_offered,
               credit_hours=credit_hours, stream_scope=None,
               special_requirement=special_requirement, is_droppable=True)


# --- Rule D: retake exhaustion (no ortools needed -- short-circuits before model build) ---

def test_exhausted_required_course_returns_clean_infeasible():
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=2, current_semester=1, student_stream_id=None,
        exhausted_course_ids={1},
    )
    assert result["feasible"] is False
    assert result["plan"] is None
    assert "retake limit exhausted" in result["reason"]
    assert "C1" in result["reason"]


def test_exhausted_course_not_required_has_no_effect():
    """A course being in exhausted_course_ids only matters if it's actually
    still required -- e.g. a course the student separately already passed
    on an earlier attempt shouldn't block anything."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        exhausted_course_ids={999},  # unrelated course_id, not in required set
        horizon_semesters=4,
    )
    assert result["feasible"] is True


def test_exhausted_course_short_circuits_before_horizon_check():
    """Even a student already past the max-years horizon should get the
    retake-exhaustion message first if that's ALSO true, since it's checked
    first and is arguably the more specific/actionable reason. This just
    documents current precedence -- exhaustion check runs before the horizon
    check."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=6, current_semester=1, student_stream_id=None,  # already past year5
        exhausted_course_ids={1},
    )
    assert result["feasible"] is False
    assert "retake limit exhausted" in result["reason"]


# --- Transitive closure validation (needs ortools -- model is partially built
# before the check is reached, since it lives inside the prerequisite loop) ---

def test_missing_prerequisite_raises_valueerror_not_silent_wrong_answer():
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)  # prerequisite, MISSING from required set
    _course(g, 2, credit_hours=3, semester_offered=1)
    g.add_edge(1, 2, applicable_streams=None)

    with pytest.raises(ValueError, match="not transitively closed"):
        solve_recovery_plan(
            graph=g, required_course_ids=[2], already_passed_ids=[],  # course 1 omitted!
            current_year_level=1, current_semester=1, student_stream_id=None,
            horizon_semesters=4,
        )


def test_prerequisite_correctly_marked_passed_does_not_raise():
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)
    _course(g, 2, credit_hours=3, semester_offered=1)
    g.add_edge(1, 2, applicable_streams=None)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[2], already_passed_ids=[1],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=4,
    )
    assert result["feasible"] is True


# --- Genuine CP-SAT infeasibility (needs ortools) ---

def test_genuine_infeasible_credit_cap_returns_clean_message():
    """A single course whose credit hours exceed even the year5 override cap
    can never be scheduled, in any slot, ever -- true INFEASIBLE status from
    CP-SAT itself, not a data-assembly bug."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=30, semester_offered=1)  # exceeds every possible cap

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=4, general_credit_cap=18, year5_credit_cap=22,
    )
    assert result["feasible"] is False
    assert result["plan"] is None
    assert "No path exists" in result["reason"]


def test_feasible_case_unaffected_by_new_messaging():
    """Sanity check: normal feasible cases still work exactly as before --
    the new status branches shouldn't have broken the happy path."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=4,
    )
    assert result["feasible"] is True
    assert result["plan"] == {0: [1]}