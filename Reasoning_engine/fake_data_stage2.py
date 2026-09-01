"""
STAGE 2 FAKE DATA
==================
Same 10-course fake curriculum as Stage 1 (reused, not duplicated, so we
know any behavior difference comes from the model change, not the data).

New concept: this student is not starting from scratch. Slot 1 already
happened. Two of its three courses passed; one failed.

    C101 -> FAILED  (blocks C104 -> C201 -> C203, a 3-deep chain)
    C102 -> PASS
    C103 -> PASS

NOW_SLOT = 1 means "slot 1 is history, the solver only gets to decide
slots 2 onward."

WHY THIS SCENARIO: C101 sits at the root of the deepest prerequisite chain
in the fake curriculum (C101 -> C104 -> C201 -> C203). If the retake and
its downstream delay are handled correctly, this is the single scenario
most likely to expose an off-by-one in "strictly after" or a parity bug --
which is exactly the kind of thing that broke silently in earlier
vibe-coded attempts.

HAND-COMPUTED EXPECTED ANSWER (worked through step by step, not guessed):

  C101 retake: earliest FUTURE slot matching parity 1 (odd), after slot 1
               -> slot 3 (slot 1 is past, slot 2 is wrong parity)
  C104: needs C101 done (slot 3), parity 2 (even), earliest even slot > 3
               -> slot 4
  C201: needs C104 done (slot 4), parity 1 (odd), earliest odd slot > 4
               -> slot 5
  C203: needs C201 done (slot 5), parity 2 (even), earliest even slot > 5
               -> slot 6

  Meanwhile, unrelated courses proceed on the earliest schedule still
  available to them:
  C105: no prereq, parity 2, earliest future even slot -> slot 2
  C106: needs C102 (already passed), parity 2, earliest future even slot,
        can share slot 2 with C105 (3+3=6 <= cap 9) -> slot 2
  C202: no prereq, parity 1, earliest future odd slot -> slot 3
        (can it share slot 3 with the C101 retake? cap for (year2,sem1)
        is 7; C101 retake=3cr + C202=3cr = 6 <= 7, yes it fits)
        THIS is the "domino effect" case: C202 moves into the gap that
        opened up because C101's retake is sitting in slot 3 anyway.
  C204: needs C105 (slot 2), parity 2, earliest even slot > 2
        -> slot 4 (shares with C104: 3+3=6 <= cap 7 for year2 sem2)

Graduation slot (max over all courses): 6
"""

from fake_data_stage1 import COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP

NOW_SLOT = 1

STUDENT_STAGE2 = {
    "id": "fake_student_02",
    "completed_courses": {
        "C101": {"status": "FAILED", "slot": 1},
        "C102": {"status": "PASS", "slot": 1},
        "C103": {"status": "PASS", "slot": 1},
    },
}

EXPECTED_SCHEDULE_STAGE2 = {
    "C101": 3,
    "C102": 1,   # historical, fixed
    "C103": 1,   # historical, fixed
    "C104": 4,
    "C105": 2,
    "C106": 2,
    "C201": 5,
    "C202": 3,
    "C203": 6,
    "C204": 4,
}
EXPECTED_GRADUATION_SLOT = 6