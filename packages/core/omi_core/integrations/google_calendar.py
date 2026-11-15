"""Connecteur Google Calendar (OAuth lecture et creation d'evenements)."""

from __future__ import annotations

import os
from typing import Any

import httpx
from omi_shared.rest_models import FactCreate

from ..memory.schema import MemoryDatabase
from .base import GoogleOAuthIntegration


class GoogleCalendarIntegration(GoogleOAuthIntegration):
    id = "google_calendar"
    name = "Google Calendar"
    priority = 1
    description = "Lecture et creation d'evenements, rappels de rendez-vous et preparation."

    def __init__(self, access_token: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.access_token = access_token or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "")
        self.connected = bool(self.access_token)

    def tools(self) -> list[str]:
        return ["calendar_list_events", "calendar_create_event"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Google Calendar manquant."}

        events = await self.list_upcoming_events(max_results=10)
        for ev in events:
            summary = ev.get("summary", "Evenement sans titre")
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
            self._pending_events.append(
                {
                    "source": self.id,
                    "type": "calendar.upcoming",
                    "payload": {"summary": summary, "start": start},
                }
            )
            db.add_event(
                self.id, "calendar.upcoming", {"summary": summary, "start": start}, urgency=1
            )
        if events:
            db.create_fact(
                FactCreate(
                    subject="Tom",
                    predicate="prochain_rendez_vous",
                    object=str(events[0].get("summary", "")),
                    text=f"Prochain rendez-vous agenda : {events[0].get('summary', '')}",
                    category="rythme",
                    source=self.id,
                )
            )
        self.mark_synced()
        return {"ok": True, "synced_events": len(events)}

    async def list_upcoming_events(self, max_results: int = 10) -> list[dict[str, Any]]:
        if not self.access_token:
            return []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"maxResults": max_results, "singleEvents": "true", "orderBy": "startTime"},
            )
            resp.raise_for_status()
            return resp.json().get("items", [])

    async def create_event(self, summary: str, start_iso: str, end_iso: str) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Google Calendar manquant."}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json={
                    "summary": summary,
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
                },
            )
            resp.raise_for_status()
            return {"ok": True, "event": resp.json()}
