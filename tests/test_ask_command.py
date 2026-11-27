"""Tests pour la commande /ask (approbation terminal niveau 3+ et execution libre par defaut)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from jarvis_core.agent.loop import AgentLoop
from jarvis_core.llm.base import LLMProvider
from jarvis_core.llm.router import LLMRouter
from jarvis_core.tools.approvals import ApprovalManager
from jarvis_core.tools.registry import ToolRegistry, ToolSpec, extract_ask_flag
from jarvis_core.tools.standard import RunCommandArgs
from jarvis_shared.models import Message, RiskLevel, StreamDelta, ToolCall, Usage


def test_extract_ask_flag_variants() -> None:
    assert extract_ask_flag("/ask nettoie /tmp") == (True, "nettoie /tmp")
    assert extract_ask_flag("/ASK   fais le menage") == (True, "fais le menage")
    assert extract_ask_flag("/ask") == (True, "")
    assert extract_ask_flag("[Workspace actif : /home/tom]\n/ask liste") == (
        True,
        "[Workspace actif : /home/tom]\nliste",
    )
    assert extract_ask_flag("liste les fichiers") == (False, "liste les fichiers")


def test_run_command_without_ask_allows_all_risk_levels() -> None:
    requested: list[Any] = []
    approvals = ApprovalManager(ttl_seconds=5.0, on_approval_requested=requested.append)
    registry = ToolRegistry(approval_manager=approvals)
    executed: list[str] = []

    registry.register(
        ToolSpec(
            name="run_command",
            description="Terminal",
            risk=RiskLevel.SENSITIVE,
            args_model=RunCommandArgs,
            handler=lambda command, **_: (
                executed.append(command),
                {"ok": True, "command": command},
            )[1],
        )
    )

    res_l2 = asyncio.run(
        registry.execute(
            ToolCall(id="1", name="run_command", arguments={"command": "echo ok"}),
            ask_terminal_approval=False,
        )
    )
    res_l3 = asyncio.run(
        registry.execute(
            ToolCall(id="2", name="run_command", arguments={"command": "rm -rf /"}),
            ask_terminal_approval=False,
        )
    )

    assert not res_l2.is_error
    assert not res_l3.is_error
    assert executed == ["echo ok", "rm -rf /"]
    assert requested == []


def test_run_command_with_ask_notifies_only_from_level_3() -> None:
    requested: list[Any] = []
    approvals = ApprovalManager(ttl_seconds=5.0, on_approval_requested=requested.append)
    registry = ToolRegistry(approval_manager=approvals)
    executed: list[str] = []

    registry.register(
        ToolSpec(
            name="run_command",
            description="Terminal",
            risk=RiskLevel.SENSITIVE,
            args_model=RunCommandArgs,
            handler=lambda command, **_: (
                executed.append(command),
                {"ok": True, "command": command},
            )[1],
        )
    )

    # Niveau 2 : pas de notification, execute directement
    res_l2 = asyncio.run(
        registry.execute(
            ToolCall(id="1", name="run_command", arguments={"command": "ls -la"}),
            ask_terminal_approval=True,
        )
    )
    assert not res_l2.is_error
    assert executed == ["ls -la"]
    assert requested == []

    # Niveau 3 : notification envoyee et execution bloquee si refusee/expiree
    res_l3_denied = asyncio.run(
        registry.execute(
            ToolCall(id="2", name="run_command", arguments={"command": "shutdown -h now"}),
            ask_terminal_approval=True,
            approval_timeout=0.01,
        )
    )
    assert res_l3_denied.is_error is True
    assert len(requested) == 1
    assert requested[0].risk == RiskLevel.DANGEROUS
    assert requested[0].exact_command == "shutdown -h now"
    assert executed == ["ls -la"]

    # Niveau 3 : si approuvee par l'utilisateur, execution autorisee
    async def _approve_after_request() -> Any:
        task = asyncio.create_task(
            registry.execute(
                ToolCall(id="3", name="run_command", arguments={"command": "mkfs.ext4 /dev/sdb1"}),
                ask_terminal_approval=True,
                approval_timeout=2.0,
            )
        )
        await asyncio.sleep(0.02)
        approvals.resolve(requested[-1].id, approved=True, channel="web")
        return await task

    res_l3_ok = asyncio.run(_approve_after_request())
    assert not res_l3_ok.is_error
    assert len(requested) == 2
    assert executed == ["ls -la", "mkfs.ext4 /dev/sdb1"]


class _ScriptedToolProvider(LLMProvider):
    name = "openrouter"

    def __init__(self, command: str) -> None:
        self.command = command
        self.calls = 0

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamDelta]:
        self.calls += 1
        if self.calls == 1:
            yield StreamDelta(
                tool_calls=[
                    ToolCall(id="tc-1", name="run_command", arguments={"command": self.command})
                ],
                usage=Usage(model="mock", tokens_in=10, tokens_out=5, cost_usd=0.0),
            )
        else:
            yield StreamDelta(
                text="Termine.",
                usage=Usage(model="mock", tokens_in=10, tokens_out=5, cost_usd=0.0),
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] for _ in texts]

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def fetch_available_models(self) -> list[str]:
        return ["mock"]


def test_agent_loop_parses_slash_ask_from_prompt() -> None:
    requested: list[Any] = []
    approvals = ApprovalManager(ttl_seconds=0.02, on_approval_requested=requested.append)
    registry = ToolRegistry(approval_manager=approvals)
    executed: list[str] = []
    registry.register(
        ToolSpec(
            name="run_command",
            description="Terminal",
            risk=RiskLevel.SENSITIVE,
            args_model=RunCommandArgs,
            handler=lambda command, **_: (executed.append(command), {"ok": True})[1],
        )
    )

    router_no_ask = LLMRouter({"openrouter": _ScriptedToolProvider("rm -rf /")})
    loop_no_ask = AgentLoop(router=router_no_ask, tool_registry=registry)
    res1 = asyncio.run(loop_no_ask.run_turn("supprime tout", fallback_models_override=[]))
    assert res1.error is None
    assert executed == ["rm -rf /"]
    assert requested == []

    router_ask = LLMRouter({"openrouter": _ScriptedToolProvider("rm -rf /")})
    loop_ask = AgentLoop(router=router_ask, tool_registry=registry)
    res2 = asyncio.run(loop_ask.run_turn("/ask supprime tout", fallback_models_override=[]))
    assert res2.error is None
    assert executed == ["rm -rf /"]  # pas re-execute car refuse/expire
    assert len(requested) == 1
    assert requested[0].risk == RiskLevel.DANGEROUS


def test_telegram_ask_command_and_minimal_alerts() -> None:
    from jarvis_core.attention.engine import AttentionDecision
    from jarvis_core.channels.telegram import TelegramGateway
    from jarvis_core.notifications.dispatcher import ChannelDispatcher
    from jarvis_core.runtime import _format_alert_telegram_message
    from jarvis_shared.models import ApprovalRequest, NotificationRecord, UrgencyLevel

    gw = TelegramGateway(bot_token="fake", allowed_chat_id=123)
    res = asyncio.run(gw.handle_text(123, "/ask nettoie /tmp"))
    assert res.authorized is True
    assert "inconnue" not in res.reply_text.lower()

    notif = NotificationRecord(
        id="n1",
        source="system",
        channel="telegram",
        title="Alerte",
        message="CPU a 98% sur le noeud principal ... [suite]",
        urgency=UrgencyLevel.URGENT,
    )
    decision = AttentionDecision(
        urgency=UrgencyLevel.URGENT,
        channels=["telegram"],
        message="CPU a 98%",
        reason="Charge elevee [suite]",
        dedup_key="cpu_high",
    )
    formatted = _format_alert_telegram_message(notif, decision)
    assert "[suite]" not in formatted
    assert "🚨" not in formatted
    assert "CPU a 98%" in formatted

    sent_messages: list[tuple[str, Any]] = []

    class _DummyTg:
        async def send(self, text: str, urgency: int = 1, inline_keyboard: Any = None) -> None:
            sent_messages.append((text, inline_keyboard))

    dispatcher = ChannelDispatcher(telegram_sender=_DummyTg())
    req = ApprovalRequest(
        id="app_1",
        tool_name="run_command",
        arguments={"command": "rm -rf /"},
        description="Executer la commande : rm -rf /",
        risk=RiskLevel.DANGEROUS,
        exact_command="rm -rf /",
        expires_at="2099-01-01T00:00:00Z",
    )
    asyncio.run(dispatcher._send_approval("telegram", req))
    assert len(sent_messages) == 1
    msg_text, kb = sent_messages[0]
    for emoji in ("🚨", "✅", "❌"):
        assert emoji not in msg_text
        assert all(emoji not in btn["text"] for row in kb for btn in row)
    assert "[suite]" not in msg_text


def test_telegram_persistent_ask_and_auto_mode(tmp_path: Any) -> None:
    from jarvis_core.api.server import (
        JarvisCoreState,
        execute_telegram_command,
        execute_telegram_text,
    )

    state = JarvisCoreState(data_dir=tmp_path / "data")
    captured: list[str] = []

    async def _fake_turn(text: str, **_: Any) -> Any:
        captured.append(text)
        return type("_Msg", (), {"content": "ok"})()

    state.run_chat_turn = _fake_turn  # type: ignore[method-assign]

    # Par defaut sur Telegram : mode auto
    asyncio.run(execute_telegram_text(state, "liste /tmp"))
    assert captured[-1] == "liste /tmp"

    # /ask sans argument bascule en mode ask persistant
    reply_ask = asyncio.run(execute_telegram_command(state, "ask", ""))
    assert "mode ask" in reply_ask.lower()
    asyncio.run(execute_telegram_text(state, "supprime /tmp/x"))
    assert captured[-1].startswith("/ask ")

    # /auto sans argument repasse en mode auto
    reply_auto = asyncio.run(execute_telegram_command(state, "auto", ""))
    assert "mode auto" in reply_auto.lower()
    asyncio.run(execute_telegram_text(state, "liste /tmp"))
    assert captured[-1] == "liste /tmp"
