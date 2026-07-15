from sqlalchemy.orm import sessionmaker
from models import (
    engine, Student, StudentCourseStatus, Batch, 
    Department, Stream, Course, CurriculumVersion
)

Session = sessionmaker(bind=engine)
session = Session()

def register_mock_students():
    print("👤 Starting Clean Student Profile Registration (Phase 2)...")

    # 1. Fetch foundational records
    ece_dept = session.query(Department).filter_by(code="ECE").first()
    curriculum = session.query(CurriculumVersion).filter_by(version_name="ECE-Curriculum-2022").first()
    
    if not ece_dept or not curriculum:
        print("❌ Error: Foundation data or curriculum must be loaded first!")
        return

    # Fetch ECE Streams
    streams = {s.name: s for s in session.query(Stream).filter_by(department_id=ece_dept.id).all()}
    comp_stream = streams.get("Computer Engineering")
    power_stream = streams.get("Power Engineering")

    # 2. Setup Cohorts (Batches)
    # Batch A: Active 4th Year Cohort (Entered 2022, currently in Y4S2)
    batch_y4 = session.query(Batch).filter_by(entry_year=2022, department_id=ece_dept.id).first()
    if not batch_y4:
        batch_y4 = Batch(
            department_id=ece_dept.id,
            entry_year=2022,
            current_year_level=4,
            current_semester=2,
            curriculum_version_id=curriculum.id
        )
        session.add(batch_y4)

    # Batch B: Active 3rd Year Cohort (Entered 2023, currently in Y3S2)
    batch_y3 = session.query(Batch).filter_by(entry_year=2023, department_id=ece_dept.id).first()
    if not batch_y3:
        batch_y3 = Batch(
            department_id=ece_dept.id,
            entry_year=2023,
            current_year_level=3,
            current_semester=2,
            curriculum_version_id=curriculum.id
        )
        session.add(batch_y3)

    session.flush()

    # Helper to retrieve courses by code (case-insensitive)
    def get_course(code):
        return session.query(Course).filter(Course.course_code.ilike(code)).first()

    # Clear old student data
    print("🧹 Cleaning existing mock student profiles...")
    session.query(StudentCourseStatus).delete()
    session.query(Student).delete()
    session.flush()

    # ==========================================
    # PROFILE 1: Yordi (The Model Student)
    # Status: Year 4, Semester 2 - Computer Stream
    # ==========================================
    print("\n✍️ Registering Yordi (Model Student - Y4S2)...")
    yordi = Student(
        id="ETS/1234/14",
        name="Yordi",
        batch_id=batch_y4.id,
        stream_id=comp_stream.id if comp_stream else None,
        cgpa=3.85,
        status="ACTIVE"
    )
    session.add(yordi)

    # Automatically pass all courses from Year 1 to Year 4 Semester 1
    yordi_historical_courses = session.query(Course).filter(
        Course.curriculum_version_id == curriculum.id,
        (Course.year_level < 4) | ((Course.year_level == 4) & (Course.semester_offered == 1))
    ).all()

    for course in yordi_historical_courses:
        if course.stream_id and course.stream_id != yordi.stream_id:
            continue
            
        status_entry = StudentCourseStatus(
            student_id=yordi.id,
            course_id=course.id,
            attempt_number=1,
            academic_year_taken=batch_y4.entry_year + (course.year_level - 1),
            semester_taken=course.semester_offered,
            status="PASSED"
        )
        session.add(status_entry)

    # ==========================================
    # PROFILE 2: Abebe (The Irregular Student)
    # Status: Year 4, Semester 2 - Power Stream (Delayed by prereqs)
    # ==========================================
    print("✍️ Registering Abebe (Irregular Student - Y4S2)...")
    abebe = Student(
        id="ETS/5678/14",
        name="Abebe",
        batch_id=batch_y4.id,
        stream_id=power_stream.id if power_stream else None,
        cgpa=2.45,
        status="ACTIVE"
    )
    session.add(abebe)

    # Abebe is in Year 4, Semester 2 (Power), but failed a critical 3rd-year prerequisite
    signals_course = get_course("ECEg3201") # Signals & Systems
    networks_course = get_course("ECEg3112") # Data Comm & Networks (or similar)

    for course in yordi_historical_courses:
        if course.stream_id and course.stream_id != abebe.stream_id:
            continue

        if signals_course and course.id == signals_course.id:
            # Abebe failed Signals on attempt 1, and hasn't passed it yet
            status_entry = StudentCourseStatus(
                student_id=abebe.id,
                course_id=course.id,
                attempt_number=1,
                academic_year_taken=batch_y4.entry_year + 2,
                semester_taken=1,
                status="FAILED"
            )
        elif networks_course and course.id == networks_course.id:
            # Abebe dropped Networks
            status_entry = StudentCourseStatus(
                student_id=abebe.id,
                course_id=course.id,
                attempt_number=None,
                academic_year_taken=batch_y4.entry_year + 2,
                semester_taken=2,
                status="DROPPED"
            )
        else:
            status_entry = StudentCourseStatus(
                student_id=abebe.id,
                course_id=course.id,
                attempt_number=1,
                academic_year_taken=batch_y4.entry_year + (course.year_level - 1),
                semester_taken=course.semester_offered,
                status="PASSED"
            )
        session.add(status_entry)

    # ==========================================
    # PROFILE 3: Chala (The Delayed/Pre-Stream Student)
    # Status: Year 3, Semester 2 - Stream: NONE
    # ==========================================
    print("✍️ Registering Chala (Delayed Student - Y3S2, No Stream)...")
    chala = Student(
        id="ETS/9999/14",
        name="Chala",
        batch_id=batch_y3.id,
        stream_id=None, # Corrected: No stream assigned because he is academically Y3S2
        cgpa=2.10,
        status="ACTIVE"
    )
    session.add(chala)

    # Chala is currently in Year 3, Semester 2, so we populate history up to Year 3, Semester 1
    chala_historical_courses = session.query(Course).filter(
        Course.curriculum_version_id == curriculum.id,
        (Course.year_level < 3) | ((Course.year_level == 3) & (Course.semester_offered == 1))
    ).all()

    math_course = get_course("Math1014") # Applied Math IB

    for course in chala_historical_courses:
        if course.stream_id:
            continue # No stream courses yet at Y3S1

        if math_course and course.id == math_course.id:
            # Chala failed Math1014 on first attempt, passed on second attempt
            fail_entry = StudentCourseStatus(
                student_id=chala.id,
                course_id=course.id,
                attempt_number=1,
                academic_year_taken=batch_y3.entry_year,
                semester_taken=2,
                status="FAILED"
            )
            pass_entry = StudentCourseStatus(
                student_id=chala.id,
                course_id=course.id,
                attempt_number=2,
                academic_year_taken=batch_y3.entry_year + 1,
                semester_taken=2,
                status="PASSED"
            )
            session.add_all([fail_entry, pass_entry])
        else:
            status_entry = StudentCourseStatus(
                student_id=chala.id,
                course_id=course.id,
                attempt_number=1,
                academic_year_taken=batch_y3.entry_year + (course.year_level - 1),
                semester_taken=course.semester_offered,
                status="PASSED"
            )
            session.add(status_entry)

    try:
        session.commit()
        print("\n🎉 Profiles successfully generated with flawless academic alignment!")
    except Exception as e:
        session.rollback()
        print(f"❌ Transaction failed during profile population: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    register_mock_students()