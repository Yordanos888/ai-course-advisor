# orchestrator.py
import re
from rag_service import get_educational_context
from query_api import get_course_details, get_downstream_impact, search_course_by_name
from llm_client import generate_response
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func
from models import engine, Course, Stream, CourseStream, Department

Session = sessionmaker(bind=engine)


def _resolve_general_sql_query(user_query: str) -> str:
    """
    Handles general catalog questions that do NOT name a specific course.
    Detects patterns like:
      - "list courses in 4th year 1st semester"
      - "what courses are in the computer stream?"
      - "how many total credit hours?"
    Returns a structured context string or None if no general pattern matched.
    """
    session = Session()
    try:
        q = user_query.lower()

        # ------------------------------------------------------------------
        # 1. Year / Semester listing
        # ------------------------------------------------------------------
        year = None
        sem = None

        # "4th year", "year 4", "year four"
        y_match = re.search(r'(\d)(?:st|nd|rd|th)\s*year', q)
        if y_match:
            year = int(y_match.group(1))
        else:
            y_match = re.search(r'year\s*(\d)', q)
            if y_match:
                year = int(y_match.group(1))

        # "1st semester", "semester 1", "sem 2"
        s_match = re.search(r'(\d)(?:st|nd)\s*sem(?:ester)?', q)
        if s_match:
            sem = int(s_match.group(1))
        else:
            s_match = re.search(r'sem(?:ester)?\s*(\d)', q)
            if s_match:
                sem = int(s_match.group(1))

        if year is not None or sem is not None:
            query = session.query(Course)
            if year is not None:
                query = query.filter_by(year_level=year)
            if sem is not None:
                query = query.filter_by(semester_offered=sem)
            courses = query.order_by(Course.year_level, Course.semester_offered, Course.course_code).all()

            if courses:
                lines = []
                for c in courses:
                    dept_code = c.department.code if c.department else "Common"
                    lines.append(
                        f"- {c.course_code}: {c.name} ({c.credit_hours} cr) — "
                        f"Year {c.year_level}, Sem {c.semester_offered} [{dept_code}]"
                    )
                header_parts = []
                if year:
                    header_parts.append(f"Year {year}")
                if sem:
                    header_parts.append(f"Semester {sem}")
                header = "Catalog listing for " + ", ".join(header_parts)
                return header + ":\n" + "\n".join(lines)

        # ------------------------------------------------------------------
        # 2. Stream-specific courses
        # ------------------------------------------------------------------
        stream_keywords = {
            "computer": "Computer",
            "communication": "Communication",
            "control": "Control",
            "power": "Power"
        }
        for keyword, stream_name in stream_keywords.items():
            if keyword in q and ("course" in q or "subject" in q or "class" in q):
                stream = session.query(Stream).filter(Stream.name.ilike(f"%{stream_name}%")).first()
                if stream:
                    direct = session.query(Course).filter_by(stream_id=stream.id).all()
                    shared_ids = [
                        cs.course_id for cs in
                        session.query(CourseStream).filter_by(stream_id=stream.id).all()
                    ]
                    shared = session.query(Course).filter(Course.id.in_(shared_ids)).all() if shared_ids else []
                    all_courses = {c.id: c for c in direct + shared}
                    courses = sorted(all_courses.values(), key=lambda c: (c.year_level, c.semester_offered, c.course_code))
                    if courses:
                        lines = [
                            f"- {c.course_code}: {c.name} (Year {c.year_level}, Sem {c.semester_offered})"
                            for c in courses
                        ]
                        return f"Courses belonging to the {stream_name} Engineering stream:\n" + "\n".join(lines)

        # ------------------------------------------------------------------
        # 3. Total curriculum statistics
        # ------------------------------------------------------------------
        if any(phrase in q for phrase in ["total credit", "how many credit", "credit hours total", "sum of credit"]):
            total_credits = session.query(func.sum(Course.credit_hours)).scalar() or 0
            count = session.query(Course).count()
            return (
                f"The ECE curriculum contains {count} courses with a total of "
                f"{int(total_credits)} credit hours."
            )

        # ------------------------------------------------------------------
        # 4. Common / cross-department courses
        # ------------------------------------------------------------------
        if "common" in q and ("department" in q or "between" in q or "shared" in q):
            from models import CommonCourse
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


def process_student_query(user_query: str, route: str, student_profile: dict = None) -> dict:
    """
    Central orchestration layer. Route classification happens ONCE in bot.py
    (via intent_router.classify_route) and is passed in here.
    """
    if route == "REASONING_ENGINE":
        return {
            "response": (
                "⚙️ The Advanced Search Constraint solver is active. "
                "I will be computing optimal multi-year recovery plans for you shortly."
            ),
            "route": route,
        }

    backend_context = ""

    if route == "SQL_GRAPH":
        # ------------------------------------------------------------------
        # A. Specific course lookup (code or name)
        # ------------------------------------------------------------------
        # Improved regex: allows optional spaces/hyphens (e.g. "ECEg 3105", "ECEg-3105")
        course_codes = re.findall(r'[A-Za-z]{3,4}g?\s*-?\s*\d{4}', user_query)
        # Normalize: strip spaces and hyphens
        course_codes = [re.sub(r"[\s-]", "", c) for c in course_codes]

        target_code = None
        if course_codes:
            target_code = course_codes[0]
        else:
            # Fallback: search by name/keywords
            matches = search_course_by_name(user_query)
            if matches:
                top_score = matches[0][0]
                tied_at_top = [c for score, c in matches if score == top_score]
                if len(tied_at_top) > 1:
                    options = "\n".join(f"- `{c.course_code}`: {c.name}" for c in tied_at_top[:5])
                    return {
                        "response": f"I found a few courses that might match — which one did you mean?\n{options}",
                        "route": route,
                    }
                target_code = tied_at_top[0].course_code

        if target_code:
            details = get_course_details(target_code)
            if not details:
                return {
                    "response": (
                        f"🛑 Escalation Notice: I cannot locate the course `{target_code}` in the "
                        f"official ECE curriculum schema. Please check the spelling or verify with the "
                        f"Department Head Office to see if this is an updated course listing."
                    ),
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
                        backend_context += (
                            f"- {imp['course_code']} ({imp['name']}) "
                            f"[Applies to: {imp['applies_to_streams']}]\n"
                        )
                else:
                    backend_context += "\nDownstream Impacts: None. This is a terminal course.\n"

        # ------------------------------------------------------------------
        # B. General catalog query (no specific course mentioned)
        # ------------------------------------------------------------------
        if not backend_context:
            general_context = _resolve_general_sql_query(user_query)
            if general_context:
                backend_context = general_context
            else:
                return {
                    "response": (
                        "⚠️ I couldn't identify a specific course or catalog query from your question. "
                        "Could you give me a course code (e.g., ECEg3201), a year/semester "
                        "(e.g., '4th year 1st semester'), or a stream name?"
                    ),
                    "route": route,
                }

    elif route == "RAG_TELEGRAM":
        retrieved_announcement = get_educational_context(user_query, distance_threshold=0.6)
        if not retrieved_announcement:
            return {
                "response": (
                    "🛑 Escalation Notice: I searched the recent department announcements and channel "
                    "postings regarding your request, but could not find matching official guidelines. "
                    "Please consult the ECE Academic Advisor or visit the Registrar's Office for clear validation."
                ),
                "route": route,
            }
        backend_context = f"Official Telegram Channel Notice Content:\n{retrieved_announcement}"

    # ------------------------------------------------------------------
    # Synthesis (shared across SQL_GRAPH and RAG_TELEGRAM)
    # ------------------------------------------------------------------
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