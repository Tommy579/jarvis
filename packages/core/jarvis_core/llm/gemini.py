"""Adaptateur Gemini direct via google-genai (sans auto-execution d'outils pour respecter les permissions)."""

from __future__ import annotations

import array
import asyncio
import base64
import io
import math
import mimetypes
import os
import time
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from jarvis_shared.models import (
    Message,
    ModelCapabilities,
    Role,
    StreamDelta,
    ToolCall,
    Usage,
)

from .base import LLMProvider, ProviderError, RateLimitError
from .openrouter import OpenRouterProvider

DEFAULT_KNOWN_GEMINI_MODELS: dict[str, ModelCapabilities] = {
    "gemini-3.5-flash-lite": ModelCapabilities(
        model_id="gemini-3.5-flash-lite",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=1000000,
        input_cost_per_1m=0.075,
        output_cost_per_1m=0.30,
    ),
    "models/gemini-3.5-flash-lite": ModelCapabilities(
        model_id="models/gemini-3.5-flash-lite",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=1000000,
        input_cost_per_1m=0.075,
        output_cost_per_1m=0.30,
    ),
    "gemini-4-hydrogen": ModelCapabilities(
        model_id="gemini-4-hydrogen",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=2000000,
        input_cost_per_1m=1.25,
        output_cost_per_1m=5.00,
    ),
    "gemini-4-argon": ModelCapabilities(
        model_id="gemini-4-argon",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=1000000,
        input_cost_per_1m=0.30,
        output_cost_per_1m=1.20,
    ),
    "gemini-4-carbon": ModelCapabilities(
        model_id="gemini-4-carbon",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=1000000,
        input_cost_per_1m=0.075,
        output_cost_per_1m=0.30,
    ),
    "gemini-live": ModelCapabilities(
        model_id="gemini-live",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=128000,
    ),
    "gemini-3-flash-preview": ModelCapabilities(
        model_id="gemini-3-flash-preview",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=1000000,
        input_cost_per_1m=0.10,
        output_cost_per_1m=0.40,
    ),
    "gemini-3.1-pro-preview": ModelCapabilities(
        model_id="gemini-3.1-pro-preview",
        provider="gemini",
        supports_tools=True,
        supports_vision=True,
        supports_audio=True,
        context_length=2000000,
        input_cost_per_1m=1.25,
        output_cost_per_1m=5.00,
    ),
}


class GeminiProvider(LLMProvider):
    """Adaptateur Google Gemini direct utilisant google-genai avec controle manuel des outils."""

    name = "gemini"

    def __init__(
        self,
        api_key: str = "",
        trusted_for_highly_private: bool = True,
        vertexai: bool | None = None,
        project: str | None = None,
        location: str | None = None,
    ):
        self.api_key = api_key
        self.vertexai = (
            vertexai
            if vertexai is not None
            else (
                os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() in ("true", "1", "yes")
                or self.api_key.startswith("AQ.")
            )
        )
        self.project = (
            project if project is not None else (os.getenv("GOOGLE_CLOUD_PROJECT") or None)
        )
        self.location = (
            location
            if location is not None
            else (os.getenv("GOOGLE_CLOUD_LOCATION") or ("us-central1" if self.vertexai else None))
        )
        self.trusted_for_highly_private = trusted_for_highly_private
        self._client: Any = None
        self._thought_signatures: dict[str, bytes] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            if self.vertexai:
                if self.project or not self.api_key:
                    self._client = genai.Client(
                        vertexai=True,
                        project=self.project,
                        location=self.location,
                    )
                else:
                    self._client = genai.Client(
                        vertexai=True,
                        api_key=self.api_key,
                    )
            else:
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options={"api_version": "v1beta"},
                )
        return self._client

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        catalog = dict(DEFAULT_KNOWN_GEMINI_MODELS)
        if not self.api_key and not self.vertexai:
            return catalog

        def _list() -> dict[str, ModelCapabilities]:
            client = self._get_client()
            for m in client.models.list():
                name = getattr(m, "name", None)
                if not name:
                    continue
                short_name = name.removeprefix("models/")
                cap = ModelCapabilities(
                    model_id=short_name,
                    provider="gemini",
                    supports_tools=True,
                    supports_vision=True,
                    supports_audio=True,
                    context_length=int(getattr(m, "input_token_limit", 1000000) or 1000000),
                )
                catalog[short_name] = cap
                catalog[name] = cap
            return catalog

        try:
            return await asyncio.to_thread(_list)
        except Exception:
            return catalog

    @staticmethod
    def _sanitize_gemini_schema(
        schema: dict[str, Any],
        defs: dict[str, Any] | None = None,
        *,
        _seen: set[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        root_defs = defs if defs is not None else dict(schema.get("$defs") or {})
        seen = _seen if _seen is not None else set()

        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            def_name = ref.removeprefix("#/$defs/")
            if def_name in root_defs and def_name not in seen:
                target = dict(root_defs[def_name])
                if "description" in schema and "description" not in target:
                    target["description"] = schema["description"]
                return GeminiProvider._sanitize_gemini_schema(
                    target, root_defs, _seen=seen | {def_name}
                )

        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in ("$defs", "$schema", "additionalProperties", "$ref"):
                continue
            if k == "anyOf" and isinstance(v, list):
                non_null = [
                    item
                    for item in v
                    if not (isinstance(item, dict) and item.get("type") == "null")
                ]
                has_null = len(non_null) < len(v)
                if len(non_null) == 1 and isinstance(non_null[0], dict):
                    resolved = GeminiProvider._sanitize_gemini_schema(
                        non_null[0], root_defs, _seen=seen
                    )
                    out.update(resolved)
                    if has_null:
                        out["nullable"] = True
                    continue
                out[k] = [
                    GeminiProvider._sanitize_gemini_schema(item, root_defs, _seen=seen)
                    if isinstance(item, dict)
                    else item
                    for item in v
                ]
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {
                    pk: GeminiProvider._sanitize_gemini_schema(pv, root_defs, _seen=seen)
                    for pk, pv in v.items()
                }
            elif k == "items" and isinstance(v, dict):
                out[k] = GeminiProvider._sanitize_gemini_schema(v, root_defs, _seen=seen)
            else:
                out[k] = v
        return out

    @staticmethod
    def _to_gemini_function_declarations(
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if not tools:
            return []
        decls: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function") or t
            raw_params = fn.get("parameters", {"type": "object", "properties": {}})
            decls.append(
                {
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "parameters": GeminiProvider._sanitize_gemini_schema(raw_params),
                }
            )
        return [{"function_declarations": decls}]

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> StreamDelta:
        t0 = time.perf_counter()
        system_parts = [m.content for m in messages if m.role == Role.SYSTEM and m.content]
        system_instruction = "\n\n".join(system_parts) if system_parts else None

        contents: list[Any] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                continue
            role = "model" if msg.role == Role.ASSISTANT else "user"
            parts: list[Any] = []
            if msg.content:
                parts.append({"text": msg.content})
            for img in msg.images:
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": img.mime_type,
                            "data": base64.b64decode(img.base64_data),
                        }
                    }
                )
            if msg.role == Role.ASSISTANT and msg.tool_calls:
                for tc in msg.tool_calls:
                    fc_part: dict[str, Any] = {
                        "function_call": {
                            "name": tc.name,
                            "args": tc.arguments,
                        }
                    }
                    sig = self._thought_signatures.get(tc.id)
                    if sig is not None:
                        fc_part["thought_signature"] = sig
                    parts.append(fc_part)
            if msg.role == Role.TOOL and msg.tool_results:
                for tr in msg.tool_results:
                    parts.append(
                        {
                            "function_response": {
                                "name": tr.name,
                                "response": (
                                    tr.output
                                    if isinstance(tr.output, dict)
                                    else {"result": tr.output}
                                ),
                            }
                        }
                    )
            if parts:
                contents.append({"role": role, "parts": parts})

        def _resolve_vertex_model(raw_model: str) -> str:
            cleaned = raw_model.removeprefix("models/").removeprefix("google/")
            if cleaned in ("gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-live"):
                return "gemini-3-flash-preview"
            return cleaned

        def _invoke() -> Any:
            client = self._get_client()
            config: dict[str, Any] = {"temperature": temperature}
            if system_instruction:
                config["system_instruction"] = system_instruction
            if tools:
                config["tools"] = self._to_gemini_function_declarations(tools)
                # Desactiver l'appel automatique des outils par le SDK Gemini :
                # c'est AgentLoop qui controle les permissions niveaux 0..3.
                config["automatic_function_calling"] = {"disable": True}
            thinking_mode = getattr(self, "thinking_mode", "auto")
            thinking_budgets = {"off": 0, "low": 1024, "medium": 4096, "high": 16384}
            if thinking_mode in thinking_budgets:
                config["thinking_config"] = {"thinking_budget": thinking_budgets[thinking_mode]}
            effective_model = _resolve_vertex_model(model) if self.vertexai else model
            try:
                return client.models.generate_content(
                    model=effective_model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                err_msg = str(exc).lower()
                if (
                    "404" in err_msg or "not_found" in err_msg
                ) and effective_model != "gemini-3-flash-preview":
                    return client.models.generate_content(
                        model="gemini-3-flash-preview",
                        contents=contents,
                        config=config,
                    )
                raise

        try:
            response = await asyncio.to_thread(_invoke)
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
                raise RateLimitError(str(e)) from e
            raise ProviderError(f"Erreur Gemini ({model}) : {e}") from e

        latency_ms = (time.perf_counter() - t0) * 1000.0
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for cand in getattr(response, "candidates", None) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    args = dict(getattr(fc, "args", None) or {})
                    tc = ToolCall(name=fc.name, arguments=args)
                    sig = getattr(part, "thought_signature", None)
                    if isinstance(sig, bytes):
                        self._thought_signatures[tc.id] = sig
                    tool_calls.append(tc)

        if not text_parts and not tool_calls and getattr(response, "text", None):
            text_parts.append(response.text)

        usage_meta = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage_meta, "candidates_token_count", 0) or 0)
        cached = int(getattr(usage_meta, "cached_content_token_count", 0) or 0)

        lookup_model = model.removeprefix("models/").removeprefix("google/")
        cap = DEFAULT_KNOWN_GEMINI_MODELS.get(lookup_model) or DEFAULT_KNOWN_GEMINI_MODELS.get(
            model
        )
        cost_usd = 0.0
        if cap is not None:
            cost_usd = (
                tokens_in * cap.input_cost_per_1m / 1_000_000
                + tokens_out * cap.output_cost_per_1m / 1_000_000
            )

        return StreamDelta(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason="stop",
            usage=Usage(
                model=model,
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
    ) -> AsyncIterator[StreamDelta]:
        delta = await self.complete(
            model=model,
            messages=messages,
            tools=tools,
            fallback_models=fallback_models,
            temperature=temperature,
        )
        if delta.text:
            sentences = OpenRouterProvider.split_into_sentences(delta.text)
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

    async def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcrit un segment audio via Gemini Flash (retourne '' si silence)."""
        if not audio_bytes:
            return ""
        if audio_bytes[:4] == b"RIFF":
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
                    if wav_file.getsampwidth() == 2:
                        frames = wav_file.readframes(wav_file.getnframes())
                        samples = array.array("h", frames)
                        if not samples:
                            return ""
                        peak = max(abs(sample) for sample in samples)
                        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
                        if peak < 400 and rms < 150:
                            return ""
            except (wave.Error, EOFError):
                return ""
        from google.genai import types

        client = self._get_client()
        prompt = (
            "Transcris mot pour mot uniquement la parole humaine clairement audible en francais "
            "dans cet audio. N'invente rien. S'il n'y a aucune parole claire (silence, souffle "
            "ou bruit ambiant), reponds exactement SILENCE."
        )
        contents = [
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            prompt,
        ]
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3-flash-preview",
            contents=contents,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        raw = (getattr(response, "text", None) or "").strip()
        if not raw or "SILENCE" in raw.upper():
            return ""
        return raw

    def _usage_from_response(self, response: Any, model: str, started: float) -> Usage:
        """Convertit usage_metadata en Usage (tokens + cout estime) pour la comptabilite."""
        usage_meta = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage_meta, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage_meta, "candidates_token_count", 0) or 0)
        cached = int(getattr(usage_meta, "cached_content_token_count", 0) or 0)
        lookup_model = model.removeprefix("models/").removeprefix("google/")
        cap = DEFAULT_KNOWN_GEMINI_MODELS.get(lookup_model) or DEFAULT_KNOWN_GEMINI_MODELS.get(
            model
        )
        cost_usd = 0.0
        if cap is not None:
            cost_usd = (
                tokens_in * cap.input_cost_per_1m / 1_000_000
                + tokens_out * cap.output_cost_per_1m / 1_000_000
            )
        return Usage(
            model=model,
            provider=self.name,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached,
            cost_usd=cost_usd,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def analyze_media(
        self,
        file_path: Path,
        prompt: str,
        mime_type: str | None = None,
        model: str = "gemini-3-flash-preview",
        with_usage: bool = False,
    ) -> Any:
        """Analyse un fichier audio ou video via Gemini (inline pour petit audio, Files API sinon).

        with_usage=True renvoie (texte, Usage) pour que l'appelant puisse facturer l'appel
        dans son budget.
        """
        from google.genai import types

        resolved_mime = (
            mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        )
        client = self._get_client()
        size_bytes = file_path.stat().st_size
        started = time.monotonic()

        if resolved_mime.startswith("audio/") and size_bytes <= 15 * 1024 * 1024:
            data = await asyncio.to_thread(file_path.read_bytes)
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type=resolved_mime), prompt],
            )
            text = (getattr(response, "text", None) or "").strip()
            if with_usage:
                return text, self._usage_from_response(response, model, started)
            return text

        uploaded = await asyncio.to_thread(client.files.upload, file=str(file_path))
        try:
            for _ in range(30):
                state_name = getattr(getattr(uploaded, "state", None), "name", None) or ""
                if state_name and state_name.upper() == "PROCESSING":
                    await asyncio.sleep(1.5)
                    uploaded = await asyncio.to_thread(client.files.get, name=uploaded.name)
                else:
                    break
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=[uploaded, prompt],
            )
            text = (getattr(response, "text", None) or "").strip()
            if with_usage:
                return text, self._usage_from_response(response, model, started)
            return text
        finally:
            try:
                await asyncio.to_thread(client.files.delete, name=uploaded.name)
            except Exception:
                pass
