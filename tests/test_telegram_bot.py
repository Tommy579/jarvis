"""Tests pour TelegramGateway et les interactions bidirectionnelles Telegram."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from omi_core.channels.telegram import TelegramGateway, build_alert_keyboard, build_approval_keyboard


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
    gw = TelegramGateway(bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", allowed_chat_id=123456)
    bot, dp = gw.build_aiogram_dispatcher()
    assert bot is not None
    assert dp is not None
    assert dp.message is not None
