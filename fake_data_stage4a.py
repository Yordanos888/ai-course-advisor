"""
STAGE 4a FAKE DATA -- retake limit
====================================
Deliberately minimal: ONE course, no prereqs, no cap pressure. Everything
about prereqs/caps/parity/domino-effects was already proven in Stages 1-3;
adding them back in here would just make it harder to tell whether a
failure is about the retake limit or about something else. This stage
tests exactly one thing.

Campus rule: 3 graded attempts maximum. DROPPED does not count as a
graded attempt (only PASS/FAILED do).

THREE SCENARIOS:

  Student A -- AT the limit, not over it:
    2 past FAILED attempts. 1 attempt remains. Should succeed, scheduling
    the 3rd (final legal) attempt in the future.

  Student B -- OVER the limit:
    3 past FAILED attempts, still not passed. 0 attempts remain, but the
    course is still required. Should be INFEASIBLE, with a clear reason
    -- not a silently wrong "plan" that pretends a 4th attempt is legal.

  Student C -- proves DROPPED doesn't count:
    1 FAILED + 1 DROPPED, still not passed. Only 1 of the 2 past attempts
    counts as "graded" (the DROPPED one doesn't), so 2 attempts remain.
    Should succeed -- same shape of success as Student A, different
    reason (this specifically tests that DROPPED is excluded from the
    count, not just that "having attempts left" works).
"""

COURSES = {
    "REQ1": {"credit_hours": 3, "semester_offered": 1, "year_level": 1, "prereqs": []},
}

HORIZON_SLOTS = 8  # 4 years, generous slack

# Deliberately generous caps -- this stage isn't testing cap logic, so caps
# should never be the reason anything succeeds or fails here.
NORMAL_SEMESTER_CREDIT_CAP = {
    (y, s): 21 for y in range(1, 5) for s in (1, 2)
}

MAX_ATTEMPTS = 3

STUDENT_A_AT_LIMIT = {
    "id": "fake_student_4a_A",
    "now_slot": 3,
    "completed_courses": {
        "REQ1": [
            {"status": "FAILED", "slot": 1},
            {"status": "FAILED", "slot": 3},
        ],
    },
}
EXPECTED_A = {"feasible": True, "schedule": {"REQ1": 5}, "graduation_slot": 5}

STUDENT_B_OVER_LIMIT = {
    "id": "fake_student_4a_B",
    "now_slot": 5,
    "completed_courses": {
        "REQ1": [
            {"status": "FAILED", "slot": 1},
            {"status": "FAILED", "slot": 3},
            {"status": "FAILED", "slot": 5},
        ],
    },
}
EXPECTED_B = {"feasible": False}  # must be diagnosed, not silently patched over

STUDENT_C_DROPPED_EXCLUDED = {
    "id": "fake_student_4a_C",
    "now_slot": 3,
    "completed_courses": {
        "REQ1": [
            {"status": "FAILED", "slot": 1},
            {"status": "DROPPED", "slot": 3},
        ],
    },
}
EXPECTED_C = {"feasible": True, "schedule": {"REQ1": 5}, "graduation_slot": 5}