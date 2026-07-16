from sqlalchemy.orm import sessionmaker
from models import (
    engine, Student, Course, StudentCourseStatus, Batch, Stream, Department, CurriculumVersion
)

Session = sessionmaker(bind=engine)
session = Session()

def seed_test_students():
    print("🧹 Cleaning old student test records...")
    session.query(StudentCourseStatus).delete()
    session.query(Student).delete()
    session.commit()

    # Retrieve existing department metadata
    ece_dept = session.query(Department).filter(Department.name.ilike("%Electrical%")).first()
    dept_id = ece_dept.id if ece_dept else 1

    comp_stream = session.query(Stream).filter(Stream.name.ilike("%Computer%")).first()
    power_stream = session.query(Stream).filter(Stream.name.ilike("%Power%")).first()

    comp_stream_id = comp_stream.id if comp_stream else 1
    power_stream_id = power_stream.id if power_stream else 2

    # Fetch an active curriculum version to satisfy the batch NOT NULL constraint
    cv = session.query(CurriculumVersion).first()
    cv_id = cv.id if cv else 1

    # Verify/create active academic batches
    # Year 4 Sem 2 batch
    batch_y4_s2 = session.query(Batch).filter_by(current_year_level=4, current_semester=2).first()
    if not batch_y4_s2:
        batch_y4_s2 = Batch(department_id=dept_id, entry_year=2022, current_year_level=4, current_semester=2, curriculum_version_id=cv_id)
        session.add(batch_y4_s2)
    
    # Year 3 Sem 2 batch
    batch_y3_s2 = session.query(Batch).filter_by(current_year_level=3, current_semester=2).first()
    if not batch_y3_s2:
        batch_y3_s2 = Batch(department_id=dept_id, entry_year=2023, current_year_level=3, current_semester=2, curriculum_version_id=cv_id)
        session.add(batch_y3_s2)

    # Year 5 Sem 2 batch
    batch_y5_s2 = session.query(Batch).filter_by(current_year_level=5, current_semester=2).first()
    if not batch_y5_s2:
        batch_y5_s2 = Batch(department_id=dept_id, entry_year=2021, current_year_level=5, current_semester=2, curriculum_version_id=cv_id)
        session.add(batch_y5_s2)

    session.commit()

    # --- Student Instantiations (Strict Schema Match) ---
    yordi = Student(id="ETS/1234/14", name="Yordi", batch_id=batch_y4_s2.id, stream_id=comp_stream_id, cgpa=3.8, status="ACTIVE")
    session.add(yordi)

    abebe = Student(id="ETS/5678/14", name="Abebe", batch_id=batch_y4_s2.id, stream_id=power_stream_id, cgpa=2.4, status="ACTIVE")
    session.add(abebe)

    chala = Student(id="ETS/9999/14", name="Chala", batch_id=batch_y3_s2.id, stream_id=None, cgpa=2.1, status="ACTIVE")
    session.add(chala)

    zeleke = Student(id="ETS/1111/14", name="Zeleke", batch_id=batch_y5_s2.id, stream_id=comp_stream_id, cgpa=3.2, status="ACTIVE")
    session.add(zeleke)

    aster = Student(id="ETS/2222/14", name="Aster", batch_id=batch_y5_s2.id, stream_id=comp_stream_id, cgpa=3.9, status="ACTIVE")
    session.add(aster)

    session.commit()

    # Helper maps for loading histories
    all_courses = {c.course_code.upper(): c.id for c in session.query(Course).all()}

    def add_status(student_id, course_code, status, year=1, sem=1, attempt=1):
        code_upper = course_code.upper()
        if code_upper in all_courses:
            record = StudentCourseStatus(
                student_id=student_id,
                course_id=all_courses[code_upper],
                status=status,
                academic_year_taken=year,
                semester_taken=sem,
                attempt_number=attempt
            )
            session.add(record)

    # --- HISTORIES SETUP ---

    # 1. Yordi: Has successfully PASSED all courses from Years 1-3, and Year 4 Semester 1.
    for code, c_id in all_courses.items():
        course = session.query(Course).filter_by(id=c_id).first()
        if (course.year_level < 4) or (course.year_level == 4 and course.semester_offered == 1):
            if not course.stream_id or course.stream_id == comp_stream_id:
                add_status("ETS/1234/14", code, "PASSED", course.year_level, course.semester_offered)

    # 2. Abebe (Domino): Passed basic courses, but FAILED Power systems I (ECEg4109)
    for code, c_id in all_courses.items():
        course = session.query(Course).filter_by(id=c_id).first()
        if (course.year_level < 4) or (course.year_level == 4 and course.semester_offered == 1):
            if not course.stream_id or course.stream_id == power_stream_id:
                if course.course_code.upper() == "ECEg4109":
                    add_status("ETS/5678/14", code, "FAILED", 4, 1, 1)
                else:
                    add_status("ETS/5678/14", code, "PASSED", course.year_level, course.semester_offered)

    # 3. Chala (Drop Case): No stream. Passed basic 1st/2nd-year, but failed Applied Math II in Year 2 Sem 1, cleared it on attempt 2.
    for code, c_id in all_courses.items():
        course = session.query(Course).filter_by(id=c_id).first()
        if course.year_level < 3:
            if course.course_code.upper() == "MATH2007":  # Applied Math II
                add_status("ETS/9999/14", code, "FAILED", 2, 1, 1)
                add_status("ETS/9999/14", code, "PASSED", 2, 2, 2)
            else:
                add_status("ETS/9999/14", code, "PASSED", course.year_level, course.semester_offered)

    # 4. Zeleke (5th-Year Finalist): Has passed everything EXCEPT one Computer-stream major (e.g., ECEg5405 - VLSI)
    for code, c_id in all_courses.items():
        course = session.query(Course).filter_by(id=c_id).first()
        if course.course_code.upper() != "ECEG5108": # Do not pre-pass FYP-II
            if course.course_code.upper() == "ECEG5405": # Failed/In-Progress Computer-stream major
                add_status("ETS/1111/14", code, "FAILED", 4, 2, 1)
            else:
                add_status("ETS/1111/14", code, "PASSED", course.year_level, course.semester_offered)

    # 5. Aster (Exit Exam Candidate): Completed literally EVERY single curriculum course beautifully.
    for code, c_id in all_courses.items():
        course = session.query(Course).filter_by(id=c_id).first()
        if course.course_code.upper() not in ["ECEG5108", "NEE5108"]: # Exclude final projects/exams themselves
            if not course.stream_id or course.stream_id == comp_stream_id:
                add_status("ETS/2222/14", code, "PASSED", course.year_level, course.semester_offered)

    session.commit()
    print("🌱 Seeded 5 highly distinct student academic profiles successfully!")

if __name__ == "__main__":
    seed_test_students()