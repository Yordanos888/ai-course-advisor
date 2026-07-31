from sqlalchemy.orm import sessionmaker
from sqlalchemy import case
from models import (
    engine, Student, Course, StudentCourseStatus,
    Prerequisite, Stream, Batch, CommonCourse, CourseStream
)

Session = sessionmaker(bind=engine)
session = Session()


# ============================================================
# Shared helpers
# ============================================================

def _resolve_course_stream_scope(course):
    """
    Returns the TRUE set of streams a course belongs to, correctly consulting
    BOTH tables:
      - course.stream_id: set when the course belongs to exactly one stream
      - course_streams: junction table for courses shared by SOME (not all) streams
    Returns None if the course is genuinely common to every stream, otherwise a
    set of stream_ids.
    """
    if course.stream_id is not None:
        return {course.stream_id}
    shared_entries = session.query(CourseStream).filter_by(course_id=course.id).all()
    if shared_entries:
        return {e.stream_id for e in shared_entries}
    return None  # truly common to all streams


def _stream_scope_label(course):
    scope = _resolve_course_stream_scope(course)
    if scope is None:
        return "Common (all streams)"
    names = [session.query(Stream).filter_by(id=sid).first().name for sid in scope]
    return ", ".join(sorted(names))


def _stream_scope_is_relevant(course):
    """Streams aren't chosen until Year 4 Semester 2 -- showing 'Stream Scope' before
    that point is meaningless noise, since no student has one to compare against yet."""
    return course.year_level > 4 or (course.year_level == 4 and course.semester_offered >= 2)


# ============================================================
# Student academic history
# ============================================================

def get_student_academic_summary(student_id):
    """
    Fetches a student and returns their current academic standing and a
    dictionary of their latest course statuses. Uses explicit ordering by
    year/semester/attempt so retakes deterministically overwrite older records.
    """
    student = session.query(Student).filter_by(id=student_id).first()
    if not student:
        return None, None, {}

    batch = session.query(Batch).filter_by(id=student.batch_id).first()
    current_year = batch.current_year_level if batch else 1
    current_sem = batch.current_semester if batch else 1

    records = (
        session.query(StudentCourseStatus)
        .filter_by(student_id=student_id)
        .order_by(
            StudentCourseStatus.academic_year_taken.asc(),
            StudentCourseStatus.semester_taken.asc(),
            case(
                (StudentCourseStatus.attempt_number == None, 0),
                else_=StudentCourseStatus.attempt_number
            ).asc()
        )
        .all()
    )

    history = {}
    for r in records:
        course = session.query(Course).filter_by(id=r.course_id).first()
        if not course:
            continue
        history[course.course_code.upper()] = r.status.upper()

    return student, (current_year, current_sem), history


def evaluate_special_requirements(student, course, history):
    """
    Evaluates courses with global structural requirements (ALL_STREAM_COURSES / ALL_COURSES).
    Returns (is_cleared, list_of_missing_requirements)
    """
    if not course.special_requirement:
        return True, []

    missing = []

    if course.special_requirement == "ALL_STREAM_COURSES":
        if not student.stream_id:
            return False, ["Student has not been assigned to an academic stream yet."]

        stream_courses = session.query(Course).filter_by(
            curriculum_version_id=course.curriculum_version_id,
            stream_id=student.stream_id
        ).all()

        for sc in stream_courses:
            if sc.id == course.id:
                continue
            sc_code = sc.course_code.upper()
            status = history.get(sc_code)
            if status != "PASSED":
                missing.append(f"{sc.course_code} ({sc.name}) [Status: {status or 'NOT TAKEN'}]")

    elif course.special_requirement == "ALL_COURSES":
        all_curriculum_courses = session.query(Course).filter_by(
            curriculum_version_id=course.curriculum_version_id
        ).all()

        for ac in all_curriculum_courses:
            if ac.id == course.id:
                continue
            if ac.stream_id and ac.stream_id != student.stream_id:
                continue
            ac_code = ac.course_code.upper()
            status = history.get(ac_code)
            if status != "PASSED":
                missing.append(f"{ac.course_code} [Status: {status or 'NOT TAKEN'}]")

    if missing:
        return False, missing
    return True, []


# ============================================================
# Course lookup (no student required)
# ============================================================

def get_course_details(course_code):
    """
    V1 Core: Retrieves isolated details about a specific course.
    - Department shown by name, not raw ID.
    - Prerequisites show course NAME alongside code.
    - Stream scope hidden before streams are actually chosen (Y4S2+).
    - Stream scope correctly consults course_streams for partial multi-stream sharing,
      not just course.stream_id.
    - special_requirement (ALL_STREAM_COURSES / ALL_COURSES) is surfaced as a
      readable prerequisite entry instead of showing "None".
    """
    course = session.query(Course).filter(Course.course_code.ilike(course_code)).first()
    if not course:
        return None

    prereqs = session.query(Prerequisite).filter_by(course_id=course.id).all()
    prereq_display = []
    for p in prereqs:
        p_course = session.query(Course).filter_by(id=p.prerequisite_course_id).first()
        if p_course:
            if p.applicable_stream_id:
                stream_name = session.query(Stream).filter_by(id=p.applicable_stream_id).first()
                stream_suffix = f" ({stream_name.name} only)" if stream_name else ""
            else:
                stream_suffix = ""
            prereq_display.append(f"{p_course.course_code} — {p_course.name}{stream_suffix}")

    if course.special_requirement == "ALL_STREAM_COURSES":
        prereq_display.append("All courses in your assigned stream must be PASSED")
    elif course.special_requirement == "ALL_COURSES":
        prereq_display.append("Every course in the curriculum must be PASSED")

    dept_name = course.department.name if course.department else "Common / College-wide"

    result = {
        "code": course.course_code,
        "name": course.name,
        "credit_hours": course.credit_hours,
        "year_level": course.year_level,
        "semester": course.semester_offered,
        "scope": dept_name,
        "prerequisites": prereq_display or ["None"],
    }

    if _stream_scope_is_relevant(course):
        result["stream"] = _stream_scope_label(course)
    else:
        result["stream"] = None  # bot.py should skip this line entirely when None

    return result


def get_downstream_impact(course_code):
    """
    V1 Core: Traces forward cascading blocks if a course is failed/not taken.
    A downstream course's reported stream-applicability is the INTERSECTION of:
      1. What the prerequisite edge itself implies (applicable_stream_id)
      2. What the downstream course's OWN scope is (stream_id / course_streams)
    This is necessary because a course can be scoped to specific streams via
    course_streams even when the edge pointing to it has no stream condition.
    """
    target_course = session.query(Course).filter(Course.course_code.ilike(course_code)).first()
    if not target_course:
        return None, []

    all_courses = {c.id: c for c in session.query(Course).all()}
    all_streams = {s.id: s.name for s in session.query(Stream).all()}

    edges = {}
    for p in session.query(Prerequisite).all():
        edges.setdefault(p.prerequisite_course_id, []).append((p.course_id, p.applicable_stream_id))

    edge_restriction = {target_course.id: None}  # None = no restriction implied by edges yet
    queue = [target_course.id]
    visited_via = {}

    while queue:
        current_id = queue.pop(0)
        current_restriction = edge_restriction[current_id]
        for dep_id, edge_stream_id in edges.get(current_id, []):
            if edge_stream_id is None:
                new_restriction = current_restriction
            elif current_restriction is None:
                new_restriction = {edge_stream_id}
            elif edge_stream_id in current_restriction:
                new_restriction = {edge_stream_id}
            else:
                continue  # this path doesn't apply to anyone affected upstream

            label = "ALL" if new_restriction is None else tuple(sorted(new_restriction))
            visited_via.setdefault(dep_id, set()).add(label)

            if dep_id not in edge_restriction:
                edge_restriction[dep_id] = new_restriction
                queue.append(dep_id)

    impacted_courses = []
    for cid, labels in visited_via.items():
        if cid not in all_courses:
            continue
        course = all_courses[cid]

        # restriction implied purely by the prerequisite edge(s)
        if "ALL" in labels:
            edge_stream_ids = None
        else:
            edge_stream_ids = set()
            for lbl in labels:
                edge_stream_ids.update(lbl)

        # restriction implied by the course's OWN scope (stream_id or course_streams)
        own_scope_ids = _resolve_course_stream_scope(course)

        # final applicable streams = intersection of the two
        if edge_stream_ids is None and own_scope_ids is None:
            final_ids = None
        elif edge_stream_ids is None:
            final_ids = own_scope_ids
        elif own_scope_ids is None:
            final_ids = edge_stream_ids
        else:
            final_ids = edge_stream_ids & own_scope_ids

        if final_ids is None:
            applies_to = "All streams"
        elif len(final_ids) == 0:
            applies_to = "⚠️ No streams (data inconsistency -- check curriculum entry)"
        else:
            applies_to = ", ".join(sorted(all_streams.get(sid, "Unknown") for sid in final_ids))

        impacted_courses.append({
            "course_code": course.course_code,
            "name": course.name,
            "year_level": course.year_level,
            "semester_offered": course.semester_offered,
            "applies_to_streams": applies_to,
        })

    impacted_courses.sort(key=lambda c: (c["year_level"], c["semester_offered"]))
    return target_course, impacted_courses


# ============================================================
# Student-specific eligibility / registration checks
# ============================================================

def get_eligible_courses(student_id):
    """Determines which courses a student is eligible to take in their current semester."""
    student, current_standing, history = get_student_academic_summary(student_id)
    if not student:
        print(f"❌ Student {student_id} not found.")
        return [], []

    target_year, target_sem = current_standing
    print(f"\n🔍 Evaluating eligibility for {student.name} ({student_id})")
    print(f"   Standing: Year {target_year}, Semester {target_sem} | Stream: {student.stream.name if student.stream else 'None'}")

    offered_courses = session.query(Course).filter_by(
        year_level=target_year, semester_offered=target_sem
    ).all()

    eligible = []
    blocked = []

    for course in offered_courses:
        is_stream_compatible = False
        scope = _resolve_course_stream_scope(course)
        if scope is None:
            is_stream_compatible = True
        elif student.stream_id in scope:
            is_stream_compatible = True

        if not is_stream_compatible:
            continue

        course_is_cleared = True
        missing_reasons = []

        prereqs = session.query(Prerequisite).filter_by(course_id=course.id).all()
        for p in prereqs:
            if p.applicable_stream_id is not None and p.applicable_stream_id != student.stream_id:
                continue
            prereq_course = session.query(Course).filter_by(id=p.prerequisite_course_id).first()
            if prereq_course:
                p_code = prereq_course.course_code.upper()
                status = history.get(p_code)
                if status != "PASSED":
                    course_is_cleared = False
                    missing_reasons.append(f"Prerequisite {p_code} is {status or 'NOT TAKEN'}")

        if course.special_requirement:
            special_cleared, special_missing = evaluate_special_requirements(student, course, history)
            if not special_cleared:
                course_is_cleared = False
                missing_reasons.extend(special_missing)

        if course_is_cleared:
            eligible.append(course)
        else:
            blocked.append((course, missing_reasons))

    print(f"   ✅ Eligible Courses:")
    for c in eligible:
        print(f"      - {c.course_code}: {c.name}")
    if blocked:
        print(f"   ❌ Blocked Courses:")
        for c, reasons in blocked:
            print(f"      - {c.course_code}: {c.name}")
            for r in reasons:
                print(f"         ⚠️ {r}")

    return eligible, blocked


def check_course_registration_violations(student_id, proposed_course_codes):
    """Evaluates a custom list of course codes a student wants to add."""
    student, _, history = get_student_academic_summary(student_id)
    if not student:
        return ["Student not found"]

    violations = []
    normalized_codes = [code.upper() for code in proposed_course_codes]

    for code_str in normalized_codes:
        course = session.query(Course).filter(Course.course_code.ilike(code_str)).first()
        if not course:
            violations.append(f"Course code '{code_str}' does not exist in curriculum.")
            continue

        prereqs = session.query(Prerequisite).filter_by(course_id=course.id).all()
        for p in prereqs:
            if p.applicable_stream_id is not None and p.applicable_stream_id != student.stream_id:
                continue
            prereq_course = session.query(Course).filter_by(id=p.prerequisite_course_id).first()
            if prereq_course:
                p_code = prereq_course.course_code.upper()
                if history.get(p_code) != "PASSED":
                    violations.append(f"Cannot take {course.course_code} because prerequisite {p_code} is not passed.")

        if course.special_requirement:
            special_cleared, special_missing = evaluate_special_requirements(student, course, history)
            if not special_cleared:
                for sm in special_missing:
                    violations.append(f"Cannot take {course.course_code}: Missing requirement {sm}")

    return violations


def get_common_course_offering_alternatives(course_code):
    course = session.query(Course).filter(Course.course_code.ilike(course_code)).first()
    if not course:
        return f"Course '{course_code}' not found."

    shares = session.query(CommonCourse).filter_by(course_id=course.id).all()
    if not shares:
        return f"Course '{course.course_code}' is not flagged as shared with other departments."

    return [s.context_note for s in shares]

def search_course_by_name(query_text):
    """
    Fuzzy course lookup by name/keywords, used as a fallback when no course
    CODE pattern is found in the user's message.
    """
    stopwords = {"the", "a", "an", "is", "for", "what", "course", "about", "of", "to", "in"}
    words = [w.strip(".,?!-_").lower() for w in query_text.split()]
    keywords = [w for w in words if w and w not in stopwords and len(w) > 0]
    if not keywords:
        return []

    all_courses = session.query(Course).all()
    scored = []
    for c in all_courses:
        name_lower = c.name.lower()
        match_count = sum(1 for kw in keywords if kw in name_lower)
        if match_count > 0:
            scored.append((match_count, c))

    scored.sort(key=lambda x: -x[0])
    return scored