"""Connecteur Google Contacts (synchronisation de la table people et anniversaires)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..memory.schema import MemoryDatabase
from .base import GoogleOAuthIntegration


class GoogleContactsIntegration(GoogleOAuthIntegration):
    id = "google_contacts"
    name = "Google Contacts"
    priority = 2
    description = "Synchronisation de la table people et des dates d'anniversaire."

    def __init__(self, access_token: str | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.access_token = access_token or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "")
        self.connected = bool(self.access_token)

    def tools(self) -> list[str]:
        return ["contacts_sync", "contacts_lookup"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        if not self.access_token:
            return {"ok": False, "error": "Jeton OAuth Google Contacts manquant."}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://people.googleapis.com/v1/people/me/connections",
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"personFields": "names,emailAddresses,phoneNumbers,birthdays,relations"},
            )
            resp.raise_for_status()
            connections = resp.json().get("connections", [])

        synced = 0
        for item in connections:
            names = item.get("names") or []
            if not names:
                continue
            display_name = str(names[0].get("displayName", "")).strip()
            if not display_name:
                continue
            relations = item.get("relations") or []
            relation = str(relations[0].get("type", "contact")) if relations else "contact"
            birthdays = item.get("birthdays") or []
            bday = None
            if birthdays and isinstance(birthdays[0].get("date"), dict):
                d = birthdays[0]["date"]
                if d.get("month") and d.get("day"):
                    bday = f"{d.get('year', '0000')}-{int(d['month']):02d}-{int(d['day']):02d}"
            emails = item.get("emailAddresses") or []
            phones = item.get("phoneNumbers") or []
            contact_ref = (emails[0].get("value") if emails else None) or (
                phones[0].get("value") if phones else None
            )
            db.upsert_person(
                name=display_name, relation=relation, birthday=bday, contact_ref=contact_ref
            )
            synced += 1

        self.mark_synced()
        return {"ok": True, "synced_people": synced}
