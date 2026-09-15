"""
solve_schedule.py
===================
The public interface between the reasoning engine and the rest of the
project. Hides everything internal (slot numbering, semester-type
arithmetic, the filter->resolve->solve pipeline) behind two functions:

    solve_schedule_for_student(session, current_year, current_sem,
                                 stream_short_name, failed_course_codes)
    compare_all_streams(session, current_year, current_sem,
                          failed_course_codes, stream_names=None)

INPUT CONTRACT: current_year (1-5), current_sem (1, 2, or 3),
stream_short_name ("Computer"/"Communication"/"Control"/"Power", or None
for an undecided student), plus three lists of ALREADY-RESOLVED course
codes (fuzzy free-text resolution via query_api.resolve_course_entity is
the caller's job, same separation of concerns orchestrator.py uses):

    failed_course_codes   -- attempted and failed (graded attempt used)
    added_course_codes    -- taken AHEAD of normal schedule, already passed
    dropped_course_codes  -- dropped out of a past semester, never graded

HISTORY ASSUMPTION (explicitly confirmed, matches the existing system):
"current position implies clean history" -- every course normally taken
before the student's declared (year, sem) is assumed PASSED at its own
labeled slot, EXCEPT as overridden by the three lists above. This does
not attempt to reconstruct actual delay history beyond what the student
reports.
"""

from db_loader import load_raw_courses_from_db, filter_and_resolve_for_student
from model_stage8_fixed import build_and_solve, slot_to_year_sem, natural_slot_for_course

SOLVE_HORIZON_SLOTS = 45   # 15 years -- generous, so the solver always finds
                           # a real plan rather than a bare "no solution"
POLICY_HORIZON_SLOTS = 15  # 5 years -- the officially targeted completion time

ALL_STREAMS = ["Computer", "Communication", "Control", "Power"]


def year_sem_to_slot(year, sem, semester_types=(1, 2, 3)):
    types_per_year = len(semester_types)
    idx = semester_types.index(sem)
    return (year - 1) * types_per_year + idx + 1


def _transitively_depends_on_failure(code, resolved_courses, failed_set, memo=None):
    """
    True if `code`'s prerequisite chain (direct or transitive) includes
    anything in failed_set. Used to prevent a real bug: naively marking
    every course labeled before the student's declared position as PASS
    can contradict a reported failure whose DEPENDENT is also labeled
    before that position (you can't have passed something before its own
    prerequisite was actually completed). Caught on real data in Stage
    8c's testing (CEng2103 -> MEng2102) and resurfaces here for the same
    structural reason -- fixed at the source this time, not just in test
    construction.
    """
    memo = memo if memo is not None else {}
    if code in memo:
        return memo[code]
    info = resolved_courses.get(code)
    if not info:
        memo[code] = False
        return False
    memo[code] = False  # guard against cycles before recursing
    for p in info.get("prereqs", []):
        if p in failed_set or _transitively_depends_on_failure(p, resolved_courses, failed_set, memo):
            memo[code] = True
            return True
    return False


def _latest_past_slot_matching_parity(info, now_slot, semester_types=(1, 2, 3)):
    """
    The most recent slot at or before now_slot whose parity this course
    could actually have been offered in (home parity, or the
    cross-department flipped parity). Used to place an ADDED course --
    one the student took ahead of its normal schedule -- at a plausible
    real point in their past rather than at its (future) natural slot.
    Returns None if no such slot exists.
    """
    allowed = {info["semester_offered"]}
    if info.get("alt_parity"):
        allowed.add(info["alt_parity"])
    for s in range(now_slot, 0, -1):
        if slot_to_year_sem(s, semester_types)[1] in allowed:
            return s
    return None


def build_implicit_history(resolved_courses, current_year, current_sem,
                            failed_codes, added_codes=None, dropped_codes=None):
    """
    Returns (completed_courses, now_slot, warnings).
    completed_courses: the {code: [{"status":..., "slot":...}]} shape
    build_and_solve expects.

    THREE KINDS OF STUDENT-REPORTED HISTORY, on top of the baseline
    "everything before your declared position was passed" assumption:

      failed_codes  -- attempted and failed. Counts as a graded attempt
                       (retake limit applies). Needs a future retake.
      dropped_codes -- was in a past semester but dropped out of it, so
                       never graded. Does NOT count toward the retake
                       limit (campus rule), but still needs a future
                       attempt since it was never passed.
      added_codes   -- taken AHEAD of its normal schedule and passed.
                       Its natural slot is in the future, but it's
                       already done, so it must be pinned as PASS in the
                       past and never scheduled again.

    Conflicts between the three lists are resolved by precedence
    (failed > dropped > added) with a warning, rather than silently
    picking one -- a student who lists the same course twice has told us
    something contradictory and deserves to know how it was read.
    """
    added_codes = set(added_codes or [])
    dropped_codes = set(dropped_codes or [])
    failed_codes = set(failed_codes)

    now_slot = year_sem_to_slot(current_year, current_sem) - 1
    completed = {}
    warnings = []

    # --- Resolve contradictory listings before anything else ---
    for code in sorted(failed_codes & dropped_codes):
        warnings.append(f"{code} was listed as both failed and dropped -- treating it as failed.")
        dropped_codes.discard(code)
    for code in sorted(failed_codes & added_codes):
        warnings.append(f"{code} was listed as both failed and added -- treating it as failed.")
        added_codes.discard(code)
    for code in sorted(dropped_codes & added_codes):
        warnings.append(f"{code} was listed as both dropped and added -- treating it as dropped.")
        added_codes.discard(code)

    # --- Validate each list against the student's declared position ---
    valid_added = set()
    for code in sorted(added_codes):
        info = resolved_courses.get(code)
        if info is None:
            continue  # reported below with the other unknown codes
        natural = natural_slot_for_course(info)
        if natural <= now_slot:
            warnings.append(
                f"{code} ({info['name']}) is normally taken before your current position, "
                f"so it's already counted as passed -- no need to list it as added."
            )
            continue
        if _latest_past_slot_matching_parity(info, now_slot) is None:
            warnings.append(
                f"{code} ({info['name']}) couldn't be placed in your past (no earlier "
                f"semester matches when it's offered) -- it was scheduled normally instead."
            )
            continue
        valid_added.add(code)

    valid_dropped = set()
    for code in sorted(dropped_codes):
        info = resolved_courses.get(code)
        if info is None:
            continue
        natural = natural_slot_for_course(info)
        if natural > now_slot:
            warnings.append(
                f"{code} ({info['name']}) is normally taken later than your current position, "
                f"so it couldn't have been dropped yet -- it was scheduled normally instead."
            )
            continue
        valid_dropped.add(code)

    # Courses known NOT to have been passed: their dependents must not be
    # auto-assumed passed either (same causality rule that already
    # applied to failures -- a dropped course blocks its chain exactly
    # the same way an ungraded failure does).
    not_passed = failed_codes | valid_dropped
    depends_memo = {}

    # --- Baseline pass set (before adds), used to validate the adds ---
    base_passed = set()
    for code, info in resolved_courses.items():
        if natural_slot_for_course(info) <= now_slot and code not in not_passed:
            if not _transitively_depends_on_failure(code, resolved_courses, not_passed, depends_memo):
                base_passed.add(code)

    # An added course is only credible if its own prerequisites were
    # actually satisfiable by then -- otherwise accepting it would make
    # the whole model infeasible for a reason the student can't see.
    for code in sorted(valid_added):
        info = resolved_courses[code]
        unmet = [p for p in info.get("prereqs", []) if p not in base_passed and p not in valid_added]
        if unmet:
            warnings.append(
                f"{code} ({info['name']}) couldn't be counted as taken ahead of schedule, "
                f"because its prerequisite(s) {', '.join(unmet)} wouldn't have been completed "
                f"yet -- it was scheduled normally instead."
            )
            valid_added.discard(code)

    # --- Build the actual history records ---
    for code, info in resolved_courses.items():
        natural = natural_slot_for_course(info)

        if code in failed_codes:
            slot = natural if natural <= now_slot else natural
            if natural > now_slot:
                warnings.append(
                    f"{code} ({info['name']}) is normally taken later than your declared "
                    f"position, but was listed as failed -- treating it as failed at its "
                    f"normal slot anyway."
                )
            completed[code] = [{"status": "FAILED", "slot": slot}]

        elif code in valid_dropped:
            # DROPPED, not FAILED: no graded attempt consumed, but also
            # not passed, so the solver still owes it a future slot.
            completed[code] = [{"status": "DROPPED", "slot": natural}]

        elif code in valid_added:
            placed = _latest_past_slot_matching_parity(info, now_slot)
            completed[code] = [{"status": "PASS", "slot": placed}]

        elif natural <= now_slot:
            if _transitively_depends_on_failure(code, resolved_courses, not_passed, depends_memo):
                warnings.append(
                    f"{code} ({info['name']}) was NOT assumed already passed, since it "
                    f"depends on a course you reported failing or dropping -- the solver "
                    f"will place it after that retake instead."
                )
            else:
                completed[code] = [{"status": "PASS", "slot": natural}]

    all_reported = failed_codes | dropped_codes | added_codes
    unresolved = sorted(c for c in all_reported if c not in resolved_courses)
    if unresolved:
        warnings.append(
            f"These aren't part of this stream's curriculum, so they were ignored: "
            f"{', '.join(unresolved)}"
        )

    return completed, now_slot, warnings


def solve_schedule_for_student(session, current_year, current_sem, stream_short_name,
                                 failed_course_codes, added_course_codes=None,
                                 dropped_course_codes=None):
    raw = load_raw_courses_from_db(session)
    resolved = filter_and_resolve_for_student(raw, stream_short_name)

    completed, now_slot, warnings = build_implicit_history(
        resolved, current_year, current_sem,
        set(failed_course_codes), added_course_codes, dropped_course_codes,
    )

    result = build_and_solve(
        courses=resolved,
        horizon_slots=SOLVE_HORIZON_SLOTS,
        policy_horizon_slots=POLICY_HORIZON_SLOTS,
        completed_courses=completed,
        now_slot=now_slot,
    )

    if not result["feasible"]:
        return {
            "feasible": False,
            "status": result.get("status"),
            "violations": result.get("violations", []),
            "warnings": warnings,
        }

    plan_by_term = {}
    for code, slot in result["schedule"].items():
        if slot <= now_slot:
            continue  # historical, not part of the forward-looking plan
        year, sem = slot_to_year_sem(slot)
        info = resolved[code]

        # If this course landed on its FLIPPED parity (not its home
        # semester_offered), it's being taken alongside the partner
        # department -- surface that so the student knows to actually
        # register for it through that department's offering.
        cross_dept_note = None
        if info.get("alt_parity") and sem == info["alt_parity"] and sem != info["semester_offered"]:
            cross_dept_note = info.get("alt_parity_department")

        plan_by_term.setdefault((year, sem), []).append({
            "code": code,
            "name": info["name"],
            "credit_hours": info["credit_hours"],
            "cross_dept_note": cross_dept_note,
        })

    grad_year, grad_sem = slot_to_year_sem(result["graduation_slot"])

    return {
        "feasible": True,
        "stream": stream_short_name,
        "plan_by_term": plan_by_term,
        "graduation": {"year": grad_year, "semester": grad_sem},
        "exceeds_5_year_policy": result["exceeds_policy_horizon"],
        "warnings": warnings,
    }


def compare_all_streams(session, current_year, current_sem, failed_course_codes,
                          added_course_codes=None, dropped_course_codes=None, stream_names=None):
    stream_names = stream_names or ALL_STREAMS
    return {
        stream: solve_schedule_for_student(
            session, current_year, current_sem, stream,
            failed_course_codes, added_course_codes, dropped_course_codes,
        )
        for stream in stream_names
    }
