"""Construction du runtime JARVIS 2.0 : providers, routeur, outils, memoire, attention, canaux et agent."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jarvis_shared.models import (
    AgentState,
    ApprovalRequest,
    Message,
    ModelCapabilities,
    NotificationRecord,
    StreamDelta,
    Usage,
)

from jarvis_core.agent.autonomous import AutonomousTaskManager, AutonomyToolkit
from jarvis_core.agent.loop import AgentLoop
from jarvis_core.agent.orchestrator import GoalManager, GoalToolkit
from jarvis_core.attention.engine import AttentionDecision, AttentionEngine
from jarvis_core.attention.presence import PresenceDetector
from jarvis_core.channels.telegram import TelegramGateway, build_alert_keyboard
from jarvis_core.llm.base import LLMProvider
from jarvis_core.llm.catalog import ModelSelectionCatalog
from jarvis_core.llm.gemini import GeminiProvider
from jarvis_core.llm.ollama import OllamaProvider
from jarvis_core.llm.openrouter import OpenRouterProvider
from jarvis_core.llm.router import DEFAULT_ROLE_ROUTES, LLMRouter, RoleRoute
from jarvis_core.mcp.client import MCPClientManager
from jarvis_core.memory.engine import MemoryEngine
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.memory.storage import MemoryStorage
from jarvis_core.memory.wiki import MarkdownWiki
from jarvis_core.notifications.dispatcher import ChannelDispatcher
from jarvis_core.notifications.policy import PolicyStore
from jarvis_core.security import SecretStore
from jarvis_core.tools.approvals import ApprovalManager
from jarvis_core.tools.autonomy import AutonomyPolicy
from jarvis_core.tools.browser import BrowserToolkit
from jarvis_core.tools.cli_agents import CLIAgentToolkit
from jarvis_core.tools.desktop_a11y import DesktopA11yToolkit
from jarvis_core.tools.integrations import IntegrationToolkit
from jarvis_core.tools.notifications import NotificationToolkit
from jarvis_core.tools.registry import ToolRegistry, detect_platform
from jarvis_core.tools.standard import StandardToolkit

logger = logging.getLogger(__name__)


class _OfflineProvider(LLMProvider):
    """Fournisseur de repli local quand aucune cle LLM n'est configuree (mode offline)."""

    name = "offline"

    async def fetch_available_models(self) -> dict[str, ModelCapabilities]:
        return {}

    async def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> StreamDelta:
        text = "Mode hors-ligne actif : configurez OPENROUTER_API_KEY ou GEMINI_API_KEY dans .env."
        tokens_in = sum(len(m.content.split()) for m in messages if m.content)
        return StreamDelta(
            text=text,
            usage=Usage(
                model=model, provider=self.name, tokens_in=tokens_in, tokens_out=14, cost_usd=0.0
            ),
        )

    async def stream(
        self,
        model: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamDelta]:
        delta = await self.complete(model, messages, tools, fallback_models, temperature)
        yield delta


def _provider_is_usable(prov: LLMProvider | None) -> bool:
    """Un fournisseur compte s'il a une cle API ou un projet Vertex exploitable."""
    if prov is None:
        return False
    key = getattr(prov, "api_key", None)
    if key is None:
        return True  # repli hors-ligne : pas de cle requise
    if str(key).strip():
        return True
    return bool(getattr(prov, "vertexai", False) and getattr(prov, "project", None))


class _TelegramSender:
    def __init__(self, gateway: TelegramGateway) -> None:
        self.gateway = gateway

    async def send(self, message: str, urgency: int = 1, inline_keyboard: Any = None) -> Any:
        if not self.gateway.bot_token or self.gateway.allowed_chat_id is None:
            return {"ok": False}
        from aiogram import Bot
        from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

        bot = Bot(token=self.gateway.bot_token)
        try:
            markup = None
            if inline_keyboard:
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"])
                            for b in row
                        ]
                        for row in inline_keyboard
                    ]
                )
            sent = await bot.send_message(
                chat_id=self.gateway.allowed_chat_id,
                text=message,
                disable_notification=int(urgency) <= 1,
                reply_markup=markup,
            )
            if int(urgency) >= 3:
                try:
                    voice_bytes = await asyncio.to_thread(self.gateway.synthesize_voice, message)
                    if voice_bytes:
                        await bot.send_voice(
                            chat_id=self.gateway.allowed_chat_id,
                            voice=BufferedInputFile(voice_bytes, filename="alerte.mp3"),
                            caption="🚨 Alerte vocale Jarvis",
                        )
                except Exception:
                    pass
            return sent
        finally:
            await bot.session.close()


@dataclass
class JarvisRuntime:
    database: MemoryDatabase
    router: LLMRouter
    tool_registry: ToolRegistry
    memory_engine: MemoryEngine
    approvals: ApprovalManager
    agent: AgentLoop
    approval_queue: asyncio.Queue[ApprovalRequest]
    attention: AttentionEngine
    channels: dict[str, Any]
    alert_queue: asyncio.Queue[tuple[NotificationRecord, AttentionDecision]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=64)
    )
    policy_store: PolicyStore | None = None
    dispatcher: ChannelDispatcher | None = None
    autonomy_policy: AutonomyPolicy | None = None
    task_manager: AutonomousTaskManager | None = None
    goal_catalog: ModelSelectionCatalog | None = None
    goal_manager: GoalManager | None = None
    mcp_manager: MCPClientManager | None = None
    browser_toolkit: BrowserToolkit | None = None
    scheduler: Any = None


# Canaux qui correspondent a une surface d'affichage locale (UI web, voix de la sphere,
# notification bureau). L'AttentionEngine les nomme « sphere / toast / voice » alors que
# la file WebSocket attend « web / desktop / voice » : sans cette table de correspondance,
# toute notification de niveau 1 ou 2 est silencieusement perdue (seul le niveau 3+
# declenchait un envoi Telegram).
UI_CHANNELS = frozenset({"sphere", "sphere_alert", "toast", "web", "desktop", "voice"})


def _format_alert_telegram_message(notif: NotificationRecord, decision: AttentionDecision) -> str:
    """Formate une alerte Telegram en precisant la raison de l'etat d'alerte."""
    reason = (decision.reason or notif.reason or "").strip()
    msg = (notif.message or "").strip()
    if reason and reason.lower() not in msg.lower():
        return f"🚨 Alerte JARVIS : {reason}\n{msg}" if msg else f"🚨 Alerte JARVIS : {reason}"
    detail = msg or reason or "État d'alerte déclenché"
    return detail if detail.startswith("🚨") else f"🚨 Alerte JARVIS : {detail}"


def build_notification_dispatcher(
    senders: dict[str, Any],
    alert_queue: asyncio.Queue[tuple[NotificationRecord, AttentionDecision]],
    schedule_send: Callable[..., None],
) -> Callable[[NotificationRecord, AttentionDecision], None]:
    """Construit la fonction de distribution : envoie sur les canaux reels et pousse a l'UI."""

    def _dispatch(notif: NotificationRecord, decision: AttentionDecision) -> None:
        urgency_val = int(notif.urgency)
        is_alert = urgency_val >= 3
        alert_text = _format_alert_telegram_message(notif, decision) if is_alert else ""
        alert_kb = build_alert_keyboard(notif.dedup_key or notif.id) if is_alert else None
        telegram_sent = False

        for channel in decision.channels:
            sender = senders.get(channel)
            if sender is None:
                continue
            if is_alert and channel in ("telegram", "telegram_silent"):
                telegram_sent = True
                schedule_send(sender, alert_text, urgency_val, inline_keyboard=alert_kb)
            else:
                schedule_send(sender, notif.message, urgency_val)

        if is_alert and not telegram_sent:
            tg_sender = senders.get("telegram")
            if tg_sender is not None:
                schedule_send(tg_sender, alert_text, urgency_val, inline_keyboard=alert_kb)

        if is_alert or any(ch in UI_CHANNELS for ch in decision.channels):
            if alert_queue.full():
                try:
                    alert_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                alert_queue.put_nowait((notif, decision))
            except asyncio.QueueFull:
                pass

    return _dispatch


def _load_roles(config_dir: Path) -> dict[str, RoleRoute]:
    models_file = config_dir / "models.yaml"
    if not models_file.exists():
        return DEFAULT_ROLE_ROUTES
    raw = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
    default_provider = str(raw.get("defaults", {}).get("provider", "openrouter"))
    roles: dict[str, RoleRoute] = {}
    for name, cfg in (raw.get("roles") or {}).items():
        if not isinstance(cfg, dict):
            continue
        roles[str(name)] = RoleRoute(
            role_name=str(name),
            model=str(cfg.get("model", "")),
            provider=str(cfg.get("provider", default_provider)),
            fallback=[str(f) for f in cfg.get("fallback", [])],
        )
    return roles or DEFAULT_ROLE_ROUTES


def _build_channels(store: SecretStore, database: MemoryDatabase | None = None) -> dict[str, Any]:
    senders: dict[str, Any] = {}
    tg_token = store.get_secret("TELEGRAM_BOT_TOKEN")
    tg_chat = store.get_secret("TELEGRAM_ALLOWED_CHAT_ID")
    if tg_token and tg_chat:
        tg_sender = _TelegramSender(TelegramGateway(bot_token=tg_token, allowed_chat_id=tg_chat))
        senders["telegram"] = tg_sender
        senders["telegram_silent"] = tg_sender

    return senders


def build_runtime(
    data_dir: Path,
    config_dir: Path,
    secrets: SecretStore | None = None,
    on_state_change: Callable[[AgentState], Any] | None = None,
    on_sentence_ready: Callable[[str], Any] | None = None,
    *,
    database: MemoryDatabase | None = None,
    providers: dict[str, LLMProvider] | None = None,
    channels: dict[str, Any] | None = None,
    allow_offline: bool = False,
    on_ws_event: Callable[[str, dict[str, Any]], Any] | None = None,
) -> JarvisRuntime:
    from dotenv import load_dotenv

    load_dotenv(override=False)
    store = secrets or SecretStore()
    active_providers: dict[str, LLMProvider] = dict(providers) if providers is not None else {}
    if not active_providers:
        or_key = store.get_secret("OPENROUTER_API_KEY")
        if or_key:
            active_providers["openrouter"] = OpenRouterProvider(api_key=or_key)
        gem_key = store.get_secret("GEMINI_API_KEY")
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() in ("true", "1", "yes")
        # Vertex sans projet configure ne peut rien appeler : le considerer comme absent
        # evite de masquer le mode hors-ligne (et le repli local des proposants).
        if use_vertex and not store.get_secret("GOOGLE_CLOUD_PROJECT"):
            use_vertex = False
        if gem_key or use_vertex:
            active_providers["gemini"] = GeminiProvider(api_key=gem_key or "")
        ollama_url = store.get_secret("OLLAMA_BASE_URL")
        if ollama_url:
            active_providers["ollama"] = OllamaProvider(base_url=ollama_url)
    if allow_offline and not any(_provider_is_usable(p) for p in active_providers.values()):
        # Aucun fournisseur reellement utilisable : on bascule sur le repli hors-ligne
        # plutot que de laisser le routeur echouer a chaque tour de parole.
        offline = _OfflineProvider()
        active_providers.setdefault("openrouter", offline)
        active_providers.setdefault("gemini", offline)
    if not active_providers:
        raise RuntimeError("Aucune cle LLM configuree (OPENROUTER_API_KEY ou GEMINI_API_KEY).")
    if providers is None and "ollama" not in active_providers:
        active_providers["ollama"] = OllamaProvider()

    db = database or MemoryDatabase(data_dir / "jarvis.db")
    storage = MemoryStorage(data_dir / "jarvis.db", database=db)
    wiki = MarkdownWiki(data_dir / "wiki", auto_git=False)
    policy_store = PolicyStore(db, wiki_dir=wiki.root_dir if wiki else None)
    policy = policy_store.get()

    raw_budget = db.get_setting("daily_budget_usd")
    daily_budget = float(raw_budget) if raw_budget is not None else 5.0
    router = LLMRouter(
        providers=active_providers,
        roles=_load_roles(config_dir),
        daily_budget_usd=daily_budget,
        usage_logger=lambda role, usage: storage.log_llm_call(role, usage),
        daily_spend_getter=storage.get_today_spend_usd,
    )

    senders = _build_channels(store, database=db)
    if channels:
        senders.update(channels)

    dispatcher = ChannelDispatcher(
        db=db,
        secret_store=store,
        public_base_url=store.get_secret("PUBLIC_WEBHOOK_BASE_URL"),
        telegram_sender=senders.get("telegram"),
        action_token_secret=store.get_secret("ACTION_TOKEN_SECRET"),
    )

    approval_queue: asyncio.Queue[ApprovalRequest] = asyncio.Queue()

    def _on_request(req: ApprovalRequest) -> None:
        db.create_approval(
            tool_name=req.tool_name,
            description=req.description,
            args=req.arguments,
            risk=int(req.risk),
            expires_at=req.expires_at,
            approval_id=req.id,
        )
        approval_queue.put_nowait(req)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(dispatcher.request_approval(req, policy_store.get()))
        except RuntimeError:
            pass

    autonomy_policy = AutonomyPolicy.load_from_db(db)

    def _record_db_audit(entry: dict[str, Any]) -> None:
        args = entry.get("arguments") or {}
        target = str(
            args.get("url") or args.get("ref") or args.get("command") or args.get("file_path") or ""
        )
        db.record_autonomy_audit(
            tool_name=str(entry.get("name", "")),
            risk=int(entry.get("risk", 0)),
            status=str(entry.get("status", "")),
            target=target,
            summary=str(args)[:400],
            task_id=entry.get("task_id"),
            error=entry.get("error"),
        )

    approvals = ApprovalManager(
        ttl_seconds=float(policy.approval_ttl_seconds),
        voice_pin=store.get_secret("JARVIS_VOICE_PIN") or None,
        on_approval_requested=_on_request,
    )
    registry = ToolRegistry(
        approval_manager=approvals,
        current_platform=detect_platform(),
        autonomy_policy=autonomy_policy,
        audit_recorder=_record_db_audit,
    )
    StandardToolkit(database=db).register_all(registry)
    NotificationToolkit(database=db).register_all(registry)
    IntegrationToolkit().register_all(registry)
    DesktopA11yToolkit().register_all(registry)
    CLIAgentToolkit().register_all(registry)

    mcp_manager = MCPClientManager(registry)
    mcp_manager.load_from_yaml(config_dir / "mcp.yaml")

    # Le serveur MCP Playwright et les outils browser_* pilotent le meme port CDP : on
    # n'active qu'une seule implementation a la fois (sinon deux jeux d'outils concurrents
    # sur les memes onglets et un prompt inutilement double).
    playwright_mcp = mcp_manager.servers.get("playwright")
    browser_toolkit: BrowserToolkit | None = None
    if playwright_mcp is not None and playwright_mcp.enabled:
        logger.warning(
            "Serveur MCP 'playwright' active : outils browser_* integres non enregistres "
            "(une seule implementation du controle navigateur a la fois)."
        )
    else:
        browser_toolkit = BrowserToolkit(autonomy_policy=autonomy_policy)
        browser_toolkit.register_all(registry)

    alert_queue: asyncio.Queue[tuple[NotificationRecord, AttentionDecision]] = asyncio.Queue(
        maxsize=64
    )

    def _schedule_send(
        sender: Any,
        text: str,
        urgency: int,
        inline_keyboard: Any = None,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
            if inline_keyboard is not None and isinstance(sender, _TelegramSender):
                coro = sender.send(text, urgency=urgency, inline_keyboard=inline_keyboard)
            else:
                coro = sender.send(text, urgency=urgency)
            loop.create_task(coro)
        except RuntimeError:
            pass

    dispatch_notification = build_notification_dispatcher(senders, alert_queue, _schedule_send)

    q_start_h = int(policy.quiet_start.split(":")[0]) if ":" in policy.quiet_start else 23
    q_end_h = int(policy.quiet_end.split(":")[0]) if ":" in policy.quiet_end else 8
    attention = AttentionEngine(
        presence=PresenceDetector(quiet_start_hour=q_start_h, quiet_end_hour=q_end_h),
        storage=db,
        router=router,
        on_dispatch=dispatch_notification,
        policy_store=policy_store,
    )

    from .memory.engine import DEFAULT_PERSONA_PROMPT

    memory_engine = MemoryEngine(
        storage=storage,
        wiki=wiki,
        persona_prompt=DEFAULT_PERSONA_PROMPT,
    )
    task_manager = AutonomousTaskManager(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        database=db,
        policy=autonomy_policy,
        channels=senders,
        alert_queue=alert_queue,
    )
    AutonomyToolkit(task_manager).register_all(registry)

    goal_catalog = ModelSelectionCatalog.from_yaml(config_dir)
    goal_manager = GoalManager(
        router=router,
        tool_registry=registry,
        catalog=goal_catalog,
        memory_engine=memory_engine,
        database=db,
        policy=autonomy_policy,
        channels=senders,
        alert_queue=alert_queue,
        on_ws_event=on_ws_event,
    )
    GoalToolkit(goal_manager).register_all(registry)

    MAIN_AGENT_EXCLUDED_TOOLS = {"write_file", "run_command"}
    agent = AgentLoop(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        excluded_tools=MAIN_AGENT_EXCLUDED_TOOLS,
        on_state_change=on_state_change,
        on_sentence_ready=on_sentence_ready,
    )
    return JarvisRuntime(
        database=db,
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        approvals=approvals,
        agent=agent,
        approval_queue=approval_queue,
        attention=attention,
        channels=senders,
        alert_queue=alert_queue,
        policy_store=policy_store,
        dispatcher=dispatcher,
        autonomy_policy=autonomy_policy,
        task_manager=task_manager,
        goal_catalog=goal_catalog,
        goal_manager=goal_manager,
        mcp_manager=mcp_manager,
        browser_toolkit=browser_toolkit,
    )
