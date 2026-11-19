"""Tests unitaires et API pour l'attenuation audio systeme (ducking) quand Jarvis parle."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.audio_ducking import SystemAudioDucker, parse_pactl_sink_inputs

SAMPLE_PACTL_OUT = """Sink Input #4005
\tMute: no
\tVolume: front-left: 65536 / 100% / 0,00 dB,   front-right: 65536 / 100% / 0,00 dB
\t\tapplication.name = "Chromium"
\t\tapplication.process.id = "3059"
\t\tapplication.process.binary = "chromium"
\t\tmedia.name = "YouTube"
Sink Input #4540
\tMute: no
\tVolume: front-left: 65536 / 100% / 0,00 dB,   front-right: 65536 / 100% / 0,00 dB
\t\tapplication.name = "Chromium"
\t\tapplication.process.id = "72401"
\t\tapplication.process.binary = "chromium"
\t\tmedia.name = "Playback"
Sink Input #4600
\tMute: no
\tVolume: front-left: 40000 /  61% / -12,86 dB,   front-right: 40000 /  61% / -12,86 dB
\t\tapplication.name = "Spotify"
\t\tapplication.process.id = "8888"
\t\tapplication.process.binary = "spotify"
\t\tmedia.name = "Spotify"
"""


class AudioDuckingTests(unittest.TestCase):
    def test_parse_pactl_sink_inputs(self) -> None:
        streams = parse_pactl_sink_inputs(SAMPLE_PACTL_OUT)
        self.assertEqual(len(streams), 3)
        self.assertEqual(streams[0].id, "4005")
        self.assertEqual(streams[0].volumes, [65536, 65536])
        self.assertEqual(streams[0].pid, 3059)
        self.assertEqual(streams[2].id, "4600")
        self.assertEqual(streams[2].volumes, [40000, 40000])

    def test_duck_skips_jarvis_browser_and_restores_exact_volumes(self) -> None:
        commands: list[list[str]] = []

        def fake_run(args: list[str]) -> tuple[int, str]:
            commands.append(args)
            if args[:3] == ["pactl", "list", "sink-inputs"]:
                return 0, SAMPLE_PACTL_OUT
            return 0, ""

        def fake_cmdline(pid: int) -> str:
            if pid == 72401:
                return (
                    "/usr/lib/chromium/chromium --user-data-dir=/home/tom/.config/chromium-jarvis"
                )
            return "/usr/lib/chromium/chromium"

        ducker = SystemAudioDucker(
            enabled=True,
            run_cmd=fake_run,
            has_pactl=True,
            cmdline_reader=fake_cmdline,
            dedicated_browser_detector=lambda: True,
        )

        res1 = ducker.duck(ratio=0.25, ttl_sec=10.0)
        self.assertTrue(res1["active"])
        self.assertEqual(res1["ducked"], 2)

        set_cmds = [c for c in commands if c[:2] == ["pactl", "set-sink-input-volume"]]
        # Doit baisser #4005 (YouTube) et #4600 (Spotify), jamais #4540 (chromium-jarvis)
        self.assertEqual(
            set_cmds,
            [
                ["pactl", "set-sink-input-volume", "4005", "16384", "16384"],
                ["pactl", "set-sink-input-volume", "4600", "10000", "10000"],
            ],
        )

        # Un second appel pendant la lecture ne doit pas ecraser les volumes d'origine
        ducker.duck(ratio=0.25, ttl_sec=10.0)
        duck2_cmds = [c for c in commands if c[:2] == ["pactl", "set-sink-input-volume"]]
        self.assertEqual(
            duck2_cmds,
            [
                ["pactl", "set-sink-input-volume", "4005", "16384", "16384"],
                ["pactl", "set-sink-input-volume", "4600", "10000", "10000"],
                ["pactl", "set-sink-input-volume", "4005", "16384", "16384"],
                ["pactl", "set-sink-input-volume", "4600", "10000", "10000"],
            ],
        )
        commands.clear()
        res_restore = ducker.restore()
        self.assertFalse(res_restore["active"])
        self.assertEqual(res_restore["restored"], 2)

        restore_cmds = [c for c in commands if c[:2] == ["pactl", "set-sink-input-volume"]]
        self.assertEqual(
            restore_cmds,
            [
                ["pactl", "set-sink-input-volume", "4005", "65536", "65536"],
                ["pactl", "set-sink-input-volume", "4600", "40000", "40000"],
            ],
        )

    def test_api_voice_ducking_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = JarvisCoreState(data_dir=Path(tmp))
            calls: list[str] = []

            class _FakeDucker:
                def duck(self, ratio: float = 0.22) -> dict[str, object]:
                    calls.append(f"duck:{ratio}")
                    return {"active": True, "ducked": 1, "supported": True}

                def restore(self) -> dict[str, object]:
                    calls.append("restore")
                    return {"active": False, "restored": 1, "supported": True}

            state.audio_ducker = _FakeDucker()  # type: ignore[assignment]
            # create_app exige JARVIS_API_TOKEN ou le mode local explicite.
            with mock.patch.dict("os.environ", {"JARVIS_ALLOW_INSECURE": "1"}):
                with TestClient(create_app(state)) as client:
                    r1 = client.post("/api/voice/ducking", json={"active": True, "ratio": 0.2})
                    self.assertEqual(r1.status_code, 200)
                    self.assertTrue(r1.json()["active"])

                    r2 = client.post("/api/voice/ducking", json={"active": False})
                    self.assertEqual(r2.status_code, 200)
                    self.assertFalse(r2.json()["active"])

            self.assertIn("duck:0.2", calls)
            self.assertIn("restore", calls)

    def test_watchdog_auto_restores_on_timeout(self) -> None:
        commands: list[list[str]] = []

        def fake_run(args: list[str]) -> tuple[int, str]:
            commands.append(args)
            if args[:3] == ["pactl", "list", "sink-inputs"]:
                return 0, SAMPLE_PACTL_OUT
            return 0, ""

        ducker = SystemAudioDucker(
            enabled=True,
            run_cmd=fake_run,
            has_pactl=True,
            cmdline_reader=lambda pid: "chromium-jarvis" if pid == 72401 else "chromium",
            dedicated_browser_detector=lambda: True,
        )
        # Timer watchdog minimal (clamped a 2.0s interne) -> test annulation propre sur restore
        ducker.duck(ratio=0.25, ttl_sec=0.1)
        self.assertTrue(ducker._saved_volumes)
        time.sleep(0.05)
        ducker.restore()
        self.assertEqual(ducker._saved_volumes, {})


if __name__ == "__main__":
    unittest.main()
