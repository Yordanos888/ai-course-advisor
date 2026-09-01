from sqlalchemy.orm import sessionmaker
from models import engine, Course, StudentCourseStatus, Batch, Student, Stream
from query_api import resolve_course_entity
from reasoning_assembly import get_ranked_recovery_plans, compare_stream_options
from query_api import resolve_course_entity, _resolve_course_stream_scope
import datetime

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
        standard_codes = []
        summer_codes = []
        
        for cid in plan[slot]:
            c = session.query(Course).filter_by(id=cid).first()
            if c:
                code_str = f"*{c.course_code}* ({c.credit_hours} cr)"
                if c.semester_offered == 3:
                    summer_codes.append(code_str)
                else:
                    standard_codes.append(code_str)
                    
        if standard_codes:
            lines.append(f"• Year {year}, Sem {sem}: " + ", ".join(standard_codes))
        if summer_codes:
            lines.append(f"☀️ Year {year}, Sem 3 (Summer): " + ", ".join(summer_codes))
            
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

def process_reasoning_request(student: Student, year: int, sem: int, stream_name: str, failed_courses: list) -> str:
    session = Session()
    student = session.merge(student)
    try:
        unresolved = []
        recorded_failures = []
        ECE_DEPT_ID = 1 # Assuming 1 is ECE
        
        # 1. Update Student Batch and Stream Profiles
        approx_entry_year = datetime.date.today().year - (year - 1)
        batch = session.query(Batch).filter_by(
            department_id=ECE_DEPT_ID, current_year_level=year, current_semester=sem
        ).first()
        
        if not batch:
            # Fallback if batch doesn't exist yet
            from models import CurriculumVersion
            cv = session.query(CurriculumVersion).filter_by(department_id=ECE_DEPT_ID, is_active=True).first()
            batch = Batch(department_id=ECE_DEPT_ID, entry_year=approx_entry_year,
                          current_year_level=year, current_semester=sem, curriculum_version_id=cv.id)
            session.add(batch)
            session.flush()
            
        student.batch_id = batch.id
        
        if stream_name:
            stream_obj = session.query(Stream).filter(
                Stream.department_id == ECE_DEPT_ID, Stream.name.ilike(f"{stream_name}%")
            ).first()
            student.stream_id = stream_obj.id if stream_obj else None
        else:
            student.stream_id = None
            
        # Clear old statuses to compute a fresh timeline
        session.query(StudentCourseStatus).filter_by(student_id=student.id).delete()

        # 2. Resolve Explicit Failures
        failed_course_ids = set()
        for text in failed_courses:
            if not text.strip(): continue
            c = resolve_course_entity(session, text)
            if not c:
                unresolved.append(text)
                continue
            
            failed_course_ids.add(c.id)
            session.add(StudentCourseStatus(
                student_id=student.id, course_id=c.id, attempt_number=1,
                academic_year_taken=c.year_level, semester_taken=c.semester_offered, status="FAILED"
            ))
            recorded_failures.append(c.course_code)

        # 3. Generate Implicit Passes
        # Get all courses chronologically BEFORE the student's current year/sem
        past_courses = session.query(Course).filter(
            (Course.year_level < year) | 
            ((Course.year_level == year) & (Course.semester_offered < sem))
        ).all()

        for c in past_courses:
            # Skip if explicitly failed
            if c.id in failed_course_ids:
                continue
                
            # Check if this course actually belongs to the student's stream
            scope = _resolve_course_stream_scope(session, c)
            is_applicable = False
            if scope is None:
                is_applicable = True # Common course
            elif student.stream_id in scope:
                is_applicable = True # Matches their stream
                
            if is_applicable:
                session.add(StudentCourseStatus(
                    student_id=student.id, course_id=c.id, attempt_number=1,
                    academic_year_taken=c.year_level, semester_taken=c.semester_offered, status="PASSED"
                ))

        session.commit()

        # 4. Execute CP-SAT Solver
        if student.stream_id is None:
            comparisons = compare_stream_options(student.id)
            output = _format_stream_comparison(session, student, comparisons)
        else:
            result = get_ranked_recovery_plans(student.id, top_n=3)
            output = _format_recovery_result(session, student, result)

        # 5. Construct Final Telegram Message
        header = f"✅ *Profile Updated:* Year {year}, Sem {sem}\n"
        if stream_name: header += f"Stream: {stream_name}\n"
        if recorded_failures: 
            header += f"Failed: {', '.join(recorded_failures)}\n"
        else:
            header += "Failed: None (All past courses marked as PASSED)\n"
            
        if unresolved: 
            header += f"⚠️ Unrecognized (Skipped): {', '.join(unresolved)}\n"
        
        return header + "\n" + output
        
    finally:
        session.close()