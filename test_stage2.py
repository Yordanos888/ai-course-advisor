from fake_data_stage1 import COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP
from fake_data_stage2 import (
    NOW_SLOT, STUDENT_STAGE2, EXPECTED_SCHEDULE_STAGE2, EXPECTED_GRADUATION_SLOT
)
from model_stage2 import build_and_solve, slot_to_year_sem, cap_for_slot


def print_schedule(schedule):
    by_slot = {}
    for course, slot in schedule.items():
        by_slot.setdefault(slot, []).append(course)
    for slot in sorted(by_slot):
        year, sem = slot_to_year_sem(slot)
        tag = " (PAST)" if slot <= NOW_SLOT else ""
        print(f"  Slot {slot} (Year {year}, Sem {sem}){tag}: {sorted(by_slot[slot])}")


def main():
    result = build_and_solve(
        courses=COURSES,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        completed_courses=STUDENT_STAGE2["completed_courses"],
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
    if result["schedule"] != EXPECTED_SCHEDULE_STAGE2:
        ok = False
        print("MISMATCH:")
        for c in COURSES:
            got = result["schedule"].get(c)
            expected = EXPECTED_SCHEDULE_STAGE2.get(c)
            marker = "OK " if got == expected else "XX "
            print(f"  {marker}{c}: solver={got}  expected={expected}")
    else:
        print("Schedule matches hand-computed expected answer exactly.")

    if result["graduation_slot"] != EXPECTED_GRADUATION_SLOT:
        ok = False
        print(f"MISMATCH graduation slot: solver={result['graduation_slot']} expected={EXPECTED_GRADUATION_SLOT}")
    else:
        print(f"Graduation slot matches expected ({EXPECTED_GRADUATION_SLOT}).")

    print("\n=== INDEPENDENT CONSTRAINT RE-CHECK ===")
    sched = result["schedule"]
    completed = STUDENT_STAGE2["completed_courses"]

    # 1) historical PASS courses must stay exactly where they actually happened
    pass_ok = True
    for c, rec in completed.items():
        if rec["status"] == "PASS" and sched[c] != rec["slot"]:
            pass_ok = False
            ok = False
            print(f"  XX HISTORY VIOLATED: {c} was PASS at slot {rec['slot']} but schedule shows {sched[c]}")
    print("  Historical PASS slots preserved: checked, no violations." if pass_ok else "  History check FAILED above.")

    # 2) nothing scheduled in or before NOW_SLOT except historical facts
    past_ok = True
    for c, s in sched.items():
        rec = completed.get(c)
        is_historical = rec is not None and rec["status"] == "PASS"
        if s <= NOW_SLOT and not is_historical:
            past_ok = False
            ok = False
            print(f"  XX PAST VIOLATED: {c} scheduled at slot {s} (<= NOW_SLOT={NOW_SLOT}) but has no PASS history")
    print("  Nothing new scheduled in the past: checked, no violations." if past_ok else "  Past-boundary check FAILED above.")

    # 3) the failed course got a genuine retake (different slot from its failed attempt)
    retake_ok = True
    for c, rec in completed.items():
        if rec["status"] == "FAILED" and sched[c] == rec["slot"]:
            retake_ok = False
            ok = False
            print(f"  XX RETAKE MISSING: {c} failed at slot {rec['slot']} but schedule still shows the same slot")
    print("  Failed courses got a genuine future retake: checked, no violations." if retake_ok else "  Retake check FAILED above.")

    # 4) prereqs
    prereq_ok = True
    for c, info in COURSES.items():
        for p in info["prereqs"]:
            if not (sched[c] > sched[p]):
                prereq_ok = False
                ok = False
                print(f"  XX PREREQ VIOLATED: {c} (slot {sched[c]}) not after {p} (slot {sched[p]})")
    print("  Prerequisite ordering: checked, no violations." if prereq_ok else "  Prerequisite check FAILED above.")

    # 5) credit caps (future slots only)
    slot_totals = {}
    for c, s in sched.items():
        if s > NOW_SLOT:
            slot_totals[s] = slot_totals.get(s, 0) + COURSES[c]["credit_hours"]
    cap_ok = True
    for s, total in slot_totals.items():
        cap = cap_for_slot(s, NORMAL_SEMESTER_CREDIT_CAP)
        if total > cap:
            cap_ok = False
            ok = False
            print(f"  XX CREDIT CAP VIOLATED: slot {s} has {total} credits, cap is {cap}")
    print("  Credit caps (future slots): checked, no violations." if cap_ok else "  Credit cap check FAILED above.")

    # 6) parity
    parity_ok = True
    for c, s in sched.items():
        _, sem = slot_to_year_sem(s)
        if sem != COURSES[c]["semester_offered"]:
            parity_ok = False
            ok = False
            print(f"  XX PARITY VIOLATED: {c} offered in sem {COURSES[c]['semester_offered']} but placed in slot {s}")
    print("  Semester parity: checked, no violations." if parity_ok else "  Parity check FAILED above.")

    print("\n=== FINAL:", "PASS" if ok else "FAIL", "===")


if __name__ == "__main__":
    main()