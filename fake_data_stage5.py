"""
STAGE 5 FAKE DATA
==================
Three independent scenarios, kept separate so a failure in one can't be
confused with a failure in another.

------------------------------------------------------------------
SCENARIO A: parity-flip actually gets exploited, not just accepted
------------------------------------------------------------------
RIGID1 and XDEPT1 both want the same tight slot 1 (parity 1, cap 4,
each course is 4 credits -- only one fits).

RIGID1 has no alternative -- it can ONLY be taken at parity 1.
XDEPT1 is a cross-department common course: its home parity is 1, but
the partner department offers the equivalent course at parity 2, so
campus rules allow it to be taken at EITHER parity.

If the parity-flip is correctly modeled and exploited:
    RIGID1 keeps slot 1 (it has no other option).
    XDEPT1 uses its alt-parity escape hatch -> slot 2 (parity 2, cap 4,
    fits exactly). Graduation slot = 2.

If the parity-flip were forgotten (a realistic bug -- e.g. someone reads
XDEPT1's `semester_offered` field and stops there without checking
`alt_parity`), XDEPT1 would be forced to wait for the next parity-1 slot
instead -> slot 3. Graduation slot = 3, which is still a VALID schedule
(nothing violates any hard rule), just a worse one. That's exactly the
kind of bug that's easy to miss because the output still "looks right."
This test exists specifically to catch that.

------------------------------------------------------------------
SCENARIO B: stream filtering
------------------------------------------------------------------
COMMON1 belongs to no stream (everyone takes it). SA1 is Stream-A-only.
SB1 is Stream-B-only. A Stream-A student's schedulable set should be
{COMMON1, SA1} -- SB1 must not even appear as something to plan for.

------------------------------------------------------------------
SCENARIO C: stream choice for an undecided student
------------------------------------------------------------------
CORE1 is common to both streams. Stream A's remaining path is a short
2-course chain; Stream B's is a longer 3-course chain. An undecided
student should get BOTH streams solved and compared, not have one
silently picked for them.

  Stream A: CORE1@1, A1@1 (shares slot 1, cap allows 3+3=6<=9), A2@2
            -> graduation slot 2
  Stream B: CORE1@1, B1@1 (shares slot 1), B2@2, B3@3 (parity1, after
            B2's slot 2, earliest odd slot > 2)
            -> graduation slot 3
"""

# ---------------- Scenario A: parity flip ----------------
COURSES_PARITY_FLIP = {
    "RIGID1": {"credit_hours": 4, "semester_offered": 1, "year_level": 1, "prereqs": []},
    "XDEPT1": {"credit_hours": 4, "semester_offered": 1, "year_level": 1, "alt_parity": 2, "prereqs": []},
}
HORIZON_PARITY_FLIP = 6
CAPS_PARITY_FLIP = {
    (1, 1): 4,   # tight -- forces the trade-off
    (1, 2): 4,   # just enough room for XDEPT1's flipped attempt
    (2, 1): 21,
    (2, 2): 21,
    (3, 1): 21,
    (3, 2): 21,
}
EXPECTED_SCHEDULE_A = {"RIGID1": 1, "XDEPT1": 2}
EXPECTED_GRAD_A = 2

# ---------------- Scenario B: stream filtering ----------------
COURSES_STREAM_FILTER = {
    "COMMON1": {"credit_hours": 3, "semester_offered": 1, "stream": None, "prereqs": []},
    "SA1": {"credit_hours": 3, "semester_offered": 1, "stream": "A", "prereqs": []},
    "SB1": {"credit_hours": 3, "semester_offered": 1, "stream": "B", "prereqs": []},
}
EXPECTED_FILTERED_A = {"COMMON1", "SA1"}
EXPECTED_FILTERED_B = {"COMMON1", "SB1"}

# ---------------- Scenario C: stream choice comparison ----------------
# Year-levels here are REALISTIC (>= 4 for stream-specific courses), not
# arbitrary -- the model now hard-floors any stream-specific course at
# Year 4 Sem 2 (stream enrollment can't happen earlier in reality), so a
# fake scenario using small year-levels for stream courses would be
# testing something that can no longer legally occur. The chain-length
# asymmetry this test exists to prove (Stream A finishes faster than
# Stream B) is preserved, just shifted to realistic timing.
COURSES_STREAM_CHOICE = {
    "CORE1": {"credit_hours": 3, "semester_offered": 1, "year_level": 1, "stream": None, "prereqs": []},

    "A1": {"credit_hours": 3, "semester_offered": 2, "year_level": 4, "stream": "A", "prereqs": []},
    "A2": {"credit_hours": 3, "semester_offered": 1, "year_level": 5, "stream": "A", "prereqs": ["A1"]},

    "B1": {"credit_hours": 3, "semester_offered": 2, "year_level": 4, "stream": "B", "prereqs": []},
    "B2": {"credit_hours": 3, "semester_offered": 1, "year_level": 5, "stream": "B", "prereqs": ["B1"]},
    "B3": {"credit_hours": 3, "semester_offered": 2, "year_level": 5, "stream": "B", "prereqs": ["B2"]},
}
HORIZON_STREAM_CHOICE = 12
CAPS_STREAM_CHOICE = {
    (y, s): 9 for y in range(1, 6) for s in (1, 2)
}
EXPECTED_GRAD_STREAM_A = 9   # Year 5, Sem 1
EXPECTED_GRAD_STREAM_B = 10  # Year 5, Sem 2
