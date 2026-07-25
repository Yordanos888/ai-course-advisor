import datetime
from sqlalchemy import (
    create_engine, 
    Column, 
    Integer, 
    String, 
    Float, 
    Boolean, 
    DateTime, 
    ForeignKey, 
    UniqueConstraint, 
    Index, 
    text
)
from sqlalchemy.orm import declarative_base, relationship

DATABASE_URL = "sqlite:///academic_records.db"
engine = create_engine(DATABASE_URL, echo=True)

Base = declarative_base()

# ==========================================
# Schema Definitions
# ==========================================

class Department(Base):
    __tablename__ = 'departments'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False) 
    code = Column(String, unique=True, nullable=False) 

    streams = relationship("Stream", back_populates="department")
    curriculums = relationship("CurriculumVersion", back_populates="department")
    batches = relationship("Batch", back_populates="department")


class Stream(Base):
    __tablename__ = 'streams'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=False)
    name = Column(String, nullable=False) 

    department = relationship("Department", back_populates="streams")
    courses = relationship("Course", back_populates="stream")
    students = relationship("Student", back_populates="stream")


class CurriculumVersion(Base):
    __tablename__ = 'curriculum_versions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    version_name = Column(String, nullable=False) 
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=True) 
    is_active = Column(Boolean, default=True)

    department = relationship("Department", back_populates="curriculums")
    courses = relationship("Course", back_populates="curriculum_version")
    batches = relationship("Batch", back_populates="curriculum_version")


class Course(Base):
    __tablename__ = 'courses'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_code = Column(String, nullable=False) 
    name = Column(String, nullable=False)
    credit_hours = Column(Integer, nullable=False)
    semester_offered = Column(Integer, nullable=False) # 1, 2, or 3 (Summer)
    year_level = Column(Integer, nullable=False) 
    
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=True) 
    stream_id = Column(Integer, ForeignKey('streams.id'), nullable=True) # NULL = common/all streams
    curriculum_version_id = Column(Integer, ForeignKey('curriculum_versions.id'), nullable=False)
    
    is_droppable = Column(Boolean, default=True) 
    note = Column(String, nullable=True) 

    # UPGRADE 5: Robust enum-style structural field for global rules (e.g. Final Year Project II)
    special_requirement = Column(String, nullable=True) # 'ALL_STREAM_COURSES', 'ALL_COURSES', or NULL

    __table_args__ = (
        UniqueConstraint('course_code', 'curriculum_version_id', name='_course_version_uc'),
    )

    department = relationship("Department")
    stream = relationship("Stream", back_populates="courses")
    curriculum_version = relationship("CurriculumVersion", back_populates="courses")
    shared_streams = relationship("CourseStream", back_populates="course", cascade="all, delete-orphan")


# UPGRADE 4: Association table for courses shared by some but not all streams
class CourseStream(Base):
    """
    Junction table for courses shared by multiple streams, but not all of them.
    (e.g., Data Comm & Networks ECEg4406 for Computer and Communication streams)
    """
    __tablename__ = 'course_streams'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False)
    stream_id = Column(Integer, ForeignKey('streams.id'), nullable=False)

    course = relationship("Course", back_populates="shared_streams")
    stream = relationship("Stream")


class Prerequisite(Base):
    __tablename__ = 'prerequisites'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False)
    prerequisite_course_id = Column(Integer, ForeignKey('courses.id'), nullable=False)
    
    # UPGRADE 2: Filterable stream context for conditional prerequisites (e.g. IDP)
    applicable_stream_id = Column(Integer, ForeignKey('streams.id'), nullable=True) # NULL = applies to all streams

    applicable_stream = relationship("Stream")


class CommonCourse(Base):
    __tablename__ = 'common_courses'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False)
    shared_with_department_id = Column(Integer, ForeignKey('departments.id'), nullable=False)
    context_note = Column(String, nullable=True) 

    course = relationship("Course")
    shared_department = relationship("Department")


class Batch(Base):
    __tablename__ = 'batches'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=True) 
    entry_year = Column(Integer, nullable=False) 
    current_year_level = Column(Integer, nullable=False) 
    current_semester = Column(Integer, nullable=False) 
    curriculum_version_id = Column(Integer, ForeignKey('curriculum_versions.id'), nullable=False)

    department = relationship("Department", back_populates="batches")
    curriculum_version = relationship("CurriculumVersion", back_populates="batches")
    students = relationship("Student", back_populates="batch")


class Student(Base):
    __tablename__ = 'students'
    
    id = Column(String, primary_key=True) 
    name = Column(String, nullable=False)
    batch_id = Column(Integer, ForeignKey('batches.id'), nullable=False)
    stream_id = Column(Integer, ForeignKey('streams.id'), nullable=True) 
    cgpa = Column(Float, nullable=True) 
    status = Column(String, default="ACTIVE") 

    batch = relationship("Batch", back_populates="students")
    stream = relationship("Stream", back_populates="students")
    course_statuses = relationship("StudentCourseStatus", back_populates="student")


class StudentCourseStatus(Base):
    __tablename__ = 'student_course_status'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String, ForeignKey('students.id'), nullable=False)
    course_id = Column(Integer, ForeignKey('courses.id'), nullable=False)
    attempt_number = Column(Integer, nullable=True) 
    academic_year_taken = Column(Integer, nullable=False) 
    semester_taken = Column(Integer, nullable=False) 
    status = Column(String, nullable=False) 

    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', 'attempt_number', name='_student_course_attempt_uc'),
        Index('_student_course_dropped_uc', 'student_id', 'course_id', 
              unique=True, 
              sqlite_where=text("status = 'DROPPED'")),
    )

    student = relationship("Student", back_populates="course_statuses")
    course = relationship("Course")


class CampusRule(Base):
    __tablename__ = 'campus_rules'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_key = Column(String, nullable=False) 
    rule_value = Column(Float, nullable=False)
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=True) 
    year_level = Column(Integer, nullable=True) 
    description = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint('rule_key', 'department_id', 'year_level', name='_rule_scope_uc'),
    )


class UnansweredQuestionsLog(Base):
    __tablename__ = 'unanswered_questions_log'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String, ForeignKey('students.id'), nullable=True)
    raw_query = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


class UserFeedback(Base):
    __tablename__ = 'user_feedback'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String, ForeignKey('students.id'), nullable=False)
    bot_response_context = Column(String, nullable=False)
    rating = Column(Integer, nullable=False) 
    comments = Column(String, nullable=True)


if __name__ == "__main__":
    print("Generating schema tables with advanced Round 4 specifications...")
    Base.metadata.create_all(engine)
    print("Done! Database academic_records.db generated with all columns and constraints.")