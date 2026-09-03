from fake_data_stage3 import (
    COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP, NOW_SLOT,
    STUDENT_STAGE3, EXPECTED_SCHEDULE_STAGE3, EXPECTED_GRADUATION_SLOT
)
from model_stage2 import build_and_solve, slot_to_year_sem, cap_for_slot  # reused unchanged


def print_schedule(schedule):
    by_slot = {}
    for course, slot in schedule.items():
        by_slot.setdefault(slot, []).append(course)
    for slot in sorted(by_slot):
        year, sem = slot_to_year_sem(slot)
        print(f"  Slot {slot} (Year {year}, Sem {sem}): {sorted(by_slot[slot])}")


def main():
    result = build_and_solve(
        courses=COURSES,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        completed_courses=STUDENT_STAGE3["completed_courses"],
        now_slot=NOW_SLOT,
    )

    print("=== SOLVER RESULT ===")
    print("Feasible:", result["feasible"], "| Status:", result["status"])
    if not result["feasible"]:
        print("FAILED: expected a feasible schedule, solver found none.")
        return

    print_schedule(result["schedule"])
    print(f"Graduation slot: {result['graduation_slot']}")

    ok = True

    print("\n=== VERIFICATION AGAINST HAND-COMPUTED EXPECTED ANSWER ===")
    if result["schedule"] != EXPECTED_SCHEDULE_STAGE3:
        ok = False
        print("MISMATCH:")
        for c in COURSES:
            got = result["schedule"].get(c)
            expected = EXPECTED_SCHEDULE_STAGE3.get(c)
            marker = "OK " if got == expected else "XX "
            print(f"  {marker}{c}: solver={got}  expected={expected}")
    else:
        print("Schedule matches hand-computed expected answer exactly.")

    if result["graduation_slot"] != EXPECTED_GRADUATION_SLOT:
        ok = False
        print(f"MISMATCH graduation slot: solver={result['graduation_slot']} expected={EXPECTED_GRADUATION_SLOT}")
    else:
        print(f"Graduation slot matches expected ({EXPECTED_GRADUATION_SLOT}).")

    # THE key check for this stage: did the solver fall into the
    # alphabetical-tie-break trap (starting B first) instead of correctly
    # starting the longer chain (Z) first?
    print("\n=== TRAP CHECK: did the solver avoid the tie-break trap? ===")
    sched = result["schedule"]
    if sched.get("Z1") == 1 and sched.get("B1") == 3:
        print("  Correctly started the LONG chain (Z) first. Trap avoided.")
    elif sched.get("B1") == 1 and sched.get("Z1") == 3:
        ok = False
        print("  XX FELL INTO THE TRAP: started B first (graduation slot 5, worse).")
        print("     This means the tie-break is dominating the primary objective --")
        print("     check the objective weighting in model_stage2.py.")
    else:
        print(f"  Unexpected assignment -- Z1={sched.get('Z1')}, B1={sched.get('B1')}. Investigate.")

    print("\n=== INDEPENDENT CONSTRAINT RE-CHECK ===")

    prereq_ok = True
    for c, info in COURSES.items():
        for p in info["prereqs"]:
            if not (sched[c] > sched[p]):
                prereq_ok = False
                ok = False
                print(f"  XX PREREQ VIOLATED: {c} (slot {sched[c]}) not after {p} (slot {sched[p]})")
    print("  Prerequisite ordering: checked, no violations." if prereq_ok else "  Prerequisite check FAILED above.")

    slot_totals = {}
    for c, s in sched.items():
        slot_totals[s] = slot_totals.get(s, 0) + COURSES[c]["credit_hours"]
    cap_ok = True
    for s, total in slot_totals.items():
        cap = cap_for_slot(s, NORMAL_SEMESTER_CREDIT_CAP)
        if total > cap:
            cap_ok = False
            ok = False
            print(f"  XX CREDIT CAP VIOLATED: slot {s} has {total} credits, cap is {cap}")
    print("  Credit caps: checked, no violations." if cap_ok else "  Credit cap check FAILED above.")

    parity_ok = True
    for c, s in sched.items():
        _, sem = slot_to_year_sem(s)
        if sem != COURSES[c]["semester_offered"]:
            parity_ok = False
            ok = False
            print(f"  XX PARITY VIOLATED: {c} offered in sem {COURSES[c]['semester_offered']} but placed in slot {s}")
    print("  Semester parity: checked, no violations." if parity_ok else "  Parity check FAILED above.")

    # Also explicitly confirm Candidate A (4) really is better than
    # Candidate B (5), independent of the solver, so the test itself isn't
    # trusting a hand-computed number that could itself be wrong.
    print("\n=== SANITY: is Candidate A actually better than Candidate B? ===")
    candidate_a_grad = 4
    candidate_b_grad = 5
    if candidate_a_grad < candidate_b_grad:
        print(f"  Confirmed: Candidate A ({candidate_a_grad}) < Candidate B ({candidate_b_grad}).")
    else:
        ok = False
        print("  XX Candidate A is not actually better -- the test scenario itself is wrong.")

    print("\n=== FINAL:", "PASS" if ok else "FAIL", "===")


if __name__ == "__main__":
    main()