"""Tests unitaires et API pour les profils vocaux et le filtrage de locuteur."""

from __future__ import annotations

import base64
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.voice_profiles import (
    SpeakerEmbedder,
    VoiceProfileStore,
    compute_kaldi_fbank,
    decode_audio_to_16k,
)


def _make_wav_bytes(freq_hz: float, duration_sec: float = 2.0, sample_rate: int = 16000) -> bytes:
    t = np.linspace(
        0.0, duration_sec, int(sample_rate * duration_sec), endpoint=False, dtype=np.float32
    )
    if freq_hz <= 0:
        samples = np.zeros_like(t)
    else:
        samples = 0.25 * np.sin(2.0 * np.pi * freq_hz * t) + 0.1 * np.sin(
            2.0 * np.pi * (freq_hz * 2.0) * t
        )
    pcm16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _fake_speaker_embed(samples_16k: np.ndarray) -> np.ndarray:
    """Genere un vecteur 256-D deterministe selon la frequence dominante du signal."""
    n = min(len(samples_16k), 16000)
    seg = samples_16k[:n]
    spec = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(len(seg), d=1.0 / 16000.0)
    dom_freq = float(freqs[int(np.argmax(spec))]) if len(spec) > 0 else 0.0
    vec = np.zeros(256, dtype=np.float32)
    if dom_freq < 160.0:
        vec[0] = 1.0
    elif dom_freq < 230.0:
        vec[1] = 1.0
    elif dom_freq < 320.0:
        vec[2] = 1.0
    else:
        vec[3] = 1.0
    return vec


class VoiceProfilesTests(unittest.TestCase):
    def test_kaldi_fbank_and_wav_decoding(self) -> None:
        wav_48k = _make_wav_bytes(140.0, duration_sec=1.5, sample_rate=48000)
        decoded = decode_audio_to_16k(wav_48k, "audio/wav")
        self.assertEqual(decoded.dtype, np.float32)
        self.assertAlmostEqual(len(decoded), 24000, delta=10)

        feats = compute_kaldi_fbank(decoded)
        self.assertEqual(feats.ndim, 2)
        self.assertEqual(feats.shape[1], 80)
        self.assertTrue(np.all(np.isfinite(feats)))
        # CMN : moyenne temporelle proche de 0 sur chaque bande
        self.assertLess(float(np.max(np.abs(feats.mean(axis=0)))), 1e-4)

    def test_multi_speaker_store_enrollment_verify_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = MemoryDatabase(Path(tmp) / "jarvis.db")
            embedder = SpeakerEmbedder(embed_fn=_fake_speaker_embed)
            store = VoiceProfileStore(db, embedder=embedder)

            self.assertFalse(store.is_active())

            # Rejette le silence pur a l'enregistrement
            with self.assertRaises(ValueError):
                store.enroll_profile(
                    name="Silence",
                    role="ami",
                    audio_bytes=_make_wav_bytes(0.0, duration_sec=2.0),
                )

            # Enregistre 3 voix (Tom, Copine, Ami) sans dependre de Silero VAD
            with patch(
                "jarvis_core.voice_profiles.extract_speech_samples",
                side_effect=lambda s, **_: (
                    (s, len(s) / 16000.0)
                    if float(np.max(np.abs(s))) > 0.01
                    else (np.zeros(0, dtype=np.float32), 0.0)
                ),
            ):
                cfg1 = store.enroll_profile(
                    name="Tom",
                    role="propriétaire",
                    audio_bytes=_make_wav_bytes(120.0, duration_sec=2.5),
                )
                self.assertTrue(cfg1.enabled)
                self.assertEqual(len(cfg1.profiles), 1)

                store.enroll_profile(
                    name="Léa",
                    role="compagne",
                    audio_bytes=_make_wav_bytes(200.0, duration_sec=2.5),
                )
                cfg3 = store.enroll_profile(
                    name="Alex",
                    role="ami",
                    audio_bytes=_make_wav_bytes(280.0, duration_sec=2.5),
                )
                self.assertEqual(len(cfg3.profiles), 3)

                # Verifie chaque personne autorisee
                res_tom = store.verify_audio(_make_wav_bytes(125.0, duration_sec=1.5))
                self.assertTrue(res_tom.accepted)
                self.assertEqual(res_tom.matched_name, "Tom")

                res_lea = store.verify_audio(_make_wav_bytes(205.0, duration_sec=1.5))
                self.assertTrue(res_lea.accepted)
                self.assertEqual(res_lea.matched_name, "Léa")

                # Verifie qu'une voix inconnue (420 Hz) est ignoree
                res_unknown = store.verify_audio(_make_wav_bytes(420.0, duration_sec=1.5))
                self.assertFalse(res_unknown.accepted)
                self.assertIsNone(res_unknown.matched_name)

                # Ajoute un echantillon supplementaire a Tom
                tom_id = cfg3.profiles[0].id
                cfg_sample = store.add_sample(tom_id, _make_wav_bytes(130.0, duration_sec=2.0))
                self.assertEqual(cfg_sample.profiles[0].sample_count, 2)

                # Persistance apres rechargement du store
                reloaded = VoiceProfileStore(db, embedder=embedder)
                self.assertTrue(reloaded.is_active())
                self.assertEqual(len(reloaded.get_config().profiles), 3)
                self.assertTrue(
                    reloaded.verify_audio(_make_wav_bytes(285.0, duration_sec=1.5)).accepted
                )

                # Desactivation manuelle -> tout le monde passe
                reloaded.update_settings(enabled=False)
                self.assertFalse(reloaded.is_active())
                self.assertTrue(
                    reloaded.verify_audio(
                        _make_wav_bytes(420.0, duration_sec=1.5), force=False
                    ).accepted
                )

                # Suppression de tous les profils -> desactive automatiquement
                for p in list(reloaded.get_config().profiles):
                    reloaded.delete_profile(p.id)
                self.assertEqual(len(reloaded.get_config().profiles), 0)
                self.assertFalse(reloaded.get_config().enabled)

    def test_api_endpoints_and_transcribe_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = JarvisCoreState(data_dir=Path(tmp))
            state.voice_profiles.embedder = SpeakerEmbedder(embed_fn=_fake_speaker_embed)
            client = TestClient(create_app(state))

            with (
                patch(
                    "jarvis_core.voice_profiles.extract_speech_samples",
                    side_effect=lambda s, **_: (s, len(s) / 16000.0),
                ),
                patch.object(
                    state,
                    "_decode_and_vad_wav",
                    side_effect=lambda b, m, is_wake_check=False: (True, decode_audio_to_16k(b, m)),
                ),
            ):
                tom_b64 = base64.b64encode(_make_wav_bytes(120.0, duration_sec=2.0)).decode("ascii")
                unknown_b64 = base64.b64encode(_make_wav_bytes(420.0, duration_sec=2.0)).decode(
                    "ascii"
                )

                resp = client.post(
                    "/api/voice/profiles",
                    json={"name": "Tom", "role": "propriétaire", "audio_base64": tom_b64},
                )
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertTrue(body["enabled"])
                self.assertEqual(len(body["profiles"]), 1)
                profile_id = body["profiles"][0]["id"]

                # Test /api/voice/profiles/verify
                ver_ok = client.post(
                    "/api/voice/profiles/verify",
                    json={"audio_base64": tom_b64},
                )
                self.assertEqual(ver_ok.status_code, 200)
                self.assertTrue(ver_ok.json()["accepted"])
                self.assertEqual(ver_ok.json()["matched_name"], "Tom")

                ver_no = client.post(
                    "/api/voice/profiles/verify",
                    json={"audio_base64": unknown_b64},
                )
                self.assertEqual(ver_no.status_code, 200)
                self.assertFalse(ver_no.json()["accepted"])

                # Test /api/voice/transcribe : voix connue passe, voix inconnue ignoree ("")
                tr_ok = client.post("/api/voice/transcribe", json={"audio_base64": tom_b64})
                self.assertEqual(tr_ok.status_code, 200)
                self.assertEqual(tr_ok.json()["text"], "Bonjour Jarvis")

                tr_no = client.post("/api/voice/transcribe", json={"audio_base64": unknown_b64})
                self.assertEqual(tr_no.status_code, 200)
                self.assertEqual(tr_no.json()["text"], "")

                # Test /api/voice/wake-check avec wake_threshold valide et borne invalide
                wk_ok = client.post(
                    "/api/voice/wake-check",
                    json={"audio_base64": tom_b64, "wake_threshold": 0.25},
                )
                self.assertEqual(wk_ok.status_code, 200)
                wk_bad = client.post(
                    "/api/voice/wake-check",
                    json={"audio_base64": tom_b64, "wake_threshold": 0.99},
                )
                self.assertEqual(wk_bad.status_code, 422)

                # Ajout d'un echantillon + reglage seuil + suppression
                smp = client.post(
                    f"/api/voice/profiles/{profile_id}/samples",
                    json={"audio_base64": tom_b64},
                )
                self.assertEqual(smp.status_code, 200)
                self.assertEqual(smp.json()["profiles"][0]["sample_count"], 2)

                upd = client.put("/api/voice/profiles/settings", json={"threshold": 0.6})
                self.assertEqual(upd.status_code, 200)
                self.assertAlmostEqual(upd.json()["threshold"], 0.6)

                dele = client.delete(f"/api/voice/profiles/{profile_id}")
                self.assertEqual(dele.status_code, 200)
                self.assertEqual(dele.json()["profiles"], [])
                self.assertFalse(dele.json()["enabled"])


if __name__ == "__main__":
    unittest.main()
