"""Routeur LLM par role avec changement a chaud, fallback en cascade, delegation vision et suivi des couts."""

from __future__ import annotations

import asyncio
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

from .base import BudgetExceededError, LLMProvider, ProviderError
from .gemini import DEFAULT_KNOWN_GEMINI_MODELS
from .ollama import DEFAULT_KNOWN_OLLAMA_MODELS
from .openrouter import DEFAULT_KNOWN_OPENROUTER_MODELS


@dataclass
class RoleRoute:
    role_name: str
    model: str
    provider: str
    fallback: list[str] = field(default_factory=list)


DEFAULT_ROLE_ROUTES: dict[str, RoleRoute] = {
    "agent": RoleRoute(
        role_name="agent",
        model="deepseek/deepseek-v4.1-flash-1017",
        provider="openrouter",
        fallback=["z-ai/glm-5.3", "minimax/minimax-m3", "gemini-3-flash-preview"],
    ),
    "triage": RoleRoute(
        role_name="triage",
        model="gemini-3.5-flash-lite",
        provider="gemini",
        fallback=["gemini-3-flash-preview"],
    ),
    "vision": RoleRoute(
        role_name="vision",
        model="gemini-3.5-flash-lite",
        provider="gemini",
        fallback=["gemini-3-flash-preview"],
    ),
    "memory": RoleRoute(
        role_name="memory",
        model="deepseek/deepseek-v4.1-flash-1017",
        provider="openrouter",
        fallback=["gemini-3-flash-preview"],
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
        daily_budget_usd: float = 5.0,
        usage_logger: Callable[[str, Usage], None] | None = None,
        daily_spend_getter: Callable[[], float] | None = None,
    ):
        self.providers = providers
        self.thinking_mode: str = "auto"
        self.roles: dict[str, RoleRoute] = {
            k: RoleRoute(v.role_name, v.model, v.provider, list(v.fallback))
            for k, v in (roles or DEFAULT_ROLE_ROUTES).items()
        }
        self.daily_budget_usd = daily_budget_usd
        self.usage_logger = usage_logger
        self.daily_spend_getter = daily_spend_getter
        self._in_memory_spend_usd = 0.0
        self.capabilities: dict[str, ModelCapabilities] = {}
        self.capabilities.update(DEFAULT_KNOWN_OPENROUTER_MODELS)
        self.capabilities.update(DEFAULT_KNOWN_GEMINI_MODELS)
        self.capabilities.update(DEFAULT_KNOWN_OLLAMA_MODELS)

    def set_thinking_mode(self, mode: str) -> str:
        """Définit le mode de raisonnement (auto, off, low, medium, high) sur les providers."""
        allowed = {"auto", "off", "low", "medium", "high"}
        clean = mode.strip().lower()
        if clean not in allowed:
            clean = "auto"
        self.thinking_mode = clean
        for provider in self.providers.values():
            setattr(provider, "thinking_mode", clean)
        return clean

    async def validate_registry(self, strict: bool = False) -> dict[str, list[str]]:
        """Interroge les fournisseurs au demarrage pour verifier les slugs et capacites."""
        warnings: list[str] = []
        errors: list[str] = []
        for provider in self.providers.values():
            caps = await provider.fetch_available_models()
            self.capabilities.update(caps)

        for role_name, route in self.roles.items():
            cap = self.capabilities.get(route.model)
            if not cap:
                msg = f"Modele '{route.model}' du role '{role_name}' absent du catalogue connu."
                if strict:
                    errors.append(msg)
                else:
                    warnings.append(msg)
                continue
            if role_name == "agent" and not cap.supports_tools:
                warnings.append(f"Le modele '{route.model}' ne declare pas le support des outils.")
            if role_name == "vision" and not cap.supports_vision:
                warnings.append(f"Le modele vision '{route.model}' ne supporte pas les images.")

        return {"warnings": warnings, "errors": errors}

    def set_role_model(
        self, role_name: str, model_or_alias: str, provider: str | None = None
    ) -> RoleRoute:
        """Change a chaud le modele d'un role sans alterer l'historique de conversation."""
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
        elif ":" in raw or raw in DEFAULT_KNOWN_OLLAMA_MODELS:
            provider = "ollama"
            model_or_alias = raw
        elif provider is None:
            if "/" in raw and not raw.startswith("models/"):
                provider = "openrouter"
            else:
                provider = "gemini"

        if provider not in self.providers:
            # Repli sur tout fournisseur disponible (ex: openrouter pour google/gemini-3.5-flash-lite)
            if "openrouter" in self.providers:
                provider = "openrouter"
            else:
                provider = next(iter(self.providers.keys()))

        current = self.roles.get(role_name)
        fallback = list(current.fallback) if current else []
        route = RoleRoute(
            role_name=role_name,
            model=model_or_alias,
            provider=provider,
            fallback=fallback,
        )
        self.roles[role_name] = route
        return route

    def get_role_route(self, role_name: str) -> RoleRoute:
        return self.roles.get(role_name) or self.roles["agent"]

    def is_role_provider_trusted_for_highly_private(self, role_name: str = "agent") -> bool:
        route = self.get_role_route(role_name)
        prov = self.providers.get(route.provider)
        return bool(prov and prov.trusted_for_highly_private)

    def _check_budget(self) -> None:
        spent = self.daily_spend_getter() if self.daily_spend_getter else self._in_memory_spend_usd
        if self.daily_budget_usd > 0 and spent >= self.daily_budget_usd:
            raise BudgetExceededError(spent, self.daily_budget_usd)

    def _record_usage(self, role_name: str, usage: Usage | None) -> None:
        if not usage:
            return
        self._in_memory_spend_usd += usage.cost_usd
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
            combined_content = (
                f"{msg.content}\n\n"
                f'<untrusted source="vision_delegation">\n[Description visuelle : {desc}]\n</untrusted>'
            ).strip()
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

    def _candidate_chain(self, route: RoleRoute) -> list[tuple[LLMProvider, str]]:
        chain: list[tuple[LLMProvider, str]] = []
        primary_prov = self.providers.get(route.provider)
        if self._is_provider_configured(primary_prov):
            chain.append((primary_prov, route.model))  # type: ignore[arg-type]
        for fb_model in route.fallback:
            fb_prov_name = (
                "ollama"
                if (":" in fb_model or fb_model in DEFAULT_KNOWN_OLLAMA_MODELS)
                else "openrouter"
                if ("/" in fb_model and not fb_model.startswith("models/"))
                else "gemini"
            )
            fb_prov = self.providers.get(fb_prov_name)
            if not self._is_provider_configured(fb_prov):
                fb_prov = primary_prov if self._is_provider_configured(primary_prov) else None
            if fb_prov and (fb_prov, fb_model) not in chain:
                chain.append((fb_prov, fb_model))
        gem_prov = self.providers.get("gemini")
        if self._is_provider_configured(gem_prov) and gem_prov is not None:
            if (gem_prov, "gemini-3-flash-preview") not in chain:
                chain.append((gem_prov, "gemini-3-flash-preview"))
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
            return base
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
        elif ":" in raw or raw in DEFAULT_KNOWN_OLLAMA_MODELS:
            provider = "ollama"
        elif provider is None:
            if "/" in raw and not raw.startswith("models/"):
                provider = "openrouter"
            else:
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
        )

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
    ) -> StreamDelta:
        self._check_budget()
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

        candidates = self._candidate_chain(route)
        if not candidates:
            raise ProviderError(f"Aucun fournisseur configure pour le role '{role}'.")

        last_err: Exception | None = None
        for provider, model_id in candidates:
            try:
                if timeout_s is not None:
                    async with asyncio.timeout(timeout_s):
                        delta = await provider.complete(
                            model=model_id,
                            messages=effective_messages,
                            tools=tools,
                            fallback_models=route.fallback
                            if provider.name == "openrouter"
                            else None,
                            temperature=temperature,
                        )
                else:
                    delta = await provider.complete(
                        model=model_id,
                        messages=effective_messages,
                        tools=tools,
                        fallback_models=route.fallback if provider.name == "openrouter" else None,
                        temperature=temperature,
                    )
                self._record_usage(role, delta.usage)
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
    ) -> AsyncIterator[StreamDelta]:
        self._check_budget()
        route = self._resolve_route(
            role,
            model_override=model_override,
            provider_override=provider_override,
            fallback_models_override=fallback_models_override,
        )
        effective_messages = messages
        if role != "vision" and not self._model_supports_vision(route.model):
            effective_messages = await self._delegate_images_to_vision_role(messages)

        candidates = self._candidate_chain(route)
        if not candidates:
            raise ProviderError(f"Aucun fournisseur configure pour le role '{role}'.")

        last_err: Exception | None = None
        for provider, model_id in candidates:
            try:
                if timeout_s is not None:
                    async with asyncio.timeout(timeout_s):
                        async for delta in provider.stream(
                            model=model_id,
                            messages=effective_messages,
                            tools=tools,
                            fallback_models=route.fallback
                            if provider.name == "openrouter"
                            else None,
                            temperature=temperature,
                        ):
                            if delta.usage:
                                self._record_usage(role, delta.usage)
                            yield delta
                else:
                    async for delta in provider.stream(
                        model=model_id,
                        messages=effective_messages,
                        tools=tools,
                        fallback_models=route.fallback if provider.name == "openrouter" else None,
                        temperature=temperature,
                    ):
                        if delta.usage:
                            self._record_usage(role, delta.usage)
                        yield delta
                return
            except Exception as e:
                last_err = e
                continue

        raise ProviderError(
            f"Echec du streaming sur tous les modeles ({role}) : {last_err}"
        ) from last_err
