from fake_data_stage5 import (
    COURSES_PARITY_FLIP, HORIZON_PARITY_FLIP, CAPS_PARITY_FLIP,
    EXPECTED_SCHEDULE_A, EXPECTED_GRAD_A,
    COURSES_STREAM_FILTER, EXPECTED_FILTERED_A, EXPECTED_FILTERED_B,
    COURSES_STREAM_CHOICE, HORIZON_STREAM_CHOICE, CAPS_STREAM_CHOICE,
    EXPECTED_GRAD_STREAM_A, EXPECTED_GRAD_STREAM_B,
)
from model_stage5 import (
    build_and_solve, filter_courses_for_student, compare_streams,
    slot_to_year_sem, cap_for_slot,
)


def scenario_a_parity_flip():
    print("\n=== SCENARIO A: parity flip gets exploited ===")
    result = build_and_solve(
        courses=COURSES_PARITY_FLIP,
        horizon_slots=HORIZON_PARITY_FLIP,
        normal_caps=CAPS_PARITY_FLIP,
    )
    print("Result:", result)

    ok = True
    if not result["feasible"]:
        print("  XX expected feasible, got infeasible")
        return False

    if result["schedule"] != EXPECTED_SCHEDULE_A:
        ok = False
        print(f"  XX schedule mismatch: got {result['schedule']}, expected {EXPECTED_SCHEDULE_A}")
    else:
        print("  OK schedule matches expected exactly")

    if result["graduation_slot"] != EXPECTED_GRAD_A:
        ok = False
        print(f"  XX graduation slot mismatch: got {result['graduation_slot']}, expected {EXPECTED_GRAD_A}")
        if result["graduation_slot"] == 3:
            print("     (this is the 'forgot the parity flip' failure mode -- valid but suboptimal)")
    else:
        print(f"  OK graduation slot matches expected ({EXPECTED_GRAD_A}) -- flip was genuinely exploited")

    # independent re-check: XDEPT1 must be in a slot matching home OR alt parity
    sched = result["schedule"]
    xdept_slot = sched["XDEPT1"]
    _, sem = slot_to_year_sem(xdept_slot)
    course = COURSES_PARITY_FLIP["XDEPT1"]
    allowed = {course["semester_offered"], course.get("alt_parity")}
    if sem not in allowed:
        ok = False
        print(f"  XX PARITY VIOLATED: XDEPT1 placed in slot {xdept_slot} (sem {sem}), allowed parities are {allowed}")
    else:
        print(f"  OK XDEPT1's slot parity ({sem}) is within its allowed set {allowed}")

    return ok


def scenario_b_stream_filtering():
    print("\n=== SCENARIO B: stream filtering ===")
    ok = True

    filtered_a = filter_courses_for_student(COURSES_STREAM_FILTER, "A")
    if set(filtered_a.keys()) != EXPECTED_FILTERED_A:
        ok = False
        print(f"  XX Stream A filter mismatch: got {set(filtered_a.keys())}, expected {EXPECTED_FILTERED_A}")
    else:
        print(f"  OK Stream A correctly filtered to {set(filtered_a.keys())} (SB1 excluded entirely)")

    filtered_b = filter_courses_for_student(COURSES_STREAM_FILTER, "B")
    if set(filtered_b.keys()) != EXPECTED_FILTERED_B:
        ok = False
        print(f"  XX Stream B filter mismatch: got {set(filtered_b.keys())}, expected {EXPECTED_FILTERED_B}")
    else:
        print(f"  OK Stream B correctly filtered to {set(filtered_b.keys())} (SA1 excluded entirely)")

    return ok


def scenario_c_stream_choice():
    print("\n=== SCENARIO C: stream choice comparison ===")
    results = compare_streams(
        all_courses=COURSES_STREAM_CHOICE,
        streams=["A", "B"],
        horizon_slots=HORIZON_STREAM_CHOICE,
        normal_caps=CAPS_STREAM_CHOICE,
    )
    print("Results:", results)

    ok = True
    if not results["A"]["feasible"] or not results["B"]["feasible"]:
        print("  XX expected both streams feasible")
        return False

    if results["A"]["graduation_slot"] != EXPECTED_GRAD_STREAM_A:
        ok = False
        print(f"  XX Stream A grad slot: got {results['A']['graduation_slot']}, expected {EXPECTED_GRAD_STREAM_A}")
    else:
        print(f"  OK Stream A graduation slot matches expected ({EXPECTED_GRAD_STREAM_A})")

    if results["B"]["graduation_slot"] != EXPECTED_GRAD_STREAM_B:
        ok = False
        print(f"  XX Stream B grad slot: got {results['B']['graduation_slot']}, expected {EXPECTED_GRAD_STREAM_B}")
    else:
        print(f"  OK Stream B graduation slot matches expected ({EXPECTED_GRAD_STREAM_B})")

    # Confirm the comparison actually shows a real difference -- if both
    # streams always produced identical results, this test wouldn't prove
    # the comparison is doing anything meaningful.
    if results["A"]["graduation_slot"] == results["B"]["graduation_slot"]:
        ok = False
        print("  XX both streams produced identical results -- comparison has no signal in this test")
    else:
        print(f"  OK streams produced genuinely different outcomes "
              f"(A={results['A']['graduation_slot']}, B={results['B']['graduation_slot']}) -- comparison is meaningful")

    # Also confirm neither stream's solve leaked the other stream's courses
    if "SA1" in results.get("B", {}).get("schedule", {}) or "A1" in results.get("B", {}).get("schedule", {}) or "A2" in results.get("B", {}).get("schedule", {}):
        ok = False
        print("  XX Stream B's plan contains Stream A courses -- filtering leaked")
    else:
        print("  OK Stream B's plan contains no Stream A courses")

    if "B1" in results.get("A", {}).get("schedule", {}) or "B2" in results.get("A", {}).get("schedule", {}) or "B3" in results.get("A", {}).get("schedule", {}):
        ok = False
        print("  XX Stream A's plan contains Stream B courses -- filtering leaked")
    else:
        print("  OK Stream A's plan contains no Stream B courses")

    return ok


def main():
    results = [
        scenario_a_parity_flip(),
        scenario_b_stream_filtering(),
        scenario_c_stream_choice(),
    ]
    print("\n=== FINAL:", "PASS" if all(results) else "FAIL", "===")


if __name__ == "__main__":
    main()