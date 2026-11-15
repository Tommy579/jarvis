"""Registre d'outils avec schemas Pydantic, filtrage par plateforme, niveaux 0..3 et defense anti-injection."""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from omi_shared.models import RiskLevel, ToolCall, ToolResult
from pydantic import BaseModel

from .approvals import ApprovalManager

_UNTRUSTED_PATTERN = re.compile(r"<untrusted\b[^>]*>.*?</untrusted>", re.DOTALL | re.IGNORECASE)


def wrap_untrusted_content(source: str, content: str) -> str:
    """Encadre tout contenu externe (mail, web, OCR ecran, audio) dans une balise <untrusted>."""
    safe_source = re.sub(r"[^a-zA-Z0-9_.\-:]", "_", source or "external")
    # Neutraliser toute tentative de fermeture prematuree de la balise </untrusted>
    sanitized = str(content).replace("</untrusted>", "&lt;/untrusted&gt;")
    return f'<untrusted source="{safe_source}">\n{sanitized}\n</untrusted>'


def contains_untrusted_tags(text: str) -> bool:
    return bool(_UNTRUSTED_PATTERN.search(text or ""))


class ToolPermissionError(PermissionError):
    """Levee lorsqu'un outil est bloque par la politique de risque ou par un contexte non fiable."""


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    args_model: type[BaseModel]
    risk: RiskLevel = RiskLevel.READ_ONLY
    platforms: set[str] = field(default_factory=lambda: {"linux", "windows", "darwin"})
    returns_untrusted: bool = False
    untrusted_source: str | None = None
    enabled_by_default: bool = True

    def is_supported_on(self, current_platform: str) -> bool:
        return current_platform in self.platforms

    def to_openai_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


def detect_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


class ToolRegistry:
    """Registre central des outils avec audit et controle strict des permissions."""

    def __init__(
        self,
        approval_manager: ApprovalManager | None = None,
        current_platform: str | None = None,
    ):
        self.approval_manager = approval_manager or ApprovalManager()
        self.current_platform = current_platform or detect_platform()
        self._tools: dict[str, ToolSpec] = {}
        self.audit_log: list[dict[str, Any]] = []

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        spec = self._tools.get(name)
        if not spec or not spec.enabled_by_default:
            return None
        if not spec.is_supported_on(self.current_platform):
            return None
        return spec

    def list_schemas(self, has_untrusted_context: bool = False) -> list[dict[str, Any]]:
        """Retourne uniquement les outils compatibles avec l'OS courant."""
        schemas: list[dict[str, Any]] = []
        for spec in self._tools.values():
            if not spec.enabled_by_default:
                continue
            if not spec.is_supported_on(self.current_platform):
                continue
            schemas.append(spec.to_openai_schema())
        return schemas

    async def execute(
        self,
        tool_call: ToolCall,
        has_untrusted_context: bool = False,
        pre_approved: bool = False,
        approval_timeout: float | None = None,
    ) -> ToolResult:
        spec = self.get(tool_call.name)
        if not spec:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={
                    "error": f"Outil '{tool_call.name}' inconnu ou non disponible sur {self.current_platform}."
                },
                is_error=True,
            )

        # Validation Pydantic des arguments
        try:
            validated_args = spec.args_model.model_validate(tool_call.arguments)
            args_dict = validated_args.model_dump()
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={"error": f"Arguments invalides pour '{tool_call.name}' : {e}"},
                is_error=True,
            )

        # Defense anti-injection stricte :
        # Si le tour contient du contenu externe non fiable (<untrusted>),
        # les outils de niveau 2 et 3 sont bloques immediatement sans meme proposer d'execution automatique.
        if has_untrusted_context and spec.risk >= RiskLevel.SENSITIVE and not pre_approved:
            self._record_audit(
                tool_call=tool_call,
                risk=spec.risk,
                status="blocked_untrusted_context",
                has_untrusted_context=True,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={
                    "error": (
                        f"Execution de '{tool_call.name}' (risque {int(spec.risk)}) refusee : "
                        "le tour courant contient des donnees externes non fiables (<untrusted>)."
                    ),
                    "blocked_by": "untrusted_context_guard",
                },
                is_error=True,
            )

        # Verification de la matrice de permissions pour les niveaux 2 et 3
        if spec.risk >= RiskLevel.SENSITIVE and not pre_approved:
            exact_cmd = args_dict.get("command") or str(args_dict)
            req = self.approval_manager.create_request(
                tool_name=spec.name,
                arguments=args_dict,
                description=f"Outil '{spec.name}' (niveau {int(spec.risk)})",
                risk=spec.risk,
                exact_command=exact_cmd if spec.risk == RiskLevel.DANGEROUS else None,
            )
            decision = await self.approval_manager.wait_for_decision(
                req.id, timeout=approval_timeout
            )
            if decision != "approved":
                self._record_audit(
                    tool_call=tool_call,
                    risk=spec.risk,
                    status=f"approval_{decision}",
                    has_untrusted_context=has_untrusted_context,
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    output={
                        "error": f"Approbation requise pour '{spec.name}' (statut : {decision}).",
                        "approval_request_id": req.id,
                        "status": decision,
                    },
                    is_error=True,
                )

        # Execution effective de l'outil
        try:
            if inspect.iscoroutinefunction(spec.handler):
                raw_out = await spec.handler(**args_dict)
            else:
                raw_out = await asyncio.to_thread(spec.handler, **args_dict)
        except Exception as e:
            self._record_audit(
                tool_call=tool_call,
                risk=spec.risk,
                status="execution_error",
                has_untrusted_context=has_untrusted_context,
                error=str(e),
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={"error": str(e)},
                is_error=True,
            )

        # Si l'outil lit une source externe, encapsuler la sortie dans <untrusted>
        output_val = raw_out
        if spec.returns_untrusted:
            source_label = spec.untrusted_source or spec.name
            text_repr = raw_out if isinstance(raw_out, str) else str(raw_out)
            output_val = wrap_untrusted_content(source_label, text_repr)

        self._record_audit(
            tool_call=tool_call,
            risk=spec.risk,
            status="executed",
            has_untrusted_context=has_untrusted_context,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output=output_val,
            is_error=False,
            is_untrusted=spec.returns_untrusted,
            source=spec.untrusted_source or spec.name,
        )

    def _record_audit(
        self,
        tool_call: ToolCall,
        risk: RiskLevel,
        status: str,
        has_untrusted_context: bool,
        error: str | None = None,
    ) -> None:
        self.audit_log.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
                "risk": int(risk),
                "status": status,
                "has_untrusted_context": has_untrusted_context,
                "error": error,
            }
        )
