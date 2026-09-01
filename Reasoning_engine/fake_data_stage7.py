"""
STAGE 7 FAKE DATA
==================
Purpose: force a genuine tie in graduation speed between structurally
DIFFERENT plans, so multi-path enumeration has something real to find
and rank -- not just re-discover the single plan our tie-break already
prefers.

Three independent courses (no prereqs between them), all parity 1:
    Q1: 6 credits
    Q2: 3 credits
    Q3: 3 credits

Cap at (year1, sem1) = 9. No pair of all three fits together (6+3+3=12),
but any TWO of them fit exactly at cap (Q1+Q2=9, Q1+Q3=9, Q2+Q3=6) --
so exactly one course always gets pushed to the next sem1 slot (slot 3).
Every possible pairing achieves the same graduation slot (3) -- there is
NO way to finish faster or slower. Graduation speed is a pure four-way(*)
tie. The only thing that differs between the tied plans is HOW EVENLY
the credit load is spread across slot 1 and slot 3:

    Q1+Q2 @ slot1 (9 credits), Q3 @ slot3 (3 credits)  -> load (9, 3)
    Q1+Q3 @ slot1 (9 credits), Q2 @ slot3 (3 credits)  -> load (9, 3)
    Q2+Q3 @ slot1 (6 credits), Q1 @ slot3 (6 credits)  -> load (6, 6)  <-- most even

(*) Only 3 distinct optimal assignments exist given 3 courses and a
2-slot outcome; two of them happen to produce the same LOAD SHAPE (9,3)
since Q2 and Q3 are interchangeable 3-credit courses, but they are still
different course-to-slot assignments.

If we only ever ran the single-best-solution solver (Stages 1-6), it
would silently return ONE of these three and the student would never
see that a perfectly even-load alternative exists at no cost in time.
Stage 7 exists specifically to surface that.
"""

COURSES = {
    "Q1": {"credit_hours": 6, "semester_offered": 1, "prereqs": []},
    "Q2": {"credit_hours": 3, "semester_offered": 1, "prereqs": []},
    "Q3": {"credit_hours": 3, "semester_offered": 1, "prereqs": []},
}

HORIZON_SLOTS = 6

NORMAL_SEMESTER_CREDIT_CAP = {
    (1, 1): 9,
    (2, 1): 21,   # generous, wherever the pushed-out course lands
    (1, 2): 21,
    (2, 2): 21,
    (3, 1): 21,
    (3, 2): 21,
}

EXPECTED_GRADUATION_SLOT = 3  # forced, no plan can do better or worse

# The single most-even plan that MUST be found among the enumerated
# results, and MUST rank #1 by evenness (variance 0, vs variance 9 for
# the other two shapes).
EXPECTED_BEST_EVENNESS_SCHEDULE = {"Q2": 1, "Q3": 1, "Q1": 3}
EXPECTED_BEST_EVENNESS_VARIANCE = 0.0

# The two other valid optimal shapes that should ALSO be discoverable
# (proving enumeration finds more than just the tie-break's favorite),
# even though they rank worse on evenness.
OTHER_VALID_SCHEDULE_SHAPES = [
    {"Q1": 1, "Q2": 1, "Q3": 3},
    {"Q1": 1, "Q3": 1, "Q2": 3},
]
OTHER_SHAPES_VARIANCE = 9.0