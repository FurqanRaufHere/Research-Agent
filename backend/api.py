"""
FastAPI app exposing MCP endpoints (minimal, with mock search + SerpAPI adapter support).

To run locally:
    export DATABASE_URL="sqlite:///./data/research_agent.db"
    export SERPAPI_KEY="your_key_here"
    uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000

"""

import asyncio
from datetime import datetime
import uuid
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import json
from typing import List, Optional
import requests
from dotenv import load_dotenv

from backend.llm_adapter import GROQAdapter
from backend.search_adapter import SearchAdapter
from .db import init_db, create_topic, create_subtopic, get_cached_search, cache_search_results, save_note, get_notes_for_subtopic, log_mcp_event

# Import LangGraph (our new implementation)
from langgraph.research_graph import run_research_agent

# Import MCP Server
from backend.mcp_server import MCPServer

try:
    from langgraph import orchestrator as orchestrator_module
except ImportError:
    orchestrator_module = None

load_dotenv()
app = FastAPI()

llm = GROQAdapter()
searcher = SearchAdapter()
mcp_server = MCPServer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MCP_ACCESS_TOKEN = os.environ.get('MCP_ACCESS_TOKEN', None)
SERPAPI_KEY = os.environ.get('SERPAPI_KEY', None)
SEARCH_MODE = os.environ.get('SEARCH_MODE', 'mock')

class TopicIn(BaseModel):
    title: str

class SubtopicIn(BaseModel):
    topic_id: int
    title: str

class SearchIn(BaseModel):
    query: str
    max_results: Optional[int] = 5

class ExtractIn(BaseModel):
    url: Optional[str]
    text: Optional[str]
    subtopic_id: Optional[int]

class SaveNoteIn(BaseModel):
    subtopic_id: int
    source_title: Optional[str]
    source_url: Optional[str]
    content: str
    extracted_summary: Optional[str]


@app.on_event('startup')
def startup_event():
    init_db()


def require_token(req: Request):
    if MCP_ACCESS_TOKEN is None:
        return True
    header = req.headers.get('x-mcp-token')
    if header != MCP_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail='Invalid MCP token')
    return True


# Helper to run orchestrator in background
async def _run_orchestrator_background(topic: str, max_results: int, run_id: str):
    try:
        # call the async run_agent function
        if orchestrator_module is None:
            raise ImportError("langgraph.orchestrator module not available")
        out = await orchestrator_module.run_agent(topic, max_results=max_results)
        # optional: log result to DB for post-mortem
        log_mcp_event('/agent/run/result', json.dumps({"run_id": run_id, "result": out}), json.dumps({"status":"done"}))
    except Exception as e:
        log_mcp_event('/agent/run/result', json.dumps({"run_id": run_id, "error": str(e)}), json.dumps({"status":"failed"}))


@app.post("/agent/run")
async def agent_run(payload: dict, token_check: bool = Depends(require_token)):
    """
    Trigger orchestrator in background and return run id.
    payload example: {"topic": "quantum batteries", "max_results": 3}
    """
    topic = payload.get("topic")
    if not topic:
        raise HTTPException(status_code=400, detail="Missing 'topic' in payload")
    max_results = int(payload.get("max_results", 3))

    run_id = uuid.uuid4().hex
    started_at = datetime.utcnow().isoformat()

    # log start event
    log_mcp_event('/agent/run', json.dumps({"run_id": run_id, "topic": topic, "max_results": max_results}),
                  json.dumps({"status": "started", "started_at": started_at}))

    # schedule background execution (non-blocking)
    # create_task returns immediately; the orchestrator runs async in the event loop
    asyncio.create_task(_run_orchestrator_background(topic, max_results, run_id))

    return {"run_id": run_id, "status": "scheduled", "started_at": started_at}


@app.post("/research/langgraph")
async def research_langgraph(payload: dict, token_check: bool = Depends(require_token)):
    """
    NEW ENDPOINT: Run research using LangGraph + MCP server.
    This is the proper implementation with StateGraph nodes and edges.
    
    payload: {"topic": "...", "max_results": 3}
    
    Returns: Complete research state with subtopics, notes, and synthesized report
    """
    topic = payload.get("topic")
    if not topic:
        raise HTTPException(status_code=400, detail="Missing 'topic'")
    
    max_results = int(payload.get("max_results", 3))
    
    log_mcp_event('/research/langgraph', json.dumps({"topic": topic}), 
                  json.dumps({"status": "started"}))
    
    try:
        # Run the LangGraph research agent
        result = await run_research_agent(topic, max_results=max_results)
        
        log_mcp_event('/research/langgraph', json.dumps({"topic": topic}), 
                      json.dumps({"status": "completed"}))
        
        return {
            "success": True,
            "topic": result["topic"],
            "subtopics": result["subtopics"],
            "notes": result["notes"],
            "report": result["report"]
        }
    except Exception as e:
        log_mcp_event('/research/langgraph', json.dumps({"topic": topic, "error": str(e)}), 
                      json.dumps({"status": "failed"}))
        raise HTTPException(status_code=500, detail=f"Research failed: {str(e)}")


@app.post("/mcp/tools/list")
def list_mcp_tools(token_check: bool = Depends(require_token)):
    """
    List available MCP tools from the MCP server.
    Returns tool definitions for any MCP client.
    """
    tools = mcp_server._handle_list_tools()
    return {"tools": tools}


@app.post('/mcp/topic')
async def mcp_create_topic(payload: TopicIn, token_check: bool = Depends(require_token)):
    topic = create_topic(payload.title)
    resp = {"topic_id": topic.id, "title": topic.title, "subtopics": []}
    log_mcp_event('/mcp/topic', payload.json(), json.dumps(resp))
    return resp


@app.post('/mcp/subtopic/create')
async def mcp_create_subtopic(payload: SubtopicIn, token_check: bool = Depends(require_token)):
    sub = create_subtopic(payload.topic_id, payload.title)
    resp = {"id": sub.id, "topic_id": sub.topic_id, "title": sub.title}
    log_mcp_event('/mcp/subtopic/create', payload.json(), json.dumps(resp), topic_id=payload.topic_id)
    return resp


@app.post('/mcp/search')
async def mcp_search(payload: SearchIn, token_check: bool = Depends(require_token)):
    # Check cache first
    cached = get_cached_search(payload.query)
    if cached:
        resp = {"results": json.loads(cached.results_json)}
        log_mcp_event('/mcp/search', payload.json(), json.dumps(resp))
        return resp

    results = []
    if SEARCH_MODE == 'serpapi' and SERPAPI_KEY:
        # call SerpAPI (Google) - simple implementation
        params = {
            'engine': 'google',
            'q': payload.query,
            'num': payload.max_results,
            'api_key': SERPAPI_KEY,
        }
        r = requests.get('https://serpapi.com/search', params=params, timeout=15)
        data = r.json()
        organic = data.get('organic_results', [])
        for it in organic[:payload.max_results]:
            results.append({'title': it.get('title'), 'snippet': it.get('snippet'), 'url': it.get('link')})
    else:
        # Mock search: read seed docs from /data/seed_docs
        seed_dir = os.path.join(os.getcwd(), 'data', 'seed_docs')
        if os.path.exists(seed_dir):
            files = [f for f in os.listdir(seed_dir) if f.endswith('.txt') or f.endswith('.html')]
            for fn in files[:payload.max_results]:
                p = os.path.join(seed_dir, fn)
                with open(p, 'r', encoding='utf-8') as fh:
                    txt = fh.read()
                results.append({'title': fn, 'snippet': txt[:250], 'url': 'file://' + p})
        else:
            # fallback mock
            for i in range(payload.max_results):
                results.append({'title': f'Mock result {i+1} for {payload.query}', 'snippet': 'This is a mock snippet', 'url': f'https://example.com/{i+1}'})

    cache_search_results(payload.query, json.dumps(results))
    resp = {"results": results}
    log_mcp_event('/mcp/search', payload.json(), json.dumps(resp))
    return resp


@app.post('/mcp/extract')
async def mcp_extract(payload: ExtractIn, token_check: bool = Depends(require_token)):
    # Simple extractor: if text provided, summarize locally; if url starts with http, GET it; if file://, read file
    content = ''
    source_title = None
    source_url = payload.url
    if payload.text:
        content = payload.text
    elif payload.url and payload.url.startswith('file://'):
        p = payload.url[len('file://'):]
        with open(p, 'r', encoding='utf-8') as fh:
            content = fh.read()
        source_title = os.path.basename(p)
    elif payload.url and payload.url.startswith('http'):
        try:
            r = requests.get(payload.url, timeout=10)
            content = r.text[:20000]
            source_title = payload.url
        except Exception as e:
            raise HTTPException(status_code=400, detail=f'Error fetching URL: {e}')
    else:
        raise HTTPException(status_code=400, detail='No url or text provided')

    # naive summary: first 400 chars
    summary = content[:400]
    resp = {"source_title": source_title or source_url, "content": content, "summary": summary}
    log_mcp_event('/mcp/extract', json.dumps(payload.dict()), json.dumps({'summary': summary}), topic_id=payload.subtopic_id)
    return resp


@app.post('/mcp/save_note')
async def mcp_save_note(payload: SaveNoteIn, token_check: bool = Depends(require_token)):
    note = save_note(payload.subtopic_id, payload.source_title, payload.source_url, payload.content, payload.extracted_summary)
    resp = {"note_id": note.id}
    log_mcp_event('/mcp/save_note', payload.json(), json.dumps(resp), topic_id=payload.subtopic_id)
    return resp


@app.get('/mcp/notes')
async def mcp_get_notes(subtopic_id: int, token_check: bool = Depends(require_token)):
    notes = get_notes_for_subtopic(subtopic_id)
    out = []
    for n in notes:
        out.append({
            'id': n.id,
            'source_title': n.source_title,
            'source_url': n.source_url,
            'extracted_summary': n.extracted_summary,
            'created_at': n.created_at.isoformat()
        })
    resp = {"notes": out}
    log_mcp_event('/mcp/notes', json.dumps({'subtopic_id':subtopic_id}), json.dumps(resp), topic_id=subtopic_id)
    return resp



class Query(BaseModel):
    text: str

@app.get("/")
def root():
    return {"message": "Welcome to the Research Agent MCP API"}


@app.get("/health")
def health():
    return {"status": "ok"}


class Query(BaseModel):
    text: str


@app.post("/mcp/plan")
def plan_topic(q: Query):
    """Plan a research topic into subtopics"""
    plan = llm.plan(q.text)
    return {"plan": plan}


@app.post("/mcp/need_search")
def need_search(q: Query):
    """Decide if a subtopic needs web search"""
    ans = llm.need_search(q.text)
    return {"need_search": ans}


@app.post("/mcp/summarize")
def summarize_doc(body: dict):
    """Summarize extracted content for a subtopic"""
    content = body["content"]
    subtopic = body["subtopic"]
    summary = llm.summarize_document(content, subtopic)
    return {"summary": summary}


@app.post("/mcp/synthesize")
def synthesize(body: dict):
    """Synthesize all notes into a final research report"""
    topic = body["topic"]
    notes = body["notes"]
    final = llm.synthesize_report(topic, notes)
    return {"report": final}
# ---------- end of backend/api.py ----------