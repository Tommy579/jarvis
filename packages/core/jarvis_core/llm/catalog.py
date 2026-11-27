"""Catalogue strict des modeles low-cost autorises pour l'orchestration autonome et les sous-agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelProfile:
    """Profil d'un modele autorise avec cout indicatif et forces par type de tache."""

    model_id: str
    provider: str
    tier: str  # "ultra_cheap" | "cheap" | "escalation"
    input_cost_per_1m: float
    output_cost_per_1m: float
    supports_vision: bool = False
    supports_tools: bool = True
    strengths: str = ""
    best_for: tuple[str, ...] = field(default_factory=tuple)


DEFAULT_AUTONOMOUS_MODELS: tuple[ModelProfile, ...] = (
    ModelProfile(
        model_id="gemini-4-carbon",
        provider="gemini",
        tier="ultra_cheap",
        input_cost_per_1m=0.075,
        output_cost_per_1m=0.30,
        supports_vision=True,
        strengths="Ultra economique et tres rapide : triage, extraction, verification legere, formatage.",
        best_for=("triage", "extract", "verify_light", "general"),
    ),
    ModelProfile(
        model_id="deepseek/deepseek-v4.1-flash-1017",
        provider="openrouter",
        tier="ultra_cheap",
        input_cost_per_1m=0.15,
        output_cost_per_1m=0.60,
        supports_vision=False,
        strengths="Code, bash, debug, logique structuree et appels d'outils rapides.",
        best_for=("code", "coder", "system", "verifier", "general"),
    ),
    ModelProfile(
        model_id="minimax/minimax-m3",
        provider="openrouter",
        tier="cheap",
        input_cost_per_1m=0.20,
        output_cost_per_1m=0.80,
        supports_vision=True,
        strengths="Agentique multi-etapes, long contexte et vision.",
        best_for=("research", "browser", "vision", "agentic"),
    ),
    ModelProfile(
        model_id="z-ai/glm-5.3",
        provider="openrouter",
        tier="cheap",
        input_cost_per_1m=0.25,
        output_cost_per_1m=0.80,
        supports_vision=False,
        strengths="Synthese, redaction, planification et repli fiable sur raisonnement.",
        best_for=("writer", "research", "code", "general"),
    ),
    ModelProfile(
        model_id="moonshotai/kimi-k3",
        provider="openrouter",
        tier="cheap",
        input_cost_per_1m=0.25,
        output_cost_per_1m=0.90,
        supports_vision=False,
        strengths="Analyse de longs documents, recherche approfondie et synthese claire.",
        best_for=("research", "writer", "analysis"),
    ),
    ModelProfile(
        model_id="gemini-4-argon",
        provider="gemini",
        tier="cheap",
        input_cost_per_1m=0.30,
        output_cost_per_1m=1.20,
        supports_vision=True,
        strengths="Multimodal natif, navigateur AXTree, orchestration et verification robuste.",
        best_for=("browser", "orchestrator", "verifier", "vision", "general"),
    ),
    ModelProfile(
        model_id="gemini-4-hydrogen",
        provider="gemini",
        tier="escalation",
        input_cost_per_1m=1.25,
        output_cost_per_1m=5.00,
        supports_vision=True,
        strengths="Escalade ultime pour debloquer un sous-agent coince ou tache critique complexe.",
        best_for=("escalation", "verifier", "deep"),
    ),
    ModelProfile(
        model_id="qwen3.5:2b",
        provider="ollama",
        tier="local_fallback",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        supports_vision=False,
        strengths="Secours local ultra-leger (faible capacite : uniquement si vraiment necessaire ou hors-ligne).",
        best_for=("local_fallback",),
    ),
)

_ALIAS_MAP: dict[str, str] = {
    "deepseek": "deepseek/deepseek-v4.1-flash-1017",
    "deepseek-v4.1": "deepseek/deepseek-v4.1-flash-1017",
    "deepseek-v4.1-1017": "deepseek/deepseek-v4.1-flash-1017",
    "deepseek/deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash-1017",
    "glm": "z-ai/glm-5.3",
    "glm-5.3": "z-ai/glm-5.3",
    "minimax": "minimax/minimax-m3",
    "minimax-m3": "minimax/minimax-m3",
    "kimi": "moonshotai/kimi-k3",
    "kimi-k3": "moonshotai/kimi-k3",
    "carbon": "gemini-4-carbon",
    "4-carbon": "gemini-4-carbon",
    "argon": "gemini-4-argon",
    "4-argon": "gemini-4-argon",
    "hydrogen": "gemini-4-hydrogen",
    "4-hydrogen": "gemini-4-hydrogen",
    "qwen-3.5-2b": "qwen3.5:2b",
    "qwen3.5-2b": "qwen3.5:2b",
    "qwen3.5:2b": "qwen3.5:2b",
}


class ModelSelectionCatalog:
    """Gere la liste blanche des modeles low-cost et le routage par profil de tache."""

    def __init__(
        self,
        profiles: list[ModelProfile] | None = None,
        allowed_override: list[str] | None = None,
    ) -> None:
        base = list(profiles or DEFAULT_AUTONOMOUS_MODELS)
        if allowed_override:
            norm_allowed = {self.normalize_id(m) for m in allowed_override if m.strip()}
            filtered = [p for p in base if p.model_id in norm_allowed]
            # Ajouter aussi tout modele explicitement autorise par l'utilisateur meme absent du preset
            known_ids = {p.model_id for p in filtered}
            for raw_id in norm_allowed - known_ids:
                prov = "openrouter" if "/" in raw_id else "gemini"
                filtered.append(
                    ModelProfile(
                        model_id=raw_id,
                        provider=prov,
                        tier="cheap",
                        input_cost_per_1m=0.30,
                        output_cost_per_1m=1.20,
                        supports_vision=("gemini" in raw_id or "minimax" in raw_id),
                        strengths="Modele autorise par configuration utilisateur.",
                    )
                )
            base = filtered or base

        self._profiles: dict[str, ModelProfile] = {p.model_id: p for p in base}
        self._order: list[str] = [p.model_id for p in base]

    @classmethod
    def from_yaml(cls, config_dir: Path | None) -> ModelSelectionCatalog:
        if config_dir is None:
            return cls()
        models_file = config_dir / "models.yaml"
        if not models_file.exists():
            return cls()
        try:
            raw: dict[str, Any] = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        pool = raw.get("autonomous_pool")
        if isinstance(pool, list) and pool:
            return cls(allowed_override=[str(item) for item in pool])
        return cls()

    @staticmethod
    def normalize_id(model_or_alias: str) -> str:
        raw = (model_or_alias or "").strip()
        key = raw.lower()
        return _ALIAS_MAP.get(key, raw)

    @property
    def allowed_models(self) -> list[str]:
        return list(self._order)

    def get(self, model_or_alias: str) -> ModelProfile | None:
        norm = self.normalize_id(model_or_alias)
        return self._profiles.get(norm)

    def is_allowed(self, model_or_alias: str) -> bool:
        return self.get(model_or_alias) is not None

    def available_profiles(self, router: Any | None = None) -> list[ModelProfile]:
        """Filtre eventuellement selon les providers effectivement configures dans le router."""
        if router is None:
            return [self._profiles[m] for m in self._order]
        out: list[ModelProfile] = []
        for mid in self._order:
            prof = self._profiles[mid]
            prov = router.providers.get(prof.provider)
            if prov is not None and router._is_provider_configured(prov):
                out.append(prof)
        return out or [self._profiles[m] for m in self._order]

    def resolve_model(
        self,
        requested: str | None,
        *,
        role_profile: str = "general",
        require_vision: bool = False,
        router: Any | None = None,
    ) -> str:
        """Retourne un modele strictement dans l'allowlist et compatible avec le role/provider."""
        candidates = self.available_profiles(router)
        if require_vision:
            vision_cands = [p for p in candidates if p.supports_vision]
            if vision_cands:
                candidates = vision_cands

        if requested:
            norm = self.normalize_id(requested)
            for p in candidates:
                if p.model_id == norm:
                    return p.model_id

        # Sinon choisir le moins cher adapte au role_profile (hors local_fallback sauf demande explicite)
        matching = [p for p in candidates if role_profile in p.best_for]
        non_local = [p for p in candidates if p.tier != "local_fallback"]
        pool = matching or non_local or candidates
        cheapest = min(pool, key=lambda p: p.input_cost_per_1m + p.output_cost_per_1m)
        return cheapest.model_id

    def escalation_chain(
        self,
        current_model: str,
        *,
        require_vision: bool = False,
        router: Any | None = None,
    ) -> list[str]:
        """Retourne les modeles de remplacement / escalade disponibles dans l'allowlist."""
        candidates = self.available_profiles(router)
        if require_vision:
            vision_cands = [p for p in candidates if p.supports_vision]
            if vision_cands:
                candidates = vision_cands

        norm = self.normalize_id(current_model)
        # Ne jamais retrograder vers un petit modele local lors d'une escalade tant qu'il existe du cloud
        cloud_candidates = [p for p in candidates if p.tier != "local_fallback"]
        pool = cloud_candidates or candidates
        tier_order = {"ultra_cheap": 0, "cheap": 1, "escalation": 2, "local_fallback": 9}
        ordered = sorted(
            pool,
            key=lambda p: (
                tier_order.get(p.tier, 1),
                p.input_cost_per_1m + p.output_cost_per_1m,
            ),
        )
        return [p.model_id for p in ordered if p.model_id != norm]

    def describe_for_orchestrator(self, router: Any | None = None) -> str:
        """Description compacte des modeles low-cost disponibles pour le prompt de l'orchestrateur."""
        lines = ["Modèles low-cost autorisés (choisis toujours le moins cher adapté à la tâche) :"]
        for p in self.available_profiles(router):
            vision = "vision" if p.supports_vision else "texte"
            lines.append(
                f"- `{p.model_id}` ({p.provider}, {p.tier}, ${p.input_cost_per_1m:.3f}/${p.output_cost_per_1m:.2f} par 1M, {vision}) : {p.strengths}"
            )
        return "\n".join(lines)
