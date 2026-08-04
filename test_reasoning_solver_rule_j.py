"""
CP-SAT integration tests for Rule J -- common courses schedulable via either
semester parity (home dept's parity OR the partner dept's flipped parity).
Requires ortools -- run with: pytest test_reasoning_solver_rule_j.py -v
"""
import networkx as nx
from reasoning_solver import solve_recovery_plan


def _course(g, cid, credit_hours, semester_offered, year_level=1):
    g.add_node(cid, course_code=f"C{cid}", name=f"Course {cid}",
               year_level=year_level, semester_offered=semester_offered,
               credit_hours=credit_hours, stream_scope=None,
               special_requirement=None, is_droppable=True)


def test_non_flipped_course_waits_for_home_parity():
    """Baseline (no Rule J): student's current slot parity doesn't match the
    course's home parity, so it can't be scheduled at slot 0 -- must wait for
    slot 1 (the next matching-parity slot)."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)  # home parity 1

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=2, current_semester=2,  # slot0 parity = 2, mismatch
        student_stream_id=None, horizon_semesters=4,
    )
    assert result["feasible"]
    assert result["total_semesters_used"] == 2  # had to wait for slot 1


def test_flipped_course_can_use_immediate_mismatched_slot():
    """Same course, same student position, but flagged as Rule J eligible --
    should now fit in slot 0 immediately, since the flip opens both parities."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)  # home parity 1

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1], already_passed_ids=[],
        current_year_level=2, current_semester=2,  # slot0 parity = 2, mismatch
        student_stream_id=None, horizon_semesters=4,
        flipped_parity_course_ids={1},
    )
    assert result["feasible"]
    assert result["total_semesters_used"] == 1  # no longer waits


def test_flipped_course_still_respects_prerequisites():
    """Rule J only widens the parity domain -- prerequisite ordering must
    still hold. Course 2 (flipped) requires course 1 first."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)
    _course(g, 2, credit_hours=3, semester_offered=1)
    g.add_edge(1, 2, applicable_streams=None)

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2], already_passed_ids=[],
        current_year_level=1, current_semester=1,
        student_stream_id=None, horizon_semesters=6,
        flipped_parity_course_ids={2},  # only the downstream course is flipped
    )
    assert result["feasible"]
    plan = result["plan"]
    slot_of = {cid: s for s, cids in plan.items() for cid in cids}
    assert slot_of[1] < slot_of[2]  # prerequisite order still enforced


def test_flip_does_not_affect_non_flipped_siblings_credit_cap_slot():
    """Two courses in the same slot: one flipped (can move freely), one not
    (locked to its home parity). Confirms the flip is per-course, not global."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=10, semester_offered=1)   # not flipped, home parity 1
    _course(g, 2, credit_hours=10, semester_offered=2)   # flipped, home parity 2

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2], already_passed_ids=[],
        current_year_level=1, current_semester=1,  # slot0 parity = 1
        student_stream_id=None, horizon_semesters=4,
        general_credit_cap=18, flipped_parity_course_ids={2},
    )
    assert result["feasible"]
    plan = result["plan"]
    slot_of = {cid: s for s, cids in plan.items() for cid in cids}
    # course 1 can only ever be in an odd-parity-matching slot (0, 2, ...)
    assert slot_of[1] in (0, 2)
    # course 2, flipped, can land in slot 0 too (both fit under 18 cap only if
    # split -- 10+10=20>18 so they can't share slot 0; confirm they don't)
    assert slot_of[1] != slot_of[2]