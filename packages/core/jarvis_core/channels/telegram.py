"""Bot Telegram avec liste blanche stricte sur chat_id, commandes et clavier inline."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 4000
MAX_CAPTION_CHARS = 1024
MAX_MEDIA_BYTES = 15 * 1024 * 1024
MAX_CALLBACK_DATA_CHARS = 128
MAX_TELEGRAM_REPLY_CHARS = 4096
_MAX_BLOCKED_ATTEMPTS = 500
_TG_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{16,}")


def redact_telegram_secrets(text: str, bot_token: str = "") -> str:
    """Masque tout jeton Telegram (bot<id>:<secret>) avant journalisation."""
    cleaned = _TG_BOT_TOKEN_RE.sub("bot***", str(text or ""))
    tok = (bot_token or "").strip()
    if tok:
        cleaned = cleaned.replace(tok, "***")
    return cleaned


def _cap_telegram_text(text: str, limit: int = MAX_TELEGRAM_REPLY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


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


TELEGRAM_SUPPORTED_COMMANDS: frozenset[str] = frozenset(
    {
        "start",
        "help",
        "model",
        "models",
        "status",
        "pause",
        "brief",
        "remember",
        "forget",
        "quiet",
        "auto",
        "local",
        "council",
        "moa",
        "tasks",
        "stop",
        "goal",
        "goals",
        "workspace",
        "clear",
        "new",
        "usage",
        "tokens",
        "skills",
        "skill",
        "settings",
        "config",
        "tint",
        "theme",
        "ask",
    }
)

TELEGRAM_MENU_COMMANDS: tuple[tuple[str, str], ...] = (
    ("help", "Afficher toutes les commandes"),
    ("status", "État des services et modèles"),
    ("workspace", "Définir ou afficher le workspace actif"),
    ("clear", "Nouvelle conversation (effacer l'historique)"),
    ("usage", "Consommation de jetons et coût du jour"),
    ("model", "Afficher ou changer le modèle actif"),
    ("local", "Activer le modèle local (qwen3.5:2b)"),
    ("council", "Activer le mode conseil avancé"),
    ("auto", "Routage auto ou tâche autonome"),
    ("ask", "Demander approbation terminal niveau 3+"),
    ("goal", "Lancer un objectif autonome supervisé"),
    ("goals", "Lister les objectifs récents"),
    ("tasks", "Lister les tâches autonomes"),
    ("stop", "Arrêt d'urgence des tâches et objectifs"),
    ("brief", "Résumé des notifications et événements"),
    ("remember", "Mémoriser un fait"),
    ("forget", "Oublier un fait"),
    ("skills", "Serveurs MCP, outils et intégrations"),
    ("settings", "Résumé de la configuration actuelle"),
    ("tint", "Basculer l'accent visuel"),
    ("quiet", "Mode silencieux"),
    ("pause", "Suspendre les notifications"),
)

TELEGRAM_HELP_TEXT = (
    "👋 Bonjour ! Je suis Jarvis, votre assistant personnel.\n\n"
    "Vous pouvez m'écrire directement, m'envoyer des notes vocales ou des photos, "
    "ou utiliser les commandes suivantes :\n\n"
    "💬 Conversation & Mémoire\n"
    "- /clear (ou /new) : démarrer une nouvelle conversation\n"
    "- /brief : résumé des notifications et événements récents\n"
    "- /remember <fait> : mémoriser un fait\n"
    "- /forget <fait> : oublier un fait\n"
    "- /vocal <message> : demander une réponse vocale\n\n"
    "🧠 Modèles & Workspace\n"
    "- /model (ou /models) [modèle] : afficher les modèles ou changer le modèle actif\n"
    "- /local [message] : activer le modèle local (qwen3.5:2b)\n"
    "- /council (ou /moa) [message] : activer le mode conseil avancé\n"
    "- /auto [objectif] : rétablir le routage auto (sans argument) ou lancer une tâche autonome\n"
    "- /ask <demande> : notifier et demander approbation pour les commandes terminal niveau 3+\n"
    "- /workspace [dossier | reset] : définir ou afficher le dossier de travail actif\n\n"
    "🎯 Objectifs & Tâches\n"
    "- /goal <objectif> : lancer un objectif autonome supervisé\n"
    "- /goals : lister les objectifs récents\n"
    "- /tasks : lister les tâches autonomes récentes\n"
    "- /stop : arrêt d'urgence des tâches et objectifs\n\n"
    "⚙️ Système & Suivi\n"
    "- /status : état des services, modèle, capteurs, workspace et MCP\n"
    "- /usage (ou /tokens) : jetons consommés et coût IA du jour\n"
    "- /skills (ou /skill) : serveurs MCP, outils et intégrations actifs\n"
    "- /settings (ou /config) : résumé de la configuration actuelle\n"
    "- /tint (ou /theme) : basculer l'accent visuel (laiton / bleu Jarvis)\n"
    "- /quiet : mode silencieux\n"
    "- /pause : suspendre temporairement les notifications\n"
    "- /help : afficher cette aide complète"
)


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

    def is_authorized(self, chat_id: int, user_id: int | None = None) -> bool:
        if self.allowed_chat_id is None:
            return False
        allowed = int(chat_id) == self.allowed_chat_id
        # Pour un chat prive (allowed_chat_id > 0), l'expediteur doit aussi correspondre
        if allowed and user_id is not None and self.allowed_chat_id > 0:
            allowed = int(user_id) == self.allowed_chat_id
        if not allowed:
            self.blocked_attempts.append(int(chat_id))
            if len(self.blocked_attempts) > _MAX_BLOCKED_ATTEMPTS:
                del self.blocked_attempts[:-_MAX_BLOCKED_ATTEMPTS]
            logger.warning("Tentative Telegram non autorisee depuis chat_id=%s", chat_id)
        return allowed

    async def handle_command(
        self, chat_id: int, command: str, arg: str = "", *, user_id: int | None = None
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id, user_id=user_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        if len(command) + len(arg) > MAX_TEXT_CHARS:
            return TelegramMessageResult(
                authorized=True,
                reply_text=f"Commande trop longue (max {MAX_TEXT_CHARS} caracteres).",
            )

        cmd = command.lstrip("/").split("@", 1)[0].lower()
        if cmd not in TELEGRAM_SUPPORTED_COMMANDS:
            return TelegramMessageResult(
                authorized=True,
                reply_text=(
                    "Commande inconnue. Tapez /help pour la liste complète.\n"
                    "Commandes disponibles : /start, /help, /status, /brief, /model, /local, "
                    "/council, /auto, /ask, /workspace, /usage, /clear, /goal, /goals, /tasks, "
                    "/stop, /skills, /settings, /tint, /remember, /forget, /pause, /quiet."
                ),
            )
        if self.on_command:
            reply = await self.on_command(cmd, arg.strip())
            return TelegramMessageResult(authorized=True, reply_text=reply)
        if cmd in ("start", "help"):
            return TelegramMessageResult(
                authorized=True,
                reply_text=TELEGRAM_HELP_TEXT,
            )
        return TelegramMessageResult(authorized=True, reply_text=f"Commande /{cmd} recue.")

    async def handle_text(
        self, chat_id: int, text: str, *, user_id: int | None = None
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id, user_id=user_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        stripped = (text or "").strip()
        if len(stripped) > MAX_TEXT_CHARS:
            return TelegramMessageResult(
                authorized=True,
                reply_text=f"Message trop long (max {MAX_TEXT_CHARS} caracteres).",
            )
        if stripped.startswith("/"):
            parts = stripped.split(maxsplit=1)
            return await self.handle_command(
                chat_id,
                parts[0],
                parts[1] if len(parts) > 1 else "",
                user_id=user_id,
            )
        if self.on_text:
            reply = await self.on_text(stripped)
            return TelegramMessageResult(authorized=True, reply_text=reply)
        return TelegramMessageResult(authorized=True, reply_text=f"Recu : {stripped}")

    async def handle_voice(
        self, chat_id: int, audio_bytes: bytes, *, user_id: int | None = None
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id, user_id=user_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        if len(audio_bytes) > MAX_MEDIA_BYTES:
            return TelegramMessageResult(
                authorized=True, reply_text="Fichier audio trop volumineux (max 15 Mo)."
            )
        if self.on_voice:
            reply_text, ogg_bytes = await self.on_voice(audio_bytes)
            return TelegramMessageResult(
                authorized=True, reply_text=reply_text, voice_ogg=ogg_bytes
            )
        return TelegramMessageResult(authorized=True, reply_text="Message vocal recu.")

    async def handle_photo(
        self, chat_id: int, image_bytes: bytes, caption: str = "", *, user_id: int | None = None
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id, user_id=user_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        if len(image_bytes) > MAX_MEDIA_BYTES:
            return TelegramMessageResult(
                authorized=True, reply_text="Image trop volumineuse (max 15 Mo)."
            )
        safe_caption = (caption or "").strip()[:MAX_CAPTION_CHARS]
        if self.on_photo:
            reply = await self.on_photo(image_bytes, safe_caption)
            return TelegramMessageResult(authorized=True, reply_text=reply)
        return TelegramMessageResult(
            authorized=True, reply_text="Photo transmise au module vision."
        )

    async def handle_callback(
        self, chat_id: int, callback_data: str, *, user_id: int | None = None
    ) -> TelegramMessageResult:
        if not self.is_authorized(chat_id, user_id=user_id):
            return TelegramMessageResult(authorized=False, reply_text="")
        if len(callback_data or "") > MAX_CALLBACK_DATA_CHARS:
            return TelegramMessageResult(authorized=True, reply_text="Action inline invalide.")
        parts = callback_data.split(":")
        if len(parts) == 3 and parts[0] == "approve" and parts[2] in ("yes", "no"):
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
    def synthesize_voice(text: str) -> bytes | None:
        """Synthétise une réponse vocale via Google AI Studio (Gemini TTS)."""
        from jarvis_core.tts import synthesize_gemini_tts_wav, wav_to_mp3

        wav_bytes = synthesize_gemini_tts_wav(text[:600])
        if not wav_bytes:
            return None
        return wav_to_mp3(wav_bytes)

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
            sender_id = getattr(message.from_user, "id", None)
            if not self.is_authorized(message.chat.id, user_id=sender_id):
                return
            text = message.text or ""
            wants_voice = text.lower().startswith("/vocal") or any(
                w in text.lower() for w in ("vocal", "en audio", "à l'oral", "parle", "dis-le")
            )
            clean_text = text[6:].strip() if text.lower().startswith("/vocal") else text
            res = await self.handle_text(message.chat.id, clean_text, user_id=sender_id)
            if res.authorized and res.reply_text:
                if wants_voice or res.voice_ogg:
                    voice_bytes = res.voice_ogg or await asyncio.to_thread(
                        self.synthesize_voice, res.reply_text
                    )
                    if voice_bytes:
                        await message.answer_voice(
                            BufferedInputFile(voice_bytes, filename="jarvis.mp3"),
                            caption=_cap_telegram_text(res.reply_text, 1024),
                            parse_mode=None,
                            reply_markup=_to_markup(res.inline_keyboard),
                        )
                        return
                await message.answer(
                    _cap_telegram_text(res.reply_text),
                    parse_mode=None,
                    reply_markup=_to_markup(res.inline_keyboard),
                )

        @dp.message(F.voice | F.audio)
        async def _on_voice(message: Message) -> None:
            sender_id = getattr(message.from_user, "id", None)
            if not self.is_authorized(message.chat.id, user_id=sender_id):
                return
            voice_item = message.voice or message.audio
            if not voice_item:
                return
            file_size = int(getattr(voice_item, "file_size", 0) or 0)
            if file_size > MAX_MEDIA_BYTES:
                await message.answer("Fichier audio trop volumineux (max 15 Mo).", parse_mode=None)
                return
            try:
                dest = io.BytesIO()
                await bot.download(voice_item, destination=dest)
                audio_bytes = dest.getvalue()
                if len(audio_bytes) > MAX_MEDIA_BYTES:
                    await message.answer(
                        "Fichier audio trop volumineux (max 15 Mo).", parse_mode=None
                    )
                    return
                res = await self.handle_voice(message.chat.id, audio_bytes, user_id=sender_id)
                if res.authorized and res.reply_text:
                    voice_bytes = res.voice_ogg or await asyncio.to_thread(
                        self.synthesize_voice, res.reply_text
                    )
                    if voice_bytes:
                        await message.answer_voice(
                            BufferedInputFile(voice_bytes, filename="jarvis.mp3"),
                            caption=_cap_telegram_text(res.reply_text, 1024),
                            parse_mode=None,
                            reply_markup=_to_markup(res.inline_keyboard),
                        )
                        return
                    await message.answer(
                        _cap_telegram_text(res.reply_text),
                        parse_mode=None,
                        reply_markup=_to_markup(res.inline_keyboard),
                    )
            except Exception as exc:
                logger.error(
                    "Erreur traitement audio Telegram: %s",
                    redact_telegram_secrets(str(exc), self.bot_token),
                )
                await message.answer(
                    "Erreur interne lors du traitement du message audio.", parse_mode=None
                )

        @dp.message(F.photo)
        async def _on_photo(message: Message) -> None:
            sender_id = getattr(message.from_user, "id", None)
            if not self.is_authorized(message.chat.id, user_id=sender_id):
                return
            if not message.photo:
                return
            photo_item = message.photo[-1]
            file_size = int(getattr(photo_item, "file_size", 0) or 0)
            if file_size > MAX_MEDIA_BYTES:
                await message.answer("Image trop volumineuse (max 15 Mo).", parse_mode=None)
                return
            try:
                dest = io.BytesIO()
                await bot.download(photo_item, destination=dest)
                img_bytes = dest.getvalue()
                if len(img_bytes) > MAX_MEDIA_BYTES:
                    await message.answer("Image trop volumineuse (max 15 Mo).", parse_mode=None)
                    return
                res = await self.handle_photo(
                    message.chat.id, img_bytes, message.caption or "", user_id=sender_id
                )
                if res.authorized and res.reply_text:
                    await message.answer(_cap_telegram_text(res.reply_text), parse_mode=None)
            except Exception as exc:
                logger.error(
                    "Erreur photo Telegram: %s",
                    redact_telegram_secrets(str(exc), self.bot_token),
                )
                await message.answer(
                    "Erreur interne lors du traitement de l'image.", parse_mode=None
                )

        @dp.callback_query()
        async def _on_cb(query: CallbackQuery) -> None:
            if not query.message:
                return
            sender_id = getattr(query.from_user, "id", None)
            res = await self.handle_callback(
                query.message.chat.id, query.data or "", user_id=sender_id
            )
            if res.authorized:
                await query.answer(res.reply_text[:180])
                try:
                    await query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass

        return bot, dp
