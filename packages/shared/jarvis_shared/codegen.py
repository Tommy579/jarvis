"""Generateur de types TypeScript a partir des modeles Pydantic v2 de jarvis_shared."""

from __future__ import annotations

import argparse
import types
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from . import rest_models, ws_protocol


def _ts_type(annotation: Any) -> str:
    if annotation is None or annotation is type(None):
        return "null"
    if annotation is str:
        return "string"
    if annotation is int or annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation is Any:
        return "unknown"

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        parts: list[str] = []
        for item in args:
            if isinstance(item, str):
                parts.append(f'"{item}"')
            elif isinstance(item, bool):
                parts.append("true" if item else "false")
            elif item is None:
                parts.append("null")
            else:
                parts.append(str(item))
        return " | ".join(parts)

    if origin is list or origin is tuple or origin is set:
        inner = _ts_type(args[0]) if args else "unknown"
        if " | " in inner:
            return f"({inner})[]"
        return f"{inner}[]"

    if origin is dict:
        val_type = _ts_type(args[1]) if len(args) > 1 else "unknown"
        return f"Record<string, {val_type}>"

    if origin in (Union, getattr(types, "UnionType", object())):
        # Decompose Annotated or Union types cleanly
        rendered = [_ts_type(arg) for arg in args]
        # Deduplicate while preserving order
        seen: list[str] = []
        for item in rendered:
            if item not in seen:
                seen.append(item)
        return " | ".join(seen)

    # Annotated[T, ...]
    if str(origin) == "typing.Annotated" and args:
        return _ts_type(args[0])

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        vals = [f'"{e.value}"' if isinstance(e.value, str) else str(e.value) for e in annotation]
        return " | ".join(vals)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__

    return "unknown"


def _collect_models(*modules: Any) -> list[type[BaseModel]]:
    collected: list[type[BaseModel]] = []
    seen: set[type[BaseModel]] = set()
    for mod in modules:
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == mod.__name__
                and obj not in seen
            ):
                seen.add(obj)
                collected.append(obj)
    return collected


def generate_typescript() -> str:
    lines: list[str] = [
        "/* eslint-disable */",
        "// Fichier genere automatiquement depuis packages/shared/jarvis_shared/. Ne pas modifier a la main.",
        "",
        'export type SphereState = "idle" | "listening" | "thinking" | "speaking" | "alert" | "offline";',
        'export type SensorName = "screen" | "mic" | "camera";',
        "export type RiskLevel = 0 | 1 | 2 | 3;",
        "export type HighRiskLevel = 2 | 3;",
        "export type UrgencyLevel = 0 | 1 | 2 | 3 | 4;",
        'export type SensitivityLevel = "normal" | "privé" | "très privé";',
        "",
    ]

    models = _collect_models(ws_protocol, rest_models)
    for model in models:
        lines.append(f"export interface {model.__name__} {{")
        for field_name, field_info in model.model_fields.items():
            ts_field_type = _ts_type(field_info.annotation)
            optional_mark = (
                "?"
                if not field_info.is_required()
                and field_info.default is None
                and "null" not in ts_field_type
                else ""
            )
            lines.append(f"  {field_name}{optional_mark}: {ts_field_type};")
        lines.append("}")
        lines.append("")

    client_events = [
        "UserTextEvent",
        "UserAudioEvent",
        "ActionApproveEvent",
        "AlertAckEvent",
        "SettingsModelEvent",
        "SensorsToggleEvent",
        "UserInterruptEvent",
    ]
    server_events = [
        "StateEvent",
        "TranscriptPartialEvent",
        "TranscriptFinalEvent",
        "AssistantDeltaEvent",
        "AssistantDoneEvent",
        "ToolCallEvent",
        "ToolResultEvent",
        "TtsChunkEvent",
        "MicLevelEvent",
        "ApprovalRequestEvent",
        "AlertEvent",
        "GoalUpdatedEvent",
        "SubagentStatusEvent",
        "SupervisorInterventionEvent",
        "VisualArtifactEvent",
    ]
    lines.append(f"export type ClientWsEvent = {' | '.join(client_events)};")
    lines.append(f"export type ServerWsEvent = {' | '.join(server_events)};")
    lines.append("")
    return "\n".join(lines)


def write_typescript(out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_typescript()
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genere les types TypeScript depuis jarvis_shared."
    )
    parser.add_argument(
        "--out",
        default="web/src/types/generated.ts",
        help="Chemin du fichier TypeScript genere.",
    )
    args = parser.parse_args()
    target = write_typescript(Path(args.out))
    print(f"Types TypeScript generes dans {target}")


if __name__ == "__main__":
    main()
