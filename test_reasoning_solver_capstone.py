"""
CP-SAT integration tests for the Exit Exam (special_requirement='ALL_COURSES')
capstone modeling. Requires ortools -- run with:
pytest test_reasoning_solver_capstone.py -v
"""
import networkx as nx
from reasoning_solver import solve_recovery_plan


def _course(g, cid, credit_hours, semester_offered, year_level=1, special_requirement=None):
    g.add_node(cid, course_code=f"C{cid}", name=f"Course {cid}",
               year_level=year_level, semester_offered=semester_offered,
               credit_hours=credit_hours, stream_scope=None,
               special_requirement=special_requirement, is_droppable=True)


def test_exam_lands_with_the_last_scheduled_course_not_after():
    """Exam should be pinned to the SAME slot as the max of everything else
    -- not an extra semester on top -- confirming it's detached from slot
    competition (0 credits, no cap contribution)."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)
    _course(g, 2, credit_hours=3, semester_offered=2)  # lands later than course 1
    _course(g, 99, credit_hours=0, semester_offered=1, special_requirement='ALL_COURSES')

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2, 99], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=6,
    )
    assert result["feasible"]
    plan = result["plan"]
    slot_of = {cid: s for s, cids in plan.items() for cid in cids}
    assert slot_of[99] == max(slot_of[1], slot_of[2])
    # total_semesters_used must equal what it'd be WITHOUT the exam at all --
    # confirms it added no extra semester
    assert result["total_semesters_used"] == max(slot_of[1], slot_of[2]) + 1


def test_exam_waits_for_same_semester_peer_like_fyp2():
    """Even a course nominally 'in the same semester' as the exam (e.g.
    FYP-II) must complete no later than the exam -- since required_course_ids
    is the caller's job to make transitively complete, this just confirms the
    max-equality genuinely covers ALL other required courses, not a subset."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=3, semester_offered=1)   # ordinary course
    _course(g, 2, credit_hours=6, semester_offered=2, year_level=5)  # stand-in for FYP-II
    _course(g, 99, credit_hours=0, semester_offered=1, special_requirement='ALL_COURSES')

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 2, 99], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=6,
    )
    assert result["feasible"]
    plan = result["plan"]
    slot_of = {cid: s for s, cids in plan.items() for cid in cids}
    assert slot_of[99] >= slot_of[1]
    assert slot_of[99] >= slot_of[2]


def test_exam_is_only_thing_left_schedules_immediately():
    """If everything else is already PASSED, the exam alone should schedule
    at slot 0 (nothing to wait for) -- exercises the 'no others' fallback."""
    g = nx.DiGraph()
    _course(g, 99, credit_hours=0, semester_offered=1, special_requirement='ALL_COURSES')

    result = solve_recovery_plan(
        graph=g, required_course_ids=[99], already_passed_ids=[],
        current_year_level=5, current_semester=2, student_stream_id=None,
        horizon_semesters=2,
    )
    assert result["feasible"]
    assert result["plan"][0] == [99]
    assert result["total_semesters_used"] == 1


def test_exam_does_not_count_against_credit_cap():
    """Exam (0 credit) sharing a slot with a course at exactly the cap
    should not push the total over -- confirms it's not double-counted or
    blocked by the cap check."""
    g = nx.DiGraph()
    _course(g, 1, credit_hours=18, semester_offered=1)  # exactly at general cap
    _course(g, 99, credit_hours=0, semester_offered=1, special_requirement='ALL_COURSES')

    result = solve_recovery_plan(
        graph=g, required_course_ids=[1, 99], already_passed_ids=[],
        current_year_level=1, current_semester=1, student_stream_id=None,
        horizon_semesters=4, general_credit_cap=18,
    )
    assert result["feasible"]
    slot_of = {cid: s for s, cids in result["plan"].items() for cid in cids}
    assert slot_of[99] == slot_of[1]  # shares the same slot, no cap violation