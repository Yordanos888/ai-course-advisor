from fake_data_stage7 import (
    COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP,
    EXPECTED_GRADUATION_SLOT,
    EXPECTED_BEST_EVENNESS_SCHEDULE, EXPECTED_BEST_EVENNESS_VARIANCE,
    OTHER_VALID_SCHEDULE_SHAPES, OTHER_SHAPES_VARIANCE,
)
from model_stage7 import find_ranked_plans


def main():
    result = find_ranked_plans(
        courses=COURSES,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        max_solutions=5,
    )

    print("=== SOLVER RESULT ===")
    print("Feasible:", result["feasible"])
    if not result["feasible"]:
        print("FAILED: expected feasible plans.")
        return

    print(f"Best graduation slot: {result['best_graduation_slot']}")
    print(f"Number of distinct plans found: {len(result['plans'])}")
    for i, p in enumerate(result["plans"]):
        print(f"  Plan {i+1}: schedule={p['schedule']}  "
              f"grad_slot={p['graduation_slot']}  evenness_variance={p['load_evenness_variance']}")

    ok = True

    print("\n=== VERIFICATION ===")

    # 1) graduation slot matches the forced expected value
    if result["best_graduation_slot"] != EXPECTED_GRADUATION_SLOT:
        ok = False
        print(f"  XX best graduation slot: got {result['best_graduation_slot']}, expected {EXPECTED_GRADUATION_SLOT}")
    else:
        print(f"  OK best graduation slot matches expected ({EXPECTED_GRADUATION_SLOT})")

    # 2) at least 2 distinct plans were found (proves enumeration is doing
    #    something beyond returning the single tie-break favorite)
    if len(result["plans"]) < 2:
        ok = False
        print(f"  XX only {len(result['plans'])} plan(s) found -- enumeration found no alternatives")
    else:
        print(f"  OK {len(result['plans'])} distinct plans found (more than just one)")

    # 3) the most-even plan MUST be present, and MUST be ranked #1
    schedules_found = [p["schedule"] for p in result["plans"]]
    if EXPECTED_BEST_EVENNESS_SCHEDULE not in schedules_found:
        ok = False
        print(f"  XX the most-even plan {EXPECTED_BEST_EVENNESS_SCHEDULE} was never found at all")
    else:
        print(f"  OK the most-even plan {EXPECTED_BEST_EVENNESS_SCHEDULE} was found")

    if result["plans"][0]["schedule"] != EXPECTED_BEST_EVENNESS_SCHEDULE:
        ok = False
        print(f"  XX plan ranked #1 is {result['plans'][0]['schedule']}, "
              f"expected the most-even plan {EXPECTED_BEST_EVENNESS_SCHEDULE} to rank #1")
    else:
        print(f"  OK the most-even plan is correctly ranked #1")

    if result["plans"][0]["load_evenness_variance"] != EXPECTED_BEST_EVENNESS_VARIANCE:
        ok = False
        print(f"  XX top plan's variance: got {result['plans'][0]['load_evenness_variance']}, "
              f"expected {EXPECTED_BEST_EVENNESS_VARIANCE}")
    else:
        print(f"  OK top plan's variance matches expected ({EXPECTED_BEST_EVENNESS_VARIANCE}, perfectly even)")

    # 4) at least one of the less-even (9,3)-shaped plans should also be
    #    discoverable, proving we're not just finding ONE alternative but
    #    genuinely exploring the tie space
    found_other_shape = any(s in schedules_found for s in OTHER_VALID_SCHEDULE_SHAPES)
    if not found_other_shape:
        ok = False
        print(f"  XX neither of the other valid shapes {OTHER_VALID_SCHEDULE_SHAPES} was found")
    else:
        print(f"  OK at least one of the less-even alternative shapes was also found")

    # 5) independent re-check: every returned plan must actually be a
    # legal schedule (cap + parity), not just internally self-consistent
    print("\n=== INDEPENDENT CONSTRAINT RE-CHECK (every returned plan) ===")
    from model_stage2 import slot_to_year_sem, cap_for_slot as cap_lookup
    all_plans_legal = True
    for i, p in enumerate(result["plans"]):
        sched = p["schedule"]
        slot_totals = {}
        for c, s in sched.items():
            slot_totals[s] = slot_totals.get(s, 0) + COURSES[c]["credit_hours"]
        for s, total in slot_totals.items():
            cap = cap_lookup(s, NORMAL_SEMESTER_CREDIT_CAP)
            if total > cap:
                all_plans_legal = False
                ok = False
                print(f"  XX Plan {i+1}: slot {s} has {total} credits, cap is {cap}")
        for c, s in sched.items():
            _, sem = slot_to_year_sem(s)
            if sem != COURSES[c]["semester_offered"]:
                all_plans_legal = False
                ok = False
                print(f"  XX Plan {i+1}: {c} parity violated at slot {s}")
    print("  All returned plans respect caps and parity." if all_plans_legal else "  Some plans violated rules above.")

    print("\n=== FINAL:", "PASS" if ok else "FAIL", "===")


if __name__ == "__main__":
    main()