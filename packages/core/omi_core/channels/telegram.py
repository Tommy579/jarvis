"""Bot Telegram avec liste blanche stricte sur chat_id, commandes et clavier inline."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramMessageResult:
    authorized: bool
    reply_text: str
    voice_ogg: bytes | None = None
    inline_keyboard: list[list[dict[str, str]]] | None = None


def build_approval_keyboard(approval_id: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "Accepter", "callback_data": f"approve:{approval_id}:yes"},
            {"text": "Refuser", "callback_data": f"approve:{approval_id}:no"},
        ]
    ]


def build_alert_keyboard(alert_id: str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "Acquitter", "callback_data": f"ack:{alert_id}"},
            {"text": "Trop derangeant", "callback_data": f"noisy:{alert_id}"},
        ]
    ]


class TelegramGateway:
    """Gestionnaire Telegram avec liste blanche stricte et support aiogram."""

    def __init__(
        self,
        bot_token: str | None = None,
        allowed_chat_id: int | str | None = None,
        on_text: Callable[[str], Awaitable[str]] | None = None,
        on_voice: Callable[[bytes], Awaitable[tuple[str, bytes | None]]] | None = None,
        on_photo: Callable[[bytes, str], Awaitable[str]] | None = None,
        on_command: Callable[[str, str], Awaitable[str]] | None = None,
        on_approval: Callable[[str, bool], Awaitable[str]] | None = None,
        on_alert_ack: Callable[[str, bool], Awaitable[str]] | None = None,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        raw_chat = (
            allowed_chat_id
            if allowed_chat_id is not None
            else os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "")
        )
        self.allowed_chat_id = int(raw_chat) if str(raw_chat).strip() else None
        self.on_text = on_text
        self.on_voice = on_voice
        self.on_photo = on_photo
        self.on_command = on_command
        self.on_approval = on_approval
        self.on_alert_ack = on_alert_ack
        self.blocked_attempts: list[int] = []

    def is_authorized(self, chat_id: int) -> bool:
        if self.allowed_chat_id is None:
            return False
        allowed = int(chat_id) == self.allowed_chat_id
        if not allowed:
            self.blocked_attempts.append(int(chat_id))
            logger.warning("Tentative Telegram non autorisee depuis chat_id=%s", chat_id)
        return allowed

    async def handle_command(
        self, chat_id: int, command: str, arg: str = ""
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id):
            return TelegramMessageResult(authorized=False, reply_text="")

        cmd = command.lstrip("/").split("@", 1)[0].lower()
        supported = {"start", "help", "model", "status", "pause", "brief", "remember", "forget", "quiet"}
        if cmd not in supported:
            return TelegramMessageResult(
                authorized=True,
                reply_text="Commande inconnue. Commandes disponibles : /start, /status, /brief, /model, /remember, /forget, /pause, /quiet.",
            )
        if self.on_command:
            reply = await self.on_command(cmd, arg.strip())
            return TelegramMessageResult(authorized=True, reply_text=reply)
        if cmd in ("start", "help"):
            return TelegramMessageResult(
                authorized=True,
                reply_text="Bonjour ! Je suis Jarvis. Écrivez-moi, envoyez-moi un vocal ou tapez /status.",
            )
        return TelegramMessageResult(authorized=True, reply_text=f"Commande /{cmd} recue.")

    async def handle_text(self, chat_id: int, text: str) -> TelegramMessageResult:
        if not self.is_authorized(chat_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        stripped = text.strip()
        if stripped.startswith("/"):
            parts = stripped.split(maxsplit=1)
            return await self.handle_command(chat_id, parts[0], parts[1] if len(parts) > 1 else "")
        if self.on_text:
            reply = await self.on_text(stripped)
            return TelegramMessageResult(authorized=True, reply_text=reply)
        return TelegramMessageResult(authorized=True, reply_text=f"Recu : {stripped}")

    async def handle_voice(self, chat_id: int, audio_bytes: bytes) -> TelegramMessageResult:
        if not self.is_authorized(chat_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        if self.on_voice:
            reply_text, ogg_bytes = await self.on_voice(audio_bytes)
            return TelegramMessageResult(
                authorized=True, reply_text=reply_text, voice_ogg=ogg_bytes
            )
        return TelegramMessageResult(authorized=True, reply_text="Message vocal recu.")

    async def handle_photo(
        self, chat_id: int, image_bytes: bytes, caption: str = ""
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        if self.on_photo:
            reply = await self.on_photo(image_bytes, caption)
            return TelegramMessageResult(authorized=True, reply_text=reply)
        return TelegramMessageResult(
            authorized=True, reply_text="Photo transmise au module vision."
        )

    async def handle_callback(self, chat_id: int, callback_data: str) -> TelegramMessageResult:
        if not self.is_authorized(chat_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        parts = callback_data.split(":")
        if len(parts) == 3 and parts[0] == "approve":
            approved = parts[2] == "yes"
            if self.on_approval:
                reply = await self.on_approval(parts[1], approved)
                return TelegramMessageResult(authorized=True, reply_text=reply)
            return TelegramMessageResult(
                authorized=True,
                reply_text=f"Action {parts[1]} {'approuvee' if approved else 'refusee'}.",
            )
        if len(parts) == 2 and parts[0] in ("ack", "noisy"):
            too_noisy = parts[0] == "noisy"
            if self.on_alert_ack:
                reply = await self.on_alert_ack(parts[1], too_noisy)
                return TelegramMessageResult(authorized=True, reply_text=reply)
            return TelegramMessageResult(
                authorized=True, reply_text=f"Alerte {parts[1]} acquittee."
            )
        return TelegramMessageResult(authorized=True, reply_text="Action inline non reconnue.")

    @staticmethod
    def synthesize_elevenlabs(text: str) -> bytes | None:
        """Synthétise une réponse vocale avec la voix ElevenLabs configurée."""
        el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not el_key:
            return None
        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or "pNInz6obpgDQGcFmaJgB"
        try:
            import httpx

            resp = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
                headers={"xi-api-key": el_key, "Content-Type": "application/json"},
                json={
                    "text": text[:600],
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                },
                timeout=12.0,
            )
            if resp.status_code == 200 and resp.content:
                return resp.content
        except Exception:
            pass
        return None

    def build_aiogram_dispatcher(self) -> Any:
        """Construit le Dispatcher aiogram pret pour le long polling."""
        import asyncio
        import io
        from aiogram import Bot, Dispatcher, F
        from aiogram.types import (
            BufferedInputFile,
            CallbackQuery,
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            Message,
        )

        if not self.bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN manquant.")

        dp = Dispatcher()
        bot = Bot(token=self.bot_token)

        def _to_markup(rows: list[list[dict[str, str]]] | None) -> InlineKeyboardMarkup | None:
            if not rows:
                return None
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text=b["text"], callback_data=b["callback_data"])
                        for b in row
                    ]
                    for row in rows
                ]
            )

        @dp.message(F.text)
        async def _on_text(message: Message) -> None:
            text = message.text or ""
            wants_voice = text.lower().startswith("/vocal") or any(
                w in text.lower() for w in ("vocal", "en audio", "à l'oral", "parle", "dis-le")
            )
            clean_text = text[6:].strip() if text.lower().startswith("/vocal") else text
            res = await self.handle_text(message.chat.id, clean_text)
            if res.authorized and res.reply_text:
                if wants_voice or res.voice_ogg:
                    voice_bytes = res.voice_ogg or await asyncio.to_thread(
                        self.synthesize_elevenlabs, res.reply_text
                    )
                    if voice_bytes:
                        await message.answer_voice(
                            BufferedInputFile(voice_bytes, filename="jarvis.mp3"),
                            caption=res.reply_text[:1024],
                            reply_markup=_to_markup(res.inline_keyboard),
                        )
                        return
                await message.answer(res.reply_text, reply_markup=_to_markup(res.inline_keyboard))

        @dp.message(F.voice | F.audio)
        async def _on_voice(message: Message) -> None:
            voice_item = message.voice or message.audio
            if not voice_item:
                return
            try:
                dest = io.BytesIO()
                await bot.download(voice_item, destination=dest)
                audio_bytes = dest.getvalue()
                res = await self.handle_voice(message.chat.id, audio_bytes)
                if res.authorized and res.reply_text:
                    voice_bytes = res.voice_ogg or await asyncio.to_thread(
                        self.synthesize_elevenlabs, res.reply_text
                    )
                    if voice_bytes:
                        await message.answer_voice(
                            BufferedInputFile(voice_bytes, filename="jarvis.mp3"),
                            caption=res.reply_text[:1024],
                            reply_markup=_to_markup(res.inline_keyboard),
                        )
                        return
                    await message.answer(res.reply_text, reply_markup=_to_markup(res.inline_keyboard))
            except Exception as exc:
                logger.exception("Erreur traitement audio Telegram: %s", exc)
                await message.answer(f"Erreur audio: {exc}")

        @dp.message(F.photo)
        async def _on_photo(message: Message) -> None:
            if not message.photo:
                return
            try:
                dest = io.BytesIO()
                await bot.download(message.photo[-1], destination=dest)
                img_bytes = dest.getvalue()
                res = await self.handle_photo(message.chat.id, img_bytes, message.caption or "")
                if res.authorized and res.reply_text:
                    await message.answer(res.reply_text)
            except Exception as exc:
                logger.exception("Erreur photo Telegram: %s", exc)
                await message.answer(f"Erreur photo: {exc}")

        @dp.callback_query()
        async def _on_cb(query: CallbackQuery) -> None:
            if not query.message:
                return
            res = await self.handle_callback(query.message.chat.id, query.data or "")
            if res.authorized:
                await query.answer(res.reply_text[:180])
                try:
                    await query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

        return bot, dp
