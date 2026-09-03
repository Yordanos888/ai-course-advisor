from data_real_stage8a import load_real_courses, DEFERRED_TO_STAGE_8B
from model_stage8_fixed import (
    build_and_solve, filter_courses_for_student, compute_normal_caps,
    cap_for_slot, slot_to_year_sem, natural_slot_for_course,
)


def structural_checks():
    print("=== STRUCTURAL VALIDATION OF PARSED REAL DATA ===")
    ok = True
    courses, skipped = load_real_courses()
    print(f"Parsed {len(courses)} courses (deferred to 8b: {skipped})")
    if set(skipped) != DEFERRED_TO_STAGE_8B:
        ok = False
        print(f"  XX skipped set mismatch")
    else:
        print("  OK exactly the 3 deferred rows were skipped")

    orphans = [(c, p) for c, info in courses.items() for p in info["prereqs"] if p not in courses]
    if orphans:
        ok = False
        print(f"  XX orphan prerequisite references: {orphans}")
    else:
        print("  OK no orphan prerequisite references")

    return ok, courses


def real_solve_test(courses):
    print("\n=== FULL REAL SOLVE (FIXED MODEL): fresh Control-stream student ===")
    ok = True

    filtered = filter_courses_for_student(courses, "Control")
    print(f"Filtered course count for Control stream: {len(filtered)}")

    horizon_slots = 15  # 5 years x 3 semester-types
    result = build_and_solve(courses=filtered, horizon_slots=horizon_slots)

    print("Feasible:", result["feasible"], "| Status:", result.get("status"))
    if not result["feasible"]:
        print("  XX expected feasible, got:", result)
        return False

    schedule = result["schedule"]
    print(f"Graduation slot: {result['graduation_slot']} {slot_to_year_sem(result['graduation_slot'])}")

    if set(schedule.keys()) != set(filtered.keys()):
        ok = False
        print("  XX schedule doesn't cover exactly the filtered course set")
    else:
        print("  OK every filtered course appears exactly once")

    # THE KEY NEW CHECK: non-droppable courses must be at or after their
    # natural (labeled) slot -- never pulled earlier, per the confirmed rule.
    non_droppable_ok = True
    for c, info in filtered.items():
        if not info.get("is_droppable", True):
            natural = natural_slot_for_course(info, semester_types=(1, 2, 3))
            actual = schedule[c]
            if actual < natural:
                non_droppable_ok = False
                ok = False
                print(f"  XX NON-DROPPABLE FLOOR VIOLATED: {c} scheduled at slot {actual}, "
                      f"natural/labeled slot is {natural} -- pulled EARLIER than allowed")
            else:
                print(f"  OK {c} (non-droppable): natural slot {natural}, actual slot {actual} "
                      f"({'exactly on time' if actual == natural else 'delayed, which is allowed'})")
    if non_droppable_ok:
        print("  OK all non-droppable courses respect their hard floor")

    # independent prereq re-check
    prereq_ok = True
    for c, info in filtered.items():
        for p in info["prereqs"]:
            if p in schedule and not (schedule[c] > schedule[p]):
                prereq_ok = False
                ok = False
                print(f"  XX PREREQ VIOLATED: {c} not after {p}")
    print("  Prerequisite ordering: checked across all courses, no violations." if prereq_ok else "  Prereq check FAILED above.")

    # independent credit-cap re-check
    normal_caps = compute_normal_caps(filtered)
    slot_totals = {}
    for c, s in schedule.items():
        slot_totals[s] = slot_totals.get(s, 0) + filtered[c]["credit_hours"]
    cap_ok = True
    for s, total in slot_totals.items():
        cap = cap_for_slot(s, normal_caps)
        if total > cap:
            cap_ok = False
            ok = False
            print(f"  XX CREDIT CAP VIOLATED: slot {s} has {total} credits, cap is {cap}")
    print("  Credit caps: checked, no violations." if cap_ok else "  Cap check FAILED above.")

    # parity re-check
    parity_ok = True
    for c, s in schedule.items():
        _, sem = slot_to_year_sem(s)
        allowed = {filtered[c]["semester_offered"]}
        if filtered[c].get("alt_parity"):
            allowed.add(filtered[c]["alt_parity"])
        if sem not in allowed:
            parity_ok = False
            ok = False
            print(f"  XX PARITY VIOLATED: {c} in slot {s}")
    print("  Semester parity: checked, no violations." if parity_ok else "  Parity check FAILED above.")

    if result["graduation_slot"] > horizon_slots:
        ok = False
        print(f"  XX graduation exceeds 5-year horizon")
    else:
        print(f"  OK graduation ({result['graduation_slot']}) is within the 5-year horizon")

    return ok


def main():
    struct_ok, courses = structural_checks()
    solve_ok = real_solve_test(courses)
    print("\n=== FINAL:", "PASS" if (struct_ok and solve_ok) else "FAIL", "===")


if __name__ == "__main__":
    main()