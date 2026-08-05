import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Student, Batch, Stream, Course, CurriculumVersion, CampusRule
import reasoning_assembly
import prerequisite_graph
import seed_data
import orchestrator  # FIX: Import the module itself so we can patch its Session

from orchestrator import process_student_query


@pytest.fixture
def routing_db(monkeypatch):
    """
    Sets up the in-memory database using the same reliable Phase 1 seeding
    logic we used for the domino test.
    """
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()
    
    monkeypatch.setattr(reasoning_assembly, "Session", TestSession)
    monkeypatch.setattr(prerequisite_graph, "Session", TestSession)
    monkeypatch.setattr(seed_data, "session", session)
    monkeypatch.setattr(orchestrator, "Session", TestSession)  # FIX: Patch the orchestrator's Session!
    
    seed_data.seed_foundational_data()
    
    # Bridge the rule keys for the solver
    ece_dept = session.query(Stream).filter_by(name="Computer Engineering").first().department
    session.add_all([
        CampusRule(rule_key="general_credit_cap", rule_value=20, department_id=None, description="Test general cap"),
        CampusRule(rule_key="year5_credit_cap", rule_value=22, department_id=ece_dept.id, year_level=5, description="Test year 5 overload cap"),
        CampusRule(rule_key="retake_limit", rule_value=3, department_id=None, description="Test retake limit"),
        CampusRule(rule_key="max_years_to_graduate", rule_value=5, department_id=None, description="Test max years horizon")
    ])
    
    # Basic Curriculum & Batch
    curriculum = CurriculumVersion(version_name="ECE_2024", department_id=ece_dept.id, is_active=True)
    session.add(curriculum)
    session.flush()
    
    batch = Batch(department_id=ece_dept.id, entry_year=2021, current_year_level=4, current_semester=1, curriculum_version_id=curriculum.id)
    session.add(batch)
    
    # Add a single dummy course so the solver has something to schedule
    dummy_course = Course(course_code="DUMMY101", name="Dummy Course", credit_hours=3, year_level=4, semester_offered=1, department_id=ece_dept.id, curriculum_version_id=curriculum.id)
    session.add(dummy_course)
    
    session.commit()
    yield session
    session.close()


@patch("orchestrator.generate_response") 
def test_routing_committed_student_generates_standard_plan(mock_generate_response, routing_db):
    """
    Verifies that a student WITH a committed stream triggers get_recovery_plan
    and formats the single-plan backend context.
    """
    stream = routing_db.query(Stream).filter_by(name="Computer Engineering").first()
    batch = routing_db.query(Batch).first()
    
    student = Student(id="UGR/1111/14", name="Committed Student", batch_id=batch.id, stream_id=stream.id)
    routing_db.add(student)
    routing_db.commit()

    mock_generate_response.return_value = "Mocked LLM Advice"

    student_profile = {"student_id": student.id}
    
    response = process_student_query(
        user_query="How do I graduate?", 
        student_profile=student_profile, 
        route="REASONING_ENGINE"
    )
    
    mock_generate_response.assert_called_once()
    
    # FIX: Grab the first positional argument (synthesis_prompt)
    synthesis_prompt = mock_generate_response.call_args.args[0]

    # FIX: Check for the actual formatted text, not the Python dictionary key!
    assert "ranked recovery plan(s) found" in synthesis_prompt
    
    # We still want to ensure it didn't accidentally give the stream comparison format
    assert "stream comparison" not in synthesis_prompt 


@patch("orchestrator.generate_response") 
def test_routing_undeclared_student_generates_stream_comparison(mock_generate_response, routing_db):
    """
    Verifies that a student WITHOUT a stream triggers compare_stream_options
    and formats a multi-option ranked backend context.
    """
    batch = routing_db.query(Batch).first()
    
    student = Student(id="UGR/2222/14", name="Undeclared Student", batch_id=batch.id, stream_id=None)
    routing_db.add(student)
    routing_db.commit()

    mock_generate_response.return_value = "Mocked LLM Advice"

    student_profile = {"student_id": student.id}

    response = process_student_query(
        user_query="Which stream is fastest?", 
        student_profile=student_profile, 
        route="REASONING_ENGINE"
    )
    
    mock_generate_response.assert_called_once()
    
    # FIX: Grab the first positional argument (synthesis_prompt)
    synthesis_prompt = mock_generate_response.call_args.args[0]
    
    assert "Computer Engineering" in synthesis_prompt
    assert "Communication Engineering" in synthesis_prompt