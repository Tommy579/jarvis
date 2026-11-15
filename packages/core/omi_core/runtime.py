"""Construction du runtime OMI 2.0 : providers, routeur, outils, memoire, attention, canaux et agent."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from omi_shared.models import (
    AgentState,
    ApprovalRequest,
    Message,
    ModelCapabilities,
    NotificationRecord,
    StreamDelta,
    Usage,
)

from omi_core.agent.loop import AgentLoop
from omi_core.attention.engine import AttentionDecision, AttentionEngine
from omi_core.attention.presence import PresenceDetector
from omi_core.channels.telegram import TelegramGateway
from omi_core.notifications.dispatcher import ChannelDispatcher
from omi_core.notifications.policy import PolicyStore
from omi_core.tools.integrations import IntegrationToolkit
from omi_core.tools.notifications import NotificationToolkit
from omi_core.llm.base import LLMProvider
from omi_core.llm.gemini import GeminiProvider
from omi_core.llm.ollama import OllamaProvider
from omi_core.llm.openrouter import OpenRouterProvider
from omi_core.llm.router import DEFAULT_ROLE_ROUTES, LLMRouter, RoleRoute
from omi_core.memory.engine import MemoryEngine
from omi_core.memory.schema import MemoryDatabase
from omi_core.memory.storage import MemoryStorage
from omi_core.memory.wiki import MarkdownWiki
from omi_core.security import SecretStore
from omi_core.tools.approvals import ApprovalManager
from omi_core.tools.registry import ToolRegistry, detect_platform
from omi_core.tools.standard import StandardToolkit


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
                    voice_bytes = await asyncio.to_thread(
                        self.gateway.synthesize_elevenlabs, message
                    )
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
class OmiRuntime:
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
) -> OmiRuntime:
    from dotenv import load_dotenv
    load_dotenv(override=False)
    store = secrets or SecretStore()
    active_providers: dict[str, LLMProvider] = dict(providers) if providers is not None else {}
    if not active_providers:
        or_key = store.get_secret("OPENROUTER_API_KEY")
        if or_key:
            active_providers["openrouter"] = OpenRouterProvider(api_key=or_key)
        gem_key = store.get_secret("GEMINI_API_KEY")
        if gem_key:
            active_providers["gemini"] = GeminiProvider(api_key=gem_key)
        ollama_url = store.get_secret("OLLAMA_BASE_URL")
        if ollama_url:
            active_providers["ollama"] = OllamaProvider(base_url=ollama_url)
    if not active_providers:
        if allow_offline:
            offline = _OfflineProvider()
            active_providers = {"openrouter": offline, "gemini": offline}
        else:
            raise RuntimeError("Aucune cle LLM configuree (OPENROUTER_API_KEY ou GEMINI_API_KEY).")
    if providers is None and "ollama" not in active_providers:
        active_providers["ollama"] = OllamaProvider()

    db = database or MemoryDatabase(data_dir / "omi.db")
    storage = MemoryStorage(data_dir / "omi.db", database=db)
    wiki = MarkdownWiki(data_dir / "wiki", auto_git=False)
    policy_store = PolicyStore(db, wiki_dir=wiki.root_dir if wiki else None)
    policy = policy_store.get()

    router = LLMRouter(
        providers=active_providers,
        roles=_load_roles(config_dir),
        daily_budget_usd=float(db.get_setting("daily_budget_usd", "5.0") or 5.0),
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

    approvals = ApprovalManager(
        ttl_seconds=float(policy.approval_ttl_seconds),
        voice_pin=store.get_secret("OMI_VOICE_PIN") or None,
        on_approval_requested=_on_request,
    )
    registry = ToolRegistry(approval_manager=approvals, current_platform=detect_platform())
    StandardToolkit(database=db).register_all(registry)
    NotificationToolkit(database=db).register_all(registry)
    IntegrationToolkit().register_all(registry)

    alert_queue: asyncio.Queue[tuple[NotificationRecord, AttentionDecision]] = asyncio.Queue(
        maxsize=64
    )

    def _dispatch(notif: NotificationRecord, decision: AttentionDecision) -> None:
        for channel in decision.channels:
            sender = senders.get(channel)
            if sender is None:
                continue
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(sender.send(notif.message, urgency=int(notif.urgency)))
            except RuntimeError:
                pass
        if int(notif.urgency) >= 3 or any(
            ch in decision.channels for ch in ("desktop", "web", "voice")
        ):
            if alert_queue.full():
                try:
                    alert_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                alert_queue.put_nowait((notif, decision))
            except asyncio.QueueFull:
                pass

    q_start_h = int(policy.quiet_start.split(":")[0]) if ":" in policy.quiet_start else 23
    q_end_h = int(policy.quiet_end.split(":")[0]) if ":" in policy.quiet_end else 8
    attention = AttentionEngine(
        presence=PresenceDetector(quiet_start_hour=q_start_h, quiet_end_hour=q_end_h),
        storage=db,
        router=router,
        on_dispatch=_dispatch,
        policy_store=policy_store,
    )

    from .memory.engine import DEFAULT_PERSONA_PROMPT

    memory_engine = MemoryEngine(
        storage=storage,
        wiki=wiki,
        persona_prompt=DEFAULT_PERSONA_PROMPT,
    )
    agent = AgentLoop(
        router=router,
        tool_registry=registry,
        memory_engine=memory_engine,
        on_state_change=on_state_change,
        on_sentence_ready=on_sentence_ready,
    )
    return OmiRuntime(
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
    )
