"""
STAGE 1 FAKE DATA
==================
Purpose: smallest possible curriculum that still exercises the two real
constraints (prerequisites + credit cap) so we can hand-verify the solver's
output before trusting it with anything harder.

Shape deliberately mirrors the real curriculum's fields (see models.py /
ece_curriculum.csv) so Stage 8's swap to real data is a data-mapping change,
not a model rewrite.

Semester "slots" are just integers 1..HORIZON. Slot parity determines which
semester-in-year it represents:
    odd slot  -> "1st semester" courses (semester_offered == 1)
    even slot -> "2nd semester" courses (semester_offered == 2)

No summer sessions in Stage 1 (real curriculum has none needed for this test).
"""

# Each course: code -> dict
# credit_hours, semester_offered (1 or 2), year_level, prereqs (list of course codes)
COURSES = {
    # ---- Year 1 ----
    "C101": {"credit_hours": 3, "semester_offered": 1, "year_level": 1, "prereqs": []},
    "C102": {"credit_hours": 3, "semester_offered": 1, "year_level": 1, "prereqs": []},
    "C103": {"credit_hours": 3, "semester_offered": 1, "year_level": 1, "prereqs": []},

    "C104": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": ["C101"]},
    "C105": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": []},
    "C106": {"credit_hours": 3, "semester_offered": 2, "year_level": 1, "prereqs": ["C102"]},

    # ---- Year 2 ----
    "C201": {"credit_hours": 4, "semester_offered": 1, "year_level": 2, "prereqs": ["C104"]},
    "C202": {"credit_hours": 3, "semester_offered": 1, "year_level": 2, "prereqs": []},

    "C203": {"credit_hours": 4, "semester_offered": 2, "year_level": 2, "prereqs": ["C201"]},
    "C204": {"credit_hours": 3, "semester_offered": 2, "year_level": 2, "prereqs": ["C105"]},
}

# Planning horizon: how many semester-slots the solver is allowed to consider.
# Deliberately larger than the 4 slots actually needed, so that "the solver
# chose to finish early anyway" is a real signal, not a forced outcome.
HORIZON_SLOTS = 8  # 4 years x 2 semesters/year

# Per-semester credit cap, per campus rule: "sum of courses normally offered
# in that semester." Keyed by (year_level, semester_offered) since that's
# what determines which courses are "normally offered" together.
# Computed here by hand from COURSES above, for Stage 1 only -- Stage 8 will
# compute this from real data instead of hardcoding it.
NORMAL_SEMESTER_CREDIT_CAP = {
    (1, 1): 3 + 3 + 3,  # C101+C102+C103 = 9
    (1, 2): 3 + 3 + 3,  # C104+C105+C106 = 9
    (2, 1): 4 + 3,      # C201+C202      = 7
    (2, 2): 4 + 3,      # C203+C204      = 7
}

# A student with NO failures, NO retakes -- the simplest possible case.
# Stage 1 asks: does the solver reconstruct the obvious on-time path?
STUDENT_STAGE1 = {
    "id": "fake_student_01",
    "completed_courses": {},  # course_code -> status, empty = fresh start
}

# Hand-computed expected answer (this is what we check the solver against):
EXPECTED_SCHEDULE_STAGE1 = {
    "C101": 1, "C102": 1, "C103": 1,   # slot 1 (Y1 Sem1)
    "C104": 2, "C105": 2, "C106": 2,   # slot 2 (Y1 Sem2)
    "C201": 3, "C202": 3,              # slot 3 (Y2 Sem1)
    "C203": 4, "C204": 4,              # slot 4 (Y2 Sem2)
}
EXPECTED_GRADUATION_SLOT = 4