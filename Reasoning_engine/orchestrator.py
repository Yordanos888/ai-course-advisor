"""
orchestrator.py (NEW)
=======================
Replaces the old process_reasoning_request(), which called the now-
discarded reasoning_assembly.py (get_ranked_recovery_plans /
compare_stream_options). Same external signature as before, so
bot.py needs ZERO changes:

    process_reasoning_request(student, year, sem, stream_name, failed_courses) -> str

WHAT'S PRESERVED FROM THE OLD ORCHESTRATOR, AND WHY:
  - Updating student.batch_id / student.stream_id, and writing
    StudentCourseStatus rows, is kept even though solve_schedule.py
    doesn't itself need to READ that persisted state back (it computes
    its planning history directly from the year/sem/failed_courses
    parameters passed in for this one request). This write path stays
    because OTHER parts of the system already depend on it --
    student_service.get_derived_profile() reads student.batch/stream_id
    to answer other bot commands, and dropping the write would silently
    break those.
  - Free-text failed-course resolution still goes through query_api's
    resolve_course_entity, the same fuzzy matcher every other command uses.

WHAT'S NEW:
  - The actual planning computation is solve_schedule.py /
    model_stage8_fixed.py (Stages 1-8 of the reasoning engine), not the
    discarded reasoning_assembly.py.
  - Infeasible results, retake-limit diagnoses, and "plan exists but
    exceeds 5 years" are all real, distinct outcomes now (see
    format_schedule.py), not a single generic "no feasible plan" string.
"""

import datetime
from sqlalchemy.orm import sessionmaker
from models import engine, Course, StudentCourseStatus, Batch, Stream, CurriculumVersion
from query_api import resolve_course_entity, _resolve_course_stream_scope
from solve_schedule import solve_schedule_for_student, compare_all_streams
from format_schedule import format_schedule_result, format_stream_comparison

Session = sessionmaker(bind=engine)

ECE_DEPARTMENT_ID = 1


def _persist_student_profile(session, student, year, sem, stream_name):
    approx_entry_year = datetime.date.today().year - (year - 1)
    batch = session.query(Batch).filter_by(
        department_id=ECE_DEPARTMENT_ID, current_year_level=year, current_semester=sem
    ).first()
    if not batch:
        cv = session.query(CurriculumVersion).filter_by(
            department_id=ECE_DEPARTMENT_ID, is_active=True
        ).first()
        batch = Batch(department_id=ECE_DEPARTMENT_ID, entry_year=approx_entry_year,
                       current_year_level=year, current_semester=sem,
                       curriculum_version_id=cv.id)
        session.add(batch)
        session.flush()
    student.batch_id = batch.id

    if stream_name:
        stream_obj = session.query(Stream).filter(
            Stream.department_id == ECE_DEPARTMENT_ID, Stream.name.ilike(f"{stream_name}%")
        ).first()
        student.stream_id = stream_obj.id if stream_obj else None
    else:
        student.stream_id = None


def _resolve_failed_courses(session, failed_course_texts):
    """Free-text -> real course codes via the existing fuzzy resolver.
    Returns (resolved_codes, unresolved_texts)."""
    resolved_codes = []
    unresolved = []
    for text in failed_course_texts:
        text = text.strip()
        if not text:
            continue
        course = resolve_course_entity(session, text)
        if course is None:
            unresolved.append(text)
        else:
            resolved_codes.append(course.course_code)
    return resolved_codes, unresolved


def _persist_course_history(session, student, year, sem, failed_codes):
    """Audit-trail write, matching the old system's behavior. Nothing
    currently reads these rows back for planning purposes (see module
    docstring), but this keeps the schema populated consistently."""
    session.query(StudentCourseStatus).filter_by(student_id=student.id).delete()

    failed_set = set(failed_codes)
    for code in failed_codes:
        course = session.query(Course).filter_by(course_code=code).first()
        if course:
            session.add(StudentCourseStatus(
                student_id=student.id, course_id=course.id, attempt_number=1,
                academic_year_taken=course.year_level, semester_taken=course.semester_offered,
                status="FAILED",
            ))

    past_courses = session.query(Course).filter(
        (Course.year_level < year) | ((Course.year_level == year) & (Course.semester_offered < sem))
    ).all()
    for c in past_courses:
        if c.course_code in failed_set:
            continue
        scope = _resolve_course_stream_scope(session, c)
        is_applicable = scope is None or (student.stream_id in scope if student.stream_id else False)
        if is_applicable:
            session.add(StudentCourseStatus(
                student_id=student.id, course_id=c.id, attempt_number=1,
                academic_year_taken=c.year_level, semester_taken=c.semester_offered,
                status="PASSED",
            ))


def _format_header(year, sem, stream_name, resolved_codes, unresolved):
    header = f"✅ *Profile Updated:* Year {year}, Sem {sem}\n"
    if stream_name:
        header += f"Stream: {stream_name}\n"
    if resolved_codes:
        header += f"Failed: {', '.join(resolved_codes)}\n"
    else:
        header += "Failed: None (all past courses assumed passed)\n"
    if unresolved:
        header += f"⚠️ Unrecognized (skipped): {', '.join(unresolved)}\n"
    return header


def process_reasoning_request(student, year: int, sem: int, stream_name: str, failed_courses: list) -> str:
    session = Session()
    student = session.merge(student)
    try:
        resolved_codes, unresolved = _resolve_failed_courses(session, failed_courses)

        _persist_student_profile(session, student, year, sem, stream_name)
        _persist_course_history(session, student, year, sem, resolved_codes)
        session.commit()

        if stream_name:
            result = solve_schedule_for_student(session, year, sem, stream_name, resolved_codes)
            body = format_schedule_result(result)
        else:
            comparisons = compare_all_streams(session, year, sem, resolved_codes)
            body = format_stream_comparison(comparisons)

        header = _format_header(year, sem, stream_name, resolved_codes, unresolved)
        return header + "\n" + body
    finally:
        session.close()
