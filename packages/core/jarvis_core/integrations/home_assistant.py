"""Connecteur Home Assistant (REST API : presence, capteurs, lumieres, annonces media_player)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..memory.schema import MemoryDatabase
from .base import BaseIntegration


class HomeAssistantIntegration(BaseIntegration):
    id = "home_assistant"
    name = "Home Assistant"
    priority = 2
    description = "Presence maison, capteurs, eclairage et annonces vocales media_player."

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        enabled: bool = False,
    ) -> None:
        super().__init__(enabled=enabled)
        self.base_url = (base_url or os.environ.get("HOME_ASSISTANT_URL", "")).rstrip("/")
        self.token = token or os.environ.get("HOME_ASSISTANT_TOKEN", "")
        self.connected = bool(self.base_url and self.token)

    def tools(self) -> list[str]:
        return ["home_get_states", "home_call_service", "home_announce"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not (self.base_url and self.token):
            return {"ok": False, "error": "URL ou jeton Home Assistant manquant."}

        states = await self.get_states()
        for st in states:
            entity_id = str(st.get("entity_id", ""))
            if entity_id.startswith("person.") or entity_id.startswith("binary_sensor."):
                db.add_event(
                    self.id,
                    "home.state",
                    {"entity_id": entity_id, "state": st.get("state")},
                    urgency=0,
                )
        self.mark_synced()
        return {"ok": True, "entities": len(states)}

    async def get_states(self) -> list[dict[str, Any]]:
        if not (self.base_url and self.token):
            return []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/api/states", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> dict[str, Any]:
        if not (self.base_url and self.token):
            return {"ok": False, "error": "Home Assistant non configure."}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self._headers(),
                json=data,
            )
            resp.raise_for_status()
            return {"ok": True, "result": resp.json()}

    async def announce(self, entity_id: str, message: str) -> dict[str, Any]:
        return await self.call_service(
            "tts",
            "speak",
            {"entity_id": entity_id, "message": message},
        )
