"""
reasoning_assembly.py -- Phase 6, Step 3.5
"""
from sqlalchemy import or_
from sqlalchemy.orm import sessionmaker
from models import (
    engine, Student, StudentCourseStatus, Course, CourseStream,
    CommonCourse, CampusRule, Stream,
)
from prerequisite_graph import load_prerequisite_graph
from reasoning_solver import solve_recovery_plan, solve_ranked_recovery_plans

Session = sessionmaker(bind=engine)

_DEFAULT_GENERAL_CREDIT_CAP = 18
_DEFAULT_YEAR5_CREDIT_CAP = 22
_DEFAULT_MAX_YEARS = 5
_DEFAULT_RETAKE_LIMIT = 3
_HORIZON_BUFFER_SEMESTERS = 2 

def _campus_rule(session, rule_key, department_id=None, year_level=None, default=None):
    candidates = [
        r for r in session.query(CampusRule).filter(CampusRule.rule_key == rule_key).all()
        if r.department_id in (None, department_id) and r.year_level in (None, year_level)
    ]
    if not candidates:
        return default
    return max(candidates, key=lambda r: (r.department_id is not None) + (r.year_level is not None)).rule_value

def _student_department_id(student):
    if student.batch and student.batch.department_id:
        return student.batch.department_id
    if student.stream:
        return student.stream.department_id
    raise ValueError(f"Student {student.id}: can't resolve a department from batch or stream.")

def _student_position(student):
    if student.batch is None:
        raise ValueError(f"Student {student.id} has no batch on record; can't determine current position.")
    return student.batch.current_year_level, student.batch.current_semester

def _already_passed_ids(session, student_id):
    rows = (
        session.query(StudentCourseStatus.course_id)
        .filter(StudentCourseStatus.student_id == student_id,
                StudentCourseStatus.status == 'PASSED')
        .distinct().all()
    )
    return {r[0] for r in rows}

def _applicable_course_ids(session, department_id, stream_id, curriculum_version_id):
    base = (
        session.query(Course.id, Course.stream_id)
        .filter(Course.curriculum_version_id == curriculum_version_id,
                or_(Course.department_id == department_id, Course.department_id.is_(None)))
        .all()
    )
    base_ids = {cid for cid, _ in base}
    ids = {cid for cid, sid in base if sid is None or sid == stream_id}

    if stream_id is not None:
        shared = (
            session.query(CourseStream.course_id)
            .filter(CourseStream.stream_id == stream_id, CourseStream.course_id.in_(base_ids))
            .all()
        )
        ids.update(r[0] for r in shared)
    return ids

def _exhausted_course_ids(session, student_id, candidate_course_ids, retake_limit):
    exhausted = set()
    for cid in candidate_course_ids:
        rows = (
            session.query(StudentCourseStatus)
            .filter(StudentCourseStatus.student_id == student_id,
                    StudentCourseStatus.course_id == cid,
                    StudentCourseStatus.status != 'DROPPED')
            .all()
        )
        if not rows or any(r.status == 'PASSED' for r in rows):
            continue
        if len(rows) >= retake_limit:
            exhausted.add(cid)
    return exhausted

def _flipped_parity_course_ids(session, candidate_course_ids):
    rows = (
        session.query(CommonCourse.course_id)
        .filter(CommonCourse.course_id.in_(candidate_course_ids))
        .distinct().all()
    )
    return {r[0] for r in rows}

def _inject_all_stream_courses_edges(graph, stream_id):
    if stream_id is None:
        return
    for node_id, data in list(graph.nodes(data=True)):
        if data.get('special_requirement') != 'ALL_STREAM_COURSES':
            continue
        same_slot = (data['year_level'], data['semester_offered'])
        for cid, cdata in graph.nodes(data=True):
            if cid == node_id:
                continue
            scope = cdata.get('stream_scope')
            if scope is None or stream_id not in scope:
                continue  
            if (cdata['year_level'], cdata['semester_offered']) == same_slot:
                continue  
            graph.add_edge(cid, node_id, applicable_streams={stream_id})

def assemble_plan_inputs(session, student_id, stream_id_override=None):
    student = session.get(Student, student_id)
    if student is None:
        raise ValueError(f"No student found with id={student_id}")

    department_id = _student_department_id(student)
    stream_id = stream_id_override if stream_id_override is not None else student.stream_id
    current_year_level, current_semester = _student_position(student)
    curriculum_version_id = student.batch.curriculum_version_id

    # CHANGED: Pass the active session here
    graph = load_prerequisite_graph(session)
    _inject_all_stream_courses_edges(graph, stream_id)

    already_passed = _already_passed_ids(session, student_id)
    applicable = _applicable_course_ids(session, department_id, stream_id, curriculum_version_id)
    required = applicable - already_passed

    retake_limit = int(_campus_rule(
        session, 'retake_limit', department_id=department_id, default=_DEFAULT_RETAKE_LIMIT))
    exhausted = _exhausted_course_ids(session, student_id, required, retake_limit)
    flipped = _flipped_parity_course_ids(session, required)

    general_cap = _campus_rule(
        session, 'general_credit_cap', department_id=department_id, default=_DEFAULT_GENERAL_CREDIT_CAP)
    year5_cap = _campus_rule(
        session, 'year5_credit_cap', department_id=department_id, year_level=5, default=_DEFAULT_YEAR5_CREDIT_CAP)
    max_years = int(_campus_rule(
        session, 'max_years_to_graduate', department_id=department_id, default=_DEFAULT_MAX_YEARS))

    return {
        "graph": graph,
        "required_course_ids": required,
        "already_passed_ids": already_passed,
        "current_year_level": current_year_level,
        "current_semester": current_semester,
        "student_stream_id": stream_id,
        "general_credit_cap": general_cap,
        "year5_credit_cap": year5_cap,
        "max_years": max_years,
        "flipped_parity_course_ids": flipped,
        "exhausted_course_ids": exhausted,
        "horizon_semesters": max_years * 2 + _HORIZON_BUFFER_SEMESTERS,
    }

def get_recovery_plan(student_id, stream_id_override=None, **solver_kwargs):
    session = Session()
    try:
        inputs = assemble_plan_inputs(session, student_id, stream_id_override)
    finally:
        session.close()
    inputs.update(solver_kwargs)
    return solve_recovery_plan(**inputs)

def get_ranked_recovery_plans(student_id, stream_id_override=None, top_n=3, **solver_kwargs):
    session = Session()
    try:
        inputs = assemble_plan_inputs(session, student_id, stream_id_override)
    finally:
        session.close()
    inputs.update(solver_kwargs)
    return solve_ranked_recovery_plans(**inputs, top_n=top_n)

def compare_stream_options(student_id, **solver_kwargs):
    session = Session()
    try:
        student = session.get(Student, student_id)
        if student is None:
            raise ValueError(f"No student found with id={student_id}")
        department_id = _student_department_id(student)
        streams = session.query(Stream).filter(Stream.department_id == department_id).all()
        if not streams:
            raise ValueError(f"No streams configured for department_id={department_id}")

        comparisons = []
        for stream in streams:
            inputs = assemble_plan_inputs(session, student_id, stream_id_override=stream.id)
            inputs.update(solver_kwargs)
            result = solve_recovery_plan(**inputs)
            comparisons.append({"stream_id": stream.id, "stream_name": stream.name, "result": result})
    finally:
        session.close()

    comparisons.sort(key=lambda e: (0, e["result"]["total_semesters_used"]) if e["result"]["feasible"]
                      else (1, float('inf')))
    return comparisons

if __name__ == "__main__":
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    if not sid:
        print("Usage: python reasoning_assembly.py <student_id>")
        sys.exit(1)
    print(get_ranked_recovery_plans(sid, top_n=3))