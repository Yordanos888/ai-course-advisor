# test_llm.py
from llm_client import generate_response

print("🤖 Sending a test prompt to Ollama...")
test_prompt = "Reply with exactly three words: 'System is ready'."

response = generate_response(test_prompt)
print(f"\nResponse from local LLM:\n{response}")