"""
STAGE 4b FAKE DATA -- drop legality
======================================
Four courses, each isolating one specific rule so a failure in one
scenario can't be confused with a failure in another:

    NONDROP1    -- droppable=False, stands in for Internship/FYP/NEE
    DROP_A      -- ordinary droppable course
    DROP_B      -- ordinary droppable course, same shape as DROP_A
    DROP_TIGHT  -- droppable, but credit_hours (25) exceeds every cap
                   (21) in this fake data, so NO future slot can ever
                   have room for it -- guarantees the "no credit gap"
                   rule triggers regardless of horizon length

FOUR SCENARIOS:

  Scenario 1 -- non-droppable rule:
    Registered: [NONDROP1, DROP_A] (2 courses, so the "keep >=1" rule
    is NOT what should block this -- isolates rule 1 specifically)
    Try to drop NONDROP1 -> must be illegal, reason: not droppable.

  Scenario 2 -- "keep at least one course" rule:
    Registered: [DROP_A] (only 1 course)
    Try to drop DROP_A -> must be illegal, reason: would leave zero.

  Scenario 3 -- legal drop, full round trip:
    Registered: [DROP_A, DROP_B]
    Try to drop DROP_A -> legal. DROP_B is kept (assumed PASS at the
    in-progress slot). DROP_A gets a DROPPED record and is rescheduled
    to the next legal future slot.
    now_slot=0 -> in_progress_slot=1. DROP_A parity=1 (odd), next legal
    future odd slot after slot 1 is slot 3 (generous caps, no
    competition). Expected resulting schedule: {DROP_A: 3, DROP_B: 1}.
    Expected graduation slot: 3.

  Scenario 4 -- "future credit gap" rule:
    Registered: [DROP_TIGHT, DROP_A] (2 courses, so rule 2 isn't the
    blocker here either -- isolates rule 3 specifically)
    Try to drop DROP_TIGHT -> must be illegal, reason: no future
    semester has enough room (25 credit hours never fits under any
    21-credit cap).
"""

COURSES = {
    "NONDROP1":   {"credit_hours": 3,  "semester_offered": 1, "year_level": 1, "prereqs": [], "droppable": False},
    "DROP_A":     {"credit_hours": 3,  "semester_offered": 1, "year_level": 1, "prereqs": [], "droppable": True},
    "DROP_B":     {"credit_hours": 3,  "semester_offered": 1, "year_level": 1, "prereqs": [], "droppable": True},
    "DROP_TIGHT": {"credit_hours": 25, "semester_offered": 1, "year_level": 1, "prereqs": [], "droppable": True},
}

HORIZON_SLOTS = 8  # generous -- Scenario 4's impossibility comes from
                    # credit hours exceeding every cap, not from running
                    # out of horizon, so a long horizon can't accidentally
                    # rescue it and mask a bug

NORMAL_SEMESTER_CREDIT_CAP = {
    (y, s): 21 for y in range(1, 5) for s in (1, 2)
}

MAX_ATTEMPTS = 3
NOW_SLOT = 0  # nothing historical yet; current in-progress semester is slot 1

SCENARIO_1_NON_DROPPABLE = {
    "current_semester_courses": ["NONDROP1", "DROP_A"],
    "drop_course": "NONDROP1",
}
EXPECTED_1 = {"legal": False, "reason_contains": "not droppable"}

SCENARIO_2_KEEP_AT_LEAST_ONE = {
    "current_semester_courses": ["DROP_A"],
    "drop_course": "DROP_A",
}
EXPECTED_2 = {"legal": False, "reason_contains": "leave zero courses"}

SCENARIO_3_LEGAL_DROP = {
    "current_semester_courses": ["DROP_A", "DROP_B"],
    "drop_course": "DROP_A",
    # Only the courses actually relevant to this student's remaining
    # requirements -- NOT the full COURSES dict, which also contains
    # DROP_TIGHT (25 credit hours, can never fit any cap). Passing the
    # full dict here would make build_and_solve try to schedule an
    # unrelated impossible course and report a false infeasible.
    "courses": {"DROP_A": COURSES["DROP_A"], "DROP_B": COURSES["DROP_B"]},
}
EXPECTED_3 = {
    "legal": True,
    "resulting_schedule": {"DROP_A": 3, "DROP_B": 1},
    "resulting_graduation_slot": 3,
}

SCENARIO_4_NO_CREDIT_GAP = {
    "current_semester_courses": ["DROP_TIGHT", "DROP_A"],
    "drop_course": "DROP_TIGHT",
}
EXPECTED_4 = {"legal": False, "reason_contains": "enough credit-hour"}

# Scenario 5 -- combined violations, proves reasons accumulate rather than
# short-circuiting at the first failed rule. NONDROP1 is both
# non-droppable AND the only course this semester -- a bot explaining
# "why not" to a student needs both reasons, not just whichever rule
# happened to be checked first.
SCENARIO_5_COMBINED_VIOLATIONS = {
    "current_semester_courses": ["NONDROP1"],
    "drop_course": "NONDROP1",
}
EXPECTED_5 = {"legal": False, "reason_count": 2}