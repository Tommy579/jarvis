"""Serveur MCP sortant exposant la memoire d'JARVIS en lecture seule aux agents externes (IDE, etc.)."""

from __future__ import annotations

from typing import Any


class JARVISMemoryMCPServer:
    """Serveur JSON-RPC 2.0 MCP exposant memory_search, wiki_read et context_summary."""

    def __init__(self, memory_engine: Any):
        self.memory_engine = memory_engine

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "memory_search",
                "description": "Recherche hybride dans les faits et episodes d'JARVIS.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "wiki_read",
                "description": "Lit une page du wiki Markdown d'JARVIS (ex: moi.md, projets/jarvis.md).",
                "inputSchema": {
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
            },
            {
                "name": "context_summary",
                "description": "Retourne un resume du contexte de travail courant.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def handle_jsonrpc(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "jarvis-memory-mcp", "version": "2.0.0"},
                    "capabilities": {"tools": {}},
                },
            }

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.list_tools()}}

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "memory_search":
                results = self.memory_engine.search(
                    query=str(args.get("query", "")),
                    limit=int(args.get("limit", 10)),
                    provider_trusted=False,
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": str(results)}]},
                }
            if name == "wiki_read":
                text = self.memory_engine.wiki.read_page(str(args.get("slug", "moi.md")))
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": text}]},
                }
            if name == "context_summary":
                moi = self.memory_engine.wiki.read_page("moi.md")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": moi}]},
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Outil MCP inconnu : {name}"},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Methode MCP inconnue : {method}"},
        }
