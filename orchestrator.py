# orchestrator.py
import re
from intent_router import route_and_validate
from rag_service import get_educational_context
from query_api import get_course_details, get_downstream_impact, get_student_academic_summary
from llm_client import generate_response

def process_student_query(user_query: str, student_profile: dict = None) -> str:
    """
    Central orchestration layer executing Phase 5 goals:
    1. Routes and validates profile rules via intent_router.py.
    2. Dynamically pulls data from SQL Graph or ChromaDB RAG layers.
    3. Handles empty/low-confidence results with explicit human escalation.
    4. Generates a transparent, grounded natural language answer using llama3.1.
    """

    # Step 1: Run the gateway intent analysis & profile structural check
    routing_result = route_and_validate(user_query, student_profile)
    route = routing_result["route"]

    # If the user needs to provide profile metrics, or routing itself failed,
    # return the router's own message directly -- no backend call to make.
    if route == "STATUS_CHECK" or route == "ROUTING_FAILED":
        return routing_result["response"]

    # Step 2 & 3: Execute target backend queries & check for content presence
    backend_context = ""

    if route == "SQL_GRAPH":
        # Extract potential course codes using regular expressions (e.g., ECEg3201)
        course_codes = re.findall(r'[A-Za-z]{3,4}g?\d{4}', user_query)
        if not course_codes:
            return ("⚠️ I recognized you are asking about course curriculum facts, "
                    "but I couldn't isolate a specific course code (e.g., ECEg3201). "
                    "Could you please specify which course code you are interested in?")

        target_code = course_codes[0]

        # Pull core structured details and cascade block maps from Phase 1
        details = get_course_details(target_code)
        if not details:
            return (f"🛑 Escalation Notice: I cannot locate the course `{target_code}` in the "
                    f"official ECE curriculum schema. Please check the spelling or verify with the "
                    f"Department Head Office to see if this is an updated course listing.")

        backend_context = (
            f"Course Code: {details['code']}\n"
            f"Name: {details['name']}\n"
            f"Credit Hours: {details['credit_hours']}\n"
            f"Academic Standing Slot: Year {details['year_level']}, Semester {details['semester']}\n"
            f"Prerequisites: {', '.join(details['prerequisites'])}\n"
        )

        # If the user question points to failing or dropping impacts, track downstream links
        if any(word in user_query.lower() for word in ["fail", "drop", "block", "cascade"]):
            _, impacted = get_downstream_impact(target_code)
            if impacted:
                backend_context += "\nDownstream Courses Blocked if Failed/Dropped:\n"
                for imp in impacted:
                    backend_context += f"- {imp['course_code']} ({imp['name']}) [Applies to: {imp['applies_to_streams']}]\n"
            else:
                backend_context += "\nDownstream Impacts: None. This is a terminal course.\n"

    elif route == "RAG_TELEGRAM":
        retrieved_announcement = get_educational_context(user_query, distance_threshold=0.6)

        if not retrieved_announcement:
            return ("🛑 Escalation Notice: I searched the recent department announcements and channel postings "
                    "regarding your request, but could not find matching official guidelines. Please consult the "
                    "ECE Academic Advisor or visit the Registrar's Office for clear validation.")

        backend_context = f"Official Telegram Channel Notice Content:\n{retrieved_announcement}"

    elif route == "REASONING_ENGINE":
        # Temporary structural placeholder until Phase 6 CP-SAT logic goes live
        return ("⚙️ The Advanced Search Constraint solver is active. "
                "I will be computing optimal multi-year recovery plans for you shortly.")

    # Step 4: Pass structured framework context to llama3.1 for clean, grounded response synthesis
    synthesis_system_prompt = """You are an expert, supportive ECE Academic Advisor at AASTU.

Strict rules you must follow:
- Base your response ENTIRELY on the Verified Backend Context you are given below.
- If a detail (a course name, a reason, a policy) is not present in the context, do not invent it --
  omit it or say it isn't specified, rather than making up a plausible-sounding explanation.
- If a course name is not given in the context, refer to it only by its course code.
- Explain the 'why' behind requirements only using what the context actually states.
- Keep your tone warm and clear, but do not pad your answer with invented elaboration."""

    synthesis_prompt = f"""Student Query: {user_query}

Verified Backend Context:
{backend_context}

Write your advisor response now."""

    ai_response = generate_response(synthesis_prompt, temperature=0.3, system=synthesis_system_prompt).strip()
    return ai_response