from fake_data_stage4a import (
    COURSES, HORIZON_SLOTS, NORMAL_SEMESTER_CREDIT_CAP, MAX_ATTEMPTS,
    STUDENT_A_AT_LIMIT, EXPECTED_A,
    STUDENT_B_OVER_LIMIT, EXPECTED_B,
    STUDENT_C_DROPPED_EXCLUDED, EXPECTED_C,
)
from model_stage4 import build_and_solve


def run_case(label, student, expected):
    print(f"\n--- {label} ---")
    result = build_and_solve(
        courses=COURSES,
        horizon_slots=HORIZON_SLOTS,
        normal_caps=NORMAL_SEMESTER_CREDIT_CAP,
        completed_courses=student["completed_courses"],
        now_slot=student["now_slot"],
        max_attempts=MAX_ATTEMPTS,
    )
    print("Result:", result)

    ok = True
    if result["feasible"] != expected["feasible"]:
        ok = False
        print(f"  XX feasibility mismatch: got {result['feasible']}, expected {expected['feasible']}")
    else:
        print(f"  OK feasibility matches expected ({expected['feasible']})")

    if expected["feasible"]:
        if result.get("schedule") != expected["schedule"]:
            ok = False
            print(f"  XX schedule mismatch: got {result.get('schedule')}, expected {expected['schedule']}")
        else:
            print("  OK schedule matches expected exactly")
        if result.get("graduation_slot") != expected["graduation_slot"]:
            ok = False
            print(f"  XX graduation slot mismatch: got {result.get('graduation_slot')}, expected {expected['graduation_slot']}")
        else:
            print("  OK graduation slot matches expected")
    else:
        # Infeasible case: must have a clear diagnosis, not just a bare status
        if result.get("status") != "RETAKE_LIMIT_EXCEEDED":
            ok = False
            print(f"  XX expected a diagnosed RETAKE_LIMIT_EXCEEDED status, got {result.get('status')}")
        else:
            print("  OK correctly diagnosed as RETAKE_LIMIT_EXCEEDED (not a bare/unexplained infeasible)")
        if not result.get("violations"):
            ok = False
            print("  XX expected a human-readable violation message, got none")
        else:
            print(f"  OK violation message present: {result['violations']}")

    return ok


def main():
    results = []
    results.append(run_case(
        "Student A: exactly AT the retake limit (2 failed, 1 remains)",
        STUDENT_A_AT_LIMIT, EXPECTED_A,
    ))
    results.append(run_case(
        "Student B: OVER the retake limit (3 failed, 0 remain)",
        STUDENT_B_OVER_LIMIT, EXPECTED_B,
    ))
    results.append(run_case(
        "Student C: DROPPED attempt correctly excluded from the count",
        STUDENT_C_DROPPED_EXCLUDED, EXPECTED_C,
    ))

    print("\n=== FINAL:", "PASS" if all(results) else "FAIL", "===")


if __name__ == "__main__":
    main()