"""
db_loader.py
=============
Reads the curriculum directly from the database (Course, Prerequisite,
CourseStream, CommonCourse) -- the system's actual source of truth,
already populated by parse_curriculum.py. This REPLACES the CSV-based
loaders built during Stage 8 (data_real_stage8a.py / data_real_stage8b.py)
for integration purposes; those remain useful as standalone test
scaffolding but should not be the thing bot.py's plans are computed from.

Two-step design, mirroring the filter-then-resolve pattern used since
Stage 5:

  load_raw_courses_from_db(session) -> the full curriculum, with each
      course's raw prerequisite edges kept as (prereq_code, stream_name
      or None) pairs -- NOT yet resolved to one student's stream.

  filter_and_resolve_for_student(raw_courses, student_stream) -> the
      subset relevant to one student, with prereq edges resolved into a
      flat list exactly matching what model_stage8_fixed.build_and_solve
      already expects. Every other stage's tested solver code is
      untouched by this file.

Stream names used throughout match the short form already used
elsewhere in the codebase (student_service.py: `stream.name.split()[0]`)
-- "Computer", "Communication", "Control", "Power" -- not the full DB
name ("Computer Engineering").
"""

from models import Course, Prerequisite, CourseStream, CommonCourse, Stream, Department


def _short_stream_name(full_name):
    return full_name.split()[0]


def load_raw_courses_from_db(session):
    courses = {}
    stream_short_by_id = {s.id: _short_stream_name(s.name) for s in session.query(Stream).all()}
    dept_code_by_id = {d.id: d.code for d in session.query(Department).all()}

    all_courses = session.query(Course).all()
    id_to_code = {c.id: c.course_code for c in all_courses}

    coursestream_by_course = {}
    for cs in session.query(CourseStream).all():
        coursestream_by_course.setdefault(cs.course_id, set()).add(stream_short_by_id[cs.stream_id])

    # Map course_id -> partner department CODE (e.g. "EME", "SE"), for
    # display purposes ("Software Engineering (3 cr) [with SE department]")
    # -- not just whether a CommonCourse entry exists, but WHICH department.
    partner_dept_by_course = {
        cc.course_id: dept_code_by_id.get(cc.shared_with_department_id)
        for cc in session.query(CommonCourse).all()
    }

    prereq_edges_by_course = {}
    for p in session.query(Prerequisite).all():
        prereq_code = id_to_code.get(p.prerequisite_course_id)
        if prereq_code is None:
            continue  # dangling reference, shouldn't happen but don't crash on it
        stream_name = stream_short_by_id.get(p.applicable_stream_id) if p.applicable_stream_id else None
        prereq_edges_by_course.setdefault(p.course_id, []).append((prereq_code, stream_name))

    for c in all_courses:
        if c.stream_id is not None:
            streams = {stream_short_by_id[c.stream_id]}
        elif c.id in coursestream_by_course:
            streams = coursestream_by_course[c.id]
        else:
            streams = None  # common to all streams

        alt_parity = None
        alt_parity_department = None
        if c.id in partner_dept_by_course and c.semester_offered in (1, 2):
            alt_parity = 2 if c.semester_offered == 1 else 1
            alt_parity_department = partner_dept_by_course[c.id]

        courses[c.course_code] = {
            "name": c.name,
            "credit_hours": c.credit_hours,
            "year_level": c.year_level,
            "semester_offered": c.semester_offered,
            "alt_parity": alt_parity,
            "alt_parity_department": alt_parity_department,
            "streams": streams,
            "is_droppable": c.is_droppable,
            "special_requirement": c.special_requirement,
            "raw_prereq_edges": prereq_edges_by_course.get(c.id, []),
            # True once the course is genuinely part of the ECE
            # department's own curriculum, False for the university-wide
            # courses taken BEFORE department enrollment. Per the campus
            # doc: Year 1 (both semesters) is the shared "Freshman year"
            # for the whole university, and Year 2 Semester 1 is the
            # shared "pre-Engineering" term before a department is even
            # chosen -- a student hasn't joined ECE yet at that point, so
            # nothing there is "ECE major" work. Department_id/"Department
            # Scope" was tried first and rejected: it tracks who
            # *administers* a course (e.g. Industry Internship is tagged
            # "Common" because it's coordinated centrally, despite being
            # a core ECE requirement), not whether it's part of the major.
            "is_major": not (c.year_level == 1 or (c.year_level == 2 and c.semester_offered == 1)),
        }
    return courses


def filter_and_resolve_for_student(raw_courses, student_stream):
    """
    student_stream: short name ("Control", etc). Returns a course dict
    with "prereqs" fully resolved to a flat list -- the exact shape
    model_stage8_fixed.build_and_solve expects, unchanged since Stage 1.
    """
    filtered = {
        code: info for code, info in raw_courses.items()
        if info["streams"] is None or student_stream in info["streams"]
    }
    resolved = {}
    for code, info in filtered.items():
        prereqs = [
            prereq_code for prereq_code, stream_scope in info["raw_prereq_edges"]
            if stream_scope is None or stream_scope == student_stream
        ]
        new_info = dict(info)
        new_info["prereqs"] = prereqs
        resolved[code] = new_info
    return resolved
