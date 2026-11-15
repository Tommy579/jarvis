"""Connecteur Apple Calendar (iCloud / flux .ics ou webcal:// en lecture)."""

from __future__ import annotations

import os
from typing import Any, Literal

import httpx
from omi_shared.rest_models import FactCreate

from ..memory.schema import MemoryDatabase
from .base import BaseIntegration


def parse_ics_events(ics_text: str, max_results: int = 10) -> list[dict[str, str]]:
    """Extrait les evenements VEVENT (summary, start) depuis un flux iCalendar (.ics)."""
    unfolded = ics_text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in unfolded.splitlines():
        line = raw_line.strip()
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None and (current.get("summary") or current.get("start")):
                events.append(
                    {
                        "summary": current.get("summary", "Evenement sans titre"),
                        "start": current.get("start", ""),
                    }
                )
                if len(events) >= max_results:
                    break
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key_part, value = line.split(":", 1)
        prop = key_part.split(";", 1)[0].upper()
        if prop == "SUMMARY":
            current["summary"] = value.replace("\\,", ",").replace("\\;", ";")
        elif prop == "DTSTART":
            current["start"] = value
    return events


class AppleCalendarIntegration(BaseIntegration):
    id = "apple_calendar"
    name = "Apple Calendar (iCloud / .ics)"
    priority = 1
    description = "Lecture des rendez-vous Apple iCloud ou calendrier .ics / webcal://."
    auth_type: Literal["oauth", "api_key", "url", "csv"] = "url"

    def __init__(self, ics_url: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.ics_url = (ics_url or os.environ.get("APPLE_CALENDAR_ICS_URL", "")).strip()
        self.connected = bool(self.ics_url)

    def tools(self) -> list[str]:
        return ["apple_calendar_list_events"]

    def get_authorize_url(self) -> str | None:
        return "https://www.icloud.com/calendar/"

    def _normalized_url(self) -> str:
        if self.ics_url.startswith("webcal://"):
            return "https://" + self.ics_url[len("webcal://") :]
        return self.ics_url

    async def list_upcoming_events(self, max_results: int = 10) -> list[dict[str, str]]:
        url = self._normalized_url()
        if not url:
            return []
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return parse_ics_events(resp.text, max_results=max_results)

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.ics_url:
            return {"ok": False, "error": "URL .ics Apple Calendar manquante."}

        events = await self.list_upcoming_events(max_results=10)
        for ev in events:
            summary = ev.get("summary", "Evenement sans titre")
            start = ev.get("start", "")
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
            first_summary = events[0]["summary"]
            db.create_fact(
                FactCreate(
                    subject="Tom",
                    predicate="prochain_rendez_vous",
                    object=first_summary,
                    text=f"Prochain rendez-vous Apple Calendar : {first_summary}",
                    category="rythme",
                    source=self.id,
                )
            )
        self.mark_synced()
        return {"ok": True, "synced_events": len(events)}
