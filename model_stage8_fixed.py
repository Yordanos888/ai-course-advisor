"""
STAGE 8a-FIX MODEL  (+ real-world corrections from live testing)
====================================================================
Fixes the behavioral finding from the first real-data run: the objective
inherited from Stage 1 (minimize total earliness / front-load everything
the explicit prerequisite chain allows) produced unrealistic plans on
real data, because the real curriculum's prerequisite edges are sparser
than our fake data's -- many later-year courses have NO explicit
prerequisite, so nothing stopped the old objective from pulling them
years early (e.g. the Industry Internship landing in Year 1).

TWO CHANGES FROM STAGE 6/7/8a's ORIGINAL OBJECTIVE:

  1. SECONDARY OBJECTIVE CHANGED: "minimize sum_of_slots" (front-load
     everything) -> "minimize total |deviation| from each course's own
     labeled (year_level, semester_offered)". A fresh, on-track student
     should get back exactly the normal curriculum. Deviation is still
     minimized when a real constraint forces it (a failure, a genuine
     credit-cap gap worth exploiting) -- the domino effect still works,
     it's just no longer the DEFAULT behavior for everyone.

  2. NEW HARD CONSTRAINT for non-droppable courses (per explicit
     confirmation): a non-droppable course (Internship, FYP-I, FYP-II,
     NEE) can NEVER be scheduled EARLIER than its own labeled slot --
     "fixed" means a hard floor, not just a soft preference.

FOUR MORE FIXES FROM REAL-WORLD TESTING FEEDBACK:

  3. NON-DROPPABLE COURSES NOW GET A DEDICATED, HIGH-PRIORITY OBJECTIVE
     TIER (not just the general deviation tier every course shares).
     Without this, the solver could trade "delay a non-droppable course
     by 1" for "save 3 slots of deviation on droppable electives" --
     net-favorable under a single combined deviation sum, but wrong: a
     non-droppable course must sit at its exact natural slot whenever
     its prerequisites allow it, full stop, never sacrificed to help
     something else's deviation.

  4. HARD FLOOR: no course belonging to a specific stream (or a subset
     of streams) can be scheduled before Year 4 Semester 2. In reality,
     stream assignment is an administrative decision the department
     makes AT that point, based on grades -- a student below it isn't
     enrolled in any stream yet, so a plan that pulls a stream course
     earlier is describing something that cannot actually happen, not
     just something suboptimal. Courses common to ALL streams are
     unaffected (they were never gated by stream enrollment to begin
     with).

  5. ALL_STREAM_COURSES REQUIREMENT FIXED (was a real bug): every OTHER
     course used to get a uniform ">=" against the special-requirement
     course (e.g. FYP-II) reasoning that same-slot peers are legitimate.
     True ONLY for a peer whose own natural slot actually equals the
     special-requirement course's natural slot (a genuinely
     concurrent-by-design final-term course). Any other peer -- even one
     that happens to LAND in the same slot after being pushed there by
     a cross-department parity flip -- must be STRICTLY before, since
     "all major courses done" means actually finished, not just
     technically unblocked. (Concretely: Software Engineering's natural
     slot is Year 5 Sem 1, FYP-II's is Year 5 Sem 2 -- under the old
     ">=" rule, SWE flipping onto Sem 2 parity could land in the SAME
     slot as FYP-II, which is wrong.)

  6. FLIP-MINIMIZATION TIE-BREAK: a cross-department parity flip is only
     used when it genuinely improves the plan (graduation time or
     natural-slot adherence) -- added as a low-priority objective tier
     so otherwise-equal plans prefer NOT using the flip, rather than the
     solver reaching for it with no real benefit.

TWO MORE FIXES FROM A LATER ROUND OF REAL-WORLD TESTING (retake behavior):

  7. RETAKE COURSES GET THEIR OWN, LOWER-PRIORITY DEVIATION TIER,
     strictly below the general on-track deviation tier (was previously
     folded in uniformly). "Stay close to your natural slot" is still a
     real, optimized preference for a retake -- it's just never allowed
     to outrank keeping an on-track (never-attempted) course at ITS
     natural slot. Without this split, a retake competing for room in
     some future occurrence of its parity-slot could bump an unrelated
     healthy course out of its own natural slot just to shave the
     retake's own deviation -- observed concretely on real data: a
     single failed Freshman course pulled toward its earliest legal
     retake slot displaced an unrelated Year-2 course, which then landed
     on FYP-II's own natural slot and triggered fix #8's bug below,
     costing the whole plan an entire extra graduation year over one
     failure. Splitting the tiers means the solver now prefers to push
     the retake itself further out rather than disturb anyone else.

  8. ALL_STREAM_COURSES SCOPE NARROWED TO GENUINE MAJOR COURSES (was a
     real bug, found via #7's investigation above): the peer loop used
     to run against EVERY course in the filtered curriculum, including
     university-wide courses taken BEFORE department enrollment (e.g. a
     Freshman English course). "All Stream Major Courses" (the actual
     campus-rule wording, confirmed against the source CSV) means the
     student's major coursework specifically. Left unscoped, any such
     course landing on FYP-II's own natural slot for any reason (a
     retake, a cross-department flip, anything) forces FYP-II strictly
     past it -- and since there are multiple semester-types per year,
     "strictly past" skips a full year rather than one term.

     Loaders now tag each course "is_major", and the peer loop skips
     non-major courses entirely. Loaders/fake data with no such concept
     default every course to major (True), so existing tests that never
     needed this distinction are unaffected. The real-data loaders
     derive "is_major" from ENROLLMENT TIMING, not course subject or
     the CSV's "Department Scope" column: per the campus doc, Year 1
     (both semesters) is the whole university's shared "Freshman year",
     and Year 2 Semester 1 is the shared "pre-Engineering" term before a
     department is even chosen -- nothing there is ECE major work yet,
     regardless of subject. "Department Scope" was tried first and
     rejected: it tracks who *administers* a course (e.g. Industry
     Internship is tagged "Common" because it's coordinated centrally by
     the university, despite being a core ECE requirement), not whether
     it belongs to the major -- it would have wrongly exempted Internship
     from this requirement while still gating on gen-ed courses.

  9. ALL_STREAM_COURSES '>=' CRITERION -- A "SAME HOME PARITY" RELAXATION
     WAS TRIED AND REVERTED (a genuine false start, kept here so it
     isn't tried again). The idea: exempt any peer sharing this course's
     own home parity (semester_offered) from the strict '>' rule, not
     just a peer matching its exact natural (year, sem) slot -- on the
     theory that a peer reaching this course's slot via its OWN ordinary
     parity (a retake, a prerequisite chain, a cap crunch) is different
     from one reaching it by exploiting a cross-department alt_parity
     flip (the actual exploit fix #4 was built to close), so only the
     latter should still need strict '>'.

     This conflated two different things: "won't collide via a parity
     exploit" is not the same claim as "safe to treat as already done."
     The requirement is genuine COMPLETION -- every major course actually
     finished and graded before this course starts -- not merely not
     landing on the same slot. Caught on a real case: Modern Control
     Systems (home parity 2, same as FYP-II) got delayed by a real
     prerequisite chain (a Comp2003 retake, no exploit anywhere in it)
     onto FYP-II's own natural slot. Under the parity-based relaxation,
     FYP-II was allowed to run concurrently with it -- but Modern
     Control Systems is still in progress at that point, so FYP-II
     genuinely hasn't cleared its "all major courses done" gate yet. The
     correct behavior is for FYP-II to wait a full extra year, exactly
     what the original natural-slot-equality rule already produced. That
     the next same-parity slot is a full year away rather than one term
     is a real, unavoidable consequence of the semester-type structure --
     not a parity-skip artifact to route around.

BACKWARD-COMPATIBILITY DESIGN: semester_types is now a PARAMETER, not a
hardcoded (1,2,3) constant. This lets Stage 1-7's existing fake data (all
built around a 2-semester-type system) be re-run through THIS model with
semester_types=(1,2) and produce IDENTICAL slot numbers to their original
hand-computed expected answers -- a real validation, not just an
assertion that "it should still work."
"""

from ortools.sat.python import cp_model
from model_stage4 import validate_retake_limits, summarize_course_history, MAX_ATTEMPTS_DEFAULT

CAP_FALLBACK_UNCONSTRAINED = 999


def slot_to_year_sem(slot, semester_types=(1, 2, 3)):
    types_per_year = len(semester_types)
    year = (slot - 1) // types_per_year + 1
    sem = semester_types[(slot - 1) % types_per_year]
    return year, sem


def natural_slot_for_course(course, semester_types=(1, 2, 3)):
    """The slot corresponding to this course's own labeled (year_level,
    semester_offered) -- its 'normal' position in an on-track plan."""
    types_per_year = len(semester_types)
    idx = semester_types.index(course["semester_offered"])
    return (course["year_level"] - 1) * types_per_year + idx + 1


def stream_enrollment_floor_slot(semester_types=(1, 2, 3)):
    """The slot for (Year 4, Semester 2) -- the earliest any
    stream-specific course can legally be scheduled, since that's when
    stream enrollment actually happens administratively."""
    return natural_slot_for_course({"year_level": 4, "semester_offered": 2}, semester_types)


def _is_major_course(course_info):
    """Whether a course counts as a 'stream major course' for the
    ALL_STREAM_COURSES requirement (FYP-II-style) -- i.e. genuinely part
    of the department's own curriculum, whether stream-specific or
    common to all streams within the department, as opposed to a
    generic university-wide/gen-ed course (e.g. a Freshman humanities or
    language course) that just happens to still be outstanding.

    Loaders that carry this distinction (db_loader.py, the Stage 8a/8b
    CSV loaders) set "is_major" explicitly. Older/fake data with no such
    concept defaults to True, so every course is still treated as a
    peer -- unchanged behavior for curricula that never needed this
    distinction in the first place.
    """
    return course_info.get("is_major", True)


def _course_stream_scope(course_info):
    """Checks both 'streams' (real data: a set/None) and 'stream' (some
    older fake data: a single string/None) for backward compatibility."""
    if "streams" in course_info:
        return course_info["streams"]
    return course_info.get("stream")


def compute_normal_caps(filtered_courses):
    caps = {}
    for info in filtered_courses.values():
        key = (info["year_level"], info["semester_offered"])
        caps[key] = caps.get(key, 0) + info["credit_hours"]
    return caps


BEYOND_YEAR5_CAP = 20  # credit-hour ceiling for any semester past year 5


def cap_for_slot(slot, normal_caps, semester_types=(1, 2, 3), year5_override=22):
    year, sem = slot_to_year_sem(slot, semester_types)
    normal_sum = normal_caps.get((year, sem), 0)
    # Beyond year 5 the curriculum labels no courses at all, so
    # normal_sum is always 0 there. Without this branch it would fall
    # through to CAP_FALLBACK_UNCONSTRAINED (effectively unlimited),
    # letting an overrun student be handed an impossible 30+ credit
    # term. Campus rule: 20 credit hours max per semester past year 5.
    if year > 5:
        return BEYOND_YEAR5_CAP
    if normal_sum == 0:
        return CAP_FALLBACK_UNCONSTRAINED
    if year == 5:
        return year5_override
    return normal_sum


def filter_courses_for_student(all_courses, student_stream):
    """
    Checks both "streams" (real data: a SET, supports multi-stream
    courses) and "stream" (Stage 5's original fake data: a single
    string) for backward compatibility during re-validation.
    """
    result = {}
    for c, info in all_courses.items():
        scope = _course_stream_scope(info)
        include = scope is None or student_stream in scope
        if include:
            result[c] = info
    return result


def valid_future_slots_for_course(course, horizon_slots, now_slot, semester_types=(1, 2, 3)):
    allowed_parities = {course["semester_offered"]}
    if course.get("alt_parity"):
        allowed_parities.add(course["alt_parity"])
    return [
        s for s in range(now_slot + 1, horizon_slots + 1)
        if slot_to_year_sem(s, semester_types)[1] in allowed_parities
    ]


def earliest_slot_at_or_after(min_slot, allowed_parities, horizon_slots, semester_types=(1, 2, 3)):
    for s in range(min_slot, horizon_slots + 1):
        if slot_to_year_sem(s, semester_types)[1] in allowed_parities:
            return s
    return None


def _build_and_solve_core(courses, horizon_slots, policy_horizon_slots, completed_courses, now_slot,
                            max_attempts, semester_types, normal_caps_override, verbose,
                            allow_overload, hard_grad_slot_cap=None, fyp2_waived_courses=None):
    """
    The actual model-building/solving logic, parameterized by:

    allow_overload: if False, Year 5 credit caps are FIXED at their
        normal value -- no overload boolean exists at all, so it's
        structurally impossible for the solver to use it. If True, each
        Year-5 slot gets a real decision (see the credit-cap section).

    hard_grad_slot_cap: if set, adds a HARD constraint that graduation
        must happen at or before this slot. Used by the public
        build_and_solve() wrapper to ask "CAN this student genuinely
        graduate within 5 years if the overload is allowed?" -- a
        feasibility question, not an optimization preference. If that
        question comes back infeasible, the overload doesn't actually
        rescue on-time completion and must not be used at all (see the
        two-phase orchestration in build_and_solve() below).

    fyp2_waived_courses: a set of course codes to exclude entirely from
        the ALL_STREAM_COURSES peer requirement for THIS solve, on top
        of the permanent is_major exclusion. See build_and_solve()'s
        FYP2_PREREQ_WAIVER escalation for what this represents and why
        it's applied only as a last resort, never by default.
    """
    completed_courses = completed_courses or {}
    fyp2_waived_courses = fyp2_waived_courses or set()

    violations = validate_retake_limits(courses, completed_courses, max_attempts)
    if violations:
        return {"feasible": False, "status": "RETAKE_LIMIT_EXCEEDED", "violations": violations}

    normal_caps = normal_caps_override if normal_caps_override is not None else compute_normal_caps(courses)

    post_hoc_codes = [c for c, info in courses.items() if info.get("special_requirement") == "ALL_COURSES"]
    main_codes = [c for c in courses if c not in post_hoc_codes]

    model = cp_model.CpModel()
    slot_of, assign, valid_slots, decided_codes = {}, {}, {}, []

    for c in main_codes:
        records = completed_courses.get(c, [])
        summary = summarize_course_history(records) if records else None
        if summary and summary["passed_slot"] is not None:
            slot_of[c] = model.NewConstant(summary["passed_slot"])
            continue
        decided_codes.append(c)
        valid_slots[c] = valid_future_slots_for_course(courses[c], horizon_slots, now_slot, semester_types)
        if not valid_slots[c]:
            raise ValueError(f"{c} has no legal future slot left before horizon ends")
        for s in valid_slots[c]:
            assign[c, s] = model.NewBoolVar(f"assign_{c}_{s}")
        model.Add(sum(assign[c, s] for s in valid_slots[c]) == 1)
        slot_of[c] = sum(s * assign[c, s] for s in valid_slots[c])

    # --- Ordinary prerequisites ---
    for c in main_codes:
        for p in courses[c]["prereqs"]:
            if p in slot_of:
                model.Add(slot_of[c] > slot_of[p])

    # --- ALL_STREAM_COURSES dynamic requirement (FYP-II-style) -- strict
    # '>' against every MAJOR peer EXCEPT one whose own natural slot
    # genuinely coincides with this course's natural slot (a real,
    # by-design concurrent final-term course). Generic gen-ed/common
    # pre-department courses are skipped entirely -- "All Stream Major
    # Courses" means the student's major coursework, not literally every
    # course still outstanding.
    #
    # A same-HOME-PARITY relaxation (>= for any peer sharing this
    # course's own semester_offered, not just its exact natural slot)
    # was tried and REVERTED: it conflated "won't collide via a parity
    # exploit" with "safe to run concurrently," but those aren't the
    # same thing. The requirement is genuine COMPLETION -- every major
    # course actually finished and graded -- not merely not colliding on
    # a slot. Confirmed wrong via a real case: Modern Control Systems
    # (home parity 2, same as FYP-II) got delayed by a real prerequisite
    # chain (a Comp2003 retake, no exploit) onto FYP-II's own natural
    # slot. Under the parity-based relaxation, FYP-II was allowed to run
    # concurrently with it -- but Modern Control Systems is still
    # in-progress at that point, so FYP-II genuinely can't have started
    # yet; the correct behavior IS for FYP-II to wait a full extra year,
    # exactly as the strict natural-slot rule already produced before
    # this reversion. That the next same-parity slot is a full year away
    # (not one term) is a real, unavoidable consequence of the
    # semester-type structure, not a bug to route around.
    #
    # fyp2_waived_courses (NEW): a handful of specific courses that the
    # department will, in practice, informally NOT insist on as a
    # completed prerequisite for FYP-II -- but only when the student
    # would otherwise miss the 5-year timeline, and never by default.
    # This is a per-solve-attempt override supplied by the caller (see
    # build_and_solve()'s escalation), not a standing property of any
    # course -- so it's applied here as a plain skip, same mechanism as
    # the is_major skip, just conditional on this specific solve attempt
    # rather than permanent. ---
    for c in main_codes:
        if courses[c].get("special_requirement") == "ALL_STREAM_COURSES":
            c_natural = natural_slot_for_course(courses[c], semester_types)
            for c2 in main_codes:
                if c2 == c:
                    continue
                if not _is_major_course(courses[c2]):
                    continue
                if c2 in fyp2_waived_courses:
                    continue
                c2_natural = natural_slot_for_course(courses[c2], semester_types)
                if c2_natural == c_natural:
                    model.Add(slot_of[c] >= slot_of[c2])
                else:
                    model.Add(slot_of[c] > slot_of[c2])

    # --- Hard floor for non-droppable courses ---
    natural_slots = {}
    non_droppable_decided = []
    for c in decided_codes:
        natural_slots[c] = natural_slot_for_course(courses[c], semester_types)
        is_droppable = courses[c].get("is_droppable", courses[c].get("droppable", True))
        if not is_droppable:
            model.Add(slot_of[c] >= natural_slots[c])
            non_droppable_decided.append(c)

    # --- Hard floor: stream-specific courses can't be scheduled before
    # stream enrollment actually happens (Year 4 Sem 2). ---
    stream_floor = stream_enrollment_floor_slot(semester_types)
    for c in decided_codes:
        if _course_stream_scope(courses[c]) is not None:
            model.Add(slot_of[c] >= stream_floor)

    # --- Credit caps ---
    # allow_overload=False: Year 5 caps are FIXED at their normal value --
    # no boolean, no headroom, structurally impossible to overload.
    # allow_overload=True: each Year-5 slot gets a real decision, and
    # using it is only ever rewarded by the caller's two-phase logic when
    # it's the difference between reaching the policy horizon or not
    # (see build_and_solve()).
    overload_bools = []
    for s in range(now_slot + 1, horizon_slots + 1):
        year, sem = slot_to_year_sem(s, semester_types)
        contributors = [c for c in decided_codes if s in valid_slots[c]]
        if not contributors:
            continue
        normal_sum = normal_caps.get((year, sem), 0)
        if allow_overload and year == 5 and normal_sum > 0:
            use_overload = model.NewBoolVar(f"use_overload_{s}")
            headroom = max(0, 22 - normal_sum)
            effective_cap = normal_sum + use_overload * headroom
            model.Add(
                sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= effective_cap
            )
            overload_bools.append(use_overload)
        else:
            # Route through cap_for_slot for every non-overload case, so
            # the year>5 ceiling and the empty-slot-type fallback are
            # both applied consistently. (Previously this used normal_sum
            # directly whenever it was non-zero, which meant the year>5
            # rule could never fire -- past year 5, normal_sum is always
            # 0 since no course is labeled there.)
            #
            # year5_override is pinned to normal_sum here: this branch is
            # the NO-overload path, so year 5 must get its plain normal
            # cap. Leaving cap_for_slot's default (22) would silently
            # re-enable the very overload that Phase 1 exists to forbid.
            cap = cap_for_slot(s, normal_caps, semester_types, year5_override=normal_sum)
            model.Add(sum(courses[c]["credit_hours"] * assign[c, s] for c in contributors) <= cap)
    overload_count = sum(overload_bools) if overload_bools else 0
    max_overload_count = max(len(overload_bools), 1)

    # --- Objective ---
    grad_slot = model.NewIntVar(1, horizon_slots, "grad_slot")
    for c in main_codes:
        model.Add(grad_slot >= slot_of[c])

    if hard_grad_slot_cap is not None:
        model.Add(grad_slot <= hard_grad_slot_cap)

    absdev = {}
    for c in decided_codes:
        signed = model.NewIntVar(-horizon_slots, horizon_slots, f"signeddev_{c}")
        model.Add(signed == slot_of[c] - natural_slots[c])
        dev = model.NewIntVar(0, horizon_slots, f"absdev_{c}")
        model.AddAbsEquality(dev, signed)
        absdev[c] = dev

    # Flip-avoidance: cross-department parity flip only used when it
    # genuinely improves grad_slot -- unaffected by the overload
    # two-phase logic, kept as its own tier right after grad_slot
    # (this part was already confirmed working correctly).
    flip_terms = []
    for c in decided_codes:
        alt = courses[c].get("alt_parity")
        if alt:
            flip_terms.append(sum(
                assign[c, s] for s in valid_slots[c]
                if slot_to_year_sem(s, semester_types)[1] == alt
            ))
    flip_count = sum(flip_terms) if flip_terms else 0
    max_flip_count = max(len(flip_terms), 1)

    non_droppable_dev_sum = sum(absdev[c] for c in non_droppable_decided) if non_droppable_decided else 0
    max_non_droppable_dev = horizon_slots * max(len(non_droppable_decided), 1)

    # RETAKE-DEVIATION TIER: a course currently being retaken (has a
    # FAILED or DROPPED record in its history) gets its OWN deviation
    # tier, ranked BELOW general deviation rather than folded in
    # uniformly. "Stay close to your natural slot" remains a real,
    # optimized preference for retakes -- it's just never allowed to
    # outrank keeping an on-track (never-attempted) course at its own
    # natural slot. Without this split, a retake competing for room in
    # some future occurrence of its parity-slot can bump an unrelated
    # healthy course out of ITS natural slot just to shave the retake's
    # own deviation -- a bad trade this tier ordering now forbids: any
    # displacement of an on-track course costs tier 4 (general_dev),
    # strictly above tier 5 (retake_dev), so the solver will always
    # prefer to push the retake itself further out instead.
    retaken_decided = {
        c for c in decided_codes
        if any(r.get("status") in ("FAILED", "DROPPED") for r in completed_courses.get(c, []))
    }
    general_dev_sum = sum(dev for c, dev in absdev.items() if c not in retaken_decided) if absdev else 0
    max_general_dev = horizon_slots * max(len(decided_codes), 1)

    retake_dev_sum = sum(absdev[c] for c in retaken_decided) if retaken_decided else 0
    max_retake_dev = horizon_slots * max(len(retaken_decided), 1)

    # Once overload is being used at all (Phase 2, hard_grad_slot_cap
    # set), minimize HOW MANY Year-5 slots need it -- use it as sparingly
    # as possible, right alongside flip-avoidance in priority.
    overload_term = overload_count if allow_overload else 0
    max_overload_term = max_overload_count if allow_overload else 1

    sorted_codes = sorted(main_codes)
    code_index = {c: i for i, c in enumerate(sorted_codes)}
    max_index = max(len(main_codes), 1)
    tie_break_term = sum((max_index - code_index[c]) * slot_of[c] for c in main_codes)
    max_possible_tie_break = max_index * horizon_slots * max(len(main_codes), 1)

    # Lexicographic weighting, outer tier dominates every inner one:
    # grad_slot > (flip+overload) > non_droppable_dev > general_dev > retake_dev > tie_break
    w_tie = 1
    w_retake_dev = (max_possible_tie_break + 1) * w_tie
    w_general_dev = (max_retake_dev + 1) * w_retake_dev
    w_non_droppable_dev = (max_general_dev + 1) * w_general_dev
    w_privilege = (max_non_droppable_dev + 1) * w_non_droppable_dev
    w_grad = (max_flip_count + max_overload_term + 1) * w_privilege

    model.Minimize(
        grad_slot * w_grad
        + (flip_count + overload_term) * w_privilege
        + non_droppable_dev_sum * w_non_droppable_dev
        + general_dev_sum * w_general_dev
        + retake_dev_sum * w_retake_dev
        + tie_break_term * w_tie
    )

    solver = cp_model.CpSolver()
    if verbose:
        solver.parameters.log_search_progress = True
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": False, "status": solver.StatusName(status)}

    schedule = {c: solver.Value(slot_of[c]) for c in main_codes}

    true_max_slot = max(schedule.values()) if schedule else now_slot
    for c in post_hoc_codes:
        allowed_parities = {courses[c]["semester_offered"]}
        if courses[c].get("alt_parity"):
            allowed_parities.add(courses[c]["alt_parity"])
        placed = earliest_slot_at_or_after(true_max_slot, allowed_parities, horizon_slots, semester_types)
        if placed is None:
            return {"feasible": False, "status": "CAPSTONE_UNSCHEDULABLE",
                     "violations": [f"{c}: no slot at or after {true_max_slot} within horizon."]}
        schedule[c] = placed

    graduation_slot = max(schedule.values()) if schedule else now_slot

    return {
        "feasible": True,
        "status": solver.StatusName(status),
        "schedule": schedule,
        "graduation_slot": graduation_slot,
        "normal_caps_used": normal_caps,
        "policy_horizon_slots": policy_horizon_slots,
        "exceeds_policy_horizon": graduation_slot > policy_horizon_slots,
    }


def build_and_solve(courses, horizon_slots, policy_horizon_slots=None, completed_courses=None, now_slot=0,
                     max_attempts=MAX_ATTEMPTS_DEFAULT, semester_types=(1, 2, 3),
                     normal_caps_override=None, verbose=False, fyp2_waivable_courses=None):
    """
    horizon_slots: the actual ceiling the solver is allowed to search
        within (variable domains are bounded here). Pass a generous value
        for real-data use (e.g. 10 years' worth of slots) so the solver
        can always find a genuine plan rather than reporting a bare
        infeasibility when a student's ACTUAL best path just takes longer
        than the target.

    policy_horizon_slots: the "official" limit to check compliance
        against (e.g. 15 for AASTU's 5-year policy). If None, defaults to
        horizon_slots itself.

    fyp2_waivable_courses: an ORDERED list of course codes the caller has
        identified as eligible, for THIS student's stream, for the
        FYP2_PREREQ_WAIVER escalation below (see solve_schedule.py for
        the real ECE list). Order is the caller's preference for which
        single course to try waiving first when more than one option
        exists. Pass None or [] for curricula with no such nuance.

    TWO-PHASE OVERLOAD LOGIC (per explicit real-world feedback): the
    Year-5 credit overload is only a legitimate privilege when it's
    specifically the difference between reaching the policy horizon and
    not -- "any improvement helps" is the WRONG criterion here (that's
    the rule for cross-department flips, which stayed a single-tier
    objective preference). Concretely:

      Phase 1: solve with NO overload possible at all. If that already
      reaches policy_horizon_slots, we're done -- overload was never
      needed, don't use it even if it's available.

      Phase 2 (only if Phase 1 exceeds the policy horizon): ask "CAN this
      student reach the policy horizon AT ALL if overload is allowed?" --
      a hard feasibility question (grad_slot <= policy_horizon_slots),
      not a soft preference. If YES, use that plan -- overload genuinely
      rescues on-time completion. If NO, fall back to Phase 1's honest
      result: overload doesn't help enough to matter, so it's not used,
      even though it might shave off a semester or two without actually
      reaching the target.

    FYP2_PREREQ_WAIVER ESCALATION (only reached if Phase 1 AND Phase 2
    both miss the policy horizon): a handful of specific courses are, in
    real departmental practice, sometimes informally NOT insisted on as
    a completed prerequisite for FYP-II -- a genuine but unofficial
    inconsistency the department applies, not a written curriculum rule.
    Per explicit instruction this is used under the exact same
    discipline as the overload: ONLY if, and only if, doing so is what
    actually closes the gap to the policy horizon -- never as a default,
    and never preferred over the overload (the overload is an actual
    documented campus rule; this waiver is not, so it's reached for only
    once the documented mechanism has already been tried and found
    insufficient). Escalates in order of how few courses it touches and
    how intrusive the accommodation is:

      1. Each candidate in fyp2_waivable_courses tried ALONE (in the
         caller's given order), overload still OFF.
      2. The full candidate set waived TOGETHER, overload still OFF.
      3. Each candidate tried ALONE again, this time WITH overload ON.
      4. The full candidate set waived TOGETHER, WITH overload ON.

    The first attempt that reaches the policy horizon under a hard cap
    (same feasibility-question discipline as the overload phase) wins.
    If none do, falls back to Phase 1's honest result -- the waiver
    doesn't help enough to matter, so it's not used, and no explanation
    is fabricated for a course that wasn't actually the blocker.

    Every returned result carries "fyp2_prereq_waivers_used": the exact
    list of course codes waived to reach this result (empty unless this
    escalation actually fired), so callers can build the required
    student-facing explanation -- this is a nuance, not a rule, so it
    must never be applied silently.
    """
    if policy_horizon_slots is None:
        policy_horizon_slots = horizon_slots
    fyp2_waivable_courses = list(dict.fromkeys(fyp2_waivable_courses or []))  # de-dup, keep order

    def _tag(result, waivers_used=()):
        result["fyp2_prereq_waivers_used"] = sorted(waivers_used)
        return result

    phase1 = _build_and_solve_core(
        courses, horizon_slots, policy_horizon_slots, completed_courses, now_slot,
        max_attempts, semester_types, normal_caps_override, verbose,
        allow_overload=False,
    )

    # Phase 1 infeasible does NOT mean "no plan exists" -- without the
    # overload, a plan may simply need more room than horizon_slots
    # allows, while WITH the overload it could fit (possibly even within
    # the policy horizon). Non-time failures (e.g. retake limit) are
    # returned as-is, since more credits per term can't fix those.
    if not phase1["feasible"]:
        if phase1.get("status") not in ("INFEASIBLE", "CAPSTONE_UNSCHEDULABLE"):
            return _tag(phase1)
        phase2_rescue = _build_and_solve_core(
            courses, horizon_slots, policy_horizon_slots, completed_courses, now_slot,
            max_attempts, semester_types, normal_caps_override, verbose,
            allow_overload=True,
        )
        return _tag(phase2_rescue) if phase2_rescue["feasible"] else _tag(phase1)

    if phase1["graduation_slot"] <= policy_horizon_slots:
        return _tag(phase1)

    phase2 = _build_and_solve_core(
        courses, horizon_slots, policy_horizon_slots, completed_courses, now_slot,
        max_attempts, semester_types, normal_caps_override, verbose,
        allow_overload=True, hard_grad_slot_cap=policy_horizon_slots,
    )
    if phase2["feasible"]:
        return _tag(phase2)

    if fyp2_waivable_courses:
        candidate_sets = [{c} for c in fyp2_waivable_courses]
        if len(fyp2_waivable_courses) > 1:
            candidate_sets.append(set(fyp2_waivable_courses))

        for use_overload in (False, True):
            for waived in candidate_sets:
                attempt = _build_and_solve_core(
                    courses, horizon_slots, policy_horizon_slots, completed_courses, now_slot,
                    max_attempts, semester_types, normal_caps_override, verbose,
                    allow_overload=use_overload, hard_grad_slot_cap=policy_horizon_slots,
                    fyp2_waived_courses=waived,
                )
                if attempt["feasible"]:
                    return _tag(attempt, waived)

    return _tag(phase1)


def solve_with_horizon_extension(courses, base_horizon, completed_courses=None, now_slot=0,
                                   max_attempts=MAX_ATTEMPTS_DEFAULT, semester_types=(1, 2, 3),
                                   normal_caps_override=None, extension_step=3, max_horizon=60):
    """
    Tries the mandated horizon (e.g. 5 years = 15 slots) first. If that's
    genuinely infeasible FOR A TIME REASON (ran out of horizon room --
    status INFEASIBLE or CAPSTONE_UNSCHEDULABLE), extends the horizon in
    steps and retries until a real plan is found or max_horizon is hit.

    Does NOT extend for RETAKE_LIMIT_EXCEEDED -- that's not a time
    problem, more time can't fix a student who is genuinely out of legal
    attempts on a required course, so extending would just waste solves
    and produce a misleading "just wait longer" implication.

    Always returns a usable result: either a plan within the mandated
    horizon (within_mandated_horizon=True), a plan that required more
    time than mandated (within_mandated_horizon=False, with an explicit
    message and how much extra time was needed), or a genuine
    non-time-related infeasibility (unchanged, no schedule to give).
    """
    result = build_and_solve(
        courses=courses, horizon_slots=base_horizon, completed_courses=completed_courses,
        now_slot=now_slot, max_attempts=max_attempts, semester_types=semester_types,
        normal_caps_override=normal_caps_override,
    )
    if result["feasible"]:
        result["within_mandated_horizon"] = True
        result["message"] = None
        return result

    if result.get("status") not in ("INFEASIBLE", "CAPSTONE_UNSCHEDULABLE"):
        # Not a time problem (e.g. retake limit exceeded) -- extending
        # the horizon cannot help. Report as-is.
        result["within_mandated_horizon"] = False
        result["message"] = (
            "This is not a time problem -- extending the horizon would not help. "
            "See the reported violations for the actual blocker."
        )
        return result

    horizon = base_horizon
    while horizon < max_horizon:
        horizon += extension_step
        result = build_and_solve(
            courses=courses, horizon_slots=horizon, completed_courses=completed_courses,
            now_slot=now_slot, max_attempts=max_attempts, semester_types=semester_types,
            normal_caps_override=normal_caps_override,
        )
        if result["feasible"]:
            base_years = base_horizon / len(semester_types)
            actual_years = result["graduation_slot"] / len(semester_types)
            result["within_mandated_horizon"] = False
            result["message"] = (
                f"5-year completion is NOT achievable given this student's history. "
                f"The earliest feasible completion found requires extending the plan to "
                f"approximately year {actual_years:.1f} -- "
                f"{actual_years - base_years:.1f} years beyond the mandated {base_years:.0f}-year limit. "
                f"This schedule is provided as the best achievable plan, not a guarantee "
                f"of on-time graduation."
            )
            return result

    return {
        "feasible": False,
        "status": "INFEASIBLE_EVEN_EXTENDED",
        "within_mandated_horizon": False,
        "message": f"No feasible completion found even extending to {max_horizon / len(semester_types):.0f} years.",
    }
