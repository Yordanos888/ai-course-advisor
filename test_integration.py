# test_integration.py
from orchestrator import process_student_query

print("🚀 Testing Phase 5 Final Checkpoint Integration Suite...\n")

# Mock Student Profile tracking (Pre-Y4S2 vs Post-Y4S2)
mock_profile_new = None
mock_profile_active = {"year": "4th year", "semester": "2nd semester", "stream": "Computer"}

# Test Case 1: Course Info Lookup (Grounded SQL Verification)
q1 = "What are the requirements and credits for ECEg4105?" 
print(f"💬 Question: {q1}")
print(f"🤖 Advisor:\n{process_student_query(q1, mock_profile_new)['response']}\n")
print("-" * 50)

# Test Case 2: Downstream Block Impact
q2 = "What happens if I drop or fail ECEg4105?"  # incorrect course code
print(f"💬 Question: {q2}")
print(f"🤖 Advisor:\n{process_student_query(q1, mock_profile_new)['response']}\n")
print("-" * 50)

# Test Case 3: RAG Failure / Escalation Fallback Trigger
q3 = "Where can I find the special registration form links for the summer session?"
print(f"💬 Question: {q3}")
print(f"🤖 Advisor:\n{process_student_query(q1, mock_profile_new)['response']}\n")
print("-" * 50)