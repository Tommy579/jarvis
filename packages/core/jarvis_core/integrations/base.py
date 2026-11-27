"""Interface commune des connecteurs d'integrations et registre."""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx
from dotenv import set_key
from jarvis_shared.rest_models import IntegrationInfo

from ..memory.schema import MemoryDatabase

_OAUTH_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}


def _get_refresh_lock(provider: str) -> asyncio.Lock:
    lock = _OAUTH_REFRESH_LOCKS.get(provider)
    if lock is None:
        lock = asyncio.Lock()
        _OAUTH_REFRESH_LOCKS[provider] = lock
    return lock


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
    "Mail.Send",
    "Calendars.ReadWrite",
)


def default_oauth_redirect_uri(provider: str) -> str:
    host = os.environ.get("JARVIS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = os.environ.get("JARVIS_PORT", "8765").strip() or "8765"
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


_OAUTH_REFRESH_CONFIG: dict[str, tuple[str, str, str, str, str]] = {
    "google": (
        "https://oauth2.googleapis.com/token",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ),
    "microsoft": (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_OAUTH_ACCESS_TOKEN",
        "MICROSOFT_OAUTH_REFRESH_TOKEN",
    ),
    "spotify": (
        "https://accounts.spotify.com/api/token",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_ACCESS_TOKEN",
        "SPOTIFY_REFRESH_TOKEN",
    ),
}


def _persist_refreshed_env(key: str, value: str) -> None:
    os.environ[key] = value
    env_raw = (
        os.environ.get("JARVIS_ENV_PATH", "").strip() or os.environ.get("OMI_ENV_PATH", "").strip()
    )
    env_path = Path(env_raw) if env_raw else (Path.cwd() / ".env")
    if env_path.exists():
        try:
            set_key(str(env_path), key, value)
            if os.name != "nt":
                os.chmod(env_path, 0o600)
        except OSError:
            pass


_LAST_REFRESHED_TOKENS: dict[str, tuple[str, str]] = {}


async def refresh_oauth_access_token(
    provider: str, *, failed_token: str | None = None
) -> str | None:
    """Rafraichit un jeton OAuth 2.0 expire de facon serialisee (evite les courses critiques)."""
    cfg = _OAUTH_REFRESH_CONFIG.get(provider)
    if not cfg:
        return None
    token_url, id_env, secret_env, access_env, refresh_env = cfg
    async with _get_refresh_lock(provider):
        # Si une autre coroutine vient deja de renouveler exactement ce jeton expire, le reutiliser
        last_pair = _LAST_REFRESHED_TOKENS.get(provider)
        if failed_token and last_pair and last_pair[0] == failed_token and last_pair[1]:
            return last_pair[1]

        client_id = os.environ.get(id_env, "").strip()
        client_secret = os.environ.get(secret_env, "").strip()
        refresh_token = os.environ.get(refresh_env, "").strip()
        if not (client_id and client_secret and refresh_token):
            return None

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if resp.status_code >= 400:
            return None
        payload = resp.json()
        new_access = str(payload.get("access_token", "")).strip()
        if not new_access:
            return None
        if failed_token:
            _LAST_REFRESHED_TOKENS[provider] = (failed_token, new_access)
        _persist_refreshed_env(access_env, new_access)
        new_refresh = str(payload.get("refresh_token", "")).strip()
        if new_refresh:
            _persist_refreshed_env(refresh_env, new_refresh)
        return new_access


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

    async def _oauth_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Execute une requete HTTP Bearer avec rafraichissement automatique sur 401."""
        headers = dict(kwargs.pop("headers", None) or {})
        token = getattr(self, "access_token", "") or ""
        if self.oauth_provider and self.oauth_provider in _OAUTH_REFRESH_CONFIG:
            access_env = _OAUTH_REFRESH_CONFIG[self.oauth_provider][3]
            env_tok = os.environ.get(access_env, "").strip()
            if env_tok and env_tok != token and getattr(self, "_from_env_token", False):
                token = env_tok
                self.access_token = env_tok
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401 and self.oauth_provider:
                new_token = await refresh_oauth_access_token(
                    self.oauth_provider, failed_token=token
                )
                if new_token:
                    self.access_token = new_token
                    headers["Authorization"] = f"Bearer {new_token}"
                    resp = await client.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp


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
            return build_google_oauth_authorize_url(client_id, default_oauth_redirect_uri("google"))
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
