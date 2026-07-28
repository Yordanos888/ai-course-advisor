# reasoning_intake.py
import json
from sqlalchemy.orm import sessionmaker
from models import engine, Course, StudentCourseStatus, Batch
from llm_client import generate_response
from query_api import search_course_by_name

Session = sessionmaker(bind=engine)
session = Session()


def needs_stream(student, profile) -> bool:
    """Part A: only ask for stream once the student is at/past the streaming
    period (4th year 2nd semester onward) AND it isn't already on record."""
    if student.stream_id:
        return False
    year, semester = profile.get("year", ""), profile.get("semester", "")
    is_4th_2nd = "4th" in str(year) and "2nd" in str(semester)
    is_5th = "5th" in str(year)
    return is_4th_2nd or is_5th


def _resolve_course(text_fragment):
    """Resolves a course mention (code or name) to (code, name), or None."""
    import re
    code_match = re.search(r'[A-Za-z]{3,4}g?\d{4}', text_fragment)
    if code_match:
        course = session.query(Course).filter(Course.course_code.ilike(code_match.group(0))).first()
        if course:
            return course.id, course.course_code, course.name
    matches = search_course_by_name(text_fragment)
    if matches:
        top_score = matches[0][0]
        tied = [c for score, c in matches if score == top_score]
        if len(tied) == 1:
            c = tied[0]
            return c.id, c.course_code, c.name
    return None


def extract_courses_from_reply(reply_text: str, kind: str):
    """
    Uses the LLM to extract a structured list of {course, status} from a free-text
    reply -- this is deliberately an LLM call, not regex. Testing showed regex/keyword
    parsing fails on negation ("I haven't failed anything") and on sentences where
    "and" connects clauses rather than separating list items. The LLM handles both
    correctly; this function only handles resolving the extracted mentions to real
    course records, which IS deterministic and testable.

    kind: "FAILED_DROPPED" or "ADVANCED" -- shapes the extraction prompt's expected statuses.
    """
    if kind == "FAILED_DROPPED":
        status_options = "FAILED or DROPPED"
    else:
        status_options = "PASSED"

    extraction_prompt = f"""Extract course mentions and their status from the student's message below.
Reply with ONLY a JSON array, no other text. Each item: {{"course": "<code or name as written>", "status": "<{status_options}>"}}.
If the student says they have none, or the message doesn't mention any actual course, reply with an empty array: []

Student message: {reply_text}
JSON:"""

    raw = generate_response(extraction_prompt, temperature=0).strip()
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None  # caller should treat this as "couldn't understand, ask again"

    resolved = []
    for item in items:
        course_text = item.get("course", "")
        status = item.get("status", "").upper()
        found = _resolve_course(course_text)
        resolved.append({
            "input_text": course_text,
            "course_id": found[0] if found else None,
            "course_code": found[1] if found else None,
            "course_name": found[2] if found else None,
            "status": status,
        })
    return resolved


def _next_attempt_number(student_id, course_id):
    count = session.query(StudentCourseStatus).filter(
        StudentCourseStatus.student_id == student_id,
        StudentCourseStatus.course_id == course_id,
        StudentCourseStatus.attempt_number.isnot(None)
    ).count()
    return count + 1


def record_course_statuses(student, resolved_list):
    """Persists extracted course statuses. Returns (recorded: list, unresolved: list)."""
    batch = session.query(Batch).filter_by(id=student.batch_id).first()
    year, sem = batch.entry_year, batch.current_semester  # approximate -- see note below

    recorded, unresolved = [], []
    for item in resolved_list:
        if not item["course_id"]:
            unresolved.append(item["input_text"])
            continue

        if item["status"] == "DROPPED":
            existing = session.query(StudentCourseStatus).filter_by(
                student_id=student.id, course_id=item["course_id"], status="DROPPED").first()
            if existing:
                continue  # already on record, nothing to do
            session.add(StudentCourseStatus(student_id=student.id, course_id=item["course_id"],
                                             attempt_number=None, academic_year_taken=year,
                                             semester_taken=sem, status="DROPPED"))
        else:
            attempt = _next_attempt_number(student.id, item["course_id"])
            session.add(StudentCourseStatus(student_id=student.id, course_id=item["course_id"],
                                             attempt_number=attempt, academic_year_taken=year,
                                             semester_taken=sem, status=item["status"]))

                                             
        recorded.append(item["course_code"])

    session.commit()
    return recorded, unresolved