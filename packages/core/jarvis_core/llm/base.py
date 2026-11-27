"""Interface abstraite LLMProvider et erreurs normalisees."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from jarvis_shared.models import Message, ModelCapabilities, StreamDelta


class ProviderError(Exception):
    """Erreur generique d'un fournisseur LLM."""

    def __init__(self, message: str, status_code: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class RateLimitError(ProviderError):
    """Erreur 429 / depassement de quota."""

    def __init__(self, message: str = "Quota depasse (429)"):
        super().__init__(message, status_code=429, retryable=True)


class BudgetExceededError(ProviderError):
    """Coupe-circuit declenche lorsque le plafond journalier de couts est depasse."""

    def __init__(self, spent_usd: float, limit_usd: float, provider: str | None = None):
        scope = f" pour {provider}" if provider else ""
        super().__init__(
            f"Plafond journalier LLM{scope} atteint ({spent_usd:.4f} USD >= {limit_usd:.4f} USD).",
            status_code=402,
            retryable=False,
        )
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        self.provider = provider


class LLMProvider(ABC):
    """Interface commune a tous les adaptateurs LLM (OpenRouter, Gemini)."""

    name: str = "base"
    trusted_for_highly_private: bool = False

    @abstractmethod
    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        """Interroge l'API du fournisseur pour lister les modeles et leurs capacites."""

    @abstractmethod
    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> StreamDelta:
        """Appel complet non-streame retournant le texte, les tool_calls et l'usage."""

    @abstractmethod
    def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Appel en streaming SSE retournant des fragments de texte, tool_calls et l'usage final."""

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

    async def _stream_via_complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
        thinking_mode: str | None = None,
    ) -> AsyncIterator[StreamDelta]:
        delta = await self.complete(
            model=model,
            messages=messages,
            tools=tools,
            fallback_models=fallback_models,
            temperature=temperature,
            thinking_mode=thinking_mode,
        )
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
