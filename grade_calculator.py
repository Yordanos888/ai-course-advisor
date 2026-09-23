"""
grade_calculator.py
=====================
Hardcoded, formula-based grade tools -- deliberately NOT part of the
constraint-solver reasoning engine (model_stage8_fixed.py / solve_schedule.py).
Three related tools, all driven by a single Telegram entry point (/gpa,
see gpa_bot.py):

  1. SGPA calculator: given one semester's actual course list (with
     drops/adds resolved) and per-course letter grades, compute that
     semester's SGPA.

  2/3. Goal-based CGPA/SGPA planning: given a student's declared current
     position, current CGPA, and a chosen future "end" semester, work
     across the (current .. end) semester window:
       (a) required SGPA -- solve for the minimum, evenly-balanced SGPA
           needed each remaining semester to hit a goal CGPA.
       (b) what-if CGPA -- given hypothetical per-semester SGPAs, compute
           the running CGPA after each semester.

  Both branches of 2/3 share the same intake pipeline (current semester,
  previous-semester credit adjustments, current CGPA, end semester,
  future-semester credit adjustments) and diverge only at the very end.
  gpa_bot.py implements that shared pipeline as a single conversation
  with a fork near the end; this module only holds the math + parsing.

GRADE SCALE (AASTU's official fixed-point scale):
    A+ [90-100] -> 4.00      B+ [75-80) -> 3.50     C- [45-50) -> 1.75
    A  [85-90)  -> 4.00      B  [70-75) -> 3.00     D  [40-45) -> 1.00
    A- [80-85)  -> 3.75      B- [65-70) -> 2.75     F  [0-40)  -> 0.00
                               C+ [60-65) -> 2.50
                               C  [50-60) -> 2.00
Students report the LETTER grade directly (matching how AASTU results
are actually released) -- no raw marks are entered anywhere here.

DISPLAY CONVENTION: every GPA-like number shown to the student is
rounded to exactly two decimal places -- no more, no less (per explicit
product decision: "no rounding [beyond that]. use two decimal places").
"""

import re

# ---------------------------------------------------------------------
# Grade scale
# ---------------------------------------------------------------------

GRADE_POINTS = {
    "A+": 4.00, "A": 4.00, "A-": 3.75,
    "B+": 3.50, "B": 3.00, "B-": 2.75,
    "C+": 2.50, "C": 2.00, "C-": 1.75,
    "D": 1.00, "F": 0.00,
}
VALID_GRADES = list(GRADE_POINTS.keys())


def normalize_grade(raw):
    """'a+', ' A+ ', 'A +' -> 'A+'. Returns None if not recognizable."""
    g = raw.strip().upper().replace(" ", "")
    return g if g in GRADE_POINTS else None


# ---------------------------------------------------------------------
# Semester bookkeeping helpers (pure -- no DB access)
# ---------------------------------------------------------------------

def _is_streaming_period(year, sem):
    """Streaming starts Year 4 Semester 2, matching model_stage8_fixed.py
    and query_api._stream_scope_is_relevant."""
    return year > 4 or (year == 4 and sem >= 2)


def parse_semester_token(text):
    """'3Y2S' / '3y2s' / '3 Y 2 S' -> (3, 2). None if it doesn't match."""
    m = re.match(r"^\s*(\d)\s*[Yy]\s*(\d)\s*[Ss]\s*$", text.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def format_semester_token(year, sem):
    return f"{year}Y{sem}S"


def semesters_in_window(start_year, start_sem, end_year, end_sem):
    """
    Ordered list of (year, sem) tuples from (start_year, start_sem)
    through (end_year, end_sem) inclusive, stepping through the
    curriculum's 2-semester-per-year structure.
    Raises ValueError if the end is before the start.
    """
    def to_index(y, s):
        return (y - 1) * 2 + (s - 1)

    start_idx = to_index(start_year, start_sem)
    end_idx = to_index(end_year, end_sem)
    if end_idx < start_idx:
        raise ValueError("The target semester can't be before the current semester.")

    window = []
    idx = start_idx
    while idx <= end_idx:
        y = idx // 2 + 1
        s = idx % 2 + 1
        window.append((y, s))
        idx += 1
    return window


def semesters_before(year, sem):
    """All (y, s) pairs strictly before (year, sem), starting at (1, 1)."""
    if year < 1 or sem not in (1, 2):
        raise ValueError("Invalid semester.")
    if year == 1 and sem == 1:
        return []
    return semesters_in_window(1, 1, year, sem)[:-1]


def parse_credit_override_string(text):
    """
    Parses '3Y2S=12, 4Y1S=19' -> {(3, 2): 12, (4, 1): 19}.
    Raises ValueError with a human-readable message on malformed input,
    so the caller can ask the user to re-enter rather than silently
    misreading it.
    """
    overrides = {}
    text = text.strip()
    if not text:
        return overrides
    parts = [p.strip() for p in text.split(",") if p.strip()]
    for part in parts:
        if "=" not in part:
            raise ValueError(f"Couldn't understand '{part}' -- expected format like '3Y2S=12'.")
        left, right = part.split("=", 1)
        sem_tuple = parse_semester_token(left)
        if sem_tuple is None:
            raise ValueError(f"Couldn't understand the semester in '{part}' -- expected e.g. '3Y2S'.")
        try:
            credits = int(right.strip())
        except ValueError:
            raise ValueError(f"Couldn't understand the credit-hour number in '{part}'.")
        if credits < 0:
            raise ValueError(f"Credit hours can't be negative ('{part}').")
        overrides[sem_tuple] = credits
    return overrides


def parse_course_list(text):
    """'None' (any case) -> []; otherwise split on commas. Matches the
    same convention bot.py already uses for failed/added/dropped lists."""
    text = text.strip()
    if text.lower() in ("none", "no", "-"):
        return []
    return [c.strip() for c in text.split(",") if c.strip()]


def parse_grade_list(text):
    """'A+, B, A-' -> ['A+', 'B', 'A-']. Does NOT validate the grades
    themselves -- callers should run normalize_grade on each entry."""
    return [g.strip() for g in text.split(",") if g.strip()]


def parse_sgpa_list(text):
    """'3.33, 3.21, 3.50' -> [3.33, 3.21, 3.50]. Raises ValueError on a
    non-numeric entry."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    values = []
    for p in parts:
        try:
            values.append(float(p))
        except ValueError:
            raise ValueError(f"Couldn't understand '{p}' as a number.")
    return values


# ---------------------------------------------------------------------
# Part 1: SGPA for one actual semester
# ---------------------------------------------------------------------

def compute_sgpa(course_grade_pairs):
    """
    course_grade_pairs: [(credit_hours, letter_grade), ...]
    Returns the SGPA as a float, rounded to 2 decimals.
    Raises ValueError on an unknown grade or zero total credits.
    """
    total_points = 0.0
    total_credits = 0
    for credit_hours, grade in course_grade_pairs:
        normalized = normalize_grade(grade)
        if normalized is None:
            raise ValueError(f"Unknown grade '{grade}'. Valid grades: {', '.join(VALID_GRADES)}.")
        total_points += GRADE_POINTS[normalized] * credit_hours
        total_credits += credit_hours
    if total_credits == 0:
        raise ValueError("Total credit hours is zero -- can't compute an SGPA.")
    return round(total_points / total_credits, 2)


# ---------------------------------------------------------------------
# Part 2: required (balanced minimum) SGPA for a goal CGPA
# ---------------------------------------------------------------------

def compute_required_sgpa(prev_total_credits, current_cgpa, future_credits_by_semester, goal_cgpa):
    """
    future_credits_by_semester: ordered list of credit-hour totals, one
        per semester in the future window (current semester through the
        goal semester, inclusive).

    The "minimum" SGPA is the SAME value applied evenly across every
    future semester -- spreading the needed grade points out evenly is
    exactly what minimizes the peak SGPA required in any single term
    (any uneven split would require a higher SGPA in at least one term
    to make up for a lower one elsewhere).

    Returns one of:
        {"feasible": True, "already_secured": True}
            -- goal is already mathematically guaranteed (required <= 0)
        {"feasible": False, "reason": "..."}
            -- required SGPA would exceed 4.00, i.e. impossible
        {"feasible": True, "already_secured": False, "required_sgpa": float}
            -- the normal case
    """
    total_future_credits = sum(future_credits_by_semester)
    if total_future_credits <= 0:
        raise ValueError("Total future credit hours must be greater than zero.")

    prior_points = current_cgpa * prev_total_credits
    total_credits_at_goal = prev_total_credits + total_future_credits
    required_total_points = goal_cgpa * total_credits_at_goal
    needed_points = required_total_points - prior_points

    required_sgpa = needed_points / total_future_credits

    if required_sgpa <= 0:
        return {"feasible": True, "already_secured": True, "required_sgpa": 0.0}

    if required_sgpa > 4.0:
        return {
            "feasible": False,
            "reason": (
                f"Reaching a {goal_cgpa:.2f} CGPA by then would require a "
                f"{required_sgpa:.2f} average SGPA across your remaining semesters, "
                f"which is above the maximum possible (4.00). This goal is not "
                f"mathematically achievable in that timeframe."
            ),
        }

    return {"feasible": True, "already_secured": False, "required_sgpa": round(required_sgpa, 2)}


# ---------------------------------------------------------------------
# Part 3: projected running CGPA from hypothetical SGPAs
# ---------------------------------------------------------------------

def compute_cgpa_projection(prev_total_credits, current_cgpa, future_credits_by_semester, hypothetical_sgpas):
    """
    future_credits_by_semester and hypothetical_sgpas must be the same
    length and in the same (chronological) order.

    Returns a list of dicts, one per future semester, each carrying the
    RUNNING cgpa after that semester finishes:
        [{"credits": c, "sgpa": s, "running_cgpa": ...}, ...]
    """
    if len(future_credits_by_semester) != len(hypothetical_sgpas):
        raise ValueError(
            f"Expected {len(future_credits_by_semester)} SGPA value(s) "
            f"(one per future semester), got {len(hypothetical_sgpas)}."
        )

    running_points = current_cgpa * prev_total_credits
    running_credits = prev_total_credits
    results = []
    for credits, sgpa in zip(future_credits_by_semester, hypothetical_sgpas):
        if not (0.0 <= sgpa <= 4.0):
            raise ValueError(f"SGPA values must be between 0.00 and 4.00 (got {sgpa}).")
        running_points += sgpa * credits
        running_credits += credits
        running_cgpa = running_points / running_credits if running_credits > 0 else 0.0
        results.append({
            "credits": credits,
            "sgpa": round(sgpa, 2),
            "running_cgpa": round(running_cgpa, 2),
        })
    return results


# ---------------------------------------------------------------------
# DB-backed helpers (require a SQLAlchemy session -- kept separate from
# the pure math above so the math can be unit-tested without a DB)
# ---------------------------------------------------------------------

def get_semester_courses(session, year, sem, student_stream=None):
    """
    Returns [{"code":..., "name":..., "credit_hours":...}, ...] for the
    NORMAL (before any drop/add) course list of one semester, filtered
    to the student's stream if this is a streaming-period semester and a
    stream is known. Ordered by course_code -- this fixes the order
    grades must later be entered in.
    """
    from models import Course, Stream
    from query_api import _resolve_course_stream_scope

    courses = (
        session.query(Course)
        .filter_by(year_level=year, semester_offered=sem)
        .order_by(Course.course_code)
        .all()
    )

    if _is_streaming_period(year, sem) and student_stream:
        stream_obj = session.query(Stream).filter(Stream.name.ilike(f"{student_stream}%")).first()
        if stream_obj:
            filtered = []
            for c in courses:
                scope = _resolve_course_stream_scope(session, c)
                if scope is None or stream_obj.id in scope:
                    filtered.append(c)
            courses = filtered

    return [{"code": c.course_code, "name": c.name, "credit_hours": c.credit_hours} for c in courses]


def semester_total_credits(session, year, sem, student_stream=None):
    return sum(c["credit_hours"] for c in get_semester_courses(session, year, sem, student_stream))


def resolve_credit_adjustment_courses(session, course_texts):
    """
    Resolves free-text course references (fuzzy, via the same resolver
    every other command uses) to their credit hours only -- this feature
    never needs the full course object, just the number to add/subtract
    from a running total.
    Returns (resolved_credit_hours_list, unresolved_texts).
    """
    from query_api import resolve_course_entity

    resolved = []
    unresolved = []
    for text in course_texts:
        text = text.strip()
        if not text:
            continue
        course = resolve_course_entity(session, text)
        if course is None:
            unresolved.append(text)
        else:
            resolved.append(course.credit_hours)
    return resolved, unresolved
