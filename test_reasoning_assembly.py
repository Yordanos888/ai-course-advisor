"""
test_reasoning_assembly.py
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import (
    Base, Department, Stream, CurriculumVersion, Batch, Student, Course,
    CourseStream, Prerequisite, CommonCourse, CampusRule, StudentCourseStatus,
)
import reasoning_assembly
import prerequisite_graph

@pytest.fixture
def db(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    monkeypatch.setattr(reasoning_assembly, "Session", TestSession)
    monkeypatch.setattr(prerequisite_graph, "Session", TestSession)
    session = TestSession()
    yield session
    session.close()

def _base_setup(session):
    dept = Department(name="Electrical and Computer Engineering", code="ECE")
    other_dept = Department(name="Software Engineering", code="SE")
    session.add_all([dept, other_dept])
    session.commit()

    computer = Stream(department_id=dept.id, name="Computer")
    communication = Stream(department_id=dept.id, name="Communication")
    session.add_all([computer, communication])
    session.commit()

    curriculum = CurriculumVersion(version_name="2024", department_id=dept.id, is_active=True)
    session.add(curriculum)
    session.commit()

    batch = Batch(department_id=dept.id, entry_year=2021, current_year_level=4,
                   current_semester=1, curriculum_version_id=curriculum.id)
    session.add(batch)
    session.commit()

    return {
        "dept": dept, "other_dept": other_dept,
        "computer": computer, "communication": communication,
        "curriculum": curriculum, "batch": batch,
    }

def _course(session, curriculum_id, code, credit_hours=3, year_level=1, semester_offered=1,
            department_id=None, stream_id=None, special_requirement=None, is_droppable=True):
    c = Course(course_code=code, name=code, credit_hours=credit_hours, year_level=year_level,
               semester_offered=semester_offered, department_id=department_id, stream_id=stream_id,
               curriculum_version_id=curriculum_id, is_droppable=is_droppable,
               special_requirement=special_requirement)
    session.add(c)
    session.commit()
    return c

def _status(session, student_id, course_id, status, attempt_number, year=2023, sem=1):
    row = StudentCourseStatus(student_id=student_id, course_id=course_id, attempt_number=attempt_number,
                               academic_year_taken=year, semester_taken=sem, status=status)
    session.add(row)
    session.commit()
    return row

def test_applicable_course_ids_scoping(db):
    ctx = _base_setup(db)
    curr, dept = ctx["curriculum"], ctx["dept"]
    computer, communication = ctx["computer"], ctx["communication"]

    common = _course(db, curr.id, "COMMON1", year_level=1, semester_offered=1)
    dept_common = _course(db, curr.id, "DEPTCOMMON1", year_level=2, semester_offered=1, department_id=dept.id)
    comp_major = _course(db, curr.id, "COMPMAJ1", year_level=3, semester_offered=1,
                          department_id=dept.id, stream_id=computer.id)
    comm_major = _course(db, curr.id, "COMMAJ1", year_level=4, semester_offered=1,
                          department_id=dept.id, stream_id=communication.id)
    shared = _course(db, curr.id, "SHARED1", year_level=3, semester_offered=2, department_id=dept.id)
    db.add(CourseStream(course_id=shared.id, stream_id=computer.id))
    db.add(CourseStream(course_id=shared.id, stream_id=communication.id))
    db.commit()

    other_curr = CurriculumVersion(version_name="2019-legacy", department_id=dept.id, is_active=False)
    db.add(other_curr)
    db.commit()
    legacy = _course(db, other_curr.id, "LEGACY1", department_id=dept.id)

    ids = reasoning_assembly._applicable_course_ids(db, dept.id, computer.id, curr.id)

    assert ids == {common.id, dept_common.id, comp_major.id, shared.id}
    assert comm_major.id not in ids
    assert legacy.id not in ids

def test_exhaustion_and_passed_status(db):
    ctx = _base_setup(db)
    curr, dept = ctx["curriculum"], ctx["dept"]

    exhausted_course = _course(db, curr.id, "FAIL3", department_id=dept.id)
    dropped_course = _course(db, curr.id, "DROP2FAIL1", department_id=dept.id)
    passed_on_3rd = _course(db, curr.id, "PASS3RD", department_id=dept.id)
    untouched_course = _course(db, curr.id, "UNTOUCHED", department_id=dept.id)

    student = Student(id="s1", name="Test Student", batch_id=ctx["batch"].id, stream_id=ctx["computer"].id)
    db.add(student)
    db.commit()

    _status(db, "s1", exhausted_course.id, "FAILED", 1)
    _status(db, "s1", exhausted_course.id, "FAILED", 2)
    _status(db, "s1", exhausted_course.id, "FAILED", 3)

    _status(db, "s1", dropped_course.id, "FAILED", 1)
    _status(db, "s1", dropped_course.id, "DROPPED", 2)   
    _status(db, "s1", dropped_course.id, "FAILED", 3)

    _status(db, "s1", passed_on_3rd.id, "FAILED", 1)
    _status(db, "s1", passed_on_3rd.id, "FAILED", 2)
    _status(db, "s1", passed_on_3rd.id, "PASSED", 3)      

    candidates = {exhausted_course.id, dropped_course.id, passed_on_3rd.id, untouched_course.id}
    exhausted = reasoning_assembly._exhausted_course_ids(db, "s1", candidates, retake_limit=3)
    assert exhausted == {exhausted_course.id}

    passed = reasoning_assembly._already_passed_ids(db, "s1")
    assert passed == {passed_on_3rd.id}

def test_flipped_parity_course_ids(db):
    ctx = _base_setup(db)
    curr, dept, other_dept = ctx["curriculum"], ctx["dept"], ctx["other_dept"]

    shared_course = _course(db, curr.id, "SHAREDFLIP", department_id=dept.id)
    plain_course = _course(db, curr.id, "PLAIN", department_id=dept.id)
    db.add(CommonCourse(course_id=shared_course.id, shared_with_department_id=other_dept.id))
    db.commit()

    flipped = reasoning_assembly._flipped_parity_course_ids(db, {shared_course.id, plain_course.id})
    assert flipped == {shared_course.id}

def test_campus_rule_specificity(db):
    ctx = _base_setup(db)
    dept, other_dept = ctx["dept"], ctx["other_dept"]

    db.add_all([
        CampusRule(rule_key="general_credit_cap", rule_value=18, description="global default"),
        CampusRule(rule_key="general_credit_cap", rule_value=20, department_id=dept.id, description="ECE-wide"),
        CampusRule(rule_key="general_credit_cap", rule_value=22, department_id=dept.id, year_level=5,
                   description="ECE year5 override"),
    ])
    db.commit()

    assert reasoning_assembly._campus_rule(db, "general_credit_cap", department_id=dept.id, year_level=5) == 22
    assert reasoning_assembly._campus_rule(db, "general_credit_cap", department_id=dept.id, year_level=3) == 20
    assert reasoning_assembly._campus_rule(db, "general_credit_cap", department_id=other_dept.id, year_level=5) == 18
    assert reasoning_assembly._campus_rule(db, "nonexistent_key", default=99) == 99

def test_inject_all_stream_courses_edges(db):
    ctx = _base_setup(db)
    curr, dept = ctx["curriculum"], ctx["dept"]
    computer, communication = ctx["computer"], ctx["communication"]

    fyp2 = _course(db, curr.id, "FYP2", credit_hours=6, year_level=5, semester_offered=2,
                    department_id=dept.id, special_requirement="ALL_STREAM_COURSES", is_droppable=False)
    comp_major = _course(db, curr.id, "COMPMAJ", year_level=3, semester_offered=1,
                          department_id=dept.id, stream_id=computer.id)
    comp_major_same_slot = _course(db, curr.id, "COMPMAJ_SAMESLOT", year_level=5, semester_offered=2,
                                    department_id=dept.id, stream_id=computer.id)
    comm_major = _course(db, curr.id, "COMMAJ", year_level=4, semester_offered=1,
                          department_id=dept.id, stream_id=communication.id)
    shared = _course(db, curr.id, "SHARED", year_level=3, semester_offered=2, department_id=dept.id)
    db.add(CourseStream(course_id=shared.id, stream_id=computer.id))
    dept_common = _course(db, curr.id, "DEPTCOMMON", year_level=2, semester_offered=1, department_id=dept.id)
    db.commit()

    graph = prerequisite_graph.load_prerequisite_graph(db)
    reasoning_assembly._inject_all_stream_courses_edges(graph, computer.id)

    assert graph.has_edge(comp_major.id, fyp2.id)
    assert graph[comp_major.id][fyp2.id]["applicable_streams"] == {computer.id}
    assert not graph.has_edge(comp_major_same_slot.id, fyp2.id)  
    assert not graph.has_edge(comm_major.id, fyp2.id)            
    assert graph.has_edge(shared.id, fyp2.id)                    
    assert not graph.has_edge(dept_common.id, fyp2.id)           

    graph2 = prerequisite_graph.load_prerequisite_graph(db)
    edges_before = graph2.number_of_edges()
    reasoning_assembly._inject_all_stream_courses_edges(graph2, None)
    assert graph2.number_of_edges() == edges_before

def test_assemble_plan_inputs_end_to_end(db):
    ctx = _base_setup(db)
    curr, dept, computer, batch = ctx["curriculum"], ctx["dept"], ctx["computer"], ctx["batch"]

    passed_course = _course(db, curr.id, "PASSED1", department_id=dept.id)
    open_course = _course(db, curr.id, "OPEN1", department_id=dept.id)
    exhausted_course = _course(db, curr.id, "EXH1", department_id=dept.id)
    shared_flip_course = _course(db, curr.id, "FLIP1", department_id=dept.id)
    db.add(CommonCourse(course_id=shared_flip_course.id, shared_with_department_id=dept.id))
    db.add_all([
        CampusRule(rule_key="max_years_to_graduate", rule_value=5, description="standard"),
        CampusRule(rule_key="general_credit_cap", rule_value=18, description="standard"),
        CampusRule(rule_key="year5_credit_cap", rule_value=22, year_level=5, description="year5 override"),
        CampusRule(rule_key="retake_limit", rule_value=3, description="standard"),
    ])
    db.commit()

    student = Student(id="s1", name="Test Student", batch_id=batch.id, stream_id=computer.id)
    db.add(student)
    db.commit()

    _status(db, "s1", passed_course.id, "PASSED", 1)
    _status(db, "s1", exhausted_course.id, "FAILED", 1)
    _status(db, "s1", exhausted_course.id, "FAILED", 2)
    _status(db, "s1", exhausted_course.id, "FAILED", 3)

    inputs = reasoning_assembly.assemble_plan_inputs(db, "s1")

    assert inputs["already_passed_ids"] == {passed_course.id}
    assert passed_course.id not in inputs["required_course_ids"]
    assert open_course.id in inputs["required_course_ids"]
    assert exhausted_course.id in inputs["required_course_ids"]  
    assert inputs["exhausted_course_ids"] == {exhausted_course.id}
    assert shared_flip_course.id in inputs["flipped_parity_course_ids"]
    assert inputs["current_year_level"] == batch.current_year_level
    assert inputs["current_semester"] == batch.current_semester
    assert inputs["student_stream_id"] == computer.id
    assert inputs["general_credit_cap"] == 18
    assert inputs["year5_credit_cap"] == 22
    assert inputs["max_years"] == 5
    assert inputs["horizon_semesters"] == 5 * 2 + reasoning_assembly._HORIZON_BUFFER_SEMESTERS

def test_assemble_plan_inputs_missing_student_raises(db):
    with pytest.raises(ValueError, match="No student found"):
        reasoning_assembly.assemble_plan_inputs(db, "ghost")

def test_assemble_plan_inputs_missing_batch_raises(db):
    ctx = _base_setup(db)
    student = Student(id="s2", name="No Batch", batch_id=None, stream_id=ctx["computer"].id)
    db.add(student)
    db.commit()
    with pytest.raises(ValueError, match="no batch"):
        reasoning_assembly.assemble_plan_inputs(db, "s2")

def test_get_recovery_plan_trivial_case(db):
    pytest.importorskip("ortools")
    ctx = _base_setup(db)
    curr, dept, computer, batch = ctx["curriculum"], ctx["dept"], ctx["computer"], ctx["batch"]

    _course(db, curr.id, "SOLO", credit_hours=3, year_level=4, semester_offered=1,
            department_id=dept.id, stream_id=computer.id)

    student = Student(id="s1", name="Test Student", batch_id=batch.id, stream_id=computer.id)
    db.add(student)
    db.commit()

    result = reasoning_assembly.get_recovery_plan("s1")
    assert result["feasible"]
    assert result["total_semesters_used"] == 1

def test_compare_stream_options_ranks_feasible_first(db):
    pytest.importorskip("ortools")
    ctx = _base_setup(db)
    curr, dept = ctx["curriculum"], ctx["dept"]
    computer, communication, batch = ctx["computer"], ctx["communication"], ctx["batch"]

    _course(db, curr.id, "COMPQUICK", credit_hours=3, year_level=4, semester_offered=1,
            department_id=dept.id, stream_id=computer.id)
    
    comm_a = _course(db, curr.id, "COMMA", credit_hours=3, year_level=4, semester_offered=1,
                          department_id=dept.id, stream_id=communication.id)
    comm_b = _course(db, curr.id, "COMMB", credit_hours=3, year_level=4, semester_offered=1,
                          department_id=dept.id, stream_id=communication.id)
    db.add(Prerequisite(course_id=comm_b.id, prerequisite_course_id=comm_a.id))
    db.commit()

    student = Student(id="s3", name="Undeclared", batch_id=batch.id, stream_id=None)
    db.add(student)
    db.commit()

    comparisons = reasoning_assembly.compare_stream_options("s3")
    by_name = {c["stream_name"]: c["result"] for c in comparisons}

    assert by_name["Computer"]["feasible"]
    assert by_name["Communication"]["feasible"]
    assert by_name["Computer"]["total_semesters_used"] < by_name["Communication"]["total_semesters_used"]
    assert comparisons[0]["stream_name"] == "Computer"