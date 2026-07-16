import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from query_api import (
    get_eligible_courses, 
    check_course_registration_violations, 
    get_common_course_offering_alternatives,
    get_course_details,
    get_downstream_impact
)

# Load environment variables from .env file
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# Command: /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Welcome to the ECE Academic Advisor Bot (V1)!\n\n"
        "Here are the core commands you can use to query our curriculum:\n\n"
        "ℹ️ *Plain Course Details (No Student needed):*\n"
        "`/course <course_code>`\n"
        "_Example:_ `/course ECEg3201`\n\n"
        "💥 *Trace Downstream Impact of Failing/Dropping:*\n"
        "`/downstream <course_code>`\n"
        "_Example:_ `/downstream ECEg3201`\n\n"
        "🔍 *Check Course Eligibility:*\n"
        "`/eligible <student_id>`\n"
        "_Example:_ `/eligible ETS/1234/14`\n\n"
        "⚠️ *Validate Custom Registration List:*\n"
        "`/check <student_id> <course_codes>`\n"
        "_Example:_ `/check ETS/5678/14 ECEg5108`\n\n"
        "🔄 *Find Course Alternatives:*\n"
        "`/alternatives <course_code>`\n"
        "_Example:_ `/alternatives Math1014`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


# course_command: skip the Stream Scope line when details['stream'] is None
async def course_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a Course Code.\nUsage: `/course ECEg3201`", parse_mode="Markdown")
        return

    course_code = context.args[0].strip()
    details = get_course_details(course_code)

    if not details:
        await update.message.reply_text(f"❌ Course `{course_code}` not found in the loaded curriculum.", parse_mode="Markdown")
        return

    response = (
        f"📖 *Course Details: {details['code']}*\n\n"
        f"🔹 *Name:* {details['name']}\n"
        f"🔹 *Credit Hours:* {details['credit_hours']} Cr. Hrs\n"
        f"🔹 *Standing:* Year {details['year_level']}, Semester {details['semester']}\n"
        f"🔹 *Department Scope:* {details['scope']}\n"
    )
    if details['stream'] is not None:  # FIX: omit entirely before streams are chosen (Y4S2+)
        response += f"🔹 *Stream Scope:* {details['stream']}\n"
    response += f"🔹 *Prerequisites:*\n" + "\n".join(f"   • {p}" for p in details['prerequisites'])

    await update.message.reply_text(response, parse_mode="Markdown")


# downstream_command: use the renamed 'applies_to_streams' key
async def downstream_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a Course Code.\nUsage: `/downstream ECEg3201`", parse_mode="Markdown")
        return

    course_code = context.args[0].strip()
    target_course, impacted = get_downstream_impact(course_code)

    if not target_course:
        await update.message.reply_text(f"❌ Course `{course_code}` not found in the curriculum.", parse_mode="Markdown")
        return

    response = f"💥 *Downstream Prerequisite Block Cascade*\n"
    response += f"If a student fails/drops `{target_course.course_code}` ({target_course.name}), they will cascadingly be blocked from:\n\n"

    if not impacted:
        response += "_None! This course is a dead-end terminal course with no downstream dependents._"
    else:
        for course in impacted:
            response += (
                f"• `{course['course_code']}` — {course['name']}\n"
                f"   📍 *Standing:* Year {course['year_level']}, Sem {course['semester_offered']}\n"
                f"   📍 *Applies to:* {course['applies_to_streams']}\n\n"  # FIX: renamed key
            )

    await update.message.reply_text(response, parse_mode="Markdown")


# Commands from before (Passing along clean DB contexts)
async def eligible_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Please provide a Student ID.\nUsage: `/eligible ETS/1234/14`", parse_mode="Markdown")
        return
    student_id = context.args[0].strip()
    await update.message.reply_text(f"⏳ Querying active eligibility records for student: `{student_id}`...", parse_mode="Markdown")
    try:
        eligible, blocked = get_eligible_courses(student_id)
        if not eligible and not blocked:
            await update.message.reply_text("❌ Student ID not found.")
            return
        response = f"📋 *Academic Eligibility Report for {student_id}*\n\n✅ *Eligible Courses:*\n"
        response += "\n".join([f"• `{c.course_code}` — {c.name}" for c in eligible]) if eligible else "_None_\n"
        response += "\n\n❌ *Blocked Courses:*\n"
        if blocked:
            for course, missing in blocked:
                response += f"• `{course.course_code}` — {course.name}\n"
                for reason in missing:
                    response += f"   ⚠️ _{reason}_\n"
        else:
            response += "_None_"
        await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Database query error.")

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Missing parameters!\nUsage: `/check <student_id> <course_code>`", parse_mode="Markdown")
        return
    student_id = context.args[0].strip()
    proposed_courses = [code.strip() for code in context.args[1:]]
    try:
        violations = check_course_registration_violations(student_id, proposed_courses)
        if not violations:
            await update.message.reply_text(f"🎉 *No violations!* Student `{student_id}` is cleared.", parse_mode="Markdown")
        else:
            response = f"⚠️ *Registration Blocked!*:\n\n" + "\n".join([f"🛑 {v}" for v in violations])
            await update.message.reply_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(e)

async def alternatives_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return
    course_code = context.args[0].strip()
    try:
        alternatives = get_common_course_offering_alternatives(course_code)
        if isinstance(alternatives, str):
            await update.message.reply_text(f"ℹ️ {alternatives}")
        else:
            await update.message.reply_text(f"🔄 *Alternatives:*\n\n" + "\n".join([f"📍 {alt}" for alt in alternatives]), parse_mode="Markdown")
    except Exception as e:
        logger.error(e)


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN not found in environment. Did you set up your .env file?")
        return
    print("🤖 Launching Phase 2 Telegram Bot (With secure token & full V1 Commands)...")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("course", course_command))
    app.add_handler(CommandHandler("downstream", downstream_command))
    app.add_handler(CommandHandler("eligible", eligible_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("alternatives", alternatives_command))

    app.run_polling()

if __name__ == "__main__":
    main()