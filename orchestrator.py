# orchestrator.py
import re
import sqlite3
import json
from rag_service import get_educational_context
from query_api import get_course_details, get_downstream_impact, search_course_by_name
from llm_client import generate_response
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from models import engine, Course, Stream, CourseStream, Department, CommonCourse, Prerequisite

Session = sessionmaker(bind=engine)


# ============================================================
# FIX: single source of truth for "does this course belong to stream X",
# reused everywhere instead of two divergent implementations (one correct,
# one buggy). Previously the single-stream branch used a raw SQL filter with
# a blanket `Course.stream_id == None` clause, which swept in EVERY
# null-stream course -- including ones scoped to a DIFFERENT subset of
# streams via course_streams (e.g. Instrumentation Engineering, which is
# Control+Power only, was incorrectly showing up under Computer-stream
# queries). The multi-stream comparison branch already had the correct
# per-course membership check; this fix just makes everyone use it.
# ============================================================
def _course_stream_membership(session, course):
    """Returns None if the course is truly common to ALL streams, otherwise
    the set of stream_ids it actually belongs to."""
    if course.stream_id:
        return {course.stream_id}
    entries = session.query(CourseStream).filter_by(course_id=course.id).all()
    if entries:
        return {e.stream_id for e in entries}
    return None  # no stream_id AND no course_streams rows -- truly common


def _course_belongs_to_all_streams(session, course, required_stream_ids):
    """True if the course applies to every stream in required_stream_ids."""
    membership = _course_stream_membership(session, course)
    if membership is None:
        return True  # common to everyone
    return all(sid in membership for sid in required_stream_ids)


def _resolve_general_sql_query(user_query: str) -> str:
    """
    Handles general catalog questions that do NOT name a specific course.
    Returns a structured context string, or None if no known pattern matched
    (callers should fall back to Text-to-SQL in that case).
    """
    session = Session()
    try:
        q = user_query.lower()

        # ------------------------------------------------------------------
        # 0. Reverse Prerequisite Lookup (e.g., "for which course is X a prereq?")
        # ------------------------------------------------------------------
        if any(w in q for w in ["prerequisite", "prereq", "requirement"]) and any(w in q for w in ["for which", "what course", "which course"]):
            potential_codes = re.findall(r'[A-Za-z]{3,4}g?\s*-?\s*\d{4}', q)
            normalized_codes = [re.sub(r"[\s-]", "", c).upper() for c in potential_codes]

            tp_obj = None
            if normalized_codes:
                tp_obj = session.query(Course).filter(Course.course_code.ilike(normalized_codes[0])).first()

            if not tp_obj:
                matches = search_course_by_name(user_query)
                if matches:
                    tp_obj = matches[0][1]

            if tp_obj:
                dependents = session.query(Prerequisite).filter_by(prerequisite_course_id=tp_obj.id).all()
                if dependents:
                    lines = []
                    for d in dependents:
                        dep_c = session.query(Course).filter_by(id=d.course_id).first()
                        if dep_c:
                            stream_note = ""
                            if d.applicable_stream_id:
                                s = session.query(Stream).filter_by(id=d.applicable_stream_id).first()
                                if s:
                                    stream_note = f" ({s.name} only)"
                            lines.append(f"- {dep_c.course_code}: {dep_c.name}{stream_note}")
                    return f"The course `{tp_obj.course_code}: {tp_obj.name}` is a prerequisite for:\n" + "\n".join(lines)
                else:
                    return f"I couldn't find any courses that require `{tp_obj.course_code}: {tp_obj.name}` as a prerequisite."

        # ------------------------------------------------------------------
        # 1. Catalog Query (Year, Semester, Stream)
        # ------------------------------------------------------------------
        year = None
        sem = None
        stream_display_name = None

        y_match = re.search(r'\b(1st|first|2nd|second|3rd|third|4th|fourth|5th|fifth)\s*[- ]?\s*year\b', q)
        if y_match:
            y_map = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4, "5th": 5, "fifth": 5}
            year = y_map[y_match.group(1)]
        else:
            y_match = re.search(r'year\s*(\d)', q)
            if y_match:
                year = int(y_match.group(1))

        s_match = re.search(r'\b(1st|first|2nd|second)\s*[- ]?\s*sem(?:ester)?\b', q)
        if s_match:
            s_map = {"1st": 1, "first": 1, "2nd": 2, "second": 2}
            sem = s_map[s_match.group(1)]
        else:
            s_match = re.search(r'sem(?:ester)?\s*(\d)', q)
            if s_match:
                sem = int(s_match.group(1))

        stream_keywords = {"computer": "Computer", "communication": "Communication",
                            "control": "Control", "power": "Power"}
        found_streams = []
        for keyword, name in stream_keywords.items():
            if keyword in q:
                s_obj = session.query(Stream).filter(Stream.name.ilike(f"%{name}%")).first()
                if s_obj:
                    found_streams.append((s_obj, name))

        is_shared_query = any(w in q for w in ["share", "common", "between", "cross", "both"])

        if year is not None or sem is not None or found_streams:
            base_query = session.query(Course)
            if year is not None:
                base_query = base_query.filter_by(year_level=year)
            if sem is not None:
                base_query = base_query.filter_by(semester_offered=sem)
            candidates = base_query.all()

            if found_streams:
                stream_ids = [s[0].id for s in found_streams]
                stream_display_name = " and ".join(s[1] for s in found_streams)
                # FIX: use the single correct membership check for BOTH the
                # single-stream and multi-stream cases -- no more divergent logic.
                courses = [c for c in candidates if _course_belongs_to_all_streams(session, c, stream_ids)]
            else:
                courses = candidates
            courses.sort(key=lambda c: (c.year_level, c.semester_offered, c.course_code))

            if is_shared_query:
                final_list = []
                for c in courses:
                    is_shared_stream = (c.stream_id is None)
                    is_shared_dept = (c.department_id is None) or session.query(CommonCourse).filter_by(course_id=c.id).first() is not None
                    if is_shared_stream or is_shared_dept:
                        final_list.append(c)
                courses = final_list

            if courses:
                lines = []
                for c in courses:
                    dept_code = c.department.code if c.department else "Common"
                    share_note = ""
                    if is_shared_query:
                        others = session.query(CommonCourse).filter_by(course_id=c.id).all()
                        if others:
                            other_depts = [session.query(Department).filter_by(id=o.shared_with_department_id).first().code for o in others]
                            share_note = f" [Shared with: {', '.join(other_depts)}]"
                        elif c.department_id is None:
                            share_note = " [Shared with all Departments]"
                    lines.append(
                        f"- {c.course_code}: {c.name} ({c.credit_hours} cr) — "
                        f"Year {c.year_level}, Sem {c.semester_offered} [{dept_code}]{share_note}"
                    )

                header_parts = []
                if stream_display_name:
                    header_parts.append(f"the {stream_display_name} Engineering stream(s)")
                if year:
                    header_parts.append(f"Year {year}")
                if sem:
                    header_parts.append(f"Semester {sem}")
                if is_shared_query:
                    header_parts.append("shared/common courses")

                header = "Catalog listing for " + ", ".join(header_parts)
                return header + ":\n" + "\n".join(lines)

        # ------------------------------------------------------------------
        # 3. Total curriculum statistics
        # ------------------------------------------------------------------
        if any(phrase in q for phrase in ["total credit", "how many credit", "credit hours total", "sum of credit"]):
            total_credits = session.query(func.sum(Course.credit_hours)).scalar() or 0
            count = session.query(Course).count()
            return f"The ECE curriculum contains {count} courses with a total of {int(total_credits)} credit hours."

        # ------------------------------------------------------------------
        # 4. Common / cross-department courses
        # ------------------------------------------------------------------
        if "common" in q and ("department" in q or "between" in q or "shared" in q):
            common_links = session.query(CommonCourse).all()
            if common_links:
                lines = []
                seen = set()
                for link in common_links:
                    c = session.query(Course).filter_by(id=link.course_id).first()
                    d = session.query(Department).filter_by(id=link.shared_with_department_id).first()
                    if c and d:
                        key = (c.course_code, d.code)
                        if key not in seen:
                            seen.add(key)
                            lines.append(f"- {c.course_code}: {c.name} (shared with {d.code})")
                if lines:
                    return "Cross-department common courses:\n" + "\n".join(lines)

        return None
    finally:
        session.close()


# ============================================================
# NEW: Text-to-SQL fallback -- only used when NOTHING above matched.
# Read-only by construction (opens the SQLite file in mode=ro, so even a
# malicious/incorrect generated statement cannot write), plus a belt-and-
# suspenders check that the generated statement is a single SELECT.
# The schema description below is hand-curated, not a raw DDL dump --
# it deliberately documents the non-obvious conventions (NULL meanings,
# course_streams, special_requirement) that a generic schema dump would
# never communicate, since those conventions are exactly what's easy to
# get wrong (as the stream-membership bug above demonstrates).
# ============================================================

_SCHEMA_DESCRIPTION = """
Tables and important conventions (read carefully -- these are NOT obvious from column names alone):

courses(id, course_code, name, credit_hours, year_level, semester_offered,
        department_id, stream_id, curriculum_version_id, is_droppable,
        special_requirement, note)
  - department_id: NULL means the course is common/college-wide (belongs to no single department).
  - stream_id: if set, the course belongs ONLY to that one stream.
               If NULL, check the course_streams table:
                 - if course_streams has row(s) for this course, it belongs ONLY to those
                   specific streams (a partial subset), NOT all streams.
                 - if course_streams has NO rows for this course AND stream_id is NULL,
                   the course is common to ALL streams.
               NEVER assume stream_id IS NULL means "all streams" without checking course_streams first.
  - special_requirement: 'ALL_STREAM_COURSES' means the course (e.g. a final year project)
    requires every course in the student's own stream to be passed first -- this is NOT a
    normal prerequisite row. 'ALL_COURSES' means it requires the entire curriculum
    (e.g. an exit exam). NULL means no special requirement.

streams(id, department_id, name)

course_streams(id, course_id, stream_id)
  -- Junction table: which specific streams a course belongs to, when NOT all streams.

prerequisites(id, course_id, prerequisite_course_id, applicable_stream_id)
  - applicable_stream_id: NULL means this prerequisite applies to a student in ANY stream.
    If set, this specific prerequisite relationship only applies to a student in that stream.

common_courses(id, course_id, shared_with_department_id, context_note)
  -- Cross-department sharing: additional departments (besides the course's home
     department in courses.department_id) that also accept this course.

departments(id, name, code)
"""


def _generate_sql_from_question(user_query: str):
    """Asks the LLM to write a single read-only SELECT statement, given the
    curated schema description. Returns the SQL string, or None if the LLM
    didn't return anything usable."""
    prompt = f"""You are a SQLite expert. Given the schema below, write ONE single SQL SELECT
statement that answers the student's question. Reply with ONLY the SQL statement,
no explanation, no markdown code fences, no semicolon-separated multiple statements.
The query MUST be read-only (SELECT only) -- never write, update, delete, or alter anything.

{_SCHEMA_DESCRIPTION}

Student question: {user_query}
SQL:"""
    raw = generate_response(prompt, temperature=0).strip()
    # Strip accidental markdown fences if the model adds them despite instructions
    raw = re.sub(r"^```(?:sql)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE).strip()
    return raw or None


def _is_safe_select(sql: str) -> bool:
    """Belt-and-suspenders guard, on top of the read-only DB connection:
    reject anything that isn't a single, plain SELECT statement."""
    normalized = sql.strip().rstrip(";").strip().upper()
    if not normalized.startswith("SELECT"):
        return False
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "PRAGMA", ";"]
    # allow the single leading SELECT, but reject any of these appearing anywhere else
    body = sql.strip().rstrip(";")
    return not any(word in body.upper() for word in forbidden)


def _run_text_to_sql_fallback(user_query: str, db_path: str = "academic_records.db"):
    """
    Last-resort path: generate SQL, validate it's read-only, execute it against
    a read-only connection, and format the results as backend_context.
    Returns None if anything along the way fails, so the caller can show the
    normal "couldn't understand" message instead of a confusing raw error.
    """
    sql = _generate_sql_from_question(user_query)
    if not sql or not _is_safe_select(sql):
        return None

    try:
        # mode=ro opens the file read-only at the OS/SQLite level -- even if
        # the safety check above somehow missed something, this connection
        # physically cannot write to the database.
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.close()
    except sqlite3.Error:
        return None

    if not rows:
        return None

    lines = [f"(Query executed: {sql})", "Results:"]
    for row in rows[:50]:  # cap what gets fed into the prompt
        lines.append(" - " + ", ".join(f"{k}={row[k]}" for k in row.keys()))
    return "\n".join(lines)


def process_student_query(user_query: str, route: str, student_profile: dict = None) -> dict:
    """
    Central orchestration layer. Route classification happens ONCE in bot.py
    (via intent_router.classify_route) and is passed in here.
    """
    if route == "REASONING_ENGINE":
        return {
            "response": ("⚙️ The Advanced Search Constraint solver is active. "
                         "I will be computing optimal multi-year recovery plans for you shortly."),
            "route": route,
        }

    backend_context = ""

    if route == "SQL_GRAPH":
        course_codes = re.findall(r'[A-Za-z]{3,4}g?\s*-?\s*\d{4}', user_query)
        course_codes = [re.sub(r"[\s-]", "", c) for c in course_codes]

        target_code = None
        if course_codes:
            target_code = course_codes[0]
        else:
            general_context = _resolve_general_sql_query(user_query)
            if general_context:
                backend_context = general_context
            else:
                matches = search_course_by_name(user_query)
                if matches:
                    top_score = matches[0][0]
                    tied_at_top = [c for score, c in matches if score == top_score]
                    if len(tied_at_top) > 1:
                        options = "\n".join(f"- `{c.course_code}`: {c.name}" for c in tied_at_top[:5])
                        return {"response": f"I found a few courses that might match — which one did you mean?\n{options}",
                                "route": route}
                    target_code = tied_at_top[0].course_code

        if target_code:
            details = get_course_details(target_code)
            if not details:
                return {
                    "response": (f"🛑 Escalation Notice: I cannot locate the course `{target_code}` in the "
                                 f"official ECE curriculum schema. Please check the spelling or verify with the "
                                 f"Department Head Office to see if this is an updated course listing."),
                    "route": route,
                }
            backend_context = (
                f"Course Code: {details['code']}\n"
                f"Name: {details['name']}\n"
                f"Credit Hours: {details['credit_hours']}\n"
                f"Academic Standing Slot: Year {details['year_level']}, Semester {details['semester']}\n"
                f"Prerequisites: {', '.join(details['prerequisites'])}\n"
            )
            if any(word in user_query.lower() for word in ["fail", "drop", "block", "cascade", "consequence"]):
                _, impacted = get_downstream_impact(target_code)
                if impacted:
                    backend_context += "\nDownstream Courses Blocked if Failed/Dropped:\n"
                    for imp in impacted:
                        backend_context += f"- {imp['course_code']} ({imp['name']}) [Applies to: {imp['applies_to_streams']}]\n"
                else:
                    backend_context += "\nDownstream Impacts: None. This is a terminal course.\n"

        # NEW: Text-to-SQL fallback -- only reached if nothing above produced any context
        if not backend_context:
            fallback_context = _run_text_to_sql_fallback(user_query)
            if fallback_context:
                backend_context = fallback_context

        if not backend_context:
            return {
                "response": ("⚠️ I couldn't identify a specific course or catalog query from your question. "
                             "Could you give me a course code (e.g., ECEg3201), a year/semester "
                             "(e.g., '4th year 1st semester'), or a stream name?"),
                "route": route,
            }

    elif route == "RAG_TELEGRAM":
        retrieved_announcement = get_educational_context(user_query, distance_threshold=0.6)
        if not retrieved_announcement:
            return {
                "response": ("🛑 Escalation Notice: I searched the recent department announcements and channel "
                             "postings regarding your request, but could not find matching official guidelines. "
                             "Please consult the ECE Academic Advisor or visit the Registrar's Office for clear validation."),
                "route": route,
            }
        backend_context = f"Official Telegram Channel Notice Content:\n{retrieved_announcement}"

    synthesis_system_prompt = """You are an expert, supportive ECE Academic Advisor at AASTU.

Strict rules you must follow:
- Base your response ENTIRELY on the Verified Backend Context you are given below.
- Include every relevant detail present in the context — do not drop specifics for
  brevity. For example, if a course listing specifies which stream(s) it applies to,
  state that explicitly; do not just list the course code.
- If a detail (a course name, a reason, a policy) is not present in the context, do not invent it —
  omit it or say it isn't specified, rather than making up a plausible-sounding explanation.
- If a course name is not given in the context, refer to it only by its course code.
- Be concise: deliver only the relevant information, with enough explanation to be
  clear, but no padding, no repeated phrasing, and no unnecessary elaboration."""

    synthesis_prompt = f"""Student Query: {user_query}

Verified Backend Context:
{backend_context}

Write your advisor response now."""

    ai_response = generate_response(synthesis_prompt, temperature=0.3, system=synthesis_system_prompt).strip()
    return {"response": ai_response, "route": route}