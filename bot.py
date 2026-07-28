from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from intent_router import classify_route
from orchestrator import process_student_query
from student_service import (
    get_student_by_telegram_id, link_telegram_account,
    get_derived_profile, set_student_stream, looks_like_student_id
)
from reasoning_intake import needs_stream, extract_courses_from_reply, record_course_statuses


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = str(update.effective_user.id)
    student = get_student_by_telegram_id(telegram_user_id)
    if student:
        await update.message.reply_text(f"👋 Welcome back, {student.name}! Ask me anything about your courses.")
        return
    context.user_data['awaiting_id'] = True
    await update.message.reply_text(
        "👋 Welcome to the ECE Academic Advisor Bot!\n\n"
        "Before we start, please send me your student ID (e.g. ETS/1444/13) so I can pull up your record."
    )


async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_user_id = str(update.effective_user.id)
    user_text = update.message.text.strip()
    user_data = context.user_data

    student = get_student_by_telegram_id(telegram_user_id)

    # --- Mandatory gate: must be linked before anything else works ---
    if not student:
        if not looks_like_student_id(user_text):
            await update.message.reply_text(
                "Please send me your student ID first (e.g. ETS/1444/13) -- it's required to get started."
            )
            return
        ok, msg = link_telegram_account(telegram_user_id, user_text)
        await update.message.reply_text(("✅ " if ok else "❌ ") + msg)
        return

    # --- Mid-intake: waiting on stream ---
    if user_data.get('reasoning_stage') == 'STREAM':
        ok, msg = set_student_stream(student, user_text)
        if not ok:
            await update.message.reply_text("❌ " + msg + " Please try again.")
            return
        user_data['reasoning_stage'] = 'FAILED_DROPPED'
        await update.message.reply_text(
            msg + "\n\nB. Do you have any courses you failed or dropped? "
                  "If so, list them (course code or name) and say whether each was failed or dropped. "
                  "If not, just say 'no'."
        )
        return

    # --- Mid-intake: waiting on failed/dropped courses ---
    if user_data.get('reasoning_stage') == 'FAILED_DROPPED':
        resolved = extract_courses_from_reply(user_text, kind="FAILED_DROPPED")
        if resolved is None:
            await update.message.reply_text("I didn't quite catch that -- could you rephrase which courses you failed or dropped, if any?")
            return
        recorded, unresolved = record_course_statuses(student, resolved)
        note = f"Recorded: {', '.join(recorded)}. " if recorded else ""
        if unresolved:
            note += f"⚠️ Couldn't identify: {', '.join(unresolved)} -- please check the course code/name."
        user_data['reasoning_stage'] = 'ADVANCED'
        await update.message.reply_text(
            note + "\n\nC. Have you added and PASSED any course(s) ahead of your normal schedule "
                   "(e.g. a future-semester course taken early)? If so, list them. If not, say 'no'."
        )
        return

    # --- Mid-intake: waiting on advanced/pulled-forward courses ---
    if user_data.get('reasoning_stage') == 'ADVANCED':
        resolved = extract_courses_from_reply(user_text, kind="ADVANCED")
        if resolved is None:
            await update.message.reply_text("I didn't quite catch that -- could you rephrase which courses you took ahead of schedule, if any?")
            return
        recorded, unresolved = record_course_statuses(student, resolved)
        note = f"Recorded: {', '.join(recorded)}. " if recorded else ""
        if unresolved:
            note += f"⚠️ Couldn't identify: {', '.join(unresolved)}."

        pending_query = user_data.pop('pending_query', user_text)
        user_data['reasoning_stage'] = None
        route = classify_route(user_text)
        result = process_student_query(pending_query, "REASONING_ENGINE", get_derived_profile(student))
        result = process_student_query(user_text, route, None)
        await update.message.reply_text(note + "\n\n" + result['response'])
        return

    # --- Normal, new question ---
    route = classify_route(user_text)

    if route == "ROUTING_FAILED":
        await update.message.reply_text("⚠️ I had trouble understanding that. Could you rephrase?")
        return

    if route == "REASONING_ENGINE":
        profile = get_derived_profile(student)
        user_data['pending_query'] = user_text

        if needs_stream(student, profile):
            user_data['reasoning_stage'] = 'STREAM'
            await update.message.reply_text(
                "To help plan your path, a few quick questions:\n\n"
                "A. What is your stream? (Computer, Communication, Control, or Power)"
            )
            return

        user_data['reasoning_stage'] = 'FAILED_DROPPED'
        await update.message.reply_text(
            "A. Your batch/semester is already on file.\n\n"
            "B. Do you have any courses you failed or dropped? "
            "If so, list them (course code or name) and say whether each was failed or dropped. "
            "If not, just say 'no'."
        )
        return

    # SQL_GRAPH / RAG_TELEGRAM -- no profile questions at all, per your spec
    result = process_student_query(user_text, None)
    await update.message.reply_text(result['response'])


def main():
    import os
    from dotenv import load_dotenv
    load_dotenv()
    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    app.run_polling()


if __name__ == "__main__":
    main()