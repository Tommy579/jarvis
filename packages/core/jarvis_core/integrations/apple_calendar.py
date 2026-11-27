"""Connecteur Apple Calendar (iCloud / flux .ics ou webcal:// en lecture)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from ..memory.schema import MemoryDatabase
from ..security import redact_sensitive_text, validate_http_target_host
from .base import BaseIntegration


def _ics_sort_key(start_raw: str) -> str:
    digits = "".join(ch for ch in start_raw if ch.isdigit())
    return digits.ljust(14, "0") if digits else "99999999999999"


def parse_ics_events(ics_text: str, max_results: int = 10) -> list[dict[str, str]]:
    """Extrait, trie chronologiquement et conserve le TZID des VEVENT depuis un flux .ics."""
    unfolded = (
        ics_text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")
    )
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
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key_part, value = line.split(":", 1)
        parts = key_part.split(";")
        prop = parts[0].upper()
        if prop == "SUMMARY":
            current["summary"] = value.replace("\\,", ",").replace("\\;", ";")
        elif prop == "DTSTART":
            current["start"] = value
            for param in parts[1:]:
                if param.upper().startswith("TZID="):
                    current["tzid"] = param.split("=", 1)[1].strip()
    events.sort(key=lambda e: _ics_sort_key(e.get("start", "")))
    now_key = datetime.now(timezone.utc).strftime("%Y%m%d000000")
    upcoming = [e for e in events if _ics_sort_key(e.get("start", "")) >= now_key]
    selected = upcoming if upcoming else events
    return selected[:max_results]


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
        return ["calendar_list_events"]

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
        ok_host, reason = await asyncio.to_thread(validate_http_target_host, url)
        if not ok_host:
            raise ValueError(reason or "URL .ics interdite.")
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(redact_sensitive_text(str(exc))) from None
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
        self.mark_synced()
        return {"ok": True, "synced_events": len(events)}
