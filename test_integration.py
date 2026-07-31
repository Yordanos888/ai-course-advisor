# test_integration.py
from orchestrator import process_student_query

print("🚀 Testing Phase 5 Final Checkpoint Integration Suite...\n")

# Mock Student Profile tracking (Pre-Y4S2 vs Post-Y4S2)
mock_profile_new = None
mock_profile_comp = {"year": "4th year", "semester": "2nd semester", "stream": "Computer"}
mock_profile_cont = {"year": "4th year", "semester": "2nd semester", "stream": "Control"}

# Test Case 1: Course Info Lookup (Grounded SQL Verification)
q1 = "when is the date for 2027 education registration?" 
print(f"💬 Question: {q1}")
print(f"🤖 Advisor:\n{process_student_query(q1, mock_profile_new)['response']}\n")
print("-" * 50)

# Test Case 2: Downstream Block Impact
q2 = "when is the date the internship money will be sent?"  
print(f"💬 Question: {q2}")
print(f"🤖 Advisor:\n{process_student_query(q2, mock_profile_comp)['response']}\n")
print("-" * 50)

# Test Case 3: RAG Failure / Escalation Fallback Trigger
q3 = "Where can I find the special registration form links for the summer session?"
print(f"💬 Question: {q3}")
print(f"🤖 Advisor:\n{process_student_query(q3, mock_profile_new)['response']}\n")
print("-" * 50)

# Test 4: same as test 2 but different stream
q4 = "when is the deadline date for presenting internship?"  
print(f"💬 Question: {q4}")
print(f"🤖 Advisor:\n{process_student_query(q4, mock_profile_cont)['response']}\n")
print("-" * 50)