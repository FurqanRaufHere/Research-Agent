import os
import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "llama-3.1-70b-versatile")

class GROQAdapter:
    def __init__(self):
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY missing in .env")
        self.url = "https://api.groq.com/openai/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

    def _request(self, messages, temperature=0):
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": temperature
        }
        r = requests.post(self.url, json=payload, headers=self.headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    # ---------------------------
    # Agent functions
    # ---------------------------

    def plan(self, topic):
        prompt = f"""
You are a research planner. Break the topic into 4–7 subtopics.
Return ONLY a numbered list, each item short and crisp.
Topic: {topic}
"""
        return self._request([{"role": "user", "content": prompt}])

    def need_search(self, subtopic):
        prompt = f"""
Decide if this subtopic requires external search.

Respond with ONLY one word: "yes" or "no".
Subtopic: {subtopic}
"""
        return self._request([{"role": "user", "content": prompt}]).lower()

    def summarize_document(self, content, subtopic):
        prompt = f"""
You are analyzing a document for a research agent.

Subtopic: {subtopic}

Summarize the document in 4–6 tight bullet points.
Text:
{content}
"""
        return self._request([{"role": "user", "content": prompt}])

    def synthesize_report(self, topic, notes):
        prompt = f"""
You are a research synthesizer.

Topic: {topic}

Here are the notes collected for each subtopic:
{notes}

Write a clean, structured research summary with:
- Executive summary
- Subtopic sections
- Final insights

Do NOT hallucinate info; only use the notes.
"""
        return self._request([{"role": "user", "content": prompt}])
# ---------- end of backend/llm_adapter.py ----------