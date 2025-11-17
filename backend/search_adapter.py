import os
import requests
import hashlib
from dotenv import load_dotenv

load_dotenv()

SEARCH_MODE = os.getenv("SEARCH_MODE", "mock")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

class SearchAdapter:
    def __init__(self):
        if SEARCH_MODE == "serpapi" and not SERPAPI_KEY:
            raise ValueError("SERPAPI_KEY missing in .env")

    def _cache_key(self, query):
        return hashlib.sha256(query.lower().strip().encode()).hexdigest()

    # -----------------------
    # Mock search (for dev)
    # -----------------------
    def mock_search(self, query):
        return [{
            "title": f"Mock result for {query}",
            "link": "https://example.com",
            "snippet": f"This is placeholder text for {query}."
        }]

    # -----------------------
    # Real SerpAPI search
    # -----------------------
    def serpapi_search(self, query, max_results=5):
        url = "https://serpapi.com/search"
        params = {
            "engine": "google",
            "api_key": SERPAPI_KEY,
            "q": query
        }

        r = requests.get(url, params=params)
        r.raise_for_status()
        data = r.json()

        organic = data.get("organic_results", [])
        cleaned = []

        for item in organic[:max_results]:
            cleaned.append({
                "title": item.get("title"),
                "link": item.get("link"),
                "snippet": item.get("snippet")
            })

        return cleaned

    # -----------------------
    # Main entry point
    # -----------------------
    def search(self, query, max_results=5):
        if SEARCH_MODE == "mock":
            return self.mock_search(query)
        return self.serpapi_search(query, max_results)
# ---------- end of backend/search_adapter.py ----------