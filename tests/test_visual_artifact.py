"""Tests de l'outil present_visual_artifact et de l'émission WebSocket associée."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jarvis_core.runtime import build_runtime
from jarvis_core.tools.visual_artifact import PresentVisualArtifactArgs, VisualArtifactToolkit
from jarvis_shared.models import ToolCall
from jarvis_shared.ws_protocol import ServerWsEvent
from pydantic import TypeAdapter, ValidationError


@pytest.mark.asyncio
async def test_present_visual_artifact_raspberry_pi_led(tmp_path: Path) -> None:
    emitted: list[tuple[str, dict[str, Any]]] = []

    def _on_ws_event(event_type: str, payload: dict[str, Any]) -> None:
        emitted.append((event_type, payload))

    rt = build_runtime(
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).resolve().parents[1] / "config",
        allow_offline=True,
        on_ws_event=_on_ws_event,
    )

    spec = rt.tool_registry.get("present_visual_artifact")
    assert spec is not None

    args = {
        "title": "Montage LED + Résistance sur Raspberry Pi",
        "subtitle": "GPIO 17 (Pin 11) vers GND (Pin 6)",
        "mode": "3d",
        "category": "hardware",
        "elements": [
            {
                "id": "rpi",
                "kind": "board",
                "label": "Raspberry Pi",
                "x": 25,
                "y": 55,
                "width": 24,
                "height": 28,
            },
            {
                "id": "res",
                "kind": "resistor",
                "label": "Résistance",
                "sublabel": "330 Ω",
                "x": 55,
                "y": 42,
            },
            {
                "id": "led",
                "kind": "led",
                "label": "LED Rouge",
                "color": "#ef4444",
                "x": 78,
                "y": 55,
            },
            {
                "id": "w1",
                "kind": "wire",
                "label": "GPIO 17",
                "from_id": "rpi",
                "to_id": "res",
                "from_pin": "GPIO 17",
                "to_pin": "Entrée",
                "color": "#f59e0b",
            },
            {
                "id": "w2",
                "kind": "wire",
                "label": "Anode",
                "from_id": "res",
                "to_id": "led",
                "color": "#ef4444",
            },
            {
                "id": "w3",
                "kind": "wire",
                "label": "GND",
                "from_id": "led",
                "to_id": "rpi",
                "from_pin": "Cathode (-)",
                "to_pin": "GND (Pin 6)",
                "color": "#38bdf8",
            },
        ],
        "steps": [
            {
                "title": "Préparer le Raspberry Pi",
                "spoken_text": "On commence par repérer la broche GPIO 17 et une broche de masse sur le Raspberry Pi.",
                "annotation": "Pin 11 = GPIO 17 · Pin 6 = GND",
                "reveal_elements": ["rpi"],
                "highlight_elements": ["rpi"],
            },
            {
                "title": "Placer la résistance 330 Ω",
                "spoken_text": "Relie la broche GPIO 17 à la résistance de 330 ohms pour limiter le courant.",
                "reveal_elements": ["res", "w1"],
                "highlight_elements": ["res", "w1"],
                "active_connections": ["w1"],
            },
            {
                "title": "Connecter la LED et la masse",
                "spoken_text": "Branche la patte longue de la LED après la résistance et ramène la patte courte vers GND.",
                "reveal_elements": ["led", "w2", "w3"],
                "highlight_elements": ["led"],
                "active_connections": ["w1", "w2", "w3"],
            },
        ],
    }

    res = await rt.tool_registry.execute(
        ToolCall(id="tc-vis-1", name="present_visual_artifact", arguments=args)
    )
    assert res.is_error is False
    assert res.output["ok"] is True
    assert res.output["steps_count"] == 3
    assert res.output["elements_count"] == 6

    assert len(emitted) == 1
    ev_type, ev_payload = emitted[0]
    assert ev_type == "visual.artifact"

    adapter = TypeAdapter(ServerWsEvent)
    validated = adapter.validate_python({"type": ev_type, "payload": ev_payload})
    assert validated.type == "visual.artifact"
    assert len(validated.payload.steps) == 3
    assert validated.payload.steps[2].active_connections == ["w1", "w2", "w3"]


@pytest.mark.asyncio
async def test_colors_are_sanitized_and_positions_clamped() -> None:
    emitted: list[dict[str, Any]] = []
    toolkit = VisualArtifactToolkit(on_ws_event=lambda _event, payload: emitted.append(payload))

    await toolkit.present_visual_artifact(
        title="Test",
        elements=[
            {
                "id": "a",
                "label": "A",
                "color": "url(http://evil.test/x.png)",
                "x": 999,
                "y": -5,
                "z": 99,
            },
            {"id": "b", "label": "B", "color": "#ef4444"},
        ],
        steps=[
            {
                "title": "Etape 1",
                "spoken_text": "Voici A.",
                "reveal_elements": ["a", "inconnu"],
                "highlight_elements": ["inconnu"],
            }
        ],
    )

    payload = emitted[0]
    first = payload["elements"][0]
    assert first["color"] == "#38bdf8"
    assert first["x"] == 96.0
    assert first["y"] == 4.0
    assert first["z"] == 25.0
    assert payload["elements"][1]["color"] == "#ef4444"
    # "b" n'est reference par aucune etape : l'outil le revele a la premiere etape.
    assert payload["steps"][0]["reveal_elements"] == ["a", "b"]
    assert payload["steps"][0]["highlight_elements"] == []


@pytest.mark.asyncio
async def test_elements_not_referenced_are_revealed_in_first_step() -> None:
    emitted: list[dict[str, Any]] = []
    toolkit = VisualArtifactToolkit(on_ws_event=lambda _event, payload: emitted.append(payload))

    await toolkit.present_visual_artifact(
        title="T",
        elements=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        steps=[{"title": "E1", "spoken_text": "s"}, {"title": "E2", "spoken_text": "s"}],
    )

    assert [element["id"] for element in emitted[0]["elements"]] == ["a", "b"]
    assert set(emitted[0]["steps"][0]["reveal_elements"]) == {"a", "b"}


def test_oversized_payload_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PresentVisualArtifactArgs(
            title="T",
            elements=[{"id": f"e{i}", "label": "x"} for i in range(81)],
            steps=[{"title": "E", "spoken_text": "s"}],
        )
