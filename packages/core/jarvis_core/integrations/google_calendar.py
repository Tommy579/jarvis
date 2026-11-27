"""Connecteur Google Calendar (OAuth lecture et creation d'evenements)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ..memory.schema import MemoryDatabase
from .base import GoogleOAuthIntegration


class GoogleCalendarIntegration(GoogleOAuthIntegration):
    id = "google_calendar"
    name = "Google Calendar"
    priority = 1
    description = "Lecture et creation d'evenements, rappels de rendez-vous et preparation."

    def __init__(self, access_token: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self._from_env_token = not bool(access_token)
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
        self.mark_synced()
        return {"ok": True, "synced_events": len(events)}

    async def list_upcoming_events(self, max_results: int = 10) -> list[dict[str, Any]]:
        if not self.access_token:
            return []
        now_iso = datetime.now(timezone.utc).isoformat()
        resp = await self._oauth_request(
            "GET",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params={
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": now_iso,
            },
        )
        return resp.json().get("items", [])

    async def create_event(self, summary: str, start_iso: str, end_iso: str) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Google Calendar manquant."}
        resp = await self._oauth_request(
            "POST",
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            json={
                "summary": summary,
                "start": {"dateTime": start_iso},
                "end": {"dateTime": end_iso},
            },
        )
        return {"ok": True, "event": resp.json()}
