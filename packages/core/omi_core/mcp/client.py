"""Client MCP chargeant les serveurs de config/mcp.yaml avec niveau de risque 2 par defaut."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from omi_shared.models import RiskLevel
from pydantic import BaseModel, ConfigDict, create_model

from omi_core.tools.registry import ToolRegistry, ToolSpec


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    enabled: bool = True
    default_risk: RiskLevel = RiskLevel.SENSITIVE


class DynamicMCPArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPClientManager:
    """Importe les outils des serveurs MCP declares et leur assigne un niveau de risque par defaut (2)."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.servers: dict[str, MCPServerConfig] = {}

    def register_server(self, config: MCPServerConfig) -> None:
        self.servers[config.name] = config

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
            )
            self.registry.register(spec)
            imported.append(fq_name)
        return imported
