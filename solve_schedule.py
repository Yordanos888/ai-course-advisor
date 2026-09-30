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
from model_stage8_fixed import (build_and_solve, slot_to_year_sem, natural_slot_for_course,
                                stream_enrollment_floor_slot, cap_for_slot, _is_major_course)

SOLVE_HORIZON_SLOTS = 45   # 15 years -- generous, so the solver always finds
                           # a real plan rather than a bare "no solution"
POLICY_HORIZON_SLOTS = 15  # 5 years -- the officially targeted completion time

ALL_STREAMS = ["Computer", "Communication", "Control", "Power"]

# FYP2_PREREQ_WAIVER_CANDIDATES: courses the department will, in real
# practice, sometimes NOT insist on as a completed prerequisite for
# FYP-II (ECEg5108) -- a genuine but informal inconsistency, confirmed
# explicitly, not a written curriculum rule. build_and_solve() only ever
# reaches for these after the documented overload mechanism has already
# been tried and found insufficient (see its docstring), and only when
# waiving one actually closes the gap to the 5-year policy horizon.
#
# Per-stream candidates, in the order they should be tried singly before
# the full set is tried together:
FYP2_PREREQ_WAIVER_CANDIDATES_BY_STREAM = {
    "Computer": ["ECEg4410"],                  # Database Systems
    "Communication": ["ECEg4102"],              # Microprocessors and Interfacing
    "Power": ["ECEg4102", "ECEg4510"],          # Microprocessors and Interfacing, Modern Control Systems
    "Control": ["ECEg4506"],                    # Process Control Fundamentals
}
# Applies on top of the stream-specific list above, for every stream.
FYP2_PREREQ_WAIVER_UNIVERSAL_CANDIDATE = "ECEg4112"  # Integrated Design Project


def _fyp2_waiver_candidates_for_stream(stream_short_name):
    """Ordered candidate list for THIS student's stream: the stream-
    specific courses first (as the department would reach for the more
    specific accommodation first), then the universal one common to
    every stream."""
    candidates = list(FYP2_PREREQ_WAIVER_CANDIDATES_BY_STREAM.get(stream_short_name, []))
    if FYP2_PREREQ_WAIVER_UNIVERSAL_CANDIDATE not in candidates:
        candidates.append(FYP2_PREREQ_WAIVER_UNIVERSAL_CANDIDATE)
    return candidates


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


# CROWD RELIEF SCOPE: deliberately narrowed to ONE course. The general
# rule (any droppable, all-stream course at/after 4Y2S) let relief undo
# the solver's legal plan (e.g. pulling Microprocessors back into a full
# semester). Only this course may be rebalanced now.
CROWD_RELIEF_ELIGIBLE_COURSES = {"IEng5104"}   # Industrial Management and Engineering Economy


def apply_crowd_relief(schedule, resolved, now_slot, normal_caps=None, waived=()):
    """
    Post-processing pass: rebalances same-parity future semesters by
    moving eligible courses from crowded terms to lighter ones.

    SCOPE (current): only courses in CROWD_RELIEF_ELIGIBLE_COURSES (just
    Industrial Management and Engineering Economy) may move, and the
    National Exit Exam (ALL_COURSES) is excluded from the per-term course
    counts. Every rule below still applies on top of that.

    WHAT QUALIFIES AS A RELIEF CANDIDATE:
        A course is eligible for crowd relief if and only if ALL of:
          1. It is scheduled in a FUTURE slot (slot > now_slot).
          2. Its 'streams' field is None (common to all streams -- not
             stream-specific or a subset-of-streams course).
          3. It is droppable (is_droppable == True or absent).
          4. Its semester parity is 1 or 2 (Semester 3 is the Industry
             Internship slot -- never touched by crowd relief).
          5. Its NATURAL slot (year_level, semester_offered) is at or
             after 4Y2S -- where stream enrollment actually begins.
             Pre-stream courses (Years 1 through 4Y1S) are excluded
             entirely, even if they happen to be all-stream and droppable.

    SAME-PARITY RULE:
        A course at a semester-1 slot can only be moved to another
        semester-1 slot, and likewise for semester-2. This preserves
        the curriculum's parity discipline (courses offered in sem-1
        are offered in sem-1, etc.). The target slot has no lower bound
        -- a streaming-period course can be moved to any earlier
        same-parity slot if doing so relieves the imbalance and the
        prerequisite chain allows it.

    TRIGGER CONDITION:
        A move is only attempted when the source term's course count
        minus the target term's course count is >= 2. This guarantees
        the move reduces the source by 1 and increases the target by 1,
        making both counts converge by at least 1 -- a genuine,
        measurable relief. A difference of 1 would merely swap the
        imbalance from one term to the other.

    PREREQUISITE SAFETY:
        A candidate can be moved from slot S_src to slot S_dst only if:
          a. S_dst is a future slot that has the correct parity.
          b. Every prerequisite of the candidate is scheduled BEFORE
             S_dst (strictly, slot < S_dst) -- the candidate still
             can't start until its dependencies are done.
          c. No course that lists the candidate as a prerequisite is
             scheduled at or before S_dst -- the candidate must finish
             before any of its dependents start. Since the slot model
             uses strict ">" for prereqs, a dependent at slot D
             requires the candidate at slot D-1 or earlier; moving
             the candidate to S_dst is safe only if every dependent is
             at a slot STRICTLY AFTER S_dst.

    GREEDY ITERATION:
        The pass loops, each iteration scanning all (candidate, target)
        pairs and picking the move with the largest course-count
        differential. It stops when no qualifying move remains. This
        converges quickly because every move reduces at least one
        term's count.

    HARD RULES A MOVE MAY NEVER BREAK (the same rules CP-SAT enforces):
        d. CREDIT CAP: the destination must stay within its cap -- the
           semester's normal cap, or 22 for a Year-5 slot the solver
           ALREADY overloaded. Relief never creates a new overload.
        e. GATES: a move to a LATER slot must not push a major course past
           FYP-II (unless waived) or any course past the NEE, and must
           not extend graduation.

    NOTE: This function operates directly on the raw slot dict
    (code -> slot_number) returned by the solver, not on plan_by_term.
    It is a pure transformation: takes the schedule dict, returns a
    (possibly modified) copy. The caller (solve_schedule_for_student)
    applies it before building plan_by_term, so format_schedule.py
    sees the already-balanced result with no further changes needed.
    """
    # Work on a mutable copy -- never modify the solver's own output.
    schedule = dict(schedule)

    # Build a reverse map: for each course, which OTHER courses list it
    # as a prerequisite. Used for safety check (c).
    dependents_of = {code: [] for code in resolved}
    for code, info in resolved.items():
        for p in info.get("prereqs", []):
            if p in dependents_of:
                dependents_of[p].append(code)

    def _is_relief_candidate(code):
        info = resolved[code]
        if code not in CROWD_RELIEF_ELIGIBLE_COURSES:
            return False                          # relief is limited to one course
        if schedule.get(code, 0) <= now_slot:
            return False                          # historical / not planned
        if info.get("streams") is not None:
            return False                          # stream-specific course
        if not info.get("is_droppable", info.get("droppable", True)):
            return False                          # non-droppable (internship, FYP, NEE)
        _, sem = slot_to_year_sem(schedule[code])
        if sem not in (1, 2):
            return False                          # sem-3 (internship slot) -- skip
        if natural_slot_for_course(info) < stream_enrollment_floor_slot():
            return False                          # pre-stream course (before 4Y2S)
        return True

    normal_caps = normal_caps or {}
    waived = set(waived or ())
    slot_loads = {}

    def _cap_allows(code, s_dst):
        if not normal_caps:
            return True                       # no cap info supplied
        year, sem = slot_to_year_sem(s_dst)
        normal = normal_caps.get((year, sem), 0)
        cap = cap_for_slot(s_dst, normal_caps, year5_override=normal)
        if year == 5 and slot_loads.get(s_dst, 0) > normal:
            cap = max(cap, 22)                # overload already legitimately in use
        return slot_loads.get(s_dst, 0) + resolved[code]["credit_hours"] <= cap

    def _gates_allow(code, s_dst):
        if s_dst <= schedule[code]:
            return True                       # moving earlier can't break a gate
        if s_dst > max(schedule.values()):
            return False                      # would extend graduation
        c_nat = natural_slot_for_course(resolved[code])
        for f, fi in resolved.items():
            if f == code or f not in schedule:
                continue
            sr = fi.get("special_requirement")
            if sr == "ALL_COURSES" and s_dst > schedule[f]:
                return False
            if (sr == "ALL_STREAM_COURSES" and code not in waived
                    and _is_major_course(resolved[code])):
                same_natural = natural_slot_for_course(fi) == c_nat
                if s_dst > schedule[f] or (s_dst == schedule[f] and not same_natural):
                    return False
        return True

    def _move_is_safe(code, s_dst):
        """True if moving `code` to slot s_dst violates no prereq, cap or gate rule."""
        if not _cap_allows(code, s_dst) or not _gates_allow(code, s_dst):
            return False
        # (b) all prereqs must finish before s_dst
        for p in resolved[code].get("prereqs", []):
            if p in schedule and schedule[p] >= s_dst:
                return False
        # (c) all dependents of code must start after s_dst
        for dep in dependents_of.get(code, []):
            if dep in schedule and schedule[dep] <= s_dst:
                return False
        return True

    changed = True
    while changed:
        changed = False

        # Count courses per future slot (by slot number, not (year,sem))
        slot_counts = {}
        for code, s in schedule.items():
            if s > now_slot and resolved[code].get("special_requirement") != "ALL_COURSES":
                slot_counts[s] = slot_counts.get(s, 0) + 1   # NEE is a one-time exam, not counted
        slot_loads.clear()
        for code, s in schedule.items():
            if s > now_slot:
                slot_loads[s] = slot_loads.get(s, 0) + resolved[code]["credit_hours"]

        # Group future slots by their semester parity
        parity_slots = {}  # parity (1 or 2) -> sorted list of future slots
        for s in slot_counts:
            _, sem = slot_to_year_sem(s)
            if sem in (1, 2):
                parity_slots.setdefault(sem, []).append(s)
        for sem in parity_slots:
            parity_slots[sem].sort()

        # Find the best (most relieving) move among all candidates
        best_move = None   # (code, s_src, s_dst, differential)
        for code in list(schedule):
            if not _is_relief_candidate(code):
                continue
            s_src = schedule[code]
            _, parity = slot_to_year_sem(s_src)
            src_count = slot_counts.get(s_src, 0)

            for s_dst in parity_slots.get(parity, []):
                if s_dst == s_src:
                    continue
                dst_count = slot_counts.get(s_dst, 0)
                diff = src_count - dst_count
                if diff < 2:
                    continue            # trigger condition not met
                if not _move_is_safe(code, s_dst):
                    continue
                if best_move is None or diff > best_move[3]:
                    best_move = (code, s_src, s_dst, diff)

        if best_move is not None:
            code, s_src, s_dst, _ = best_move
            schedule[code] = s_dst
            changed = True

    return schedule


def solve_schedule_for_student(session, current_year, current_sem, stream_short_name,
                                 failed_course_codes, added_course_codes=None,
                                 dropped_course_codes=None):
    raw = load_raw_courses_from_db(session)
    resolved = filter_and_resolve_for_student(raw, stream_short_name)

    completed, now_slot, warnings = build_implicit_history(
        resolved, current_year, current_sem,
        set(failed_course_codes), added_course_codes, dropped_course_codes,
    )

    waiver_candidates = [c for c in _fyp2_waiver_candidates_for_stream(stream_short_name) if c in resolved]

    result = build_and_solve(
        courses=resolved,
        horizon_slots=SOLVE_HORIZON_SLOTS,
        policy_horizon_slots=POLICY_HORIZON_SLOTS,
        completed_courses=completed,
        now_slot=now_slot,
        fyp2_waivable_courses=waiver_candidates,
    )

    if not result["feasible"]:
        return {
            "feasible": False,
            "status": result.get("status"),
            "violations": result.get("violations", []),
            "warnings": warnings,
        }

    # Apply crowd relief: rebalance same-parity terms by moving eligible
    # courses from crowded semesters to lighter ones before rendering.
    # This is a post-processing pass -- the solver's constraint guarantees
    # are unchanged; crowd relief only touches droppable, all-stream courses
    # and only when a move is both safe (prereqs satisfied) and genuinely
    # reduces the imbalance by at least one course in each direction.
    schedule = apply_crowd_relief(
        result["schedule"], resolved, now_slot,
        normal_caps=result.get("normal_caps_used", {}),
        waived=result.get("fyp2_prereq_waivers_used", []),
    )

    plan_by_term = {}
    for code, slot in schedule.items():
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

    # fyp2_prereq_waivers: resolved to {code, name} for the explanation
    # format_schedule.py must show whenever this fired -- this is an
    # informal departmental nuance, not a written rule, so it can never
    # be applied without telling the student exactly what happened.
    fyp2_prereq_waivers = [
        {"code": c, "name": resolved[c]["name"]}
        for c in result.get("fyp2_prereq_waivers_used", [])
        if c in resolved
    ]

    # explain_context: a lightweight bundle the explainability layer needs.
    # Kept separate from the main return keys so format_schedule.py never
    # has to know it exists (it accesses only plan_by_term, graduation, etc.).
    explain_context = {
        "schedule": schedule,            # crowd-relief-applied {code: slot}
        "solver_schedule": result["schedule"],   # BEFORE crowd relief, so the explainer can tell
                                                 # solver placements from relief moves
        "courses": resolved,             # {code: info} with prereqs, credits…
        "completed": completed,          # {code: [{status, slot}]}
        "now_slot": now_slot,
        "normal_caps": result.get("normal_caps_used", {}),
        "waived": [w["code"] for w in fyp2_prereq_waivers],
    }

    return {
        "feasible": True,
        "stream": stream_short_name,
        "plan_by_term": plan_by_term,
        "graduation": {"year": grad_year, "semester": grad_sem},
        "graduation_slot": result["graduation_slot"],
        "exceeds_5_year_policy": result["exceeds_policy_horizon"],
        "fyp2_prereq_waivers": fyp2_prereq_waivers,
        "warnings": warnings,
        "explain_context": explain_context,
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
