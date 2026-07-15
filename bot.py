import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from query_api import (
    get_eligible_courses, 
    check_course_registration_violations, 
    get_common_course_offering_alternatives
)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 1. Enable logging to see updates and errors in the console
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# 2. Command: /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Welcome to the ECE Academic Advisor Bot!\n\n"
        "I am currently running in *Phase 2 (Deterministic Interface)*.\n"
        "Please use the following structured commands:\n\n"
        "🔍 *Check Course Eligibility:*\n"
        "`/eligible <student_id>`\n"
        "_Example:_ `/eligible ETS/1234/14`\n\n"
        "⚠️ *Validate Registration Plan:*\n"
        "`/check <student_id> <course_codes>`\n"
        "_Example:_ `/check ETS/5678/14 ECEg4108 ECEg4201`\n\n"
        "🔄 *Find Course Alternatives:*\n"
        "`/alternatives <course_code>`\n"
        "_Example:_ `/alternatives Math1014`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


# 3. Command: /eligible <student_id>
async def eligible_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a Student ID.\nUsage: `/eligible ETS/1234/14`", parse_mode="Markdown")
        return

    student_id = context.args[0].strip()
    await update.message.reply_text(f"⏳ Querying active eligibility records for student: `{student_id}`...", parse_mode="Markdown")

    try:
        # Call our robust Phase 1/3 Query API
        eligible, blocked = get_eligible_courses(student_id)
        
        if not eligible and not blocked:
            await update.message.reply_text("❌ Student ID not found, or they have no active courses matching their current batch status.")
            return

        response = f"📋 *Academic Eligibility Report for {student_id}*\n\n"
        
        response += "✅ *Eligible Courses (Cleared to Take):*\n"
        if eligible:
            for course in eligible:
                response += f"• `{course.course_code}` — {course.name} ({course.credit_hours} Cr. Hrs)\n"
        else:
            response += "_None_\n"

        response += "\n❌ *Blocked Courses:*\n"
        if blocked:
            for course, missing in blocked:
                response += f"• `{course.course_code}` — {course.name}\n"
                for reason in missing:
                    response += f"   ⚠️ _{reason}_\n"
        else:
            response += "_None (All offered courses cleared!)_"

        await update.message.reply_text(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error checking eligibility: {e}")
        await update.message.reply_text("❌ An error occurred while retrieving student eligibility. Please check the backend console.")


# 4. Command: /check <student_id> <course_codes>
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Missing parameters!\n"
            "Usage: `/check <student_id> <course_code_1> <course_code_2> ...`\n"
            "_Example:_ `/check ETS/5678/14 ECEg4102 ECEg4201`", 
            parse_mode="Markdown"
        )
        return

    student_id = context.args[0].strip()
    proposed_courses = [code.strip() for code in context.args[1:]]

    await update.message.reply_text(
        f"⏳ Evaluating prerequisite and stream safety validation rules for `{student_id}` registering for {', '.join(proposed_courses)}...",
        parse_mode="Markdown"
    )

    try:
        # Call validator
        violations = check_course_registration_violations(student_id, proposed_courses)
        
        if not violations:
            await update.message.reply_text(
                f"🎉 *No registration violations found!* Student `{student_id}` is fully cleared to take these courses.",
                parse_mode="Markdown"
            )
        else:
            response = f"⚠️ *Registration Blocked!* Found {len(violations)} rule violations for `{student_id}`:\n\n"
            for v in violations:
                response += f"🛑 {v}\n"
            await update.message.reply_text(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error executing registration validation: {e}")
        await update.message.reply_text("❌ An error occurred during validation. Check console logs.")


# 5. Command: /alternatives <course_code>
async def alternatives_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a Course Code.\nUsage: `/alternatives Math1014`", parse_mode="Markdown")
        return

    course_code = context.args[0].strip()
    
    try:
        alternatives = get_common_course_offering_alternatives(course_code)
        
        if isinstance(alternatives, str):
            # API returned an error string or "no shared mappings found"
            await update.message.reply_text(f"ℹ️ {alternatives}")
        else:
            response = f"🔄 *Cross-Department Offerings for {course_code.upper()}:*\n\n"
            for alt in alternatives:
                response += f"📍 {alt}\n"
            await update.message.reply_text(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error looking up alternatives: {e}")
        await update.message.reply_text("❌ An error occurred during alternative course lookup.")


# 6. Initialize Bot Application
def main():
    print("🤖 Launching Phase 2 Telegram Bot...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("eligible", eligible_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("alternatives", alternatives_command))

    # Run polling loop
    print("✨ Bot is active and listening for user commands!")
    app.run_polling()

if __name__ == "__main__":
    main()