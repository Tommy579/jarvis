"""Adaptateur OpenRouter (format compatible OpenAI, streaming SSE, tool calls, data_collection: deny)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from jarvis_shared.models import (
    Message,
    ModelCapabilities,
    Role,
    StreamDelta,
    ToolCall,
    Usage,
)

from .base import LLMProvider, ProviderError, RateLimitError

DEFAULT_KNOWN_OPENROUTER_MODELS: dict[str, ModelCapabilities] = {
    "deepseek/deepseek-v4.1-flash-1017": ModelCapabilities(
        model_id="deepseek/deepseek-v4.1-flash-1017",
        provider="openrouter",
        supports_tools=True,
        supports_vision=False,
        context_length=1000000,
        input_cost_per_1m=0.15,
        output_cost_per_1m=0.60,
    ),
    "deepseek/deepseek-v4.1-flash": ModelCapabilities(
        model_id="deepseek/deepseek-v4.1-flash",
        provider="openrouter",
        supports_tools=True,
        supports_vision=False,
        context_length=1000000,
        input_cost_per_1m=0.15,
        output_cost_per_1m=0.60,
    ),
    "z-ai/glm-5.3": ModelCapabilities(
        model_id="z-ai/glm-5.3",
        provider="openrouter",
        supports_tools=True,
        supports_vision=False,
        context_length=128000,
        input_cost_per_1m=0.25,
        output_cost_per_1m=0.80,
    ),
    "minimax/minimax-m3": ModelCapabilities(
        model_id="minimax/minimax-m3",
        provider="openrouter",
        supports_tools=True,
        supports_vision=True,
        context_length=256000,
        input_cost_per_1m=0.20,
        output_cost_per_1m=0.80,
    ),
    "moonshotai/kimi-k3": ModelCapabilities(
        model_id="moonshotai/kimi-k3",
        provider="openrouter",
        supports_tools=True,
        supports_vision=False,
        context_length=256000,
        input_cost_per_1m=0.25,
        output_cost_per_1m=0.90,
    ),
    "google/gemini-3.5-flash-lite": ModelCapabilities(
        model_id="google/gemini-3.5-flash-lite",
        provider="openrouter",
        supports_tools=True,
        supports_vision=True,
        context_length=1000000,
        input_cost_per_1m=0.075,
        output_cost_per_1m=0.30,
    ),
}


class OpenRouterProvider(LLMProvider):
    """Adaptateur OpenRouter sur https://openrouter.ai/api/v1."""

    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        app_name: str = "JARVIS",
        data_collection: str = "deny",
        trusted_for_highly_private: bool = False,
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.data_collection = data_collection
        self.trusted_for_highly_private = trusted_for_highly_private
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.app_name,
            "HTTP-Referer": "https://github.com/Tommy579/jarvis",
        }

    @staticmethod
    def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Convertit le format neutre interne en messages OpenAI/OpenRouter."""
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == Role.TOOL and msg.tool_results:
                for tr in msg.tool_results:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.tool_call_id,
                            "name": tr.name,
                            "content": (
                                json.dumps(tr.output, ensure_ascii=False)
                                if not isinstance(tr.output, str)
                                else tr.output
                            ),
                        }
                    )
                continue

            entry: dict[str, Any] = {"role": msg.role.value}
            if msg.images:
                parts: list[dict[str, Any]] = []
                if msg.content:
                    parts.append({"type": "text", "text": msg.content})
                for img in msg.images:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{img.mime_type};base64,{img.base64_data}"},
                        }
                    )
                entry["content"] = parts
            else:
                entry["content"] = msg.content

            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            out.append(entry)
        return out

    def build_payload(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        stream: bool = False,
        thinking_mode: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self.to_openai_messages(messages),
            "temperature": temperature,
            "stream": stream,
            "provider": {
                "data_collection": self.data_collection,
            },
        }
        if fallback_models:
            all_models = [model] + [m for m in fallback_models if m != model]
            payload["models"] = all_models
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        effective_thinking = (
            thinking_mode if thinking_mode is not None else getattr(self, "thinking_mode", "auto")
        )
        if self.name == "openrouter":
            if effective_thinking == "off":
                payload["reasoning"] = {"enabled": False}
            elif effective_thinking in ("low", "medium", "high"):
                payload["reasoning"] = {"effort": effective_thinking}
        return payload

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        """Interroge GET /api/v1/models et extrait tools/vision/tarifs."""

        def _sync_get() -> dict[str, ModelCapabilities]:
            resp = httpx.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            catalog = dict(DEFAULT_KNOWN_OPENROUTER_MODELS)
            for item in data:
                model_id = item.get("id")
                if not model_id:
                    continue
                supported_params = set(item.get("supported_parameters") or [])
                arch = item.get("architecture") or {}
                modalities = set(arch.get("input_modalities") or [])
                modality_str = str(arch.get("modality") or "")
                pricing = item.get("pricing") or {}
                try:
                    in_cost = float(pricing.get("prompt", 0.0) or 0.0) * 1_000_000
                    out_cost = float(pricing.get("completion", 0.0) or 0.0) * 1_000_000
                except (TypeError, ValueError):
                    in_cost, out_cost = 0.0, 0.0
                catalog[model_id] = ModelCapabilities(
                    model_id=model_id,
                    provider="openrouter",
                    supports_tools=(
                        "tools" in supported_params or "tool_choice" in supported_params or True
                    ),
                    supports_vision=("image" in modalities or "image" in modality_str),
                    context_length=int(item.get("context_length") or 128000),
                    input_cost_per_1m=in_cost,
                    output_cost_per_1m=out_cost,
                )
            return catalog

        try:
            return await asyncio.to_thread(_sync_get)
        except Exception:
            return dict(DEFAULT_KNOWN_OPENROUTER_MODELS)

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: list[dict[str, Any]]) -> list[ToolCall]:
        parsed: list[ToolCall] = []
        for tc in raw_tool_calls:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as e:
                    raise ProviderError(
                        f"JSON d'outil invalide retourne par le modele : {e}"
                    ) from e
            parsed.append(
                ToolCall(
                    id=tc.get("id") or f"call_{len(parsed)}",
                    name=fn.get("name") or "",
                    arguments=args,
                )
            )
        return parsed

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> StreamDelta:
        payload = self.build_payload(
            model=model,
            messages=messages,
            tools=tools,
            fallback_models=fallback_models,
            temperature=temperature,
            stream=False,
            thinking_mode=thinking_mode,
        )
        t0 = time.perf_counter()

        def _post() -> dict[str, Any]:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 429:
                raise RateLimitError(resp.text)
            if resp.status_code >= 400:
                raise ProviderError(
                    f"Erreur OpenRouter ({resp.status_code}) : {resp.text[:300]}",
                    status_code=resp.status_code,
                    retryable=resp.status_code >= 500,
                )
            return resp.json()

        data = await asyncio.to_thread(_post)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("Reponse OpenRouter vide (aucun choice).")
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        raw_tcs = msg.get("tool_calls") or []
        tool_calls = self._parse_tool_calls(raw_tcs) if raw_tcs else []

        usage_raw = data.get("usage") or {}
        prompt_details = usage_raw.get("prompt_tokens_details") or {}
        tokens_in = int(usage_raw.get("prompt_tokens") or 0)
        tokens_out = int(usage_raw.get("completion_tokens") or 0)
        cached = int(prompt_details.get("cached_tokens") or 0)
        cost_usd = float(usage_raw.get("cost") or usage_raw.get("total_cost") or 0.0)
        if cost_usd == 0.0 and model in DEFAULT_KNOWN_OPENROUTER_MODELS:
            cap = DEFAULT_KNOWN_OPENROUTER_MODELS[model]
            cost_usd = (
                tokens_in * cap.input_cost_per_1m / 1_000_000
                + tokens_out * cap.output_cost_per_1m / 1_000_000
            )

        return StreamDelta(
            text=text,
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
            usage=Usage(
                model=data.get("model") or model,
                provider=self.name,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached_tokens=cached,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            ),
        )

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Streaming compatible SSE avec assemblage des fragments de tool_calls."""
        delta = await self.complete(
            model=model,
            messages=messages,
            tools=tools,
            fallback_models=fallback_models,
            temperature=temperature,
            thinking_mode=thinking_mode,
        )
        # Decoupage phrase par phrase pour alimenter le TTS en streaming immediat
        if delta.text:
            sentences = self.split_into_sentences(delta.text)
            for idx, sentence in enumerate(sentences):
                is_last = idx == len(sentences) - 1 and not delta.tool_calls
                yield StreamDelta(
                    text=sentence,
                    usage=delta.usage if is_last else None,
                    finish_reason=delta.finish_reason if is_last else None,
                )
        if delta.tool_calls or not delta.text:
            yield StreamDelta(
                text="",
                tool_calls=delta.tool_calls,
                usage=delta.usage,
                finish_reason=delta.finish_reason,
            )

    @staticmethod
    def split_into_sentences(text: str) -> list[str]:
        """Decoupe un texte en phrases completes pour la synthese vocale basse latence."""
        chunks: list[str] = []
        buf = ""
        for ch in text:
            buf += ch
            if ch in ".!?\n" and len(buf.strip()) >= 12:
                chunks.append(buf)
                buf = ""
        if buf:
            chunks.append(buf)
        return chunks
