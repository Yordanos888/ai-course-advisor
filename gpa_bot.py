"""
gpa_bot.py
===========
Telegram front-end for the grade-calculation feature (see
grade_calculator.py for all the actual math). Self-contained
ConversationHandler, entry point /gpa -- add it to bot.py's main() with:

    from gpa_bot import gpa_conv_handler
    app.add_handler(gpa_conv_handler)   # BEFORE the catch-all MessageHandler

Deliberately kept separate from bot.py (same reasoning bot.py already
uses for query_api.py / orchestrator.py): this is a hardcoded,
formula-driven feature, NOT part of the constraint-solver reasoning
engine, and giving it its own file keeps that boundary obvious.

THREE SUB-FEATURES BEHIND ONE ENTRY POINT (/gpa):
  1. SGPA calculator for one actual semester.
  2/3. Goal-based CGPA/SGPA planning -- shares one intake pipeline
       (current semester -> past adjustments -> current CGPA -> end
       semester -> future adjustments), then forks at the very end into
       either "solve for required SGPA" or "project CGPA from
       hypothetical SGPAs".

CONTINUOUS CALCULATION: neither sub-feature ends the conversation after
one answer. After every result, the bot offers a small menu to try
another combination (different grades, a different goal, a different
semester, or switching between the required-SGPA and what-if-projection
modes) using the SAME already-entered context, and only actually ends
the conversation when the student explicitly chooses to finish.

YEAR/SEMESTER INPUT: always asked as ONE combined reply (e.g. "4, 2")
rather than two separate prompts, to shorten this already-long
conversation. See grade_calculator.parse_year_sem_input.

INTERNSHIP (YEAR 4 SEMESTER 3): Year 4 is the only year with a 3rd,
summer-position semester -- the Industry Internship -- which is graded
like any other semester. grade_calculator.semesters_for_year /
validate_year_sem enforce this (semester 3 is only ever accepted when
the year is 4); this file just surfaces that via user-facing errors.

STREAM HANDLING: stream only matters for a semester at/after Year 4
Semester 2 (see grade_calculator._is_streaming_period). The bot ALWAYS
asks for it fresh, exactly once per /gpa session, at the first point it
becomes necessary -- it deliberately never reuses student.stream_id from
an unrelated earlier /course_planning session, since that value could be
stale, wrong, or simply not what the student wants to explore for this
particular GPA calculation. GPA math is sensitive enough to verify every
time it matters rather than silently trusting old data.
"""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler, filters, ContextTypes
)
from sqlalchemy.orm import sessionmaker

from models import engine
from student_service import get_student_by_telegram_id
from query_api import resolve_course_entity
import grade_calculator as gc

Session = sessionmaker(bind=engine)

STREAM_NAMES = ["Computer", "Communication", "Control", "Power"]
STREAM_MAP = {'1': 'Computer', '2': 'Communication', '3': 'Control', '4': 'Power'}

(
    GPA_MENU,
    SGPA_SEMESTER, SGPA_STREAM, SGPA_OOB_CREDITS, SGPA_DROPPED, SGPA_ADDED, SGPA_GRADES, SGPA_CONTINUE,
    CG_SEMESTER, CG_STREAM,
    CG_PAST_DROPPED, CG_PAST_ADDED,
    CG_CURRENT_CGPA,
    CG_END_SEMESTER,
    CG_OOB_CREDITS,
    CG_FUTURE_OVERRIDES,
    CG_MODE,
    CG_GOAL_CGPA, CG_GOAL_CONTINUE,
    CG_HYPOTHETICAL_SGPAS, CG_PROJECTION_CONTINUE,
) = range(21)


# ==========================================
# Shared small helpers
# ==========================================

async def _require_registration(update: Update):
    student = get_student_by_telegram_id(str(update.effective_user.id))
    if not student:
        await update.message.reply_text("⚠️ Please register with your Student ID first (send /start).")
        return None
    return student


def _year_sem_error_message(year, sem):
    """Human-readable explanation for why a (year, sem) pair was rejected."""
    if not (1 <= year <= gc.MAX_PLANNING_YEAR):
        return (
            f"❌ Year must be between 1 and {gc.MAX_PLANNING_YEAR}. "
            "Please re-enter, e.g. '4, 2'."
        )
    valid = gc.semesters_for_year(year)
    if sem == 3 and year != 4:
        return (
            f"❌ Year {year} doesn't have a semester 3 -- only Year 4 has a 3rd term "
            f"(the Industry Internship). Please re-enter, e.g. '{year}, 1'."
        )
    valid_str = "/".join(str(s) for s in valid)
    return f"❌ Year {year} only has semester(s) {valid_str}. Please re-enter, e.g. '{year}, {valid[0]}'."


async def cancel_gpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("GPA calculation cancelled.")
    return ConversationHandler.END


# ==========================================
# Entry point / top-level menu
# ==========================================

async def start_gpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = await _require_registration(update)
    if student is None:
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "📊 <b>Grade Calculator</b>\n\n"
        "What would you like to do?\n"
        "[1] Calculate my SGPA for a semester\n"
        "[2] Plan or project my CGPA (goal-based)\n\n"
        "Send /cancel at any time to stop.",
        parse_mode=ParseMode.HTML,
    )
    return GPA_MENU


async def handle_gpa_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == '1':
        await update.message.reply_text(
            "Which academic year and semester? Reply with both together, e.g. '4, 2'.\n"
            "(Year 4 also has a 3rd term for the Industry Internship, e.g. '4, 3'.)"
        )
        return SGPA_SEMESTER
    elif text == '2':
        await update.message.reply_text(
            "What is your CURRENT academic year and semester? Reply with both together, e.g. '3, 1'."
        )
        return CG_SEMESTER
    else:
        await update.message.reply_text("❌ Please reply with 1 or 2.")
        return GPA_MENU


# ==========================================
# PART 1: SGPA for one actual semester
# ==========================================

async def handle_sgpa_semester(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year, sem = gc.parse_year_sem_input(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return SGPA_SEMESTER
    if not gc.validate_year_sem(year, sem):
        await update.message.reply_text(_year_sem_error_message(year, sem))
        return SGPA_SEMESTER

    context.user_data['sgpa_year'] = year
    context.user_data['sgpa_sem'] = sem

    # Out-of-batch: beyond the official 5-year curriculum, no courses in DB.
    # Stream is irrelevant here -- just ask how many credits each course is.
    if gc.is_out_of_batch(year, sem):
        context.user_data['sgpa_stream'] = None
        await update.message.reply_text(
            f"📋 <b>{gc.format_semester_token(year, sem)}</b> is beyond the standard "
            f"5-year curriculum — your courses aren't registered in our system.\n\n"
            f"How many courses are you taking and what are their credit hours? "
            f"Enter the credit hours comma-separated, one per course.\n"
            f"Example: <code>3, 3, 2, 6</code>",
            parse_mode=ParseMode.HTML,
        )
        return SGPA_OOB_CREDITS

    # Always ask fresh -- never trust student.stream_id from an unrelated
    # earlier /course_planning session. A stream on file there could be
    # stale, wrong, or the student may want to explore a different
    # scenario; GPA math is sensitive enough to verify every time it
    # actually matters, rather than silently reusing old data.
    if gc._is_streaming_period(year, sem):
        await update.message.reply_text(
            "That semester is in the stream-specific period. Which stream are you in?\n"
            "[1] Computer\n[2] Communication\n[3] Control\n[4] Power"
        )
        return SGPA_STREAM

    context.user_data['sgpa_stream'] = None
    return await _sgpa_show_courses(update, context)


async def handle_sgpa_oob_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect credit hours for each course in an out-of-batch semester.

    The student enters a comma-separated list of credit-hour values (one per
    course), e.g. '3, 3, 2, 6'. Synthetic course entries are built from these
    so the existing grade-entry / SGPA-calculation flow works unchanged."""
    parts = [p.strip() for p in update.message.text.split(',') if p.strip()]
    if not parts:
        await update.message.reply_text(
            "❌ Please enter at least one credit-hour value, e.g. <code>3, 3, 2, 6</code>.",
            parse_mode=ParseMode.HTML,
        )
        return SGPA_OOB_CREDITS

    credit_hours = []
    bad = []
    for p in parts:
        try:
            ch = int(p)
            if ch <= 0:
                raise ValueError
            credit_hours.append(ch)
        except ValueError:
            bad.append(p)

    if bad:
        await update.message.reply_text(
            f"❌ Couldn't read these as credit hours: {', '.join(bad)}. "
            "Please enter whole positive numbers only, e.g. <code>3, 3, 2, 6</code>.",
            parse_mode=ParseMode.HTML,
        )
        return SGPA_OOB_CREDITS

    # Build synthetic course list: "Course 1", "Course 2", …
    working = [
        {"code": f"C{i}", "name": f"Course {i}", "credit_hours": ch}
        for i, ch in enumerate(credit_hours, 1)
    ]
    context.user_data['sgpa_working_courses'] = working
    return await _prompt_for_grades(update, context)


async def handle_sgpa_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in STREAM_MAP:
        await update.message.reply_text("❌ Please type only a number from 1 to 4.")
        return SGPA_STREAM
    context.user_data['sgpa_stream'] = STREAM_MAP[text]
    return await _sgpa_show_courses(update, context)


async def _sgpa_show_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = context.user_data['sgpa_year']
    sem = context.user_data['sgpa_sem']
    stream = context.user_data.get('sgpa_stream')

    session = Session()
    try:
        courses = gc.get_semester_courses(session, year, sem, stream)
    finally:
        session.close()

    if not courses:
        await update.message.reply_text(
            f"ℹ️ No courses are registered for {gc.format_semester_token(year, sem)}. "
            "Please double-check the year/semester with /gpa again."
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data['sgpa_working_courses'] = courses

    lines = [f"📚 <b>Normal courses for {gc.format_semester_token(year, sem)}:</b>"]
    for c in courses:
        lines.append(f"• {c['code']}: {c['name']} ({c['credit_hours']} cr)")
    lines.append(
        "\nDid you DROP or not take any of these? List them comma-separated "
        "(course code or name), or reply 'None'."
    )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    return SGPA_DROPPED


async def handle_sgpa_dropped(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texts = gc.parse_course_list(update.message.text)
    working = context.user_data['sgpa_working_courses']

    session = Session()
    try:
        not_found = []
        not_in_semester = []
        for t in texts:
            course = resolve_course_entity(session, t)
            if course is None:
                not_found.append(t)
                continue
            match = next((c for c in working if c['code'] == course.course_code), None)
            if match is None:
                not_in_semester.append(t)
            else:
                working.remove(match)
    finally:
        session.close()

    context.user_data['sgpa_working_courses'] = working

    warnings = []
    if not_found:
        warnings.append(f"⚠️ Not recognized, skipped: {', '.join(not_found)}")
    if not_in_semester:
        warnings.append(f"⚠️ Not part of this semester's list, skipped: {', '.join(not_in_semester)}")
    if warnings:
        await update.message.reply_text("\n".join(warnings))

    await update.message.reply_text(
        "Did you take any course(s) AHEAD of schedule that should also count "
        "toward THIS semester? List them comma-separated, or reply 'None'."
    )
    return SGPA_ADDED


async def handle_sgpa_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texts = gc.parse_course_list(update.message.text)
    working = context.user_data['sgpa_working_courses']

    session = Session()
    try:
        not_found = []
        for t in texts:
            course = resolve_course_entity(session, t)
            if course is None:
                not_found.append(t)
                continue
            if any(c['code'] == course.course_code for c in working):
                continue  # already in the list, don't duplicate
            working.append({"code": course.course_code, "name": course.name, "credit_hours": course.credit_hours})
    finally:
        session.close()

    if not_found:
        await update.message.reply_text(f"⚠️ Not recognized, skipped: {', '.join(not_found)}")

    if not working:
        await update.message.reply_text("ℹ️ No courses remain for this semester -- nothing to calculate.")
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data['sgpa_working_courses'] = working
    return await _prompt_for_grades(update, context)


async def _prompt_for_grades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    working = context.user_data['sgpa_working_courses']
    lines = [f"✅ <b>Course list for this semester ({len(working)} course(s)):</b>"]
    for i, c in enumerate(working, 1):
        lines.append(f"{i}. {c['name']} ({c['credit_hours']} cr)")
    lines.append(
        f"\nEnter your grade for EACH course above, in that exact order, "
        f"comma-separated.\nValid grades: {', '.join(gc.VALID_GRADES)}\n"
        f"Example: A+, B, A-"
    )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    return SGPA_GRADES


async def handle_sgpa_grades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    working = context.user_data['sgpa_working_courses']
    raw_grades = gc.parse_grade_list(update.message.text)

    if len(raw_grades) != len(working):
        await update.message.reply_text(
            f"❌ I need exactly {len(working)} grade(s), one per course listed above, "
            f"in order -- got {len(raw_grades)}. Please try again."
        )
        return SGPA_GRADES

    normalized = [gc.normalize_grade(g) for g in raw_grades]
    if None in normalized:
        bad = [raw_grades[i] for i, g in enumerate(normalized) if g is None]
        await update.message.reply_text(
            f"❌ Unrecognized grade(s): {', '.join(bad)}.\n"
            f"Valid grades: {', '.join(gc.VALID_GRADES)}. Please re-enter all grades."
        )
        return SGPA_GRADES

    pairs = [(c['credit_hours'], g) for c, g in zip(working, normalized)]
    sgpa = gc.compute_sgpa(pairs)

    lines = ["🎯 <b>SGPA Result</b>\n"]
    for c, g in zip(working, normalized):
        lines.append(f"• {c['name']}: {g} ({gc.GRADE_POINTS[g]:.2f}) × {c['credit_hours']} cr")
    lines.append(f"\n<b>SGPA: {sgpa:.2f}</b>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    await update.message.reply_text(
        "What next?\n"
        "[1] Try different grades for the same courses\n"
        "[2] Calculate a different semester\n"
        "[3] Finish"
    )
    return SGPA_CONTINUE


async def handle_sgpa_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == '1':
        return await _prompt_for_grades(update, context)
    elif text == '2':
        for key in ('sgpa_year', 'sgpa_sem', 'sgpa_stream', 'sgpa_working_courses'):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            "Which academic year and semester? Reply with both together, e.g. '4, 2'."
        )
        return SGPA_SEMESTER
    elif text == '3':
        context.user_data.clear()
        await update.message.reply_text("👍 Done! Send /gpa anytime to calculate again.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Please reply with 1, 2, or 3.")
        return SGPA_CONTINUE


# ==========================================
# PART 2/3: goal-based CGPA/SGPA planning (shared pipeline)
# ==========================================

async def handle_cg_semester(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        year, sem = gc.parse_year_sem_input(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return CG_SEMESTER
    if not gc.validate_year_sem(year, sem):
        await update.message.reply_text(_year_sem_error_message(year, sem))
        return CG_SEMESTER

    context.user_data['cg_curr_year'] = year
    context.user_data['cg_curr_sem'] = sem

    # Always ask fresh -- see the matching comment in handle_sgpa_semester.
    if gc._is_streaming_period(year, sem):
        context.user_data['cg_stream_return'] = 'past'
        await update.message.reply_text(
            "Your current position is in the stream-specific period. Which stream are you in?\n"
            "[1] Computer\n[2] Communication\n[3] Control\n[4] Power"
        )
        return CG_STREAM

    context.user_data['cg_stream'] = None
    return await _ask_past_adjustments(update, context)


async def handle_cg_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in STREAM_MAP:
        await update.message.reply_text("❌ Please type only a number from 1 to 4.")
        return CG_STREAM
    context.user_data['cg_stream'] = STREAM_MAP[text]

    return_to = context.user_data.pop('cg_stream_return', 'past')
    if return_to == 'future':
        return await _check_oob_and_proceed(update, context)
    return await _ask_past_adjustments(update, context)


async def _ask_past_adjustments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = context.user_data['cg_curr_year']
    sem = context.user_data['cg_curr_sem']
    if gc.semesters_before(year, sem) == []:
        context.user_data['cg_past_subtract'] = 0
        context.user_data['cg_past_add'] = 0
        return await _finish_past_adjustments(update, context)

    await update.message.reply_text(
        "Did you drop or intentionally not take any course(s) normally scheduled "
        "in your PREVIOUS semesters (before your current one)?\n"
        "List them comma-separated (course code or name), or reply 'None'."
    )
    return CG_PAST_DROPPED


async def handle_cg_past_dropped(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texts = gc.parse_course_list(update.message.text)
    session = Session()
    try:
        credits, unresolved = gc.resolve_credit_adjustment_courses(session, texts)
    finally:
        session.close()

    if unresolved:
        await update.message.reply_text(f"⚠️ Not recognized, skipped: {', '.join(unresolved)}")

    context.user_data['cg_past_subtract'] = sum(credits)
    await update.message.reply_text(
        "Did you take any course(s) AHEAD of schedule in a PREVIOUS semester "
        "(from a later semester, already passed)?\nList them comma-separated, or reply 'None'."
    )
    return CG_PAST_ADDED


async def handle_cg_past_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texts = gc.parse_course_list(update.message.text)
    session = Session()
    try:
        credits, unresolved = gc.resolve_credit_adjustment_courses(session, texts)
    finally:
        session.close()

    if unresolved:
        await update.message.reply_text(f"⚠️ Not recognized, skipped: {', '.join(unresolved)}")

    context.user_data['cg_past_add'] = sum(credits)
    return await _finish_past_adjustments(update, context)


async def _finish_past_adjustments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = context.user_data['cg_curr_year']
    sem = context.user_data['cg_curr_sem']
    stream = context.user_data.get('cg_stream')

    session = Session()
    try:
        prev_semesters = gc.semesters_before(year, sem)
        normal_prev_total = sum(gc.semester_total_credits(session, y, s, stream) for (y, s) in prev_semesters)
    finally:
        session.close()

    adjusted = normal_prev_total - context.user_data.get('cg_past_subtract', 0) + context.user_data.get('cg_past_add', 0)
    if adjusted < 0:
        adjusted = 0
    context.user_data['cg_prev_total_credits'] = adjusted

    await update.message.reply_text(
        f"📐 Your total previous credit hours (adjusted): <b>{adjusted}</b>\n\n"
        f"What is your CURRENT CGPA? (e.g. 3.10)",
        parse_mode=ParseMode.HTML,
    )
    return CG_CURRENT_CGPA


async def handle_cg_current_cgpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cgpa = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Please enter a number, e.g. 3.10.")
        return CG_CURRENT_CGPA
    if not (0.0 <= cgpa <= 4.0):
        await update.message.reply_text("❌ CGPA must be between 0.00 and 4.00.")
        return CG_CURRENT_CGPA

    context.user_data['cg_current_cgpa'] = round(cgpa, 2)
    await update.message.reply_text(
        "Which academic year and semester do you want to reach your goal by (or project to)? "
        "Reply with both together, e.g. '4, 1'."
    )
    return CG_END_SEMESTER


async def handle_cg_end_semester(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        end_year, end_sem = gc.parse_year_sem_input(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return CG_END_SEMESTER
    if not gc.validate_year_sem(end_year, end_sem):
        await update.message.reply_text(_year_sem_error_message(end_year, end_sem))
        return CG_END_SEMESTER

    curr_year = context.user_data['cg_curr_year']
    curr_sem = context.user_data['cg_curr_sem']

    try:
        window = gc.semesters_in_window(curr_year, curr_sem, end_year, end_sem)
    except ValueError:
        await update.message.reply_text(
            "❌ That target can't be before your current semester. "
            "Which year and semester do you want to reach your goal by? e.g. '4, 1'."
        )
        return CG_END_SEMESTER

    context.user_data['cg_end_year'] = end_year
    context.user_data['cg_end_sem'] = end_sem
    context.user_data['cg_window'] = window

    if not context.user_data.get('cg_stream') and gc._is_streaming_period(end_year, end_sem):
        context.user_data['cg_stream_return'] = 'future'
        await update.message.reply_text(
            "Your target is in the stream-specific period. Which stream are you in?\n"
            "[1] Computer\n[2] Communication\n[3] Control\n[4] Power"
        )
        return CG_STREAM

    return await _check_oob_and_proceed(update, context)


async def _check_oob_and_proceed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """After the planning window is set: if it contains out-of-batch semesters
    (year > 5), collect their total credit hours before continuing. The DB has
    no courses for those semesters, so the student must supply the numbers."""
    window = context.user_data['cg_window']
    oob = [ys for ys in window if gc.is_out_of_batch(*ys)]
    if oob:
        tokens_str = ', '.join(gc.format_semester_token(y, s) for (y, s) in oob)
        example = ', '.join(f"{gc.format_semester_token(y, s)}=15" for (y, s) in oob)
        await update.message.reply_text(
            f"📋 Your window includes out-of-batch semester(s): <b>{tokens_str}</b>\n"
            f"These are beyond the standard 5-year curriculum, so their courses "
            f"aren't in our system.\n\n"
            f"How many total credit hours will you take in each one?\n"
            f"Example: <code>{example}</code>",
            parse_mode=ParseMode.HTML,
        )
        return CG_OOB_CREDITS
    return await _ask_future_normal_or_override(update, context)


async def handle_cg_oob_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect total credit hours for each out-of-batch semester in the planning
    window. Uses the same XY1S=N syntax as the existing override parser."""
    window = context.user_data['cg_window']
    oob_list = [ys for ys in window if gc.is_out_of_batch(*ys)]

    try:
        raw = gc.parse_credit_override_string(update.message.text)
    except ValueError as e:
        example = ', '.join(f"{gc.format_semester_token(y, s)}=15" for (y, s) in oob_list)
        await update.message.reply_text(
            f"❌ {e}\n"
            f"Please enter credit hours for: "
            f"{', '.join(gc.format_semester_token(y, s) for (y, s) in oob_list)}\n"
            f"Example: <code>{example}</code>",
            parse_mode=ParseMode.HTML,
        )
        return CG_OOB_CREDITS

    missing = [gc.format_semester_token(y, s) for (y, s) in oob_list if (y, s) not in raw]
    if missing:
        example = ', '.join(f"{gc.format_semester_token(y, s)}=15" for (y, s) in oob_list)
        await update.message.reply_text(
            f"❌ Please provide credit hours for all out-of-batch semester(s): "
            f"{', '.join(missing)}\n"
            f"Example: <code>{example}</code>",
            parse_mode=ParseMode.HTML,
        )
        return CG_OOB_CREDITS

    bad = [gc.format_semester_token(y, s) for (y, s) in oob_list if raw.get((y, s), 0) <= 0]
    if bad:
        await update.message.reply_text(
            f"❌ Credit hours must be greater than zero for: {', '.join(bad)}. Please re-enter."
        )
        return CG_OOB_CREDITS

    context.user_data['cg_oob_credits'] = {(y, s): raw[(y, s)] for (y, s) in oob_list}
    return await _ask_future_normal_or_override(update, context)


async def _ask_future_normal_or_override(update: Update, context: ContextTypes.DEFAULT_TYPE):
    window = context.user_data['cg_window']
    stream = context.user_data.get('cg_stream')
    oob_credits = context.user_data.get('cg_oob_credits', {})

    # Build credit-hour map: DB lookup for in-batch, already-collected for OOB.
    session = Session()
    try:
        normal_credits = {}
        for (y, s) in window:
            if gc.is_out_of_batch(y, s):
                normal_credits[(y, s)] = oob_credits[(y, s)]
            else:
                normal_credits[(y, s)] = gc.semester_total_credits(session, y, s, stream)
    finally:
        session.close()

    context.user_data['cg_normal_future_credits'] = normal_credits

    in_batch = [(y, s) for (y, s) in window if not gc.is_out_of_batch(y, s)]

    single = len(window) == 1
    heading = "Your future semester" if single else "Your future semesters"
    lines = [f"📐 <b>{heading}:</b>"]
    for (y, s) in window:
        cr = normal_credits[(y, s)]
        if gc.is_out_of_batch(y, s):
            lines.append(f"• {gc.format_semester_token(y, s)}: {cr} cr <i>(as entered)</i>")
        else:
            lines.append(f"• {gc.format_semester_token(y, s)}: {cr} cr")

    if not in_batch:
        # Every semester is out-of-batch: credits are already fixed.
        # Skip the override prompt and go straight to mode selection.
        context.user_data['cg_future_credits'] = [normal_credits[(y, s)] for (y, s) in window]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        await update.message.reply_text(
            "What would you like to do next?\n"
            "[1] Find the required SGPA to reach a goal CGPA\n"
            "[2] Enter hypothetical SGPAs and see the projected CGPA"
        )
        return CG_MODE

    # At least some in-batch semesters remain -- ask about drops/adds for those.
    example_token = gc.format_semester_token(*in_batch[0])
    has_oob = bool(oob_credits)
    if single and not has_oob:
        lines.append(
            "\nIs this semester normal (you'll follow the standard schedule), "
            "or will you add/drop a course?\n"
            f"Reply 'normal', or give an override like '<code>{example_token}=12</code>'."
        )
    elif has_oob:
        lines.append(
            "\nAre the in-batch semester(s) normal (standard schedule), "
            "or will you add/drop courses in any of them?\n"
            "Out-of-batch credits are already set. Reply 'normal', or give overrides like "
            f"'<code>{example_token}=12</code>' for only the in-batch semester(s) that differ."
        )
    else:
        lines.append(
            "\nAre ALL of these normal (you'll follow the standard schedule), "
            "or will you add/drop courses in any of them?\n"
            "Reply 'normal', or give overrides like '<code>3Y2S=12, 4Y1S=19</code>' "
            "for only the semester(s) that differ."
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    return CG_FUTURE_OVERRIDES


async def handle_cg_future_overrides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    window = context.user_data['cg_window']
    normal_credits = context.user_data['cg_normal_future_credits']
    oob_credits = context.user_data.get('cg_oob_credits', {})

    if text.lower() in ('normal', 'none', 'no'):
        overrides = {}
    else:
        try:
            overrides = gc.parse_credit_override_string(text)
        except ValueError as e:
            await update.message.reply_text(f"❌ {e}\nPlease re-enter, e.g. '3Y2S=12, 4Y1S=19'.")
            return CG_FUTURE_OVERRIDES

    # Warn about overrides targeting OOB semesters (those are fixed already).
    oob_overridden = [
        gc.format_semester_token(y, s)
        for (y, s) in overrides
        if gc.is_out_of_batch(y, s) and (y, s) in {ys for ys in window}
    ]
    if oob_overridden:
        await update.message.reply_text(
            f"⚠️ Overrides for out-of-batch semester(s) were ignored "
            f"(those credits were already entered): {', '.join(oob_overridden)}"
        )

    # Warn about overrides to semesters entirely outside the window.
    in_batch_set = {ys for ys in window if not gc.is_out_of_batch(*ys)}
    ignored = [gc.format_semester_token(y, s) for (y, s) in overrides if (y, s) not in in_batch_set]
    if ignored:
        await update.message.reply_text(
            f"⚠️ These aren't in your in-batch future window and were ignored: {', '.join(ignored)}"
        )

    final_credits = []
    for (y, s) in window:
        if gc.is_out_of_batch(y, s):
            final_credits.append(oob_credits.get((y, s), normal_credits.get((y, s), 0)))
        else:
            final_credits.append(overrides.get((y, s), normal_credits[(y, s)]))

    if sum(final_credits) <= 0:
        await update.message.reply_text("❌ Your future semester(s) must have at least some credit hours total.")
        return CG_FUTURE_OVERRIDES

    context.user_data['cg_future_credits'] = final_credits

    await update.message.reply_text(
        "What would you like to do next?\n"
        "[1] Find the required SGPA to reach a goal CGPA\n"
        "[2] Enter hypothetical SGPAs and see the projected CGPA"
    )
    return CG_MODE


async def handle_cg_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == '1':
        await _prompt_goal_cgpa(update, context)
        return CG_GOAL_CGPA
    elif text == '2':
        await _prompt_hypothetical_sgpas(update, context)
        return CG_HYPOTHETICAL_SGPAS
    else:
        await update.message.reply_text("❌ Please reply with 1 or 2.")
        return CG_MODE


async def _prompt_goal_cgpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    end_token = gc.format_semester_token(context.user_data['cg_end_year'], context.user_data['cg_end_sem'])
    await update.message.reply_text(f"What CGPA do you want to reach by {end_token}? (e.g. 3.30)")


async def _prompt_hypothetical_sgpas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    window = context.user_data['cg_window']
    if len(window) == 1:
        token = gc.format_semester_token(*window[0])
        await update.message.reply_text(f"Enter your hypothetical SGPA for {token}.\nExample: 3.33")
    else:
        tokens = gc.format_semester_list(window)
        await update.message.reply_text(
            f"Enter your hypothetical SGPA for each future semester, comma-separated, "
            f"in this order:\n{tokens}\nExample: 3.33, 3.21, 3.50"
        )


async def handle_cg_goal_cgpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        goal = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Please enter a number, e.g. 3.30.")
        return CG_GOAL_CGPA
    if not (0.0 <= goal <= 4.0):
        await update.message.reply_text("❌ Goal CGPA must be between 0.00 and 4.00.")
        return CG_GOAL_CGPA

    prev_total = context.user_data['cg_prev_total_credits']
    current_cgpa = context.user_data['cg_current_cgpa']
    future_credits = context.user_data['cg_future_credits']
    window = context.user_data['cg_window']
    single = len(window) == 1

    result = gc.compute_required_sgpa(prev_total, current_cgpa, future_credits, goal)

    if not result["feasible"]:
        req = result["required_sgpa"]
        end_token = gc.format_semester_token(*window[-1])
        if single:
            reason = (
                f"Reaching a {goal:.2f} CGPA by {end_token} would require a {req:.2f} SGPA "
                f"in that semester, which is above the maximum possible (4.00)."
            )
        else:
            reason = (
                f"Reaching a {goal:.2f} CGPA by {end_token} would require a {req:.2f} average "
                f"SGPA across your remaining semesters, which is above the maximum possible (4.00)."
            )
        await update.message.reply_text(
            f"❌ <b>Not achievable</b>\n\n{reason} This goal isn't mathematically achievable in that timeframe.",
            parse_mode=ParseMode.HTML,
        )
    elif result["already_secured"]:
        await update.message.reply_text(
            f"✅ <b>Goal already secured</b>\n\n"
            f"Even with the lowest possible performance from here, you'll reach at least "
            f"a {goal:.2f} CGPA by {gc.format_semester_token(*window[-1])}.",
            parse_mode=ParseMode.HTML,
        )
    else:
        req = result["required_sgpa"]
        end_token = gc.format_semester_token(*window[-1])
        if single:
            lines = [
                f"🎯 <b>Required SGPA: {req:.2f}</b>\n",
                f"To reach a {goal:.2f} CGPA by {end_token}, "
                f"you need an SGPA of <b>{req:.2f}</b> in that semester.",
            ]
        else:
            lines = [
                f"🎯 <b>Required SGPA: {req:.2f}</b>\n",
                f"To reach a {goal:.2f} CGPA by {end_token}, "
                f"you need an average SGPA of <b>{req:.2f}</b> in EACH of your remaining semesters:",
            ]
            for (y, s), c in zip(window, future_credits):
                lines.append(f"• {gc.format_semester_token(y, s)} ({c} cr): SGPA ≥ {req:.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    await update.message.reply_text(
        "What next?\n"
        "[1] Try a different goal CGPA (same timeframe)\n"
        "[2] See a hypothetical-SGPA projection for the same timeframe instead\n"
        "[3] Choose a different goal semester\n"
        "[4] Finish"
    )
    return CG_GOAL_CONTINUE


async def handle_cg_goal_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == '1':
        await _prompt_goal_cgpa(update, context)
        return CG_GOAL_CGPA
    elif text == '2':
        await _prompt_hypothetical_sgpas(update, context)
        return CG_HYPOTHETICAL_SGPAS
    elif text == '3':
        for key in ('cg_end_year', 'cg_end_sem', 'cg_window', 'cg_normal_future_credits', 'cg_future_credits', 'cg_oob_credits'):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            "Which academic year and semester do you want to reach your goal by (or project to)? "
            "Reply with both together, e.g. '4, 1'."
        )
        return CG_END_SEMESTER
    elif text == '4':
        context.user_data.clear()
        await update.message.reply_text("👍 Done! Send /gpa anytime to calculate again.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Please reply with 1, 2, 3, or 4.")
        return CG_GOAL_CONTINUE


async def handle_cg_hypothetical_sgpas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    window = context.user_data['cg_window']
    future_credits = context.user_data['cg_future_credits']

    try:
        values = gc.parse_sgpa_list(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nPlease re-enter (comma-separated if more than one).")
        return CG_HYPOTHETICAL_SGPAS

    if len(values) != len(window):
        tokens = gc.format_semester_list(window)
        await update.message.reply_text(
            f"❌ I need exactly {len(window)} SGPA value(s), one per semester, in this order:\n{tokens}\n"
            f"Got {len(values)}. Please re-enter."
        )
        return CG_HYPOTHETICAL_SGPAS

    try:
        projection = gc.compute_cgpa_projection(
            context.user_data['cg_prev_total_credits'],
            context.user_data['cg_current_cgpa'],
            future_credits, values,
        )
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nPlease re-enter.")
        return CG_HYPOTHETICAL_SGPAS

    lines = ["📈 <b>Projected CGPA</b>\n"]
    for (y, s), entry in zip(window, projection):
        lines.append(
            f"• {gc.format_semester_token(y, s)}: SGPA {entry['sgpa']:.2f} "
            f"({entry['credits']} cr) → running CGPA <b>{entry['running_cgpa']:.2f}</b>"
        )
    lines.append(f"\n<b>Final projected CGPA: {projection[-1]['running_cgpa']:.2f}</b>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    await update.message.reply_text(
        "What next?\n"
        "[1] Try another set of hypothetical SGPAs (same timeframe)\n"
        "[2] Find the required SGPA for a goal CGPA instead (same timeframe)\n"
        "[3] Choose a different goal semester\n"
        "[4] Finish"
    )
    return CG_PROJECTION_CONTINUE


async def handle_cg_projection_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == '1':
        await _prompt_hypothetical_sgpas(update, context)
        return CG_HYPOTHETICAL_SGPAS
    elif text == '2':
        await _prompt_goal_cgpa(update, context)
        return CG_GOAL_CGPA
    elif text == '3':
        for key in ('cg_end_year', 'cg_end_sem', 'cg_window', 'cg_normal_future_credits', 'cg_future_credits', 'cg_oob_credits'):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            "Which academic year and semester do you want to reach your goal by (or project to)? "
            "Reply with both together, e.g. '4, 1'."
        )
        return CG_END_SEMESTER
    elif text == '4':
        context.user_data.clear()
        await update.message.reply_text("👍 Done! Send /gpa anytime to calculate again.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Please reply with 1, 2, 3, or 4.")
        return CG_PROJECTION_CONTINUE


# ==========================================
# Handler assembly
# ==========================================

gpa_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("gpa", start_gpa)],
    states={
        GPA_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gpa_menu)],

        SGPA_SEMESTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_semester)],
        SGPA_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_stream)],
        SGPA_OOB_CREDITS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_oob_credits)],
        SGPA_DROPPED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_dropped)],
        SGPA_ADDED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_added)],
        SGPA_GRADES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_grades)],
        SGPA_CONTINUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_continue)],

        CG_SEMESTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_semester)],
        CG_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_stream)],
        CG_PAST_DROPPED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_past_dropped)],
        CG_PAST_ADDED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_past_added)],
        CG_CURRENT_CGPA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_current_cgpa)],
        CG_END_SEMESTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_end_semester)],
        CG_OOB_CREDITS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_oob_credits)],
        CG_FUTURE_OVERRIDES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_future_overrides)],
        CG_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_mode)],
        CG_GOAL_CGPA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_goal_cgpa)],
        CG_GOAL_CONTINUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_goal_continue)],
        CG_HYPOTHETICAL_SGPAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_hypothetical_sgpas)],
        CG_PROJECTION_CONTINUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_projection_continue)],
    },
    fallbacks=[CommandHandler("cancel", cancel_gpa)],
)
