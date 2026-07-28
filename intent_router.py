# intent_router.py
from llm_client import generate_response

def classify_route(user_query: str) -> str:
    """Pure route classification: SQL_GRAPH, RAG_TELEGRAM, or REASONING_ENGINE."""
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
"When is the add/drop deadline?" -> RAG_TELEGRAM
"I failed ECEg3105, how do I still graduate on time?" -> REASONING_ENGINE
"What courses can I take next semester if I failed two courses?" -> REASONING_ENGINE

Student Query: {user_query}
Label:"""

    raw_response = generate_response(router_prompt, temperature=0).strip()
    normalized = raw_response.strip().upper()

    if "SQL_GRAPH" in normalized:
        return "SQL_GRAPH"
    elif "REASONING_ENGINE" in normalized:
        return "REASONING_ENGINE"
    elif "RAG_TELEGRAM" in normalized:
        return "RAG_TELEGRAM"
    return "ROUTING_FAILED"