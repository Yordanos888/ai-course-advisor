import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)


from query_api import (
    get_course_details_formatted,
    get_downstream_impact_formatted,
    get_semester_courses_formatted,
    get_dependant_courses_formatted,
    get_cross_department_formatted,
    get_cross_stream_formatted
)
from student_service import (
    get_student_by_telegram_id,
    register_or_link_student,
    looks_like_student_id
)
from orchestrator import process_reasoning_request
from gpa_bot import gpa_conv_handler


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
    AWAITING_ADDED, AWAITING_DROPPED, AWAITING_STREAM_POST_COMPARISON = range(7)

STREAM_NAMES = ["Computer", "Communication", "Control", "Power"]


def _parse_course_list(text: str):
    """'None' (any case) -> empty list; otherwise split on commas."""
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


# ==========================================
# 1. Registration & Core Commands
# ==========================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = str(update.effective_user.id)
    student = get_student_by_telegram_id(telegram_user_id)

    welcome_text = (
        "👋 Welcome to the ECE Academic Advisor Bot!\n\n"
        "Available Commands:\n"
        "/course <code or name> - Get course details\n"
        "/downstream_impact <code or name> - See what failing a course blocks\n"
        "/semester <year> <semester> [stream] - List courses for a specific term\n"
        "/dependant <code or name> - See courses that depend on this one\n"
        "/cross_department - List cross-department courses\n"
        "/cross_stream - List cross-stream courses\n"
        "/course_planning - Generate a multi-semester recovery plan\n"
        "/gpa - Calculate your SGPA/CGPA"
    )

    if student:
        await update.message.reply_text(f"👋 Welcome back, {student.name}!\n\n{welcome_text}",
                                          parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text(
        "👋 Welcome to the ECE Academic Advisor Bot!\n\n"
        "Please send me your student ID (e.g., ETS/1444/13) to register before using commands."
    )

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = str(update.effective_user.id)
    user_text = update.message.text.strip()

    if looks_like_student_id(user_text):
        ok, msg = register_or_link_student(telegram_user_id, user_text)
        await update.message.reply_text(("✅ " if ok else "❌ ") + msg)
    else:
        await update.message.reply_text("Please send a valid student ID (e.g., ETS/1444/13).")


# ==========================================
# 2. Information Query Commands
# ==========================================

async def require_registration(update: Update) -> bool:
    """Helper to block unregistered users from using commands."""
    student = get_student_by_telegram_id(str(update.effective_user.id))
    if not student:
        await update.message.reply_text("⚠️ Please register with your Student ID first.")
        return False
    return True

async def course_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    if not context.args:
        await update.message.reply_text("Usage: /course <course_code or name>\nExample: /course ECEg3105")
        return
    query = " ".join(context.args)
    response = get_course_details_formatted(query)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)

async def downstream_impact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    if not context.args:
        await update.message.reply_text("Usage: /downstream_impact <course_code or name>\nExample: /downstream_impact Math1007")
        return
    query = " ".join(context.args)
    response = get_downstream_impact_formatted(query)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)

async def semester_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /semester <year> <semester> [stream]\n"
            "Example: /semester 4 2 Computer"
        )
        return

    year = context.args[0]
    semester = context.args[1]
    stream = " ".join(context.args[2:]) if len(context.args) > 2 else None

    response = get_semester_courses_formatted(year, semester, stream)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)

async def dependant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    if not context.args:
        await update.message.reply_text("Usage: /dependant <course_code or name>\nExample: /dependant Applied Electronics I")
        return
    query = " ".join(context.args)
    response = get_dependant_courses_formatted(query)
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)

async def cross_department_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    response = get_cross_department_formatted()
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)

async def cross_stream_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    response = get_cross_stream_formatted()
    await update.message.reply_text(response, parse_mode=ParseMode.HTML)


# ==========================================
# 3. Course Planning Intake (Deterministic State Machine)
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
    context.user_data['failed_courses'] = _parse_course_list(update.message.text)
    await update.message.reply_text(
        "Have you taken any courses AHEAD of your normal schedule "
        "(courses from a later semester that you already passed)?\n"
        "List them comma-separated, or reply 'None'."
    )
    return AWAITING_ADDED


async def handle_added_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['added_courses'] = _parse_course_list(update.message.text)
    await update.message.reply_text(
        "Have you DROPPED any courses from a semester you already took "
        "(so you still need to take them later)?\n"
        "List them comma-separated, or reply 'None'."
    )
    return AWAITING_DROPPED


async def handle_dropped_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['dropped_courses'] = _parse_course_list(update.message.text)

    student = get_student_by_telegram_id(str(update.effective_user.id))
    year = context.user_data['year']
    sem = context.user_data['semester']
    stream_name = context.user_data['stream']
    failed_courses = context.user_data.get('failed_courses', [])
    added_courses = context.user_data.get('added_courses', [])
    dropped_courses = context.user_data.get('dropped_courses', [])

    await update.message.reply_text("⏳ Processing your academic history and calculating optimal recovery paths...")

    try:
        result = process_reasoning_request(student, year, sem, stream_name,
                                            failed_courses, added_courses, dropped_courses)
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in Reasoning Engine execution: {e}")
        await update.message.reply_text("🚨 I encountered an error calculating your plan. Please check your inputs and try again.")
        context.user_data.clear()
        return ConversationHandler.END

    if stream_name is None:
        # Undecided student just saw the stream comparison -- stay in the
        # conversation to catch their stream pick as the NEXT message,
        # instead of ending here (which used to leave the bot with no
        # state to interpret that reply, so it fell through to the
        # registration handler and asked for a student ID instead).
        await update.message.reply_text(
            "Which stream would you like the full plan for? "
            "(Computer, Communication, Control, or Power)"
        )
        return AWAITING_STREAM_POST_COMPARISON

    context.user_data.clear()
    return ConversationHandler.END

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
        result = process_reasoning_request(student, year, sem, chosen_stream,
                                            failed_courses, added_courses, dropped_courses)
        await update.message.reply_text(result, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error in Reasoning Engine execution: {e}")
        await update.message.reply_text("🚨 I encountered an error calculating your plan. Please try again.")

    context.user_data.clear()
    return ConversationHandler.END

async def cancel_planning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Course planning cancelled.")
    return ConversationHandler.END

# ==========================================
# 4. Main Application Setup
# ==========================================

def main():
    import os
    from dotenv import load_dotenv
    load_dotenv()

    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    # Base commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("course", course_command))
    app.add_handler(CommandHandler("downstream_impact", downstream_impact_command))
    app.add_handler(CommandHandler("semester", semester_command))
    app.add_handler(CommandHandler("dependant", dependant_command))
    app.add_handler(CommandHandler("cross_department", cross_department_command))
    app.add_handler(CommandHandler("cross_stream", cross_stream_command))

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
        },
        fallbacks=[CommandHandler("cancel", cancel_planning)]
    )
    app.add_handler(planning_handler)
    app.add_handler(gpa_conv_handler)

    # Catch-all for non-command text (handles registration if not registered)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration))

    print("🚀 Telegram Bot is running in Deterministic Command Mode...")
    app.run_polling()

if __name__ == "__main__":
    main()
