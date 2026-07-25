# llm_client.py
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# "groq" is now the primary path. "ollama" is kept available in case you ever
# want local dev again, but is no longer the default given the memory/speed issues.
LLM_PROVIDER = "groq"

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1:latest"


def generate_response(prompt: str, temperature: float = 0.3, system: str = None,
                       max_retries: int = 2) -> str:
    """
    Unified LLM calling wrapper.
    - `temperature`: caller-controlled (0 for deterministic routing, higher for natural synthesis).
    - `system`: optional structured system message.
    - `max_retries`: automatic retry with backoff specifically for Groq's 429
      (rate limit) responses -- worth having since the free tier's 30 RPM cap
      is realistic to hit during rapid testing loops.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    if LLM_PROVIDER == "groq":
        if not GROQ_API_KEY:
            return "⚠️ Configuration Error: GROQ_API_KEY not found. Check your .env file."

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
        }

        attempt = 0
        while attempt <= max_retries:
            try:
                response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=20)

                if response.status_code == 429:
                    # Rate limit hit -- Groq tells us how long to wait via this header.
                    retry_after = float(response.headers.get("Retry-After", 2))
                    if attempt < max_retries:
                        print(f"⏳ Groq rate limit hit, retrying in {retry_after:.1f}s "
                              f"(attempt {attempt + 1}/{max_retries})...")
                        time.sleep(retry_after)
                        attempt += 1
                        continue
                    return "⚠️ Groq Rate Limit: still limited after retries. Slow down request frequency or check your quota at console.groq.com."

                response.raise_for_status()
                return response.json()['choices'][0]['message']['content'].strip()

            except requests.exceptions.RequestException as e:
                return f"⚠️ Groq LLM Engine Error (HTTP): {e}"
            except (KeyError, IndexError, ValueError) as e:
                return f"⚠️ Groq LLM Engine Error (response shape): {e} | raw body: {response.text[:300]}"

        return "⚠️ Groq LLM Engine Error: exhausted retries."

    elif LLM_PROVIDER == "ollama":
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
            "keep_alive": "10m",
        }
        try:
            response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=120)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            return f"⚠️ Local LLM Engine Error (Ollama HTTP): {e}"
        try:
            return response.json()["message"]["content"].strip()
        except (KeyError, ValueError) as e:
            return f"⚠️ Local LLM Engine Error (Ollama response shape): {e} | raw body: {response.text[:300]}"

    return "⚠️ Configuration Error: Invalid LLM_PROVIDER specified."