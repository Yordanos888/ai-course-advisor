from sqlalchemy.orm import sessionmaker
from models import engine, Course, StudentCourseStatus, Batch, Student
from query_api import resolve_course_entity
from reasoning_assembly import get_ranked_recovery_plans, compare_stream_options

Session = sessionmaker(bind=engine)

def _next_attempt_number(session, student_id, course_id):
    count = session.query(StudentCourseStatus).filter(
        StudentCourseStatus.student_id == student_id,
        StudentCourseStatus.course_id == course_id,
        StudentCourseStatus.attempt_number.isnot(None)
    ).count()
    return count + 1

def _slot_to_year_sem(current_year_level, current_semester, slot_offset):
    total = (current_year_level - 1) * 2 + (current_semester - 1) + slot_offset
    return total // 2 + 1, total % 2 + 1

def _format_plan_lines(session, current_year_level, current_semester, plan):
    lines = []
    for slot in sorted(plan.keys()):
        year, sem = _slot_to_year_sem(current_year_level, current_semester, slot)
        codes = []
        for cid in plan[slot]:
            c = session.query(Course).filter_by(id=cid).first()
            if c:
                codes.append(f"*{c.course_code}* ({c.credit_hours} cr)")
        lines.append(f"• Year {year}, Sem {sem}: " + ", ".join(codes))
    return lines

def _format_recovery_result(session, student, result):
    if not result["feasible"]:
        return f"❌ *No Feasible Plan Found*\nReason: {result['reason']}"

    plans = result["plans"]
    lines = [
        f"🎯 *Recovery Plans Computed*",
        f"Found {len(plans)} optimal path(s), completing in {plans[0]['total_semesters_used']} semester(s).\n"
    ]
    
    current_year = student.batch.current_year_level if student.batch else 1
    current_sem = student.batch.current_semester if student.batch else 1

    for i, p in enumerate(plans, 1):
        lines.append(f"📌 *Option {i}*")
        lines.extend(_format_plan_lines(session, current_year, current_sem, p["plan"]))
        lines.append("") # blank line for spacing
        
    return "\n".join(lines)

def _format_stream_comparison(session, student, comparisons):
    lines = ["⚖️ *Stream Recovery Comparison*\nSince you haven't selected a stream, here are the optimal paths for each:\n"]
    
    current_year = student.batch.current_year_level if student.batch else 1
    current_sem = student.batch.current_semester if student.batch else 1

    for entry in comparisons:
        r = entry["result"]
        if r["feasible"]:
            lines.append(f"🛠️ *{entry['stream_name']} Stream* — ({r['total_semesters_used']} semesters to graduate)")
            lines.extend(_format_plan_lines(session, current_year, current_sem, r["plan"]))
            lines.append("")
        else:
            lines.append(f"❌ *{entry['stream_name']} Stream* — INFEASIBLE: {r['reason']}\n")
            
    return "\n".join(lines)

def process_reasoning_request(student: Student, failed_courses: list, advanced_courses: list) -> str:
    """
    Entry point for the /course_planning command flow.
    1. Deterministically maps free-text course inputs to database IDs.
    2. Updates the student's status records.
    3. Triggers the CP-SAT constraint solver.
    4. Formats and returns the result.
    """
    session = Session()
    try:
        unresolved = []
        recorded = []
        
        batch = session.query(Batch).filter_by(id=student.batch_id).first()
        year = batch.current_year_level if batch else 1
        sem = batch.current_semester if batch else 1

        # 1. Process Failed/Dropped Courses
        for text in failed_courses:
            if not text.strip() or text.lower() == 'none': continue
            c = resolve_course_entity(session, text)
            if not c:
                unresolved.append(text)
                continue
            
            attempt = _next_attempt_number(session, student.id, c.id)
            session.add(StudentCourseStatus(
                student_id=student.id, course_id=c.id, attempt_number=attempt,
                academic_year_taken=year, semester_taken=sem, status="FAILED"
            ))
            recorded.append(f"{c.course_code} (Failed)")

        # 2. Process Advanced/Passed Courses
        for text in advanced_courses:
            if not text.strip() or text.lower() == 'none': continue
            c = resolve_course_entity(session, text)
            if not c:
                unresolved.append(text)
                continue
                
            attempt = _next_attempt_number(session, student.id, c.id)
            session.add(StudentCourseStatus(
                student_id=student.id, course_id=c.id, attempt_number=attempt,
                academic_year_taken=year, semester_taken=sem, status="PASSED"
            ))
            recorded.append(f"{c.course_code} (Passed)")
            
        session.commit()

        # 3. Execute CP-SAT Solver
        if student.stream_id is None:
            comparisons = compare_stream_options(student.id)
            output = _format_stream_comparison(session, student, comparisons)
        else:
            result = get_ranked_recovery_plans(student.id, top_n=3)
            output = _format_recovery_result(session, student, result)

        # 4. Construct Final Telegram Message
        header = "✅ *Academic Profile Updated*\n"
        if recorded: 
            header += f"Recorded: {', '.join(recorded)}\n"
        if unresolved: 
            header += f"⚠️ Unrecognized (Skipped): {', '.join(unresolved)}\n"
        
        return header + "\n" + output
        
    finally:
        session.close()