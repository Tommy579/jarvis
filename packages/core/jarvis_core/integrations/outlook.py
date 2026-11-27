"""Connecteurs Microsoft 365 / Outlook (Mail et Calendar via Microsoft Graph OAuth 2.0)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ..memory.schema import MemoryDatabase
from ..security import wrap_untrusted_content
from .base import MicrosoftOAuthIntegration
from .gmail import filter_important_messages


class OutlookMailIntegration(MicrosoftOAuthIntegration):
    id = "outlook_mail"
    name = "Outlook Mail"
    priority = 1
    description = "Lecture filtree des courriels Microsoft 365 / Outlook et creation de brouillons."

    def __init__(self, access_token: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self._from_env_token = not bool(access_token)
        self.access_token = access_token or os.environ.get("MICROSOFT_OAUTH_ACCESS_TOKEN", "")
        self.connected = bool(self.access_token)

    def tools(self) -> list[str]:
        return ["mail_search", "mail_draft", "mail_send"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Outlook Mail manquant."}
        raw = await self.list_messages(max_results=20)
        normalized = [
            {
                "id": msg.get("id"),
                "from": (msg.get("from", {}).get("emailAddress", {}).get("address", "")),
                "subject": msg.get("subject", ""),
                "snippet": msg.get("bodyPreview", ""),
            }
            for msg in raw
        ]
        filtered = filter_important_messages(normalized)
        for msg in filtered:
            payload = {
                "id": msg.get("id"),
                "from": msg.get("from", ""),
                "subject": msg.get("subject", ""),
                "snippet": wrap_untrusted_content("outlook", str(msg.get("snippet", ""))),
            }
            self._pending_events.append(
                {"source": self.id, "type": "outlook.message", "payload": payload}
            )
            db.add_event(self.id, "outlook.message", payload, urgency=1)
        self.mark_synced()
        return {"ok": True, "fetched": len(raw), "kept": len(filtered)}

    async def list_messages(
        self, max_results: int = 10, query: str | None = None
    ) -> list[dict[str, Any]]:
        if not self.access_token:
            return []
        cleaned_query = (query or "").strip()
        params: dict[str, Any] = {
            "$top": max_results,
            "$select": "id,subject,from,bodyPreview,receivedDateTime",
        }
        headers: dict[str, str] = {}
        if cleaned_query and cleaned_query.lower() != "is:unread":
            escaped = cleaned_query.replace('"', '\\"')
            params["$search"] = f'"{escaped}"'
            headers["ConsistencyLevel"] = "eventual"
        else:
            params["$filter"] = "isRead eq false"
        resp = await self._oauth_request(
            "GET",
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            params=params,
            headers=headers or None,
        )
        return resp.json().get("value", [])

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Outlook Mail manquant."}
        resp = await self._oauth_request(
            "POST",
            "https://graph.microsoft.com/v1.0/me/messages",
            json={
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
        )
        return {"ok": True, "draft": resp.json()}

    async def send_message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Outlook Mail manquant."}
        resp = await self._oauth_request(
            "POST",
            "https://graph.microsoft.com/v1.0/me/sendMail",
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                }
            },
        )
        return {"ok": True, "status": resp.status_code}


class OutlookCalendarIntegration(MicrosoftOAuthIntegration):
    id = "outlook_calendar"
    name = "Outlook Calendar"
    priority = 1
    description = "Lecture et creation d'evenements Microsoft 365 / Outlook."

    def __init__(self, access_token: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self._from_env_token = not bool(access_token)
        self.access_token = access_token or os.environ.get("MICROSOFT_OAUTH_ACCESS_TOKEN", "")
        self.connected = bool(self.access_token)

    def tools(self) -> list[str]:
        return ["calendar_list_events", "calendar_create_event"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Outlook Calendar manquant."}

        events = await self.list_upcoming_events(max_results=10)
        for ev in events:
            summary = ev.get("subject", "Evenement sans titre")
            start = ev.get("start", {}).get("dateTime", "")
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
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = await self._oauth_request(
            "GET",
            "https://graph.microsoft.com/v1.0/me/events",
            params={
                "$top": max_results,
                "$filter": f"start/dateTime ge '{now_iso}'",
                "$orderby": "start/dateTime asc",
                "$select": "id,subject,start,end",
            },
        )
        return resp.json().get("value", [])

    async def create_event(
        self, summary: str, start_iso: str, end_iso: str, time_zone: str = "UTC"
    ) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Outlook Calendar manquant."}
        resp = await self._oauth_request(
            "POST",
            "https://graph.microsoft.com/v1.0/me/events",
            json={
                "subject": summary,
                "start": {"dateTime": start_iso, "timeZone": time_zone},
                "end": {"dateTime": end_iso, "timeZone": time_zone},
            },
        )
        return {"ok": True, "event": resp.json()}
