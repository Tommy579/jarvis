"""Tests pour TelegramGateway et les interactions bidirectionnelles Telegram."""

from pathlib import Path

import pytest
from jarvis_core.api.server import (
    JarvisCoreState,
    execute_telegram_command,
    execute_telegram_text,
    resolve_workspace_directory,
)
from jarvis_core.channels.telegram import TELEGRAM_SUPPORTED_COMMANDS, TelegramGateway


@pytest.mark.asyncio
async def test_telegram_gateway_permissions() -> None:
    gw = TelegramGateway(bot_token="fake_tok", allowed_chat_id=123456)

    # Non autorisé
    res_unauth = await gw.handle_text(999999, "Hello")
    assert res_unauth.authorized is False
    assert 999999 in gw.blocked_attempts

    # Autorisé
    res_auth = await gw.handle_text(123456, "Hello Jarvis")
    assert res_auth.authorized is True
    assert "Recu" in res_auth.reply_text


@pytest.mark.asyncio
async def test_telegram_gateway_commands_and_callbacks() -> None:
    async def mock_cmd(cmd: str, arg: str) -> str:
        return f"OK {cmd} with {arg}"

    async def mock_approval(app_id: str, approved: bool) -> str:
        return f"Approbation {app_id}: {approved}"

    async def mock_alert(alert_id: str, noisy: bool) -> str:
        return f"Alerte {alert_id} noisy={noisy}"

    gw = TelegramGateway(
        bot_token="fake_tok",
        allowed_chat_id=123456,
        on_command=mock_cmd,
        on_approval=mock_approval,
        on_alert_ack=mock_alert,
    )

    # Commandes
    cmd_res = await gw.handle_text(123456, "/status details")
    assert cmd_res.authorized is True
    assert cmd_res.reply_text == "OK status with details"

    unknown_cmd = await gw.handle_text(123456, "/foobar")
    assert "Commandes disponibles" in unknown_cmd.reply_text

    # Callbacks inline
    cb_app = await gw.handle_callback(123456, "approve:req_123:yes")
    assert "Approbation req_123: True" in cb_app.reply_text

    cb_deny = await gw.handle_callback(123456, "approve:req_123:no")
    assert "Approbation req_123: False" in cb_deny.reply_text

    cb_ack = await gw.handle_callback(123456, "ack:alert_42")
    assert "Alerte alert_42 noisy=False" in cb_ack.reply_text

    cb_noisy = await gw.handle_callback(123456, "noisy:alert_42")
    assert "Alerte alert_42 noisy=True" in cb_noisy.reply_text


@pytest.mark.asyncio
async def test_telegram_gateway_voice_and_photo() -> None:
    async def mock_voice(audio_bytes: bytes) -> tuple[str, bytes | None]:
        assert len(audio_bytes) > 0
        return ("Réponse vocale", b"OGG_SIMULATED")

    async def mock_photo(img_bytes: bytes, caption: str) -> str:
        return f"Image analysée ({len(img_bytes)} bytes): {caption}"

    gw = TelegramGateway(
        bot_token="fake_tok",
        allowed_chat_id=123456,
        on_voice=mock_voice,
        on_photo=mock_photo,
    )

    v_res = await gw.handle_voice(123456, b"\x00\x01\x02\x03")
    assert v_res.authorized is True
    assert v_res.reply_text == "Réponse vocale"
    assert v_res.voice_ogg == b"OGG_SIMULATED"

    p_res = await gw.handle_photo(123456, b"\xff\xd8\xff\xe0", caption="Regarde ça")
    assert p_res.authorized is True
    assert "Regarde ça" in p_res.reply_text


def test_telegram_dispatcher_build() -> None:
    gw = TelegramGateway(
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", allowed_chat_id=123456
    )
    bot, dp = gw.build_aiogram_dispatcher()
    assert bot is not None
    assert dp is not None
    assert dp.message is not None


@pytest.mark.asyncio
async def test_telegram_gui_commands_whitelist_and_help() -> None:
    received: list[tuple[str, str]] = []

    async def mock_cmd(cmd: str, arg: str) -> str:
        received.append((cmd, arg))
        return f"OK {cmd}:{arg}"

    gw = TelegramGateway(bot_token="fake_tok", allowed_chat_id=123456, on_command=mock_cmd)
    gui_commands = [
        "/workspace",
        "/clear",
        "/new",
        "/usage",
        "/tokens",
        "/local",
        "/council",
        "/moa",
        "/auto",
        "/model",
        "/models",
        "/status",
        "/goal",
        "/goals",
        "/skills",
        "/skill",
        "/settings",
        "/config",
        "/tint",
        "/theme",
        "/help",
    ]
    for raw in gui_commands:
        res = await gw.handle_text(123456, f"{raw} test")
        assert res.authorized is True
        assert res.reply_text == f"OK {raw.lstrip('/')}:test"

    assert len(received) == len(gui_commands)

    # /help sans callback doit afficher toutes les commandes
    gw_default = TelegramGateway(bot_token="fake_tok", allowed_chat_id=123456)
    help_res = await gw_default.handle_text(123456, "/help")
    assert help_res.authorized is True
    for cmd in ("/workspace", "/clear", "/usage", "/local", "/council", "/auto", "/goal", "/skills", "/settings", "/tint", "/status", "/model", "/help"):
        assert cmd in help_res.reply_text
    assert "goal" in TELEGRAM_SUPPORTED_COMMANDS


@pytest.mark.asyncio
async def test_telegram_execute_gui_commands(tmp_path: Path, monkeypatch) -> None:
    state = JarvisCoreState(data_dir=tmp_path)
    state.ensure_runtime()

    captured_prompts: list[str] = []

    async def fake_run_chat_turn(text: str, **kwargs):
        captured_prompts.append(text)
        return type("Msg", (), {"content": f"ECHO: {text}"})()

    monkeypatch.setattr(state, "run_chat_turn", fake_run_chat_turn)

    # /help liste toutes les commandes
    help_out = await execute_telegram_command(state, "help", "")
    for expected in ("/workspace", "/clear", "/usage", "/local", "/council", "/auto", "/goal", "/skills", "/settings", "/tint", "/help"):
        assert expected in help_out

    # /workspace + message + reset (resolution vers ~/Documents/marges)
    fake_home = tmp_path / "home"
    marges_dir = fake_home / "Documents" / "marges"
    marges_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    ws_empty = await execute_telegram_command(state, "workspace", "")
    assert "Aucun workspace" in ws_empty
    ws_missing = await execute_telegram_command(state, "workspace", "dossier_inexistant")
    assert "Dossier introuvable" in ws_missing
    ws_set = await execute_telegram_command(state, "workspace", "marges")
    assert ws_set == "Workspace actif défini sur : ~/Documents/marges"
    resolved_p, display_p = resolve_workspace_directory("marges")
    assert resolved_p == marges_dir.resolve()
    assert display_p == "~/Documents/marges"
    text_reply = await execute_telegram_text(state, "analyse ce dossier")
    assert "[Workspace actif : ~/Documents/marges]" in text_reply
    assert captured_prompts[-1] == "[Workspace actif : ~/Documents/marges]\nanalyse ce dossier"
    ws_reset = await execute_telegram_command(state, "workspace", "reset")
    assert "réinitialisé" in ws_reset

    # /local, /council, /auto (sans arg)
    initial_model = state.get_agent_model().model
    local_out = await execute_telegram_command(state, "local", "")
    assert "qwen3.5:2b" in local_out
    assert state.get_agent_model().model == "qwen3.5:2b"

    council_out = await execute_telegram_command(state, "council", "question test")
    assert state.get_agent_model().model == "gemini-3.1-pro-preview"
    assert "ECHO: question test" == council_out

    auto_out = await execute_telegram_command(state, "auto", "")
    assert initial_model in auto_out
    assert state.get_agent_model().model == initial_model

    # /clear / /new
    state.db.add_message("user", "hello")
    clear_out = await execute_telegram_command(state, "clear", "")
    assert "1 message" in clear_out
    assert state.db.list_messages() == []

    # /usage, /status, /model, /skills, /settings, /tint
    assert "Usage IA du jour" in await execute_telegram_command(state, "usage", "")
    assert "Jarvis opérationnel" in await execute_telegram_command(state, "status", "")
    assert "Configuration des modèles" in await execute_telegram_command(state, "models", "")
    assert "Skills & Outils" in await execute_telegram_command(state, "skills", "")
    assert "Paramètres actuels" in await execute_telegram_command(state, "settings", "")
    tint_out = await execute_telegram_command(state, "tint", "")
    assert "Accent" in tint_out
    assert "Usage : /goal" in await execute_telegram_command(state, "goal", "")
