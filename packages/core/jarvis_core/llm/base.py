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

    def __init__(self, spent_usd: float, limit_usd: float):
        super().__init__(
            f"Plafond journalier LLM atteint ({spent_usd:.4f} USD >= {limit_usd:.4f} USD).",
            status_code=402,
            retryable=False,
        )
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd


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
    ) -> AsyncIterator[StreamDelta]:
        """Appel en streaming SSE retournant des fragments de texte, tool_calls et l'usage final."""
