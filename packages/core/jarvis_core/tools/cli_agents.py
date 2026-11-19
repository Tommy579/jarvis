"""Outils CLI pour déléguer des sous-tâches à `agy` ou `hermes` comme sous-agents."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Literal

from jarvis_shared.models import RiskLevel
from pydantic import BaseModel, Field

from jarvis_core.tools.registry import ToolRegistry, ToolSpec

DEFAULT_HERMES_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_HERMES_PROVIDER = "openrouter"


class RunCliSubagentArgs(BaseModel):
    agent: Literal["agy", "hermes"] = Field(
        description="Agent CLI à exécuter : 'agy' (Antigravity CLI) ou 'hermes' (Hermes Agent)."
    )
    prompt: str = Field(
        min_length=1,
        max_length=12000,
        description="Instruction complète et autonome transmise au sous-agent CLI.",
    )
    workdir: str | None = Field(
        default=None,
        description="Répertoire de travail cible (optionnel).",
    )
    model: str | None = Field(
        default=None,
        description=(
            "Modèle optionnel. Pour 'hermes', défaut 'deepseek/deepseek-v4.1-flash-1017' "
            "via OpenRouter (ou 'qwen3.5:2b' en secours local uniquement si nécessaire)."
        ),
    )
    provider: str | None = Field(
        default=None,
        description="Fournisseur explicite pour hermes (défaut: 'openrouter', ou 'ollama' pour qwen3.5:2b).",
    )
    mode: Literal["auto", "accept-edits", "plan"] = Field(
        default="auto",
        description="Mode d'exécution pour agy ('auto', 'accept-edits' ou 'plan').",
    )
    timeout_s: float = Field(
        default=180.0,
        ge=5.0,
        le=900.0,
        description="Délai maximal d'exécution en secondes.",
    )


def _normalize_cli_model(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    low = cleaned.lower()
    if low in ("qwen-3.5-2b", "qwen3.5-2b", "ollama/qwen3.5:2b"):
        return "qwen3.5:2b"
    if low in ("deepseek-v4.1-1017", "deepseek-v4.1-flash-1017"):
        return "deepseek/deepseek-v4.1-flash-1017"
    return cleaned


def build_cli_subagent_command(
    *,
    agent: Literal["agy", "hermes"],
    prompt: str,
    workdir: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    mode: Literal["auto", "accept-edits", "plan"] = "auto",
    executable: str | None = None,
) -> tuple[list[str], str | None]:
    """Construit la ligne de commande et le cwd pour `agy` ou `hermes`."""
    cwd: str | None = None
    if workdir and workdir.strip():
        resolved = Path(workdir).expanduser().resolve()
        if resolved.exists() and resolved.is_dir():
            cwd = str(resolved)

    norm_model = _normalize_cli_model(model)
    bin_path = executable or shutil.which(agent) or agent

    if agent == "agy":
        cmd = [
            bin_path,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--mode",
            mode,
        ]
        if norm_model:
            cmd.extend(["--model", norm_model])
        if cwd:
            cmd.extend(["--add-dir", cwd])
        return cmd, cwd

    effective_model = norm_model or DEFAULT_HERMES_MODEL
    effective_provider = provider
    if not effective_provider:
        if effective_model == "qwen3.5:2b" or ":" in effective_model:
            effective_provider = "ollama"
        else:
            effective_provider = DEFAULT_HERMES_PROVIDER

    cmd = [
        bin_path,
        "-z",
        prompt,
        "-m",
        effective_model,
        "--provider",
        effective_provider,
        "--yolo",
    ]
    if cwd:
        cmd.extend(["--in", cwd])
    return cmd, cwd


async def execute_cli_subagent(args: RunCliSubagentArgs) -> dict[str, Any]:
    """Exécute `agy` ou `hermes` en sous-processus non interactif et retourne sa sortie."""
    exe = shutil.which(args.agent)
    if not exe:
        return {
            "ok": False,
            "agent": args.agent,
            "error": f"Binaire '{args.agent}' introuvable dans le PATH.",
        }

    cmd, cwd = build_cli_subagent_command(
        agent=args.agent,
        prompt=args.prompt,
        workdir=args.workdir,
        model=args.model,
        provider=args.provider,
        mode=args.mode,
        executable=exe,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=float(args.timeout_s),
        )
    except (TimeoutError, asyncio.TimeoutError):
        proc.kill()
        await proc.wait()
        return {
            "ok": False,
            "agent": args.agent,
            "command": cmd[:4],
            "error": f"Dépassement du délai ({args.timeout_s:.0f}s) pour {args.agent}.",
        }
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise

    stdout = stdout_b.decode("utf-8", errors="replace").strip()
    stderr = stderr_b.decode("utf-8", errors="replace").strip()
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "agent": args.agent,
        "returncode": proc.returncode,
        "model": _normalize_cli_model(args.model)
        or (DEFAULT_HERMES_MODEL if args.agent == "hermes" else "default"),
        "output": stdout[-8000:] if stdout else "",
        "stderr": stderr[-2000:] if stderr else "",
    }


class CLIAgentToolkit:
    """Expose `run_cli_subagent` pour piloter `agy` et `hermes` en CLI."""

    def register_all(self, registry: ToolRegistry) -> None:
        registry.register(
            ToolSpec(
                name="run_cli_subagent",
                description=(
                    "Lance un sous-agent CLI externe ('agy' ou 'hermes' avec "
                    "deepseek/deepseek-v4.1-flash-1017 sous OpenRouter par défaut, ou "
                    "qwen3.5:2b en secours local léger) sur une tâche autonome et renvoie sa sortie."
                ),
                handler=self.run_cli_subagent,
                args_model=RunCliSubagentArgs,
                risk=RiskLevel.REVERSIBLE_WRITE,
            )
        )

    async def run_cli_subagent(self, args: RunCliSubagentArgs) -> dict[str, Any]:
        return await execute_cli_subagent(args)
