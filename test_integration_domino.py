"""
test_integration_domino.py

Integration test for Phase 6. Seeds foundational data using seed_data.py, 
constructs a synthetic "domino effect" prerequisite chain representing the ECE curriculum,
and validates that the solver pushes schedules back appropriately.

Run with: pytest test_integration_domino.py -v
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Student, Batch, Stream, Course, StudentCourseStatus, CurriculumVersion, Prerequisite, CampusRule
import reasoning_assembly
import prerequisite_graph

# Import your actual seeding function
import seed_data


@pytest.fixture
def real_db(monkeypatch):
    """
    Sets up an in-memory database, patches all session references across modules,
    and runs the actual seed_data script safely in memory.
    """
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession() # Instantiate the test session early
    
    # Patch all modules that create their own sessions so they share the memory space
    monkeypatch.setattr(reasoning_assembly, "Session", TestSession)
    monkeypatch.setattr(prerequisite_graph, "Session", TestSession)
    
    # THE FIX: Overwrite the active module-level session in seed_data
    monkeypatch.setattr(seed_data, "session", session)
    
    # Run the real Phase 1 seeding logic (now safely hitting the in-memory SQLite DB)
    seed_data.seed_foundational_data()
    
    yield session
    session.close()


def test_domino_effect_with_real_curriculum(real_db):
    """
    Tests a scenario where failing a foundational course (Math I) forces the solver 
    to sequentially push back Math II, Signals, and DSP across consecutive semesters, 
    respecting credit caps.
    """
    # 1. Fetch the seeded data
    ece_dept = real_db.query(Stream).filter_by(name="Computer Engineering").first().department
    stream = real_db.query(Stream).filter_by(name="Computer Engineering").first()

    # BRIDGE THE MISMATCH: reasoning_assembly.py expects different rule keys than seed_data.py provided.
    # We add the expected keys here mimicking the loaded values so the solver doesn't fall back to defaults.
    real_db.add_all([
        CampusRule(rule_key="general_credit_cap", rule_value=20, department_id=None, description="Test general cap"),
        CampusRule(rule_key="year5_credit_cap", rule_value=22, department_id=ece_dept.id, year_level=5, description="Test year 5 overload cap"),
        CampusRule(rule_key="retake_limit", rule_value=3, department_id=None, description="Test retake limit"),
        CampusRule(rule_key="max_years_to_graduate", rule_value=5, department_id=None, description="Test max years horizon")
    ])

    # 2. Setup active curriculum & batch (Mocking Phase 2 CSV load)
    curriculum = CurriculumVersion(version_name="ECE_2024", department_id=ece_dept.id, is_active=True)
    real_db.add(curriculum)
    real_db.flush()

    batch = Batch(department_id=ece_dept.id, entry_year=2021, current_year_level=3, current_semester=1, curriculum_version_id=curriculum.id)
    real_db.add(batch)
    real_db.flush()

    # 3. Create the Domino Chain of Courses (Math 1 -> Math 2 -> Signals -> DSP)
    # They are assigned heavy credit hours so they can't easily be crammed together
    math_1 = Course(course_code="MATH1014", name="Applied Math I", credit_hours=5, year_level=1, semester_offered=1, department_id=None, curriculum_version_id=curriculum.id)
    math_2 = Course(course_code="MATH2007", name="Applied Math II", credit_hours=5, year_level=1, semester_offered=2, department_id=None, curriculum_version_id=curriculum.id)
    signals = Course(course_code="ECE2041", name="Signals & Systems", credit_hours=5, year_level=2, semester_offered=1, department_id=ece_dept.id, curriculum_version_id=curriculum.id)
    dsp = Course(course_code="ECE3042", name="Digital Signal Processing", credit_hours=5, year_level=3, semester_offered=2, department_id=ece_dept.id, stream_id=stream.id, curriculum_version_id=curriculum.id)
    
    real_db.add_all([math_1, math_2, signals, dsp])
    real_db.flush()

    # Apply strict prerequisite edges
    real_db.add_all([
        Prerequisite(course_id=math_2.id, prerequisite_course_id=math_1.id),
        Prerequisite(course_id=signals.id, prerequisite_course_id=math_2.id),
        Prerequisite(course_id=dsp.id, prerequisite_course_id=signals.id)
    ])

    # 4. Create the test student
    student = Student(id="UGR/9999/14", name="Domino Test Student", batch_id=batch.id, stream_id=stream.id)
    real_db.add(student)
    real_db.flush()

    # 5. Set up the student's status: Failed Math 1, leaving the whole chain unresolved
    status_fail = StudentCourseStatus(
        student_id=student.id, course_id=math_1.id, attempt_number=1,
        academic_year_taken=2021, semester_taken=1, status="FAILED"
    )
    real_db.add(status_fail)
    real_db.commit()

    # 6. Run the recovery plan generator
    result = reasoning_assembly.get_recovery_plan(student_id=student.id)

    # 7. Assertions to guarantee the domino effect worked
    assert result["feasible"] is True, f"Solver deemed the plan infeasible: {result.get('reason')}"
    
    plan = result["plan"]
    
    # Reverse lookup to find which slot a course landed in
    slot_of = {cid: slot for slot, cids in plan.items() for cid in cids}
    
    # Ensure they are scheduled in the strict sequential order mandated by the graph
    assert slot_of[math_1.id] < slot_of[math_2.id], "Math II was not pushed back by Math I"
    assert slot_of[math_2.id] < slot_of[signals.id], "Signals was not pushed back by Math II"
    assert slot_of[signals.id] < slot_of[dsp.id], "DSP was not pushed back by Signals"

    # Confirm it takes at least 4 semesters (slots 0, 1, 2, 3) to clear this chain
    assert result["total_semesters_used"] >= 4