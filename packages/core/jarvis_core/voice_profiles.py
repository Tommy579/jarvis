"""Verification du locuteur (empreintes vocales) basee sur WeSpeaker ONNX + Silero VAD."""

from __future__ import annotations

import io
import json
import logging
import threading
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import numpy as np
from jarvis_shared.rest_models import VoiceProfileRecord, VoiceProfilesConfig, VoiceVerifyResult

from .memory.schema import MemoryDatabase

logger = logging.getLogger(__name__)

VOICE_PROFILES_SETTING_KEY = "voice_profiles_v1"
SAMPLE_RATE = 16000
MAX_SAMPLES_PER_PROFILE = 8
DEFAULT_THRESHOLD = 0.48
MIN_ENROLL_SPEECH_SEC = 1.0
WESPEAKER_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
WESPEAKER_FILENAME = "voxceleb_resnet34_LM.onnx"
# Revision epinglee (commit du depot au 2024-05-06) : empeche un modele substitue cote amont.
WESPEAKER_REVISION = "f0c48c298fd835726c27956a5d617bad7115627e"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    if src_rate <= 0 or src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32, copy=False)
    out_len = max(1, int(round(len(samples) * dst_rate / src_rate)))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def decode_audio_to_16k(audio_bytes: bytes, mime_type: str = "audio/wav") -> np.ndarray:
    """Decode un flux audio (WAV ou format conteneur via PyAV) en mono float32 16 kHz."""
    if not audio_bytes:
        raise ValueError("Audio vide.")

    is_wav = mime_type == "audio/wav" or audio_bytes.startswith(b"RIFF")
    if is_wav:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                rate = wf.getframerate() or SAMPLE_RATE
                channels = wf.getnchannels() or 1
                sampwidth = wf.getsampwidth()
                n_frames = wf.getnframes()
                raw_pcm = wf.readframes(n_frames)
            if sampwidth == 2:
                samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 4:
                samples = np.frombuffer(raw_pcm, dtype=np.int32).astype(np.float32) / 2147483648.0
            elif sampwidth == 1:
                samples = (
                    np.frombuffer(raw_pcm, dtype=np.uint8).astype(np.float32) - 128.0
                ) / 128.0
            else:
                raise ValueError(f"Largeur PCM WAV non supportee : {sampwidth} octets.")
            if channels > 1:
                usable = (len(samples) // channels) * channels
                samples = samples[:usable].reshape(-1, channels).mean(axis=1)
            return _resample_linear(samples, rate, SAMPLE_RATE)
        except (wave.Error, EOFError) as exc:
            if mime_type == "audio/wav":
                raise ValueError("Fichier WAV invalide ou corrompu.") from exc

    try:
        import av

        container = av.open(io.BytesIO(audio_bytes))
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        for frame in container.decode(audio=0):
            for out_frame in resampler.resample(frame):
                arr = out_frame.to_ndarray()
                chunks.append(arr.reshape(-1).astype(np.float32))
        for out_frame in resampler.resample(None):
            arr = out_frame.to_ndarray()
            chunks.append(arr.reshape(-1).astype(np.float32))
        if not chunks:
            raise ValueError("Aucune piste audio exploitable.")
        return np.concatenate(chunks).astype(np.float32, copy=False)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Format audio illisible ({exc}).") from exc


@lru_cache(maxsize=4)
def _kaldi_mel_filterbank(
    sample_rate: int = SAMPLE_RATE, n_fft: int = 512, num_mel: int = 80
) -> np.ndarray:
    low_mel = 1127.0 * np.log(1.0 + 20.0 / 700.0)
    high_mel = 1127.0 * np.log(1.0 + (sample_rate / 2.0) / 700.0)
    mels = np.linspace(low_mel, high_mel, num_mel + 2)
    hz = 700.0 * (np.exp(mels / 1127.0) - 1.0)
    fft_freqs = np.linspace(0.0, sample_rate / 2.0, n_fft // 2 + 1)
    fb = np.zeros((num_mel, n_fft // 2 + 1), dtype=np.float32)
    for m in range(1, num_mel + 1):
        f_l, f_c, f_r = hz[m - 1], hz[m], hz[m + 1]
        up = (fft_freqs - f_l) / max(1e-12, f_c - f_l)
        down = (f_r - fft_freqs) / max(1e-12, f_r - f_c)
        fb[m - 1] = np.maximum(0.0, np.minimum(up, down))
    return fb


@lru_cache(maxsize=4)
def _hamming_window(frame_len: int) -> np.ndarray:
    return np.hamming(frame_len).astype(np.float32)


def compute_kaldi_fbank(
    samples_16k: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    num_mel: int = 80,
) -> np.ndarray:
    """Extrait des bancs de filtres Mel 80 canaux compatibles Kaldi/WeSpeaker avec CMN."""
    samples = np.asarray(samples_16k, dtype=np.float32) * 32768.0
    frame_len = int(0.025 * sample_rate)  # 400
    frame_shift = int(0.010 * sample_rate)  # 160
    n_fft = 512

    min_len = frame_len + 8 * frame_shift  # Garantit >= 9 trames pour ResNet34
    if len(samples) < min_len:
        samples = np.pad(samples, (0, min_len - len(samples)))

    n_frames = 1 + (len(samples) - frame_len) // frame_shift
    idx = np.arange(frame_len)[None, :] + np.arange(n_frames)[:, None] * frame_shift
    frames = samples[idx].copy()
    frames -= frames.mean(axis=1, keepdims=True)

    first = frames[:, :1] * (1.0 - 0.97)
    rest = frames[:, 1:] - 0.97 * frames[:, :-1]
    frames = np.concatenate([first, rest], axis=1)
    frames *= _hamming_window(frame_len)[None, :]

    spec = np.fft.rfft(frames, n=n_fft, axis=1)
    power = (spec.real**2 + spec.imag**2).astype(np.float32)
    fb = _kaldi_mel_filterbank(sample_rate, n_fft, num_mel)
    mel_e = np.maximum(power @ fb.T, 1e-10)
    feats = np.log(mel_e).astype(np.float32)
    feats -= feats.mean(axis=0, keepdims=True)
    return feats


def extract_speech_samples(
    samples_16k: np.ndarray,
    *,
    vad_threshold: float = 0.45,
    min_speech_duration_ms: int = 150,
) -> tuple[np.ndarray, float]:
    """Isole les segments de vraie parole humaine (Silero VAD ou repli energie RMS)."""
    arr = np.asarray(samples_16k, dtype=np.float32).reshape(-1)
    if len(arr) == 0:
        return arr, 0.0

    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        opts = VadOptions(
            threshold=vad_threshold,
            min_speech_duration_ms=min_speech_duration_ms,
        )
        ts_list = get_speech_timestamps(arr, opts)
        if not ts_list:
            return np.zeros(0, dtype=np.float32), 0.0
        parts = [arr[int(seg["start"]) : int(seg["end"])] for seg in ts_list]
        speech = np.concatenate(parts).astype(np.float32, copy=False)
        return speech, len(speech) / float(SAMPLE_RATE)
    except Exception as exc:
        logger.debug("Silero VAD indisponible pour tri locuteur (%s), repli RMS.", exc)

    # Repli energie par fenetres de 30 ms si Silero VAD indisponible
    win = int(0.03 * SAMPLE_RATE)
    n_wins = len(arr) // win
    if n_wins == 0:
        rms = float(np.sqrt(np.mean(arr * arr))) if len(arr) > 0 else 0.0
        return (
            (arr, len(arr) / float(SAMPLE_RATE))
            if rms >= 0.008
            else (np.zeros(0, dtype=np.float32), 0.0)
        )
    reshaped = arr[: n_wins * win].reshape(n_wins, win)
    rms_per_win = np.sqrt(np.mean(reshaped * reshaped, axis=1))
    voiced = reshaped[rms_per_win >= 0.008].reshape(-1)
    return voiced.astype(np.float32, copy=False), len(voiced) / float(SAMPLE_RATE)


class SpeakerEmbedder:
    """Extracteur d'empreintes vocales 256-D via WeSpeaker ResNet34-LM ONNX."""

    def __init__(
        self,
        embed_fn: Callable[[np.ndarray], np.ndarray] | None = None,
        repo_id: str = WESPEAKER_REPO,
        filename: str = WESPEAKER_FILENAME,
        revision: str = WESPEAKER_REVISION,
    ) -> None:
        self._embed_fn = embed_fn
        self._repo_id = repo_id
        self._filename = filename
        self._revision = revision
        self._session: Any = None
        self._lock = threading.Lock()

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        with self._lock:
            if self._session is not None:
                return self._session
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            model_path = hf_hub_download(self._repo_id, self._filename, revision=self._revision)
            self._session = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"],
            )
            return self._session

    @staticmethod
    def l2_normalize(vec: np.ndarray) -> np.ndarray:
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(arr))
        if norm <= 1e-12:
            return arr
        return (arr / norm).astype(np.float32)

    def embed(self, samples_16k: np.ndarray) -> np.ndarray:
        if self._embed_fn is not None:
            return self.l2_normalize(self._embed_fn(samples_16k))
        feats = compute_kaldi_fbank(samples_16k)
        session = self._ensure_session()
        input_name = session.get_inputs()[0].name
        emb = session.run(None, {input_name: feats[None, :, :]})[0][0]
        return self.l2_normalize(emb)


@dataclass
class _StoredVoiceProfile:
    id: str
    name: str
    role: str = "ami"
    embeddings: list[list[float]] = field(default_factory=list)
    duration_sec: float = 0.0
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_record(self) -> VoiceProfileRecord:
        return VoiceProfileRecord(
            id=self.id,
            name=self.name,
            role=self.role,
            sample_count=len(self.embeddings),
            duration_sec=round(float(self.duration_sec), 1),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "embeddings": self.embeddings,
            "duration_sec": self.duration_sec,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _StoredVoiceProfile:
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            role=str(raw.get("role") or "ami"),
            embeddings=[[float(x) for x in emb] for emb in raw.get("embeddings", []) if emb],
            duration_sec=float(raw.get("duration_sec") or 0.0),
            created_at=str(raw.get("created_at") or _utc_now_iso()),
            updated_at=str(raw.get("updated_at") or _utc_now_iso()),
        )


class VoiceProfileStore:
    """Gestionnaire persistant des voix reconnues par Jarvis et du filtre locuteur."""

    def __init__(
        self,
        db: MemoryDatabase,
        embedder: SpeakerEmbedder | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder or SpeakerEmbedder()
        self._lock = threading.Lock()
        self._enabled: bool = False
        self._threshold: float = DEFAULT_THRESHOLD
        self._profiles: list[_StoredVoiceProfile] = []
        self._load()

    def _load(self) -> None:
        raw = self.db.get_setting(VOICE_PROFILES_SETTING_KEY)
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return
        self._enabled = bool(data.get("enabled", False))
        self._threshold = self._clamp_threshold(float(data.get("threshold", DEFAULT_THRESHOLD)))
        loaded: list[_StoredVoiceProfile] = []
        for item in data.get("profiles", []):
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                loaded.append(_StoredVoiceProfile.from_dict(item))
        self._profiles = loaded

    def _save(self) -> None:
        payload = {
            "enabled": self._enabled,
            "threshold": self._threshold,
            "profiles": [p.to_dict() for p in self._profiles],
        }
        self.db.set_setting(VOICE_PROFILES_SETTING_KEY, json.dumps(payload))

    @staticmethod
    def _clamp_threshold(value: float) -> float:
        return round(min(0.85, max(0.25, float(value))), 3)

    def is_active(self) -> bool:
        with self._lock:
            return self._enabled and len(self._profiles) > 0

    def get_config(self) -> VoiceProfilesConfig:
        with self._lock:
            return VoiceProfilesConfig(
                enabled=self._enabled,
                threshold=self._threshold,
                profiles=[p.to_record() for p in self._profiles],
            )

    def update_settings(
        self,
        *,
        enabled: bool | None = None,
        threshold: float | None = None,
    ) -> VoiceProfilesConfig:
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if threshold is not None:
                self._threshold = self._clamp_threshold(threshold)
            self._save()
            return VoiceProfilesConfig(
                enabled=self._enabled,
                threshold=self._threshold,
                profiles=[p.to_record() for p in self._profiles],
            )

    def _extract_enrollment_embedding(
        self, audio_bytes: bytes, mime_type: str
    ) -> tuple[list[float], float]:
        samples_16k = decode_audio_to_16k(audio_bytes, mime_type=mime_type)
        speech, speech_sec = extract_speech_samples(samples_16k)
        if speech_sec < MIN_ENROLL_SPEECH_SEC or len(speech) < int(
            MIN_ENROLL_SPEECH_SEC * SAMPLE_RATE
        ):
            raise ValueError(
                f"Parole trop courte ({speech_sec:.1f} s detectee). "
                "Parlez distinctement pendant au moins 2 secondes."
            )
        emb = self.embedder.embed(speech)
        return [float(x) for x in emb], speech_sec

    def enroll_profile(
        self,
        *,
        name: str,
        role: str,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
    ) -> VoiceProfilesConfig:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Le nom de la voix est requis.")
        cleaned_role = (role or "ami").strip() or "ami"

        emb_list, speech_sec = self._extract_enrollment_embedding(audio_bytes, mime_type)
        now = _utc_now_iso()

        with self._lock:
            was_empty = len(self._profiles) == 0
            profile = _StoredVoiceProfile(
                id=uuid.uuid4().hex[:12],
                name=cleaned_name,
                role=cleaned_role,
                embeddings=[emb_list],
                duration_sec=speech_sec,
                created_at=now,
                updated_at=now,
            )
            self._profiles.append(profile)
            # Active automatiquement le filtrage lors de l'ajout du tout premier profil.
            if was_empty:
                self._enabled = True
            self._save()
            return VoiceProfilesConfig(
                enabled=self._enabled,
                threshold=self._threshold,
                profiles=[p.to_record() for p in self._profiles],
            )

    def add_sample(
        self,
        profile_id: str,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
    ) -> VoiceProfilesConfig:
        emb_list, speech_sec = self._extract_enrollment_embedding(audio_bytes, mime_type)
        now = _utc_now_iso()

        with self._lock:
            target = next((p for p in self._profiles if p.id == profile_id), None)
            if target is None:
                raise KeyError(f"Profil vocal introuvable : {profile_id}")
            target.embeddings.append(emb_list)
            if len(target.embeddings) > MAX_SAMPLES_PER_PROFILE:
                target.embeddings = target.embeddings[-MAX_SAMPLES_PER_PROFILE:]
            target.duration_sec += speech_sec
            target.updated_at = now
            self._save()
            return VoiceProfilesConfig(
                enabled=self._enabled,
                threshold=self._threshold,
                profiles=[p.to_record() for p in self._profiles],
            )

    def delete_profile(self, profile_id: str) -> VoiceProfilesConfig:
        with self._lock:
            before = len(self._profiles)
            self._profiles = [p for p in self._profiles if p.id != profile_id]
            if len(self._profiles) == before:
                raise KeyError(f"Profil vocal introuvable : {profile_id}")
            if not self._profiles:
                self._enabled = False
            self._save()
            return VoiceProfilesConfig(
                enabled=self._enabled,
                threshold=self._threshold,
                profiles=[p.to_record() for p in self._profiles],
            )

    @staticmethod
    def _score_profile(candidate: np.ndarray, profile: _StoredVoiceProfile) -> float:
        if not profile.embeddings:
            return -1.0
        mats = np.asarray(profile.embeddings, dtype=np.float32)
        sims = mats @ candidate
        best_sample = float(np.max(sims))
        if len(profile.embeddings) > 1:
            centroid = SpeakerEmbedder.l2_normalize(np.mean(mats, axis=0))
            centroid_sim = float(np.dot(centroid, candidate))
            return max(best_sample, centroid_sim)
        return best_sample

    def verify_samples(
        self,
        samples_16k: np.ndarray | None,
        *,
        is_wake_check: bool = False,
        force: bool = False,
    ) -> VoiceVerifyResult:
        with self._lock:
            enabled = self._enabled
            threshold = self._threshold
            profiles = list(self._profiles)

        if not force and (not enabled or not profiles):
            return VoiceVerifyResult(
                accepted=True,
                threshold=threshold,
                reason="filter_disabled" if not enabled else "no_profiles",
            )

        if not profiles:
            return VoiceVerifyResult(
                accepted=False,
                threshold=threshold,
                reason="no_profiles",
            )

        if samples_16k is None or len(samples_16k) < int(0.12 * SAMPLE_RATE):
            return VoiceVerifyResult(
                accepted=False,
                threshold=threshold,
                reason="audio_too_short",
            )

        speech, speech_sec = extract_speech_samples(samples_16k)
        target_samples = speech if speech_sec >= 0.2 else np.asarray(samples_16k, dtype=np.float32)
        effective_threshold = (
            max(0.32, round(threshold - 0.05, 3))
            if is_wake_check and len(target_samples) < int(1.2 * SAMPLE_RATE)
            else threshold
        )

        candidate = self.embedder.embed(target_samples)

        best_score = -1.0
        best_profile: _StoredVoiceProfile | None = None
        for profile in profiles:
            score = self._score_profile(candidate, profile)
            if score > best_score:
                best_score = score
                best_profile = profile

        accepted = best_profile is not None and best_score >= effective_threshold
        return VoiceVerifyResult(
            accepted=accepted,
            matched_name=best_profile.name if accepted and best_profile else None,
            matched_id=best_profile.id if accepted and best_profile else None,
            score=round(max(0.0, best_score), 3),
            threshold=effective_threshold,
            reason="matched" if accepted else "below_threshold",
        )

    def verify_audio(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
        *,
        force: bool = True,
    ) -> VoiceVerifyResult:
        samples_16k = decode_audio_to_16k(audio_bytes, mime_type=mime_type)
        return self.verify_samples(samples_16k, is_wake_check=False, force=force)
