"""
gpa_bot.py
===========
Telegram front-end for the grade-calculation feature (see
grade_calculator.py for all the actual math). Self-contained
ConversationHandler, entry point /gpa -- add it to bot.py's main() with:

    from gpa_bot import gpa_conv_handler
    app.add_handler(gpa_conv_handler)

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

STREAM HANDLING: stream only matters for a semester at/after Year 4
Semester 2 (see grade_calculator._is_streaming_period). If the student's
profile already has a stream on file (student_service.get_derived_profile),
that's used silently. Otherwise the bot asks for it, but only at the
point it first becomes necessary -- not upfront for every student,
since most GPA questions never touch the streaming period at all.
"""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler, filters, ContextTypes
)
from sqlalchemy.orm import sessionmaker

from models import engine
from student_service import get_student_by_telegram_id, get_derived_profile
from query_api import resolve_course_entity
import grade_calculator as gc

Session = sessionmaker(bind=engine)

STREAM_NAMES = ["Computer", "Communication", "Control", "Power"]
STREAM_MAP = {'1': 'Computer', '2': 'Communication', '3': 'Control', '4': 'Power'}

(
    GPA_MENU,
    SGPA_YEAR, SGPA_SEM, SGPA_STREAM, SGPA_DROPPED, SGPA_ADDED, SGPA_GRADES,
    CG_CURR_YEAR, CG_CURR_SEM, CG_STREAM,
    CG_PAST_DROPPED, CG_PAST_ADDED,
    CG_CURRENT_CGPA,
    CG_END_YEAR, CG_END_SEM,
    CG_FUTURE_OVERRIDES,
    CG_MODE,
    CG_GOAL_CGPA,
    CG_HYPOTHETICAL_SGPAS,
) = range(19)


# ==========================================
# Shared small helpers
# ==========================================

async def _require_registration(update: Update):
    student = get_student_by_telegram_id(str(update.effective_user.id))
    if not student:
        await update.message.reply_text("⚠️ Please register with your Student ID first (send /start).")
        return None
    return student


def _known_stream_from_profile(update: Update):
    student = get_student_by_telegram_id(str(update.effective_user.id))
    if student:
        return get_derived_profile(student).get("stream")
    return None


def _valid_year(text):
    return text.strip() in ('1', '2', '3', '4', '5')


def _valid_sem(text):
    return text.strip() in ('1', '2')


def _semester_label(year, sem):
    return f"Year {year}, Semester {sem} ({gc.format_semester_token(year, sem)})"


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
        await update.message.reply_text("Which academic year is this semester in? Type a number [1-5].")
        return SGPA_YEAR
    elif text == '2':
        await update.message.reply_text("What is your CURRENT academic year? Type a number [1-5].")
        return CG_CURR_YEAR
    else:
        await update.message.reply_text("❌ Please reply with 1 or 2.")
        return GPA_MENU


# ==========================================
# PART 1: SGPA for one actual semester
# ==========================================

async def handle_sgpa_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not _valid_year(text):
        await update.message.reply_text("❌ Please type only a single number from 1 to 5.")
        return SGPA_YEAR
    context.user_data['sgpa_year'] = int(text)
    await update.message.reply_text("And which semester? Type 1 or 2.")
    return SGPA_SEM


async def handle_sgpa_sem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not _valid_sem(text):
        await update.message.reply_text("❌ Please type only 1 or 2.")
        return SGPA_SEM
    sem = int(text)
    year = context.user_data['sgpa_year']
    context.user_data['sgpa_sem'] = sem

    known_stream = _known_stream_from_profile(update)
    if known_stream:
        context.user_data['sgpa_stream'] = known_stream
        return await _sgpa_show_courses(update, context)

    if gc._is_streaming_period(year, sem):
        await update.message.reply_text(
            "That semester is in the stream-specific period. Which stream are you in?\n"
            "[1] Computer\n[2] Communication\n[3] Control\n[4] Power"
        )
        return SGPA_STREAM

    context.user_data['sgpa_stream'] = None
    return await _sgpa_show_courses(update, context)


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
            f"ℹ️ No courses are registered for {_semester_label(year, sem)}. "
            "Please double-check the year/semester with /gpa again."
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data['sgpa_working_courses'] = courses

    lines = [f"📚 <b>Normal courses for {_semester_label(year, sem)}:</b>"]
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

    lines = [f"✅ <b>Final course list for this semester ({len(working)} course(s)):</b>"]
    for i, c in enumerate(working, 1):
        lines.append(f"{i}. {c['name']} ({c['credit_hours']} cr)")
    lines.append(
        f"\nNow enter your grade for EACH course above, in that exact order, "
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

    context.user_data.clear()
    return ConversationHandler.END


# ==========================================
# PART 2/3: goal-based CGPA/SGPA planning (shared pipeline)
# ==========================================

async def handle_cg_curr_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not _valid_year(text):
        await update.message.reply_text("❌ Please type only a single number from 1 to 5.")
        return CG_CURR_YEAR
    context.user_data['cg_curr_year'] = int(text)
    await update.message.reply_text("And your current semester? Type 1 or 2.")
    return CG_CURR_SEM


async def handle_cg_curr_sem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not _valid_sem(text):
        await update.message.reply_text("❌ Please type only 1 or 2.")
        return CG_CURR_SEM
    sem = int(text)
    year = context.user_data['cg_curr_year']
    context.user_data['cg_curr_sem'] = sem

    known_stream = _known_stream_from_profile(update)
    if known_stream:
        context.user_data['cg_stream'] = known_stream
        return await _ask_past_adjustments(update, context)

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
        return await _ask_future_normal_or_override(update, context)
    return await _ask_past_adjustments(update, context)


async def _ask_past_adjustments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year = context.user_data['cg_curr_year']
    sem = context.user_data['cg_curr_sem']
    if gc.semesters_before(year, sem) == []:
        # Nothing before Year 1 Sem 1 -- skip straight past both questions.
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
        "Which academic year do you want to reach your goal by (or project to)? Type [1-5]."
    )
    return CG_END_YEAR


async def handle_cg_end_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not _valid_year(text):
        await update.message.reply_text("❌ Please type only a single number from 1 to 5.")
        return CG_END_YEAR
    context.user_data['cg_end_year'] = int(text)
    await update.message.reply_text("And which semester in that year? Type 1 or 2.")
    return CG_END_SEM


async def handle_cg_end_sem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not _valid_sem(text):
        await update.message.reply_text("❌ Please type only 1 or 2.")
        return CG_END_SEM
    end_sem = int(text)
    end_year = context.user_data['cg_end_year']
    curr_year = context.user_data['cg_curr_year']
    curr_sem = context.user_data['cg_curr_sem']

    try:
        window = gc.semesters_in_window(curr_year, curr_sem, end_year, end_sem)
    except ValueError:
        await update.message.reply_text(
            "❌ That target can't be before your current semester. "
            "Which academic year do you want to reach your goal by? Type [1-5]."
        )
        return CG_END_YEAR

    context.user_data['cg_end_sem'] = end_sem
    context.user_data['cg_window'] = window

    if not context.user_data.get('cg_stream') and gc._is_streaming_period(end_year, end_sem):
        context.user_data['cg_stream_return'] = 'future'
        await update.message.reply_text(
            "Your target is in the stream-specific period. Which stream are you in?\n"
            "[1] Computer\n[2] Communication\n[3] Control\n[4] Power"
        )
        return CG_STREAM

    return await _ask_future_normal_or_override(update, context)


async def _ask_future_normal_or_override(update: Update, context: ContextTypes.DEFAULT_TYPE):
    window = context.user_data['cg_window']
    stream = context.user_data.get('cg_stream')

    session = Session()
    try:
        normal_credits = {(y, s): gc.semester_total_credits(session, y, s, stream) for (y, s) in window}
    finally:
        session.close()

    context.user_data['cg_normal_future_credits'] = normal_credits

    lines = ["📐 <b>Your future semesters, using the standard curriculum:</b>"]
    for (y, s) in window:
        lines.append(f"• {gc.format_semester_token(y, s)}: {normal_credits[(y, s)]} cr")
    lines.append(
        "\nAre ALL of these normal (you'll follow the standard schedule), "
        "or will you add/drop courses in any of them?\n"
        "Reply 'normal', or give overrides like '3Y2S=12, 4Y1S=19' "
        "for only the semester(s) that differ."
    )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    return CG_FUTURE_OVERRIDES


async def handle_cg_future_overrides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    window = context.user_data['cg_window']
    normal_credits = context.user_data['cg_normal_future_credits']

    if text.lower() in ('normal', 'none', 'no'):
        overrides = {}
    else:
        try:
            overrides = gc.parse_credit_override_string(text)
        except ValueError as e:
            await update.message.reply_text(f"❌ {e}\nPlease re-enter, e.g. '3Y2S=12, 4Y1S=19'.")
            return CG_FUTURE_OVERRIDES

    ignored = [gc.format_semester_token(y, s) for (y, s) in overrides if (y, s) not in window]
    if ignored:
        await update.message.reply_text(
            f"⚠️ These aren't in your future window and were ignored: {', '.join(ignored)}"
        )

    final_credits = []
    for (y, s) in window:
        final_credits.append(overrides.get((y, s), normal_credits[(y, s)]))

    if sum(final_credits) <= 0:
        await update.message.reply_text("❌ Your future semesters must have at least some credit hours total.")
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
        await update.message.reply_text(
            f"What CGPA do you want to reach by {gc.format_semester_token(context.user_data['cg_end_year'], context.user_data['cg_end_sem'])}? "
            f"(e.g. 3.30)"
        )
        return CG_GOAL_CGPA
    elif text == '2':
        window = context.user_data['cg_window']
        tokens = ", ".join(gc.format_semester_token(y, s) for (y, s) in window)
        await update.message.reply_text(
            f"Enter your hypothetical SGPA for each future semester, comma-separated, "
            f"in this order:\n{tokens}\nExample: 3.33, 3.21, 3.50"
        )
        return CG_HYPOTHETICAL_SGPAS
    else:
        await update.message.reply_text("❌ Please reply with 1 or 2.")
        return CG_MODE


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

    result = gc.compute_required_sgpa(prev_total, current_cgpa, future_credits, goal)

    if not result["feasible"]:
        await update.message.reply_text(f"❌ <b>Not achievable</b>\n\n{result['reason']}", parse_mode=ParseMode.HTML)
    elif result["already_secured"]:
        await update.message.reply_text(
            f"✅ <b>Goal already secured</b>\n\n"
            f"Even with the lowest possible performance from here, you'll reach at least "
            f"a {goal:.2f} CGPA by {gc.format_semester_token(*window[-1])}.",
            parse_mode=ParseMode.HTML,
        )
    else:
        req = result["required_sgpa"]
        lines = [
            f"🎯 <b>Required SGPA: {req:.2f}</b>\n",
            f"To reach a {goal:.2f} CGPA by {gc.format_semester_token(*window[-1])}, "
            f"you need an average SGPA of <b>{req:.2f}</b> in EACH of your remaining semesters:",
        ]
        for (y, s), c in zip(window, future_credits):
            lines.append(f"• {gc.format_semester_token(y, s)} ({c} cr): SGPA ≥ {req:.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    context.user_data.clear()
    return ConversationHandler.END


async def handle_cg_hypothetical_sgpas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    window = context.user_data['cg_window']
    future_credits = context.user_data['cg_future_credits']

    try:
        values = gc.parse_sgpa_list(update.message.text)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}\nPlease re-enter, comma-separated (e.g. 3.33, 3.21, 3.50).")
        return CG_HYPOTHETICAL_SGPAS

    if len(values) != len(window):
        tokens = ", ".join(gc.format_semester_token(y, s) for (y, s) in window)
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

    context.user_data.clear()
    return ConversationHandler.END


# ==========================================
# Handler assembly
# ==========================================

gpa_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("gpa", start_gpa)],
    states={
        GPA_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_gpa_menu)],

        SGPA_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_year)],
        SGPA_SEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_sem)],
        SGPA_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_stream)],
        SGPA_DROPPED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_dropped)],
        SGPA_ADDED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_added)],
        SGPA_GRADES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sgpa_grades)],

        CG_CURR_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_curr_year)],
        CG_CURR_SEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_curr_sem)],
        CG_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_stream)],
        CG_PAST_DROPPED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_past_dropped)],
        CG_PAST_ADDED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_past_added)],
        CG_CURRENT_CGPA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_current_cgpa)],
        CG_END_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_end_year)],
        CG_END_SEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_end_sem)],
        CG_FUTURE_OVERRIDES: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_future_overrides)],
        CG_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_mode)],
        CG_GOAL_CGPA: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_goal_cgpa)],
        CG_HYPOTHETICAL_SGPAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cg_hypothetical_sgpas)],
    },
    fallbacks=[CommandHandler("cancel", cancel_gpa)],
)
