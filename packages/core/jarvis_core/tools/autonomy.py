"""Politique d'autonomie, liste blanche de domaines, arret d'urgence et garde-fous."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from jarvis_shared.models import RiskLevel
from jarvis_shared.rest_models import AutonomyPolicyConfig, AutonomyPolicyUpdate

from ..security import is_forbidden_ip_address, validate_http_target_host

__all__ = [
    "AUTONOMY_SETTING_KEY",
    "AutonomyPolicy",
    "DEFAULT_BLOCKED_DOMAINS",
    "NEVER_AUTO_APPROVE_TOOLS",
    "extract_domain",
    "is_forbidden_ip_address",
    "validate_http_target_host",
]

AUTONOMY_SETTING_KEY = "autonomy_policy"

DEFAULT_BLOCKED_DOMAINS: tuple[str, ...] = (
    "paypal.com",
    "stripe.com",
    "impots.gouv.fr",
)

NEVER_AUTO_APPROVE_TOOLS: frozenset[str] = frozenset(
    {
        "mail_send",
        "run_command",
        "run_cli_subagent",
        "write_file",
        "browser_upload_file",
    }
)

_DANGEROUS_MCP_TOOL_FRAGMENTS: tuple[str, ...] = (
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
    "write",
    "delete",
    "remove",
)


def _is_dangerous_mcp_tool_name(tool_name: str) -> bool:
    low = (tool_name or "").strip().lower()
    if not low.startswith("mcp_"):
        return False
    return any(frag in low for frag in _DANGEROUS_MCP_TOOL_FRAGMENTS)


_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_url_raw(url_or_host: str) -> str:
    return _CTRL_CHARS_RE.sub("", (url_or_host or "")).strip()


def extract_domain(url_or_host: str) -> str:
    """Extrait le nom de domaine normalise d'une URL ou d'un hote."""
    raw = _clean_url_raw(url_or_host)
    if not raw:
        return ""
    if raw.startswith("//"):
        parsed = urlparse(f"https:{raw}")
        host = (parsed.hostname or "").lower()
    elif "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
    else:
        parsed = urlparse(f"https://{raw}")
        host = (parsed.hostname or "").lower()
    return host.strip(".")


def _domain_matches(host: str, pattern: str) -> bool:
    pat = extract_domain(pattern.lstrip("*."))
    if not pat or not host:
        return False
    return host == pat or host.endswith(f".{pat}")


@dataclass
class AutonomyPolicy:
    """Controle l'execution autonome (niveaux 0..2), les domaines autorises et le kill switch."""

    mode: Literal["supervised", "autonomous"] = "supervised"
    max_auto_risk: int = int(RiskLevel.SENSITIVE)
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=lambda: list(DEFAULT_BLOCKED_DOMAINS))
    allow_browser_in_untrusted: bool = True
    block_shell_in_untrusted: bool = True
    max_steps: int = 0
    max_cost_usd: float = 10.0
    emergency_stop: bool = False
    never_auto_approve: set[str] = field(default_factory=lambda: set(NEVER_AUTO_APPROVE_TOOLS))

    @classmethod
    def from_config(cls, cfg: AutonomyPolicyConfig) -> AutonomyPolicy:
        return cls(
            mode=cfg.mode,
            max_auto_risk=int(cfg.max_auto_risk),
            allowed_domains=[d.strip().lower() for d in cfg.allowed_domains if d.strip()],
            blocked_domains=[d.strip().lower() for d in cfg.blocked_domains if d.strip()],
            allow_browser_in_untrusted=bool(cfg.allow_browser_in_untrusted),
            block_shell_in_untrusted=bool(cfg.block_shell_in_untrusted),
            max_steps=int(cfg.max_steps),
            max_cost_usd=float(cfg.max_cost_usd),
            emergency_stop=bool(cfg.emergency_stop),
        )

    def to_config(self) -> AutonomyPolicyConfig:
        safe_risk = min(max(int(self.max_auto_risk), 0), 3)
        return AutonomyPolicyConfig(
            mode=self.mode,
            max_auto_risk=safe_risk,  # type: ignore[arg-type]
            allowed_domains=list(self.allowed_domains),
            blocked_domains=list(self.blocked_domains),
            allow_browser_in_untrusted=self.allow_browser_in_untrusted,
            block_shell_in_untrusted=self.block_shell_in_untrusted,
            max_steps=self.max_steps,
            max_cost_usd=self.max_cost_usd,
            emergency_stop=self.emergency_stop,
        )

    def update(self, patch: AutonomyPolicyUpdate) -> AutonomyPolicyConfig:
        data = patch.model_dump(exclude_none=True)
        if "mode" in data:
            self.mode = data["mode"]
        if "max_auto_risk" in data:
            # Niveau 3 (DANGEROUS) ne peut jamais etre auto-approuve silencieusement
            self.max_auto_risk = min(int(data["max_auto_risk"]), int(RiskLevel.SENSITIVE))
        if "allowed_domains" in data:
            self.allowed_domains = [d.strip().lower() for d in data["allowed_domains"] if d.strip()]
        if "blocked_domains" in data:
            self.blocked_domains = [d.strip().lower() for d in data["blocked_domains"] if d.strip()]
        if "allow_browser_in_untrusted" in data:
            self.allow_browser_in_untrusted = bool(data["allow_browser_in_untrusted"])
        if "block_shell_in_untrusted" in data:
            self.block_shell_in_untrusted = bool(data["block_shell_in_untrusted"])
        if "max_steps" in data:
            self.max_steps = int(data["max_steps"])
        if "max_cost_usd" in data:
            self.max_cost_usd = float(data["max_cost_usd"])
        if "emergency_stop" in data:
            self.emergency_stop = bool(data["emergency_stop"])
        return self.to_config()

    def save_to_db(self, db: Any) -> None:
        if db is not None and hasattr(db, "set_setting"):
            db.set_setting(AUTONOMY_SETTING_KEY, json.dumps(self.to_config().model_dump()))

    @classmethod
    def load_from_db(cls, db: Any) -> AutonomyPolicy:
        if db is None or not hasattr(db, "get_setting"):
            return cls()
        raw = db.get_setting(AUTONOMY_SETTING_KEY)
        if not raw:
            return cls()
        try:
            cfg = AutonomyPolicyConfig.model_validate(json.loads(raw))
            return cls.from_config(cfg)
        except Exception:
            return cls()

    def check_url_allowed(self, url: str) -> tuple[bool, str | None]:
        """Verifie qu'une URL respecte le schema http/https et les listes noire/blanche de domaines."""
        raw = _clean_url_raw(url)
        if not raw:
            return True, None
        if raw.startswith("//"):
            return False, "URL relative au protocole (//...) interdite."
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "").lower()
        if not scheme:
            return False, "Schema URL manquant : seul http:// ou https:// est autorise."
        if scheme not in {"http", "https"}:
            return False, f"Schema URL interdit en navigation autonome : {scheme}"
        host = (parsed.hostname or "").lower().strip(".")
        if not host:
            return False, "Nom d'hote manquant dans l'URL."
        for blocked in self.blocked_domains:
            if _domain_matches(host, blocked):
                return False, f"Domaine '{host}' bloque par la politique de securite ({blocked})."
        if self.allowed_domains:
            if not any(_domain_matches(host, allowed) for allowed in self.allowed_domains):
                return False, f"Domaine '{host}' absent de la liste blanche autorisee."
        return True, None

    def is_ui_tool(self, tool_name: str, is_interactive_ui: bool = False) -> bool:
        if tool_name in self.never_auto_approve or _is_dangerous_mcp_tool_name(tool_name):
            return False
        if tool_name.startswith("mcp_"):
            return bool(is_interactive_ui)
        if is_interactive_ui:
            return True
        return tool_name.startswith(("browser_", "desktop_"))

    def can_bypass_untrusted_guard(
        self,
        tool_name: str,
        risk: RiskLevel,
        *,
        is_interactive_ui: bool = False,
        autonomous_run: bool = False,
    ) -> bool:
        """Autorise les interactions UI (niveaux <= 2) sur contexte <untrusted> uniquement en mode autonome."""
        if risk >= RiskLevel.DANGEROUS:
            return False
        if tool_name in self.never_auto_approve or _is_dangerous_mcp_tool_name(tool_name):
            return False
        effective_auto = self.mode == "autonomous" or autonomous_run
        if not effective_auto or not self.allow_browser_in_untrusted:
            return False
        return self.is_ui_tool(tool_name, is_interactive_ui=is_interactive_ui)

    def ui_tool_needs_approval_in_untrusted(
        self,
        tool_name: str,
        risk: RiskLevel,
        *,
        is_interactive_ui: bool = False,
    ) -> bool:
        """Un outil UI (niveau <= 2) reste utilisable sur contexte <untrusted> en mode supervise.

        Lire une page met le tour en <untrusted> : sans cette exception, la boucle
        snapshot -> clic serait refusee en bloc alors que le clic sur une page lue est
        precisement le travail demande. Le garde-fou ne disparait pas : l'outil repasse
        par la matrice d'approbation (validation humaine) au lieu d'un refus sec.
        """
        if risk >= RiskLevel.DANGEROUS:
            return False
        if not self.allow_browser_in_untrusted:
            return False
        return self.is_ui_tool(tool_name, is_interactive_ui=is_interactive_ui)

    def should_auto_approve(
        self,
        tool_name: str,
        risk: RiskLevel,
        *,
        autonomous_run: bool = False,
        is_interactive_ui: bool = False,
    ) -> bool:
        """Determine si un outil niveau 2 peut s'executer sans validation humaine."""
        _ = is_interactive_ui
        if self.emergency_stop:
            return False
        if risk >= RiskLevel.DANGEROUS:
            return False
        if tool_name in self.never_auto_approve or _is_dangerous_mcp_tool_name(tool_name):
            return False
        effective_auto = self.mode == "autonomous" or autonomous_run
        if not effective_auto:
            return False
        effective_cap = min(int(self.max_auto_risk), int(RiskLevel.SENSITIVE))
        return int(risk) <= effective_cap
