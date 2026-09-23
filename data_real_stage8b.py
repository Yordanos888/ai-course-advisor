"""
STAGE 8b: SPECIAL ROW PARSERS
==============================
Handles the 3 rows deferred from Stage 8a, each needing real parsing
logic instead of a naive comma-split:

  ECEg4112 (Integrated Design Project): stream-CONDITIONAL prerequisites.
    Raw: "IETP4115(All); ECEg4101,ECEg4103(Communication,Computer);
          ECEg4109(Power); ECEg4105(Control)"
    Format: semicolon-separated groups, each
        "<code>[,<code>...](<stream>[,<stream>...] | All)"
    Parsed into a list of {course, streams} entries -- streams=None means
    "applies regardless of stream" (the "(All)" case).

  ECEg5108 (FYP-II): a MIX of a genuine prerequisite and a structural
    marker, not a uniform course list.
    Raw: "ECEg5107, All Stream Major Courses"
    "ECEg5107" -> a real, unconditional prerequisite (strict > applies,
        via the ordinary prereqs mechanism already tested in every prior
        stage -- no model changes needed for this half).
    "All Stream Major Courses" -> NOT a course. Maps to
        special_requirement = "ALL_STREAM_COURSES" (the existing Stage 6
        mechanism, >= against every other stream course).
    THE FIX FLAGGED EARLIER IN THE CONVERSATION: because ECEg5107 lands
    in the REAL prereqs list, build_and_solve's normal prereq loop adds a
    STRICT > constraint for it. The ALL_STREAM_COURSES sweep separately
    adds a >= constraint against EVERY course including ECEg5107 -- that
    second constraint is redundant/subsumed by the stricter one already
    in place, not contradictory, so no core model changes were actually
    needed. Getting ECEg5107 correctly INTO the real prereqs list (this
    parser's job) was the actual fix.

  NEE5108 (National Exit Exam): entirely a marker, no real course list.
    Raw: "All Courses" -> special_requirement = "ALL_COURSES", no
    prerequisites at all (matches Stage 6's post-hoc NEE mechanism).
"""

import re


def parse_conditional_prereq_string(raw):
    """
    Returns a list of {"course": code, "streams": set_or_None} entries.
    One entry per individual course code (a segment listing multiple
    courses under one stream-condition, e.g. "ECEg4101,ECEg4103
    (Communication,Computer)", expands to TWO separate entries sharing
    the same streams set).
    """
    entries = []
    segments = [s.strip() for s in raw.split(";") if s.strip()]
    for seg in segments:
        m = re.match(r"^(.*?)\((.*?)\)$", seg)
        if not m:
            raise ValueError(f"Could not parse conditional prereq segment: {seg!r}")
        codes_part, streams_part = m.group(1), m.group(2)
        codes = [c.strip() for c in codes_part.split(",") if c.strip()]
        streams_raw = [s.strip() for s in streams_part.split(",") if s.strip()]
        streams = None if streams_raw == ["All"] else set(streams_raw)
        for code in codes:
            entries.append({"course": code, "streams": streams})
    return entries


def resolve_conditional_prereqs(courses, student_stream):
    """
    Takes the full course dict (with some courses carrying a
    "conditional_prereqs" field) and a specific student's stream, and
    returns a NEW course dict where every course's "prereqs" list is
    fully resolved to a flat list of course codes -- exactly the shape
    every prior stage's build_and_solve already expects, unchanged.

    This keeps the tested solver core completely untouched: all
    stream-conditional logic is resolved as a data-preparation step
    BEFORE the courses ever reach build_and_solve.
    """
    resolved = {}
    for code, info in courses.items():
        new_info = dict(info)
        base_prereqs = list(info.get("prereqs", []))
        for entry in info.get("conditional_prereqs", []):
            if entry["streams"] is None or student_stream in entry["streams"]:
                base_prereqs.append(entry["course"])
        new_info["prereqs"] = base_prereqs
        resolved[code] = new_info
    return resolved


def parse_special_rows(row_by_code):
    """
    row_by_code: dict of course_code -> raw CSV row (DictReader row) for
    JUST the 3 deferred codes. Returns a dict course_code -> the extra
    fields to merge into that course's normal parsed entry:
        {"prereqs": [...], "conditional_prereqs": [...], "special_requirement": ... or None}
    """
    out = {}

    # ECEg4112: entirely conditional, no unconditional prereqs of its own.
    raw_4112 = row_by_code["ECEg4112"]["Prerequisites (Split by commas)"]
    out["ECEg4112"] = {
        "prereqs": [],
        "conditional_prereqs": parse_conditional_prereq_string(raw_4112),
        "special_requirement": None,
    }

    # ECEg5108: ECEg5107 is real, "All Stream Major Courses" is the marker.
    out["ECEg5108"] = {
        "prereqs": ["ECEg5107"],
        "conditional_prereqs": [],
        "special_requirement": "ALL_STREAM_COURSES",
    }

    # NEE5108: entirely a marker, no real prerequisites.
    out["NEE5108"] = {
        "prereqs": [],
        "conditional_prereqs": [],
        "special_requirement": "ALL_COURSES",
    }

    return out


def load_real_courses_full(csv_path=None):
    """
    Returns the COMPLETE 87-course curriculum: the 84 normal courses from
    Stage 8a's parser, PLUS the 3 previously-deferred rows, now properly
    parsed with conditional-prerequisite and special_requirement support.
    """
    import csv as csv_module
    from data_real_stage8a import (
        load_real_courses, CSV_PATH, DEFERRED_TO_STAGE_8B,
        _parse_streams, _parse_department_scope,
    )

    path = csv_path or CSV_PATH
    courses, _ = load_real_courses(path, include_deferred=False)

    with open(path, newline="") as f:
        rows = list(csv_module.DictReader(f))
    row_by_code = {r["Course Code"].strip(): r for r in rows if r["Course Code"].strip() in DEFERRED_TO_STAGE_8B}

    special_fields = parse_special_rows(row_by_code)

    for code in DEFERRED_TO_STAGE_8B:
        r = row_by_code[code]
        dept_scope = _parse_department_scope(r["Department Scope"])
        year_level = int(r["Year Level"])
        semester_offered = int(r["Semester Offered"])
        alt_parity = None
        if len(dept_scope) > 1 and semester_offered in (1, 2):
            alt_parity = 2 if semester_offered == 1 else 1

        courses[code] = {
            "name": r["Course Name"].strip(),
            "credit_hours": int(r["Credit Hours"]),
            "year_level": year_level,
            "semester_offered": semester_offered,
            "alt_parity": alt_parity,
            "department_scope": dept_scope,
            "streams": _parse_streams(r["Stream Scope"]),
            "is_droppable": r["Is Droppable"].strip().upper() == "TRUE",
            # True once genuinely part of the ECE department curriculum,
            # False for the university-wide pre-department-enrollment
            # courses -- see db_loader.py's matching field for the full
            # rationale. (All 3 deferred rows here are Year 4-5, so this
            # is always True for them; the field is set for consistency.)
            "is_major": not (year_level == 1 or (year_level == 2 and semester_offered == 1)),
            **special_fields[code],
        }

    return courses