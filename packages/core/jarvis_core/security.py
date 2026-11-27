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


_SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_VALID_ACTION_DECISIONS: frozenset[str] = frozenset({"approve", "deny"})
_BEARER_REDACT_RE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_USERINFO_RE = re.compile(r"(https?|webcal)://([^/\s:@]+):([^/\s@]+)@")
_URL_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|client_secret|secret|api_key|apikey|key|code|sig|signature)=)[^&\s#]+"
)
_ICS_URL_PATH_RE = re.compile(r"(?i)\b(https?|webcal)://([^\s/]+)/[^\s?#]*\.ics\b")


def redact_sensitive_text(text: str) -> str:
    """Masque les jetons Bearer, identifiants d'URL, parametres secrets et chemins .ics prives."""
    if not text:
        return ""
    out = _BEARER_REDACT_RE.sub(r"\1***", str(text))
    out = _URL_USERINFO_RE.sub(r"\1://\2:***@", out)
    out = _URL_SECRET_QUERY_RE.sub(r"\1***", out)
    out = _ICS_URL_PATH_RE.sub(r"\1://\2/***.ics", out)
    return out


class SecretStore:
    """Gestionnaire de secrets : systemd-creds (/run/credentials) -> keyring/KWallet -> env."""

    def __init__(self, service_name: str = "jarvis-core", credentials_dir: Path | None = None):
        self.service_name = service_name
        self.credentials_dir = credentials_dir or Path(
            os.environ.get("CREDENTIALS_DIRECTORY", "/run/credentials/jarvis-core.service")
        )

    def get_secret(self, name: str, default: str = "") -> str:
        if not name or not _SECRET_NAME_RE.match(name):
            return default
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
        """Enregistre un secret dans le trousseau systeme ; repli memoire volatile (retourne False) si indisponible."""
        if not name or not _SECRET_NAME_RE.match(name):
            raise ValueError("Nom de secret invalide.")
        if "\x00" in value:
            raise ValueError("Valeur de secret invalide (caractere nul interdit).")
        try:
            import keyring  # type: ignore

            keyring.set_password(self.service_name, name, value)
            return True
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Trousseau keyring indisponible (%s) : le secret '%s' n'est conserve qu'en memoire pour ce processus.",
                type(exc).__name__,
                name,
            )
            os.environ[name] = value
            return False


def sign_action_token(secret: str, approval_id: str, action: str, ttl: int = 900) -> str:
    """Signe un jeton d'action HMAC-SHA256 avec TTL strict."""
    clean_secret = (secret or "").strip()
    clean_id = (approval_id or "").strip()
    clean_action = (action or "").strip()
    if not clean_secret:
        raise ValueError("Secret HMAC manquant pour signer le jeton d'action.")
    if not clean_id or ":" in clean_id or _CTRL_CHARS_RE.search(clean_id):
        raise ValueError("Identifiant d'approbation invalide.")
    if clean_action not in _VALID_ACTION_DECISIONS:
        raise ValueError(f"Action invalide : {clean_action!r}.")
    if ttl <= 0:
        raise ValueError("Le TTL du jeton d'action doit etre strictement positif.")
    expires_at = int(time.time()) + int(ttl)
    payload = f"{clean_id}:{clean_action}:{expires_at}"
    mac = hmac.new(
        clean_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{mac}"


def verify_action_token(secret: str, token: str) -> tuple[str, str] | None:
    """Verifie un jeton d'action. Retourne (approval_id, action) ou None si invalide ou expire."""
    if not secret or not token or _CTRL_CHARS_RE.search(token):
        return None
    parts = token.split(":")
    if len(parts) != 4:
        return None
    approval_id, action, exp_str, mac = parts
    if not approval_id or action not in _VALID_ACTION_DECISIONS or len(mac) != 64:
        return None
    payload = f"{approval_id}:{action}:{exp_str}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        exp_ts = int(exp_str)
        if time.time() > exp_ts:
            return None
    except ValueError:
        return None
    return approval_id, action


def sign_session_cookie(api_token: str, ttl: int = 43200) -> str:
    """Genere un cookie de session court signe par cle derivee (sans exposer JARVIS_API_TOKEN)."""
    clean_tok = (api_token or "").strip()
    if not clean_tok or ttl <= 0:
        raise ValueError("Parametres de session invalides.")
    subkey = hmac.new(
        clean_tok.encode("utf-8"), b"jarvis-session-cookie-v1", hashlib.sha256
    ).digest()
    nonce = os.urandom(16).hex()
    expires_at = int(time.time()) + int(ttl)
    payload = f"{nonce}:{expires_at}"
    mac = hmac.new(subkey, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{mac}"


def verify_session_cookie(api_token: str, cookie_value: str | None) -> bool:
    """Verifie en temps constant la signature et l'expiration d'un cookie de session."""
    clean_tok = (api_token or "").strip()
    raw = (cookie_value or "").strip()
    if not clean_tok or not raw or _CTRL_CHARS_RE.search(raw):
        return False
    parts = raw.split(":")
    if len(parts) != 3:
        return False
    nonce, exp_str, mac = parts
    if len(nonce) < 16 or len(mac) != 64:
        return False
    subkey = hmac.new(
        clean_tok.encode("utf-8"), b"jarvis-session-cookie-v1", hashlib.sha256
    ).digest()
    payload = f"{nonce}:{exp_str}"
    expected = hmac.new(subkey, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return False
    try:
        exp_ts = int(exp_str)
    except ValueError:
        return False
    return time.time() <= exp_ts


class SlidingWindowRateLimiter:
    """Limitation de debit en memoire par cle (IP ou route) avec eviction bornee."""

    def __init__(
        self,
        max_requests: int = 30,
        window_seconds: float = 60.0,
        max_keys: int = 4096,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_keys = max(16, int(max_keys))
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune_key(self, key: str, ts: float) -> deque[float] | None:
        q = self._hits.get(key)
        if q is None:
            return None
        cutoff = ts - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
            return None
        return q

    def _enforce_capacity(self, ts: float) -> None:
        if len(self._hits) < self.max_keys:
            return
        cutoff = ts - self.window_seconds
        for k in list(self._hits.keys()):
            q = self._hits[k]
            while q and q[0] <= cutoff:
                q.popleft()
            if not q:
                self._hits.pop(k, None)
        while len(self._hits) >= self.max_keys:
            oldest_key = next(iter(self._hits))
            self._hits.pop(oldest_key, None)

    def is_limited(self, key: str, now: float | None = None) -> bool:
        """Retourne True si le quota est deja atteint sans consommer de jeton supplementaire."""
        ts = now if now is not None else time.monotonic()
        q = self._prune_key(key, ts)
        return q is not None and len(q) >= self.max_requests

    def allow(self, key: str, now: float | None = None) -> bool:
        ts = now if now is not None else time.monotonic()
        q = self._prune_key(key, ts)
        if q is not None and len(q) >= self.max_requests:
            return False
        if q is None:
            self._enforce_capacity(ts)
            q = self._hits[key]
        q.append(ts)
        return True
