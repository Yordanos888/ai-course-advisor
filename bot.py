import logging
import re
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler
)

# Assuming these will be updated in the new query_api.py and deterministic logic modules
from query_api import (
    get_course_details_formatted, 
    get_downstream_impact_formatted,
    get_semester_courses_formatted,
    get_dependant_courses_formatted,
    get_cross_department_formatted,
    get_cross_stream_formatted
)
from student_service import (
    get_student_by_telegram_id, register_or_link_student,
    get_derived_profile, needs_batch_semester, needs_stream,
    set_student_stream, looks_like_student_id
)
from academic_intake import assign_batch_from_reply
from orchestrator import process_reasoning_request  # The new deterministic entry point for Phase 6

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

ECE_DEPARTMENT_ID = 1

# Conversation States for /course_planning
AWAITING_BATCH, AWAITING_STREAM, AWAITING_FAILED, AWAITING_ADVANCED = range(4)

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
        "/downstream_impact <code> - See what failing a course blocks\n"
        "/semester <year> <semester> - List courses for a specific term\n"
        "/dependant <code> - See courses that depend on this one\n"
        "/cross_department - List cross-department courses\n"
        "/cross_stream - List cross-stream courses\n"
        "/course_planning - Generate a multi-semester recovery plan\n"
    )

    if student:
        await update.message.reply_text(f"👋 Welcome back, {student.name}!\n\n{welcome_text}")
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
    await update.message.reply_text(response)

async def downstream_impact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    if not context.args:
        await update.message.reply_text("Usage: /downstream_impact <course_code>\nExample: /downstream_impact Math1007")
        return
    course_code = context.args[0]
    response = get_downstream_impact_formatted(course_code)
    await update.message.reply_text(response)

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
    # Grab the optional stream argument if provided
    stream = " ".join(context.args[2:]) if len(context.args) > 2 else None
    
    response = get_semester_courses_formatted(year, semester, stream)
    await update.message.reply_text(response)

async def dependant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    if not context.args:
        await update.message.reply_text("Usage: /dependant <course_code>\nExample: /dependant ECEg2102")
        return
    course_code = context.args[0]
    response = get_dependant_courses_formatted(course_code)
    await update.message.reply_text(response)

async def cross_department_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    response = get_cross_department_formatted()
    await update.message.reply_text(response)

async def cross_stream_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return
    response = get_cross_stream_formatted()
    await update.message.reply_text(response)

# ==========================================
# 3. Course Planning Intake (Deterministic State Machine)
# ==========================================

async def start_course_planning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_registration(update): return ConversationHandler.END
    
    student = get_student_by_telegram_id(str(update.effective_user.id))
    profile = get_derived_profile(student)

    if needs_batch_semester(profile):
        await update.message.reply_text("To plan your path, what is your current batch/semester?\n(Format: Year Semester, e.g., '4 2')")
        return AWAITING_BATCH

    if needs_stream(student, profile):
        await update.message.reply_text("What is your stream? (Options: Computer, Communication, Control, Power)")
        return AWAITING_STREAM

    await update.message.reply_text(
        "Do you have any courses you failed or dropped?\n"
        "Please reply with comma-separated course codes (e.g., ECEg3105, Math1007).\n"
        "If none, reply with 'None'."
    )
    return AWAITING_FAILED

async def handle_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = get_student_by_telegram_id(str(update.effective_user.id))
    text = update.message.text.strip()
    
    ok, msg = assign_batch_from_reply(student, text, ECE_DEPARTMENT_ID)
    if not ok:
        await update.message.reply_text("❌ " + msg + " Please try again (e.g., '4 2').")
        return AWAITING_BATCH

    profile = get_derived_profile(student)
    if needs_stream(student, profile):
        await update.message.reply_text(msg + "\n\nWhat is your stream? (Computer, Communication, Control, Power)")
        return AWAITING_STREAM

    await update.message.reply_text(
        msg + "\n\nDo you have any courses you failed or dropped?\n"
        "Reply with comma-separated course codes (e.g., ECEg3105, Math1007) or 'None'."
    )
    return AWAITING_FAILED

async def handle_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    student = get_student_by_telegram_id(str(update.effective_user.id))
    text = update.message.text.strip()
    
    ok, msg = set_student_stream(student, text)
    if not ok:
        await update.message.reply_text("❌ " + msg + " Please try again.")
        return AWAITING_STREAM

    await update.message.reply_text(
        msg + "\n\nDo you have any courses you failed or dropped?\n"
        "Reply with comma-separated course codes (e.g., ECEg3105, Math1007) or 'None'."
    )
    return AWAITING_FAILED

async def handle_failed_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data['failed_courses'] = [] if text.lower() == 'none' else [c.strip() for c in text.split(',')]
    
    await update.message.reply_text(
        "Have you added and PASSED any courses ahead of schedule?\n"
        "Reply with comma-separated course codes or 'None'."
    )
    return AWAITING_ADVANCED

async def handle_advanced_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    advanced_courses = [] if text.lower() == 'none' else [c.strip() for c in text.split(',')]
    failed_courses = context.user_data.get('failed_courses', [])
    
    student = get_student_by_telegram_id(str(update.effective_user.id))
    
    await update.message.reply_text("⏳ Processing your exact academic history and calculating optimal recovery paths...")
    
    try:
        # This function will now handle strict string-matching for course codes, save to DB, and run OR-Tools
        result = process_reasoning_request(student, failed_courses, advanced_courses)
        await update.message.reply_text(result)
    except Exception as e:
        logger.error(f"Error in Reasoning Engine execution: {e}")
        await update.message.reply_text("🚨 I encountered an error calculating your plan. Please check your course codes and try again.")
    
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
            AWAITING_BATCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_batch)],
            AWAITING_STREAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_stream)],
            AWAITING_FAILED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_failed_courses)],
            AWAITING_ADVANCED: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_advanced_courses)],
        },
        fallbacks=[CommandHandler("cancel", cancel_planning)]
    )
    app.add_handler(planning_handler)

    # Catch-all for non-command text (handles registration if not registered)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration))
    
    print("🚀 Telegram Bot is running in Deterministic Command Mode...")
    app.run_polling()

if __name__ == "__main__":
    main()