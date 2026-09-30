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
import math

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


def _ceiling_round(value, decimals=2):
    """
    Rounds UP to the given number of decimals -- NOT the nearest value.

    This matters specifically for the required-SGPA calculation: standard
    round() can round a true requirement like 3.5631... DOWN to 3.56,
    but a student who then achieves exactly 3.56 falls a hair short of
    the actual goal (3.56 * credits < the points truly needed). Rounding
    UP instead guarantees that hitting the displayed number is always
    genuinely sufficient -- and it's still the smallest such 2-decimal
    number, i.e. the true achievable minimum, not just "close to" it.

    A small extra round() at higher precision guards against float noise
    (e.g. a value that's mathematically exactly 3.56 landing at
    3.5600000000000005 due to floating-point arithmetic) from being
    pushed up to 3.57 by mistake.

    NOTE: This function is retained for general use, but compute_required_sgpa
    no longer calls it -- that function now uses pure integer arithmetic
    (see its docstring) which is exact and immune to float noise entirely.
    """
    factor = 10 ** decimals
    return math.ceil(round(value, decimals + 6) * factor) / factor


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


def is_out_of_batch(year, sem):
    """True for any semester beyond the official 5-year ECE curriculum (i.e. year > 5).
    Out-of-batch semesters have no courses in the DB; the student must supply
    credit-hour totals manually."""
    return year > 5


MAX_PLANNING_YEAR = 10  # generous ceiling; supports out-of-batch students beyond Y5S2


def semesters_for_year(year):
    """
    Which semester numbers exist within a given academic year.

    Every year normally has semesters 1 and 2 (per the campus rule: "a
    fixed semester, either 1st or 2nd, in each year"). Year 4 is the ONE
    exception: it also has a 3rd, summer-position term -- the Industry
    Internship (ECEg4100, 6 credit hours, confirmed against the real
    curriculum) -- which the student takes and is graded for like any
    other semester (its own SGPA out of 4.00), even though no other year
    has anything at semester_offered=3. This is NOT a uniform "3
    semesters every year" system; the summer slot only exists in Year 4.
    """
    if year == 4:
        return [1, 2, 3]
    return [1, 2]


def parse_semester_token(text):
    """'3Y2S' / '3y2s' / '3 Y 2 S' -> (3, 2). None if it doesn't match."""
    m = re.match(r"^\s*(\d)\s*[Yy]\s*(\d)\s*[Ss]\s*$", text.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_year_sem_input(text):
    """
    Parses a combined year+semester reply -- '4, 2', '4,2', '4 2', or the
    compact '4Y2S' token, whichever the student naturally types.
    Returns (year, sem) as ints. Raises ValueError (human-readable) on
    anything else -- does NOT validate that the semester actually exists
    for that year; call validate_year_sem() separately for that.
    """
    text = text.strip()
    token = parse_semester_token(text)
    if token:
        return token

    parts = [p for p in re.split(r"[,\s]+", text) if p]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("Please enter your year and semester together, e.g. '4, 2'.")
    return int(parts[0]), int(parts[1])


def validate_year_sem(year, sem):
    """True if (year, sem) is a real, selectable semester.
    Years 6+ (out-of-batch) are accepted with semesters 1 and 2 only."""
    if not (1 <= year <= MAX_PLANNING_YEAR):
        return False
    return sem in semesters_for_year(year)


def format_semester_token(year, sem):
    return f"{year}Y{sem}S"


def format_semester_list(window):
    """Human phrasing for a list of (year, sem) tuples:
    one -> '4Y1S'; two -> '4Y1S and 4Y2S'; three+ -> 'A, B, and C'."""
    tokens = [format_semester_token(y, s) for (y, s) in window]
    if len(tokens) == 1:
        return tokens[0]
    if len(tokens) == 2:
        return f"{tokens[0]} and {tokens[1]}"
    return ", ".join(tokens[:-1]) + f", and {tokens[-1]}"


def _ordered_semesters(up_to_year):
    """All (year, sem) pairs in chronological order from (1, 1) through
    the last semester of up_to_year, inclusive -- respecting
    semesters_for_year's per-year semester count (Year 4's extra
    Internship term included in its proper place)."""
    seq = []
    for y in range(1, up_to_year + 1):
        for s in semesters_for_year(y):
            seq.append((y, s))
    return seq


def semesters_in_window(start_year, start_sem, end_year, end_sem):
    """
    Ordered list of (year, sem) tuples from (start_year, start_sem)
    through (end_year, end_sem) inclusive. Raises ValueError if either
    endpoint isn't a real semester, or if the end is before the start.
    """
    if not validate_year_sem(start_year, start_sem):
        raise ValueError(f"{format_semester_token(start_year, start_sem)} isn't a real semester.")
    if not validate_year_sem(end_year, end_sem):
        raise ValueError(f"{format_semester_token(end_year, end_sem)} isn't a real semester.")

    seq = _ordered_semesters(max(start_year, end_year, MAX_PLANNING_YEAR))
    start_idx = seq.index((start_year, start_sem))
    end_idx = seq.index((end_year, end_sem))
    if end_idx < start_idx:
        raise ValueError("The target semester can't be before the current semester.")
    return seq[start_idx:end_idx + 1]


def semesters_before(year, sem):
    """All (y, s) pairs strictly before (year, sem), starting at (1, 1)."""
    if not validate_year_sem(year, sem):
        raise ValueError(f"{format_semester_token(year, sem)} isn't a real semester.")
    if (year, sem) == (1, 1):
        return []
    seq = _ordered_semesters(max(year, MAX_PLANNING_YEAR))
    idx = seq.index((year, sem))
    return seq[:idx]


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
        {"feasible": True, "already_secured": True, "required_sgpa": 0.0}
            -- goal is already mathematically guaranteed (required <= 0)
        {"feasible": False, "required_sgpa": float}
            -- required SGPA would exceed 4.00, i.e. impossible. The
            value is still returned (ceiling-rounded) so the caller can
            report exactly how far past 4.00 it would need to go; NO
            sentence is composed here, since whether it reads "in that
            semester" or "across your remaining semesters" depends on
            how many future semesters there are -- something only the
            caller (gpa_bot.py) knows. Composing the full message here
            would either hardcode a plural that reads wrong for a
            single-semester window, or require this pure-math module to
            take on UI phrasing.
        {"feasible": True, "already_secured": False, "required_sgpa": float}
            -- the normal case

    PRECISION: uses pure integer arithmetic to eliminate floating-point
    errors entirely. Since current_cgpa and goal_cgpa are always 2-decimal-
    place values (the system enforces this), we convert them to integer
    "cents" (multiply by 100), perform all operations in integer space,
    and use integer ceiling division -- (a + b - 1) // b -- which is
    exact by definition. This prevents the class of bugs where a
    mathematically-exact required SGPA of, say, 3.79 is represented as
    3.7900000001 in float, causing a spurious ceiling step to 3.80.
    """
    total_future_credits = sum(future_credits_by_semester)
    if total_future_credits <= 0:
        raise ValueError("Total future credit hours must be greater than zero.")

    # Convert 2-decimal CGPA floats to integer cents to enable exact arithmetic.
    curr_cents = round(current_cgpa * 100)
    goal_cents = round(goal_cgpa * 100)
    total_credits = prev_total_credits + total_future_credits

    # needed_points = goal * total_credits - curr * prev_total_credits
    # Multiplied by 100 to stay in integer space:
    needed_cents = goal_cents * total_credits - curr_cents * prev_total_credits

    if needed_cents <= 0:
        return {"feasible": True, "already_secured": True, "required_sgpa": 0.0}

    # required_sgpa = needed_cents / (total_future_credits * 100)
    # Ceiling at 2 decimals = ceil(needed_cents / total_future_credits) / 100
    # Integer ceiling: (a + b - 1) // b  (exact, no float division needed)
    required_hundredths = (needed_cents + total_future_credits - 1) // total_future_credits

    if required_hundredths > 400:
        return {"feasible": False, "required_sgpa": required_hundredths / 100}

    return {
        "feasible": True,
        "already_secured": False,
        "required_sgpa": min(required_hundredths / 100, 4.0),
    }


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
