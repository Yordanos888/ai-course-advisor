# test_router.py
from intent_router import route_and_validate

print("🧪 Test A: Student asks about failing a course without profile history...")
profile_empty = {"year": None, "semester": None, "stream": None}
result_a = route_and_validate("I failed my last course, what should I do?", profile_empty)
print(f"Routed To: {result_a['route']}")
print(f"Missing Fields: {result_a['missing_data']}")
print(f"AI Prompt: {result_a['response']}\n")

print("-" * 40)

print("🧪 Test B: General prerequisite check...")
result_b = route_and_validate("What do I need to pass before taking Microprocessors?", profile_empty)
print(f"Routed To: {result_b['route']}\n")

print("-" * 40)

print("🧪 Test C: Student in 4th year 2nd semester asking about graduation without specifying stream...")
profile_4th_year = {"year": "4th year", "semester": "2nd semester", "stream": None}
result_c = route_and_validate("Can I graduate on time?", profile_4th_year)
print(f"Routed To: {result_c['route']}")
print(f"Missing Fields: {result_c['missing_data']}")
print(f"AI Prompt: {result_c['response']}\n")