"""Securite reseau (SSRF), balisage anti-injection, secrets, jetons d'action et rate-limiting."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import socket
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_UNTRUSTED_TAG_RE = re.compile(r"<\s*/?\s*untrusted\b[^>]*>?", re.IGNORECASE)


def wrap_untrusted_content(source: str, content: str) -> str:
    """Encadre tout contenu externe (mail, web, OCR ecran, audio) dans une balise <untrusted>."""
    safe_source = re.sub(r"[^a-zA-Z0-9_.\-:]", "_", source or "external")
    cleaned = _INVISIBLE_CHARS_RE.sub("", str(content))
    sanitized = _UNTRUSTED_TAG_RE.sub(
        lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        cleaned,
    )
    return f'<untrusted source="{safe_source}">\n{sanitized}\n</untrusted>'


def is_forbidden_ip_address(ip_str: str, *, allow_private: bool = False) -> bool:
    """Retourne True si l'adresse IP cible loopback, link-local, metadata, multicast ou prive."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return True
    if not allow_private and ip.is_private:
        return True
    return False


def validate_http_target_host(url: str, *, allow_private: bool = False) -> tuple[bool, str | None]:
    """Valide qu'une URL HTTP(S) ne resout pas vers une adresse IP locale ou interdite (SSRF)."""
    cleaned = _CTRL_CHARS_RE.sub("", (url or "")).strip()
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False, "Seules les URLs http:// ou https:// valides sont autorisees."
    host = parsed.hostname.strip().lower().strip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return False, f"Hote local interdit ({host})."
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if is_forbidden_ip_address(host, allow_private=allow_private):
            return False, f"Adresse IP locale ou reservee interdite ({host})."
        return True, None
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        return True, None
    for info in infos:
        addr = info[4][0]
        if is_forbidden_ip_address(str(addr), allow_private=allow_private):
            return False, f"Resolution DNS vers adresse IP locale/interdite ({host} -> {addr})."
    return True, None


class SecretStore:
    """Gestionnaire de secrets : systemd-creds (/run/credentials) -> keyring/KWallet -> env."""

    def __init__(self, service_name: str = "jarvis-core", credentials_dir: Path | None = None):
        self.service_name = service_name
        self.credentials_dir = credentials_dir or Path(
            os.environ.get("CREDENTIALS_DIRECTORY", "/run/credentials/jarvis-core.service")
        )

    def get_secret(self, name: str, default: str = "") -> str:
        cred_file = self.credentials_dir / name
        if cred_file.is_file():
            try:
                return cred_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        try:
            import keyring  # type: ignore

            val = keyring.get_password(self.service_name, name)
            if val:
                return val.strip()
        except Exception:
            pass

        return os.environ.get(name, default).strip()

    def set_secret(self, name: str, value: str) -> bool:
        try:
            import keyring  # type: ignore

            keyring.set_password(self.service_name, name, value)
            return True
        except Exception:
            os.environ[name] = value
            return False


def sign_action_token(secret: str, approval_id: str, action: str, ttl: int = 900) -> str:
    """Signe un jeton d'action HMAC (ex. approbation par SMS/ntfy) avec TTL."""
    expires_at = int(time.time()) + ttl
    payload = f"{approval_id}:{action}:{expires_at}"
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{mac}"


def verify_action_token(secret: str, token: str) -> tuple[str, str] | None:
    """Verifie un jeton d'action. Retourne (approval_id, action) ou None si invalide ou expire."""
    if not secret or not token:
        return None
    parts = token.split(":")
    if len(parts) != 4:
        return None
    approval_id, action, exp_str, mac = parts
    payload = f"{approval_id}:{action}:{exp_str}"
    expected = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        exp_ts = int(exp_str)
        if time.time() > exp_ts:
            return None
    except ValueError:
        return None
    return approval_id, action


class SlidingWindowRateLimiter:
    """Limitation de debit en memoire par cle (IP ou route)."""

    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        ts = now if now is not None else time.monotonic()
        q = self._hits[key]
        cutoff = ts - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(ts)
        return True
