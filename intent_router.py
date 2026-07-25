# intent_router.py
from llm_client import generate_response

def route_and_validate(user_query: str, student_profile: dict = None) -> dict:
    """
    Analyzes the user's input, determines the intent route using the LLM,
    and enforces status validation if an advisor routing path is triggered.
    """

    # 1. Let llama3.1 handle direct classification -- temperature=0 for
    # deterministic, repeatable routing (the same kind of question should
    # always land on the same route), plus a few-shot example set so the
    # model reliably separates "what does failing X block" (a simple lookup)
    # from "help me plan around my failure" (a genuine multi-step plan).
    router_prompt = f"""You are an administrative intent router for an ECE AI Course Advisor at AASTU.
Reply with EXACTLY one label: SQL_GRAPH, RAG_TELEGRAM, or REASONING_ENGINE.
No punctuation, no explanation, no extra words.

Rules:
- SQL_GRAPH: standard course facts, credit hours, direct prerequisites, OR what a
  specific course blocks if failed/dropped (a single-course lookup).
- RAG_TELEGRAM: recent notices, schedules, or announcements posted on the channel.
- REASONING_ENGINE: ONLY when the student wants a personalized multi-semester
  recovery or graduation PLAN -- not simply "what does failing this course block".

Examples:
"What happens if I fail ECEg3105?" -> SQL_GRAPH
"What does dropping ECEg3105 block?" -> SQL_GRAPH
"What are the prerequisites for ECEg4102?" -> SQL_GRAPH
"When is the add/drop deadline?" -> RAG_TELEGRAM
"I failed ECEg3105, how do I still graduate on time?" -> REASONING_ENGINE
"What courses can I take next semester if I failed two courses?" -> REASONING_ENGINE

Student Query: {user_query}
Label:"""

    raw_response = generate_response(router_prompt, temperature=0).strip()
    print(f"[DEBUG] raw_response = {raw_response!r}")   # TEMPORARY -- remove once fixed

    raw_response = generate_response(router_prompt, temperature=0).strip()

    # 2. Normalize -- case-insensitive matching, and no silent default.
    normalized = raw_response.strip().upper()

    if "SQL_GRAPH" in normalized:
        clean_route = "SQL_GRAPH"
    elif "REASONING_ENGINE" in normalized:
        clean_route = "REASONING_ENGINE"
    elif "RAG_TELEGRAM" in normalized:
        clean_route = "RAG_TELEGRAM"
    else:
        # The model returned something unrecognizable (including an error
        # string from llm_client's own exception handling). This used to
        # silently fall through to RAG_TELEGRAM -- now it's an explicit,
        # visible failure state instead of a guess.
        return {
            "route": "ROUTING_FAILED",
            "missing_data": [],
            "response": (
                "⚠️ I had trouble understanding how to route your question just now. "
                "Could you try rephrasing it, or ask again in a moment?"
            )
        }

    # 3. Enforce Academic Status Checks ONLY if it hits the reasoning engine
    if clean_route == "REASONING_ENGINE":
        missing_fields = []

        year = student_profile.get("year") if student_profile else None
        semester = student_profile.get("semester") if student_profile else None
        stream = student_profile.get("stream") if student_profile else None

        if not year:
            missing_fields.append("current year (e.g., 4th year)")
        if not semester:
            missing_fields.append("current semester (e.g., 2nd semester)")

        # AASTU Streaming Validation Point
        if year:
            is_4th_year_2nd_sem = ("4th" in str(year) and "2nd" in str(semester))
            is_5th_year = "5th" in str(year)

            if (is_4th_year_2nd_sem or is_5th_year) and not stream:
                missing_fields.append("specialization stream (Computer, Communication, Control, or Power)")

        if missing_fields:
            return {
                "route": "STATUS_CHECK",
                "missing_data": missing_fields,
                "response": (
                    "To give you the correct advice on your graduation timeline or stream recovery, "
                    "I need to know your exact academic status. Please tell me your current year and semester. "
                    "Since you are at or past the 4th year 2nd semester streaming period, please also specify your chosen stream "
                    "(Computer, Communication, Control, or Power), or mention if you need help choosing one based on your failed courses."
                )
            }

    return {
        "route": clean_route,
        "missing_data": [],
        "response": None
    }