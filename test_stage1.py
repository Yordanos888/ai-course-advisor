from fake_data_stage1 import (
    COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP,
    STUDENT_STAGE1, EXPECTED_SCHEDULE_STAGE1, EXPECTED_GRADUATION_SLOT
)
from model_stage1 import build_and_solve, slot_to_year_sem


def print_schedule(schedule):
    by_slot = {}
    for course, slot in schedule.items():
        by_slot.setdefault(slot, []).append(course)
    for slot in sorted(by_slot):
        year, sem = slot_to_year_sem(slot)
        courses = sorted(by_slot[slot])
        print(f"  Slot {slot} (Year {year}, Sem {sem}): {courses}")


def main():
    result = build_and_solve(
        courses=COURSES,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        completed_courses=STUDENT_STAGE1["completed_courses"],
    )

    print("=== SOLVER RESULT ===")
    print("Feasible:", result["feasible"], "| Status:", result["status"])

    if not result["feasible"]:
        print("FAILED: expected a feasible schedule, solver found none.")
        return

    print_schedule(result["schedule"])
    print(f"Graduation slot: {result['graduation_slot']}")

    print("\n=== VERIFICATION AGAINST HAND-COMPUTED EXPECTED ANSWER ===")
    ok = True

    if result["schedule"] != EXPECTED_SCHEDULE_STAGE1:
        ok = False
        print("MISMATCH in schedule:")
        for c in COURSES:
            got = result["schedule"].get(c)
            expected = EXPECTED_SCHEDULE_STAGE1.get(c)
            marker = "OK " if got == expected else "XX "
            print(f"  {marker}{c}: solver={got}  expected={expected}")
    else:
        print("Schedule matches hand-computed expected answer exactly.")

    if result["graduation_slot"] != EXPECTED_GRADUATION_SLOT:
        ok = False
        print(f"MISMATCH in graduation slot: solver={result['graduation_slot']} "
              f"expected={EXPECTED_GRADUATION_SLOT}")
    else:
        print(f"Graduation slot matches expected ({EXPECTED_GRADUATION_SLOT}).")

    # Independently re-verify constraints hold (don't just trust the solver's
    # own claim of feasibility -- check the actual rules against the output).
    print("\n=== INDEPENDENT CONSTRAINT RE-CHECK (not trusting the solver blindly) ===")
    sched = result["schedule"]

    # prereqs
    prereq_ok = True
    for c, info in COURSES.items():
        for p in info["prereqs"]:
            if not (sched[c] > sched[p]):
                prereq_ok = False
                ok = False
                print(f"  XX PREREQ VIOLATED: {c} (slot {sched[c]}) not after {p} (slot {sched[p]})")
    print("  Prerequisite ordering: checked, no violations found." if prereq_ok else "  Prerequisite check FAILED above.")

    # credit caps
    from model_stage1 import cap_for_slot
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
    if cap_ok:
        print("  Credit caps: checked, no violations found.")

    # parity
    parity_ok = True
    for c, s in sched.items():
        _, sem = slot_to_year_sem(s)
        if sem != COURSES[c]["semester_offered"]:
            parity_ok = False
            ok = False
            print(f"  XX PARITY VIOLATED: {c} offered in sem {COURSES[c]['semester_offered']} but placed in slot {s} (sem {sem})")
    if parity_ok:
        print("  Semester parity: checked, no violations found.")

    print("\n=== FINAL: ", "PASS" if ok else "FAIL", "===")


if __name__ == "__main__":
    main()