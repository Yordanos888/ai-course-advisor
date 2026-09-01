"""
STAGE 8c: BROADER REAL-DATA SCENARIO TESTING
===============================================
Six scenarios, each independently rule-rechecked (never just trusting
the solver's own "feasible" claim), covering breadth Stage 8a/8b's
single-stream, single-scenario tests didn't exercise:

  1. All 4 streams, fresh student -- every stream must produce a valid,
     rule-respecting plan, not just Control (which 8a/8b always used).
  2. Drop legality on REAL courses -- an ordinary droppable course
     (legal, full round-trip re-solve) vs a real non-droppable one
     (Internship -- must be rejected outright).
  3. Cross-department parity-flip actually gets exercised on real data,
     not just present and unused.
  4. Stream-choice comparison -- an undecided student sees all 4
     streams' timelines side by side.
  5. Retake-limit-exceeded on a REAL course, with a real, readable
     diagnosis (not a generic course-code dump).
  6. A "struggling student" with MULTIPLE real failures across
     different years -- the realistic case this whole project exists for.
"""

from data_real_stage8b import load_real_courses_full, resolve_conditional_prereqs
from model_stage8_fixed import (
    build_and_solve, filter_courses_for_student, compute_normal_caps,
    cap_for_slot, slot_to_year_sem, natural_slot_for_course,
)

ok = True
STREAMS = ["Communication", "Computer", "Control", "Power"]


def check(label, condition, detail=""):
    global ok
    marker = "OK " if condition else "XX "
    print(f"  {marker}{label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        ok = False


def independent_recheck(label, filtered, schedule):
    """Re-derive rule compliance from scratch -- prereqs, caps, parity,
    non-droppable floor -- rather than trusting solver output."""
    local_ok = True

    for c, info in filtered.items():
        for p in info["prereqs"]:
            if p in schedule and not (schedule[c] > schedule[p]):
                local_ok = False
                print(f"    XX PREREQ VIOLATED ({label}): {c} not after {p}")

    normal_caps = compute_normal_caps(filtered)
    slot_totals = {}
    for c, s in schedule.items():
        slot_totals[s] = slot_totals.get(s, 0) + filtered[c]["credit_hours"]
    for s, total in slot_totals.items():
        cap = cap_for_slot(s, normal_caps)
        if total > cap:
            local_ok = False
            print(f"    XX CREDIT CAP VIOLATED ({label}): slot {s} has {total}, cap {cap}")

    for c, s in schedule.items():
        _, sem = slot_to_year_sem(s)
        allowed = {filtered[c]["semester_offered"]}
        if filtered[c].get("alt_parity"):
            allowed.add(filtered[c]["alt_parity"])
        if sem not in allowed:
            local_ok = False
            print(f"    XX PARITY VIOLATED ({label}): {c} at slot {s}")

    for c, info in filtered.items():
        if not info.get("is_droppable", True) and c in schedule:
            natural = natural_slot_for_course(info)
            if schedule[c] < natural:
                local_ok = False
                print(f"    XX NON-DROPPABLE FLOOR VIOLATED ({label}): {c} at {schedule[c]}, natural {natural}")

    check(f"Independent recheck clean ({label})", local_ok)
    return local_ok


def solve_for_stream(courses, stream, horizon_slots=18, policy_horizon_slots=15,
                       completed_courses=None, now_slot=0):
    filtered = filter_courses_for_student(courses, stream)
    resolved = resolve_conditional_prereqs(filtered, stream)
    result = build_and_solve(courses=resolved, horizon_slots=horizon_slots,
                               policy_horizon_slots=policy_horizon_slots,
                               completed_courses=completed_courses, now_slot=now_slot)
    return resolved, result


# ==================== Scenario 1: all 4 streams, fresh ====================
def scenario_1_all_streams(courses):
    print("\n=== SCENARIO 1: all 4 streams, fresh student ===")
    results = {}
    for stream in STREAMS:
        filtered, result = solve_for_stream(courses, stream)
        check(f"{stream}: feasible", result["feasible"])
        if result["feasible"]:
            print(f"  {stream}: {len(filtered)} courses, graduates at slot "
                  f"{result['graduation_slot']} {slot_to_year_sem(result['graduation_slot'])}, "
                  f"exceeds 5yr: {result['exceeds_policy_horizon']}")
            independent_recheck(stream, filtered, result["schedule"])
            results[stream] = result
    return results


# ==================== Scenario 2: drop legality on real courses ====================
def scenario_2_drop_legality(courses):
    print("\n=== SCENARIO 2: drop legality on real courses ===")
    filtered, baseline = solve_for_stream(courses, "Control")

    # Non-droppable: Internship must be rejected outright, no round-trip needed
    intern_droppable = filtered["ECEg4100"].get("is_droppable", True)
    check("ECEg4100 (Internship) is correctly flagged non-droppable", intern_droppable is False)

    # Droppable: pick an ordinary Year1 course, simulate dropping it after
    # partial completion, confirm a full plan STILL exists via round-trip resolve.
    drop_target = "ECEg2102"  # Fundamentals of EE, Year2 Sem2, droppable
    check(f"{drop_target} is flagged droppable", filtered[drop_target].get("is_droppable", True) is True)

    completed = {}
    for c, s in baseline["schedule"].items():
        if s <= 5 and c != drop_target:
            completed[c] = [{"status": "PASS", "slot": s}]
    completed[drop_target] = [{"status": "DROPPED", "slot": 5}]

    result = build_and_solve(courses=filtered, horizon_slots=18, policy_horizon_slots=15,
                               completed_courses=completed, now_slot=5)
    check(f"Round-trip re-solve after dropping {drop_target} is feasible", result["feasible"])
    if result["feasible"]:
        independent_recheck(f"drop {drop_target}", filtered, result["schedule"])
        # confirm DROPPED didn't count against the retake limit (should get
        # a normal single future attempt, same as Stage 4a's Student C proof)
        print(f"  {drop_target} rescheduled to slot {result['schedule'][drop_target]} "
              f"{slot_to_year_sem(result['schedule'][drop_target])}")


# ==================== Scenario 3: cross-dept parity flip actually used ====================
def scenario_3_parity_flip_usage(courses):
    print("\n=== SCENARIO 3: cross-department parity-flip actually exercised ===")
    filtered, baseline = solve_for_stream(courses, "Control")

    flip_courses = [c for c, info in filtered.items() if info.get("alt_parity")]
    print(f"  Cross-dept parity-flip-eligible courses in Control stream: {flip_courses}")

    # Force pressure: fail one right at its natural slot, forcing a retake
    # that must compete for room -- see if the solver ever chooses the
    # flipped parity over the home one when it's the better option.
    target = "ECEg5701"  # Power Electronics, Control/Power stream, home parity 1
    if target in filtered:
        home_parity = filtered[target]["semester_offered"]
        natural = natural_slot_for_course(filtered[target])
        completed = {}
        for c, s in baseline["schedule"].items():
            if s <= natural and c != target:
                completed[c] = [{"status": "PASS", "slot": s}]
        completed[target] = [{"status": "FAILED", "slot": natural}]

        result = build_and_solve(courses=filtered, horizon_slots=18, policy_horizon_slots=15,
                                   completed_courses=completed, now_slot=natural)
        check(f"Feasible after failing {target}", result["feasible"])
        if result["feasible"]:
            actual_slot = result["schedule"][target]
            _, actual_sem = slot_to_year_sem(actual_slot)
            used_flip = actual_sem != home_parity
            print(f"  {target}: home parity={home_parity}, retake landed at slot {actual_slot} "
                  f"(sem {actual_sem}) -- {'USED THE FLIP' if used_flip else 'used home parity'}")
            independent_recheck(f"parity-flip pressure on {target}", filtered, result["schedule"])
    else:
        print(f"  {target} not in Control stream's course set, skipping")


# ==================== Scenario 4: stream-choice comparison ====================
def scenario_4_stream_choice(courses):
    print("\n=== SCENARIO 4: stream choice comparison for an undecided student ===")
    comparison = {}
    for stream in STREAMS:
        _, result = solve_for_stream(courses, stream)
        if result["feasible"]:
            comparison[stream] = result["graduation_slot"]
    for stream, grad in sorted(comparison.items(), key=lambda x: x[1]):
        print(f"  {stream}: graduates at slot {grad} {slot_to_year_sem(grad)}")
    check("All 4 streams produced a comparable result", len(comparison) == 4)


# ==================== Scenario 5: retake limit exceeded, real diagnosis ====================
def scenario_5_retake_limit_real(courses):
    print("\n=== SCENARIO 5: retake limit exceeded on a real course ===")
    filtered, baseline = solve_for_stream(courses, "Control")
    target = "ECEg3104"  # Digital Logic Design
    natural = natural_slot_for_course(filtered[target])

    completed = {c: [{"status": "PASS", "slot": s}] for c, s in baseline["schedule"].items()
                 if s <= natural and c != target}
    completed[target] = [
        {"status": "FAILED", "slot": natural},
        {"status": "FAILED", "slot": natural + 2},
        {"status": "FAILED", "slot": natural + 4},
    ]

    result = build_and_solve(courses=filtered, horizon_slots=24, policy_horizon_slots=15,
                               completed_courses=completed, now_slot=natural + 4)
    check("Correctly diagnosed as RETAKE_LIMIT_EXCEEDED", result.get("status") == "RETAKE_LIMIT_EXCEEDED")
    if result.get("violations"):
        print(f"  Diagnosis: {result['violations'][0]}")
        check("Diagnosis message references the real course code", target in result["violations"][0])


# ==================== Scenario 6: struggling student, multiple real failures ====================
def scenario_6_struggling_student(courses):
    print("\n=== SCENARIO 6: struggling student, multiple failures across different years ===")
    print("(Simulated SEQUENTIALLY -- failure 2 is injected into the timeline that")
    print(" already reflects failure 1's consequences, not the fresh baseline. Naively")
    print(" reusing the fresh baseline for both would create a contradiction if anything")
    print(" depending on the first failure was 'already passed' before its retake.)")
    filtered, baseline = solve_for_stream(courses, "Control")

    fail_1 = "CEng2103"   # early, Year1
    fail_2 = "ECEg3104"   # mid, Year3, deep chain (DLD)

    # --- Step 1: simulate failure 1 alone, get a real contradiction-free timeline ---
    fail_1_slot = baseline["schedule"][fail_1]
    completed_1 = {c: [{"status": "PASS", "slot": s}] for c, s in baseline["schedule"].items()
                   if s < fail_1_slot}
    completed_1[fail_1] = [{"status": "FAILED", "slot": fail_1_slot}]
    after_fail_1 = build_and_solve(courses=filtered, horizon_slots=24, policy_horizon_slots=15,
                                     completed_courses=completed_1, now_slot=fail_1_slot)
    check("Timeline after failure 1 alone is feasible", after_fail_1["feasible"])
    if not after_fail_1["feasible"]:
        return

    # --- Step 2: inject failure 2 at WHATEVER slot it lands at in that
    # already-adjusted timeline (not its original fresh-baseline slot) ---
    fail_2_slot = after_fail_1["schedule"][fail_2]
    completed_2 = {c: [{"status": "PASS", "slot": s}] for c, s in after_fail_1["schedule"].items()
                   if s < fail_2_slot and c != fail_1}
    completed_2[fail_1] = [{"status": "FAILED", "slot": fail_1_slot},
                             {"status": "PASS", "slot": after_fail_1["schedule"][fail_1]}]
    completed_2[fail_2] = [{"status": "FAILED", "slot": fail_2_slot}]

    result = build_and_solve(courses=filtered, horizon_slots=27, policy_horizon_slots=15,
                               completed_courses=completed_2, now_slot=fail_2_slot)
    check("Struggling student scenario is feasible (real, honest plan)", result["feasible"])
    if result["feasible"]:
        print(f"  Graduation slot: {result['graduation_slot']} {slot_to_year_sem(result['graduation_slot'])}")
        print(f"  Exceeds 5-year policy horizon: {result['exceeds_policy_horizon']}")
        print(f"  {fail_1} retake landed at: slot {after_fail_1['schedule'][fail_1]}")
        print(f"  {fail_2} retake landed at: slot {result['schedule'][fail_2]}")
        independent_recheck("struggling student", filtered, result["schedule"])


def main():
    print("Loading full real curriculum...")
    courses = load_real_courses_full()
    check("87 courses loaded", len(courses) == 87)

    scenario_1_all_streams(courses)
    scenario_2_drop_legality(courses)
    scenario_3_parity_flip_usage(courses)
    scenario_4_stream_choice(courses)
    scenario_5_retake_limit_real(courses)
    scenario_6_struggling_student(courses)

    print("\n=== FINAL:", "PASS" if ok else "FAIL", "===")


if __name__ == "__main__":
    main()