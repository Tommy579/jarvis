"""Securite reseau, gestion des secrets, jetons d'action et sauvegardes restic."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import subprocess
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def is_allowed_bind_or_peer_ip(host: str) -> bool:
    """Autorise uniquement localhost et les adresses Tailscale (100.64.0.0/10)."""
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return True
        if isinstance(addr, ipaddress.IPv4Address) and addr in TAILSCALE_NETWORK:
            return True
    except ValueError:
        pass
    return False


def get_allowed_bind_hosts(extra_tailscale_ip: str | None = None) -> list[str]:
    """Retourne la liste des adresses d'ecoute autorisees pour omi-core."""
    hosts = ["127.0.0.1"]
    if extra_tailscale_ip and is_allowed_bind_or_peer_ip(extra_tailscale_ip):
        if extra_tailscale_ip not in hosts:
            hosts.append(extra_tailscale_ip)
    return hosts


class SecretStore:
    """Gestionnaire de secrets : systemd-creds (/run/credentials) -> keyring/KWallet -> env."""

    def __init__(self, service_name: str = "omi-core", credentials_dir: Path | None = None):
        self.service_name = service_name
        self.credentials_dir = credentials_dir or Path(
            os.environ.get("CREDENTIALS_DIRECTORY", "/run/credentials/omi-core.service")
        )

    def get_secret(self, name: str, default: str = "") -> str:
        # 1. systemd-creds
        cred_file = self.credentials_dir / name
        if cred_file.is_file():
            try:
                return cred_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        # 2. Trousseau systeme (keyring / KWallet)
        try:
            import keyring  # type: ignore

            val = keyring.get_password(self.service_name, name)
            if val:
                return val.strip()
        except Exception:
            pass

        # 3. Variable d'environnement (.env en developpement)
        return os.environ.get(name, default).strip()

    def set_secret(self, name: str, value: str) -> bool:
        try:
            import keyring  # type: ignore

            keyring.set_password(self.service_name, name, value)
            return True
        except Exception:
            os.environ[name] = value
            return False


class TokenAuthManager:
    """Appairage d'appareils (desktop, web, PWA mobile) par jeton hashé SHA-256."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path
        self._tokens: dict[str, dict[str, Any]] = {}
        if self.storage_path and self.storage_path.exists():
            self._load()

    def _hash(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            self._tokens = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except Exception:
            self._tokens = {}

    def _save(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(self._tokens, indent=2), encoding="utf-8")

    def issue_token(self, device_name: str, scopes: list[str] | None = None) -> str:
        raw_token = f"omi_{secrets.token_urlsafe(32)}"
        token_hash = self._hash(raw_token)
        self._tokens[token_hash] = {
            "device_name": device_name,
            "scopes": scopes or ["ws", "rest"],
            "created_at": time.time(),
            "revoked": False,
        }
        self._save()
        return raw_token

    def verify_token(self, raw_token: str | None, required_scope: str | None = None) -> bool:
        if not raw_token:
            return False
        token_hash = self._hash(raw_token)
        record = self._tokens.get(token_hash)
        if not record or record.get("revoked"):
            return False
        if required_scope and required_scope not in record.get("scopes", []):
            return False
        return True

    def revoke_device(self, device_name: str) -> int:
        count = 0
        for rec in self._tokens.values():
            if rec.get("device_name") == device_name and not rec.get("revoked"):
                rec["revoked"] = True
                count += 1
        if count:
            self._save()
        return count


def sign_action_token(secret: str, approval_id: str, action: str, ttl: int = 900) -> str:
    """Signe un jeton d'action HMAC (ex. approbation par SMS/ntfy) avec TTL."""
    expires_at = int(time.time()) + ttl
    payload = f"{approval_id}:{action}:{expires_at}"
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{mac}"


def verify_action_token(secret: str, token: str) -> tuple[str, str] | None:
    """Vérifie un jeton d'action. Retourne (approval_id, action) ou None si invalide ou expiré."""
    if not secret or not token:
        return None
    parts = token.split(":")
    if len(parts) != 4:
        return None
    approval_id, action, exp_str, mac = parts
    payload = f"{approval_id}:{action}:{exp_str}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
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


class ResticBackupManager:
    """Sauvegarde quotidienne chiffree de ~/omi-data vers un depot restic."""

    def __init__(
        self, data_dir: Path, repo_url: str, password_secret_name: str = "RESTIC_PASSWORD"
    ):
        self.data_dir = data_dir
        self.repo_url = repo_url
        self.password_secret_name = password_secret_name

    def build_backup_command(self) -> list[str]:
        return [
            "restic",
            "-r",
            self.repo_url,
            "backup",
            str(self.data_dir),
            "--tag",
            "omi-daily",
            "--exclude",
            "*.tmp",
        ]

    def run_backup(self, secret_store: SecretStore, timeout: int = 300) -> dict[str, Any]:
        password = secret_store.get_secret(self.password_secret_name)
        if not password:
            return {"ok": False, "error": "Mot de passe restic introuvable dans SecretStore."}
        env = os.environ.copy()
        env["RESTIC_PASSWORD"] = password
        try:
            proc = subprocess.run(
                self.build_backup_command(),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
