# llm_client.py
import os
import requests

# Toggle this variable: "ollama" for local testing, "groq" for production cloud api
LLM_PROVIDER = "ollama" 

# Configuration settings
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b" # lighter ollama model

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "your_groq_api_key_here")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

def generate_response(prompt: str) -> str:
    """
    Unified LLM calling wrapper to seamlessly switch between local dev (Ollama)
    and production deployment (Groq) without modifying downstream code.
    """
    if LLM_PROVIDER == "ollama":
        try:
            payload = {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            }
            response = requests.post(OLLAMA_URL, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            return f"⚠️ Local LLM Engine Error (Ollama): {e}"
            
    elif LLM_PROVIDER == "groq":
        try:
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2 # Lower temperature reduces hallucinations
            }
            response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=20)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"⚠️ Cloud LLM Engine Error (Groq): {e}"
            
    return "⚠️ Configuration Error: Invalid LLM_PROVIDER specified."