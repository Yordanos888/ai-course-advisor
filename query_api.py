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
        lines = ["🤔 *Multiple similar courses found. Please search again using the exact course code:*"]
        for opt in options:
            lines.append(f"• `{opt.course_code}`: {opt.name}")
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
        stream_label = _stream_scope_label(session, course) if _stream_scope_is_relevant(course) else "Common across all streams"

        lines = [
            f"📘 *{course.course_code}: {course.name}*",
            f"• *Credit Hours:* {course.credit_hours} cr",
            f"• *Schedule:* Year {course.year_level}, Semester {course.semester_offered}",
            f"• *Department Scope:* {dept_name}",
            f"• *Stream Scope:* {stream_label}",
        ]
        
        shared = session.query(CommonCourse).filter_by(course_id=course.id).all()
        if shared:
            notes = [sh.context_note for sh in shared if sh.context_note]
            if notes:
                lines.append(f"• *Cross-Dept Info:* {' | '.join(notes)}")

        lines.append("• *Prerequisites:*")
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
            return f"✅ *{target.course_code}: {target.name}* is a terminal course. Failing or dropping it does not block any direct downstream courses."

        impacted.sort(key=lambda x: (x[0], x[1], x[2]))
        lines = [f"⚠️ *Downstream Impact of Failing/Dropping {target.course_code}: {target.name}*\n"]
        for y, s, code, name, stream in impacted:
            lines.append(f"• Year {y}, Sem {s} — *{code}*: {name} `[{stream}]`")

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
            return "❌ Please provide valid numbers for year and semester (e.g., `/semester 4 2`)."

        courses = session.query(Course).filter_by(year_level=year, semester_offered=sem).order_by(Course.course_code).all()
        if not courses:
            return f"ℹ️ No courses registered for Year {year}, Semester {sem}."

        is_streaming_period = (year > 4) or (year == 4 and sem >= 2)

        # Handle Stream Selection Logic
        if is_streaming_period:
            if not stream_str:
                return (f"ℹ️ Year {year} Semester {sem} is stream-specific. Please specify your stream.\n"
                        f"Usage: `/semester {year} {sem} <stream_name>`\n"
                        f"Example: `/semester {year} {sem} Computer`")
            
            s_obj = session.query(Stream).filter(Stream.name.ilike(f"%{stream_str}%")).first()
            if not s_obj:
                return f"❌ Stream '{stream_str}' not recognized. Options: Computer, Communication, Control, Power."
            
            stream_courses = []
            for c in courses:
                scope = _resolve_course_stream_scope(session, c)
                if scope is None or s_obj.id in scope:
                    stream_courses.append(c)
                    
            courses = stream_courses  # Override with filtered list
            title = f"📅 *Curriculum for Year {year}, Semester {sem} ({s_obj.name})*"
        else:
            title = f"📅 *Curriculum for Year {year}, Semester {sem}*"

        total_credits = sum(c.credit_hours for c in courses)
        lines = [title, f"Total Courses: {len(courses)} | Total Credit Pool: {total_credits} cr\n"]

        for c in courses:
            note_str = ""
            shared = session.query(CommonCourse).filter_by(course_id=c.id).all()
            if shared:
                # Use context_note for cross-department definitions
                notes = [sh.context_note for sh in shared if sh.context_note]
                if notes:
                    note_str = f" `[Note: {' | '.join(notes)}]`"
            elif c.department_id is None:
                note_str = " `[Common College-wide]`"

            lines.append(f"• *{c.course_code}*: {c.name} ({c.credit_hours} cr){note_str}")

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
            return f"ℹ️ No direct courses list *{course.course_code}: {course.name}* as a prerequisite."

        lines = [f"🔗 *Courses directly requiring {course.course_code}: {course.name}*:\n"]
        for d in dependents:
            dep_c = session.query(Course).filter_by(id=d.course_id).first()
            if dep_c:
                stream_note = ""
                if d.applicable_stream_id:
                    s = session.query(Stream).filter_by(id=d.applicable_stream_id).first()
                    if s:
                        stream_note = f" `[{s.name.replace(' Engineering', '')} only]`"
                lines.append(f"• Year {dep_c.year_level}, Sem {dep_c.semester_offered} — *{dep_c.course_code}*: {dep_c.name}{stream_note}")

        return "\n".join(lines)
    finally:
        session.close()

def get_cross_department_formatted() -> str:
    session = Session()
    try:
        common_links = session.query(CommonCourse).all()
        if not common_links:
            return "ℹ️ No cross-department courses configured."

        lines = ["🏢 *Cross-Department Shared Courses*:\n"]
        seen = set()
        for link in common_links:
            c = session.query(Course).filter_by(id=link.course_id).first()
            if c:
                key = c.course_code
                if key not in seen:
                    seen.add(key)
                    note = f" — Note: {link.context_note}" if link.context_note else ""
                    lines.append(f"• *{c.course_code}*: {c.name}{note}")

        return "\n".join(lines)
    finally:
        session.close()

def get_cross_stream_formatted() -> str:
    session = Session()
    try:
        shared_entries = session.query(CourseStream).all()
        course_ids = {e.course_id for e in shared_entries}
        if not course_ids:
            return "ℹ️ No multi-stream shared courses found."

        lines = ["🔀 *Courses Shared Between Specific Streams (Y4S2+)*:\n"]
        for cid in sorted(course_ids):
            c = session.query(Course).filter_by(id=cid).first()
            if c:
                streams = _stream_scope_label(session, c)
                lines.append(f"• Year {c.year_level}, Sem {c.semester_offered} — *{c.course_code}*: {c.name} `[{streams}]`")

        return "\n".join(lines)
    finally:
        session.close()