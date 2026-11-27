"""Client MCP (stdio JSON-RPC 2.0 + chargement config/mcp.yaml) avec niveau de risque 2 par defaut."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jarvis_shared.models import RiskLevel
from pydantic import BaseModel, ConfigDict, create_model

from jarvis_core.tools.registry import ToolRegistry, ToolSpec

_MCP_SAFE_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "SYSTEMROOT",
    "COMSPEC",
    "APPDATA",
    "LOCALAPPDATA",
    "NODE_PATH",
    "NPM_CONFIG_CACHE",
)

_MAX_MCP_MESSAGE_BYTES = 16 * 1024 * 1024
_MAX_SKIPPED_MCP_MESSAGES = 256
_MAX_MCP_DESCRIPTION_CHARS = 600
_MCP_IDENT_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MCP_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_MCP_UNTRUSTED_TAG_RE = re.compile(r"<\s*/?\s*untrusted\b[^>]*>?", re.IGNORECASE)

_FORBIDDEN_MCP_ENV_KEYS: frozenset[str] = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "BASH_ENV",
        "ENV",
        "PYTHONSTARTUP",
        "PYTHONPATH",
        "NODE_OPTIONS",
    }
)

_DANGEROUS_PLAYWRIGHT_FRAGMENTS: tuple[str, ...] = (
    "eval",
    "run_code",
    "exec",
    "script",
    "upload",
    "file",
    "install",
    "download",
    "shell",
    "command",
    "cookie",
    "storage",
    "write",
    "delete",
    "remove",
)


def _sanitize_mcp_description(raw: Any, fallback: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return fallback
    text = _MCP_UNTRUSTED_TAG_RE.sub("", text)
    text = _MCP_CTRL_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_MCP_DESCRIPTION_CHARS] or fallback


def _is_safe_playwright_ui_tool(raw_name: str) -> bool:
    low = (raw_name or "").strip().lower()
    if not low:
        return False
    return not any(frag in low for frag in _DANGEROUS_PLAYWRIGHT_FRAGMENTS)


_PYDANTIC_RESERVED_ATTRS: frozenset[str] = frozenset(dir(BaseModel)) | {
    "model_config",
    "model_fields",
    "model_computed_fields",
    "model_extra",
    "model_fields_set",
}


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    default_risk: RiskLevel = RiskLevel.SENSITIVE


class DynamicMCPArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPStdioConnection:
    """Connexion JSON-RPC 2.0 persistante vers un serveur MCP en stdio."""

    def __init__(self, config: MCPServerConfig, timeout_seconds: float = 30.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_ring: deque[str] = deque(maxlen=50)
        self._lock = asyncio.Lock()
        self._next_id = 1
        self.connected = False
        self.last_error: str | None = None

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self._stderr_ring.append(text[:500])
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def start(self) -> None:
        if not self.config.command:
            raise RuntimeError(f"Commande manquante pour le serveur MCP '{self.config.name}'.")
        # N'expose jamais les secrets JARVIS de os.environ aux sous-processus MCP
        proc_env = {k: os.environ[k] for k in _MCP_SAFE_ENV_KEYS if k in os.environ}
        for k, v in self.config.env.items():
            key_str = str(k).strip()
            if not key_str or key_str.upper() in _FORBIDDEN_MCP_ENV_KEYS or "\x00" in key_str:
                raise RuntimeError(
                    f"Variable d'environnement interdite '{key_str}' pour le serveur MCP '{self.config.name}'."
                )
            val_str = str(v)
            if "\x00" in val_str:
                raise RuntimeError(
                    f"Caractere nul interdit dans l'environnement du serveur MCP '{self.config.name}'."
                )
            proc_env[key_str] = val_str

        self._stderr_ring.clear()
        self._proc = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
            limit=_MAX_MCP_MESSAGE_BYTES,
        )
        if self._proc.stderr is not None:
            self._stderr_task = asyncio.create_task(self._drain_stderr(self._proc.stderr))
        init_res = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "jarvis-core", "version": "2.0.0"},
            },
        )
        if isinstance(init_res, dict):
            await self.notify("notifications/initialized", {})
        self.connected = True
        self.last_error = None

    async def stop(self) -> None:
        self.connected = False
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        async with self._lock:
            if not self._proc or not self._proc.stdin:
                raise RuntimeError(f"Serveur MCP '{self.config.name}' non connecte.")
            msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
            line = (json.dumps(msg) + "\n").encode("utf-8")
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()

    async def _read_message(self, deadline: float) -> dict[str, Any]:
        if not self._proc or not self._proc.stdout:
            self.connected = False
            raise RuntimeError(f"Serveur MCP '{self.config.name}' ferme.")
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                self.connected = False
                raise TimeoutError(f"Delai depasse en lecture MCP '{self.config.name}'.")
            raw_line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=remaining)
            if not raw_line:
                self.connected = False
                stderr_hint = " | ".join(list(self._stderr_ring)[-3:])
                raise RuntimeError(
                    f"Flux MCP '{self.config.name}' ferme.{f' ({stderr_hint})' if stderr_hint else ''}"
                )
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            # Support optionnel de l'en-tete Content-Length (LSP/MCP)
            if text.lower().startswith("content-length:"):
                try:
                    length = int(text.split(":", 1)[1].strip())
                except ValueError as exc:
                    self.connected = False
                    raise RuntimeError(f"Content-Length MCP invalide : {text}") from exc
                if length <= 0 or length > _MAX_MCP_MESSAGE_BYTES:
                    self.connected = False
                    raise RuntimeError(f"Message MCP hors limite ({length} octets).")
                while True:
                    rem_hdr = deadline - loop.time()
                    if rem_hdr <= 0:
                        self.connected = False
                        raise TimeoutError(f"Delai depasse sur en-tetes MCP '{self.config.name}'.")
                    hdr = await asyncio.wait_for(self._proc.stdout.readline(), timeout=rem_hdr)
                    if not hdr or hdr.strip() == b"":
                        break
                rem_body = deadline - loop.time()
                if rem_body <= 0:
                    self.connected = False
                    raise TimeoutError(f"Delai depasse sur corps MCP '{self.config.name}'.")
                body = await asyncio.wait_for(
                    self._proc.stdout.readexactly(length), timeout=rem_body
                )
                return json.loads(body.decode("utf-8", errors="replace"))
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # Ignorer d'eventuelles bannieres texte non-JSON sur stdout
                continue
            if isinstance(parsed, dict):
                return parsed

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            if not self._proc or not self._proc.stdin:
                self.connected = False
                raise RuntimeError(f"Serveur MCP '{self.config.name}' non connecte.")
            req_id = self._next_id
            self._next_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            self._proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()

            deadline = asyncio.get_running_loop().time() + self.timeout_seconds
            skipped = 0
            while True:
                msg = await self._read_message(deadline)
                # Ignorer un nombre borne de notifications intercalees du serveur
                if "id" not in msg or msg.get("id") != req_id:
                    skipped += 1
                    if skipped > _MAX_SKIPPED_MCP_MESSAGES:
                        self.connected = False
                        raise RuntimeError(
                            f"Trop de messages MCP sans correspondance pour '{self.config.name}'."
                        )
                    continue
                if "error" in msg and msg["error"] is not None:
                    err = msg["error"]
                    raise RuntimeError(
                        err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    )
                return msg.get("result")

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.request("tools/list", {})
        if isinstance(result, dict):
            return list(result.get("tools") or [])
        return []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        return await self.request("tools/call", {"name": tool_name, "arguments": arguments})


class MCPClientManager:
    """Importe les outils des serveurs MCP declares et leur assigne un niveau de risque par defaut (2)."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.servers: dict[str, MCPServerConfig] = {}
        self.connections: dict[str, MCPStdioConnection] = {}
        self.imported_by_server: dict[str, list[str]] = {}
        self.errors_by_server: dict[str, str] = {}

    def register_server(self, config: MCPServerConfig) -> None:
        self.servers[config.name] = config

    def load_from_yaml(self, config_path: Path) -> list[MCPServerConfig]:
        if not config_path.is_file():
            return []
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        servers_raw = raw.get("servers") or {}
        loaded: list[MCPServerConfig] = []
        for name, cfg in servers_raw.items():
            if not isinstance(cfg, dict):
                continue
            risk_int = int(cfg.get("default_risk", int(RiskLevel.SENSITIVE)))
            risk = RiskLevel(min(max(risk_int, 0), 3))
            sc = MCPServerConfig(
                name=str(name),
                transport=str(cfg.get("transport", "stdio")),
                command=cfg.get("command"),
                args=[str(a) for a in (cfg.get("args") or [])],
                url=cfg.get("url"),
                env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
                enabled=bool(cfg.get("enabled", False)),
                default_risk=risk,
            )
            self.register_server(sc)
            loaded.append(sc)
        return loaded

    async def connect_server(self, server_name: str) -> list[str]:
        cfg = self.servers.get(server_name)
        if not cfg:
            raise KeyError(f"Serveur MCP '{server_name}' inconnu.")
        if cfg.transport != "stdio":
            msg = f"Transport '{cfg.transport}' non demarre automatiquement pour '{server_name}'."
            self.errors_by_server[server_name] = msg
            return []
        existing = self.connections.get(server_name)
        if existing and existing.connected:
            return self.imported_by_server.get(server_name, [])

        conn = MCPStdioConnection(cfg)
        try:
            await conn.start()
            tool_defs = await conn.list_tools()

            async def _caller(srv: str, tool: str, args: dict[str, Any]) -> Any:
                active = self.connections.get(srv)
                if not active or not active.connected:
                    raise RuntimeError(f"Serveur MCP '{srv}' deconnecte.")
                return await active.call_tool(tool, args)

            self.connections[server_name] = conn
            self.errors_by_server.pop(server_name, None)
            imported = self.import_server_tools(server_name, tool_defs, caller=_caller)
            self.imported_by_server[server_name] = imported
            return imported
        except Exception as exc:
            self.errors_by_server[server_name] = str(exc)
            await conn.stop()
            raise

    async def connect_enabled_servers(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for name, cfg in self.servers.items():
            if not cfg.enabled:
                continue
            try:
                tools = await self.connect_server(name)
                results[name] = {"ok": True, "tools": tools}
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}
        return results

    async def disconnect_all(self) -> None:
        for conn in list(self.connections.values()):
            await conn.stop()
        self.connections.clear()
        for tool_names in list(self.imported_by_server.values()):
            for fq_name in tool_names:
                self.registry.unregister(fq_name)
        self.imported_by_server.clear()

    def status_summary(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, cfg in self.servers.items():
            conn = self.connections.get(name)
            tools = self.imported_by_server.get(name, [])
            out.append(
                {
                    "name": name,
                    "enabled": cfg.enabled,
                    "transport": cfg.transport,
                    "connected": bool(conn and conn.connected),
                    "tools_count": len(tools),
                    "tools": tools,
                    "error": self.errors_by_server.get(name),
                }
            )
        return out

    def import_server_tools(
        self,
        server_name: str,
        tool_definitions: list[dict[str, Any]],
        caller: Any,
        risk_overrides: dict[str, RiskLevel] | None = None,
    ) -> list[str]:
        """Importe une liste d'outils MCP dans ToolRegistry avec risque par defaut SENSITIVE (2)."""
        server_cfg = self.servers.get(server_name) or MCPServerConfig(name=server_name)
        overrides = risk_overrides or {}
        imported: list[str] = []
        is_ui_server = server_name.lower() in {"playwright", "browser", "puppeteer"}

        def _make_invoker(srv: str, tool: str) -> Any:
            async def _invoke(parsed: BaseModel) -> Any:
                # N'envoie que les champs explicitement fournis, en excluant tout champ interne prefixe '_'
                payload = {
                    k: v
                    for k, v in parsed.model_dump(exclude_unset=True).items()
                    if not str(k).startswith("_")
                }
                res = caller(srv, tool, payload)
                if asyncio.iscoroutine(res):
                    return await res
                return res

            return _invoke

        clean_srv = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name or "").strip("_")
        if not clean_srv or not _MCP_IDENT_RE.match(clean_srv):
            raise ValueError(f"Nom de serveur MCP invalide : {server_name!r}")

        for tdef in tool_definitions:
            raw_name = str(tdef.get("name") or "").strip()
            if not raw_name or not _MCP_IDENT_RE.match(raw_name):
                continue
            fq_name = f"mcp_{clean_srv}_{raw_name}"
            desc = _sanitize_mcp_description(
                tdef.get("description"),
                fallback=f"Outil MCP {raw_name} ({clean_srv})",
            )
            risk = overrides.get(raw_name, server_cfg.default_risk)

            raw_schema = (
                tdef.get("inputSchema") if isinstance(tdef.get("inputSchema"), dict) else None
            )
            props = (raw_schema or {}).get("properties") or {}
            fields_spec: dict[str, Any] = {}
            for prop_name in props:
                if (
                    isinstance(prop_name, str)
                    and prop_name.isidentifier()
                    and not prop_name.startswith("_")
                    and prop_name not in _PYDANTIC_RESERVED_ATTRS
                ):
                    fields_spec[prop_name] = (Any, None)
            args_model = (
                create_model(
                    f"MCP_{server_name}_{raw_name}_Args", __base__=DynamicMCPArgs, **fields_spec
                )
                if fields_spec
                else DynamicMCPArgs
            )

            is_safe_ui_tool = is_ui_server and _is_safe_playwright_ui_tool(raw_name)

            spec = ToolSpec(
                name=fq_name,
                description=desc,
                handler=_make_invoker(server_name, raw_name),
                args_model=args_model,
                risk=risk,
                returns_untrusted=True,
                untrusted_source=f"mcp:{server_name}:{raw_name}",
                is_interactive_ui=is_safe_ui_tool,
                pass_model=True,
                raw_input_schema=raw_schema,
            )
            self.registry.register(spec)
            imported.append(fq_name)
        self.imported_by_server[server_name] = imported
        return imported
