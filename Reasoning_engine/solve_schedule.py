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

INPUT CONTRACT (matches the existing intake flow in bot.py/orchestrator.py,
unchanged): current_year (1-5), current_sem (1, 2, or 3), stream_short_name
("Computer"/"Communication"/"Control"/"Power", or None for an undecided
student), failed_course_codes -- a list of ALREADY-RESOLVED course codes
(fuzzy free-text resolution via query_api.resolve_course_entity is the
caller's job, same separation of concerns the current orchestrator.py uses).

HISTORY ASSUMPTION (explicitly confirmed, matches the existing system):
"current position implies clean history" -- every course normally taken
before the student's declared (year, sem) is assumed PASSED at its own
labeled slot, UNLESS it's in the failed_course_codes list, in which case
it's FAILED at that same labeled slot. This does not attempt to track
actual delay history before the student's current declared position.
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


def build_implicit_history(resolved_courses, current_year, current_sem, failed_codes):
    """
    Returns (completed_courses, now_slot, warnings).
    completed_courses: the {code: [{"status":..., "slot":...}]} shape
    build_and_solve expects.
    """
    now_slot = year_sem_to_slot(current_year, current_sem) - 1
    completed = {}
    warnings = []
    depends_on_failure_memo = {}

    for code, info in resolved_courses.items():
        natural = natural_slot_for_course(info)
        is_failed = code in failed_codes

        if natural <= now_slot:
            if is_failed:
                completed[code] = [{"status": "FAILED", "slot": natural}]
            elif _transitively_depends_on_failure(code, resolved_courses, failed_codes, depends_on_failure_memo):
                # Do NOT mark this PASS -- it would contradict the
                # reported failure of something it depends on. Leave it
                # as an open decision; the solver will correctly place
                # it after the real retake.
                warnings.append(
                    f"{code} ({info['name']}) was NOT assumed already passed, since it "
                    f"depends on a course you reported failing -- the solver will place "
                    f"it after that retake instead."
                )
            else:
                completed[code] = [{"status": "PASS", "slot": natural}]
        elif is_failed:
            warnings.append(
                f"{code} ({info['name']}) is normally taken later than your declared "
                f"position, but was listed as failed -- treating it as failed at its "
                f"normal slot anyway."
            )
            completed[code] = [{"status": "FAILED", "slot": natural}]

    unresolved = [c for c in failed_codes if c not in resolved_courses]
    if unresolved:
        warnings.append(
            f"These aren't part of this stream's curriculum, so they were ignored: "
            f"{', '.join(unresolved)}"
        )

    return completed, now_slot, warnings


def solve_schedule_for_student(session, current_year, current_sem, stream_short_name, failed_course_codes):
    failed_set = set(failed_course_codes)

    raw = load_raw_courses_from_db(session)
    resolved = filter_and_resolve_for_student(raw, stream_short_name)

    completed, now_slot, warnings = build_implicit_history(resolved, current_year, current_sem, failed_set)

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
        plan_by_term.setdefault((year, sem), []).append({
            "code": code,
            "name": resolved[code]["name"],
            "credit_hours": resolved[code]["credit_hours"],
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


def compare_all_streams(session, current_year, current_sem, failed_course_codes, stream_names=None):
    stream_names = stream_names or ALL_STREAMS
    return {
        stream: solve_schedule_for_student(session, current_year, current_sem, stream, failed_course_codes)
        for stream in stream_names
    }
