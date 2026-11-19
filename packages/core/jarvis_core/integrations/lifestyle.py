"""Connecteurs Spotify, Sante (Health Connect / HA) et Finances (import CSV / DSP2 tres prive)."""

from __future__ import annotations

import csv
import io
import os
from typing import Any, Literal

from jarvis_shared.rest_models import FactCreate

from ..memory.schema import MemoryDatabase
from .base import (
    BaseIntegration,
    build_spotify_oauth_authorize_url,
    default_oauth_redirect_uri,
)


class SpotifyIntegration(BaseIntegration):
    id = "spotify"
    name = "Spotify"
    priority = 3
    description = "Preferences musicales et lecture via l'API Spotify."
    auth_type: Literal["oauth", "api_key", "url", "csv"] = "oauth"
    oauth_provider: str | None = "spotify"

    def __init__(self, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.access_token = os.environ.get("SPOTIFY_ACCESS_TOKEN", "")
        self.connected = bool(self.access_token or os.environ.get("SPOTIFY_CLIENT_ID"))

    def is_oauth_client_configured(self) -> bool:
        return bool(
            os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
            and os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
        )

    def get_authorize_url(self) -> str | None:
        client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        if client_id:
            return build_spotify_oauth_authorize_url(
                client_id, default_oauth_redirect_uri("spotify")
            )
        return "https://developer.spotify.com/dashboard"

    def tools(self) -> list[str]:
        return ["spotify_current_track", "spotify_play"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        self.mark_synced()
        return {"ok": True, "connected": self.connected}


class HealthIntegration(BaseIntegration):
    id = "health"
    name = "Sante (Health Connect)"
    priority = 3
    description = (
        "Sommeil, pas et activite via Health Connect / Home Assistant (donnees tres privees)."
    )

    def __init__(self, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.connected = bool(os.environ.get("HOME_ASSISTANT_URL"))

    def tools(self) -> list[str]:
        return ["health_summary"]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        self.mark_synced()
        return {"ok": True, "sensitivity": "très privé"}


class FinanceIntegration(BaseIntegration):
    id = "finance"
    name = "Finances (CSV / DSP2)"
    priority = 3
    description = "Import CSV bancaire ou agregateur DSP2 classe strictement en tres prive."
    auth_type: Literal["oauth", "api_key", "url", "csv"] = "csv"

    def __init__(self, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.connected = True

    def tools(self) -> list[str]:
        return ["finance_import_csv"]

    def import_csv_text(self, csv_text: str, db: MemoryDatabase) -> int:
        reader = csv.DictReader(io.StringIO(csv_text))
        count = 0
        for row in reader:
            label = (row.get("label") or row.get("libelle") or row.get("description") or "").strip()
            amount = (row.get("amount") or row.get("montant") or "").strip()
            date_str = (row.get("date") or "").strip()
            if not label or not amount:
                continue
            db.create_fact(
                FactCreate(
                    subject="Tom",
                    predicate="operation_bancaire",
                    object=f"{label} ({amount})",
                    text=f"[{date_str}] {label} : {amount}",
                    category="finances",
                    confidence=0.95,
                    source=self.id,
                    sensitivity="très privé",
                )
            )
            count += 1
        self.mark_synced()
        return count

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        self.mark_synced()
        return {"ok": True, "sensitivity": "très privé"}
