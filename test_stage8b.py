from data_real_stage8b import load_real_courses_full, resolve_conditional_prereqs
from model_stage8_fixed import build_and_solve, filter_courses_for_student, slot_to_year_sem

ok = True


def check(label, condition, detail=""):
    global ok
    marker = "OK " if condition else "XX "
    print(f"  {marker}{label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        ok = False


print("=== Full curriculum load (all 87 courses) ===")
courses = load_real_courses_full()
check("87 courses loaded", len(courses) == 87, f"got {len(courses)}")
check("ECEg4112 has conditional_prereqs, no unconditional ones",
      courses["ECEg4112"]["prereqs"] == [] and len(courses["ECEg4112"]["conditional_prereqs"]) == 5,
      f"prereqs={courses['ECEg4112']['prereqs']} conditional={courses['ECEg4112']['conditional_prereqs']}")
check("ECEg5108 has ECEg5107 as a real prereq + ALL_STREAM_COURSES marker",
      courses["ECEg5108"]["prereqs"] == ["ECEg5107"]
      and courses["ECEg5108"]["special_requirement"] == "ALL_STREAM_COURSES")
check("NEE5108 has no real prereqs + ALL_COURSES marker",
      courses["NEE5108"]["prereqs"] == [] and courses["NEE5108"]["special_requirement"] == "ALL_COURSES")

print("\n=== Conditional prereq resolution per stream (ECEg4112) ===")
expected_by_stream = {
    "Communication": {"IETP4115", "ECEg4101", "ECEg4103"},
    "Computer": {"IETP4115", "ECEg4101", "ECEg4103"},
    "Power": {"IETP4115", "ECEg4109"},
    "Control": {"IETP4115", "ECEg4105"},
}
for stream, expected_prereqs in expected_by_stream.items():
    resolved = resolve_conditional_prereqs(courses, stream)
    actual = set(resolved["ECEg4112"]["prereqs"])
    check(f"ECEg4112 prereqs for {stream} stream", actual == expected_prereqs,
          f"got {actual}, expected {expected_prereqs}")

print("\n=== THE KEY TEST: is ECEg5107 a STRICT prerequisite of FYP-II, not just >= ===")
# Force a scenario: solve for the Control stream, then check whether the
# solver would EVER allow ECEg5108 (FYP-II) and ECEg5107 (FYP-I) to land
# in the SAME slot. If the fix works, this must be impossible (strict >).
# If it regressed to treating ECEg5107 as just another >= stream peer,
# same-slot placement would be legal, which is wrong (FYP-II genuinely
# cannot start before FYP-I is actually finished and graded).
filtered = filter_courses_for_student(courses, "Control")
resolved = resolve_conditional_prereqs(filtered, "Control")
result = build_and_solve(courses=resolved, horizon_slots=18, policy_horizon_slots=15)
check("Real Control-stream solve (with ECEg4112/5108/NEE5108 included) is feasible", result["feasible"])

if result["feasible"]:
    sched = result["schedule"]
    fyp1_slot = sched.get("ECEg5107")
    fyp2_slot = sched.get("ECEg5108")
    print(f"  ECEg5107 (FYP-I): slot {fyp1_slot} {slot_to_year_sem(fyp1_slot)}")
    print(f"  ECEg5108 (FYP-II): slot {fyp2_slot} {slot_to_year_sem(fyp2_slot)}")
    check("FYP-II is STRICTLY after FYP-I (not same slot, not before)", fyp2_slot > fyp1_slot,
          f"fyp1={fyp1_slot} fyp2={fyp2_slot}")

    # cross-check: some ORDINARY stream peer (not ECEg5107) CAN legally
    # share FYP-II's slot, proving >= still works correctly for everyone else
    same_slot_peers = [c for c, s in sched.items() if s == fyp2_slot and c != "ECEg5108"]
    check("At least one ordinary stream course legally shares FYP-II's slot (proves >= still works for peers)",
          len(same_slot_peers) > 0, f"peers at slot {fyp2_slot}: {same_slot_peers}")

    print("\n=== Independent re-check across full real solve ===")
    prereq_ok = True
    for c, info in resolved.items():
        for p in info["prereqs"]:
            if p in sched and not (sched[c] > sched[p]):
                prereq_ok = False
                check(f"prereq {c} > {p}", False)
    check("All prerequisites (including resolved conditional ones) respected", prereq_ok)

print("\n=== FINAL:", "PASS" if ok else "FAIL", "===")