"""Connecteur Gmail (lecture seule par defaut, filtrage local avant passage au LLM, brouillons)."""

from __future__ import annotations

import asyncio
import base64
import os
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

from ..memory.schema import MemoryDatabase
from ..security import wrap_untrusted_content
from .base import GoogleOAuthIntegration

NOISE_SENDERS = ("noreply@", "no-reply@", "newsletter@", "notifications@")


def filter_important_messages(
    messages: list[dict[str, Any]], vip_senders: set[str] | None = None
) -> list[dict[str, Any]]:
    """Filtre localement les courriels automatiques avant tout passage au LLM."""
    vips = {v.lower() for v in (vip_senders or set())}
    kept: list[dict[str, Any]] = []
    for msg in messages:
        sender = str(msg.get("from", "")).lower()
        labels = set(msg.get("labelIds", []))
        if any(noise in sender for noise in NOISE_SENDERS) and not any(v in sender for v in vips):
            continue
        if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_SOCIAL" in labels:
            continue
        kept.append(msg)
    return kept


class GmailIntegration(GoogleOAuthIntegration):
    id = "gmail"
    name = "Gmail"
    priority = 1
    description = "Lecture filtree localement avant analyse LLM et creation de brouillons."

    def __init__(self, access_token: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self._from_env_token = not bool(access_token)
        self.access_token = access_token or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "")
        self.connected = bool(self.access_token)

    def tools(self) -> list[str]:
        return ["mail_search", "mail_draft", "mail_send"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Gmail manquant."}
        raw = await self.list_messages(query="is:unread", max_results=20)
        sem = asyncio.Semaphore(5)

        async def _hydrate(item: dict[str, Any]) -> dict[str, Any]:
            if item.get("from") or item.get("subject") or not item.get("id"):
                return item
            async with sem:
                return await self.get_message(str(item["id"]))

        hydrated = list(await asyncio.gather(*(_hydrate(m) for m in raw))) if raw else []
        filtered = filter_important_messages(hydrated)
        for msg in filtered:
            payload = {
                "id": msg.get("id"),
                "from": msg.get("from", ""),
                "subject": msg.get("subject", ""),
                "snippet": wrap_untrusted_content("gmail", str(msg.get("snippet", ""))),
            }
            self._pending_events.append(
                {"source": self.id, "type": "gmail.message", "payload": payload}
            )
            db.add_event(self.id, "gmail.message", payload, urgency=1)
        self.mark_synced()
        return {"ok": True, "fetched": len(raw), "kept": len(filtered)}

    async def list_messages(
        self, query: str = "is:unread", max_results: int = 10
    ) -> list[dict[str, Any]]:
        if not self.access_token:
            return []
        resp = await self._oauth_request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": query, "maxResults": max_results},
        )
        return resp.json().get("messages", [])

    @staticmethod
    def _encode_mime(to: str, subject: str, body: str) -> str:
        mime = EmailMessage()
        mime["To"] = to
        mime["Subject"] = subject
        mime.set_content(body)
        return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

    async def get_message(self, message_id: str) -> dict[str, Any]:
        if not self.access_token:
            return {}
        safe_id = quote(str(message_id).strip(), safe="")
        resp = await self._oauth_request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{safe_id}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
        )
        data = resp.json()
        headers = {
            h["name"].lower(): h["value"]
            for h in data.get("payload", {}).get("headers", [])
            if "name" in h and "value" in h
        }
        return {
            "id": data.get("id", message_id),
            "labelIds": data.get("labelIds", []),
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": data.get("snippet", ""),
        }

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Gmail manquant."}
        encoded = self._encode_mime(to, subject, body)
        resp = await self._oauth_request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            json={"message": {"raw": encoded}},
        )
        return {"ok": True, "draft": resp.json()}

    async def send_message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Gmail manquant."}
        encoded = self._encode_mime(to, subject, body)
        resp = await self._oauth_request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": encoded},
        )
        return {"ok": True, "message": resp.json()}
