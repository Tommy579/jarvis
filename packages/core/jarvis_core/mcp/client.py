"""Client MCP (stdio JSON-RPC 2.0 + chargement config/mcp.yaml) avec niveau de risque 2 par defaut."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jarvis_shared.models import RiskLevel
from pydantic import BaseModel, ConfigDict, create_model

from jarvis_core.tools.registry import ToolRegistry, ToolSpec


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
        self._lock = asyncio.Lock()
        self._next_id = 1
        self.connected = False
        self.last_error: str | None = None

    async def start(self) -> None:
        if not self.config.command:
            raise RuntimeError(f"Commande manquante pour le serveur MCP '{self.config.name}'.")
        proc_env = os.environ.copy()
        for k, v in self.config.env.items():
            proc_env[str(k)] = str(v)

        self._proc = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
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
        if not self._proc or not self._proc.stdin:
            raise RuntimeError(f"Serveur MCP '{self.config.name}' non connecte.")
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        line = (json.dumps(msg) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        if not self._proc or not self._proc.stdout:
            raise RuntimeError(f"Serveur MCP '{self.config.name}' ferme.")
        while True:
            raw_line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=self.timeout_seconds
            )
            if not raw_line:
                stderr_hint = ""
                if self._proc.stderr:
                    try:
                        err_b = await asyncio.wait_for(self._proc.stderr.read(1024), timeout=0.2)
                        stderr_hint = err_b.decode("utf-8", errors="replace").strip()
                    except Exception:
                        pass
                raise RuntimeError(
                    f"Flux MCP '{self.config.name}' ferme.{f' ({stderr_hint})' if stderr_hint else ''}"
                )
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            # Support optionnel de l'en-tete Content-Length (LSP/MCP)
            if text.lower().startswith("content-length:"):
                length = int(text.split(":", 1)[1].strip())
                while True:
                    hdr = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=self.timeout_seconds
                    )
                    if not hdr or hdr.strip() == b"":
                        break
                body = await asyncio.wait_for(
                    self._proc.stdout.readexactly(length), timeout=self.timeout_seconds
                )
                return json.loads(body.decode("utf-8", errors="replace"))
            return json.loads(text)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            if not self._proc or not self._proc.stdin:
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

            while True:
                msg = await self._read_message()
                # Ignorer les notifications intercalees du serveur
                if "id" not in msg:
                    continue
                if msg.get("id") != req_id:
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

        for tdef in tool_definitions:
            raw_name = tdef.get("name") or "tool"
            fq_name = f"mcp_{server_name}_{raw_name}"
            desc = tdef.get("description") or f"Outil MCP {raw_name} ({server_name})"
            risk = overrides.get(raw_name, server_cfg.default_risk)

            props = (tdef.get("inputSchema") or {}).get("properties") or {}
            fields_spec: dict[str, Any] = {}
            for prop_name in props:
                fields_spec[prop_name] = (Any, None)
            args_model = create_model(
                f"MCP_{server_name}_{raw_name}_Args", __base__=DynamicMCPArgs, **fields_spec
            )

            async def _invoke(_srv: str = server_name, _tool: str = raw_name, **kwargs: Any) -> Any:
                return await caller(_srv, _tool, kwargs)

            spec = ToolSpec(
                name=fq_name,
                description=desc,
                handler=_invoke,
                args_model=args_model,
                risk=risk,
                returns_untrusted=True,
                untrusted_source=f"mcp:{server_name}:{raw_name}",
                is_interactive_ui=is_ui_server,
            )
            self.registry.register(spec)
            imported.append(fq_name)
        self.imported_by_server[server_name] = imported
        return imported
