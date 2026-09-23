from grade_calculator import (
    GRADE_POINTS, normalize_grade, compute_sgpa,
    compute_required_sgpa, compute_cgpa_projection,
    parse_semester_token, parse_credit_override_string,
    semesters_in_window, semesters_before, parse_sgpa_list,
)

ok = True


def check(label, condition, detail=""):
    global ok
    marker = "OK " if condition else "XX "
    print(f"  {marker}{label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        ok = False


print("=== Grade scale sanity ===")
check("A+ = 4.00", GRADE_POINTS["A+"] == 4.00)
check("A- = 3.75", GRADE_POINTS["A-"] == 3.75)
check("C = 2.00", GRADE_POINTS["C"] == 2.00)
check("F = 0.00", GRADE_POINTS["F"] == 0.00)
check("normalize handles lowercase/spacing", normalize_grade(" a+ ") == "A+")
check("normalize rejects garbage", normalize_grade("Z") is None)

print("\n=== Part 1: SGPA ===")
# 3 courses: 3cr A (4.0), 3cr B+ (3.5), 4cr C (2.0)
# = (3*4.0 + 3*3.5 + 4*2.0) / (3+3+4) = (12+10.5+8)/10 = 30.5/10 = 3.05
sgpa = compute_sgpa([(3, "A"), (3, "B+"), (4, "C")])
check("Basic SGPA calculation", sgpa == 3.05, f"got {sgpa}")

try:
    compute_sgpa([(3, "Z")])
    check("Unknown grade raises", False)
except ValueError:
    check("Unknown grade raises", True)

try:
    compute_sgpa([])
    check("Zero credits raises", False)
except ValueError:
    check("Zero credits raises", True)

print("\n=== Semester helpers ===")
check("parse_semester_token '3Y2S'", parse_semester_token("3Y2S") == (3, 2))
check("parse_semester_token '3y2s' (lowercase)", parse_semester_token("3y2s") == (3, 2))
check("parse_semester_token garbage -> None", parse_semester_token("hello") is None)

check("semesters_in_window basic", semesters_in_window(3, 1, 4, 1) == [(3, 1), (3, 2), (4, 1)])
check("semesters_before(3,1)", semesters_before(3, 1) == [(1, 1), (1, 2), (2, 1), (2, 2)])
check("semesters_before(1,1) is empty", semesters_before(1, 1) == [])

try:
    semesters_in_window(4, 1, 3, 1)
    check("window with end<start raises", False)
except ValueError:
    check("window with end<start raises", True)

overrides = parse_credit_override_string("3Y2S=12, 4Y1S=19")
check("parse_credit_override_string basic", overrides == {(3, 2): 12, (4, 1): 19}, f"got {overrides}")
check("parse_credit_override_string empty -> {}", parse_credit_override_string("") == {})

try:
    parse_credit_override_string("garbage")
    check("malformed override raises", False)
except ValueError:
    check("malformed override raises", True)

print("\n=== Part 2: required SGPA (matches worked example) ===")
# Worked example: student at 3Y1S, current CGPA 3.10, wants 3.30 by 4Y1S,
# no drops/adds, and (per "without having any dropped/added courses")
# we use the NORMAL ECE curriculum credit totals for the real semesters
# involved. We don't have live DB numbers here, so first prove the
# FORMULA behaves correctly on round numbers, independent of any real
# curriculum credit totals:
#
# prev_total_credits = 60, current_cgpa = 3.10 -> prior_points = 186
# future window (3 semesters) totals credits = [18, 18, 18] = 54
# goal_cgpa = 3.30 at total credits (60+54=114) -> required_total_points = 376.2
# needed_points = 376.2 - 186 = 190.2
# required_sgpa = 190.2 / 54 = 3.5222... -> rounds to 3.52
result = compute_required_sgpa(60, 3.10, [18, 18, 18], 3.30)
check("Required SGPA basic case", result == {"feasible": True, "already_secured": False, "required_sgpa": 3.52},
      f"got {result}")

# Sanity: manually re-derive to make sure the formula itself is right,
# not just internally self-consistent
prior_points = 3.10 * 60
total_credits_at_goal = 60 + 54
required_total_points = 3.30 * total_credits_at_goal
needed = required_total_points - prior_points
manual_required = round(needed / 54, 2)
check("Required SGPA matches independent manual derivation", result["required_sgpa"] == manual_required,
      f"solver={result['required_sgpa']} manual={manual_required}")

# Already-secured case: with 0 future SGPA, this scenario's CGPA floor
# is 186/114 = 1.6316 -- so a goal AT or BELOW that is guaranteed even
# in the worst case (my earlier test used 2.50, which is actually ABOVE
# that floor and does need a positive push -- confirmed independently
# above, not a bug).
result2 = compute_required_sgpa(60, 3.10, [18, 18, 18], 1.50)
check("Already-secured case (goal below the worst-case floor)",
      result2 == {"feasible": True, "already_secured": True, "required_sgpa": 0.0},
      f"got {result2}")

# Infeasible case: goal CGPA impossibly high
result3 = compute_required_sgpa(60, 2.00, [18], 3.95)
check("Infeasible case (required > 4.0)", result3["feasible"] is False, f"got {result3}")
if not result3["feasible"]:
    check("Infeasible reason mentions the numbers", "4.00" in result3["reason"])

print("\n=== Part 3: CGPA projection ===")
# Same student: prev=60cr @ 3.10, future 3 semesters of 18cr each,
# hypothetical SGPAs [3.33, 3.21, 3.50] (matches the user's example)
proj = compute_cgpa_projection(60, 3.10, [18, 18, 18], [3.33, 3.21, 3.50])
check("Projection returns 3 entries", len(proj) == 3, f"got {len(proj)}")

# Manually verify semester 1: running_points = 186 + 3.33*18 = 245.94, running_credits=78
# running_cgpa = 245.94/78 = 3.1531 -> 3.15
check("Sem1 running CGPA", proj[0]["running_cgpa"] == round((186 + 3.33 * 18) / 78, 2), f"got {proj[0]}")

# Sem2: running_points = 245.94 + 3.21*18 = 303.72, running_credits=96 -> 3.1638 -> 3.16
check("Sem2 running CGPA", proj[1]["running_cgpa"] == round((245.94 + 3.21 * 18) / 96, 2), f"got {proj[1]}")

# Sem3: running_points = 303.72 + 3.50*18 = 366.72, running_credits=114 -> 3.2168 -> 3.22
check("Sem3 (final) running CGPA", proj[2]["running_cgpa"] == round((303.72 + 3.50 * 18) / 114, 2), f"got {proj[2]}")

try:
    compute_cgpa_projection(60, 3.10, [18, 18, 18], [3.33, 3.21])
    check("Mismatched SGPA count raises", False)
except ValueError:
    check("Mismatched SGPA count raises", True)

try:
    compute_cgpa_projection(60, 3.10, [18], [4.5])
    check("Out-of-range SGPA raises", False)
except ValueError:
    check("Out-of-range SGPA raises", True)

print("\n=== parse_sgpa_list ===")
check("parse_sgpa_list basic", parse_sgpa_list("3.33, 3.21, 3.50") == [3.33, 3.21, 3.50])
try:
    parse_sgpa_list("3.33, abc")
    check("parse_sgpa_list rejects garbage", False)
except ValueError:
    check("parse_sgpa_list rejects garbage", True)

print("\n=== FINAL:", "ALL PASS" if ok else "SOME FAILURES", "===")
