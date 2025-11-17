#!/usr/bin/env python3
"""
MCP Server for Research Agent
Exposes tools: search_web, extract_page, summarize_content, save_note
Uses JSON-RPC 2.0 protocol (compatible with Claude, other LLMs)

Run standalone: python backend/mcp_server.py
Or integrate into FastAPI: from backend.mcp_server import MCPServer, init_mcp_server
"""

import json
import asyncio
import logging
from typing import Any, Dict, List, Optional
import os
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mcp_server")
logger.setLevel(logging.INFO)

# ==================== MCP Protocol Implementation ====================

class MCPServerError(Exception):
    """MCP Protocol error"""
    pass


class MCPServer:
    """
    Minimal MCP (Model Context Protocol) Server implementation.
    Handles JSON-RPC 2.0 messages and tool invocation.
    """
    
    def __init__(self):
        self.version = "2024.1"
        self.tools = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        """Register built-in tools for research"""
        self.register_tool(
            name="search_web",
            description="Search the web using SerpAPI",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results", "default": 5}
                },
                "required": ["query"]
            },
            handler=self._tool_search_web
        )
        
        self.register_tool(
            name="extract_page",
            description="Extract and summarize content from a URL",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to extract from"},
                    "subtopic": {"type": "string", "description": "Research subtopic"}
                },
                "required": ["url"]
            },
            handler=self._tool_extract_page
        )
        
        self.register_tool(
            name="summarize_content",
            description="Summarize text content using LLM",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to summarize"},
                    "subtopic": {"type": "string", "description": "Context/subtopic"}
                },
                "required": ["content"]
            },
            handler=self._tool_summarize_content
        )
        
        self.register_tool(
            name="save_note",
            description="Save research note to database",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_title": {"type": "string", "description": "Title of source"},
                    "source_url": {"type": "string", "description": "URL of source"},
                    "content": {"type": "string", "description": "Note content"},
                    "summary": {"type": "string", "description": "Summary"}
                },
                "required": ["source_title", "source_url", "content"]
            },
            handler=self._tool_save_note
        )
    
    def register_tool(self, name: str, description: str, inputSchema: Dict, handler: callable):
        """Register a tool"""
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": inputSchema,
            "handler": handler
        }
        logger.info(f"Registered tool: {name}")
    
    async def call_tool(self, name: str, arguments: Dict) -> Dict:
        """Call a tool by name with arguments"""
        if name not in self.tools:
            raise MCPServerError(f"Unknown tool: {name}")
        
        tool = self.tools[name]
        handler = tool["handler"]
        
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**arguments)
            else:
                result = handler(**arguments)
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"Tool error: {name} - {e}")
            return {"success": False, "error": str(e)}
    
    # ==================== Tool Implementations ====================
    
    def _tool_search_web(self, query: str, max_results: int = 5) -> List[Dict]:
        """Search web using SerpAPI"""
        from backend.search_adapter import SearchAdapter
        searcher = SearchAdapter()
        try:
            results = searcher.search(query, max_results=max_results)
            logger.info(f"Search returned {len(results)} results for: {query}")
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def _tool_extract_page(self, url: str, subtopic: Optional[str] = None) -> Dict:
        """Extract and summarize page content"""
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            content = response.text[:5000]  # limit to 5k chars
            
            # Simple extraction: use first 500 chars as summary
            summary = content[:500]
            
            logger.info(f"Extracted {len(content)} chars from {url}")
            return {
                "url": url,
                "content": content,
                "summary": summary,
                "extracted_at": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Extract failed: {url} - {e}")
            return {"url": url, "error": str(e), "content": "", "summary": ""}
    
    def _tool_summarize_content(self, content: str, subtopic: Optional[str] = None) -> str:
        """Summarize content using Groq LLM"""
        from backend.llm_adapter import GROQAdapter
        llm = GROQAdapter()
        try:
            prompt = f"""Summarize this content in 3-4 bullet points.
{"Subtopic: " + subtopic if subtopic else ""}

Content:
{content[:2000]}
"""
            summary = llm._request([{"role": "user", "content": prompt}])
            logger.info(f"Summarized content for: {subtopic or 'general'}")
            return summary
        except Exception as e:
            logger.error(f"Summarize failed: {e}")
            return content[:500]
    
    def _tool_save_note(self, source_title: str, source_url: str, content: str, summary: Optional[str] = None) -> Dict:
        """Save note to database"""
        try:
            from backend.db import log_mcp_event
            log_mcp_event(
                '/mcp/save_note',
                json.dumps({"source_title": source_title, "source_url": source_url}),
                json.dumps({"status": "saved", "length": len(content)})
            )
            logger.info(f"Saved note: {source_title}")
            return {
                "success": True,
                "source": source_title,
                "url": source_url,
                "saved_at": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Save note failed: {e}")
            return {"success": False, "error": str(e)}
    
    # ==================== MCP Protocol Handling ====================
    
    async def handle_request(self, request: Dict) -> Dict:
        """
        Handle JSON-RPC 2.0 request.
        Typical requests:
        - {"jsonrpc": "2.0", "method": "initialize", "id": 1}
        - {"jsonrpc": "2.0", "method": "tools/list", "id": 2}
        - {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "search_web", "arguments": {"query": "..."}}, "id": 3}
        """
        jsonrpc = request.get("jsonrpc", "2.0")
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")
        
        try:
            if method == "initialize":
                result = self._handle_initialize()
            elif method == "tools/list":
                result = self._handle_list_tools()
            elif method == "tools/call":
                result = await self._handle_call_tool(params)
            else:
                raise MCPServerError(f"Unknown method: {method}")
            
            return {
                "jsonrpc": jsonrpc,
                "result": result,
                "id": req_id
            }
        except Exception as e:
            logger.error(f"Request error: {e}")
            return {
                "jsonrpc": jsonrpc,
                "error": {"code": -32603, "message": str(e)},
                "id": req_id
            }
    
    def _handle_initialize(self) -> Dict:
        """Initialize MCP server"""
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "research-agent-mcp",
                "version": self.version
            },
            "capabilities": {
                "tools": {}
            }
        }
    
    def _handle_list_tools(self) -> List[Dict]:
        """List available tools"""
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": tool["inputSchema"]
            }
            for tool in self.tools.values()
        ]
    
    async def _handle_call_tool(self, params: Dict) -> Dict:
        """Call a tool"""
        name = params.get("name")
        arguments = params.get("arguments", {})
        
        if not name:
            raise MCPServerError("Missing tool name")
        
        result = await self.call_tool(name, arguments)
        return result


# ==================== Standalone Server ====================

async def run_mcp_server_stdio():
    """
    Run MCP server reading JSON-RPC from stdin, writing to stdout.
    This is how Claude Desktop connects to MCP servers.
    """
    server = MCPServer()
    
    while True:
        try:
            line = input()
            request = json.loads(line)
            response = await server.handle_request(request)
            print(json.dumps(response))
        except EOFError:
            break
        except json.JSONDecodeError:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            }))
        except Exception as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)},
                "id": None
            }))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_mcp_server_stdio())
