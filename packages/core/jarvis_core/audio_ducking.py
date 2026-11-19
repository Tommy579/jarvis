"""Attenuation temporaire (audio ducking) des autres flux audio systeme pendant que Jarvis parle."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SINK_INPUT_RE = re.compile(r"^Sink Input #(\d+)\s*$")
_VOL_CHANNEL_RE = re.compile(r":\s*(\d+)\s*/\s*\d+%")
_PROP_RE = re.compile(r'^\s*([a-zA-Z0-9_.-]+)\s*=\s*"(.*)"\s*$')
_BROWSER_BINARIES = {"chromium", "chrome", "google-chrome", "brave", "firefox", "microsoft-edge"}


@dataclass
class SinkInputInfo:
    id: str
    volumes: list[int]
    pid: int | None = None
    app_name: str = ""
    binary: str = ""
    media_name: str = ""


def parse_pactl_sink_inputs(raw_output: str) -> list[SinkInputInfo]:
    """Parse la sortie de `pactl list sink-inputs`."""
    items: list[SinkInputInfo] = []
    current: SinkInputInfo | None = None
    for line in (raw_output or "").splitlines():
        m_id = _SINK_INPUT_RE.match(line)
        if m_id:
            if current is not None:
                items.append(current)
            current = SinkInputInfo(id=m_id.group(1), volumes=[])
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("Volume:") and not current.volumes:
            vols = [int(v) for v in _VOL_CHANNEL_RE.findall(stripped)]
            current.volumes = vols
            continue
        m_prop = _PROP_RE.match(line)
        if m_prop:
            key, val = m_prop.group(1), m_prop.group(2)
            if key == "application.process.id":
                try:
                    current.pid = int(val)
                except ValueError:
                    pass
            elif key == "application.name":
                current.app_name = val
            elif key == "application.process.binary":
                current.binary = val
            elif key == "media.name":
                current.media_name = val
    if current is not None:
        items.append(current)
    return items


def _read_proc_cmdline(pid: int) -> str:
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _has_dedicated_jarvis_browser() -> bool:
    """Indique si la fenetre dediee `--user-data-dir=.../chromium-jarvis` tourne."""
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            if "chromium-jarvis" in _read_proc_cmdline(int(entry)):
                return True
    except OSError:
        pass
    return False


def _default_run_cmd(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        return proc.returncode, proc.stdout
    except Exception as exc:
        logger.debug("Commande audio %s echouee : %s", args, exc)
        return 1, ""


class SystemAudioDucker:
    """Baisse temporairement les autres flux PipeWire/PulseAudio quand Jarvis parle."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        run_cmd: Callable[[list[str]], tuple[int, str]] | None = None,
        has_pactl: bool | None = None,
        cmdline_reader: Callable[[int], str] | None = None,
        dedicated_browser_detector: Callable[[], bool] | None = None,
    ) -> None:
        self._enabled = enabled
        self._run_cmd = run_cmd or _default_run_cmd
        self._has_pactl = (shutil.which("pactl") is not None) if has_pactl is None else has_pactl
        self._read_cmdline = cmdline_reader or _read_proc_cmdline
        self._has_dedicated_browser = dedicated_browser_detector or _has_dedicated_jarvis_browser
        self._lock = threading.Lock()
        self._saved_volumes: dict[str, list[int]] = {}
        self._watchdog: threading.Timer | None = None

    def is_available(self) -> bool:
        return self._enabled and self._has_pactl

    def _is_jarvis_stream(self, stream: SinkInputInfo, has_dedicated: bool) -> bool:
        if stream.pid is not None and stream.pid == os.getpid():
            return True
        if "jarvis" in stream.app_name.lower() or "jarvis" in stream.media_name.lower():
            return True
        if stream.pid is not None:
            cmd = self._read_cmdline(stream.pid)
            if "chromium-jarvis" in cmd:
                return True
        # Si Jarvis tourne dans un navigateur classique (sans chromium-jarvis),
        # on evite de baisser le flux audio du navigateur qui porte aussi sa voix.
        if not has_dedicated and stream.binary.lower() in _BROWSER_BINARIES:
            return True
        return False

    def _cancel_watchdog_locked(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _schedule_watchdog_locked(self, ttl_sec: float) -> None:
        self._cancel_watchdog_locked()
        timer = threading.Timer(max(2.0, ttl_sec), self.restore)
        timer.daemon = True
        timer.start()
        self._watchdog = timer

    def duck(self, ratio: float = 0.22, ttl_sec: float = 25.0) -> dict[str, Any]:
        """Baisse le volume des autres flux audio systeme et arme un watchdog de restauration."""
        if not self.is_available():
            return {"active": False, "ducked": 0, "supported": self._has_pactl}

        safe_ratio = min(0.8, max(0.05, float(ratio)))
        rc, out = self._run_cmd(["pactl", "list", "sink-inputs"])
        if rc != 0:
            return {"active": False, "ducked": 0, "supported": self._has_pactl}

        streams = parse_pactl_sink_inputs(out)
        has_dedicated = self._has_dedicated_browser()

        with self._lock:
            ducked_count = 0
            for stream in streams:
                if not stream.volumes or self._is_jarvis_stream(stream, has_dedicated):
                    continue
                if stream.id not in self._saved_volumes:
                    self._saved_volumes[stream.id] = list(stream.volumes)
                orig = self._saved_volumes[stream.id]
                target = [max(1, int(round(v * safe_ratio))) if v > 0 else 0 for v in orig]
                if target != stream.volumes:
                    self._run_cmd(
                        ["pactl", "set-sink-input-volume", stream.id, *(str(v) for v in target)]
                    )
                    ducked_count += 1
            if self._saved_volumes:
                self._schedule_watchdog_locked(ttl_sec)
            return {
                "active": bool(self._saved_volumes),
                "ducked": len(self._saved_volumes) or ducked_count,
                "supported": True,
            }

    def restore(self) -> dict[str, Any]:
        """Restaure exactement les volumes d'origine de tous les flux attenues."""
        with self._lock:
            self._cancel_watchdog_locked()
            saved = dict(self._saved_volumes)
            self._saved_volumes.clear()

        if not self.is_available() or not saved:
            return {"active": False, "restored": 0, "supported": self._has_pactl}

        restored = 0
        for sink_input_id, vols in saved.items():
            if not vols:
                continue
            rc, _ = self._run_cmd(
                ["pactl", "set-sink-input-volume", sink_input_id, *(str(v) for v in vols)]
            )
            if rc == 0:
                restored += 1
        return {"active": False, "restored": restored, "supported": True}
