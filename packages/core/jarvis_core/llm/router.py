"""Routeur LLM par role avec changement a chaud, fallback en cascade, delegation vision et suivi des couts."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis_shared.models import (
    Message,
    ModelCapabilities,
    Role,
    StreamDelta,
    Usage,
)

from ..tools.registry import wrap_untrusted_content
from .base import BudgetExceededError, LLMProvider, ProviderError
from .gemini import DEFAULT_KNOWN_GEMINI_MODELS
from .ollama import DEFAULT_KNOWN_OLLAMA_MODELS
from .openrouter import DEFAULT_KNOWN_OPENROUTER_MODELS

VALID_THINKING_MODES: tuple[str, ...] = ("auto", "off", "low", "medium", "high")


def normalize_thinking_mode(mode: Any) -> str:
    """Normalise un niveau de raisonnement vers une valeur supportee."""
    if mode is False:
        return "off"
    if not isinstance(mode, str):
        return "auto"
    clean = mode.strip().lower()
    return clean if clean in VALID_THINKING_MODES else "auto"


def _accepts_thinking_mode(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "thinking_mode" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


@dataclass
class RoleRoute:
    role_name: str
    model: str
    provider: str
    fallback: list[str] = field(default_factory=list)
    thinking_mode: str = "auto"


DEFAULT_ROLE_ROUTES: dict[str, RoleRoute] = {
    "agent": RoleRoute(
        role_name="agent",
        model="deepseek/deepseek-v4.1-flash-1017",
        provider="openrouter",
        fallback=["z-ai/glm-5.3", "minimax/minimax-m3", "gemini-3.5-flash-lite"],
    ),
    "triage": RoleRoute(
        role_name="triage",
        model="gemini-4-carbon",
        provider="gemini",
        fallback=["gemini-4-argon"],
    ),
    "vision": RoleRoute(
        role_name="vision",
        model="gemini-4-carbon",
        provider="gemini",
        fallback=["gemini-4-argon"],
    ),
    "memory": RoleRoute(
        role_name="memory",
        model="deepseek/deepseek-v4.1-flash-1017",
        provider="openrouter",
        fallback=["gemini-3.5-flash-lite"],
    ),
    "deep": RoleRoute(
        role_name="deep",
        model="gemini-4-hydrogen",
        provider="gemini",
        fallback=["deepseek/deepseek-v4.1-flash-1017"],
    ),
    "orchestrator": RoleRoute(
        role_name="orchestrator",
        model="gemini-4-argon",
        provider="gemini",
        fallback=["deepseek/deepseek-v4.1-flash-1017", "z-ai/glm-5.3", "gemini-4-hydrogen"],
    ),
    "voice_realtime": RoleRoute(
        role_name="voice_realtime",
        model="gemini-live",
        provider="gemini",
        fallback=[],
    ),
}

MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "deepseek": ("openrouter", "deepseek/deepseek-v4.1-flash-1017"),
    "deepseek-v4.1": ("openrouter", "deepseek/deepseek-v4.1-flash-1017"),
    "deepseek-v4.1-1017": ("openrouter", "deepseek/deepseek-v4.1-flash-1017"),
    "glm": ("openrouter", "z-ai/glm-5.3"),
    "glm-5.3": ("openrouter", "z-ai/glm-5.3"),
    "minimax": ("openrouter", "minimax/minimax-m3"),
    "minimax-m3": ("openrouter", "minimax/minimax-m3"),
    "kimi": ("openrouter", "moonshotai/kimi-k3"),
    "kimi-k3": ("openrouter", "moonshotai/kimi-k3"),
    "moonshotai/kimi-k3": ("openrouter", "moonshotai/kimi-k3"),
    "flash-lite": ("gemini", "gemini-3.5-flash-lite"),
    "3.5-flash-lite": ("gemini", "gemini-3.5-flash-lite"),
    "gemini-3.5-flash-lite": ("gemini", "gemini-3.5-flash-lite"),
    "hydrogen": ("gemini", "gemini-4-hydrogen"),
    "4-hydrogen": ("gemini", "gemini-4-hydrogen"),
    "gemini-4-hydrogen": ("gemini", "gemini-4-hydrogen"),
    "argon": ("gemini", "gemini-4-argon"),
    "4-argon": ("gemini", "gemini-4-argon"),
    "gemini-4-argon": ("gemini", "gemini-4-argon"),
    "carbon": ("gemini", "gemini-4-carbon"),
    "4-carbon": ("gemini", "gemini-4-carbon"),
    "gemini-4-carbon": ("gemini", "gemini-4-carbon"),
    "gemini-3-flash-preview": ("gemini", "gemini-3-flash-preview"),
    "gemini-3.1-pro-preview": ("gemini", "gemini-3.1-pro-preview"),
    "qwen": ("ollama", "qwen3.5:2b"),
    "qwen-3.5-2b": ("ollama", "qwen3.5:2b"),
    "qwen3.5:2b": ("ollama", "qwen3.5:2b"),
    "qwen3.5:0.8b": ("ollama", "qwen3.5:0.8b"),
    "gemma4:31b-cloud": ("ollama", "gemma4:31b-cloud"),
    "nemotron-3-ultra:cloud": ("ollama", "nemotron-3-ultra:cloud"),
    "minimax-m3:cloud": ("ollama", "minimax-m3:cloud"),
}


class LLMRouter:
    """Orchestre les roles LLM, le changement a chaud, le fallback et la delegation multimodale."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        roles: dict[str, RoleRoute] | None = None,
        daily_budget_usd: float = 10.0,
        usage_logger: Callable[[str, Usage], None] | None = None,
        daily_spend_getter: Callable[[], float] | None = None,
        daily_provider_spend_getter: Callable[[str], float] | None = None,
    ):
        self.providers = providers
        self.thinking_mode: str = "auto"
        self.roles: dict[str, RoleRoute] = {
            k: RoleRoute(
                v.role_name,
                v.model,
                v.provider,
                list(v.fallback),
                normalize_thinking_mode(v.thinking_mode),
            )
            for k, v in (roles or DEFAULT_ROLE_ROUTES).items()
        }
        self.daily_budget_usd = daily_budget_usd
        self.usage_logger = usage_logger
        self.daily_spend_getter = daily_spend_getter
        self.daily_provider_spend_getter = daily_provider_spend_getter
        self._in_memory_spend_usd = 0.0
        self._in_memory_spend_by_provider: dict[str, float] = {}
        self.capabilities: dict[str, ModelCapabilities] = {}
        self.capabilities.update(DEFAULT_KNOWN_OPENROUTER_MODELS)
        self.capabilities.update(DEFAULT_KNOWN_GEMINI_MODELS)
        self.capabilities.update(DEFAULT_KNOWN_OLLAMA_MODELS)

    def set_thinking_mode(self, mode: str) -> str:
        """Définit le mode de raisonnement (auto, off, low, medium, high) sur les providers."""
        clean = normalize_thinking_mode(mode)
        self.thinking_mode = clean
        if "agent" in self.roles:
            self.roles["agent"].thinking_mode = clean
        for provider in self.providers.values():
            setattr(provider, "thinking_mode", clean)
        return clean

    def set_role_model(
        self,
        role_name: str,
        model_or_alias: str,
        provider: str | None = None,
        thinking_mode: str | None = None,
    ) -> RoleRoute:
        """Change a chaud le modele (et optionnellement le raisonnement) d'un role."""
        raw = model_or_alias.strip()
        key = raw.lower()
        if key.startswith("ollama/"):
            provider = "ollama"
            model_or_alias = raw.split("/", 1)[1]
        elif key in MODEL_ALIASES:
            resolved_provider, resolved_model = MODEL_ALIASES[key]
            provider = (
                resolved_provider
                if provider in (None, "openrouter", "gemini") and resolved_provider == "ollama"
                else (provider or resolved_provider)
            )
            model_or_alias = resolved_model
        elif "/" in raw and not raw.startswith("models/"):
            provider = "openrouter"
            model_or_alias = raw
        elif ":" in raw or raw in DEFAULT_KNOWN_OLLAMA_MODELS:
            provider = "ollama"
            model_or_alias = raw
        elif provider is None:
            provider = "gemini"

        if provider not in self.providers and provider != "openrouter":
            if "openrouter" in self.providers:
                provider = "openrouter"
            else:
                provider = next(iter(self.providers.keys()))

        current = self.roles.get(role_name)
        fallback = list(current.fallback) if current else []
        effective_thinking = (
            normalize_thinking_mode(thinking_mode)
            if thinking_mode is not None
            else (current.thinking_mode if current else "auto")
        )
        route = RoleRoute(
            role_name=role_name,
            model=model_or_alias,
            provider=provider,
            fallback=fallback,
            thinking_mode=effective_thinking,
        )
        self.roles[role_name] = route
        return route

    def get_role_route(self, role_name: str) -> RoleRoute:
        return self.roles.get(role_name) or self.roles["agent"]

    @staticmethod
    def _is_candidate_trusted(prov: LLMProvider | None, model_id: str) -> bool:
        if prov is None or not getattr(prov, "trusted_for_highly_private", False):
            return False
        checker = getattr(prov, "is_model_trusted", None)
        if callable(checker):
            return bool(checker(model_id))
        return True

    def is_role_provider_trusted_for_highly_private(self, role_name: str = "agent") -> bool:
        route = self.get_role_route(role_name)
        prov = self.providers.get(route.provider)
        return self._is_candidate_trusted(prov, route.model)

    def get_provider_spend_today(self, provider_name: str) -> float:
        if self.daily_provider_spend_getter is not None:
            return float(self.daily_provider_spend_getter(provider_name))
        if self.daily_spend_getter is not None:
            return float(self.daily_spend_getter())
        return float(self._in_memory_spend_by_provider.get(provider_name, 0.0))

    def _is_provider_over_budget(self, provider_name: str) -> tuple[bool, float]:
        spent = self.get_provider_spend_today(provider_name)
        if self.daily_budget_usd > 0 and spent >= self.daily_budget_usd:
            return True, spent
        return False, spent

    def _filter_candidates_by_budget(
        self, candidates: list[tuple[LLMProvider, str]]
    ) -> list[tuple[LLMProvider, str]]:
        """Exclut les fournisseurs ayant depasse leur plafond journalier, ou leve BudgetExceededError."""
        allowed: list[tuple[LLMProvider, str]] = []
        last_blocked: tuple[str, float] | None = None
        for provider, model_id in candidates:
            over, spent = self._is_provider_over_budget(provider.name)
            if over:
                last_blocked = (provider.name, spent)
                continue
            allowed.append((provider, model_id))
        if not allowed and last_blocked is not None:
            prov_name, spent = last_blocked
            raise BudgetExceededError(spent, self.daily_budget_usd, provider=prov_name)
        return allowed

    def _record_usage(
        self, role_name: str, usage: Usage | None, *, fallback_provider: str | None = None
    ) -> None:
        if not usage:
            return
        self._in_memory_spend_usd += usage.cost_usd
        prov = (usage.provider or fallback_provider or "").strip() or "unknown"
        self._in_memory_spend_by_provider[prov] = (
            self._in_memory_spend_by_provider.get(prov, 0.0) + usage.cost_usd
        )
        if self.usage_logger:
            self.usage_logger(role_name, usage)

    def _model_supports_vision(self, model_id: str) -> bool:
        cap = self.capabilities.get(model_id)
        if cap is not None:
            return cap.supports_vision
        return (
            "gemini" in model_id.lower()
            or "vision" in model_id.lower()
            or "minimax" in model_id.lower()
        )

    async def _delegate_images_to_vision_role(self, messages: list[Message]) -> list[Message]:
        """Si le modele actif ne gere pas les images, decrit les images via le role vision."""
        has_images = any(bool(m.images) for m in messages)
        if not has_images:
            return messages

        rewritten: list[Message] = []
        for msg in messages:
            if not msg.images:
                rewritten.append(msg)
                continue
            vision_prompt = Message(
                role=Role.USER,
                content=(
                    "Decris factuellement et de maniere exhaustive le contenu de cette image "
                    "(texte visible, interface, erreurs, objets) pour l'agent principal."
                ),
                images=msg.images,
            )
            vision_res = await self.complete(role="vision", messages=[vision_prompt])
            desc = vision_res.text.strip()
            wrapped_desc = wrap_untrusted_content(
                "vision_delegation", f"[Description visuelle : {desc}]"
            )
            combined_content = f"{msg.content}\n\n{wrapped_desc}".strip()
            rewritten.append(
                Message(
                    id=msg.id,
                    role=msg.role,
                    content=combined_content,
                    images=[],
                    tool_calls=msg.tool_calls,
                    tool_results=msg.tool_results,
                    contains_untrusted=True,
                    created_at=msg.created_at,
                )
            )
        return rewritten

    @staticmethod
    def _is_provider_configured(prov: LLMProvider | None) -> bool:
        if prov is None:
            return False
        api_key = getattr(prov, "api_key", None)
        if api_key is None:
            return True
        if str(api_key).strip():
            return True
        return bool(getattr(prov, "vertexai", False) and getattr(prov, "project", None))

    @staticmethod
    def _infer_provider_for_model(model_id: str) -> str:
        # Toujours verifier "/" avant ":" pour ne pas router les slugs OpenRouter :free/:thinking vers Ollama
        if "/" in model_id and not model_id.startswith("models/"):
            return "openrouter"
        if ":" in model_id or model_id in DEFAULT_KNOWN_OLLAMA_MODELS:
            return "ollama"
        return "gemini"

    def _candidate_chain(
        self,
        route: RoleRoute,
        *,
        require_trusted: bool = False,
        allow_global_fallbacks: bool = True,
    ) -> list[tuple[LLMProvider, str]]:
        chain: list[tuple[LLMProvider, str]] = []
        primary_prov = self.providers.get(route.provider)
        primary_expected = self._infer_provider_for_model(route.model)
        real_providers = {"gemini", "openrouter", "ollama"}
        is_primary_compatible = (
            route.provider not in real_providers
            or route.provider == primary_expected
            or (route.provider == "openrouter" and primary_expected == "gemini")
        )
        if self._is_provider_configured(primary_prov) and is_primary_compatible:
            chain.append((primary_prov, route.model))  # type: ignore[arg-type]
        for fb_model in route.fallback:
            fb_prov_name = self._infer_provider_for_model(fb_model)
            fb_prov = self.providers.get(fb_prov_name)
            if not self._is_provider_configured(fb_prov):
                if (
                    primary_prov is not None
                    and route.provider == "openrouter"
                    and fb_prov_name == "gemini"
                    and self._is_provider_configured(primary_prov)
                ):
                    fb_prov = primary_prov
                else:
                    fb_prov = None
            if fb_prov and (fb_prov, fb_model) not in chain:
                chain.append((fb_prov, fb_model))
        if allow_global_fallbacks:
            or_prov = self.providers.get("openrouter")
            if self._is_provider_configured(or_prov) and or_prov is not None:
                if (or_prov, "deepseek/deepseek-v4.1-flash-1017") not in chain:
                    chain.append((or_prov, "deepseek/deepseek-v4.1-flash-1017"))
            gem_prov = self.providers.get("gemini")
            if self._is_provider_configured(gem_prov) and gem_prov is not None:
                if (gem_prov, "gemini-3.5-flash-lite") not in chain:
                    chain.append((gem_prov, "gemini-3.5-flash-lite"))
        if require_trusted:
            chain = [(prov, mid) for prov, mid in chain if self._is_candidate_trusted(prov, mid)]
        return chain

    def _resolve_route(
        self,
        role: str,
        model_override: str | None = None,
        provider_override: str | None = None,
        fallback_models_override: list[str] | None = None,
    ) -> RoleRoute:
        base = self.get_role_route(role)
        if not model_override:
            if fallback_models_override is None and provider_override is None:
                return base
            return RoleRoute(
                role_name=base.role_name,
                model=base.model,
                provider=provider_override or base.provider,
                fallback=(
                    list(fallback_models_override)
                    if fallback_models_override is not None
                    else list(base.fallback)
                ),
                thinking_mode=base.thinking_mode,
            )
        raw = model_override.strip()
        key = raw.lower()
        provider = provider_override
        resolved_model = raw
        if key.startswith("ollama/"):
            provider = "ollama"
            resolved_model = raw.split("/", 1)[1]
        elif key in MODEL_ALIASES:
            alias_prov, alias_model = MODEL_ALIASES[key]
            provider = provider or alias_prov
            resolved_model = alias_model
        elif "/" in raw and not raw.startswith("models/"):
            provider = provider or "openrouter"
        elif ":" in raw or raw in DEFAULT_KNOWN_OLLAMA_MODELS:
            provider = "ollama"
        elif provider is None:
            provider = "gemini"

        if provider not in self.providers:
            if "openrouter" in self.providers and "/" in resolved_model:
                provider = "openrouter"
            elif self.providers:
                provider = next(iter(self.providers.keys()))

        fb = (
            list(fallback_models_override)
            if fallback_models_override is not None
            else list(base.fallback)
        )
        return RoleRoute(
            role_name=role,
            model=resolved_model,
            provider=provider or base.provider,
            fallback=fb,
            thinking_mode=base.thinking_mode,
        )

    @staticmethod
    def _openrouter_fallback_slugs(fallback: list[str]) -> list[str] | None:
        slugs = [m for m in fallback if "/" in m and not m.startswith("models/")]
        return slugs or None

    def _effective_thinking_mode(self, route: RoleRoute) -> str:
        return route.thinking_mode if route.thinking_mode != "auto" else self.thinking_mode

    async def complete(
        self,
        role: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        *,
        model_override: str | None = None,
        provider_override: str | None = None,
        fallback_models_override: list[str] | None = None,
        timeout_s: float | None = None,
        require_trusted: bool = False,
    ) -> StreamDelta:
        route = self._resolve_route(
            role,
            model_override=model_override,
            provider_override=provider_override,
            fallback_models_override=fallback_models_override,
        )

        # Delegation multimodale transparente si le modele du role ne supporte pas la vision
        effective_messages = messages
        if role != "vision" and not self._model_supports_vision(route.model):
            effective_messages = await self._delegate_images_to_vision_role(messages)

        allow_global = fallback_models_override is None or len(fallback_models_override) > 0
        candidates = self._candidate_chain(
            route, require_trusted=require_trusted, allow_global_fallbacks=allow_global
        )
        if not candidates:
            if require_trusted:
                raise ProviderError(
                    f"Aucun fournisseur de confiance disponible pour les donnees tres privees (role '{role}')."
                )
            raise ProviderError(f"Aucun fournisseur configure pour le role '{role}'.")
        candidates = self._filter_candidates_by_budget(candidates)

        effective_thinking = self._effective_thinking_mode(route)
        last_err: Exception | None = None
        for provider, model_id in candidates:
            try:
                call_kwargs: dict[str, Any] = {
                    "model": model_id,
                    "messages": effective_messages,
                    "tools": tools,
                    "fallback_models": (
                        self._openrouter_fallback_slugs(route.fallback)
                        if provider.name == "openrouter"
                        else None
                    ),
                    "temperature": temperature,
                }
                if _accepts_thinking_mode(provider.complete):
                    call_kwargs["thinking_mode"] = effective_thinking
                if timeout_s is not None:
                    async with asyncio.timeout(timeout_s):
                        delta = await provider.complete(**call_kwargs)
                else:
                    delta = await provider.complete(**call_kwargs)
                self._record_usage(role, delta.usage, fallback_provider=provider.name)
                return delta
            except Exception as e:
                last_err = e
                continue

        raise ProviderError(
            f"Echec de tous les modeles pour le role '{role}' : {last_err}"
        ) from last_err

    async def stream(
        self,
        role: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        *,
        model_override: str | None = None,
        provider_override: str | None = None,
        fallback_models_override: list[str] | None = None,
        timeout_s: float | None = None,
        require_trusted: bool = False,
    ) -> AsyncIterator[StreamDelta]:
        route = self._resolve_route(
            role,
            model_override=model_override,
            provider_override=provider_override,
            fallback_models_override=fallback_models_override,
        )
        effective_messages = messages
        if role != "vision" and not self._model_supports_vision(route.model):
            effective_messages = await self._delegate_images_to_vision_role(messages)

        allow_global = fallback_models_override is None or len(fallback_models_override) > 0
        candidates = self._candidate_chain(
            route, require_trusted=require_trusted, allow_global_fallbacks=allow_global
        )
        if not candidates:
            if require_trusted:
                raise ProviderError(
                    f"Aucun fournisseur de confiance disponible pour les donnees tres privees (role '{role}')."
                )
            raise ProviderError(f"Aucun fournisseur configure pour le role '{role}'.")
        candidates = self._filter_candidates_by_budget(candidates)

        effective_thinking = self._effective_thinking_mode(route)
        last_err: Exception | None = None
        for provider, model_id in candidates:
            try:
                call_kwargs: dict[str, Any] = {
                    "model": model_id,
                    "messages": effective_messages,
                    "tools": tools,
                    "fallback_models": (
                        self._openrouter_fallback_slugs(route.fallback)
                        if provider.name == "openrouter"
                        else None
                    ),
                    "temperature": temperature,
                }
                if _accepts_thinking_mode(provider.stream):
                    call_kwargs["thinking_mode"] = effective_thinking
                if timeout_s is not None:
                    async with asyncio.timeout(timeout_s):
                        async for delta in provider.stream(**call_kwargs):
                            if delta.usage:
                                self._record_usage(
                                    role, delta.usage, fallback_provider=provider.name
                                )
                            yield delta
                else:
                    async for delta in provider.stream(**call_kwargs):
                        if delta.usage:
                            self._record_usage(role, delta.usage, fallback_provider=provider.name)
                        yield delta
                return
            except Exception as e:
                last_err = e
                continue

        raise ProviderError(
            f"Echec du streaming sur tous les modeles ({role}) : {last_err}"
        ) from last_err
