"""Connecteur GitHub (webhooks CI/issues) et sondes HTTP periodiques."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx

from ..memory.schema import MemoryDatabase
from ..security import redact_sensitive_text, validate_http_target_host
from .base import BaseIntegration


class GitHubProbesIntegration(BaseIntegration):
    id = "github_probes"
    name = "GitHub et Sondes HTTP"
    priority = 2
    description = "Webhooks GitHub (CI, issues) et surveillance HTTP des services personnels."
    auth_type: Literal["oauth", "api_key", "url", "csv"] = "url"

    def __init__(self, probe_urls: list[str] | None = None, enabled: bool = False) -> None:
        super().__init__(enabled=enabled)
        self.probe_urls = probe_urls or []
        self.connected = True

    def tools(self) -> list[str]:
        return []

    async def check_probes(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            for url in self.probe_urls:
                safe_url = redact_sensitive_text(url)
                ok_host, reason = await asyncio.to_thread(
                    validate_http_target_host, url, allow_private=True
                )
                if not ok_host:
                    results.append(
                        {"url": safe_url, "ok": False, "error": reason or "URL interdite."}
                    )
                    continue
                try:
                    resp = await client.get(url)
                    ok = 200 <= resp.status_code < 400
                    results.append({"url": safe_url, "ok": ok, "status_code": resp.status_code})
                except Exception as exc:
                    results.append(
                        {"url": safe_url, "ok": False, "error": redact_sensitive_text(str(exc))}
                    )
        return results

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        probe_results = await self.check_probes()
        failures = 0
        for res in probe_results:
            if not res["ok"]:
                failures += 1
                self._pending_events.append(
                    {"source": self.id, "type": "probe.failed", "payload": res}
                )
                db.add_event(self.id, "probe.failed", res, urgency=3)
        self.mark_synced()
        return {"ok": True, "checked": len(probe_results), "failures": failures}
