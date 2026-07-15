import csv
import re
from sqlalchemy.orm import sessionmaker
from models import (
    engine, Course, Prerequisite, Department, Stream, 
    CurriculumVersion, CommonCourse, CourseStream
)

Session = sessionmaker(bind=engine)
session = Session()

CSV_FILE_PATH = "ece_curriculum.csv"

def parse_prerequisite_field(prereq_str, streams_map):
    """
    Parses strings containing stream-conditional brackets:
    e.g., "IETP4115(All); ECEg4101,ECEg4103(Communication,Computer); ECEg4109(Power); ECEg4105(Control)"
    Returns a list of dicts: [{'course_code': str, 'applicable_stream_id': int or None}]
    """
    results = []
    prereq_str = prereq_str.strip()
    if not prereq_str or prereq_str.upper() == "NONE":
        return results

    # Separate distinct groups divided by semicolons
    blocks = [b.strip() for b in prereq_str.split(";")]
    for block in blocks:
        if not block:
            continue
        
        # Check if there is a parenthesis specifying the stream conditions
        match = re.search(r"\(([^)]+)\)", block)
        if match:
            stream_content = match.group(1).strip()
            # Extract everything before the parenthesis and split by commas
            codes_raw = block[:match.start()].split(",")
            codes = [c.strip() for c in codes_raw if c.strip()]
            
            streams_raw = [s.strip() for s in stream_content.split(",")]
            
            # Resolve stream names to stream IDs
            stream_ids = []
            for s_name in streams_raw:
                if s_name.lower() == "all":
                    stream_ids.append(None)
                else:
                    # Case-insensitive lookup
                    matched_id = None
                    for name, s_id in streams_map.items():
                        if name.lower() == s_name.lower():
                            matched_id = s_id
                            break
                    if matched_id:
                        stream_ids.append(matched_id)
                    else:
                        print(f"⚠️ Warning: Could not match stream '{s_name}' for conditional prerequisite.")
            
            # Map every course in this block to every resolved stream constraint
            for code in codes:
                for s_id in (stream_ids if stream_ids else [None]):
                    results.append({'course_code': code, 'applicable_stream_id': s_id})
        else:
            # Standard comma-separated prerequisites (applies to all streams)
            codes = [c.strip() for c in block.split(",") if c.strip()]
            for code in codes:
                results.append({'course_code': code, 'applicable_stream_id': None})
                
    return results


def parse_and_load_curriculum():
    print("📖 Starting Round 4 Curriculum Loader...")

    ece_dept = session.query(Department).filter_by(code="ECE").first()
    if not ece_dept:
        print("❌ ECE Department not found. Please execute seed_data.py first!")
        return

    curriculum = session.query(CurriculumVersion).filter_by(version_name="ECE-Curriculum-2022").first()
    if not curriculum:
        curriculum = CurriculumVersion(
            version_name="ECE-Curriculum-2022",
            department_id=ece_dept.id,
            is_active=True
        )
        session.add(curriculum)
        session.flush()

    # Pre-fetch streams for mapping (maps e.g. "Computer" -> Stream.id)
    streams_map = {s.name.split()[0]: s.id for s in session.query(Stream).filter_by(department_id=ece_dept.id).all()}

    # Initialize Auxiliary departments
    dept_map = {"ECE": ece_dept.id}
    def get_or_create_dept(code, name):
        if code in dept_map:
            return dept_map[code]
        dept = session.query(Department).filter_by(code=code).first()
        if not dept:
            dept = Department(name=name, code=code)
            session.add(dept)
            session.flush()
        dept_map[code] = dept.id
        return dept.id

    get_or_create_dept("SE", "Software Engineering")
    get_or_create_dept("EME", "Electromechanical Engineering")

    raw_prerequisites_queue = []
    inserted_courses = {} # Case-insensitive index: Lowercase course code -> Course DB object

    # Stage 1: Load and Insert Courses
    with open(CSV_FILE_PATH, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        
        for row in reader:
            code = row['Course Code'].strip()
            name = row['Course Name'].strip()
            credits = int(row['Credit Hours'].strip())
            year = int(row['Year Level'].strip())
            semester = int(row['Semester Offered'].strip())
            dept_scope = row['Department Scope'].strip()
            stream_scope = row['Stream Scope'].strip()
            prereqs_str = row['Prerequisites (Split by commas)'].strip()
            is_droppable = row['Is Droppable'].strip().upper() == 'TRUE'

            db_dept_id = None
            db_stream_id = None
            special_req = None
            note_content = []

            # Resolve Department Scopes
            if dept_scope == "Common":
                db_dept_id = None
            elif dept_scope == "ECE":
                db_dept_id = ece_dept.id
            elif "ECE" in dept_scope:
                db_dept_id = ece_dept.id
                shared_codes = [d.strip() for d in dept_scope.split(",") if d.strip() != "ECE"]
                note_content.append(f"Shared with: {', '.join(shared_codes)}")

            # Parse specialized/global structural rules
            # We map this to our concrete, reliable courses.special_requirement column!
            if "All Stream Major Courses" in prereqs_str:
                special_req = "ALL_STREAM_COURSES"
            elif "All Courses" in prereqs_str:
                special_req = "ALL_COURSES"

            # Parse Stream Scope & populate CourseStreams for subset groups
            shared_streams_list = []
            if stream_scope == "Common":
                db_stream_id = None
            elif "," in stream_scope:
                db_stream_id = None # Shared, but not universally common
                note_content.append(f"Streams: {stream_scope}")
                scopes = [s.strip() for s in stream_scope.split(",")]
                for sc in scopes:
                    s_id = streams_map.get(sc)
                    if s_id:
                        shared_streams_list.append(s_id)
            else:
                db_stream_id = streams_map.get(stream_scope)

            final_note = "; ".join(note_content) if note_content else None

            course_obj = Course(
                course_code=code,
                name=name,
                credit_hours=credits,
                semester_offered=semester,
                year_level=year,
                department_id=db_dept_id,
                stream_id=db_stream_id,
                curriculum_version_id=curriculum.id,
                is_droppable=is_droppable,
                special_requirement=special_req,
                note=final_note
            )
            session.add(course_obj)
            
            # Map using lowercase key to achieve defensive case-insensitivity
            inserted_courses[code.lower()] = course_obj

            # Flush to capture the newly generated Course ID for association tables
            session.flush()

            # Populate CourseStream junction entries for multi-stream scopes
            for s_id in shared_streams_list:
                junction_entry = CourseStream(course_id=course_obj.id, stream_id=s_id)
                session.add(junction_entry)

            # Queue up prereqs for the second pass
            if prereqs_str.upper() != "NONE":
                raw_prerequisites_queue.append((code, prereqs_str))

    print(f"✅ Staged and indexed {len(inserted_courses)} courses.")

    # Stage 2: Create Common Course Connections & compute Semester Flip notes
    print("🔗 Computing cross-department semester flips...")
    for lower_code, course_obj in inserted_courses.items():
        if course_obj.note and "Shared with:" in course_obj.note:
            shared_text = course_obj.note.split("Shared with:")[1].split(";")[0].strip()
            shared_depts = [d.strip() for d in shared_text.split(",")]
            
            for dept_code in shared_depts:
                s_dept_id = dept_map.get(dept_code)
                if s_dept_id:
                    # COMPUTED RULE: Flipping semester representation (other_semester = 3 - ece_semester)
                    ece_sem = course_obj.semester_offered
                    other_sem = 3 - ece_sem
                    
                    computed_note = (
                        f"Shared {course_obj.course_code} with {dept_code}; "
                        f"offered in semester {other_sem} for {dept_code} "
                        f"(ECE offers it in semester {ece_sem})."
                    )
                    
                    sharing_entry = CommonCourse(
                        course_id=course_obj.id,
                        shared_with_department_id=s_dept_id,
                        context_note=computed_note
                    )
                    session.add(sharing_entry)

    # Stage 3: Resolve Prerequisites with Case-Insensitive protection & Brackets parsing
    print("🌿 Establishing robust prerequisite links...")
    prereq_count = 0
    for target_code, prereqs_str in raw_prerequisites_queue:
        target_course = inserted_courses.get(target_code.lower())
        if not target_course:
            continue

        resolved_edges = parse_prerequisite_field(prereqs_str, streams_map)
        for edge_info in resolved_edges:
            prereq_code_raw = edge_info['course_code']
            
            # Defensive measure: Case-insensitive lookup match
            prereq_course = inserted_courses.get(prereq_code_raw.lower())
            
            if prereq_course:
                prereq_row = Prerequisite(
                    course_id=target_course.id,
                    prerequisite_course_id=prereq_course.id,
                    applicable_stream_id=edge_info['applicable_stream_id']
                )
                session.add(prereq_row)
                prereq_count += 1
            else:
                # Ignore global placeholders, but warn about broken codes
                if prereq_code_raw not in ["All Stream Major Courses", "All Courses"]:
                    print(f"⚠️ Warning: Prerequisite code '{prereq_code_raw}' not found in course catalog index.")

    try:
        session.commit()
        print(f"\n🎉 Curriculum load complete! Inserted {len(inserted_courses)} courses, "
              f"mapped shared stream indexes, and established {prereq_count} prerequisite constraints.")
    except Exception as e:
        session.rollback()
        print(f"❌ Core Parser transaction failed: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    parse_and_load_curriculum()