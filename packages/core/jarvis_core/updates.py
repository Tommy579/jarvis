"""Verification des versions publiees sur GitHub (cache, tolerance hors-ligne)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from jarvis_shared.rest_models import UpdateStatus
from jarvis_shared.version import app_version

UPDATE_REPOSITORY = "Tommy579/jarvis"
CACHE_TTL_SECONDS = 6 * 3600.0
REQUEST_TIMEOUT_SECONDS = 4.0

_cache: tuple[float, UpdateStatus] | None = None


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Transforme "v2.1.0-rc1" en (2, 1, 0) ; ignore les suffixes non numeriques."""
    cleaned = raw.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.replace("-", ".").replace("_", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def reset_cache() -> None:
    """Vide le cache (tests)."""
    global _cache
    _cache = None


async def check_for_update(current_version: str | None = None) -> UpdateStatus:
    """Interroge la derniere release GitHub ; renvoie un statut explicite en cas d'echec."""
    global _cache
    current = current_version or app_version()
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]

    checked_at = datetime.now(timezone.utc).isoformat()
    if os.environ.get("JARVIS_UPDATE_CHECK", "1").strip() == "0":
        status = UpdateStatus(
            current_version=current,
            checked_at=checked_at,
            detail="Verification des mises a jour desactivee (JARVIS_UPDATE_CHECK=0).",
        )
        _cache = (now, status)
        return status

    url = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"jarvis-core/{current}",
                },
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        status = UpdateStatus(
            current_version=current,
            checked_at=checked_at,
            detail=f"Verification impossible : {exc}",
        )
        _cache = (now, status)
        return status

    latest = str(payload.get("tag_name") or "").strip()
    status = UpdateStatus(
        current_version=current,
        latest_version=latest or None,
        update_available=bool(latest) and is_newer(latest, current),
        release_url=payload.get("html_url"),
        published_at=payload.get("published_at"),
        checked_at=checked_at,
        detail="" if latest else "Aucune version publiee sur le depot.",
    )
    _cache = (now, status)
    return status
