#!/usr/bin/env python3
"""
langgraph/graph.py

Proper LangGraph implementation for research agent orchestration.
- Uses StateGraph with nodes and edges
- Implements agentic reasoning flow
- Integrates with MCP server tools
"""

import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END as END_NODE
from langgraph.prebuilt import create_react_agent

import httpx
import os
from dotenv import load_dotenv

from backend.mcp_server import MCPServer
from backend.llm_adapter import GROQAdapter

load_dotenv()

logger = logging.getLogger("langgraph")
logger.setLevel(logging.INFO)

FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8000").rstrip("/")
MCP_TOKEN = os.getenv("MCP_ACCESS_TOKEN", None)

# ==================== State Definition ====================

class ResearchState(TypedDict):
    """Shared state across all nodes in the research workflow"""
    topic: str
    max_results: int
    subtopics: List[str]
    notes: Dict[str, List[Dict]]  # {subtopic: [notes]}
    report: Optional[str]
    current_subtopic: Optional[str]
    search_results: List[Dict]
    step: str


# ==================== Node Functions ====================

def plan_node(state: ResearchState) -> ResearchState:
    """
    Node 1: Plan - Break topic into subtopics using LLM
    Input: topic
    Output: subtopics list
    """
    logger.info(f"[PLAN] Planning topic: {state['topic']}")
    
    llm = GROQAdapter()
    prompt = f"""You are a research planner. Break this topic into 5-7 subtopics.
Return ONLY a numbered list, one per line. No explanations.

Topic: {state['topic']}"""
    
    plan_text = llm._request([{"role": "user", "content": prompt}])
    
    # Parse subtopicss
    subtopics = []
    for line in plan_text.strip().split('\n'):
        line = line.strip()
        # Remove numbering: "1. " or "1) " or "- "
        if line and any(c.isalpha() for c in line):
            import re
            match = re.match(r'^\s*(?:\d+[\.\)]\s*|[-*]\s*)?(.*)$', line)
            if match:
                subtopic = match.group(1).strip()
                if subtopic:
                    subtopics.append(subtopic)
    
    state["subtopics"] = subtopics[:7]  # limit to 7
    state["notes"] = {sub: [] for sub in state["subtopics"]}
    state["step"] = "plan"
    
    logger.info(f"[PLAN] Generated {len(state['subtopics'])} subtopics")
    return state


def search_node(state: ResearchState) -> ResearchState:
    """
    Node 2: Search - For each subtopic, search the web
    Loops through subtopics, calls search tool
    """
    logger.info(f"[SEARCH] Searching for subtopics")
    
    mcp_server = MCPServer()
    
    for subtopic in state["subtopics"]:
        logger.info(f"[SEARCH] Processing subtopic: {subtopic}")
        state["current_subtopic"] = subtopic
        
        # Call MCP tool: search_web
        query = f"{state['topic']} {subtopic}"
        
        try:
            # Run tool (non-async, sync mode)
            results = mcp_server._tool_search_web(query, max_results=state["max_results"])
            state["search_results"] = results
            logger.info(f"[SEARCH] Found {len(results)} results for {subtopic}")
            
            # Move to extract for these results
            for result in results[:state["max_results"]]:
                extracted = mcp_server._tool_extract_page(result.get("url") or result.get("link"), subtopic)
                if extracted.get("content"):
                    state["notes"][subtopic].append(extracted)
                    logger.info(f"[SEARCH] Extracted: {result.get('title')}")
        
        except Exception as e:
            logger.error(f"[SEARCH] Error for {subtopic}: {e}")
    
    state["step"] = "search"
    logger.info(f"[SEARCH] Completed search for all subtopics")
    return state


def synthesize_node(state: ResearchState) -> ResearchState:
    """
    Node 3: Synthesize - Compile all notes into final report
    """
    logger.info(f"[SYNTHESIZE] Compiling final report")
    
    llm = GROQAdapter()
    
    # Build notes summary
    notes_summary = {}
    for subtopic, notes in state["notes"].items():
        summaries = [note.get("summary", "") for note in notes]
        notes_summary[subtopic] = "\n".join(summaries)
    
    prompt = f"""You are a research synthesizer.

Topic: {state['topic']}

Subtopic Summaries:
{json.dumps(notes_summary, indent=2)}

Write a comprehensive research report with:
1. Executive summary
2. Key findings by subtopic
3. Final insights

Be concise and factual. Do NOT hallucinate."""
    
    report = llm._request([{"role": "user", "content": prompt}])
    state["report"] = report
    state["step"] = "synthesize"
    
    logger.info(f"[SYNTHESIZE] Report generated")
    return state


# ==================== Build Graph ====================

def build_research_graph() -> StateGraph:
    """Build and compile the research workflow graph"""
    
    graph = StateGraph(ResearchState)
    
    # Add nodes
    graph.add_node("plan", plan_node)
    graph.add_node("search", search_node)
    graph.add_node("synthesize", synthesize_node)
    
    # Add edges
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_edge("search", "synthesize")
    graph.add_edge("synthesize", END_NODE)
    
    # Compile
    compiled_graph = graph.compile()
    
    logger.info("LangGraph compiled successfully")
    return compiled_graph


# ==================== Public API ====================

async def run_research_agent(topic: str, max_results: int = 3) -> Dict:
    """
    Run the complete research workflow.
    
    Args:
        topic: Research topic
        max_results: Max search results per subtopic
    
    Returns:
        Final state with subtopics, notes, and report
    """
    logger.info(f"Starting research agent for: {topic}")
    
    # Initialize state
    initial_state: ResearchState = {
        "topic": topic,
        "max_results": max_results,
        "subtopics": [],
        "notes": {},
        "report": None,
        "current_subtopic": None,
        "search_results": [],
        "step": "init"
    }
    
    # Build and run graph
    graph = build_research_graph()
    
    # Run the graph
    final_state = graph.invoke(initial_state)
    
    logger.info(f"Research completed for: {topic}")
    
    return final_state


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    result = asyncio.run(run_research_agent("Quantum computing", max_results=2))
    print("\n=== FINAL RESULT ===")
    print(json.dumps({
        "topic": result["topic"],
        "subtopics": result["subtopics"],
        "notes_count": sum(len(n) for n in result["notes"].values()),
        "report": result["report"][:500] if result["report"] else None
    }, indent=2))
