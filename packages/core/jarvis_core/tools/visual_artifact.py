"""Outil backend permettant à Jarvis de présenter une animation 2D Motion ou un modèle 3D synchronisé à sa voix."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any, Literal

from jarvis_shared.models import RiskLevel
from jarvis_shared.ws_protocol import (
    VisualArtifactPayload,
    VisualElementKind,
    VisualElementPayload,
    VisualStepPayload,
)
from pydantic import BaseModel, Field

from .registry import ToolRegistry, ToolSpec


def _clamp_pct(val: float, default: float = 50.0) -> float:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return round(max(4.0, min(96.0, num)), 2)


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
DEFAULT_ELEMENT_COLOR = "#38bdf8"


def _safe_color(value: str | None, default: str = DEFAULT_ELEMENT_COLOR) -> str:
    """N'accepte qu'une couleur hexadecimale.

    Une valeur libre finit dans `fill`/`stroke` SVG : `url(http://...)` y declencherait une
    requete sortante depuis le navigateur. Le modele ne doit pas pouvoir choisir cela.
    """
    candidate = (value or "").strip()
    return candidate if _HEX_COLOR_RE.match(candidate) else default


class VisualElementInput(BaseModel):
    id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Identifiant unique de l'élément (ex: 'rpi', 'led_1', 'wire_gpio17').",
    )
    kind: VisualElementKind = Field(
        default="node",
        description=(
            "Type de composant 2D/3D : 'board' (carte Raspberry Pi/Arduino), 'chip', 'led', 'resistor', "
            "'sensor', 'button', 'wire' (câble), 'node', 'box', 'cylinder', 'sphere', 'arrow', 'label', 'svg_path'."
        ),
    )
    label: str = Field(
        ..., min_length=1, max_length=120, description="Nom lisible affiché sur le schéma."
    )
    sublabel: str | None = Field(
        default=None, max_length=120, description="Sous-titre technique (ex: '330 Ω', 'GPIO 17')."
    )
    color: str = Field(
        default="#38bdf8",
        max_length=32,
        description="Couleur hexadécimale (ex: '#ef4444', '#22c55e', '#38bdf8').",
    )
    x: float = Field(default=50.0, description="Position horizontale normalisée 0..100.")
    y: float = Field(default=50.0, description="Position verticale normalisée 0..100.")
    z: float = Field(default=0.0, description="Hauteur 3D optionnelle (-20..20).")
    width: float = Field(default=14.0, description="Largeur normalisée (4..45).")
    height: float = Field(default=10.0, description="Hauteur normalisée (4..45).")
    from_id: str | None = Field(
        default=None, max_length=64, description="ID source pour 'wire' ou 'arrow'."
    )
    to_id: str | None = Field(
        default=None, max_length=64, description="ID cible pour 'wire' ou 'arrow'."
    )
    from_pin: str | None = Field(
        default=None, max_length=120, description="Broche de départ (ex: 'GPIO 17 (Pin 11)')."
    )
    to_pin: str | None = Field(
        default=None, max_length=120, description="Broche d'arrivée (ex: 'Anode (+)')."
    )
    svg_path: str | None = Field(
        default=None, max_length=2000, description="Chemin SVG optionnel pour 'svg_path'."
    )


class VisualStepInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=160, description="Titre court de l'étape.")
    spoken_text: str = Field(
        ...,
        min_length=1,
        max_length=600,
        description="Phrase complète et concise que Jarvis prononce à voix haute pendant cette étape.",
    )
    annotation: str = Field(
        default="",
        max_length=200,
        description="Note technique courte affichée en badge sur le canevas.",
    )
    reveal_elements: list[str] = Field(
        default_factory=list,
        max_length=80,
        description="IDs des composants/câbles qui apparaissent à cette étape.",
    )
    highlight_elements: list[str] = Field(
        default_factory=list,
        max_length=80,
        description="IDs des éléments mis en avant (focus caméra / halo) à cette étape.",
    )
    active_connections: list[str] = Field(
        default_factory=list,
        max_length=80,
        description="IDs des câbles/flèches où circule un flux animé à cette étape.",
    )


class PresentVisualArtifactArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=160, description="Titre du montage ou schéma.")
    subtitle: str = Field(default="", max_length=240, description="Sous-titre bref.")
    mode: Literal["2d", "3d"] = Field(
        default="3d",
        description="Mode initial : '3d' pour un montage matériel/circuit ou objet 3D, '2d' pour un schéma/flux/motion.",
    )
    category: Literal["hardware", "architecture", "algorithm", "physics", "custom"] = Field(
        default="hardware",
        description="Catégorie du visuel.",
    )
    elements: list[VisualElementInput] = Field(
        ...,
        min_length=1,
        max_length=80,
        description="Liste des composants, blocs et connexions du montage.",
    )
    steps: list[VisualStepInput] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="Étapes séquentielles synchronisées avec la voix.",
    )
    auto_play: bool = Field(
        default=True,
        description="Lance automatiquement la narration vocale étape par étape.",
    )


class VisualArtifactToolkit:
    """Expose l'outil `present_visual_artifact` pour afficher des animations 2D/3D synchronisées."""

    def __init__(self, on_ws_event: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
        self.on_ws_event = on_ws_event

    def register_all(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolSpec(
                name="present_visual_artifact",
                description=(
                    "Affiche une fenêtre dédiée avec une animation 2D Motion ou un modèle 3D interactif, "
                    "synchronisée étape par étape avec la voix de Jarvis (ex: montage Raspberry Pi, câblage GPIO, "
                    "circuit électronique, schéma d'architecture ou explication visuelle). "
                    "À utiliser UNIQUEMENT avec parcimonie lorsque la demande porte sur du matériel/électronique, "
                    "un mécanisme physique, une architecture complexe ou si l'utilisateur demande un schéma/animation."
                ),
                risk=RiskLevel.READ_ONLY,
                handler=self.present_visual_artifact,
                args_model=PresentVisualArtifactArgs,
            )
        )

    async def present_visual_artifact(self, **raw_args: Any) -> dict[str, Any]:
        args = PresentVisualArtifactArgs.model_validate(raw_args)
        elements = [
            VisualElementPayload(
                id=el.id,
                kind=el.kind,
                label=el.label,
                sublabel=el.sublabel,
                color=_safe_color(el.color),
                x=_clamp_pct(el.x),
                y=_clamp_pct(el.y),
                z=max(-25.0, min(25.0, float(el.z))),
                width=max(4.0, min(50.0, float(el.width))),
                height=max(4.0, min(50.0, float(el.height))),
                from_id=el.from_id,
                to_id=el.to_id,
                from_pin=el.from_pin,
                to_pin=el.to_pin,
                svg_path=el.svg_path,
            )
            for el in args.elements
        ]

        known_ids = {el.id for el in elements}
        revealed_so_far: set[str] = set()
        steps: list[VisualStepPayload] = []
        for idx, st in enumerate(args.steps):
            reveal = [eid for eid in st.reveal_elements if eid in known_ids]
            revealed_so_far.update(reveal)
            steps.append(
                VisualStepPayload(
                    index=idx,
                    title=st.title,
                    spoken_text=st.spoken_text,
                    annotation=st.annotation,
                    reveal_elements=reveal,
                    highlight_elements=[eid for eid in st.highlight_elements if eid in known_ids],
                    active_connections=[eid for eid in st.active_connections if eid in known_ids],
                )
            )

        # Si certains éléments n'ont été rattachés à aucune étape, les révéler à la première étape
        unassigned = [el.id for el in elements if el.id not in revealed_so_far]
        if unassigned and steps:
            steps[0].reveal_elements = list(dict.fromkeys([*steps[0].reveal_elements, *unassigned]))

        artifact = VisualArtifactPayload(
            id=f"art_{uuid.uuid4().hex[:10]}",
            title=args.title,
            subtitle=args.subtitle,
            mode=args.mode,
            category=args.category,
            elements=elements,
            steps=steps,
            auto_play=args.auto_play,
        )

        if self.on_ws_event is not None:
            self.on_ws_event("visual.artifact", artifact.model_dump())

        return {
            "ok": True,
            "artifact_id": artifact.id,
            "title": artifact.title,
            "mode": artifact.mode,
            "steps_count": len(steps),
            "elements_count": len(elements),
            "instruction": (
                "La fenêtre visuelle interactive est ouverte et va lire chaque étape à voix haute "
                "de façon synchronisée. Réponds maintenant à l'utilisateur en 1 seule phrase courte "
                "d'introduction sans répéter le détail des étapes."
            ),
        }
