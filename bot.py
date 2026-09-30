import logging
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from sqlalchemy.orm import sessionmaker
from models import engine as _db_engine, UserFeedback
from query_api import (
    get_course_details_formatted,
    get_downstream_impact_formatted,
    get_semester_courses_formatted,
    get_dependant_courses_formatted,
    get_cross_department_formatted,
    get_cross_stream_formatted,
    resolve_or_disambiguate,
)
_BotSession = sessionmaker(bind=_db_engine)
from student_service import (
    get_student_by_telegram_id,
    register_or_link_student,
    looks_like_student_id
)
from orchestrator import process_reasoning_request, process_reasoning_request_full
from gpa_bot import gpa_conv_handler
from explain_schedule import build_explanation_message


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

ECE_DEPARTMENT_ID = 1

# Conversation States for /course_planning
# NEW: AWAITING_STREAM_POST_COMPARISON -- catches the student's stream
# pick AFTER seeing the undecided-student comparison. Previously this
# response fell through to the registration catch-all handler (the bot
# incorrectly asked for a student ID instead of understanding the reply
# as a stream choice), because the conversation ended right after
# showing the comparison with no state left to receive it.
AWAITING_YEAR, AWAITING_SEMESTER, AWAITING_STREAM, AWAITING_FAILED, \
    AWAITING_ADDED, AWAITING_DROPPED, AWAITING_STREAM_POST_COMPARISON, \
    AWAITING_ANOTHER_STREAM, AWAITING_WHY = range(9)

# Conversation states for conversational query commands
(
    COURSE_AWAITING_QUERY,
    DEPENDANT_AWAITING_QUERY,
    DOWNSTREAM_AWAITING_QUERY,
    SEMESTER_AWAITING_YEAR,
    SEMESTER_AWAITING_SEM,
    SEMESTER_AWAITING_STREAM,
    FEEDBACK_AWAITING_RATING,
    FEEDBACK_AWAITING_COMMENT,
) = range(9, 17)

STREAM_NAMES = ["Computer", "Communication", "Control", "Power"]

async def error_handler(update, context):
    logger.error(f"Update {update} caused error: {context.error}")
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Connection hiccup — please resend your last message."
            )
        except Exception:
            pass  # if even this fails, just let it drop

def _parse_course_list(text: str):
    "'None' (any case) -> empty list; otherwise split on commas."
    text = text.strip()
    if text.lower() in ("none", "no", "-"):
        return []
    return [c.strip() for c in text.split(',') if c.strip()]


def _match_stream_name(text: str):
    """Case-insensitive prefix match against the 4 known streams, so a
    plain free-text reply like 'control' or 'Control stream' works."""
    text_lower = text.strip().lower()
    for name in STREAM_NAMES:
        if text_lower.startswith(name.lower()):
            return name
    return None


def _resolve_course_list_with_feedback(texts):
    """Resolve a list of free-text course inputs using the fuzzy resolver.
    Returns (resolved, suggestions, not_found):
      resolved    -- [(code, name)]  high-confidence matches
      suggestions -- [(raw_text, code, name)]  ambiguous; top result shown
      not_found   -- [raw_text]  no match at all
    """
    resolved = []
    suggestions = []
    not_found = []
    session = _BotSession()
    try:
        for text in texts:
            text = text.strip()
            if not text:
                continue
            course, options = resolve_or_disambiguate(session, text)
            if course:
                resolved.append((course.course_code, course.name))
            elif options:
                top = options[0]
                suggestions.append((text, top.course_code, top.name))
            else:
                not_found.append(text)
    finally:
        session.close()
    return resolved, suggestions, not_found


def _build_feedback_message(field_label, resolved, suggestions, not_found):
    """Build an HTML feedback message for the course resolution results.
    Returns (message_text, all_codes, needs_confirmation)."""
    lines = [f"Here's what I found for <b>{field_label}</b>:"]
    all_codes = []
    for code, name in resolved:
        lines.append(f"✅ <b>{name}</b>")
        all_codes.append(code)
    for raw, code, name in suggestions:
        lines.append(f"❓ <i>'{raw}'</i> → closest match: <b>{name}</b>")
        all_codes.append(code)
    for raw in not_found:
        lines.append(f"❌ <i>'{raw}'</i> — no match found, will be skipped")
    needs_confirm = bool(suggestions or not_found)
    return "\n".join(lines), all_codes, needs_confirm


# ==========================================
# 1. Registration & Core Commands
# ==========================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = str(update.effective_user.id)
    student = get_student_by_telegram_id(telegram_user_id)

    if student:
        # Already registered — greet them and stop. Do NOT offer re-registration.
        await update.message.reply_text(
            f"👋 Welcome back, <b>{student.name}</b>!\n\n"
            "Use /explain_commands to see everything you can do.",
            parse_mode=ParseMode.HTML,
        )
        return

    # First-time user — ask for their student ID.
    await update.message.reply_text(
        "👋 Welcome to the <b>ECE Academic Advisor Bot</b>!\n\n"
        "Please send me your student ID (e.g. <code>ETS/1444/13</code>) to get started.",
        parse_mode=ParseMode.HTML,
    )

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = str(update.effective_user.id)

    # If the user is already registered, ignore free-text messages that
    # aren't inside a conversation — they aren't trying to re-register.
    if get_student_by_telegram_id(telegram_user_id):
        return

    user_text = update.message.text.strip()
    if looks_like_student_id(user_text):
        ok, msg = register_or_link_student(telegram_user_id, user_text)
        await update.message.reply_text(("✅ " if ok else "❌ ") + msg)
    else:
        await update.message.reply_text("Please send a valid student ID (e.g., ETS/1444/13).")


# ==========================================
# 2. Registration guard
# ==========================================

async def require_registration(update: Update) -> bool:
    """Helper to block unregistered users from using commands."""
    student = get_student_by_telegram_id(str(update.effective_user.id))
    if not student:
        await update.message.reply_text("⚠️ Please register with your Student ID first by sending /start.")
        return False
    return True


# ==========================================
# 3. Conversational Query Commands
#    /course, /dependant, /downstream_impact, /semester
# ==========================================

# --- /course ---

async def course_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "🔍 Which course would you like details for?\n"
        "Send the course code or name (e.g. <code>ECEg3105</code> or <i>Applied Electronics</i>).",
        parse_mode=ParseMode.HTML,
    )
    return COURSE_AWAITING_QUERY

async def course_receive_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    response = get_course_details_formatted(query)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def course_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

course_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("course", course_command)],
    states={
        COURSE_AWAITING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, course_receive_query)],
    },
    fallbacks=[CommandHandler("cancel", course_cancel)],
)


# --- /dependant ---

async def dependant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "🔗 Which course do you want to find dependants for?\n"
        "Send the course code or name (e.g. <code>ECEg3105</code> or <i>Applied Electronics</i>).",
        parse_mode=ParseMode.HTML,
    )
    return DEPENDANT_AWAITING_QUERY

async def dependant_receive_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    response = get_dependant_courses_formatted(query)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def dependant_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

dependant_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("dependant", dependant_command)],
    states={
        DEPENDANT_AWAITING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, dependant_receive_query)],
    },
    fallbacks=[CommandHandler("cancel", dependant_cancel)],
)


# --- /downstream_impact ---

async def downstream_impact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "⚠️ Which course do you want to check the downstream impact for?\n"
        "Send the course code or name (e.g. <code>Math1007</code> or <i>Engineering Mathematics</i>).",
        parse_mode=ParseMode.HTML,
    )
    return DOWNSTREAM_AWAITING_QUERY

async def downstream_receive_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    response = get_downstream_impact_formatted(query)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def downstream_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

downstream_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("downstream_impact", downstream_impact_command)],
    states={
        DOWNSTREAM_AWAITING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, downstream_receive_query)],
    },
    fallbacks=[CommandHandler("cancel", downstream_cancel)],
)


# --- /semester ---

async def semester_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "📅 Which year are you asking about? Send a number (1–5)."
    )
    return SEMESTER_AWAITING_YEAR

async def semester_receive_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in ("1", "2", "3", "4", "5"):
        await update.message.reply_text("❌ Please send a single number from 1 to 5.")
        return SEMESTER_AWAITING_YEAR
    context.user_data["_sem_year"] = text
    await update.message.reply_text(
        "Which semester? Send a number (1, 2, or 3)."
    )
    return SEMESTER_AWAITING_SEM

async def semester_receive_sem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in ("1", "2", "3"):
        await update.message.reply_text("❌ Please send 1, 2, or 3.")
        return SEMESTER_AWAITING_SEM
    context.user_data["_sem_sem"] = text
    year = int(context.user_data["_sem_year"])
    sem = int(text)
    # Stream is only relevant at or after 4Y2S
    if year > 4 or (year == 4 and sem >= 2):
        await update.message.reply_text(
            "Which stream? (Computer, Communication, Control, Power)\n"
            "Or send <b>All</b> to see every course for that term.",
            parse_mode=ParseMode.HTML,
        )
        return SEMESTER_AWAITING_STREAM
    # Pre-stream years — run immediately with no stream filter
    response = get_semester_courses_formatted(str(year), str(sem), None)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    context.user_data.pop("_sem_year", None)
    context.user_data.pop("_sem_sem", None)
    return ConversationHandler.END

async def semester_receive_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    stream = None if text.lower() in ("all", "none", "-") else text
    year = context.user_data.pop("_sem_year", "1")
    sem = context.user_data.pop("_sem_sem", "1")
    response = get_semester_courses_formatted(year, sem, stream)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def semester_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("_sem_year", None)
    context.user_data.pop("_sem_sem", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

semester_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("semester", semester_command)],
    states={
        SEMESTER_AWAITING_YEAR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, semester_receive_year)],
        SEMESTER_AWAITING_SEM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, semester_receive_sem)],
        SEMESTER_AWAITING_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, semester_receive_stream)],
    },
    fallbacks=[CommandHandler("cancel", semester_cancel)],
)


# ==========================================
# 4. Non-conversational Query Commands
# ==========================================

async def cross_department_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    response = get_cross_department_formatted()
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)

async def cross_stream_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    response = get_cross_stream_formatted()
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)


# ==========================================
# 5. Course Planning Intake (Deterministic State Machine)
# ==========================================

async def start_course_planning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return ConversationHandler.END

    await update.message.reply_text(
        "Let's build your recovery plan. \n\n"
        "What is your academic year currently? Type only the number [1, 2, 3, 4, 5]."
    )
    return AWAITING_YEAR

async def handle_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in ['1', '2', '3', '4', '5']:
        await update.message.reply_text("❌ Please type only a single number from 1 to 5.")
        return AWAITING_YEAR

    context.user_data['year'] = int(text)
    await update.message.reply_text(
        "What is your academic semester currently? Type only the number [1, 2, 3]."
    )
    return AWAITING_SEMESTER

async def handle_semester(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in ['1', '2', '3']:
        await update.message.reply_text("❌ Please type only a single number from 1 to 3.")
        return AWAITING_SEMESTER

    sem = int(text)
    context.user_data['semester'] = sem
    year = context.user_data['year']

    # Conditional Stream Check: If at or past Year 4 Semester 2, ask for stream.
    if year > 4 or (year == 4 and sem >= 2):
        await update.message.reply_text(
            "Which stream are you in? Type only the number:\n"
            "[1] Computer\n[2] Communication\n[3] Control\n[4] Power"
        )
        return AWAITING_STREAM
    else:
        context.user_data['stream'] = None
        await update.message.reply_text(
            "Are there any courses you have failed? \n"
            "If so, list them with comma separation (e.g., ECEg3105, Math1007).\n"
            "If none, reply with 'None'."
        )
        return AWAITING_FAILED

async def handle_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    stream_map = {'1': 'Computer', '2': 'Communication', '3': 'Control', '4': 'Power'}

    if text not in stream_map:
        await update.message.reply_text("❌ Please type only a number from 1 to 4.")
        return AWAITING_STREAM

    context.user_data['stream'] = stream_map[text]
    await update.message.reply_text(
        "Are there any courses you have failed? \n"
        "If so, list them with comma separation (e.g., ECEg3105, Math1007).\n"
        "If none, reply with 'None'."
    )
    return AWAITING_FAILED

async def handle_failed_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Confirmation mode: user replied to a fuzzy/unresolved feedback prompt.
    if context.user_data.pop('_failed_confirm', False):
        if text.lower() in ('ok', 'yes', 'y'):
            await update.message.reply_text(
                "Have you taken any courses AHEAD of your normal schedule "
                "(courses from a later semester that you already passed)?\n"
                "List them comma-separated, or reply 'None'."
            )
            return AWAITING_ADDED
        # Otherwise: user is sending a corrected list — fall through to re-process.

    course_texts = _parse_course_list(text)
    if not course_texts:
        context.user_data['failed_courses'] = []
        await update.message.reply_text(
            "Have you taken any courses AHEAD of your normal schedule "
            "(courses from a later semester that you already passed)?\n"
            "List them comma-separated, or reply 'None'."
        )
        return AWAITING_ADDED

    resolved, suggestions, not_found = _resolve_course_list_with_feedback(course_texts)
    msg, all_codes, needs_confirm = _build_feedback_message(
        "failed courses", resolved, suggestions, not_found
    )
    context.user_data['failed_courses'] = all_codes

    if needs_confirm:
        await update.message.reply_text(
            msg + "\n\nReply <b>ok</b> to confirm, or send a corrected list.",
            parse_mode=ParseMode.HTML,
        )
        context.user_data['_failed_confirm'] = True
        return AWAITING_FAILED

    await update.message.reply_text(
        "Have you taken any courses AHEAD of your normal schedule "
        "(courses from a later semester that you already passed)?\n"
        "List them comma-separated, or reply 'None'."
    )
    return AWAITING_ADDED


async def handle_added_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Confirmation mode.
    if context.user_data.pop('_added_confirm', False):
        if text.lower() in ('ok', 'yes', 'y'):
            await update.message.reply_text(
                "Have you DROPPED any courses from a semester you already took "
                "(so you still need to take them later)?\n"
                "List them comma-separated, or reply 'None'."
            )
            return AWAITING_DROPPED
        # Otherwise: corrected list — fall through.

    course_texts = _parse_course_list(text)
    if not course_texts:
        context.user_data['added_courses'] = []
        await update.message.reply_text(
            "Have you DROPPED any courses from a semester you already took "
            "(so you still need to take them later)?\n"
            "List them comma-separated, or reply 'None'."
        )
        return AWAITING_DROPPED

    resolved, suggestions, not_found = _resolve_course_list_with_feedback(course_texts)
    msg, all_codes, needs_confirm = _build_feedback_message(
        "added (ahead-of-schedule) courses", resolved, suggestions, not_found
    )
    context.user_data['added_courses'] = all_codes

    if needs_confirm:
        await update.message.reply_text(
            msg + "\n\nReply <b>ok</b> to confirm, or send a corrected list.",
            parse_mode=ParseMode.HTML,
        )
        context.user_data['_added_confirm'] = True
        return AWAITING_ADDED

    await update.message.reply_text(
        "Have you DROPPED any courses from a semester you already took "
        "(so you still need to take them later)?\n"
        "List them comma-separated, or reply 'None'."
    )
    return AWAITING_DROPPED


async def handle_dropped_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Confirmation mode.
    if context.user_data.pop('_dropped_confirm', False):
        if text.lower() not in ('ok', 'yes', 'y'):
            # User sent a corrected list — re-resolve.
            course_texts = _parse_course_list(text)
            if course_texts:
                resolved, suggestions, not_found = _resolve_course_list_with_feedback(course_texts)
                msg, all_codes, needs_confirm = _build_feedback_message(
                    "dropped courses", resolved, suggestions, not_found
                )
                context.user_data['dropped_courses'] = all_codes
                if needs_confirm:
                    await update.message.reply_text(
                        msg + "\n\nReply <b>ok</b> to confirm, or send a corrected list.",
                        parse_mode=ParseMode.HTML,
                    )
                    context.user_data['_dropped_confirm'] = True
                    return AWAITING_DROPPED
            else:
                context.user_data['dropped_courses'] = []
        # "ok" or clean list → fall through to plan computation.
    else:
        # Initial entry.
        course_texts = _parse_course_list(text)
        if course_texts:
            resolved, suggestions, not_found = _resolve_course_list_with_feedback(course_texts)
            msg, all_codes, needs_confirm = _build_feedback_message(
                "dropped courses", resolved, suggestions, not_found
            )
            context.user_data['dropped_courses'] = all_codes
            if needs_confirm:
                await update.message.reply_text(
                    msg + "\n\nReply <b>ok</b> to confirm, or send a corrected list.",
                    parse_mode=ParseMode.HTML,
                )
                context.user_data['_dropped_confirm'] = True
                return AWAITING_DROPPED
        else:
            context.user_data['dropped_courses'] = []

    # === Compute plan ===
    student = get_student_by_telegram_id(str(update.effective_user.id))
    year = context.user_data['year']
    sem = context.user_data['semester']
    stream_name = context.user_data['stream']
    failed_courses = context.user_data.get('failed_courses', [])
    added_courses = context.user_data.get('added_courses', [])
    dropped_courses = context.user_data.get('dropped_courses', [])

    await update.message.reply_text("⏳ Processing your academic history and calculating optimal recovery paths...")

    try:
        result_text, raw_result = process_reasoning_request_full(
            student, year, sem, stream_name, failed_courses, added_courses, dropped_courses)
        await update.message.reply_text(result_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in Reasoning Engine execution: {e}")
        await update.message.reply_text("🚨 I encountered an error calculating your plan. Please check your inputs and try again.")
        context.user_data.clear()
        return ConversationHandler.END

    if stream_name is None:
        # Undecided student saw comparison — let them pick a stream.
        await update.message.reply_text(
            "Which stream would you like the full plan for? "
            "(Computer, Communication, Control, or Power)"
        )
        return AWAITING_STREAM_POST_COMPARISON

    # Store raw result for this stream so "why" can use it without re-solving.
    context.user_data['_last_raw_result'] = raw_result
    context.user_data['_last_stream'] = stream_name

    # Offer explanation or another stream.
    await update.message.reply_text(
        "💡 Reply <b>why</b> to see why your plan looks like this,\n"
        "a stream name to see another stream's plan, or <b>No</b> to finish.",
        parse_mode=ParseMode.HTML,
    )
    return AWAITING_ANOTHER_STREAM

async def handle_post_comparison_stream_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chosen_stream = _match_stream_name(text)

    if chosen_stream is None:
        await update.message.reply_text(
            "❌ I didn't recognize that stream. Please reply with one of: "
            "Computer, Communication, Control, Power."
        )
        return AWAITING_STREAM_POST_COMPARISON

    student = get_student_by_telegram_id(str(update.effective_user.id))
    year = context.user_data['year']
    sem = context.user_data['semester']
    failed_courses = context.user_data.get('failed_courses', [])
    added_courses = context.user_data.get('added_courses', [])
    dropped_courses = context.user_data.get('dropped_courses', [])

    await update.message.reply_text(f"⏳ Generating your full {chosen_stream} stream plan...")

    try:
        result_text, raw_result = process_reasoning_request_full(
            student, year, sem, chosen_stream, failed_courses, added_courses, dropped_courses)
        await update.message.reply_text(result_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in Reasoning Engine execution: {e}")
        await update.message.reply_text("🚨 I encountered an error calculating your plan. Please try again.")
        return AWAITING_STREAM_POST_COMPARISON

    # Store for "why" queries.
    context.user_data['_last_raw_result'] = raw_result
    context.user_data['_last_stream'] = chosen_stream

    await update.message.reply_text(
        "💡 Reply <b>why</b> to see why your plan looks like this,\n"
        "a stream name to see another stream's plan, or <b>No</b> to finish.",
        parse_mode=ParseMode.HTML,
    )
    return AWAITING_ANOTHER_STREAM

async def cancel_planning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Course planning cancelled.")
    return ConversationHandler.END


async def handle_another_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lets the student request plans for additional streams without restarting
    the full /course_planning conversation.  Also accepts 'why' to explain the
    last plan that was shown."""
    text = update.message.text.strip()

    if text.lower() in ('no', 'done', 'finish', 'exit', 'stop', 'n'):
        context.user_data.clear()
        await update.message.reply_text(
            "👍 Planning session closed. Use /course_planning anytime to start over."
        )
        return ConversationHandler.END

    # "why" → explain the last shown plan without re-solving.
    if text.lower() == 'why':
        raw_result = context.user_data.get('_last_raw_result')
        explanation = build_explanation_message(raw_result)
        await update.message.reply_text(explanation, parse_mode=ParseMode.HTML)
        await update.message.reply_text(
            "💡 Reply a stream name to see another stream's plan, or <b>No</b> to finish.",
            parse_mode=ParseMode.HTML,
        )
        return AWAITING_ANOTHER_STREAM

    chosen_stream = _match_stream_name(text)
    if chosen_stream is None:
        await update.message.reply_text(
            "❌ I didn't recognize that. Reply <b>why</b> to explain the current plan,\n"
            "a stream name (Computer, Communication, Control, Power) to see another plan,\n"
            "or <b>No</b> to finish.",
            parse_mode=ParseMode.HTML,
        )
        return AWAITING_ANOTHER_STREAM

    student = get_student_by_telegram_id(str(update.effective_user.id))
    year = context.user_data['year']
    sem = context.user_data['semester']
    failed_courses = context.user_data.get('failed_courses', [])
    added_courses = context.user_data.get('added_courses', [])
    dropped_courses = context.user_data.get('dropped_courses', [])

    await update.message.reply_text(
        f"⏳ Generating the <b>{chosen_stream}</b> stream plan…",
        parse_mode=ParseMode.HTML,
    )
    try:
        result_text, raw_result = process_reasoning_request_full(
            student, year, sem, chosen_stream, failed_courses, added_courses, dropped_courses)
        await update.message.reply_text(result_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error generating {chosen_stream} stream plan: {e}")
        await update.message.reply_text("🚨 Error generating that plan. Please try again.")
        return AWAITING_ANOTHER_STREAM

    context.user_data['_last_raw_result'] = raw_result
    context.user_data['_last_stream'] = chosen_stream

    await update.message.reply_text(
        "💡 Reply <b>why</b> to see why your plan looks like this,\n"
        "a stream name to see another stream's plan, or <b>No</b> to finish.",
        parse_mode=ParseMode.HTML,
    )
    return AWAITING_ANOTHER_STREAM

# ==========================================
# 6. /feedback — Conversational Rating & Comment
# ==========================================

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "⭐ I'd love to hear from you!\n\n"
        "How would you rate the bot? Send a number from <b>1</b> (poor) to <b>5</b> (excellent).",
        parse_mode=ParseMode.HTML,
    )
    return FEEDBACK_AWAITING_RATING

async def feedback_receive_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in ("1", "2", "3", "4", "5"):
        await update.message.reply_text("❌ Please send a number between 1 and 5.")
        return FEEDBACK_AWAITING_RATING
    context.user_data["_fb_rating"] = int(text)
    await update.message.reply_text(
        "Thanks! Any comments or suggestions? (Send your message, or type <b>skip</b> to submit now.)",
        parse_mode=ParseMode.HTML,
    )
    return FEEDBACK_AWAITING_COMMENT

async def feedback_receive_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    comment = None if text.lower() == "skip" else text
    rating = context.user_data.pop("_fb_rating", None)

    student = get_student_by_telegram_id(str(update.effective_user.id))
    session = _BotSession()
    try:
        entry = UserFeedback(
            student_id=student.id,
            bot_response_context="general",
            rating=rating,
            comments=comment,
        )
        session.add(entry)
        session.commit()
    except Exception as e:
        logger.error(f"Failed to save feedback: {e}")
        session.rollback()
    finally:
        session.close()

    stars = "⭐" * rating
    await update.message.reply_text(
        f"✅ Thank you for your feedback! {stars}\n"
        "Your rating has been recorded. I appreciate your input! 🙏"
    )
    return ConversationHandler.END

async def feedback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("_fb_rating", None)
    await update.message.reply_text("Feedback cancelled.")
    return ConversationHandler.END

feedback_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("feedback", feedback_command)],
    states={
        FEEDBACK_AWAITING_RATING:  [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_receive_rating)],
        FEEDBACK_AWAITING_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, feedback_receive_comment)],
    },
    fallbacks=[CommandHandler("cancel", feedback_cancel)],
)


# ==========================================
# 7. /explain_commands
# ==========================================

async def explain_commands_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Brief guide to every bot command, shown as a formatted HTML message."""
    text = (
        "📖 <b>Command Guide</b>\n\n"
        "<b>ℹ️ Information Lookup</b>\n"
        "/course — Full details for a course (credit hours, prerequisites, stream)\n"
        "/semester — All courses offered in a given term\n"
        "/dependant — Courses that directly require this one as a prerequisite\n"
        "/downstream_impact — Full chain of courses blocked by failing one\n"
        "/cross_department — Courses shared with other departments (e.g. Civil, Mechanical)\n"
        "/cross_stream — Courses shared across multiple ECE streams in Years 4–5\n\n"
        "<b>📅 Planning</b>\n"
        "/course_planning — Generate a personalized multi-semester course plan based on your history\n\n"
        "<b>📊 GPA Tools</b>\n"
        "/gpa — Calculate SGPA for a semester, find the minimum SGPA needed for a CGPA goal, "
        "or project your future CGPA\n\n"
        "<b>⚙️ General</b>\n"
        "/explain_commands — Show this command guide\n"
        "/feedback — Rate the bot and leave a comment"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def _post_init(application):
    """Register bot commands with Telegram so the '/' menu popup works.
    /explain_commands is listed first so it appears at the top of the menu."""
    await application.bot.set_my_commands([
        BotCommand("explain_commands",  "Guide to all bot commands"),
        BotCommand("course",            "Get course details by code or name"),
        BotCommand("semester",          "List courses for a specific term"),
        BotCommand("dependant",         "Courses that require this one as a prerequisite"),
        BotCommand("downstream_impact", "Full chain blocked by failing a course"),
        BotCommand("cross_department",  "Courses shared with other departments"),
        BotCommand("cross_stream",      "Courses shared across multiple streams"),
        BotCommand("course_planning",   "Generate your personalized course plan"),
        BotCommand("gpa",               "Calculate SGPA or plan your CGPA"),
        BotCommand("feedback",          "Rate the bot and leave a comment"),
    ])


# ==========================================
# 8. Main Application Setup
# ==========================================

def main():
    import os
    from dotenv import load_dotenv
    load_dotenv()

    app = (
    Application.builder()
    .token(os.getenv("TELEGRAM_BOT_TOKEN"))
    .post_init(_post_init)
    .connect_timeout(30)
    .read_timeout(30)
    .write_timeout(30)
    .pool_timeout(30)
    .build()
)
    app.add_error_handler(error_handler)

    # /start — registration only for new users
    app.add_handler(CommandHandler("start", start_command))

    # Conversational query commands (each is its own ConversationHandler)
    app.add_handler(course_conv_handler)
    app.add_handler(dependant_conv_handler)
    app.add_handler(downstream_conv_handler)
    app.add_handler(semester_conv_handler)

    # Non-conversational query commands
    app.add_handler(CommandHandler("cross_department", cross_department_command))
    app.add_handler(CommandHandler("cross_stream", cross_stream_command))
    app.add_handler(CommandHandler("explain_commands", explain_commands_command))

    # Conversation handler for course planning
    planning_handler = ConversationHandler(
        entry_points=[CommandHandler("course_planning", start_course_planning)],
        states={
            AWAITING_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_year)],
            AWAITING_SEMESTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_semester)],
            AWAITING_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_stream)],
            AWAITING_FAILED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_failed_courses)],
            AWAITING_ADDED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_added_courses)],
            AWAITING_DROPPED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dropped_courses)],
            AWAITING_STREAM_POST_COMPARISON: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_post_comparison_stream_choice)],
            AWAITING_ANOTHER_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_another_stream)],
        },
        fallbacks=[CommandHandler("cancel", cancel_planning)]
    )
    app.add_handler(planning_handler)
    app.add_handler(gpa_conv_handler)
    app.add_handler(feedback_conv_handler)

    # Catch-all for non-command text — only acts when user is NOT yet registered
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration))

    print("🚀 Telegram Bot is running in Deterministic Command Mode...")
    app.run_polling()

if __name__ == "__main__":
    main()
