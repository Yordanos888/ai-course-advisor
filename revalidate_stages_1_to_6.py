"""
RE-VALIDATION OF STAGES 1-7 AGAINST THE FIXED (STAGE 8a) MODEL
=================================================================
Runs every prior stage's EXACT fake data and EXACT hand-computed
expected answers through model_stage8_fixed.build_and_solve, with
semester_types=(1,2) so slot numbering matches the original 2-type
system those answers were computed against.

This is a real regression check, not an assertion that "it should still
work" -- if the new deviation-based objective changes behavior on any
of these carefully-designed test cases, this will catch it.
"""

from model_stage8_fixed import build_and_solve, natural_slot_for_course

ok = True


def check(label, condition, detail=""):
    global ok
    marker = "OK " if condition else "XX "
    print(f"  {marker}{label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        ok = False


# ---------------- Stage 1: fresh, no failures ----------------
print("=== Re-validating Stage 1 (fresh student) ===")
from fake_data_stage1 import COURSES as C1, HORIZON_SLOTS as H1, \
    NORMAL_SEMESTER_CREDIT_CAP as CAP1, EXPECTED_SCHEDULE_STAGE1, EXPECTED_GRADUATION_SLOT as GRAD1

r1 = build_and_solve(courses=C1, horizon_slots=H1, normal_caps_override=CAP1, semester_types=(1, 2))
check("Stage 1 feasible", r1["feasible"])
check("Stage 1 schedule matches original expected answer", r1.get("schedule") == EXPECTED_SCHEDULE_STAGE1,
      f"got {r1.get('schedule')}")
check("Stage 1 graduation slot matches", r1.get("graduation_slot") == GRAD1)

# ---------------- Stage 2: retake + domino effect ----------------
print("\n=== Re-validating Stage 2 (retake + domino effect) ===")
from fake_data_stage2 import NOW_SLOT as NOW2, STUDENT_STAGE2, EXPECTED_SCHEDULE_STAGE2, EXPECTED_GRADUATION_SLOT as GRAD2

r2 = build_and_solve(courses=C1, horizon_slots=H1, normal_caps_override=CAP1, semester_types=(1, 2),
                      completed_courses={
                          c: [rec] for c, rec in STUDENT_STAGE2["completed_courses"].items()
                      }, now_slot=NOW2)
check("Stage 2 feasible", r2["feasible"])
check("Stage 2 schedule matches original expected answer", r2.get("schedule") == EXPECTED_SCHEDULE_STAGE2,
      f"got {r2.get('schedule')}")
check("Stage 2 graduation slot matches", r2.get("graduation_slot") == GRAD2)

# ---------------- Stage 3: tie-break trap ----------------
print("\n=== Re-validating Stage 3 (tie-break trap) ===")
from fake_data_stage3 import COURSES as C3, HORIZON_SLOTS as H3, NORMAL_SEMESTER_CREDIT_CAP as CAP3, \
    EXPECTED_SCHEDULE_STAGE3, EXPECTED_GRADUATION_SLOT as GRAD3

r3 = build_and_solve(courses=C3, horizon_slots=H3, normal_caps_override=CAP3, semester_types=(1, 2))
check("Stage 3 feasible", r3["feasible"])
check("Stage 3 schedule matches original expected answer (long chain still starts first)",
      r3.get("schedule") == EXPECTED_SCHEDULE_STAGE3, f"got {r3.get('schedule')}")
check("Stage 3 graduation slot matches", r3.get("graduation_slot") == GRAD3)

# ---------------- Stage 4a: retake limit ----------------
print("\n=== Re-validating Stage 4a (retake limit) ===")
from fake_data_stage4a import COURSES as C4a, HORIZON_SLOTS as H4a, NORMAL_SEMESTER_CREDIT_CAP as CAP4a, \
    MAX_ATTEMPTS as MAXATT4a, STUDENT_A_AT_LIMIT, EXPECTED_A, STUDENT_B_OVER_LIMIT, EXPECTED_B, \
    STUDENT_C_DROPPED_EXCLUDED, EXPECTED_C

for label, student, expected in [
    ("A (at limit)", STUDENT_A_AT_LIMIT, EXPECTED_A),
    ("B (over limit)", STUDENT_B_OVER_LIMIT, EXPECTED_B),
    ("C (dropped excluded)", STUDENT_C_DROPPED_EXCLUDED, EXPECTED_C),
]:
    r = build_and_solve(courses=C4a, horizon_slots=H4a, normal_caps_override=CAP4a,
                          semester_types=(1, 2), completed_courses=student["completed_courses"],
                          now_slot=student["now_slot"], max_attempts=MAXATT4a)
    check(f"Stage 4a Student {label} feasibility matches", r["feasible"] == expected["feasible"])
    if expected["feasible"]:
        check(f"Stage 4a Student {label} schedule matches", r.get("schedule") == expected["schedule"],
              f"got {r.get('schedule')}")

# ---------------- Stage 5: parity flip + stream choice ----------------
print("\n=== Re-validating Stage 5 (parity flip + stream choice) ===")
from fake_data_stage5 import COURSES_PARITY_FLIP, HORIZON_PARITY_FLIP, CAPS_PARITY_FLIP, \
    EXPECTED_SCHEDULE_A, EXPECTED_GRAD_A, COURSES_STREAM_CHOICE, HORIZON_STREAM_CHOICE, \
    CAPS_STREAM_CHOICE, EXPECTED_GRAD_STREAM_A, EXPECTED_GRAD_STREAM_B
from model_stage8_fixed import filter_courses_for_student as filt5

r5a = build_and_solve(courses=COURSES_PARITY_FLIP, horizon_slots=HORIZON_PARITY_FLIP,
                        normal_caps_override=CAPS_PARITY_FLIP, semester_types=(1, 2))
check("Stage 5 parity-flip schedule matches", r5a.get("schedule") == EXPECTED_SCHEDULE_A, f"got {r5a.get('schedule')}")
check("Stage 5 parity-flip graduation slot matches", r5a.get("graduation_slot") == EXPECTED_GRAD_A)

for stream, expected_grad in [("A", EXPECTED_GRAD_STREAM_A), ("B", EXPECTED_GRAD_STREAM_B)]:
    filtered = filt5(COURSES_STREAM_CHOICE, stream)
    r = build_and_solve(courses=filtered, horizon_slots=HORIZON_STREAM_CHOICE,
                          normal_caps_override=CAPS_STREAM_CHOICE, semester_types=(1, 2))
    check(f"Stage 5 stream {stream} graduation slot matches", r.get("graduation_slot") == expected_grad,
          f"got {r.get('graduation_slot')}, expected {expected_grad}")

# ---------------- Stage 6: FYP-II / NEE special requirements ----------------
print("\n=== Re-validating Stage 6 (special requirements) ===")
from fake_data_stage6 import COURSES as C6, HORIZON_SLOTS as H6, NORMAL_SEMESTER_CREDIT_CAP as CAP6, \
    EXPECTED_SCHEDULE as EXPECTED6, EXPECTED_GRADUATION_SLOT as GRAD6

r6 = build_and_solve(courses=C6, horizon_slots=H6, normal_caps_override=CAP6, semester_types=(1, 2))
check("Stage 6 feasible", r6["feasible"])
check("Stage 6 schedule matches original expected answer", r6.get("schedule") == EXPECTED6, f"got {r6.get('schedule')}")
check("Stage 6 graduation slot matches", r6.get("graduation_slot") == GRAD6)

print("\n=== FINAL RE-VALIDATION RESULT:", "ALL PASS -- NO REGRESSIONS" if ok else "REGRESSION DETECTED", "===")
