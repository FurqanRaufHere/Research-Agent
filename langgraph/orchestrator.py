"""
langgraph/orchestrator.py

Orchestrator (LangGraph-style) for Research Agent.
Talks to the FastAPI MCP endpoints and drives the flow:
1) create topic (POST /mcp/topic)
2) plan subtopics (POST /mcp/plan) -> parse into list
3) for each subtopic:
    - create subtopic entry (POST /mcp/subtopic/create)
    - check existing notes (GET /mcp/notes?subtopic_id=)
    - ask LLM decision (POST /mcp/need_search)
    - if YES -> call /mcp/search, then /mcp/extract for top results,
                 then /mcp/save_note for each extracted note
4) after all subtopics processed -> gather notes and call /mcp/synthesize

Run:
    python langgraph/orchestrator.py --topic "quantum batteries" --max_results 3

"""

import os
import re
import json
import asyncio
import logging
from typing import List, Dict, Any
import httpx
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000").rstrip("/")
MCP_TOKEN = os.getenv("MCP_ACCESS_TOKEN", None)
MIN_NOTES_FOR_SKIP = int(os.getenv("MIN_NOTES_FOR_SKIP", "2"))
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "5"))
REQUEST_TIMEOUT = int(os.getenv("AGENT_REQUEST_TIMEOUT", "20"))

# configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("orchestrator")

HEADERS = {}
if MCP_TOKEN:
    HEADERS["x-mcp-token"] = MCP_TOKEN

# helper: parse planner output -> list of subtopic strings
def parse_subtopics(planner_text: str) -> List[str]:
    """
    Accepts typical outputs:
    - "1. Subtopic A\n2. Subtopic B\n..."
    - "- Subtopic A\n- Subtopic B"
    - "Subtopic A; Subtopic B; ..."
    Returns clean list of subtopic titles.
    """
    lines = planner_text.strip().splitlines()
    items = []
    if len(lines) == 1 and ("," in lines[0] or ";" in lines[0]):
        # single-line list separated by commas or semicolons
        parts = re.split(r'[;,]', lines[0])
        items = [p.strip() for p in parts if p.strip()]
    else:
        for ln in lines:
            # strip leading numbering or bullets
            ln = ln.strip()
            m = re.match(r'^\s*(?:\d+[\.\)]\s*|[-*]\s*)?(.*)$', ln)
            if m:
                val = m.group(1).strip()
                if val:
                    items.append(val)
    # dedupe while preserving order
    seen = set()
    out = []
    for it in items:
        if it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
    return out

# MCP helpers
async def mcp_post(client: httpx.AsyncClient, path: str, payload: dict) -> dict:
    url = f"{FASTAPI_URL}{path}"
    try:
        r = await client.post(url, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        logger.info(f"MCP POST {path} OK, payload keys: {list(payload.keys())}")
        return data
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error calling {path}: {e.response.status_code} {e.response.text}")
        raise
    except Exception as e:
        logger.exception(f"Error calling {path}: {e}")
        raise

async def mcp_get(client: httpx.AsyncClient, path: str, params: dict = None) -> dict:
    url = f"{FASTAPI_URL}{path}"
    try:
        r = await client.get(url, params=params or {}, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        logger.info(f"MCP GET {path} OK, params: {params}")
        return data
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error calling {path}: {e.response.status_code} {e.response.text}")
        raise
    except Exception as e:
        logger.exception(f"Error calling {path}: {e}")
        raise

# main orchestration
async def run_agent(topic: str, max_results: int = None, num_subtopics_hint: int = None) -> Dict[str, Any]:
    max_results = max_results or MAX_SEARCH_RESULTS
    async with httpx.AsyncClient() as client:
        # 1) create topic in DB
        try:
            resp_topic = await mcp_post(client, "/mcp/topic", {"title": topic})
            topic_id = resp_topic.get("topic_id") or resp_topic.get("id")
            logger.info(f"Created topic id={topic_id}")
        except Exception as e:
            logger.error("Failed to create topic; attempting to continue without DB topic")
            topic_id = None

        # 2) planner -> /mcp/plan
        try:
            planner_resp = await mcp_post(client, "/mcp/plan", {"text": topic})
            planner_text = planner_resp.get("plan") or planner_resp.get("subtopics") or planner_resp
            if isinstance(planner_text, dict):
                planner_text = json.dumps(planner_text)
            logger.info("Planner response received")
        except Exception as e:
            logger.exception("Planner failed; aborting")
            raise

        subtopics = parse_subtopics(planner_text if isinstance(planner_text, str) else str(planner_text))
        if not subtopics:
            raise RuntimeError("Planner returned no subtopics. Aborting.")
        logger.info(f"Planner produced {len(subtopics)} subtopics: {subtopics}")

        results_by_subtopic = {}

        # iterate subtopics
        for stitle in subtopics:
            logger.info(f"Processing subtopic: {stitle}")
            # create subtopic record
            sub_id = None
            if topic_id is not None:
                try:
                    sub_resp = await mcp_post(client, "/mcp/subtopic/create", {"topic_id": topic_id, "title": stitle})
                    sub_id = sub_resp.get("id")
                    logger.info(f"Created subtopic id={sub_id}")
                except Exception as e:
                    logger.warning(f"Could not create subtopic in DB: {e}; continuing with no sub_id")

            # check existing notes
            if sub_id:
                notes_resp = await mcp_get(client, "/mcp/notes", params={"subtopic_id": sub_id})
                notes = notes_resp.get("notes", [])
            else:
                notes = []
            logger.info(f"Existing notes count for subtopic '{stitle}': {len(notes)}")

            need_search_decision = None
            # if we have enough notes, skip search
            if len(notes) >= MIN_NOTES_FOR_SKIP:
                need_search_decision = {"decision": "NO", "reason": f"has {len(notes)} notes >= threshold"}
                logger.info(f"Decision: skip search ({need_search_decision['reason']})")
            else:
                # ask LLM decision endpoint
                try:
                    dec = await mcp_post(client, "/mcp/need_search", {"text": stitle})
                    # LLM adapter returns {"need_search":"yes"} or similar. Normalize.
                    if isinstance(dec, dict):
                        val = dec.get("need_search") or dec.get("decision") or dec.get("answer") or dec.get("result")
                        if val is None:
                            # sometimes the LLM returns plain text
                            # try to read top-level keys or the whole response
                            val = str(dec)
                    else:
                        val = str(dec)
                    val = str(val).strip().lower()
                    decision = "yes" if "yes" in val else "no"
                    need_search_decision = {"decision": decision.upper(), "reason": f"llm decision: {val}"}
                    logger.info(f"LLM decision: {need_search_decision}")
                except Exception as e:
                    logger.warning(f"LLM decision failed, defaulting to YES: {e}")
                    need_search_decision = {"decision": "YES", "reason": "llm failure fallback"}

            results_by_subtopic[stitle] = {"subtopic_id": sub_id, "notes_before": notes, "mcp_events": []}

            if need_search_decision["decision"] == "NO":
                # just collect summaries we have
                results_by_subtopic[stitle]["notes_after"] = notes
                continue

            # perform search via /mcp/search
            query = f"{topic} {stitle}".strip()
            try:
                search_resp = await mcp_post(client, "/mcp/search", {"query": query, "max_results": max_results})
                search_results = search_resp.get("results", [])
                logger.info(f"Search returned {len(search_results)} results")
            except Exception as e:
                logger.error(f"Search failed for query '{query}': {e}")
                search_results = []

            results_by_subtopic[stitle]["search_results"] = search_results

            # extract top N results concurrently (bounded)
            async def extract_and_save(result):
                url = result.get("url") or result.get("link") or result.get("source")
                # call /mcp/extract
                try:
                    ext = await mcp_post(client, "/mcp/extract", {"url": url, "subtopic_id": sub_id})
                    content = ext.get("content") or ext.get("text") or ""
                    summary = ext.get("summary") or ext.get("extracted_summary") or content[:400]
                    source_title = ext.get("source_title") or result.get("title")
                except Exception as e:
                    logger.warning(f"Extraction failed for {url}: {e}")
                    return {"url": url, "error": str(e)}

                # save note
                try:
                    save_resp = await mcp_post(client, "/mcp/save_note", {
                        "subtopic_id": sub_id,
                        "source_title": source_title,
                        "source_url": url,
                        "content": content,
                        "extracted_summary": summary
                    })
                    return {"url": url, "saved_note": save_resp}
                except Exception as e:
                    logger.warning(f"Save note failed for {url}: {e}")
                    return {"url": url, "error": str(e)}

            # run extraction sequentially or in limited concurrency
            saved_results = []
            for res in search_results[:max_results]:
                saved = await extract_and_save(res)
                saved_results.append(saved)

            # after extraction, read notes again
            if sub_id:
                notes_after_resp = await mcp_get(client, "/mcp/notes", params={"subtopic_id": sub_id})
                notes_after = notes_after_resp.get("notes", [])
            else:
                notes_after = []

            results_by_subtopic[stitle]["notes_after"] = notes_after
            results_by_subtopic[stitle]["saved_results"] = saved_results

        # After loop: synthesize big report
        # Gather notes structured by subtopic
        compiled_notes = {}
        for st, data in results_by_subtopic.items():
            compiled_notes[st] = [n.get("extracted_summary") or n.get("summary") or n.get("content", "")[:400] for n in data.get("notes_after", [])]

        # try to call /mcp/synthesize
        try:
            synth_payload = {"topic": topic, "notes": compiled_notes}
            synth_resp = await mcp_post(client, "/mcp/synthesize", synth_payload)
            report = synth_resp.get("report") or synth_resp
            logger.info("Synthesis completed via /mcp/synthesize")
        except Exception as e:
            logger.warning(f"/mcp/synthesize failed: {e}; falling back to local aggregation")
            # fallback: simple aggregated text
            report = {
                "topic": topic,
                "generated_at": datetime.utcnow().isoformat(),
                "compiled_notes": compiled_notes
            }

        return {"topic_id": topic_id, "topic": topic, "subtopics": subtopics, "results": results_by_subtopic, "report": report}

# CLI entrypoint
if __name__ == "__main__":
    import argparse, asyncio, pprint
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", "-t", required=True, help="Research topic")
    parser.add_argument("--max_results", "-m", type=int, default=3, help="Max search results per subtopic")
    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    out = loop.run_until_complete(run_agent(args.topic, max_results=args.max_results))
    pprint.pprint(out)
