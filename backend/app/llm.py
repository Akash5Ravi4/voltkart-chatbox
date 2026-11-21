import os
from groq import Groq

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are a helpful assistant for VoltKart, an electronics retailer. "
    "Answer concisely."
)


def get_client() -> Groq:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
    return Groq(api_key=key)


def ask_llm(history: list[dict]) -> str:
    """history is a list like [{"role": "user", "content": "..."}, ...]"""
    client = get_client()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0,  # keep answers stable and repeatable
    )
    return resp.choices[0].message.content