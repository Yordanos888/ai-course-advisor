from fake_data_stage6 import (
    COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP,
    EXPECTED_SCHEDULE, EXPECTED_GRADUATION_SLOT,
)
from model_stage6 import build_and_solve, slot_to_year_sem, cap_for_slot


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
    )

    print("=== SOLVER RESULT ===")
    print("Feasible:", result["feasible"], "| Status:", result["status"])
    if not result["feasible"]:
        print("FAILED: expected a feasible schedule, solver found none.")
        print(result)
        return

    print_schedule(result["schedule"])
    print(f"Graduation slot: {result['graduation_slot']}")

    ok = True

    print("\n=== VERIFICATION AGAINST HAND-COMPUTED EXPECTED ANSWER ===")
    if result["schedule"] != EXPECTED_SCHEDULE:
        ok = False
        print("MISMATCH:")
        for c in COURSES:
            got = result["schedule"].get(c)
            expected = EXPECTED_SCHEDULE.get(c)
            marker = "OK " if got == expected else "XX "
            print(f"  {marker}{c}: solver={got}  expected={expected}")
            if c == "FYP2" and got != expected and got == 2:
                print("       (this is the 'forgot ALL_STREAM_COURSES constraint' failure mode)")
    else:
        print("Schedule matches hand-computed expected answer exactly.")

    if result["graduation_slot"] != EXPECTED_GRADUATION_SLOT:
        ok = False
        print(f"MISMATCH graduation slot: solver={result['graduation_slot']} expected={EXPECTED_GRADUATION_SLOT}")
    else:
        print(f"Graduation slot matches expected ({EXPECTED_GRADUATION_SLOT}).")

    print("\n=== INDEPENDENT CONSTRAINT RE-CHECK ===")
    sched = result["schedule"]

    # 1) ordinary prereqs
    prereq_ok = True
    for c, info in COURSES.items():
        for p in info["prereqs"]:
            if not (sched[c] > sched[p]):
                prereq_ok = False
                ok = False
                print(f"  XX PREREQ VIOLATED: {c} (slot {sched[c]}) not after {p} (slot {sched[p]})")
    print("  Prerequisite ordering: checked, no violations." if prereq_ok else "  Prerequisite check FAILED above.")

    # 2) FYP-II must be >= every other course EXCEPT the post-hoc NEE
    #    (NEE is scoped over ALL_COURSES including FYP2 itself -- it's not
    #    part of FYP2's own ALL_STREAM_COURSES requirement)
    fyp_ok = True
    stream_peers = [c for c, info in COURSES.items()
                     if info.get("special_requirement") != "ALL_COURSES" and c != "FYP2"]
    for c2 in stream_peers:
        if not (sched["FYP2"] >= sched[c2]):
            fyp_ok = False
            ok = False
            print(f"  XX FYP2 REQUIREMENT VIOLATED: FYP2 (slot {sched['FYP2']}) is before {c2} (slot {sched[c2]})")
    print("  FYP2 >= every ALL_STREAM_COURSES peer: checked, no violations." if fyp_ok else "  FYP2 requirement check FAILED above.")

    # 3) NEE must be >= the true max of everything else, and match its own parity
    nee_ok = True
    other_max = max(v for c, v in sched.items() if c != "NEE_EXAM")
    if sched["NEE_EXAM"] < other_max:
        nee_ok = False
        ok = False
        print(f"  XX NEE VIOLATED: NEE_EXAM (slot {sched['NEE_EXAM']}) is before the true max of everything else ({other_max})")
    _, nee_sem = slot_to_year_sem(sched["NEE_EXAM"])
    if nee_sem != COURSES["NEE_EXAM"]["semester_offered"]:
        nee_ok = False
        ok = False
        print(f"  XX NEE PARITY VIOLATED: NEE_EXAM placed in sem {nee_sem}, requires sem {COURSES['NEE_EXAM']['semester_offered']}")
    print("  NEE_EXAM >= true max and correct parity: checked, no violations." if nee_ok else "  NEE check FAILED above.")

    # 4) credit caps
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

    print("\n=== FINAL:", "PASS" if ok else "FAIL", "===")


if __name__ == "__main__":
    main()