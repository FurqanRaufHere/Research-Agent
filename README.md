# Research Agent

This repository contains an automated research agent that:

- Uses a LangGraph StateGraph (Plan → Search → Synthesize) for reasoning and orchestration.
- Exposes MCP-style tools via a FastAPI backend so LLM clients or the LangGraph orchestrator can call tools (search, extract, summarize, save).
- Includes a Streamlit frontend that calls the LangGraph orchestrator (`/research/langgraph`) and displays results.

This README.md contains quick setup, architecture, local demo, and deployment steps for Render (backend) and Streamlit Cloud (frontend).

---

## Table of contents

- About the project
- Requirements & environment
- Local setup & run
- API endpoints and demo commands
- Architecture diagram
- Deployment (Render backend + Streamlit frontend)
- Troubleshooting
- Contributing

---

## About

The research agent composes automated research runs:

- **Plan**: break a topic into subtopics using an LLM.
- **Search**: run web searches (SerpAPI) and collect candidate URLs.
- **Extract**: fetch page content and summarize/extract useful notes.
- **Synthesize**: call an LLM to produce a final report from the collected notes.

All orchestration logic is implemented with LangGraph nodes, and the tools are registered in a small MCP server so LLMs/agents can call them via JSON-RPC-style interfaces.

---

## Requirements & environment

Install Python 3.10+ (3.11/3.13 tested locally). Create a virtual environment and install dependencies.

Environment variables (set locally or in hosting platform):

- `SERPAPI_KEY` — SerpAPI key (optional if `SEARCH_MODE=mock`)
- `GROQ_API_KEY` — LLM provider key (if using Groq adapter)
- `MCP_ACCESS_TOKEN` — optional token required by backend endpoints
- `DATABASE_URL` — e.g. `sqlite:///./data/research_agent.db`
- `FASTAPI_URL` — (frontend) URL of the backend when deployed

Note: The code supports a `SEARCH_MODE` env var which can be set to `mock` for offline development.

---

## Local setup & run

1. Create and activate the virtual environment and install dependencies (see above).

2. Start the backend (development):

```powershell
$env:DATABASE_URL = "sqlite:///./data/research_agent.db"
uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000
```

3. Start the Streamlit frontend (in a separate terminal):

```powershell
streamlit run frontend/app.py
```

Open Streamlit UI in your browser (usually `http://localhost:8501`) and the backend docs at `http://127.0.0.1:8000/docs`.

---

## API endpoints & demo commands

Key endpoints (FastAPI):

- `POST /research/langgraph` — Run the full LangGraph orchestrator. Body example:

```json
{ "topic": "Artificial Intelligence in Healthcare", "max_results": 3 }
```

- `GET /mcp/tools/list` — List registered MCP tools.

Legacy MCP endpoints (kept for compatibility): `/mcp/plan`, `/mcp/search`, `/mcp/extract`, `/mcp/synthesize`.

Quick PowerShell test:

```powershell
$token = "<your_mcp_token_if_set>"
$headers = @{ 'x-mcp-token' = $token; 'Content-Type' = 'application/json' }
$body = @{ topic = 'Artificial Intelligence in Healthcare'; max_results = 2 } | ConvertTo-Json
Invoke-WebRequest -Uri "http://127.0.0.1:8000/research/langgraph" -Headers $headers -Method POST -Body $body -TimeoutSec 120
```

The response will include `subtopics`, `notes` (per subtopic), and `report`.

---

## Architecture diagram

High level ASCII diagram (simplified):

```
             +-----------------+          +------------------+
             |   Streamlit UI  | <------> |   FastAPI Backend|
             |  (frontend)     |   HTTP   |  /research/langgraph |
             +-----------------+          +------------------+
                                                  |
                                                  |  LangGraph orchestrator
                                                  v
                                           +------------------+
                                           |  LangGraph Flow  |
                                           |  (Plan -> Search | 
                                           |   -> Synthesize) |
                                           +------------------+
                                                   |
             +----------------+   MCP Tools   +-----+-------+-----+
             |   SerpAPI      | <-----------> | search_web()     |
             +----------------+                | extract_page()   |
                                               | summarize_content|
                                               | save_note()      |
                                               +------------------+
                                                      |
                                                      v
                                               +--------------+
                                               |   Database   |
                                               +--------------+
```

Notes:
- The LangGraph nodes call MCP tools to fetch/search/extract data. The MCP server wraps tool implementations and registers them so LLMs/agents can call them.

---

## Deployment

We recommend deploying the backend to Render (or similar) and the frontend to Streamlit Cloud.

### Backend (Render)

1. Push your repository to GitHub.
2. Create a new Web Service on Render and connect to your repo.
3. Use the start command (Render expects `$PORT`):

```
uvicorn backend.api:app --host 0.0.0.0 --port $PORT
```

4. Set environment variables on Render:

- `SERPAPI_KEY`, `GROQ_API_KEY`, `MCP_ACCESS_TOKEN`, `DATABASE_URL` (and any others your adapters need).

5. Deploy and watch logs. Verify `https://<your-render-url>/docs` loads.

### Frontend (Streamlit Cloud)

1. Create a Streamlit Cloud app and connect to the same GitHub repo.
2. Set the main script to `frontend/app.py`.
3. Add secrets in Streamlit Cloud -> Settings:

- `FASTAPI_URL` = `https://<your-render-url>` (public backend URL)
- `MCP_ACCESS_TOKEN`, `SERPAPI_KEY`, `GROQ_API_KEY` as needed

4. Deploy — Streamlit Cloud will install `requirements.txt` and launch the app.

---

## Troubleshooting

- 403 on `extract_page`: many publishers block automated fetches. Fixes:
  - Use a realistic `User-Agent` header in `extract_page()`.
  - Add retries and backoff.
  - Fall back to using the search result snippet if full fetch fails.

- Empty report: confirm that search returned results and that `extract_page()` produced non-empty notes. Check backend logs for `Extracted` vs `Extract failed` messages.

- Missing API keys: check `SERPAPI_KEY` and `GROQ_API_KEY` environment variables.

---

## Development notes

- The project is intentionally modular: adapters for search and LLMs sit in `backend/` (see `backend/search_adapter.py` and `backend/llm_adapter.py`).
- LangGraph orchestration is in `langgraph/research_graph.py` (renamed to avoid shadowing the `langgraph` package).

---

## Contributing

Contributions are welcome. Suggested workflow:

1. Fork the repo and create a branch for your feature.
2. Run tests / linter locally.
3. Open a PR with a clear description.

---