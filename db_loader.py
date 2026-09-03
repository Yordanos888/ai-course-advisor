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

from models import Course, Prerequisite, CourseStream, CommonCourse, Stream


def _short_stream_name(full_name):
    return full_name.split()[0]


def load_raw_courses_from_db(session):
    courses = {}
    stream_short_by_id = {s.id: _short_stream_name(s.name) for s in session.query(Stream).all()}

    all_courses = session.query(Course).all()
    id_to_code = {c.id: c.course_code for c in all_courses}

    coursestream_by_course = {}
    for cs in session.query(CourseStream).all():
        coursestream_by_course.setdefault(cs.course_id, set()).add(stream_short_by_id[cs.stream_id])

    has_common_course_entry = {cc.course_id for cc in session.query(CommonCourse).all()}

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
        if c.id in has_common_course_entry and c.semester_offered in (1, 2):
            alt_parity = 2 if c.semester_offered == 1 else 1

        courses[c.course_code] = {
            "name": c.name,
            "credit_hours": c.credit_hours,
            "year_level": c.year_level,
            "semester_offered": c.semester_offered,
            "alt_parity": alt_parity,
            "streams": streams,
            "is_droppable": c.is_droppable,
            "special_requirement": c.special_requirement,
            "raw_prereq_edges": prereq_edges_by_course.get(c.id, []),
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
