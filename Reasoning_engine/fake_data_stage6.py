"""
STAGE 6 FAKE DATA
==================
One combined scenario exercising both structural requirements together,
since in reality a student has both hanging over the same plan.

Chain (deliberately 3-deep, not 2, so a missing FYP-II constraint would
be UNMISTAKABLE rather than coincidentally correct):
    SC1 -> SC1b -> SC2      (3 courses, finishes at slot 3)
    FINAL_PEER                (independent, no prereq, finishes at slot 2)
    FYP2                      (special_requirement = ALL_STREAM_COURSES)
    NEE_EXAM                  (special_requirement = ALL_COURSES, 0 credit)

HAND-COMPUTED EXPECTED ANSWER:

  SC1: parity 1, no prereq -> slot 1
  SC1b: parity 2, needs SC1(1) -> slot 2
  SC2: parity 1, needs SC1b(2) -> slot 3
  FINAL_PEER: parity 2, no prereq -> slot 2 (shares with SC1b, generous cap)

  FYP2: must be >= every OTHER course in this student's course list
        (SC1=1, SC1b=2, SC2=3, FINAL_PEER=2) -> must be >= 3.
        FYP2's own parity is 2 (even). Earliest even slot >= 3 is slot 4.
        -> FYP2 @ 4

        THE TRAP: if the ALL_STREAM_COURSES constraint were forgotten,
        FYP2 (no other prereqs, generous cap) would minimize freely to
        its own earliest parity-2 slot -- slot 2 -- which would mean the
        capstone project is "scheduled" a full slot before SC2 (one of
        its own required stream courses) even finishes. That's a real
        rule violation, not a cosmetic difference: FYP2=2 vs FYP2=4 is a
        big, unmistakable gap if this constraint is missing.

  NEE_EXAM: post-hoc, not a decision variable. Gates on the true max of
            ALL other courses = max(1,2,3,2,4) = 4. NEE's own parity is
            1 (odd). Earliest slot >= 4 matching parity 1 is slot 5
            (slot 4 is even, doesn't match; slot 5 is odd and >= 4).
            -> NEE_EXAM @ 5

  GRADUATION SLOT (true final gate) = 5
"""

COURSES = {
    "SC1": {"credit_hours": 3, "semester_offered": 1, "year_level": 1, "prereqs": []},
    "SC1b": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": ["SC1"]},
    "SC2": {"credit_hours": 3, "semester_offered": 1, "year_level": 2, "prereqs": ["SC1b"]},
    "FINAL_PEER": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": []},

    "FYP2": {
        "credit_hours": 6, "semester_offered": 2, "year_level": 2, "prereqs": [],
        "special_requirement": "ALL_STREAM_COURSES",
    },

    "NEE_EXAM": {
        "credit_hours": 0, "semester_offered": 1, "year_level": 3, "prereqs": [],
        "special_requirement": "ALL_COURSES",
    },
}

HORIZON_SLOTS = 10  # 5 years, generous slack

NORMAL_SEMESTER_CREDIT_CAP = {
    (y, s): 21 for y in range(1, 6) for s in (1, 2)
}

EXPECTED_SCHEDULE = {
    "SC1": 1, "SC1b": 2, "SC2": 3, "FINAL_PEER": 2,
    "FYP2": 4,
    "NEE_EXAM": 5,
}
EXPECTED_GRADUATION_SLOT = 5