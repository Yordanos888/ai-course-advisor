import re
import difflib
from sqlalchemy.orm import sessionmaker
from sqlalchemy import case, func
from models import (
    engine, Student, Course, StudentCourseStatus,
    Prerequisite, Stream, Batch, CommonCourse, CourseStream, Department
)

Session = sessionmaker(bind=engine)

# ============================================================
# Core Course & Stream Resolvers
# ============================================================

def _resolve_course_stream_scope(session, course):
    """Returns the TRUE set of stream IDs a course belongs to, or None if common to all."""
    if course.stream_id is not None:
        return {course.stream_id}
    shared_entries = session.query(CourseStream).filter_by(course_id=course.id).all()
    if shared_entries:
        return {e.stream_id for e in shared_entries}
    return None

def _stream_scope_label(session, course):
    scope = _resolve_course_stream_scope(session, course)
    if scope is None:
        return "Common (all streams)"
    names = [session.query(Stream).filter_by(id=sid).first().name.replace(" Engineering", "") for sid in scope]
    return ", ".join(sorted(names))

def _stream_scope_is_relevant(course):
    """Streams split starting at Year 4 Semester 2."""
    return course.year_level > 4 or (course.year_level == 4 and course.semester_offered >= 2)

def search_course_by_name(session, query_text):
    """Fuzzy course lookup by name using difflib for spelling tolerance."""
    query_lower = query_text.lower().strip()
    all_courses = session.query(Course).all()
    
    scored = []
    for c in all_courses:
        name_lower = c.name.lower()
        # Calculate sequence similarity ratio (0.0 to 1.0)
        ratio = difflib.SequenceMatcher(None, query_lower, name_lower).ratio()
        
        # Boost ratio heavily if it's a direct substring (e.g., "signals" in "Signals and Systems")
        if query_lower in name_lower and len(query_lower) > 3:
            ratio = max(ratio, 0.85)
            
        if ratio > 0.45:  # Tolerance threshold
            scored.append((ratio, c))

    scored.sort(key=lambda x: -x[0])
    return [c for score, c in scored]

def resolve_or_disambiguate(session, query_str: str):
    """
    Attempts to resolve a user query (code or name) to a single Course object.
    Returns: (Target_Course, None) if exactly one match.
             (None, List_of_Courses) if ambiguous fuzzy matches exist.
             (None, []) if not found at all.
    """
    query_str = query_str.strip()
    
    # 1. Direct course code match (highest priority)
    code_match = re.search(r'[A-Za-z]{3,4}g?\s*-?\s*\d{4}', query_str)
    if code_match:
        normalized_code = re.sub(r"[\s-]", "", code_match.group(0)).upper()
        c = session.query(Course).filter(Course.course_code.ilike(normalized_code)).first()
        if c:
            return c, None

    # 2. Fuzzy search match with spelling tolerance
    matches = search_course_by_name(session, query_str)
    if not matches:
        return None, []

    # If the top match is highly confident or is the only match, return it
    if len(matches) == 1 or difflib.SequenceMatcher(None, query_str.lower(), matches[0].name.lower()).ratio() > 0.8:
        return matches[0], None
        
    # Otherwise, return the top options (up to 5) for the user to choose from
    return None, matches[:5]

def resolve_course_entity(session, query_str: str):
    """Wrapper for orchestrator intake: returns exact match or None."""
    course, options = resolve_or_disambiguate(session, query_str)
    return course

# ============================================================
# Command Formatters (Direct Bot Handlers)
# ============================================================

def _handle_disambiguation_message(query_str: str, options: list) -> str:
    """Helper to format the disambiguation prompt when search results are fuzzy."""
    if options:
        lines = ["🤔 <b>Multiple similar courses found. Please search again using the exact course code:</b>"]
        for opt in options:
            lines.append(f"• <code>{opt.course_code}</code>: {opt.name}")
        return "\n".join(lines)
    return f"❌ Course '{query_str}' not found or not relevant in the catalog."

def get_course_details_formatted(query_str: str) -> str:
    session = Session()
    try:
        course, options = resolve_or_disambiguate(session, query_str)
        if not course:
            return _handle_disambiguation_message(query_str, options)

        prereqs = session.query(Prerequisite).filter_by(course_id=course.id).all()
        prereq_display = []
        for p in prereqs:
            p_course = session.query(Course).filter_by(id=p.prerequisite_course_id).first()
            if p_course:
                stream_suffix = ""
                if p.applicable_stream_id:
                    s_name = session.query(Stream).filter_by(id=p.applicable_stream_id).first()
                    stream_suffix = f" ({s_name.name.replace(' Engineering', '')} only)" if s_name else ""
                prereq_display.append(f"• {p_course.course_code}: {p_course.name}{stream_suffix}")

        if course.special_requirement == "ALL_STREAM_COURSES":
            prereq_display.append("• Special Requirement: All prior major courses in your stream must be PASSED")
        elif course.special_requirement == "ALL_COURSES":
            prereq_display.append("• Special Requirement: Every course in the entire curriculum must be PASSED")

        dept_name = course.department.name if course.department else "Common / College-wide"

        # ALWAYS dynamically computed (no hardcoded "pre-streaming years
        # are always common" shortcut) -- and given a clearly PROMINENT,
        # unmistakable line when the course is genuinely common to every
        # stream, rather than being just one plain bullet among several.
        scope = _resolve_course_stream_scope(session, course)

        lines = [
            f"📘 <b>{course.course_code}: {course.name}</b>",
            f"• <b>Credit Hours:</b> {course.credit_hours} cr",
            f"• <b>Schedule:</b> Year {course.year_level}, Semester {course.semester_offered}",
            f"• <b>Department Scope:</b> {dept_name}",
        ]

        if scope is None:
            lines.append("🌐 <b>Common to ALL streams</b> — every student takes this course regardless of stream.")
        else:
            lines.append(f"• <b>Stream Scope:</b> {_stream_scope_label(session, course)}")

        shared = session.query(CommonCourse).filter_by(course_id=course.id).all()
        if shared:
            notes = [sh.context_note for sh in shared if sh.context_note]
            if notes:
                lines.append(f"• <b>Cross-Dept Info:</b> {' | '.join(notes)}")

        lines.append("• <b>Prerequisites:</b>")
        if prereq_display:
            lines.extend(prereq_display)
        else:
            lines.append("  None")

        return "\n".join(lines)
    finally:
        session.close()

def get_downstream_impact_formatted(query_str: str) -> str:
    session = Session()
    try:
        target, options = resolve_or_disambiguate(session, query_str)
        if not target:
            return _handle_disambiguation_message(query_str, options)

        all_courses = {c.id: c for c in session.query(Course).all()}
        all_streams = {s.id: s.name.replace(" Engineering", "") for s in session.query(Stream).all()}

        edges = {}
        for p in session.query(Prerequisite).all():
            edges.setdefault(p.prerequisite_course_id, []).append((p.course_id, p.applicable_stream_id))

        edge_restriction = {target.id: None}
        queue = [target.id]
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
                    continue

                label = "ALL" if new_restriction is None else tuple(sorted(new_restriction))
                visited_via.setdefault(dep_id, set()).add(label)

                if dep_id not in edge_restriction:
                    edge_restriction[dep_id] = new_restriction
                    queue.append(dep_id)

        impacted = []
        for cid, labels in visited_via.items():
            if cid not in all_courses:
                continue
            course = all_courses[cid]
            edge_stream_ids = None if "ALL" in labels else set().union(*labels)
            own_scope_ids = _resolve_course_stream_scope(session, course)

            if edge_stream_ids is None and own_scope_ids is None:
                final_ids = None
            elif edge_stream_ids is None:
                final_ids = own_scope_ids
            elif own_scope_ids is None:
                final_ids = edge_stream_ids
            else:
                final_ids = edge_stream_ids & own_scope_ids

            applies_to = "All streams" if final_ids is None else ", ".join(sorted(all_streams.get(sid, "Unknown") for sid in final_ids))
            impacted.append((course.year_level, course.semester_offered, course.course_code, course.name, applies_to))

        if not impacted:
            return f"✅ <b>{target.course_code}: {target.name}</b> is a terminal course. Failing or dropping it does not block any direct downstream courses."

        impacted.sort(key=lambda x: (x[0], x[1], x[2]))
        lines = [f"⚠️ <b>Downstream Impact of Failing/Dropping {target.course_code}: {target.name}</b>\n"]
        for y, s, code, name, stream in impacted:
            lines.append(f"• Year {y}, Sem {s} — <b>{code}</b>: {name} <code>[{stream}]</code>")

        return "\n".join(lines)
    finally:
        session.close()

def get_semester_courses_formatted(year_str: str, sem_str: str, stream_str: str = None) -> str:
    session = Session()
    try:
        try:
            year = int(year_str)
            sem = int(sem_str)
        except ValueError:
            return "❌ Please provide valid numbers for year and semester (e.g., <code>/semester 4 2</code>)."

        courses = session.query(Course).filter_by(year_level=year, semester_offered=sem).order_by(Course.course_code).all()
        if not courses:
            return f"ℹ️ No courses registered for Year {year}, Semester {sem}."

        is_streaming_period = (year > 4) or (year == 4 and sem >= 2)

        # Handle Stream Selection Logic
        if is_streaming_period:
            if not stream_str:
                return (f"ℹ️ Year {year} Semester {sem} is stream-specific. Please specify your stream.\n"
                        f"Usage: <code>/semester {year} {sem} &lt;stream_name&gt;</code>\n"
                        f"Example: <code>/semester {year} {sem} Computer</code>")
            
            s_obj = session.query(Stream).filter(Stream.name.ilike(f"%{stream_str}%")).first()
            if not s_obj:
                return f"❌ Stream '{stream_str}' not recognized. Options: Computer, Communication, Control, Power."
            
            stream_courses = []
            for c in courses:
                scope = _resolve_course_stream_scope(session, c)
                if scope is None or s_obj.id in scope:
                    stream_courses.append(c)
                    
            courses = stream_courses  # Override with filtered list
            title = f"📅 <b>Curriculum for Year {year}, Semester {sem} ({s_obj.name})</b>"
        else:
            title = f"📅 <b>Curriculum for Year {year}, Semester {sem}</b>"

        total_credits = sum(c.credit_hours for c in courses)
        lines = [title, f"Total Courses: {len(courses)} | Total Credit Pool: {total_credits} cr\n"]

        for c in courses:
            notes = []

            shared = session.query(CommonCourse).filter_by(course_id=c.id).all()
            if shared:
                cross_dept_notes = [sh.context_note for sh in shared if sh.context_note]
                if cross_dept_notes:
                    notes.append(f"Note: {' | '.join(cross_dept_notes)}")
            elif c.department_id is None:
                notes.append("Common College-wide")

            # Acknowledge courses shared across a SUBSET of streams (not
            # all, not zero) the same way cross-department sharing is
            # acknowledged above -- a course can be BOTH cross-department
            # and cross-stream, so this appends rather than overrides.
            if c.stream_id is None:
                scope = _resolve_course_stream_scope(session, c)
                if scope is not None:
                    notes.append(f"Shared streams: {_stream_scope_label(session, c)}")

            note_str = f" <code>[{' ; '.join(notes)}]</code>" if notes else ""
            lines.append(f"• <b>{c.course_code}</b>: {c.name} ({c.credit_hours} cr){note_str}")

        return "\n".join(lines).strip()
    finally:
        session.close()

def get_dependant_courses_formatted(query_str: str) -> str:
    session = Session()
    try:
        course, options = resolve_or_disambiguate(session, query_str)
        if not course:
            return _handle_disambiguation_message(query_str, options)

        dependents = session.query(Prerequisite).filter_by(prerequisite_course_id=course.id).all()
        if not dependents:
            return f"ℹ️ No direct courses list <b>{course.course_code}: {course.name}</b> as a prerequisite."

        lines = [f"🔗 <b>Courses directly requiring {course.course_code}: {course.name}</b>:\n"]
        for d in dependents:
            dep_c = session.query(Course).filter_by(id=d.course_id).first()
            if dep_c:
                stream_note = ""
                if d.applicable_stream_id:
                    s = session.query(Stream).filter_by(id=d.applicable_stream_id).first()
                    if s:
                        stream_note = f" <code>[{s.name.replace(' Engineering', '')} only]</code>"
                lines.append(f"• Year {dep_c.year_level}, Sem {dep_c.semester_offered} — <b>{dep_c.course_code}</b>: {dep_c.name}{stream_note}")

        return "\n".join(lines)
    finally:
        session.close()

def get_cross_department_formatted() -> str:
    session = Session()
    try:
        common_links = session.query(CommonCourse).all()
        if not common_links:
            return "ℹ️ No cross-department courses configured."

        lines = ["🏢 <b>Cross-Department Shared Courses</b>:\n"]
        seen = set()
        for link in common_links:
            c = session.query(Course).filter_by(id=link.course_id).first()
            if c:
                key = c.course_code
                if key not in seen:
                    seen.add(key)
                    note = f" — Note: {link.context_note}" if link.context_note else ""
                    lines.append(f"• <b>{c.course_code}</b>: {c.name}{note}")

        return "\n".join(lines)
    finally:
        session.close()

def get_cross_stream_formatted() -> str:
    session = Session()
    try:
        # Fetch all courses that fall within the stream-specific period (Y4S2 and above)
        streaming_courses = session.query(Course).filter(
            (Course.year_level > 4) | 
            ((Course.year_level == 4) & (Course.semester_offered >= 2))
        ).all()
        
        shared_courses = []
        for c in streaming_courses:
            # If stream_id is None, it means it is shared (either by a subset of streams via 
            # course_streams, or by ALL streams).
            if c.stream_id is None:
                shared_courses.append(c)
                
        if not shared_courses:
            return "ℹ️ No multi-stream shared courses found in the streaming period."

        # Sort the courses chronologically, then alphabetically
        shared_courses.sort(key=lambda x: (x.year_level, x.semester_offered, x.course_code))

        lines = ["🔀 <b>Courses Shared Between Streams (Year 4 Sem 2 & Year 5)</b>:\n"]
        for c in shared_courses:
            streams = _stream_scope_label(session, c)
            lines.append(f"• Year {c.year_level}, Sem {c.semester_offered} — <b>{c.course_code}</b>: {c.name} <code>[{streams}]</code>")

        return "\n".join(lines)
    finally:
        session.close()