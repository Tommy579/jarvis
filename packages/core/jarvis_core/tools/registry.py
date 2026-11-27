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

from jarvis_shared.models import RiskLevel, ToolCall, ToolResult, Usage
from pydantic import BaseModel

from ..security import wrap_untrusted_content
from .approvals import ApprovalManager
from .autonomy import AutonomyPolicy

_UNTRUSTED_PATTERN = re.compile(r"<untrusted\b[^>]*>.*?</untrusted>", re.DOTALL | re.IGNORECASE)

_SENSITIVE_ARG_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "voice_pin",
)


def _redact_sensitive_args(args: dict[str, Any]) -> dict[str, Any]:
    """Masque les arguments sensibles (mots de passe, jetons, secrets) avant journalisation ou approbation."""
    redacted: dict[str, Any] = {}
    for k, v in args.items():
        low = str(k).lower()
        if any(frag in low for frag in _SENSITIVE_ARG_FRAGMENTS) and v is not None:
            redacted[k] = "***"
        elif isinstance(v, dict):
            redacted[k] = _redact_sensitive_args(v)
        else:
            redacted[k] = v
    return redacted


# Cle reservee qu'un handler place dans son dict de sortie pour declarer les tokens
# consommes hors flux LLM : elle est retiree de la sortie avant de la donner au modele.
TOOL_USAGE_KEY = "__tool_usage__"


def contains_untrusted_tags(text: str) -> bool:
    return bool(_UNTRUSTED_PATTERN.search(text or ""))


_WORKSPACE_PREFIX_RE = re.compile(r"^(\[Workspace actif\s*:[^\]]*\]\s*\n)(.*)$", re.DOTALL)
_ASK_CMD_RE = re.compile(r"^/ask(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


def extract_ask_flag(text: str) -> tuple[bool, str]:
    """Detecte la commande /ask au debut du prompt et retourne (actif, prompt_nettoye)."""
    raw = text or ""
    ws_match = _WORKSPACE_PREFIX_RE.match(raw)
    if ws_match:
        prefix, body = ws_match.group(1), ws_match.group(2).strip()
        ask_match = _ASK_CMD_RE.match(body)
        if ask_match:
            rest = (ask_match.group(1) or "").strip()
            return True, f"{prefix}{rest}" if rest else prefix.rstrip()
        return False, raw
    ask_match = _ASK_CMD_RE.match(raw.strip())
    if ask_match:
        return True, (ask_match.group(1) or "").strip()
    return False, raw


def format_approval_description(tool_name: str, args_dict: dict[str, Any]) -> str:
    """Formate une description concise et lisible pour les demandes d'approbation."""
    if tool_name == "mail_send":
        to = str(args_dict.get("to", "")).strip()
        subject = str(args_dict.get("subject", "")).strip()
        body = str(args_dict.get("body", "")).strip()
        if len(body) > 400:
            body = f"{body[:400].rstrip()}…"
        return f"Envoi d'un mail\nÀ : {to}\nObjet : {subject}\nMessage : {body}"
    if tool_name == "run_command":
        cmd = str(args_dict.get("command", "")).strip()
        cwd = str(args_dict.get("cwd") or "/").strip()
        return f"Commande terminal : {cmd}\nDossier : {cwd}"
    safe_args = _redact_sensitive_args(args_dict)
    summary = ", ".join(f"{k} : {v}" for k, v in safe_args.items() if v not in (None, ""))
    return f"{tool_name}\n{summary}" if summary else tool_name


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
    is_interactive_ui: bool = False
    pass_model: bool = False
    raw_input_schema: dict[str, Any] | None = None

    def is_supported_on(self, current_platform: str) -> bool:
        return current_platform in self.platforms

    def to_openai_schema(self) -> dict[str, Any]:
        if self.raw_input_schema is not None:
            schema = dict(self.raw_input_schema)
        else:
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
        autonomy_policy: AutonomyPolicy | None = None,
        audit_recorder: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.approval_manager = approval_manager or ApprovalManager()
        self.current_platform = current_platform or detect_platform()
        self.autonomy_policy = autonomy_policy or AutonomyPolicy()
        self.audit_recorder = audit_recorder
        self._tools: dict[str, ToolSpec] = {}
        self.audit_log: list[dict[str, Any]] = []

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolSpec | None:
        spec = self._tools.get(name)
        if not spec or not spec.enabled_by_default:
            return None
        if not spec.is_supported_on(self.current_platform):
            return None
        return spec

    def list_schemas(
        self,
        has_untrusted_context: bool = False,
        allowed_tools: set[str] | None = None,
        excluded_tools: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retourne uniquement les outils compatibles avec l'OS courant et le scope autorise."""
        schemas: list[dict[str, Any]] = []
        for spec in self._tools.values():
            if not spec.enabled_by_default:
                continue
            if allowed_tools is not None and spec.name not in allowed_tools:
                continue
            if excluded_tools and spec.name in excluded_tools:
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
        autonomous_run: bool = False,
        task_id: str | None = None,
        allowed_tools: set[str] | None = None,
        excluded_tools: set[str] | None = None,
        ask_terminal_approval: bool = False,
    ) -> ToolResult:
        if excluded_tools and tool_call.name in excluded_tools:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={
                    "error": (
                        f"Outil '{tool_call.name}' reserve aux sous-agents : "
                        "delegue cette tache via start_goal, delegate_subagent ou run_cli_subagent."
                    ),
                    "blocked_by": "orchestrator_delegation_policy",
                },
                is_error=True,
            )
        if allowed_tools is not None and tool_call.name not in allowed_tools:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={
                    "error": f"Outil '{tool_call.name}' non autorise pour ce profil de sous-agent.",
                    "blocked_by": "tool_scope",
                },
                is_error=True,
            )
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

        # Arret d'urgence (kill switch)
        if self.autonomy_policy.emergency_stop and (
            autonomous_run or spec.risk >= RiskLevel.REVERSIBLE_WRITE
        ):
            self._record_audit(
                tool_call=tool_call,
                risk=spec.risk,
                status="blocked_emergency_stop",
                has_untrusted_context=has_untrusted_context,
                task_id=task_id,
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={
                    "error": "Arret d'urgence actif : execution suspendue par l'utilisateur.",
                    "blocked_by": "emergency_stop",
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

        # Verification de liste blanche/noire des domaines sur toute URL cible
        target_url = str(args_dict.get("url") or args_dict.get("target_url") or "").strip()
        if target_url:
            url_ok, url_reason = self.autonomy_policy.check_url_allowed(target_url)
            if not url_ok:
                self._record_audit(
                    tool_call=tool_call,
                    risk=spec.risk,
                    status="blocked_domain",
                    has_untrusted_context=has_untrusted_context,
                    task_id=task_id,
                    error=url_reason,
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    output={"error": url_reason, "blocked_by": "domain_policy"},
                    is_error=True,
                )

        # Defense anti-injection :
        # - Niveau 3 (DANGEROUS) toujours bloque sur contexte <untrusted>
        # - Shell / ecriture fichier / CLI subagent / open_url bloques dans TOUS les modes
        #   si block_shell_in_untrusted=True
        # - Tout outil d'ecriture non-UI (risk >= 1) bloque sur contexte <untrusted>
        # - Les outils UI interactifs (niveaux <= 2, hors never_auto_approve) passent par
        #   l'approbation humaine en mode supervise ou l'auto-approbation en mode autonome
        if has_untrusted_context and not pre_approved:
            if self.autonomy_policy.block_shell_in_untrusted and spec.name in {
                "run_command",
                "write_file",
                "run_cli_subagent",
                "open_url",
            }:
                self._record_audit(
                    tool_call=tool_call,
                    risk=spec.risk,
                    status="blocked_untrusted_context",
                    has_untrusted_context=True,
                    task_id=task_id,
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    output={
                        "error": (
                            f"Execution de '{tool_call.name}' bloquee pendant la lecture d'une source externe (<untrusted>)."
                        ),
                        "blocked_by": "untrusted_context_guard",
                    },
                    is_error=True,
                )

            ui_bypass = self.autonomy_policy.can_bypass_untrusted_guard(
                spec.name,
                spec.risk,
                is_interactive_ui=spec.is_interactive_ui,
                autonomous_run=autonomous_run,
            )
            ui_needs_approval = self.autonomy_policy.ui_tool_needs_approval_in_untrusted(
                spec.name,
                spec.risk,
                is_interactive_ui=spec.is_interactive_ui,
            )
            if spec.risk >= RiskLevel.REVERSIBLE_WRITE and not ui_bypass and not ui_needs_approval:
                self._record_audit(
                    tool_call=tool_call,
                    risk=spec.risk,
                    status="blocked_untrusted_context",
                    has_untrusted_context=True,
                    task_id=task_id,
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
            if ui_needs_approval and not ui_bypass:
                self._record_audit(
                    tool_call=tool_call,
                    risk=spec.risk,
                    status="approval_required_untrusted_ui",
                    has_untrusted_context=True,
                    task_id=task_id,
                )

        # Verification de la matrice de permissions pour les niveaux 2 et 3.
        # Pour run_command : sans /ask, toutes les commandes sont autorisees quel que soit leur risque ;
        # avec /ask, une approbation est demandee uniquement a partir du niveau 3 (DANGEROUS).
        effective_risk = spec.risk
        if spec.name == "run_command" and ask_terminal_approval:
            from .standard import is_dangerous_command

            if is_dangerous_command(str(args_dict.get("command") or "")):
                effective_risk = RiskLevel.DANGEROUS

        skip_run_command_approval = spec.name == "run_command" and (
            not ask_terminal_approval or effective_risk < RiskLevel.DANGEROUS
        )

        auto_approved = False
        if (
            effective_risk >= RiskLevel.SENSITIVE
            and not pre_approved
            and not skip_run_command_approval
        ):
            if self.autonomy_policy.should_auto_approve(
                spec.name, effective_risk, autonomous_run=autonomous_run
            ):
                auto_approved = True
            else:
                safe_args = _redact_sensitive_args(args_dict)
                exact_cmd = safe_args.get("command") or str(safe_args)
                req = self.approval_manager.create_request(
                    tool_name=spec.name,
                    arguments=safe_args,
                    description=format_approval_description(spec.name, safe_args),
                    risk=effective_risk,
                    exact_command=exact_cmd if effective_risk == RiskLevel.DANGEROUS else None,
                )
                decision = await self.approval_manager.wait_for_decision(
                    req.id, timeout=approval_timeout
                )
                if decision != "approved":
                    self._record_audit(
                        tool_call=tool_call,
                        risk=effective_risk,
                        status=f"approval_{decision}",
                        has_untrusted_context=has_untrusted_context,
                        task_id=task_id,
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
            if spec.pass_model:
                if inspect.iscoroutinefunction(spec.handler):
                    raw_out = await spec.handler(validated_args)
                else:
                    raw_out = await asyncio.to_thread(spec.handler, validated_args)
            else:
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
                task_id=task_id,
                error=str(e),
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output={"error": str(e)},
                is_error=True,
            )

        # Un outil peut declarer la consommation de tokens qu'il a provoquee (ex:
        # browser_analyze_media appelle Gemini) : la cle reservee sort du contexte modele
        # mais alimente le compteur de cout du tour.
        tool_usage: Usage | None = None
        if isinstance(raw_out, dict) and TOOL_USAGE_KEY in raw_out:
            declared = raw_out.pop(TOOL_USAGE_KEY)
            if isinstance(declared, Usage):
                tool_usage = declared
            elif isinstance(declared, dict):
                try:
                    tool_usage = Usage.model_validate(declared)
                except Exception:
                    tool_usage = None

        # Si l'outil lit une source externe, encapsuler la sortie dans <untrusted>
        output_val = raw_out
        if spec.returns_untrusted:
            source_label = spec.untrusted_source or spec.name
            text_repr = raw_out if isinstance(raw_out, str) else str(raw_out)
            output_val = wrap_untrusted_content(source_label, text_repr)

        self._record_audit(
            tool_call=tool_call,
            risk=spec.risk,
            status="auto_approved" if auto_approved else "executed",
            has_untrusted_context=has_untrusted_context,
            task_id=task_id,
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output=output_val,
            is_error=False,
            is_untrusted=spec.returns_untrusted,
            source=spec.untrusted_source or spec.name,
            usage=tool_usage,
        )

    def _record_audit(
        self,
        tool_call: ToolCall,
        risk: RiskLevel,
        status: str,
        has_untrusted_context: bool,
        task_id: str | None = None,
        error: str | None = None,
    ) -> None:
        safe_args = _redact_sensitive_args(tool_call.arguments)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
            "arguments": safe_args,
            "risk": int(risk),
            "status": status,
            "has_untrusted_context": has_untrusted_context,
            "error": error,
        }
        self.audit_log.append(entry)
        if len(self.audit_log) > 2000:
            del self.audit_log[:-2000]
        if self.audit_recorder is not None:
            try:
                self.audit_recorder(entry)
            except Exception:
                pass
