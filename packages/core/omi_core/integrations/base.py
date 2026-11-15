"""Interface commune des connecteurs d'integrations et registre."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote, urlencode

from omi_shared.rest_models import IntegrationInfo

from ..memory.schema import MemoryDatabase

GOOGLE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/contacts.readonly",
)

SPOTIFY_OAUTH_SCOPES = (
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
)

MICROSOFT_OAUTH_SCOPES = (
    "offline_access",
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Calendars.ReadWrite",
)


def default_oauth_redirect_uri(provider: str) -> str:
    host = os.environ.get("OMI_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = os.environ.get("OMI_PORT", "8765").strip() or "8765"
    return f"http://{host}:{port}/api/oauth/{provider}/callback"


def build_google_playground_url() -> str:
    apis = quote(",".join(GOOGLE_OAUTH_SCOPES), safe="")
    return f"https://developers.google.com/oauthplayground/#step1&apisSelect={apis}"


def build_google_oauth_authorize_url(
    client_id: str,
    redirect_uri: str,
    state: str | None = None,
) -> str:
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    if state:
        params["state"] = state
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def build_spotify_oauth_authorize_url(
    client_id: str,
    redirect_uri: str,
    state: str | None = None,
) -> str:
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SPOTIFY_OAUTH_SCOPES),
    }
    if state:
        params["state"] = state
    return f"https://accounts.spotify.com/authorize?{urlencode(params)}"


def build_microsoft_oauth_authorize_url(
    client_id: str,
    redirect_uri: str,
    state: str | None = None,
) -> str:
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(MICROSOFT_OAUTH_SCOPES),
        "response_mode": "query",
    }
    if state:
        params["state"] = state
    return f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?{urlencode(params)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BaseIntegration(ABC):
    id: str = ""
    name: str = ""
    priority: Literal[1, 2, 3] = 2
    description: str = ""
    auth_type: Literal["oauth", "api_key", "url", "csv"] = "api_key"
    oauth_provider: str | None = None

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.connected = False
        self.last_sync: str | None = None
        self._pending_events: list[dict[str, Any]] = []

    def toggle(self, enabled: bool) -> None:
        self.enabled = enabled

    def mark_synced(self) -> None:
        self.last_sync = _now_iso()

    def get_authorize_url(self) -> str | None:
        return None

    def is_oauth_client_configured(self) -> bool:
        return False

    def info(self) -> IntegrationInfo:
        return IntegrationInfo(
            id=self.id,
            name=self.name,
            priority=self.priority,
            enabled=self.enabled,
            connected=self.connected,
            last_sync=self.last_sync,
            description=self.description,
            tools=self.tools(),
            auth_type=self.auth_type,
            oauth_provider=self.oauth_provider,
            authorize_url=self.get_authorize_url(),
            oauth_client_configured=self.is_oauth_client_configured(),
        )

    @abstractmethod
    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        """Synchronise les donnees externes vers la memoire et enregistre les evenements."""

    async def events(self) -> list[dict[str, Any]]:
        """Retourne et vide la file des evenements collectes pour le bus."""
        items = list(self._pending_events)
        self._pending_events.clear()
        return items

    @abstractmethod
    def tools(self) -> list[str]:
        """Liste des noms d'outils exposes par ce connecteur."""


class GoogleOAuthIntegration(BaseIntegration):
    """Base partagee pour les connecteurs Google utilisant OAuth 2.0."""

    auth_type: Literal["oauth", "api_key", "url", "csv"] = "oauth"
    oauth_provider: str | None = "google"

    def is_oauth_client_configured(self) -> bool:
        return bool(
            os.environ.get("GOOGLE_CLIENT_ID", "").strip()
            and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        )

    def get_authorize_url(self) -> str | None:
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        if client_id:
            return build_google_oauth_authorize_url(
                client_id, default_oauth_redirect_uri("google")
            )
        return build_google_playground_url()


class MicrosoftOAuthIntegration(BaseIntegration):
    """Base partagee pour les connecteurs Microsoft 365 / Outlook utilisant OAuth 2.0."""

    auth_type: Literal["oauth", "api_key", "url", "csv"] = "oauth"
    oauth_provider: str | None = "microsoft"

    def is_oauth_client_configured(self) -> bool:
        return bool(
            os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
            and os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
        )

    def get_authorize_url(self) -> str | None:
        client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
        if client_id:
            return build_microsoft_oauth_authorize_url(
                client_id, default_oauth_redirect_uri("microsoft")
            )
        return None


class IntegrationRegistry:
    def __init__(self, connectors: list[BaseIntegration] | None = None) -> None:
        self._connectors: dict[str, BaseIntegration] = {}
        for item in connectors or []:
            self.register(item)

    def register(self, connector: BaseIntegration) -> None:
        self._connectors[connector.id] = connector

    def list_info(self) -> list[IntegrationInfo]:
        return [c.info() for c in self._connectors.values()]

    def get(self, integration_id: str) -> BaseIntegration | None:
        return self._connectors.get(integration_id)

    def toggle(self, integration_id: str, enabled: bool | None = None) -> IntegrationInfo | None:
        item = self._connectors.get(integration_id)
        if not item:
            return None
        new_state = (not item.enabled) if enabled is None else enabled
        item.toggle(new_state)
        return item.info()

    async def sync_enabled(self, db: MemoryDatabase) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for item in self._connectors.values():
            if item.enabled:
                summary[item.id] = await item.sync(db)
        return summary
