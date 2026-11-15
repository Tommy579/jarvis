"""Connecteur Meteo, Transports et RSS (Open-Meteo, Navitia/SNCF, RSS)."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ..memory.schema import MemoryDatabase
from .base import BaseIntegration


class WeatherTransportIntegration(BaseIntegration):
    id = "weather_transport"
    name = "Meteo, Transports et RSS"
    priority = 3
    description = "Previsions Open-Meteo, horaires SNCF/Navitia et flux RSS pour les briefings."

    def __init__(
        self,
        latitude: float = 48.8566,
        longitude: float = 2.3522,
        rss_urls: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(enabled=enabled)
        self.latitude = latitude
        self.longitude = longitude
        self.rss_urls = rss_urls or []
        self.navitia_token = os.environ.get("NAVITIA_API_KEY", "")
        self.connected = True

    def tools(self) -> list[str]:
        return ["weather_forecast", "transport_departures", "rss_headlines"]

    async def fetch_weather(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "Europe/Paris",
                    "forecast_days": 2,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def fetch_rss_headlines(self, limit: int = 5) -> list[dict[str, str]]:
        headlines: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in self.rss_urls:
                resp = await client.get(url)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item")[:limit]:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    if title:
                        headlines.append({"title": title, "link": link})
        return headlines[:limit]

    async def sync(self, db: MemoryDatabase) -> dict[str, Any]:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}
        try:
            weather = await self.fetch_weather()
            db.add_event(self.id, "weather.updated", weather.get("current", {}), urgency=0)
            self.mark_synced()
            return {"ok": True, "current": weather.get("current", {})}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
