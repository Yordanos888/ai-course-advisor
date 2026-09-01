from fake_data_stage4b import (
    COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP, MAX_ATTEMPTS, NOW_SLOT,
    SCENARIO_1_NON_DROPPABLE, EXPECTED_1,
    SCENARIO_2_KEEP_AT_LEAST_ONE, EXPECTED_2,
    SCENARIO_3_LEGAL_DROP, EXPECTED_3,
    SCENARIO_4_NO_CREDIT_GAP, EXPECTED_4,
    SCENARIO_5_COMBINED_VIOLATIONS, EXPECTED_5,
)
from model_stage4 import check_drop_legality


def run_illegal_case(label, scenario, expected):
    print(f"\n--- {label} ---")
    result = check_drop_legality(
        drop_course=scenario["drop_course"],
        courses=COURSES,
        current_semester_courses=scenario["current_semester_courses"],
        completed_courses={},
        now_slot=NOW_SLOT,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        max_attempts=MAX_ATTEMPTS,
    )
    print("Result:", result)

    ok = True
    if result["legal"] != expected["legal"]:
        ok = False
        print(f"  XX legality mismatch: got {result['legal']}, expected {expected['legal']}")
    else:
        print(f"  OK legality matches expected ({expected['legal']})")

    reason_text = " | ".join(result.get("reasons", []))
    if expected["reason_contains"] not in reason_text:
        ok = False
        print(f"  XX expected reason containing '{expected['reason_contains']}', got: {reason_text}")
    else:
        print(f"  OK reason correctly mentions '{expected['reason_contains']}'")

    return ok


def run_legal_case(label, scenario, expected):
    print(f"\n--- {label} ---")
    result = check_drop_legality(
        drop_course=scenario["drop_course"],
        courses=scenario.get("courses", COURSES),
        current_semester_courses=scenario["current_semester_courses"],
        completed_courses={},
        now_slot=NOW_SLOT,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        max_attempts=MAX_ATTEMPTS,
    )
    print("Result:", result)

    ok = True
    if result["legal"] != expected["legal"]:
        ok = False
        print(f"  XX legality mismatch: got {result['legal']}, expected {expected['legal']}")
    else:
        print(f"  OK legality matches expected ({expected['legal']})")

    plan = result.get("resulting_plan", {})
    if not plan.get("feasible"):
        ok = False
        print(f"  XX expected a feasible resulting plan, got: {plan}")
    else:
        print("  OK resulting plan is feasible")

    if plan.get("schedule") != expected["resulting_schedule"]:
        ok = False
        print(f"  XX resulting schedule mismatch: got {plan.get('schedule')}, expected {expected['resulting_schedule']}")
    else:
        print("  OK resulting schedule matches expected exactly")

    if plan.get("graduation_slot") != expected["resulting_graduation_slot"]:
        ok = False
        print(f"  XX resulting graduation slot mismatch: got {plan.get('graduation_slot')}, expected {expected['resulting_graduation_slot']}")
    else:
        print("  OK resulting graduation slot matches expected")

    return ok


def run_combined_case(label, scenario, expected):
    print(f"\n--- {label} ---")
    result = check_drop_legality(
        drop_course=scenario["drop_course"],
        courses=COURSES,
        current_semester_courses=scenario["current_semester_courses"],
        completed_courses={},
        now_slot=NOW_SLOT,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        max_attempts=MAX_ATTEMPTS,
    )
    print("Result:", result)

    ok = True
    if result["legal"] != expected["legal"]:
        ok = False
        print(f"  XX legality mismatch: got {result['legal']}, expected {expected['legal']}")
    else:
        print(f"  OK legality matches expected ({expected['legal']})")

    if len(result.get("reasons", [])) != expected["reason_count"]:
        ok = False
        print(f"  XX expected {expected['reason_count']} accumulated reasons, "
              f"got {len(result.get('reasons', []))}: {result.get('reasons')}")
    else:
        print(f"  OK got exactly {expected['reason_count']} reasons "
              "(proves violations accumulate, not short-circuit)")

    return ok


def main():
    results = []
    results.append(run_illegal_case(
        "Scenario 1: attempt to drop a NON-DROPPABLE course",
        SCENARIO_1_NON_DROPPABLE, EXPECTED_1,
    ))
    results.append(run_illegal_case(
        "Scenario 2: attempt to drop the ONLY course this semester",
        SCENARIO_2_KEEP_AT_LEAST_ONE, EXPECTED_2,
    ))
    results.append(run_legal_case(
        "Scenario 3: LEGAL drop, full round trip",
        SCENARIO_3_LEGAL_DROP, EXPECTED_3,
    ))
    results.append(run_illegal_case(
        "Scenario 4: no future credit gap exists",
        SCENARIO_4_NO_CREDIT_GAP, EXPECTED_4,
    ))
    results.append(run_combined_case(
        "Scenario 5: combined violations accumulate, don't short-circuit",
        SCENARIO_5_COMBINED_VIOLATIONS, EXPECTED_5,
    ))

    print("\n=== FINAL:", "PASS" if all(results) else "FAIL", "===")


if __name__ == "__main__":
    main()