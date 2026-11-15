"""Pipeline vocal temps reel basse latence (< 1,5s) : openWakeWord, Silero VAD, faster-whisper, Piper TTS et barge-in."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class VoiceLatencyMetrics:
    vad_end_ms: float = 0.0
    stt_final_ms: float = 0.0
    llm_first_token_ms: float = 0.0
    tts_first_chunk_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return (
            self.vad_end_ms + self.stt_final_ms + self.llm_first_token_ms + self.tts_first_chunk_ms
        )

    @property
    def within_budget(self) -> bool:
        return self.total_ms <= 1500.0


class RealtimeVoicePipeline:
    """Pipeline vocal local avec mot d'activation 'Jarvis', VAD, coupure TTS (barge-in) et niveau micro 20 Hz."""

    WAKE_WORD_REGEX = re.compile(r"\b(?:hey\s+jarvis|jarvis|omi)\b[,:\s]*(.*)", re.IGNORECASE)

    def __init__(
        self,
        wake_word_model: str = "hey_jarvis",
        stt_model_size: str = "small",
        continuous_transcription: bool = False,
        on_mic_level: Callable[[float], Any] | None = None,
        on_barge_in: Callable[[], Any] | None = None,
    ):
        self.wake_word_model = wake_word_model
        self.stt_model_size = stt_model_size
        # Desactive par defaut pour la vie privee et la batterie
        self.continuous_transcription = continuous_transcription
        self.on_mic_level = on_mic_level
        self.on_barge_in = on_barge_in

        self.is_tts_playing: bool = False
        self.tts_interrupted: bool = False
        self._tts_queue: list[str] = []
        self.spoken_text: str = ""

    @staticmethod
    def compute_rms_amplitude(pcm_samples: list[float] | Any) -> float:
        """Calcule l'amplitude normalisee (0.0 a 1.0) pour animer la sphere a 20 Hz."""
        try:
            n = len(pcm_samples)
            if n == 0:
                return 0.0
            mean_sq = sum(float(s) * float(s) for s in pcm_samples) / n
            rms = math.sqrt(mean_sq)
            return min(1.0, max(0.0, rms * 4.0))
        except Exception:
            return 0.0

    @classmethod
    def extract_wake_word_query(cls, transcript: str) -> str | None:
        """Extrait la requete suivant le mot d'activation 'Jarvis' ou 'OMI' (mot entier uniquement)."""
        m = cls.WAKE_WORD_REGEX.search(transcript or "")
        if not m:
            return None
        query = m.group(1).strip()
        return query if query else ""

    def start_tts_playback(self, sentence: str) -> None:
        self.tts_interrupted = False
        self.is_tts_playing = True
        self._tts_queue.append(sentence)
        self.spoken_text = " ".join(self._tts_queue)

    @staticmethod
    def normalize_speech_tokens(text: str) -> list[str]:
        """Tokens comparables : minuscules, sans accents ni ponctuation."""
        import unicodedata

        normalized = unicodedata.normalize("NFD", text or "")
        normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
        cleaned = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
        tokens = [t for t in cleaned.split(" ") if len(t) > 1]
        return [t[:-1] if len(t) > 3 and t.endswith(("s", "x")) else t for t in tokens]

    @classmethod
    def is_self_echo(cls, spoken_text: str, transcript: str) -> bool:
        """Indique si la transcription n'est que l'echo de la voix de Jarvis (haut-parleur)."""
        spoken = set(cls.normalize_speech_tokens(spoken_text))
        heard = cls.normalize_speech_tokens(transcript)
        if not spoken or not heard:
            return False
        hits = sum(1 for token in heard if token in spoken)
        return hits / len(heard) >= 0.7

    def on_vad_speech_detected(
        self,
        speech_probability: float,
        threshold: float = 0.6,
        transcript: str | None = None,
    ) -> bool:
        """Silero VAD : coupe le TTS si l'utilisateur parle vraiment (barge-in).

        Si une transcription est fournie, elle est comparee au texte en cours de lecture :
        sur haut-parleur, la voix de Jarvis revient dans le micro et ne doit pas le couper.
        """
        if speech_probability >= threshold:
            if self.is_tts_playing:
                if transcript is not None and self.is_self_echo(self.spoken_text, transcript):
                    return False
                self.interrupt_tts()
            return True
        return False

    def interrupt_tts(self) -> None:
        """Coupe instantanement la synthese vocale en cours (barge-in)."""
        self.is_tts_playing = False
        self.tts_interrupted = True
        self._tts_queue.clear()
        self.spoken_text = ""
        if self.on_barge_in:
            self.on_barge_in()


class GeminiLiveBridge:
    """Pont optionnel vers l'API Gemini Live (voix temps reel plein-duplex avec injection memoire initiale)."""

    def __init__(self, api_key: str, model: str = "gemini-live"):
        self.api_key = api_key
        self.model = model
        self.session_active = False
        self.injected_context = ""

    def build_session_config(self, memory_engine: Any, query_hint: str = "") -> dict[str, Any]:
        msgs = memory_engine.assemble_context(
            query=query_hint,
            history=[],
            live_state={"mode": "gemini_live_voice", "register": "oral"},
            provider_trusted=True,
        )
        system_text = "\n\n".join(m.content for m in msgs if m.content)
        self.injected_context = system_text
        return {
            "model": self.model,
            "system_instruction": system_text,
            "response_modalities": ["AUDIO"],
        }
