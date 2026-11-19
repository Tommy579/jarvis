"""Application FastAPI + WebSocket pour jarvis-core (section 1.1 et 1.2)."""

from __future__ import annotations

import asyncio
import atexit
import base64
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
import yaml
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jarvis_shared.models import ImageAttachment, SystemEvent, ToolCall, ToolResult, UrgencyLevel
from jarvis_shared.notifications import NotificationOrder
from jarvis_shared.rest_models import (
    AlertRecord,
    ApprovalDecisionRequest,
    ApprovalItem,
    AttentionRules,
    AutonomousTaskCreate,
    AutonomousTaskRecord,
    AutonomyAuditEntry,
    AutonomyPolicyConfig,
    AutonomyPolicyUpdate,
    BlacklistConfig,
    ChatMessage,
    DailyUsageItem,
    EvalScenarioResult,
    EvalSuiteResult,
    FactCreate,
    FactRecord,
    FactUpdate,
    GoalCreate,
    GoalRecord,
    GoalSteerRequest,
    IntegrationInfo,
    MCPServerStatus,
    MemoryExport,
    OnboardingAnswerRequest,
    OnboardingStatus,
    PersonRecord,
    RoleModelConfig,
    SensitivityLevel,
    SensorState,
    SystemAutostartConfig,
    SystemAutostartUpdate,
    ToolCallRecord,
    UpdateRoleModelRequest,
    VipContact,
    VoiceProfilesConfig,
    VoiceVerifyResult,
    WikiPage,
    WikiUpdate,
)
from jarvis_shared.ws_protocol import ClientWsEvent, SensorName
from pydantic import BaseModel, Field, TypeAdapter

from ..audio_ducking import SystemAudioDucker
from ..autostart import get_autostart_status, set_autostart_enabled
from ..integrations import (
    AppleCalendarIntegration,
    FinanceIntegration,
    GitHubProbesIntegration,
    GmailIntegration,
    GoogleCalendarIntegration,
    GoogleContactsIntegration,
    HealthIntegration,
    HomeAssistantIntegration,
    IntegrationRegistry,
    NotionIntegration,
    OutlookCalendarIntegration,
    OutlookMailIntegration,
    SpotifyIntegration,
    WeatherTransportIntegration,
)
from ..integrations.base import (
    GoogleOAuthIntegration,
    MicrosoftOAuthIntegration,
    build_google_oauth_authorize_url,
    build_microsoft_oauth_authorize_url,
    build_spotify_oauth_authorize_url,
)
from ..integrations.trading_bot import format_trading_webhook_event
from ..llm.gemini import GeminiProvider
from ..llm.router import normalize_thinking_mode
from ..memory.import_legacy import import_legacy_profile
from ..memory.schema import MemoryDatabase, utc_now_iso
from ..memory.storage import MemoryStorage
from ..notifications.policy import PolicyStore
from ..onboarding.service import OnboardingService
from ..runtime import JarvisRuntime, build_runtime
from ..scheduler.briefings import agenda_from_events, build_evening_review, build_morning_briefing
from ..scheduler.routines import RoutineScheduler
from ..security import SecretStore, verify_action_token
from ..voice_profiles import VoiceProfileStore, decode_audio_to_16k

logger = logging.getLogger(__name__)
SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
CLIENT_WS_ADAPTER = TypeAdapter(ClientWsEvent)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class SensorToggleRequest(BaseModel):
    paused: bool


class IntegrationConfigureRequest(BaseModel):
    token: str = ""
    url: str = ""
    csv_text: str = ""
    client_id: str = ""
    client_secret: str = ""


class PersonCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    relation: str = "contact"
    birthday: str | None = None
    contact_ref: str | None = None
    notes: str = ""


class VoiceTranscribeRequest(BaseModel):
    audio_base64: str = Field(min_length=1)
    mime_type: str = "audio/wav"
    wake_threshold: float | None = Field(default=None, ge=0.15, le=0.75)


class VoiceSpeakRequest(BaseModel):
    text: str = Field(min_length=1)


class VoiceProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: str = Field(default="ami", max_length=32)
    audio_base64: str = Field(min_length=1)
    mime_type: str = "audio/wav"


class VoiceProfileSampleRequest(BaseModel):
    audio_base64: str = Field(min_length=1)
    mime_type: str = "audio/wav"


class VoiceProfileSettingsRequest(BaseModel):
    enabled: bool | None = None
    threshold: float | None = Field(default=None, ge=0.25, le=0.85)


class VoiceDuckingRequest(BaseModel):
    active: bool
    ratio: float = Field(default=0.22, ge=0.05, le=0.8)


class CameraController:
    """Controleur materiel de la webcam (/dev/video0) via OpenCV."""

    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        self._lock = threading.Lock()
        self._cap: Any = None
        self._running = False
        self._thread: threading.Thread | None = None
        self.latest_jpeg: bytes | None = None
        self.last_error: str | None = None

    @property
    def is_active(self) -> bool:
        return self._running and self._cap is not None

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._running and self._cap is not None:
                return True, "Camera active (/dev/video0)"
            try:
                import cv2

                cap = cv2.VideoCapture(self.device_index)
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    self.last_error = "Camera introuvable ou occupee (/dev/video0)."
                    return False, self.last_error
                ret, frame = cap.read()
                if ret and frame is not None:
                    ok_enc, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    if ok_enc:
                        self.latest_jpeg = buf.tobytes()
                self._cap = cap
                self._running = True
                self.last_error = None
                self._thread = threading.Thread(target=self._capture_loop, daemon=True)
                self._thread.start()
                return True, "Camera active (/dev/video0)"
            except Exception as exc:
                self.last_error = f"Erreur camera : {exc}"
                return False, self.last_error

    def _capture_loop(self) -> None:
        import cv2

        while self._running:
            cap = self._cap
            if cap is None:
                break
            try:
                ret, frame = cap.read()
                if ret and frame is not None:
                    ok_enc, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    if ok_enc:
                        self.latest_jpeg = buf.tobytes()
            except Exception as exc:
                self.last_error = str(exc)
            time.sleep(0.04)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            thread = self._thread
            self._thread = None
            cap = self._cap
            self._cap = None
            self.latest_jpeg = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def get_jpeg(self) -> bytes | None:
        return self.latest_jpeg

    def get_base64(self) -> str | None:
        raw = self.latest_jpeg
        if not raw:
            return None
        return base64.b64encode(raw).decode("ascii")


def _resolve_base_root() -> Path:
    """Racine de l'application : dossier de l'exe (binaire gele) ou depot (source).

    Permet a l'empaquetage PyInstaller de conserver `.env`, `config/` et `jarvis-data/`
    a cote du binaire au lieu du dossier temporaire du bundle.
    """
    override = os.environ.get("JARVIS_APP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


class JarvisCoreState:
    def __init__(
        self,
        data_dir: Path | None = None,
        config_dir: Path | None = None,
        legacy_profile_path: Path | None = None,
    ) -> None:
        base_root = _resolve_base_root()
        self.base_root = base_root
        self.env_file = base_root / ".env"
        self._is_isolated_test = data_dir is not None
        self._custom_data_dir = data_dir is not None
        if not self._is_isolated_test and self.env_file.exists():
            load_dotenv(self.env_file, override=False)

        self.config_dir = config_dir or Path(
            os.environ.get("JARVIS_CONFIG_DIR", "").strip() or (base_root / "config")
        )
        self.data_dir = (
            data_dir
            or Path(os.environ.get("JARVIS_DATA_DIR", str(base_root / "jarvis-data"))).expanduser()
        )
        self.wiki_dir = self.data_dir / "wiki"
        self.secrets = SecretStore()
        self.db = MemoryDatabase(self.data_dir / "jarvis.db")
        self.runtime: JarvisRuntime | None = None
        self.scheduler: RoutineScheduler | None = None
        self.oauth_states: dict[str, dict[str, Any]] = {}
        self._wake_whisper_model: Any = None
        self._wake_whisper_failed: bool = False
        self.voice_profiles = VoiceProfileStore(self.db)
        self.audio_ducker = SystemAudioDucker(enabled=not self._is_isolated_test)
        if not self._is_isolated_test:
            atexit.register(self.audio_ducker.restore)

        legacy_path = legacy_profile_path or (base_root / "jarvis_profile.json")
        if legacy_path.exists():
            import_legacy_profile(legacy_path, self.db, self.wiki_dir)
        else:
            self.wiki_dir.mkdir(parents=True, exist_ok=True)

        self.storage = MemoryStorage(self.data_dir / "jarvis.db", database=self.db)
        self.onboarding = OnboardingService(self.db, self.wiki_dir)
        self.ws_broadcast_queues: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()
        self.integrations = IntegrationRegistry(
            [
                GoogleCalendarIntegration(),
                GmailIntegration(),
                GoogleContactsIntegration(),
                OutlookCalendarIntegration(),
                OutlookMailIntegration(),
                AppleCalendarIntegration(),
                HomeAssistantIntegration(),
                GitHubProbesIntegration(),
                NotionIntegration(),
                WeatherTransportIntegration(enabled=True),
                SpotifyIntegration(),
                HealthIntegration(),
                FinanceIntegration(),
            ]
        )
        self._load_integrations_state()

        self.models: list[RoleModelConfig] = self._load_models_config()
        raw_roles = self.db.get_setting("role_models")
        if raw_roles:
            try:
                saved_roles = [
                    RoleModelConfig.model_validate(item) for item in json.loads(raw_roles)
                ]
                for item in saved_roles:
                    if (
                        "/" in item.model
                        and not item.model.startswith("models/")
                        and item.provider != "openrouter"
                    ):
                        item.provider = "openrouter"
                by_role = {m.role: m for m in saved_roles}
                merged = [by_role.pop(m.role, m) for m in self.models]
                merged.extend(by_role.values())
                self.models = merged
            except (ValueError, TypeError):
                pass
        elif (
            not self._is_isolated_test
            and not os.environ.get("OPENROUTER_API_KEY", "").strip()
            and (
                os.environ.get("GEMINI_API_KEY", "").strip()
                or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower()
                in ("1", "true", "yes")
            )
        ):
            for item in self.models:
                if item.role == "deep":
                    item.model = "gemini-4-hydrogen"
                    item.provider = "gemini"
                    item.supports_vision = True
                elif item.role in ("agent", "memory", "triage", "vision"):
                    item.model = "gemini-4-carbon"
                    item.provider = "gemini"
                    item.supports_vision = True

        self.camera = CameraController()

        raw_rules = self.db.get_setting("attention_rules")
        if raw_rules:
            try:
                self.rules = AttentionRules.model_validate_json(raw_rules)
            except ValueError:
                self.rules = self._load_attention_rules()
        else:
            self.rules = self._load_attention_rules()

        default_blacklist = BlacklistConfig(
            apps=["keepassxc", "bitwarden", "1password", "banking", "signal"],
            global_paused=False,
            sensors=[
                SensorState(
                    sensor="screen", active=True, paused=False, detail="Capture a la demande"
                ),
                SensorState(sensor="mic", active=False, paused=True, detail="Micro en pause"),
                SensorState(
                    sensor="camera", active=False, paused=True, detail="Presence locale desactivee"
                ),
            ],
        )
        raw_blacklist = self.db.get_setting("blacklist")
        if raw_blacklist:
            try:
                self.blacklist = BlacklistConfig.model_validate_json(raw_blacklist)
            except ValueError:
                self.blacklist = default_blacklist
        else:
            self.blacklist = default_blacklist

    def _broadcast_ws_event(self, event_type: str, payload: dict[str, Any]) -> None:
        for q in list(self.ws_broadcast_queues):
            try:
                q.put_nowait((event_type, payload))
            except asyncio.QueueFull:
                pass

    def ensure_runtime(self) -> JarvisRuntime:
        if self.runtime is None:
            self.runtime = build_runtime(
                data_dir=self.data_dir,
                config_dir=self.config_dir,
                database=self.db,
                allow_offline=True,
                on_ws_event=self._broadcast_ws_event,
            )
            for cfg in self.models:
                self.runtime.router.set_role_model(
                    cfg.role,
                    cfg.model,
                    cfg.provider,
                    thinking_mode=cfg.thinking_mode,
                )
        return self.runtime

    def set_sensor_paused(self, sensor: SensorName, paused: bool) -> BlacklistConfig:
        detail: str | None = None
        active = not paused

        if sensor == "camera" and not self._is_isolated_test:
            if not paused:
                ok, cam_detail = self.camera.start()
                if not ok:
                    for item in self.blacklist.sensors:
                        if item.sensor == "camera":
                            item.paused = True
                            item.active = False
                            item.detail = cam_detail
                    raise RuntimeError(cam_detail)
                active = True
                detail = cam_detail
            else:
                self.camera.stop()
                active = False
                detail = "Presence locale en pause"
        elif sensor == "mic" and not self._is_isolated_test:
            if not paused:
                try:
                    import sounddevice as sd

                    devices = sd.query_devices()
                    has_input = any(d.get("max_input_channels", 0) > 0 for d in devices)
                    active = has_input
                    detail = (
                        "Micro pret (Espace ou Maintenir micro)"
                        if has_input
                        else "Aucun micro detecte"
                    )
                except ImportError:
                    active = True
                    detail = "Micro navigateur pret (capture Web Audio)"
                except Exception as exc:
                    active = True
                    detail = f"Micro navigateur pret ({exc})"
            else:
                active = False
                detail = "Micro en pause"
        elif sensor == "screen":
            detail = "Capture ecran en pause" if paused else "Capture a la demande"

        for item in self.blacklist.sensors:
            if item.sensor == sensor:
                item.paused = paused
                item.active = active
                if detail is not None:
                    item.detail = detail
                break
        self.blacklist.global_paused = all(s.paused for s in self.blacklist.sensors)
        self.db.set_setting("blacklist", self.blacklist.model_dump_json())
        return self.blacklist

    def _build_usage_dict(self, usages: list[Any]) -> dict[str, Any]:
        default_model = self.models[0].model if self.models else "deepseek/deepseek-v4.1-flash-1017"
        if not usages:
            return {"model": default_model, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        last_u = usages[-1]
        return {
            "model": last_u.model or default_model,
            "tokens_in": sum(u.tokens_in for u in usages),
            "tokens_out": sum(u.tokens_out for u in usages),
            "cost": round(sum(u.cost_usd for u in usages), 6),
        }

    def _summarize_tool_call(
        self,
        tc: ToolCall,
        tr: ToolResult | None,
        risk_val: int,
    ) -> dict[str, Any]:
        ok = True
        summary = "Execute"
        if tr is not None:
            out_ok = tr.output.get("ok", True) if isinstance(tr.output, dict) else True
            ok = (not tr.is_error) and bool(out_ok)
            summary = (
                json.dumps(tr.output, ensure_ascii=False)[:240]
                if not isinstance(tr.output, str)
                else tr.output[:240]
            )
        return {
            "id": tc.id,
            "name": tc.name,
            "args": tc.arguments,
            "risk": risk_val,
            "ok": ok,
            "summary": summary,
            "ts": utc_now_iso(),
        }

    def _load_integrations_state(self) -> None:
        raw = self.db.get_setting("integrations_state")
        if not raw:
            return
        try:
            saved = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        for info in self.integrations.list_info():
            connector = self.integrations.get(info.id)
            if connector is None or info.id not in saved:
                continue
            entry = saved[info.id]
            if isinstance(entry, dict):
                want_enabled = bool(entry.get("enabled", connector.enabled))
                last_sync = entry.get("last_sync")
                if isinstance(last_sync, str) and last_sync:
                    connector.last_sync = last_sync
            else:
                want_enabled = bool(entry)
            if want_enabled and (self._is_isolated_test or connector.connected):
                connector.toggle(True)
            elif not want_enabled:
                connector.toggle(False)

    def save_integrations_state(self) -> None:
        payload: dict[str, dict[str, Any]] = {}
        for info in self.integrations.list_info():
            payload[info.id] = {
                "enabled": bool(info.enabled),
                "last_sync": info.last_sync,
            }
        self.db.set_setting("integrations_state", json.dumps(payload))

    async def run_chat_turn(
        self,
        text: str,
        *,
        on_delta: Any | None = None,
        on_tool_call: Any | None = None,
        on_tool_result: Any | None = None,
        on_done: Any | None = None,
    ) -> ChatMessage:
        runtime = self.ensure_runtime()
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        user_msg = self.db.add_message("user", text)

        images: list[ImageAttachment] = []
        cam_b64 = self.camera.get_base64() if self.camera.is_active else None
        if cam_b64:
            images.append(ImageAttachment(mime_type="image/jpeg", base64_data=cam_b64))

        calls_by_id: dict[str, tuple[ToolCall, int]] = {}
        tools_payload: list[dict[str, Any]] = []
        emitted_delta = False

        async def _handle_delta(chunk: str) -> None:
            nonlocal emitted_delta
            if not chunk:
                return
            emitted_delta = True
            if on_delta is not None:
                await on_delta(msg_id, chunk)

        async def _handle_tool_call(tc: ToolCall) -> None:
            spec = runtime.tool_registry.get(tc.name)
            risk_val = int(spec.risk) if spec else 0
            calls_by_id[tc.id] = (tc, risk_val)
            if on_tool_call is not None:
                await on_tool_call(tc, risk_val)

        async def _handle_tool_result(tr: ToolResult) -> None:
            tc, risk_val = calls_by_id.get(
                tr.tool_call_id,
                (ToolCall(id=tr.tool_call_id, name="tool", arguments={}), 0),
            )
            record = self._summarize_tool_call(tc, tr, risk_val)
            tools_payload.append(record)
            if on_tool_result is not None:
                await on_tool_result(record)

        usages: list[Any] = []
        try:
            turn_res = await runtime.agent.run_turn(
                user_input=text,
                channel="web",
                role_name="agent",
                images=images,
                on_delta=_handle_delta,
                on_tool_call=_handle_tool_call,
                on_tool_result=_handle_tool_result,
            )
            reply = turn_res.text.strip() or "Tache executee."
            usages = list(turn_res.usages)
        except asyncio.CancelledError:
            self.db.delete_message(user_msg.id)
            raise
        except Exception as exc:
            logger.exception("Erreur run_chat_turn")
            reply = f"Erreur lors de l'appel au modele : {exc}"

        if not emitted_delta and on_delta is not None:
            await on_delta(msg_id, reply)

        usage_dict = self._build_usage_dict(usages)
        saved_msg = self.db.add_message(
            "assistant",
            reply,
            usage=usage_dict,
            tools=tools_payload,
            message_id=msg_id,
        )
        if on_done is not None:
            await on_done(msg_id, usage_dict)
        return saved_msg

    @staticmethod
    def _decode_and_vad_wav(
        audio_bytes: bytes,
        mime_type: str,
        *,
        is_wake_check: bool = False,
        wake_threshold: float | None = None,
    ) -> tuple[bool, Any]:
        """Decode un WAV et verifie via Silero VAD qu'il contient de la vraie parole humaine."""
        if not (mime_type == "audio/wav" or audio_bytes.startswith(b"RIFF")):
            return True, None
        import io
        import wave

        try:
            import numpy as np

            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                rate = wf.getframerate() or 16000
                n_frames = wf.getnframes()
                min_dur = 0.12 if is_wake_check else 0.15
                if n_frames / rate < min_dur:
                    return False, None
                channels = wf.getnchannels() or 1
                raw_pcm = wf.readframes(n_frames)
            samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
            rms_level = float(np.sqrt(np.mean(samples * samples)))
            wake_vad = (
                min(0.75, max(0.15, float(wake_threshold))) if wake_threshold is not None else 0.38
            )
            if is_wake_check:
                # 0.15 -> 0.0020, 0.38 -> 0.0045, 0.75 -> 0.0100
                min_rms = (
                    0.0020 + ((wake_vad - 0.15) / 0.23) * 0.0025
                    if wake_vad <= 0.38
                    else 0.0045 + ((wake_vad - 0.38) / 0.37) * 0.0055
                )
            else:
                min_rms = 0.008
            if rms_level < min_rms:
                return False, None
            if rate != 16000 and len(samples) > 0:
                idx = np.linspace(0, len(samples) - 1, int(len(samples) * 16000 / rate)).astype(int)
                samples_16k = samples[idx]
            else:
                samples_16k = samples
        except (wave.Error, EOFError):
            return False, None
        except Exception:
            return True, None

        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps

            vad_opts = (
                VadOptions(threshold=wake_vad, min_speech_duration_ms=140)
                if is_wake_check
                else VadOptions(threshold=0.52, min_speech_duration_ms=220)
            )
            speech_ts = get_speech_timestamps(samples_16k, vad_opts)
            if not speech_ts:
                return False, None
        except Exception as exc:
            logger.debug("Silero VAD indisponible (%s), poursuite sans filtre neuronal.", exc)
        return True, samples_16k

    async def _verify_speaker_if_active(
        self,
        audio_bytes: bytes,
        mime_type: str,
        samples_16k: Any = None,
        *,
        is_wake_check: bool = False,
    ) -> bool:
        """Retourne False si le filtre locuteur est actif et rejette cette voix."""
        if not self.voice_profiles.is_active():
            return True
        if samples_16k is None:
            try:
                samples_16k = await asyncio.to_thread(decode_audio_to_16k, audio_bytes, mime_type)
            except ValueError:
                return False
        verification = await asyncio.to_thread(
            self.voice_profiles.verify_samples,
            samples_16k,
            is_wake_check=is_wake_check,
        )
        if not verification.accepted:
            logger.info(
                "Voix ignoree par filtre locuteur (score=%.3f < %.3f, wake=%s).",
                verification.score,
                verification.threshold,
                is_wake_check,
            )
            return False
        return True

    async def transcribe_audio(
        self, audio_base64: str, mime_type: str = "audio/wav", *, verify_speaker: bool = True
    ) -> str:
        if self._is_isolated_test and not (verify_speaker and self.voice_profiles.is_active()):
            return "Bonjour Jarvis"
        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Audio base64 invalide.") from exc
        if not audio_bytes:
            return ""
        has_speech, samples_16k = await asyncio.to_thread(
            self._decode_and_vad_wav, audio_bytes, mime_type
        )
        if not has_speech:
            return ""
        if verify_speaker and not await self._verify_speaker_if_active(
            audio_bytes, mime_type, samples_16k, is_wake_check=False
        ):
            return ""
        if self._is_isolated_test:
            return "Bonjour Jarvis"
        runtime = self.ensure_runtime()
        prov = runtime.router.providers.get("gemini")
        gem = (
            prov
            if isinstance(prov, GeminiProvider)
            else GeminiProvider(api_key=os.environ.get("GEMINI_API_KEY", "").strip())
        )
        if not gem.api_key and not gem.vertexai:
            raise HTTPException(
                status_code=400,
                detail="Cle GEMINI_API_KEY manquante pour la transcription vocale.",
            )
        return await gem.transcribe_audio(audio_bytes, mime_type=mime_type)

    async def wake_check_audio(
        self,
        audio_base64: str,
        mime_type: str = "audio/wav",
        *,
        wake_threshold: float | None = None,
    ) -> str:
        """Transcrit rapidement en local (Silero VAD + faster-whisper tiny) pour le reveil vocal."""
        if self._is_isolated_test:
            return ""
        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Audio base64 invalide.") from exc
        if not audio_bytes:
            return ""

        has_speech, samples_16k = await asyncio.to_thread(
            lambda: self._decode_and_vad_wav(
                audio_bytes,
                mime_type,
                is_wake_check=True,
                wake_threshold=wake_threshold,
            )
        )
        if not has_speech:
            return ""

        if not await self._verify_speaker_if_active(
            audio_bytes, mime_type, samples_16k, is_wake_check=True
        ):
            return ""

        if not self._wake_whisper_failed:

            def _run_local_whisper() -> str | None:
                import io

                audio_input: Any = (
                    samples_16k if samples_16k is not None else io.BytesIO(audio_bytes)
                )
                try:
                    if self._wake_whisper_model is None:
                        from faster_whisper import WhisperModel

                        self._wake_whisper_model = WhisperModel(
                            "tiny", device="cpu", compute_type="int8"
                        )
                    segments, _ = self._wake_whisper_model.transcribe(
                        audio_input,
                        language="fr",
                        beam_size=1,
                        vad_filter=False,
                        no_speech_threshold=0.72,
                        log_prob_threshold=-1.35,
                        initial_prompt="Jarvis, Hey Jarvis, coupe le record.",
                    )
                    valid = [
                        seg.text.strip()
                        for seg in segments
                        if seg.text
                        and getattr(seg, "no_speech_prob", 0.0) < 0.78
                        and getattr(seg, "avg_logprob", 0.0) > -1.35
                    ]
                    return " ".join(valid).strip()
                except Exception as exc:
                    logger.warning("Wake check faster-whisper indisponible (%s), repli STT.", exc)
                    self._wake_whisper_failed = True
                    return None

            local_text = await asyncio.to_thread(_run_local_whisper)
            if local_text is not None:
                return local_text

        return await self.transcribe_audio(audio_base64, mime_type=mime_type)

    @staticmethod
    def _clean_text_for_tts(raw_text: str) -> str:
        cleaned = re.sub(r"<untrusted[^>]*>.*?</untrusted>", "", raw_text, flags=re.DOTALL)
        cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"[*_#~>|]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:4000]

    @staticmethod
    def _split_tts_segments(text: str, max_len: int = 180) -> list[str]:
        parts = re.split(r"(?<=[.!?;:\n])\s+", text)
        segments: list[str] = []
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) > max_len:
                if current:
                    segments.append(current)
                    current = ""
                words = part.split(" ")
                chunk = ""
                for w in words:
                    if len(chunk) + len(w) + 1 <= max_len:
                        chunk = f"{chunk} {w}".strip()
                    else:
                        if chunk:
                            segments.append(chunk)
                        chunk = w[:max_len]
                if chunk:
                    segments.append(chunk)
            elif len(current) + len(part) + 1 <= max_len:
                current = f"{current} {part}".strip()
            else:
                if current:
                    segments.append(current)
                current = part
        if current:
            segments.append(current)
        return segments[:40]

    async def synthesize_speech(self, text: str) -> tuple[str, int]:
        """Synthetise un texte en PCM16 16 kHz mono base64 pour la lecture vocale par Jarvis."""
        if self._is_isolated_test:
            return "", 16000
        cleaned = self._clean_text_for_tts(text)
        if not cleaned:
            return "", 16000

        def _synth_sync() -> bytes:
            if not self._is_isolated_test and self.env_file.exists():
                load_dotenv(self.env_file, override=True)
            from jarvis_core.tts import synthesize_gemini_tts_wav, wav_to_pcm16_16khz

            wav_bytes = synthesize_gemini_tts_wav(cleaned)
            if wav_bytes:
                pcm_bytes = wav_to_pcm16_16khz(wav_bytes)
                if pcm_bytes:
                    return pcm_bytes

            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin:
                segments = self._split_tts_segments(cleaned)
                mp3_buffers: list[bytes] = []
                for seg in segments:
                    q = quote(seg, safe="")
                    url = (
                        "https://translate.googleapis.com/translate_tts"
                        f"?ie=UTF-8&client=tw-ob&tl=fr&q={q}"
                    )
                    try:
                        resp = httpx.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=4.0,
                        )
                        if resp.status_code == 200 and resp.content:
                            mp3_buffers.append(resp.content)
                    except Exception:
                        break
                if mp3_buffers:
                    proc = subprocess.run(
                        [
                            ffmpeg_bin,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            "pipe:0",
                            "-f",
                            "s16le",
                            "-acodec",
                            "pcm_s16le",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            "pipe:1",
                        ],
                        input=b"".join(mp3_buffers),
                        capture_output=True,
                        timeout=6.0,
                        check=False,
                    )
                    if proc.returncode == 0 and proc.stdout:
                        return proc.stdout

            for espeak_cmd in ("espeak-ng", "espeak"):
                espeak_bin = shutil.which(espeak_cmd)
                if espeak_bin and ffmpeg_bin:
                    wav_proc = subprocess.run(
                        [espeak_bin, "-v", "fr", "--stdout", cleaned],
                        capture_output=True,
                        timeout=5.0,
                        check=False,
                    )
                    if wav_proc.returncode == 0 and wav_proc.stdout:
                        pcm_proc = subprocess.run(
                            [
                                ffmpeg_bin,
                                "-hide_banner",
                                "-loglevel",
                                "error",
                                "-i",
                                "pipe:0",
                                "-f",
                                "s16le",
                                "-acodec",
                                "pcm_s16le",
                                "-ac",
                                "1",
                                "-ar",
                                "16000",
                                "pipe:1",
                            ],
                            input=wav_proc.stdout,
                            capture_output=True,
                            timeout=5.0,
                            check=False,
                        )
                        if pcm_proc.returncode == 0 and pcm_proc.stdout:
                            return pcm_proc.stdout
            return b""

        try:
            pcm_bytes = await asyncio.to_thread(_synth_sync)
            if not pcm_bytes:
                return "", 16000
            return base64.b64encode(pcm_bytes).decode("ascii"), 16000
        except Exception:
            return "", 16000

    def _load_models_config(self) -> list[RoleModelConfig]:
        models_file = self.config_dir / "models.yaml"
        if not models_file.exists():
            return [
                RoleModelConfig(
                    role="agent",
                    model="deepseek/deepseek-v4.1-flash-1017",
                    provider="openrouter",
                    fallback=["z-ai/glm-5.3", "minimax/minimax-m3", "gemini-4-carbon"],
                    supports_vision=False,
                    supports_tools=True,
                )
            ]
        raw = yaml.safe_load(models_file.read_text(encoding="utf-8")) or {}
        roles_raw = raw.get("roles", {})
        default_provider = str(raw.get("defaults", {}).get("provider", "openrouter"))
        items: list[RoleModelConfig] = []
        for role_name, cfg in roles_raw.items():
            if not isinstance(cfg, dict):
                continue
            model_id = str(cfg.get("model", ""))
            provider = str(cfg.get("provider", default_provider))
            fallback = [str(f) for f in cfg.get("fallback", [])]
            supports_vision = role_name == "vision" or "gemini" in model_id.lower()
            items.append(
                RoleModelConfig(
                    role=str(role_name),
                    model=model_id,
                    provider=provider,
                    fallback=fallback,
                    thinking_mode=normalize_thinking_mode(
                        cfg.get("thinking_mode") or cfg.get("reasoning")
                    ),  # type: ignore[arg-type]
                    supports_vision=supports_vision,
                    supports_tools=role_name != "voice_realtime",
                )
            )
        return items

    def _load_attention_rules(self) -> AttentionRules:
        att_file = self.config_dir / "attention.yaml"
        if not att_file.exists():
            return AttentionRules()
        raw = yaml.safe_load(att_file.read_text(encoding="utf-8")) or {}
        quiet = raw.get("quiet_hours", {})
        limits = raw.get("limits", {})
        vips = [
            VipContact(
                name=str(v.get("name", "VIP")),
                identifiers=[str(i) for i in v.get("identifiers", [])],
                default_urgency=int(v.get("default_urgency", 2)),  # type: ignore[arg-type]
            )
            for v in raw.get("vip_contacts", [])
            if isinstance(v, dict)
        ]
        return AttentionRules(
            quiet_start=str(quiet.get("start", "23:00")),
            quiet_end=str(quiet.get("end", "08:00")),
            timezone=str(quiet.get("timezone", "Europe/Paris")),
            max_calls_per_day=int(limits.get("max_calls_per_day", 3)),
            vip_contacts=vips,
            min_urgency_for_call=3,
        )


def _minutes_until(start: str, now: datetime | None = None) -> int | None:
    """Minutes avant un debut d'evenement ISO. Retourne None si la date n'est pas exploitable."""
    raw = (start or "").strip()
    if not raw or "T" not in raw:
        return None
    try:
        start_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return int((start_dt - ref).total_seconds() // 60)


CALENDAR_CONNECTORS = frozenset({"google_calendar", "outlook_calendar", "apple_calendar"})


async def _sync_and_collect_events(
    state: "JarvisCoreState", only: frozenset[str] | None = None
) -> list[dict[str, Any]]:
    """Synchronise les connecteurs actifs et retourne les evenements qu'ils ont produits.

    `only` restreint la synchronisation a un sous-ensemble : la verification pre-reunion
    tourne toutes les 5 minutes et ne doit pas interroger la meteo ou Gmail a ce rythme.
    """
    events: list[dict[str, Any]] = []
    for info in state.integrations.list_info():
        if only is not None and info.id not in only:
            continue
        connector = state.integrations.get(info.id)
        if connector is None or not connector.enabled:
            continue
        try:
            await connector.sync(state.db)
        except Exception as exc:
            logger.debug("Synchronisation de %s impossible : %s", info.id, exc)
            continue
        try:
            events.extend(await connector.events())
        except Exception as exc:
            logger.debug("Lecture des evenements %s impossible : %s", info.id, exc)
    return events


async def _ingest_calendar_events(
    state: "JarvisCoreState", events: list[dict[str, Any]], window_minutes: int = 60
) -> int:
    """Pousse vers l'attention les evenements d'agenda qui commencent dans la fenetre."""
    runtime = state.ensure_runtime()
    ingested = 0
    for ev in events:
        if ev.get("type") != "calendar.upcoming":
            continue
        payload = ev.get("payload") or {}
        minutes = _minutes_until(str(payload.get("start") or ""))
        if minutes is None or minutes < 0 or minutes > window_minutes:
            continue
        await runtime.attention.ingest_event(
            SystemEvent(
                source=str(ev.get("source") or "calendar"),
                event_type="calendar.upcoming",
                payload={**payload, "minutes_before": minutes},
                dedup_key=f"calendar:{payload.get('summary', '')}:{payload.get('start', '')}",
            ),
            current_hour=datetime.now().hour,
        )
        ingested += 1
    return ingested


def _build_routine_scheduler(state: "JarvisCoreState") -> "RoutineScheduler":
    """Construit les routines chronologiques et les branche sur le runtime.

    Sans ce cablage, `MemoryEngine.consolidate_nightly` (deduplication des faits,
    regeneration de moi.md, purge des episodes > 90 j) et les briefings ne tournent
    jamais : le planificateur existait mais n'etait instancie nulle part.
    """
    timezone_name = "Europe/Paris"
    runtime_for_policy = state.runtime
    if runtime_for_policy is not None and runtime_for_policy.policy_store is not None:
        timezone_name = runtime_for_policy.policy_store.get().timezone or timezone_name
    scheduler = RoutineScheduler(timezone=timezone_name)

    async def _morning_briefing() -> None:
        runtime = state.ensure_runtime()
        events = await _sync_and_collect_events(state)
        await _ingest_calendar_events(state, events)
        text = build_morning_briefing(state.db, agenda_from_events(events))
        today = datetime.now().strftime("%Y-%m-%d")
        await runtime.attention.ingest_event(
            SystemEvent(
                source="routine",
                event_type="routine.morning_briefing",
                payload={"message": text},
                dedup_key=f"routine:morning:{today}",
            ),
            current_hour=datetime.now().hour,
        )

    async def _pre_meeting_check() -> None:
        events = await _sync_and_collect_events(state, only=CALENDAR_CONNECTORS)
        await _ingest_calendar_events(state, events)

    async def _evening_review() -> None:
        runtime = state.ensure_runtime()
        text = build_evening_review(state.db)
        today = datetime.now().strftime("%Y-%m-%d")
        await runtime.attention.ingest_event(
            SystemEvent(
                source="routine",
                event_type="routine.evening_review",
                payload={"message": text},
                dedup_key=f"routine:evening:{today}",
            ),
            current_hour=datetime.now().hour,
        )

    async def _nightly_consolidation() -> None:
        runtime = state.ensure_runtime()
        try:
            summary = await asyncio.to_thread(runtime.memory_engine.consolidate_nightly)
            logger.info("Consolidation nocturne : %s", summary)
        except Exception:
            logger.exception("Echec de la consolidation nocturne")

    scheduler.configure_defaults(
        morning_briefing=_morning_briefing,
        pre_meeting_check=_pre_meeting_check,
        midday_check=None,
        evening_review=_evening_review,
        nightly_consolidation=_nightly_consolidation,
    )
    return scheduler


def _check_token(request_token: str | None) -> None:
    expected = os.environ.get("JARVIS_API_TOKEN", "").strip()
    if not expected:
        return
    if not request_token or not hmac.compare_digest(request_token.strip(), expected):
        raise HTTPException(status_code=401, detail="Jeton d'acces invalide ou manquant.")


class DailyBudgetUpdate(BaseModel):
    daily_budget_usd: float = Field(ge=0.0, le=1000.0)


def create_app(state: JarvisCoreState | None = None) -> FastAPI:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        load_dotenv(override=False)
    if not os.environ.get("JARVIS_API_TOKEN", "").strip():
        if os.environ.get("JARVIS_ALLOW_INSECURE") == "1":
            logger.warning("JARVIS_API_TOKEN absent : mode non authentifie (loopback uniquement).")
        else:
            raise RuntimeError(
                "JARVIS_API_TOKEN manquant. Definissez-le dans .env, "
                "ou JARVIS_ALLOW_INSECURE=1 pour un usage local sans jeton."
            )

    core_state = state or JarvisCoreState()
    if core_state.runtime is None:
        core_state.runtime = build_runtime(
            data_dir=core_state.data_dir,
            config_dir=core_state.config_dir,
            database=core_state.db,
            allow_offline=True,
        )
        for cfg in core_state.models:
            core_state.runtime.router.set_role_model(
                cfg.role,
                cfg.model,
                cfg.provider,
                thinking_mode=cfg.thinking_mode,
            )
    elif core_state.db.get_setting("role_models"):
        for cfg in core_state.models:
            core_state.runtime.router.set_role_model(
                cfg.role,
                cfg.model,
                cfg.provider,
                thinking_mode=cfg.thinking_mode,
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_event = asyncio.Event()

        async def _attention_tick_loop():
            while not stop_event.is_set():
                try:
                    await asyncio.sleep(30)
                    rt = core_state.runtime
                    if rt and getattr(rt, "attention", None):
                        now_ts = time.time()
                        for key, notif in list(rt.attention.active_alerts.items()):
                            if notif.status != "sent":
                                continue
                            try:
                                alert_dt = datetime.fromisoformat(notif.ts.replace("Z", "+00:00"))
                                elapsed = now_ts - alert_dt.timestamp()
                            except Exception:
                                elapsed = 30.0
                            if elapsed >= 600 and notif.urgency == UrgencyLevel.URGENT:
                                rt.attention.check_escalations(elapsed_seconds=elapsed)
                        if int(now_ts) % 300 < 30:
                            await rt.attention.flush_triage_batch(current_hour=datetime.now().hour)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("Attention tick: %s", exc)

        tick_task = asyncio.create_task(_attention_tick_loop())

        telegram_task: asyncio.Task | None = None
        telegram_bot: Any = None
        telegram_dp: Any = None

        if not core_state._is_isolated_test:
            tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            tg_chat = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
            if tg_token and tg_chat:
                try:
                    from jarvis_core.channels.telegram import TelegramGateway

                    async def _tg_text(text: str) -> str:
                        rt = core_state.runtime
                        if rt and getattr(rt, "attention", None):
                            rt.attention.presence.record_telegram_activity()
                        res = await core_state.run_chat_turn(text)
                        return res.content

                    async def _tg_voice(audio_bytes: bytes) -> tuple[str, bytes | None]:
                        rt = core_state.runtime
                        if rt and getattr(rt, "attention", None):
                            rt.attention.presence.record_telegram_activity()
                        b64 = base64.b64encode(audio_bytes).decode("ascii")
                        try:
                            transcript = await core_state.transcribe_audio(
                                b64, mime_type="audio/ogg", verify_speaker=False
                            )
                        except Exception as exc:
                            return (f"Impossible de transcrire le message vocal : {exc}", None)
                        if not transcript or transcript.strip().upper() == "SILENCE":
                            return ("Aucune parole distincte détectée dans ce message vocal.", None)
                        res = await core_state.run_chat_turn(
                            f"[Message vocal transcrit] : {transcript}"
                        )
                        return (res.content, None)

                    async def _tg_photo(image_bytes: bytes, caption: str) -> str:
                        rt = core_state.runtime
                        if rt and getattr(rt, "attention", None):
                            rt.attention.presence.record_telegram_activity()
                        b64 = base64.b64encode(image_bytes).decode("ascii")
                        prompt = caption.strip() or "Que vois-tu sur cette image ?"
                        try:
                            turn_res = await rt.agent.run_turn(
                                user_input=prompt,
                                channel="telegram",
                                role_name="vision" if "vision" in rt.router.roles else "agent",
                                images=[ImageAttachment(mime_type="image/jpeg", base64_data=b64)],
                            )
                            core_state.db.add_message("user", f"[Photo envoyée] {prompt}")
                            core_state.db.add_message("assistant", turn_res.text)
                            return turn_res.text
                        except Exception as exc:
                            return f"Erreur analyse photo : {exc}"

                    async def _tg_approval(approval_id: str, approved: bool) -> str:
                        core_state.db.decide_approval(approval_id, approved)
                        rt = core_state.runtime
                        if rt and getattr(rt, "approvals", None):
                            try:
                                rt.approvals.resolve(approval_id, approved, channel="telegram")
                            except KeyError:
                                pass
                            except Exception as exc:
                                return f"Erreur approbation: {exc}"
                        for q in list(core_state.ws_broadcast_queues):
                            q.put_nowait(
                                (
                                    "approval.resolved",
                                    {
                                        "id": approval_id,
                                        "approved": approved,
                                        "status": "approved" if approved else "denied",
                                        "channel": "telegram",
                                    },
                                )
                            )
                        status = "approuvée" if approved else "refusée"
                        return f"Action {approval_id} {status}."

                    async def _tg_alert_ack(alert_id: str, too_noisy: bool) -> str:
                        rt = core_state.runtime
                        if rt and getattr(rt, "attention", None):
                            notif = rt.attention.active_alerts.get(alert_id)
                            if notif:
                                notif.status = "dismissed" if too_noisy else "acknowledged"
                                return (
                                    "Alerte marquée comme trop bruyante."
                                    if too_noisy
                                    else "Alerte acquittée."
                                )
                        return "Alerte acquittée."

                    async def _tg_cmd(cmd: str, arg: str) -> str:
                        rt = core_state.runtime
                        if rt and getattr(rt, "attention", None):
                            rt.attention.presence.record_telegram_activity()
                        if cmd in ("start", "help"):
                            return (
                                "👋 Bonjour ! Je suis Jarvis, votre assistant personnel.\n\n"
                                "Vous pouvez :\n"
                                "- M'écrire directement vos messages ou questions\n"
                                "- M'envoyer des notes vocales (réponse vocale avec Gemini TTS)\n"
                                "- M'envoyer des photos à analyser\n"
                                "- Utiliser les commandes : /status, /brief, /remember, /forget, /quiet"
                            )
                        if cmd == "status":
                            pol = rt.policy_store.get() if rt else None
                            return (
                                f"🟢 Jarvis opérationnel.\n"
                                f"- Présence PC : {'Oui' if rt.attention.presence.is_at_pc else 'Non'}\n"
                                f"- Heures calmes : {pol.quiet_start if pol else '23:00'} - {pol.quiet_end if pol else '08:00'}\n"
                                f"- Modèle actif : {core_state.models[0].model if core_state.models else 'défaut'}"
                            )
                        if cmd == "brief":
                            res = await core_state.run_chat_turn(
                                "Fais-moi un résumé concis des notifications et événements récents."
                            )
                            return res.content
                        if cmd == "remember":
                            if not arg:
                                return "Usage : /remember <fait à mémoriser>"
                            res = await core_state.run_chat_turn(f"Mémorise ce fait : {arg}")
                            return res.content
                        if cmd == "forget":
                            if not arg:
                                return "Usage : /forget <fait à oublier>"
                            res = await core_state.run_chat_turn(f"Oublie ce fait : {arg}")
                            return res.content
                        if cmd == "quiet":
                            return "Mode silencieux ajusté."
                        if cmd == "model":
                            return f"Modèle actif : {core_state.models[0].model if core_state.models else 'défaut'}"
                        if cmd == "pause":
                            return "Notifications temporairement suspendues."
                        if cmd == "goal":
                            if not arg:
                                return "Usage : /goal <objectif complet à atteindre>"
                            if not rt or not rt.goal_manager:
                                return "Orchestrateur d'objectifs indisponible."
                            try:
                                g = rt.goal_manager.start_goal(goal_text=arg, notify_on_finish=True)
                                return (
                                    f"🎯 Objectif {g.id} lancé (orchestrateur : {g.orchestrator_model}, "
                                    f"budget max : ${g.max_cost_usd:.2f}). Je supervise jusqu'à vérification."
                                )
                            except Exception as exc:
                                return f"Impossible de lancer l'objectif : {exc}"
                        if cmd == "goals":
                            if not rt or not rt.goal_manager:
                                return "Orchestrateur d'objectifs indisponible."
                            goals = rt.goal_manager.list_goals(limit=5)
                            if not goals:
                                return "Aucun objectif enregistré."
                            return "\n".join(
                                f"- {g.id} [{g.status} {g.progress_pct}%] (${g.cost_usd:.3f}) : {g.title[:55]}"
                                for g in goals
                            )
                        if cmd == "auto":
                            if not arg:
                                return "Usage : /auto <objectif de la tâche autonome>"
                            if not rt or not rt.task_manager:
                                return "Gestionnaire autonome indisponible."
                            try:
                                rec = rt.task_manager.start_task(goal=arg, notify_on_finish=True)
                                return (
                                    f"🚀 Tâche autonome {rec.id} lancée (max {rec.max_iterations} étapes). "
                                    "Je te préviens dès que c'est terminé."
                                )
                            except Exception as exc:
                                return f"Impossible de lancer la tâche : {exc}"
                        if cmd == "tasks":
                            if not rt or not rt.task_manager:
                                return "Gestionnaire autonome indisponible."
                            items = rt.task_manager.list_tasks(limit=5)
                            if not items:
                                return "Aucune tâche autonome enregistrée."
                            lines = [
                                f"- {t.id} [{t.status}] ({t.iterations}/{t.max_iterations}) : {t.goal[:60]}"
                                for t in items
                            ]
                            return "\n".join(lines)
                        if cmd == "stop":
                            if not rt or not rt.task_manager:
                                return "Gestionnaire autonome indisponible."
                            cancelled = rt.task_manager.emergency_stop_all()
                            if rt.goal_manager:
                                cancelled.extend(rt.goal_manager.emergency_stop_all())
                            return f"🛑 Arrêt d'urgence activé ({len(cancelled)} tâche(s)/objectif(s) annulé(s))."
                        return f"Commande /{cmd} reçue."

                    tg_gateway = TelegramGateway(
                        bot_token=tg_token,
                        allowed_chat_id=tg_chat,
                        on_text=_tg_text,
                        on_voice=_tg_voice,
                        on_photo=_tg_photo,
                        on_command=_tg_cmd,
                        on_approval=_tg_approval,
                        on_alert_ack=_tg_alert_ack,
                    )
                    telegram_bot, telegram_dp = tg_gateway.build_aiogram_dispatcher()

                    async def _run_tg():
                        logger.info("Bot Telegram démarré pour chat_id=%s", tg_chat)
                        try:
                            await telegram_dp.start_polling(telegram_bot, handle_signals=False)
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning("Erreur polling Telegram: %s", exc)

                    telegram_task = asyncio.create_task(_run_tg())
                except Exception as exc:
                    logger.warning("Échec initialisation bot Telegram: %s", exc)

        if not core_state._is_isolated_test:
            rt_init = core_state.runtime
            if rt_init and rt_init.mcp_manager:
                try:
                    await rt_init.mcp_manager.connect_enabled_servers()
                except Exception as exc:
                    logger.warning("Connexion MCP au demarrage: %s", exc)

        core_state.scheduler = _build_routine_scheduler(core_state)
        if not core_state._is_isolated_test and core_state.scheduler is not None:
            core_state.scheduler.start()
            logger.info("Routines planifiees demarrees : %s", core_state.scheduler.registered_jobs)

        try:
            yield
        finally:
            stop_event.set()
            if core_state.scheduler is not None:
                core_state.scheduler.shutdown()
                core_state.scheduler = None
            if core_state.runtime and core_state.runtime.mcp_manager:
                try:
                    await core_state.runtime.mcp_manager.disconnect_all()
                except Exception:
                    pass
            tick_task.cancel()
            if telegram_task:
                telegram_task.cancel()
                try:
                    await telegram_task
                except asyncio.CancelledError:
                    pass
            if telegram_bot:
                try:
                    await telegram_bot.session.close()
                except Exception:
                    pass
            try:
                await tick_task
            except asyncio.CancelledError:
                pass
            core_state.audio_ducker.restore()

    application = FastAPI(title="JARVIS 2.0 Core API", version="2.0.0", lifespan=lifespan)
    application.state.core = core_state

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8765"],
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Response:
        if (
            request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and not request.url.path.startswith("/api/oauth/")
        ):
            auth = request.headers.get("Authorization", "")
            token = (
                auth[7:].strip()
                if auth.startswith("Bearer ")
                else request.query_params.get("token")
            )
            try:
                _check_token(token)
            except HTTPException as exc:
                return Response(
                    content=json.dumps({"detail": exc.detail}),
                    status_code=exc.status_code,
                    media_type="application/json",
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; worker-src 'self' blob:; "
            "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; connect-src 'self' ws: wss:;"
        )
        return response

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "jarvis-core"}

    # === Conversation ===

    @application.get("/api/messages", response_model=list[ChatMessage])
    def get_messages(limit: int = Query(default=100, ge=1, le=500)) -> list[ChatMessage]:
        return core_state.db.list_messages(limit=limit)

    @application.get("/api/messages/{message_id}/tools", response_model=list[ToolCallRecord])
    def get_message_tools(message_id: str) -> list[ToolCallRecord]:
        return core_state.db.get_message_tools(message_id)

    @application.post("/api/messages/send", response_model=ChatMessage)
    async def send_message(payload: SendMessageRequest) -> ChatMessage:
        return await core_state.run_chat_turn(payload.text)

    @application.post("/api/messages/clear")
    def clear_messages() -> dict[str, int]:
        return {"cleared": core_state.db.clear_messages()}

    @application.post("/api/voice/transcribe")
    async def transcribe_voice(payload: VoiceTranscribeRequest) -> dict[str, str]:
        try:
            text = await core_state.transcribe_audio(
                payload.audio_base64, mime_type=payload.mime_type
            )
            return {"text": text}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Erreur transcription : {exc}") from exc

    @application.post("/api/voice/wake-check")
    async def wake_check_voice(payload: VoiceTranscribeRequest) -> dict[str, str]:
        try:
            text = await core_state.wake_check_audio(
                payload.audio_base64,
                mime_type=payload.mime_type,
                wake_threshold=payload.wake_threshold,
            )
            return {"text": text}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Erreur wake-check : {exc}") from exc

    @application.post("/api/voice/speak")
    async def speak_voice(payload: VoiceSpeakRequest) -> dict[str, Any]:
        audio_b64, sample_rate = await core_state.synthesize_speech(payload.text)
        return {"audio_base64": audio_b64, "sample_rate": sample_rate}

    @application.post("/api/voice/ducking")
    async def voice_ducking(payload: VoiceDuckingRequest) -> dict[str, Any]:
        if payload.active:
            return await asyncio.to_thread(core_state.audio_ducker.duck, payload.ratio)
        return await asyncio.to_thread(core_state.audio_ducker.restore)

    @application.get("/api/voice/profiles", response_model=VoiceProfilesConfig)
    def get_voice_profiles() -> VoiceProfilesConfig:
        return core_state.voice_profiles.get_config()

    @application.put("/api/voice/profiles/settings", response_model=VoiceProfilesConfig)
    def update_voice_profile_settings(
        payload: VoiceProfileSettingsRequest,
    ) -> VoiceProfilesConfig:
        return core_state.voice_profiles.update_settings(
            enabled=payload.enabled,
            threshold=payload.threshold,
        )

    @application.post("/api/voice/profiles", response_model=VoiceProfilesConfig)
    async def create_voice_profile(payload: VoiceProfileCreateRequest) -> VoiceProfilesConfig:
        try:
            audio_bytes = base64.b64decode(payload.audio_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Audio base64 invalide.") from exc
        try:
            return await asyncio.to_thread(
                core_state.voice_profiles.enroll_profile,
                name=payload.name,
                role=payload.role,
                audio_bytes=audio_bytes,
                mime_type=payload.mime_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post(
        "/api/voice/profiles/{profile_id}/samples", response_model=VoiceProfilesConfig
    )
    async def add_voice_profile_sample(
        profile_id: str, payload: VoiceProfileSampleRequest
    ) -> VoiceProfilesConfig:
        try:
            audio_bytes = base64.b64decode(payload.audio_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Audio base64 invalide.") from exc
        try:
            return await asyncio.to_thread(
                core_state.voice_profiles.add_sample,
                profile_id,
                audio_bytes,
                payload.mime_type,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.delete("/api/voice/profiles/{profile_id}", response_model=VoiceProfilesConfig)
    def delete_voice_profile(profile_id: str) -> VoiceProfilesConfig:
        try:
            return core_state.voice_profiles.delete_profile(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/voice/profiles/verify", response_model=VoiceVerifyResult)
    async def verify_voice_profile(payload: VoiceProfileSampleRequest) -> VoiceVerifyResult:
        try:
            audio_bytes = base64.b64decode(payload.audio_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Audio base64 invalide.") from exc
        try:
            return await asyncio.to_thread(
                core_state.voice_profiles.verify_audio,
                audio_bytes,
                payload.mime_type,
                force=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # === Memoire ===

    @application.get("/api/facts", response_model=list[FactRecord])
    def get_facts(
        q: str | None = None,
        category: str | None = None,
        sensitivity: SensitivityLevel | None = None,
    ) -> list[FactRecord]:
        return core_state.db.list_facts(query=q, category=category, sensitivity=sensitivity)

    @application.get("/api/facts/review", response_model=list[FactRecord])
    def get_weekly_review() -> list[FactRecord]:
        return core_state.db.weakest_facts(limit=10)

    @application.post("/api/facts", response_model=FactRecord)
    def create_fact(payload: FactCreate) -> FactRecord:
        return core_state.db.create_fact(payload)

    @application.patch("/api/facts/{fact_id}", response_model=FactRecord)
    def patch_fact(fact_id: str, payload: FactUpdate) -> FactRecord:
        updated = core_state.db.update_fact(fact_id, payload)
        if not updated:
            raise HTTPException(status_code=404, detail="Fait introuvable.")
        return updated

    @application.delete("/api/facts/{fact_id}")
    def remove_fact(fact_id: str) -> dict[str, bool]:
        ok = core_state.db.delete_fact(fact_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Fait introuvable.")
        return {"deleted": True}

    @application.get("/api/wiki", response_model=list[WikiPage])
    def list_wiki() -> list[WikiPage]:
        return core_state.db.export_all(core_state.wiki_dir).wiki_pages

    @application.get("/api/wiki/{slug}", response_model=WikiPage)
    def get_wiki_page(slug: str) -> WikiPage:
        if not SLUG_RE.match(slug):
            raise HTTPException(status_code=400, detail="Identifiant de page invalide.")
        path = core_state.wiki_dir / f"{slug}.md"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Page wiki introuvable.")
        content = path.read_text(encoding="utf-8")
        title = content.splitlines()[0].lstrip("# ").strip() if content else slug
        return WikiPage(
            slug=slug,
            title=title or slug,
            content=content,
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        )

    @application.put("/api/wiki/{slug}", response_model=WikiPage)
    def put_wiki_page(slug: str, payload: WikiUpdate) -> WikiPage:
        if not SLUG_RE.match(slug):
            raise HTTPException(status_code=400, detail="Identifiant de page invalide.")
        core_state.wiki_dir.mkdir(parents=True, exist_ok=True)
        path = core_state.wiki_dir / f"{slug}.md"
        path.write_text(payload.content, encoding="utf-8")
        title = payload.title or (
            payload.content.splitlines()[0].lstrip("# ").strip() if payload.content else slug
        )
        return WikiPage(
            slug=slug,
            title=title or slug,
            content=payload.content,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    @application.get("/api/people", response_model=list[PersonRecord])
    def get_people() -> list[PersonRecord]:
        return core_state.db.list_people()

    @application.post("/api/people", response_model=PersonRecord)
    def create_person(payload: PersonCreateRequest) -> PersonRecord:
        return core_state.db.upsert_person(
            name=payload.name,
            relation=payload.relation,
            birthday=payload.birthday,
            contact_ref=payload.contact_ref,
            notes=payload.notes,
        )

    @application.delete("/api/people/{person_id}")
    def delete_person(person_id: str) -> dict[str, Any]:
        deleted = core_state.db.delete_person(person_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Personne introuvable.")
        return {"deleted": True, "id": person_id}

    @application.get("/api/memory/export", response_model=MemoryExport)
    def export_memory() -> MemoryExport:
        return core_state.db.export_all(core_state.wiki_dir)

    # === Onboarding ===

    @application.get("/api/onboarding/status", response_model=OnboardingStatus)
    def get_onboarding_status() -> OnboardingStatus:
        return core_state.onboarding.get_status()

    @application.post("/api/onboarding/{module_id}/answer", response_model=OnboardingStatus)
    def answer_onboarding(module_id: str, payload: OnboardingAnswerRequest) -> OnboardingStatus:
        try:
            return core_state.onboarding.submit_module_answers(module_id, payload.answers)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # === Modeles & Couts ===

    @application.get("/api/models", response_model=list[RoleModelConfig])
    def get_models() -> list[RoleModelConfig]:
        return core_state.models

    @application.get("/api/models/thinking")
    def get_thinking_mode() -> dict[str, str]:
        runtime = core_state.ensure_runtime()
        agent_route = runtime.router.roles.get("agent")
        if agent_route and agent_route.thinking_mode != "auto":
            return {"mode": agent_route.thinking_mode}
        return {"mode": runtime.router.thinking_mode}

    class UpdateThinkingModeRequest(BaseModel):
        mode: str = "auto"

    @application.put("/api/models/thinking")
    def update_thinking_mode(payload: UpdateThinkingModeRequest) -> dict[str, str]:
        runtime = core_state.ensure_runtime()
        mode = runtime.router.set_thinking_mode(payload.mode)
        for item in core_state.models:
            if item.role == "agent":
                item.thinking_mode = mode  # type: ignore[assignment]
                core_state.db.set_setting(
                    "role_models",
                    json.dumps([m.model_dump() for m in core_state.models]),
                )
                break
        return {"mode": mode}

    @application.put("/api/models/role", response_model=RoleModelConfig)
    def update_role_model(payload: UpdateRoleModelRequest) -> RoleModelConfig:
        runtime = core_state.ensure_runtime()
        route = runtime.router.set_role_model(
            payload.role,
            payload.model,
            provider=payload.provider,
            thinking_mode=payload.thinking_mode,
        )
        if payload.role == "agent" and payload.thinking_mode is not None:
            runtime.router.thinking_mode = route.thinking_mode
        updated_cfg: RoleModelConfig | None = None
        for item in core_state.models:
            if item.role == payload.role:
                item.model = route.model
                item.provider = route.provider
                item.thinking_mode = route.thinking_mode  # type: ignore[assignment]
                if payload.fallback is not None:
                    item.fallback = payload.fallback
                    route.fallback = list(payload.fallback)
                updated_cfg = item
                break
        if updated_cfg is None:
            updated_cfg = RoleModelConfig(
                role=payload.role,
                model=route.model,
                provider=route.provider,
                fallback=payload.fallback or [],
                thinking_mode=route.thinking_mode,  # type: ignore[arg-type]
            )
            core_state.models.append(updated_cfg)
        core_state.db.set_setting(
            "role_models",
            json.dumps([m.model_dump() for m in core_state.models]),
        )
        return updated_cfg

    @application.get("/api/usage/daily", response_model=list[DailyUsageItem])
    def get_daily_usage(days: int = Query(default=14, ge=1, le=90)) -> list[DailyUsageItem]:
        return core_state.db.daily_usage(days=days)

    @application.get("/api/usage/budget")
    def get_daily_budget() -> dict[str, float]:
        raw = core_state.db.get_setting("daily_budget_usd")
        return {"daily_budget_usd": float(raw) if raw is not None else 5.0}

    @application.put("/api/usage/budget")
    def update_daily_budget(payload: DailyBudgetUpdate) -> dict[str, float]:
        val = round(float(payload.daily_budget_usd), 4)
        core_state.db.set_setting("daily_budget_usd", str(val))
        if core_state.runtime is not None:
            core_state.runtime.router.daily_budget_usd = val
        return {"daily_budget_usd": val}

    @application.get("/api/evals/latest", response_model=EvalSuiteResult)
    def get_latest_evals() -> EvalSuiteResult:
        candidates = [core_state.data_dir / "evals" / "latest.json"]
        if not core_state._custom_data_dir:
            candidates.append(core_state.base_root / "evals" / "latest.json")
        for latest_file in candidates:
            if latest_file.exists():
                return EvalSuiteResult.model_validate_json(latest_file.read_text(encoding="utf-8"))
        raise HTTPException(
            status_code=404,
            detail="Aucune evaluation enregistree. Lancez jarvis eval --save.",
        )

    @application.post("/api/evals/run", response_model=EvalSuiteResult)
    async def run_evals() -> EvalSuiteResult:
        from evals.harness import EvalHarness

        harness = EvalHarness()
        started = time.perf_counter()
        raw_results = await harness.run_all()
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        active_model = core_state.ensure_runtime().router.get_role_route("agent").model
        scenarios = [
            EvalScenarioResult(
                id=r.scenario_id,
                title=f"{r.category}: {r.scenario_id}",
                model=active_model,
                passed=r.passed,
                latency_ms=round(elapsed_ms / max(len(raw_results), 1), 1),
                score=1.0 if r.passed else 0.0,
                notes=r.details or ("Reussi" if r.passed else "Echec"),
            )
            for r in raw_results
        ]
        suite = EvalSuiteResult(
            run_at=datetime.now(timezone.utc).isoformat(),
            total=len(scenarios),
            passed=sum(1 for s in scenarios if s.passed),
            scenarios=scenarios,
        )
        if not core_state._is_isolated_test:
            evals_dir = core_state.base_root / "evals"
            evals_dir.mkdir(parents=True, exist_ok=True)
            (evals_dir / "latest.json").write_text(
                suite.model_dump_json(indent=2),
                encoding="utf-8",
            )
        return suite

    # === Attention & Alertes ===

    @application.get("/api/alerts", response_model=list[AlertRecord])
    def get_alerts() -> list[AlertRecord]:
        return core_state.db.list_alerts()

    @application.post("/api/alerts/{alert_id}/ack", response_model=AlertRecord)
    def ack_alert(alert_id: str) -> AlertRecord:
        item = core_state.db.ack_alert(alert_id, too_noisy=False)
        if not item:
            raise HTTPException(status_code=404, detail="Alerte introuvable.")
        return item

    @application.post("/api/alerts/{alert_id}/too-noisy", response_model=AlertRecord)
    def mark_too_noisy(alert_id: str) -> AlertRecord:
        item = core_state.db.ack_alert(alert_id, too_noisy=True)
        if not item:
            raise HTTPException(status_code=404, detail="Alerte introuvable.")
        return item

    @application.get("/api/rules", response_model=AttentionRules)
    def get_rules() -> AttentionRules:
        return core_state.rules

    @application.put("/api/rules", response_model=AttentionRules)
    def update_rules(payload: AttentionRules) -> AttentionRules:
        core_state.rules = payload
        core_state.db.set_setting("attention_rules", core_state.rules.model_dump_json())
        return core_state.rules

    # === Approbations ===

    def _broadcast_approval_resolved(approval_id: str, approved: bool, channel: str) -> None:
        payload = {
            "id": approval_id,
            "approved": approved,
            "status": "approved" if approved else "denied",
            "channel": channel,
        }
        for q in list(core_state.ws_broadcast_queues):
            q.put_nowait(("approval.resolved", payload))

    @application.get("/api/approvals", response_model=list[ApprovalItem])
    def get_approvals() -> list[ApprovalItem]:
        return core_state.db.list_approvals()

    @application.post("/api/approvals/{approval_id}", response_model=ApprovalItem)
    def decide_approval(approval_id: str, payload: ApprovalDecisionRequest) -> ApprovalItem:
        item = core_state.db.decide_approval(approval_id, payload.approved)
        if not item:
            raise HTTPException(status_code=404, detail="Demande d'approbation introuvable.")
        runtime = core_state.ensure_runtime()
        try:
            runtime.approvals.resolve(approval_id, payload.approved)
        except KeyError:
            pass
        _broadcast_approval_resolved(approval_id, payload.approved, "web")
        return item

    # === Actions signées (SMS / Push ntfy) ===

    @application.get("/actions/{token}", response_class=Response)
    async def handle_action_token(token: str) -> Response:
        secret = (
            core_state.secrets.get_secret("ACTION_TOKEN_SECRET")
            or "default-action-token-secret-for-signing"
        )
        verified = verify_action_token(secret, token)
        if not verified:
            html_err = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Jarvis - Lien expiré</title>"
                "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
                "<style>body{font-family:-apple-system,sans-serif;background:#0f172a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:20px;text-align:center;} .card{background:#1e293b;padding:30px;border-radius:16px;max-width:400px;border:1px solid #334155;} h2{color:#f43f5e;margin-top:0;} p{color:#94a3b8;font-size:16px;}</style></head>"
                "<body><div class='card'><h2>Lien expiré ou invalide</h2><p>Ce jeton d'action est introuvable ou a déjà expiré.</p></div></body></html>"
            )
            return Response(content=html_err, status_code=400, media_type="text/html")

        approval_id, action = verified
        approved = action == "approve"
        if core_state.runtime and core_state.runtime.approvals:
            try:
                core_state.runtime.approvals.resolve(
                    request_id=approval_id,
                    approved=approved,
                    channel="action_link",
                )
            except KeyError:
                pass

        if core_state.db and hasattr(core_state.db, "decide_approval"):
            core_state.db.decide_approval(approval_id, approved)
        _broadcast_approval_resolved(approval_id, approved, "action_link")

        title = "Action approuvée" if approved else "Action refusée"
        desc = (
            "C'est fait, Jarvis exécute l'action demandée."
            if approved
            else "L'action a été annulée et refusée."
        )
        color = "#10b981" if approved else "#f43f5e"
        html_ok = (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Jarvis - {title}</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
            f"<style>body{{font-family:-apple-system,sans-serif;background:#0f172a;color:#f8fafc;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:20px;text-align:center;}} .card{{background:#1e293b;padding:30px;border-radius:16px;max-width:400px;border:1px solid #334155;}} h2{{color:{color};margin-top:0;}} p{{color:#94a3b8;font-size:16px;}}</style></head>"
            f"<body><div class='card'><h2>{title}</h2><p>{desc}</p></div></body></html>"
        )
        return Response(content=html_ok, status_code=200, media_type="text/html")

    # === Politique & Ordres de notification ===

    @application.get("/api/notifications/policy")
    def get_notification_policy() -> dict[str, Any]:
        store = PolicyStore(core_state.db)
        return store.get().model_dump()

    @application.put("/api/notifications/policy")
    def update_notification_policy(payload: dict[str, Any]) -> dict[str, Any]:
        store = PolicyStore(core_state.db)
        updated = store.update(**payload)
        return updated.model_dump()

    @application.get("/api/notifications/orders")
    def get_notification_orders(active_only: bool = True) -> list[dict[str, Any]]:
        return core_state.db.list_orders(active_only=active_only)

    @application.post("/api/notifications/orders")
    def create_notification_order(order: NotificationOrder) -> dict[str, Any]:
        core_state.db.upsert_order(order.model_dump())
        return order.model_dump()

    @application.delete("/api/notifications/orders/{order_id}")
    def delete_notification_order(order_id: str) -> dict[str, Any]:
        ok = core_state.db.revoke_order(order_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Ordre introuvable.")
        return {"ok": True, "revoked": order_id}

    @application.get("/api/notifications/attempts")
    def get_notification_attempts(limit: int = 50) -> list[dict[str, Any]]:
        return core_state.db.list_attempts(limit=limit)

    @application.post("/api/webhooks/trading")
    async def receive_trading_webhook(request: Request) -> dict[str, Any]:
        expected_token = os.environ.get("TRADER_WEBHOOK_TOKEN", "").strip()
        if expected_token:
            auth_header = request.headers.get("authorization", "")
            token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
            if not token or not hmac.compare_digest(token, expected_token):
                raise HTTPException(status_code=401, detail="Jeton webhook invalide.")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Corps JSON invalide.") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Un objet JSON est attendu.")

        event_name = str(body.get("event") or "cycle_trading")
        raw_payload = body.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else body
        event_type, summary, dedup_key = format_trading_webhook_event(event_name, payload)

        rt = core_state.ensure_runtime()
        sys_event = SystemEvent(
            source="trading_bot",
            event_type=event_type,
            payload={"message": summary, **payload},
            dedup_key=dedup_key,
        )
        decision = await rt.attention.ingest_event(sys_event, current_hour=datetime.now().hour)
        return {
            "ok": True,
            "event_type": event_type,
            "message": summary,
            "dispatched": decision is not None,
        }

    # === Integrations & Capteurs ===

    def _persist_env(key: str, value: str) -> None:
        os.environ[key] = value
        if not core_state._is_isolated_test:
            core_state.env_file.touch(exist_ok=True)
            set_key(str(core_state.env_file), key, value)

    async def _run_connector_sync(connector: Any) -> dict[str, Any]:
        try:
            res = await connector.sync(core_state.db)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Echec de synchronisation {connector.name} : {exc}",
            ) from exc
        if isinstance(res, dict) and res.get("error"):
            raise HTTPException(status_code=400, detail=str(res["error"]))
        return res if isinstance(res, dict) else {"ok": True}

    @application.get("/api/integrations", response_model=list[IntegrationInfo])
    def get_integrations() -> list[IntegrationInfo]:
        return core_state.integrations.list_info()

    @application.post("/api/integrations/{integration_id}/toggle", response_model=IntegrationInfo)
    async def toggle_integration(integration_id: str) -> IntegrationInfo:
        connector = core_state.integrations.get(integration_id)
        if not connector:
            raise HTTPException(status_code=404, detail="Integration introuvable.")
        will_enable = not connector.enabled
        if will_enable and not core_state._is_isolated_test and not connector.connected:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Identifiants manquants pour {connector.name}. "
                    "Configurez d'abord le jeton ou l'URL."
                ),
            )
        updated = core_state.integrations.toggle(integration_id, enabled=will_enable)
        if not updated:
            raise HTTPException(status_code=404, detail="Integration introuvable.")
        if will_enable and not core_state._is_isolated_test:
            try:
                await _run_connector_sync(connector)
            except HTTPException:
                connector.toggle(False)
                core_state.save_integrations_state()
                raise
        core_state.save_integrations_state()
        return connector.info()

    @application.post("/api/integrations/{integration_id}/sync", response_model=IntegrationInfo)
    async def sync_integration(integration_id: str) -> IntegrationInfo:
        connector = core_state.integrations.get(integration_id)
        if not connector:
            raise HTTPException(status_code=404, detail="Integration introuvable.")
        if not core_state._is_isolated_test and not connector.connected:
            raise HTTPException(
                status_code=400,
                detail=f"{connector.name} n'est pas configure.",
            )
        was_enabled = connector.enabled
        if not was_enabled:
            connector.toggle(True)
        try:
            if not core_state._is_isolated_test:
                await _run_connector_sync(connector)
            else:
                connector.mark_synced()
        except HTTPException:
            if not was_enabled:
                connector.toggle(False)
            core_state.save_integrations_state()
            raise
        core_state.save_integrations_state()
        return connector.info()

    def _apply_provider_tokens(
        env_prefix: str,
        connector_ids: tuple[str, ...],
        connector_cls: type,
        token: str,
        *,
        refresh_token: str = "",
        enable_all: bool = False,
        primary: Any | None = None,
    ) -> None:
        _persist_env(f"{env_prefix}_ACCESS_TOKEN", token)
        if refresh_token:
            _persist_env(f"{env_prefix}_REFRESH_TOKEN", refresh_token)
        for cid in connector_ids:
            peer = core_state.integrations.get(cid)
            if peer is None or not isinstance(peer, connector_cls):
                continue
            if enable_all or peer is primary or not peer.access_token:
                peer.access_token = token
                peer.connected = True
            if enable_all:
                peer.toggle(True)
        if enable_all:
            core_state.save_integrations_state()

    def _apply_google_token(
        token: str,
        *,
        refresh_token: str = "",
        enable_all: bool = False,
        primary: Any | None = None,
    ) -> None:
        _apply_provider_tokens(
            "GOOGLE_OAUTH",
            ("google_calendar", "gmail", "google_contacts"),
            GoogleOAuthIntegration,
            token,
            refresh_token=refresh_token,
            enable_all=enable_all,
            primary=primary,
        )

    def _apply_microsoft_token(
        token: str,
        *,
        refresh_token: str = "",
        enable_all: bool = False,
        primary: Any | None = None,
    ) -> None:
        _apply_provider_tokens(
            "MICROSOFT_OAUTH",
            ("outlook_calendar", "outlook_mail"),
            MicrosoftOAuthIntegration,
            token,
            refresh_token=refresh_token,
            enable_all=enable_all,
            primary=primary,
        )

    def _cleanup_expired_oauth_states() -> None:
        cutoff = time.time() - 900.0
        expired = [
            key
            for key, entry in core_state.oauth_states.items()
            if float(entry.get("created_at", 0.0)) < cutoff
        ]
        for key in expired:
            core_state.oauth_states.pop(key, None)

    def _build_oauth_redirect_uri(request: Request, provider: str) -> str:
        return f"{str(request.base_url).rstrip('/')}/api/oauth/{provider}/callback"

    @application.post(
        "/api/integrations/{integration_id}/configure", response_model=IntegrationInfo
    )
    async def configure_integration(
        integration_id: str,
        payload: IntegrationConfigureRequest,
    ) -> IntegrationInfo:
        connector = core_state.integrations.get(integration_id)
        if not connector:
            raise HTTPException(status_code=404, detail="Integration introuvable.")
        token = payload.token.strip()
        url = payload.url.strip()
        csv_text = payload.csv_text.strip()
        client_id = payload.client_id.strip()
        client_secret = payload.client_secret.strip()

        if isinstance(connector, GoogleOAuthIntegration):
            if client_id:
                _persist_env("GOOGLE_CLIENT_ID", client_id)
            if client_secret:
                _persist_env("GOOGLE_CLIENT_SECRET", client_secret)
            if not token:
                if client_id or client_secret:
                    return connector.info()
                raise HTTPException(status_code=400, detail="Jeton OAuth requis.")
            _apply_google_token(token, primary=connector)
        elif isinstance(connector, MicrosoftOAuthIntegration):
            if client_id:
                _persist_env("MICROSOFT_CLIENT_ID", client_id)
            if client_secret:
                _persist_env("MICROSOFT_CLIENT_SECRET", client_secret)
            if not token:
                if client_id or client_secret:
                    return connector.info()
                raise HTTPException(status_code=400, detail="Jeton OAuth requis.")
            _apply_microsoft_token(token, primary=connector)
        elif isinstance(connector, AppleCalendarIntegration):
            if not url:
                raise HTTPException(
                    status_code=400, detail="URL .ics / webcal:// Apple Calendar requise."
                )
            connector.ics_url = url
            connector.connected = True
            _persist_env("APPLE_CALENDAR_ICS_URL", url)
        elif isinstance(connector, HomeAssistantIntegration):
            if url:
                connector.base_url = url.rstrip("/")
                _persist_env("HOME_ASSISTANT_URL", connector.base_url)
            if token:
                connector.token = token
                _persist_env("HOME_ASSISTANT_TOKEN", token)
            connector.connected = bool(connector.base_url and connector.token)
            if not connector.connected:
                raise HTTPException(status_code=400, detail="URL et jeton Home Assistant requis.")
        elif isinstance(connector, GitHubProbesIntegration):
            raw_urls = url or token
            probe_urls = [u.strip() for u in raw_urls.split(",") if u.strip()]
            connector.probe_urls = probe_urls
            connector.connected = bool(probe_urls)
            _persist_env("GITHUB_PROBE_URLS", ",".join(probe_urls))
        elif isinstance(connector, NotionIntegration):
            if not token:
                raise HTTPException(status_code=400, detail="Cle NOTION_API_KEY requise.")
            connector.api_key = token
            connector.connected = True
            _persist_env("NOTION_API_KEY", token)
        elif isinstance(connector, WeatherTransportIntegration):
            if token:
                connector.navitia_token = token
                _persist_env("NAVITIA_API_KEY", token)
            connector.connected = True
        elif isinstance(connector, SpotifyIntegration):
            if client_id:
                _persist_env("SPOTIFY_CLIENT_ID", client_id)
            if client_secret:
                _persist_env("SPOTIFY_CLIENT_SECRET", client_secret)
            if token:
                connector.access_token = token
                connector.connected = True
                _persist_env("SPOTIFY_ACCESS_TOKEN", token)
            elif client_id or client_secret:
                connector.connected = bool(
                    connector.access_token or os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
                )
                return connector.info()
            else:
                raise HTTPException(status_code=400, detail="Identifiant ou jeton Spotify requis.")
        elif isinstance(connector, HealthIntegration):
            if url:
                _persist_env("HOME_ASSISTANT_URL", url.rstrip("/"))
            if token:
                _persist_env("HEALTH_WEBHOOK_SECRET", token)
            connector.connected = bool(url or token)
            if not connector.connected:
                raise HTTPException(status_code=400, detail="URL Home Assistant ou secret requis.")
        elif isinstance(connector, FinanceIntegration):
            if not csv_text:
                raise HTTPException(status_code=400, detail="Contenu CSV requis.")
            imported = connector.import_csv_text(csv_text, core_state.db)
            if imported == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Aucune ligne CSV valide importee (colonnes label,amount,date).",
                )
            connector.connected = True

        connector.toggle(True)
        if not core_state._is_isolated_test:
            try:
                await _run_connector_sync(connector)
            except HTTPException:
                connector.toggle(False)
                core_state.save_integrations_state()
                raise
        core_state.save_integrations_state()
        return connector.info()

    @application.get("/api/integrations/{integration_id}/oauth/url")
    def get_integration_oauth_url(integration_id: str, request: Request) -> dict[str, Any]:
        connector = core_state.integrations.get(integration_id)
        if not connector:
            raise HTTPException(status_code=404, detail="Integration introuvable.")
        _cleanup_expired_oauth_states()

        oauth_configs: list[tuple[type, str, str, Any]] = [
            (
                GoogleOAuthIntegration,
                "google",
                "GOOGLE_CLIENT_ID",
                build_google_oauth_authorize_url,
            ),
            (
                MicrosoftOAuthIntegration,
                "microsoft",
                "MICROSOFT_CLIENT_ID",
                build_microsoft_oauth_authorize_url,
            ),
            (
                SpotifyIntegration,
                "spotify",
                "SPOTIFY_CLIENT_ID",
                build_spotify_oauth_authorize_url,
            ),
        ]
        for cls, provider, client_id_env, url_builder in oauth_configs:
            if isinstance(connector, cls):
                client_id = os.environ.get(client_id_env, "").strip()
                if client_id:
                    state_tok = secrets.token_urlsafe(24)
                    redirect_uri = _build_oauth_redirect_uri(request, provider)
                    core_state.oauth_states[state_tok] = {
                        "provider": provider,
                        "integration_id": integration_id,
                        "redirect_uri": redirect_uri,
                        "created_at": time.time(),
                    }
                    return {
                        "url": url_builder(client_id, redirect_uri, state=state_tok),
                        "mode": "redirect",
                        "redirect_uri": redirect_uri,
                    }
                ext_url = connector.get_authorize_url() or ""
                return {"url": ext_url, "mode": "external" if ext_url else "unconfigured"}

        auth_url = connector.get_authorize_url()
        if not auth_url:
            raise HTTPException(
                status_code=400, detail=f"{connector.name} n'utilise pas de lien OAuth."
            )
        return {"url": auth_url, "mode": "external"}

    @application.get("/api/oauth/{provider}/callback")
    async def oauth_callback(
        provider: str,
        state: str = "",
        code: str = "",
        error: str = "",
    ) -> RedirectResponse:
        _cleanup_expired_oauth_states()
        state_entry = core_state.oauth_states.pop(state, None) if state else None
        if not state_entry or state_entry.get("provider") != provider:
            raise HTTPException(status_code=400, detail="Parametre state OAuth invalide ou expire.")
        if error:
            return RedirectResponse(
                f"/config?oauth=error&reason={quote(error, safe='')}", status_code=302
            )
        if not code:
            raise HTTPException(status_code=400, detail="Code d'autorisation OAuth manquant.")

        oauth_endpoints = {
            "google": (
                "https://oauth2.googleapis.com/token",
                "GOOGLE_CLIENT_ID",
                "GOOGLE_CLIENT_SECRET",
            ),
            "microsoft": (
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "MICROSOFT_CLIENT_ID",
                "MICROSOFT_CLIENT_SECRET",
            ),
            "spotify": (
                "https://accounts.spotify.com/api/token",
                "SPOTIFY_CLIENT_ID",
                "SPOTIFY_CLIENT_SECRET",
            ),
        }
        if provider not in oauth_endpoints:
            raise HTTPException(status_code=404, detail="Fournisseur OAuth inconnu.")

        token_url, id_env, secret_env = oauth_endpoints[provider]
        client_id = os.environ.get(id_env, "").strip()
        client_secret = os.environ.get(secret_env, "").strip()
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=400,
                detail=f"{id_env} ou {secret_env} manquant.",
            )

        form_data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": str(state_entry["redirect_uri"]),
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_url, data=form_data)
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Echec echange OAuth {provider} ({resp.status_code}) : {resp.text[:240]}",
            )

        token_data = resp.json()
        access_token = str(token_data.get("access_token", "")).strip()
        refresh_token = str(token_data.get("refresh_token", "")).strip()
        if not access_token:
            raise HTTPException(
                status_code=502, detail=f"Reponse OAuth {provider} sans access_token."
            )

        if provider == "google":
            _apply_google_token(access_token, refresh_token=refresh_token, enable_all=True)
        elif provider == "microsoft":
            _apply_microsoft_token(access_token, refresh_token=refresh_token, enable_all=True)
        elif provider == "spotify":
            _persist_env("SPOTIFY_ACCESS_TOKEN", access_token)
            if refresh_token:
                _persist_env("SPOTIFY_REFRESH_TOKEN", refresh_token)
            spot = core_state.integrations.get("spotify")
            if isinstance(spot, SpotifyIntegration):
                spot.access_token = access_token
                spot.connected = True
                spot.toggle(True)
                core_state.save_integrations_state()

        return RedirectResponse(f"/config?oauth=success&provider={provider}", status_code=302)

    @application.get("/api/sensors/blacklist", response_model=BlacklistConfig)
    def get_sensors_blacklist() -> BlacklistConfig:
        return core_state.blacklist

    @application.post("/api/sensors/{sensor_name}/toggle", response_model=BlacklistConfig)
    async def toggle_sensor_endpoint(
        sensor_name: SensorName,
        payload: SensorToggleRequest,
    ) -> BlacklistConfig:
        try:
            return await asyncio.to_thread(
                core_state.set_sensor_paused, sensor_name, payload.paused
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @application.get("/api/sensors/camera/frame")
    async def get_camera_frame() -> Response:
        frame = await asyncio.to_thread(core_state.camera.get_jpeg)
        if not frame:
            raise HTTPException(
                status_code=503, detail="Camera inactive ou aucune image disponible."
            )
        return Response(
            content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @application.get("/api/sensors/camera/stream")
    async def get_camera_stream() -> StreamingResponse:
        if not core_state.camera.is_active:
            raise HTTPException(
                status_code=503, detail="Camera inactive ou aucune image disponible."
            )

        async def _mjpeg_generator() -> AsyncIterator[bytes]:
            last_frame: bytes | None = None
            while core_state.camera.is_active:
                frame = core_state.camera.get_jpeg()
                if frame and frame is not last_frame:
                    last_frame = frame
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: "
                        + str(len(frame)).encode("ascii")
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                await asyncio.sleep(0.04)

        return StreamingResponse(
            _mjpeg_generator(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @application.put("/api/sensors/blacklist", response_model=BlacklistConfig)
    async def update_sensors_blacklist(payload: BlacklistConfig) -> BlacklistConfig:
        for item in payload.sensors:
            if item.sensor == "camera":
                try:
                    await asyncio.to_thread(core_state.set_sensor_paused, "camera", item.paused)
                except RuntimeError as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
        core_state.blacklist = payload
        core_state.db.set_setting("blacklist", core_state.blacklist.model_dump_json())
        return core_state.blacklist

    # === Systeme ===

    @application.get("/api/system/autostart", response_model=SystemAutostartConfig)
    def get_system_autostart() -> SystemAutostartConfig:
        return get_autostart_status()

    @application.put("/api/system/autostart", response_model=SystemAutostartConfig)
    def update_system_autostart(payload: SystemAutostartUpdate) -> SystemAutostartConfig:
        status = set_autostart_enabled(payload.enabled)
        core_state.db.set_setting("autostart_enabled", "1" if status.enabled else "0")
        return status

    # === Autonomie, Taches de fond & MCP ===

    @application.get("/api/autonomy/policy", response_model=AutonomyPolicyConfig)
    def get_autonomy_policy() -> AutonomyPolicyConfig:
        runtime = core_state.ensure_runtime()
        assert runtime.autonomy_policy is not None
        return runtime.autonomy_policy.to_config()

    @application.put("/api/autonomy/policy", response_model=AutonomyPolicyConfig)
    def update_autonomy_policy(payload: AutonomyPolicyUpdate) -> AutonomyPolicyConfig:
        runtime = core_state.ensure_runtime()
        assert runtime.autonomy_policy is not None
        updated = runtime.autonomy_policy.update(payload)
        runtime.autonomy_policy.save_to_db(core_state.db)
        return updated

    @application.post("/api/autonomy/emergency-stop")
    def trigger_emergency_stop() -> dict[str, Any]:
        runtime = core_state.ensure_runtime()
        assert runtime.task_manager is not None
        cancelled = runtime.task_manager.emergency_stop_all()
        if runtime.goal_manager is not None:
            cancelled.extend(runtime.goal_manager.emergency_stop_all())
        return {"ok": True, "emergency_stop": True, "cancelled_tasks": cancelled}

    @application.get("/api/autonomy/tasks", response_model=list[AutonomousTaskRecord])
    def list_autonomous_tasks_endpoint(
        limit: int = Query(default=30, ge=1, le=100),
    ) -> list[AutonomousTaskRecord]:
        runtime = core_state.ensure_runtime()
        assert runtime.task_manager is not None
        return runtime.task_manager.list_tasks(limit=limit)

    @application.post("/api/autonomy/tasks", response_model=AutonomousTaskRecord)
    async def create_autonomous_task_endpoint(
        payload: AutonomousTaskCreate,
    ) -> AutonomousTaskRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.task_manager is not None
        try:
            return runtime.task_manager.start_task(
                goal=payload.goal,
                max_steps=payload.max_steps,
                max_cost_usd=payload.max_cost_usd,
                notify_on_finish=payload.notify_on_finish,
                role=payload.role,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/autonomy/tasks/{task_id}/cancel", response_model=AutonomousTaskRecord)
    def cancel_autonomous_task_endpoint(task_id: str) -> AutonomousTaskRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.task_manager is not None
        rec = runtime.task_manager.cancel_task(task_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Tache autonome introuvable.")
        return rec

    @application.get("/api/autonomy/audit", response_model=list[AutonomyAuditEntry])
    def get_autonomy_audit(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[AutonomyAuditEntry]:
        return core_state.db.list_autonomy_audit(limit=limit)

    @application.get("/api/goals/models")
    def list_goal_models() -> list[dict[str, Any]]:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_catalog is not None
        return [
            {
                "id": p.model_id,
                "provider": p.provider,
                "label": p.model_id.split("/")[-1],
                "tier": p.tier,
                "strengths": p.strengths,
                "in_cost_per_m": p.input_cost_per_1m,
                "out_cost_per_m": p.output_cost_per_1m,
                "supports_vision": p.supports_vision,
                "best_for": list(p.best_for),
            }
            for p in runtime.goal_catalog.available_profiles(runtime.router)
        ]

    @application.get("/api/goals", response_model=list[GoalRecord])
    def list_goals_endpoint(limit: int = Query(default=30, ge=1, le=100)) -> list[GoalRecord]:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        return runtime.goal_manager.list_goals(limit=limit)

    @application.post("/api/goals", response_model=GoalRecord)
    async def create_goal_endpoint(payload: GoalCreate) -> GoalRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        try:
            return runtime.goal_manager.start_goal(
                goal_text=payload.goal,
                max_cost_usd=payload.max_cost_usd,
                orchestrator_model=payload.orchestrator_model,
                allowed_models=payload.allowed_models,
                notify_on_finish=payload.notify_on_finish,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/goals/{goal_id}", response_model=GoalRecord)
    def get_goal_endpoint(goal_id: str) -> GoalRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        rec = runtime.goal_manager.get_goal(goal_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Objectif introuvable")
        return rec

    @application.post("/api/goals/{goal_id}/steer", response_model=GoalRecord)
    async def steer_goal_endpoint(goal_id: str, payload: GoalSteerRequest) -> GoalRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        rec = runtime.goal_manager.steer_goal(
            goal_id,
            payload.message,
            resume=payload.resume,
            extra_budget_usd=payload.extra_budget_usd,
        )
        if not rec:
            raise HTTPException(status_code=404, detail="Objectif introuvable")
        return rec

    @application.post("/api/goals/{goal_id}/pause", response_model=GoalRecord)
    async def pause_goal_endpoint(goal_id: str) -> GoalRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        rec = runtime.goal_manager.pause_goal(goal_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Objectif introuvable")
        return rec

    @application.post("/api/goals/{goal_id}/resume", response_model=GoalRecord)
    async def resume_goal_endpoint(goal_id: str) -> GoalRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        rec = runtime.goal_manager.resume_goal(goal_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Objectif introuvable")
        return rec

    @application.post("/api/goals/{goal_id}/cancel", response_model=GoalRecord)
    async def cancel_goal_endpoint(goal_id: str) -> GoalRecord:
        runtime = core_state.ensure_runtime()
        assert runtime.goal_manager is not None
        rec = runtime.goal_manager.cancel_goal(goal_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Objectif introuvable")
        return rec

    @application.get("/api/mcp/servers", response_model=list[MCPServerStatus])
    def get_mcp_servers() -> list[MCPServerStatus]:
        runtime = core_state.ensure_runtime()
        assert runtime.mcp_manager is not None
        return [
            MCPServerStatus.model_validate(item) for item in runtime.mcp_manager.status_summary()
        ]

    @application.post("/api/mcp/connect")
    async def connect_mcp_servers() -> dict[str, Any]:
        runtime = core_state.ensure_runtime()
        assert runtime.mcp_manager is not None
        return await runtime.mcp_manager.connect_enabled_servers()

    # === WebSocket ===

    @application.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        token = websocket.query_params.get("token")
        expected = os.environ.get("JARVIS_API_TOKEN", "").strip()
        if expected and (not token or not hmac.compare_digest(token.strip(), expected)):
            await websocket.close(code=4401)
            return

        runtime = core_state.ensure_runtime()
        await websocket.accept()
        ws_send_lock = asyncio.Lock()

        async def _send_ws(event_type: str, payload: dict[str, Any]) -> None:
            async with ws_send_lock:
                await websocket.send_json({"type": event_type, "payload": payload})

        async def _pump_approvals() -> None:
            while True:
                req = await runtime.approval_queue.get()
                try:
                    await _send_ws(
                        "approval.request",
                        {
                            "id": req.id,
                            "description": req.description,
                            "risk": int(req.risk) if int(req.risk) in (2, 3) else 2,
                            "expires_at": req.expires_at,
                        },
                    )
                except Exception:
                    return

        last_proactive_alert_ts = 0.0
        proactive_cooldown_seconds = 60.0

        async def _pump_alerts() -> None:
            nonlocal last_proactive_alert_ts
            while True:
                notif, decision = await runtime.alert_queue.get()
                policy = runtime.attention.current_policy
                if not policy.enabled:
                    continue
                urgency_val = int(notif.urgency) if int(notif.urgency) in (0, 1, 2, 3, 4) else 2
                if runtime.attention.presence.is_in_quiet_hours(datetime.now().hour) and not (
                    urgency_val >= 4 and policy.emergency_override
                ):
                    continue
                now_ts = time.monotonic()
                if (
                    last_proactive_alert_ts > 0.0
                    and (now_ts - last_proactive_alert_ts) < proactive_cooldown_seconds
                    and urgency_val < 4
                ):
                    continue
                last_proactive_alert_ts = now_ts
                try:
                    await _send_ws(
                        "alert",
                        {
                            "id": notif.id,
                            "urgency": urgency_val,
                            "message": notif.message,
                            "reason": decision.reason,
                        },
                    )
                except Exception:
                    return

        async def _run_user_turn(text: str) -> None:
            await _send_ws("state", {"value": "thinking"})
            try:
                await core_state.run_chat_turn(
                    text,
                    on_delta=lambda mid, chunk: _send_ws(
                        "assistant.delta", {"text": chunk, "message_id": mid}
                    ),
                    on_tool_call=lambda tc, risk_val: _send_ws(
                        "tool.call",
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "args": tc.arguments,
                            "risk": risk_val if risk_val in (0, 1, 2, 3) else 0,
                        },
                    ),
                    on_tool_result=lambda rec: _send_ws(
                        "tool.result",
                        {"id": rec["id"], "ok": rec["ok"], "summary": rec["summary"]},
                    ),
                    on_done=lambda mid, usage_dict: _send_ws(
                        "assistant.done", {"message_id": mid, "usage": usage_dict}
                    ),
                )
            finally:
                try:
                    await _send_ws("state", {"value": "idle"})
                except Exception:
                    pass

        broadcast_q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        core_state.ws_broadcast_queues.add(broadcast_q)

        async def _pump_broadcast() -> None:
            while True:
                ev_type, ev_payload = await broadcast_q.get()
                try:
                    await _send_ws(ev_type, ev_payload)
                except Exception:
                    return

        turn_tasks: set[asyncio.Task[Any]] = set()
        pump_task = asyncio.create_task(_pump_approvals())
        alert_pump_task = asyncio.create_task(_pump_alerts())
        broadcast_pump_task = asyncio.create_task(_pump_broadcast())
        await _send_ws("state", {"value": "idle"})
        try:
            while True:
                raw_msg = await websocket.receive_json()
                event = CLIENT_WS_ADAPTER.validate_python(raw_msg)
                if event.type == "user.text":
                    for t in list(turn_tasks):
                        t.cancel()
                    task = asyncio.create_task(_run_user_turn(event.payload.text))
                    turn_tasks.add(task)
                    task.add_done_callback(turn_tasks.discard)
                elif event.type == "user.interrupt":
                    for t in list(turn_tasks):
                        t.cancel()
                    await _send_ws("state", {"value": "idle"})
                elif event.type == "action.approve":
                    core_state.db.decide_approval(event.payload.id, event.payload.approved)
                    try:
                        runtime.approvals.resolve(event.payload.id, event.payload.approved)
                    except KeyError:
                        pass
                    _broadcast_approval_resolved(event.payload.id, event.payload.approved, "web")
                elif event.type == "alert.ack":
                    core_state.db.ack_alert(event.payload.id)
                elif event.type == "settings.model":
                    update_role_model(
                        UpdateRoleModelRequest(
                            role=event.payload.role,
                            model=event.payload.model,
                            thinking_mode=event.payload.thinking_mode,
                        )
                    )
                elif event.type == "sensors.toggle":
                    try:
                        await asyncio.to_thread(
                            core_state.set_sensor_paused,
                            event.payload.sensor,
                            event.payload.paused,
                        )
                    except RuntimeError as exc:
                        await _send_ws(
                            "assistant.delta",
                            {
                                "text": f"Erreur capteur {event.payload.sensor} : {exc}",
                                "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                            },
                        )
        except WebSocketDisconnect:
            pass
        finally:
            core_state.ws_broadcast_queues.discard(broadcast_q)
            pump_task.cancel()
            alert_pump_task.cancel()
            broadcast_pump_task.cancel()
            for task in list(turn_tasks):
                task.cancel()

    web_dist = _resolve_base_root() / "web" / "dist"
    if (web_dist / "index.html").is_file():
        assets_dir = web_dist / "assets"
        if assets_dir.is_dir():
            application.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @application.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> Response:
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Route API introuvable.")
            candidate = (web_dist / full_path).resolve()
            if (
                full_path
                and candidate.is_file()
                and str(candidate).startswith(str(web_dist.resolve()))
            ):
                return FileResponse(str(candidate))
            return FileResponse(str(web_dist / "index.html"))

    return application


def _toggle_sensor(cfg: BlacklistConfig, sensor: SensorName, paused: bool) -> None:
    for item in cfg.sensors:
        if item.sensor == sensor:
            item.paused = paused
            item.active = not paused
            return


_default_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    global _default_app
    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
