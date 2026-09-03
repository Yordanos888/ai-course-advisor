"""
STAGE 8a REAL DATA
====================
Parses the real ECE curriculum CSV into the same course-dict shape every
prior stage's fake data used -- credit_hours, semester_offered, prereqs,
plus the newer fields from Stages 5-6 (stream, alt_parity, special_requirement).

DELIBERATELY DEFERRED TO STAGE 8b (excluded here, confirmed safe -- no
other course references any of these three as a prerequisite):

    ECEg4112 (Integrated Design Project) -- its prerequisite string is
        stream-CONDITIONAL: "IETP4115(All); ECEg4101,ECEg4103
        (Communication,Computer); ECEg4109(Power); ECEg4105(Control)".
        This needs a real conditional-prerequisite parser, not a naive
        comma split -- building that carelessly right now risks silently
        mis-assigning prerequisites to the wrong streams.

    ECEg5108 (Final Year Project II) -- prerequisite string is
        "ECEg5107, All Stream Major Courses": a MIX of a genuine strict
        prerequisite (ECEg5107, FYP-I) and the ALL_STREAM_COURSES
        special_requirement marker. This is exactly the distinction
        flagged earlier in the conversation (real prereqs need a strict
        `>`, the special_requirement itself only needs `>=`) -- handling
        it correctly needs the fix discussed, not a copy of Stage 6's
        uniform >= treatment.

    NEE5108 (National Exit Exam) -- prerequisite string is "All Courses",
        entirely a special_requirement marker, not a real course list.

Every other row (84 of 87) is a plain comma-separated course-code list or
"None", parsed straightforwardly.

STREAM SCOPE GENERALIZATION: the real data allows a course to belong to
MULTIPLE (but not all) streams at once, e.g. "Control, Power" or
"Computer, Control" -- Stages 5-6's fake data only ever needed a single
stream string or None. Real courses store `streams` as a SET of stream
names, or None for fully common.

CROSS-DEPARTMENT PARITY FLIP: rows whose Department Scope lists more
than one department (e.g. "ECE, EME") get alt_parity = the OTHER of
{1, 2} from their home semester_offered, per the confirmed assumption
that partner departments offer these at the opposite parity. This is an
ASSUMPTION (no real partner timetable data was available), not verified
fact -- flagged here and worth re-confirming with the department later.
"""

import csv

CSV_PATH = "C:/Users/yrdns/OneDrive/Desktop/AI/Project/AI-course advisor/ai-course-advisor/Reasoning_engine/ece_curriculum.csv"

DEFERRED_TO_STAGE_8B = {"ECEg4112", "ECEg5108", "NEE5108"}


def _parse_prereqs_simple(raw):
    raw = raw.strip()
    if raw == "" or raw.lower() == "none":
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_streams(raw):
    raw = raw.strip()
    if raw == "" or raw.lower() == "common":
        return None  # common to all streams
    return set(s.strip() for s in raw.split(",") if s.strip())


def _parse_department_scope(raw):
    return set(d.strip() for d in raw.split(",") if d.strip())


def load_real_courses(csv_path=CSV_PATH, include_deferred=False):
    """
    Returns (courses_dict, skipped_codes).
    courses_dict: course_code -> {credit_hours, year_level,
        semester_offered, alt_parity, streams, department_scope,
        prereqs, is_droppable, name}
    skipped_codes: the deferred-to-8b codes actually skipped, for
        visibility in tests/logging.
    """
    courses = {}
    skipped = []

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        code = r["Course Code"].strip()

        if code in DEFERRED_TO_STAGE_8B and not include_deferred:
            skipped.append(code)
            continue

        dept_scope = _parse_department_scope(r["Department Scope"])
        semester_offered = int(r["Semester Offered"])

        alt_parity = None
        if len(dept_scope) > 1 and semester_offered in (1, 2):
            alt_parity = 2 if semester_offered == 1 else 1

        courses[code] = {
            "name": r["Course Name"].strip(),
            "credit_hours": int(r["Credit Hours"]),
            "year_level": int(r["Year Level"]),
            "semester_offered": semester_offered,
            "alt_parity": alt_parity,
            "department_scope": dept_scope,
            "streams": _parse_streams(r["Stream Scope"]),
            "prereqs": _parse_prereqs_simple(r["Prerequisites (Split by commas)"]),
            "is_droppable": r["Is Droppable"].strip().upper() == "TRUE",
        }

    return courses, skipped