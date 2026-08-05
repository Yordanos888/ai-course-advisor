"""
check_llm_synthesis.py

Fires a real API call to the LLM to verify that it strictly adheres to the 
system prompt constraints (no hallucination, strict context adherence).
Run with: python check_llm_synthesis.py
"""
import os
from orchestrator import generate_response

# 1. The exact system prompt from your orchestrator.py
synthesis_system_prompt = """You are an expert, supportive ECE Academic Advisor at AASTU.

Strict rules you must follow:
- Base your response ENTIRELY on the Verified Backend Context you are given below.
- Include every relevant detail present in the context — do not drop specifics for
  brevity. For example, if a course listing specifies which stream(s) it applies to,
  state that explicitly; do not just list the course code.
- If a detail (a course name, a reason, a policy) is not present in the context, do not invent it —
  omit it or say it isn't specified, rather than making up a plausible-sounding explanation.
- If a course name is not given in the context, refer to it only by its course code.
- Be concise: deliver only the relevant information, with enough explanation to be
  clear, but no padding, no repeated phrasing, and no unnecessary elaboration."""

# 2. A simulated student query
user_query = "I failed Applied Math I last semester. How does this affect my graduation in the Computer Engineering stream?"

# 3. A simulated backend context (matching the output format we just tested)
backend_context = """Reasoning Engine Result: 1 ranked recovery plan(s) found, each completing in 8 semester(s).

Option 1:
Year 1, Semester 2: MATH1014 (Applied Math I, 5 cr); ECE1022 (Basic Programming, 3 cr)
Year 2, Semester 1: MATH2007 (Applied Math II, 5 cr); ECE2031 (Circuit Analysis, 4 cr)
Year 2, Semester 2: ECE2041 (Signals & Systems, 5 cr)
Year 3, Semester 1: ECE3042 (Digital Signal Processing, 5 cr) [Computer Engineering stream only]"""

# 4. Assemble the final prompt
synthesis_prompt = f"""Student Query: {user_query}

Verified Backend Context:
{backend_context}

Write your advisor response now."""

def run_quality_check():
    print("==================================================")
    print("TRANSMITTING TO LLM...")
    print("==================================================\n")
    
    try:
        # Firing the real request to your LLM client
        ai_response = generate_response(
            synthesis_prompt, 
            temperature=0.3, 
            system=synthesis_system_prompt
        )
        
        print("🤖 AI ADVISOR RESPONSE:")
        print("--------------------------------------------------")
        print(ai_response.strip())
        print("--------------------------------------------------\n")
        
        print("🔍 QUALITY CHECKLIST FOR YOU TO VERIFY:")
        print("[ ] Did it maintain an expert, supportive tone?")
        print("[ ] Did it include the specific credit hours and course names?")
        print("[ ] Did it explicitly mention the 'Computer Engineering stream only' constraint for DSP?")
        print("[ ] Did it avoid making up any outside rules or policies?")
        
    except Exception as e:
        print(f"Error calling LLM: {e}")

if __name__ == "__main__":
    run_quality_check()