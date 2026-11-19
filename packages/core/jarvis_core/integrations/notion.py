"""Connecteur Notion (client REST / MCP pour taches et notes)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..memory.schema import MemoryDatabase
from .base import BaseIntegration


class NotionIntegration(BaseIntegration):
    id = "notion"
    name = "Notion"
    priority = 2
    description = "Synchronisation des bases de taches et notes Notion via REST ou MCP."

    def __init__(self, api_key: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.api_key = api_key or os.environ.get("NOTION_API_KEY", "")
        self.connected = bool(self.api_key)

    def tools(self) -> list[str]:
        return ["notion_search", "notion_create_page"]

    def get_authorize_url(self) -> str | None:
        return "https://www.notion.so/my-integrations"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.api_key:
            return {"ok": False, "error": "Cle NOTION_API_KEY manquante."}
        results = await self.search_pages(query="")
        for item in results[:10]:
            db.add_event(
                self.id, "notion.page", {"id": item.get("id"), "url": item.get("url")}, urgency=0
            )
        self.mark_synced()
        return {"ok": True, "pages": len(results)}

    async def search_pages(self, query: str = "") -> list[dict[str, Any]]:
        if not self.api_key:
            return []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.notion.com/v1/search",
                headers=self._headers(),
                json={"query": query, "page_size": 10},
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
