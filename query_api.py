from sqlalchemy.orm import sessionmaker
from sqlalchemy import case
from models import (
    engine, Student, Course, StudentCourseStatus, 
    Prerequisite, Stream, Batch, CommonCourse, CourseStream
)

Session = sessionmaker(bind=engine)
session = Session()

def get_student_academic_summary(student_id):
    """
    Helper to fetch a student and return their active state, stream, 
    and dictionary of completed course statuses.
    
    CRITICAL FIX: Employs explicit ascending order by year, semester, and attempt 
    so that successive retakes deterministically overwrite older academic records.
    """
    student = session.query(Student).filter_by(id=student_id).first()
    if not student:
        return None, None, {}

    # Fetch batch to understand current academic standing
    batch = session.query(Batch).filter_by(id=student.batch_id).first()
    current_year = batch.current_year_level if batch else 1
    current_sem = batch.current_semester if batch else 1

    # CRITICAL FIX: Query with explicit deterministic sorting
    records = (
        session.query(StudentCourseStatus)
        .filter_by(student_id=student_id)
        .order_by(
            StudentCourseStatus.academic_year_taken.asc(),
            StudentCourseStatus.semester_taken.asc(),
            # Use a case statement to sort NULL attempts (drops) before numbered attempts
            case(
                (StudentCourseStatus.attempt_number == None, 0),
                else_=StudentCourseStatus.attempt_number
            ).asc()
        )
        .all()
    )
    
    # Map each course_code to its latest valid status
    history = {}
    for r in records:
        course = session.query(Course).filter_by(id=r.course_id).first()
        if not course:
            continue
        code = course.course_code.upper()
        
        # Latest attempt state overwrites prior states
        history[code] = r.status.upper()

    return student, (current_year, current_sem), history


def evaluate_special_requirements(student, course, history):
    """
    Helper to evaluate courses with global structural requirements.
    Returns (is_cleared, list_of_missing_requirements)
    """
    if not course.special_requirement:
        return True, []

    missing = []
    
    # 1. Evaluate 'ALL_STREAM_COURSES' (e.g., Final Year Project II)
    if course.special_requirement == "ALL_STREAM_COURSES":
        if not student.stream_id:
            return False, ["Student has not been assigned to an academic stream yet."]
        
        # Find all courses belonging to this specific stream
        stream_courses = session.query(Course).filter_by(
            curriculum_version_id=course.curriculum_version_id,
            stream_id=student.stream_id
        ).all()
        
        for sc in stream_courses:
            # Skip checking the target course itself to prevent circular evaluation
            if sc.id == course.id:
                continue
            
            sc_code = sc.course_code.upper()
            status = history.get(sc_code)
            if status != "PASSED":
                missing.append(f"{sc.course_code} ({sc.name}) [Status: {status or 'NOT TAKEN'}]")

    # 2. Evaluate 'ALL_COURSES' (e.g., National Exit Exam)
    elif course.special_requirement == "ALL_COURSES":
        # Find every single course in the curriculum
        all_curriculum_courses = session.query(Course).filter_by(
            curriculum_version_id=course.curriculum_version_id
        ).all()
        
        for ac in all_curriculum_courses:
            if ac.id == course.id:
                continue
            
            # Skip checking stream courses that do not belong to this student
            if ac.stream_id and ac.stream_id != student.stream_id:
                continue
                
            ac_code = ac.course_code.upper()
            status = history.get(ac_code)
            if status != "PASSED":
                missing.append(f"{ac.course_code} [Status: {status or 'NOT TAKEN'}]")

    if missing:
        return False, missing
    return True, []


def get_eligible_courses(student_id):
    """
    Determines which courses a student is eligible to take in their current semester.
    """
    student, current_standing, history = get_student_academic_summary(student_id)
    if not student:
        print(f"❌ Student {student_id} not found.")
        return [], []

    target_year, target_sem = current_standing
    print(f"\n🔍 Evaluating eligibility for {student.name} ({student_id})")
    print(f"   Standing: Year {target_year}, Semester {target_sem} | Stream: {student.stream.name if student.stream else 'None'}")

    # Fetch all courses offered in this year and semester
    offered_courses = session.query(Course).filter_by(
        year_level=target_year,
        semester_offered=target_sem
    ).all()

    eligible = []
    blocked = []

    for course in offered_courses:
        # Rule A: Check stream compatibility
        is_stream_compatible = False
        if course.stream_id is None:
            shared_entries = session.query(CourseStream).filter_by(course_id=course.id).all()
            if not shared_entries:
                is_stream_compatible = True  
            else:
                shared_stream_ids = [se.stream_id for se in shared_entries]
                if student.stream_id in shared_stream_ids:
                    is_stream_compatible = True
        elif course.stream_id == student.stream_id:
            is_stream_compatible = True

        if not is_stream_compatible:
            continue 

        course_is_cleared = True
        missing_reasons = []

        # Rule B: Standard/Stream-Conditional Prerequisites Check
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

        # CRITICAL FIX: Explicitly evaluate database structural 'special_requirement' column
        if course.special_requirement:
            special_cleared, special_missing = evaluate_special_requirements(student, course, history)
            if not special_cleared:
                course_is_cleared = False
                missing_reasons.extend(special_missing)

        if course_is_cleared:
            eligible.append(course)
        else:
            blocked.append((course, missing_reasons))

    # Print results
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
    """
    Evaluates a custom list of course codes a student wants to add,
    safeguarding standard prerequisites and special structural requirements.
    """
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

        # 1. Standard/Stream-Conditional Prerequisite constraints
        prereqs = session.query(Prerequisite).filter_by(course_id=course.id).all()
        for p in prereqs:
            if p.applicable_stream_id is not None and p.applicable_stream_id != student.stream_id:
                continue

            prereq_course = session.query(Course).filter_by(id=p.prerequisite_course_id).first()
            if prereq_course:
                p_code = prereq_course.course_code.upper()
                if history.get(p_code) != "PASSED":
                    violations.append(f"Cannot take {course.course_code} because prerequisite {p_code} is not passed.")

        # CRITICAL FIX: Check special requirements during manual registration checks
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

    alternatives = []
    for s in shares:
        alternatives.append(s.context_note)
    
    return alternatives


if __name__ == "__main__":
    print("🧪 Running upgraded query API with bug fixes...")
    # Test Yordi, Abebe, and Chala
    get_eligible_courses("ETS/1234/14")
    get_eligible_courses("ETS/5678/14")
    get_eligible_courses("ETS/9999/14")