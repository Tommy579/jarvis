"""Synthese vocale via Google AI Studio (Gemini TTS) et conversions audio."""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import wave

import httpx

logger = logging.getLogger("jarvis.tts")

_URL_SECRET_RE = re.compile(r"([?&](?:key|api_key|token|access_token)=)[^&\s]+", re.IGNORECASE)


def _sanitize_tts_log(text: str, secret: str = "") -> str:
    cleaned = _URL_SECRET_RE.sub(r"\1***", str(text or ""))
    if secret:
        cleaned = cleaned.replace(secret, "***")
    return cleaned


DEFAULT_GEMINI_TTS_MODEL = "gemini-3.8-flash-lite-tts"
DEFAULT_GEMINI_TTS_VOICE = "Charon"


def _wrap_pcm16_as_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _transcode_audio(
    audio_bytes: bytes, output_args: list[str], timeout: float = 8.0
) -> bytes | None:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not audio_bytes:
        return None
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            *output_args,
            "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout:
        return proc.stdout
    return None


def synthesize_gemini_tts_wav(
    text: str,
    *,
    api_key: str | None = None,
    voice_name: str | None = None,
    model: str | None = None,
    timeout: float = 12.0,
) -> bytes | None:
    """Genere un flux audio WAV via l'API Google AI Studio (Gemini TTS)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    key = (api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key:
        return None
    tts_model = (
        model if model is not None else os.environ.get("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
    ).strip() or DEFAULT_GEMINI_TTS_MODEL
    tts_voice = (
        voice_name
        if voice_name is not None
        else os.environ.get("GEMINI_TTS_VOICE", DEFAULT_GEMINI_TTS_VOICE)
    ).strip() or DEFAULT_GEMINI_TTS_VOICE

    try:
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{tts_model}:generateContent",
            headers={"x-goog-api-key": key},
            json={
                "contents": [{"parts": [{"text": cleaned}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": tts_voice}},
                        "speakingRate": 1.5,
                    },
                },
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.debug(
                "Gemini TTS HTTP %s: %s",
                resp.status_code,
                _sanitize_tts_log(resp.text[:200], key),
            )
            return None
        payload = resp.json()
        part = payload["candidates"][0]["content"]["parts"][0]["inlineData"]
        raw_bytes = base64.b64decode(part["data"])
        mime_type = str(part.get("mimeType") or "").lower()
        if raw_bytes.startswith(b"RIFF") or "wav" in mime_type:
            return raw_bytes
        return _wrap_pcm16_as_wav(raw_bytes, sample_rate=24000)
    except Exception as exc:
        logger.debug(
            "Erreur Gemini TTS (%s): %s", type(exc).__name__, _sanitize_tts_log(str(exc), key)
        )
        return None


def wav_to_pcm16_16khz(wav_bytes: bytes) -> bytes | None:
    """Convertit un WAV en PCM16 mono 16 kHz brut (s16le)"""
    return _transcode_audio(
        wav_bytes,
        ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000"],
    )


def wav_to_mp3(wav_bytes: bytes) -> bytes | None:
    """Convertit un WAV en MP3 pour les messages vocaux Telegram"""
    return _transcode_audio(
        wav_bytes,
        ["-f", "mp3", "-acodec", "libmp3lame", "-b:a", "96k"],
    )
