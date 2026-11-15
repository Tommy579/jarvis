"""Adaptateur Ollama local (API compatible OpenAI sur http://127.0.0.1:11434/v1 et decouverte via /api/tags)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import requests
from omi_shared.models import Message, ModelCapabilities, StreamDelta

from .base import ProviderError
from .openrouter import OpenRouterProvider

DEFAULT_KNOWN_OLLAMA_MODELS: dict[str, ModelCapabilities] = {
    "qwen3.5:2b": ModelCapabilities(
        model_id="qwen3.5:2b",
        provider="ollama",
        supports_tools=True,
        supports_vision=False,
        context_length=128000,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    ),
    "qwen3.5:0.8b": ModelCapabilities(
        model_id="qwen3.5:0.8b",
        provider="ollama",
        supports_tools=True,
        supports_vision=False,
        context_length=128000,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    ),
    "gemma4:31b-cloud": ModelCapabilities(
        model_id="gemma4:31b-cloud",
        provider="ollama",
        supports_tools=True,
        supports_vision=True,
        context_length=128000,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    ),
    "nemotron-3-ultra:cloud": ModelCapabilities(
        model_id="nemotron-3-ultra:cloud",
        provider="ollama",
        supports_tools=True,
        supports_vision=True,
        context_length=128000,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    ),
    "minimax-m3:cloud": ModelCapabilities(
        model_id="minimax-m3:cloud",
        provider="ollama",
        supports_tools=True,
        supports_vision=True,
        context_length=256000,
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
    ),
}


class OllamaProvider(OpenRouterProvider):
    """Adaptateur pour les modeles locaux et cloud exposes par le demon Ollama local."""

    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        trusted_for_highly_private: bool = True,
        timeout: float = 60.0,
    ):
        raw_base = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL", "").strip()
            or "http://127.0.0.1:11434/v1"
        ).rstrip("/")
        if not raw_base.endswith("/v1"):
            raw_base = f"{raw_base}/v1"
        super().__init__(
            api_key="ollama",
            base_url=raw_base,
            app_name="OMI",
            trusted_for_highly_private=trusted_for_highly_private,
            timeout=timeout,
        )
        self.root_url = raw_base[:-3]

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    @staticmethod
    def clean_model_id(model: str) -> str:
        return model.strip().removeprefix("ollama/")

    def build_payload(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        stream: bool = False,
    ) -> dict[str, Any]:
        _ = fallback_models
        payload: dict[str, Any] = {
            "model": self.clean_model_id(model),
            "messages": self.to_openai_messages(messages),
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        return payload

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        """Interroge GET /api/tags sur le demon Ollama local pour lister les modeles installes."""

        def _sync_get() -> dict[str, ModelCapabilities]:
            resp = requests.get(f"{self.root_url}/api/tags", timeout=2.0)
            resp.raise_for_status()
            models = resp.json().get("models") or []
            catalog = dict(DEFAULT_KNOWN_OLLAMA_MODELS)
            for item in models:
                name = str(item.get("name") or item.get("model") or "").strip()
                if not name:
                    continue
                low = name.lower()
                supports_vision = any(
                    tok in low for tok in ("vl", "vision", "gemma", "llava", "minicpm", "minimax")
                )
                cap = ModelCapabilities(
                    model_id=name,
                    provider="ollama",
                    supports_tools=True,
                    supports_vision=supports_vision,
                    context_length=128000,
                    input_cost_per_1m=0.0,
                    output_cost_per_1m=0.0,
                )
                catalog[name] = cap
                catalog[f"ollama/{name}"] = cap
            return catalog

        try:
            return await asyncio.to_thread(_sync_get)
        except Exception:
            return dict(DEFAULT_KNOWN_OLLAMA_MODELS)

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> StreamDelta:
        cleaned = self.clean_model_id(model)
        try:
            return await super().complete(
                model=cleaned,
                messages=messages,
                tools=tools,
                fallback_models=None,
                temperature=temperature,
            )
        except ProviderError as exc:
            # Certains petits modeles Ollama locaux refusent le champ 'tools' avec HTTP 400
            if tools and exc.status_code == 400 and "tool" in str(exc).lower():
                return await super().complete(
                    model=cleaned,
                    messages=messages,
                    tools=None,
                    fallback_models=None,
                    temperature=temperature,
                )
            raise
