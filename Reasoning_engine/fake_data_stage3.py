"""
STAGE 3 FAKE DATA
==================
Purpose: prove the solver is choosing the right path because it correctly
computes downstream consequences -- not because it got lucky with the
tie-break rule from Stage 1/2.

THE TRAP (deliberately built in):
    Two independent prerequisite chains share a slot that's too tight for
    both to start together:

        Z1 -> Z2 -> Z3     (long chain, 3 deep)
        B1 -> B2           (short chain, 2 deep)

    Both Z1 and B1 want the same earliest slot (parity 1, no prereqs of
    their own). The cap on that slot only fits ONE of them (6 credits
    each, cap is 6). Whichever one is bumped pays a delay; the other
    proceeds on schedule.

    Correct reasoning: bump the SHORT chain (B), because it has less
    total distance to make up. Bumping the LONG chain (Z) costs more,
    because its downstream dependents (Z2, Z3) all get pushed back too.

    THE TRAP: our own tie-break rule (Stage 1/2) prefers lower course
    codes into earlier slots. "B" sorts before "Z" alphabetically -- so
    the tie-break, if it were ever allowed to actually decide this, would
    push the solver toward starting B first, which is the WRONG answer.
    The only thing that can correctly override the tie-break here is the
    PRIMARY objective (minimize actual graduation slot) genuinely
    computing that starting Z first produces a shorter total graduation
    time. If Stage 3 passes, it's proof the primary objective dominates
    correctly, not proof the tie-break got lucky.

HAND-COMPUTED EXPECTED ANSWER -- both candidate orderings, worked by hand:

  CANDIDATE A (start Z first -- correct):
    Z1@1 (uses full cap 6 at slot 1)
    Z2: needs Z1(1), parity 2, earliest even slot -> Z2@2
    Z3: needs Z2(2), parity 1, earliest odd slot > 2 -> Z3@3
    B1: no prereq, parity 1, slot 1 full -> earliest available is slot 3
        (cap at slot 3 is roomy: 21, Z3=3cr + B1=6cr = 9 <= 21, fits) -> B1@3
    B2: needs B1(3), parity 2, earliest even slot > 3 -> B2@4
    GRADUATION SLOT = 4

  CANDIDATE B (start B first -- the trap, alphabetically "tempting"):
    B1@1 (uses full cap 6 at slot 1)
    B2: needs B1(1), parity 2, earliest even slot -> B2@2
    Z1: no prereq, parity 1, slot 1 full -> earliest available slot 3 -> Z1@3
    Z2: needs Z1(3), parity 2, earliest even slot > 3 -> Z2@4
    Z3: needs Z2(4), parity 1, earliest odd slot > 4 -> Z3@5
    GRADUATION SLOT = 5

  Candidate A (4) beats Candidate B (5). The solver must find Candidate A.
"""

COURSES = {
    "Z1": {"credit_hours": 6, "semester_offered": 1, "year_level": 1, "prereqs": []},
    "Z2": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": ["Z1"]},
    "Z3": {"credit_hours": 3, "semester_offered": 1, "year_level": 2, "prereqs": ["Z2"]},

    "B1": {"credit_hours": 6, "semester_offered": 1, "year_level": 1, "prereqs": []},
    "B2": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": ["B1"]},
}

HORIZON_SLOTS = 6  # 3 years, plenty of slack beyond the 4-5 slots actually needed

# Explicit caps for every (year, sem) reachable within the horizon -- not
# relying on the model's default fallback, so this test's numbers are
# fully self-contained and auditable.
NORMAL_SEMESTER_CREDIT_CAP = {
    (1, 1): 6,    # THE TIGHT SLOT -- only room for one of Z1/B1
    (1, 2): 9,
    (2, 1): 21,   # roomy on purpose -- nothing should be capped here
    (2, 2): 21,
    (3, 1): 21,
    (3, 2): 21,
}

NOW_SLOT = 0  # fresh start, no history -- isolates this test to pure
              # trade-off reasoning, uncontaminated by Stage 2's retake logic

STUDENT_STAGE3 = {
    "id": "fake_student_03",
    "completed_courses": {},
}

EXPECTED_SCHEDULE_STAGE3 = {
    "Z1": 1, "Z2": 2, "Z3": 3,
    "B1": 3, "B2": 4,
}
EXPECTED_GRADUATION_SLOT = 4