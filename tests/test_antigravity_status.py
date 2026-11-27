"""Tests unitaires du module d'inspection Antigravity.

Hermetiques : processus systeme et fichiers de logs sont simules via monkeypatch.
"""

from __future__ import annotations

import glob
import os
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from jarvis_core.tools import antigravity_status as ags
from jarvis_core.tools.antigravity_status import get_antigravity_status

EXPECTED_KEYS = {"pid", "status", "last_activity", "snippet", "timestamp"}
VALID_STATUSES = {"reflecting", "responded", "idle", "not_running"}


class FakeProcess:
    """Imite l'interface minimale de psutil.Process utilisee par le module."""

    def __init__(
        self,
        pid: int,
        name: str = "agy",
        cmdline: list[str] | None = None,
        cpu: float = 0.0,
        cpu_error: Exception | None = None,
    ) -> None:
        self.info: dict[str, Any] = {
            "pid": pid,
            "name": name,
            "cmdline": cmdline if cmdline is not None else [name],
        }
        self._cpu = cpu
        self._cpu_error = cpu_error

    def cpu_percent(self, interval: float = 0.0) -> float:  # noqa: ARG002
        if self._cpu_error is not None:
            raise self._cpu_error
        return self._cpu


class ExplodingProcess:
    """Processus dont l'acces a `.info` leve une exception psutil."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @property
    def info(self) -> dict[str, Any]:
        raise self._exc


# --------------------------------------------------------------------------- #
# Helpers / fixtures
# --------------------------------------------------------------------------- #
def _patch_processes(monkeypatch: pytest.MonkeyPatch, procs: list[Any]) -> None:
    """Remplace psutil.process_iter par une iteration sur des faux processus."""
    monkeypatch.setattr(ags.psutil, "process_iter", lambda attrs=None: iter(procs))


@pytest.fixture()
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirige les repertoires de logs du module vers un dossier temporaire isole."""
    fake_home = tmp_path / "home"
    fake_log = fake_home / ".gemini" / "antigravity-cli" / "log"
    fake_log.mkdir(parents=True)
    tmp_logs = tmp_path / "tmp"
    tmp_logs.mkdir()

    real_expanduser = os.path.expanduser

    def fake_expanduser(path: str) -> str:
        if path.startswith("~"):
            return str(fake_home) + path[1:]
        return real_expanduser(path)

    real_glob = glob.glob

    def fake_glob(pattern: str) -> list[str]:
        # Le module scanne en dur "/tmp" : on l'isole vers le dossier temporaire du test.
        # Comparaison sur le dossier parent exact, car tmp_path est lui-meme sous /tmp.
        if os.path.dirname(pattern) == "/tmp":
            pattern = os.path.join(str(tmp_logs), os.path.basename(pattern))
        return real_glob(pattern)

    monkeypatch.setattr(ags.os.path, "expanduser", fake_expanduser)
    monkeypatch.setattr(ags.glob, "glob", fake_glob)
    return fake_log


def _write_log(path: Path, content: str, age_seconds: float = 0.0) -> Path:
    """Ecrit un fichier de log et force son mtime a `age_seconds` dans le passe."""
    path.write_text(content, encoding="utf-8")
    if age_seconds:
        past = time.time() - age_seconds
        os.utime(path, (past, past))
    return path


# --------------------------------------------------------------------------- #
# 1. Processus absent
# --------------------------------------------------------------------------- #
def test_no_process_no_logs_returns_not_running(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Aucun processus agy et aucun log : statut not_running, pid None."""
    _patch_processes(monkeypatch, [])

    result = get_antigravity_status()

    assert set(result) == EXPECTED_KEYS
    assert result["pid"] is None
    assert result["status"] == "not_running"
    assert result["last_activity"] == 0.0
    assert result["snippet"] == ""
    assert isinstance(result["timestamp"], float)


def test_no_process_but_recent_log_is_idle(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Pas de processus mais un log tres recent (< 60s) : statut idle."""
    _patch_processes(monkeypatch, [])
    _write_log(log_dir / "agy.log", "recent activity\n", age_seconds=5)

    result = get_antigravity_status()

    assert result["pid"] is None
    assert result["status"] == "idle"
    assert result["last_activity"] > 0
    assert "recent activity" in result["snippet"]


def test_no_process_old_log_is_not_running(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Pas de processus et log ancien (> 60s) : statut not_running."""
    _patch_processes(monkeypatch, [])
    _write_log(log_dir / "agy.log", "old activity\n", age_seconds=600)

    result = get_antigravity_status()

    assert result["pid"] is None
    assert result["status"] == "not_running"
    assert result["last_activity"] > 0


# --------------------------------------------------------------------------- #
# 2. Processus actif
# --------------------------------------------------------------------------- #
def test_active_process_high_cpu_is_reflecting(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Processus agy avec CPU > 1% : statut reflecting."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=42.5)])
    _write_log(log_dir / "agy.log", "thinking...\n", age_seconds=300)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["status"] == "reflecting"


def test_active_process_recent_log_is_reflecting(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Processus agy inactif CPU mais log < 15s : statut reflecting."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "agy.log", "streaming\n", age_seconds=3)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["status"] == "reflecting"


def test_active_process_medium_log_age_is_responded(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Processus agy, log entre 15s et 120s : statut responded."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "agy.log", "answer done\n", age_seconds=60)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["status"] == "responded"


def test_active_process_old_log_is_idle(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Processus agy, log > 120s : statut idle."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "agy.log", "stale\n", age_seconds=900)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["status"] == "idle"


# --------------------------------------------------------------------------- #
# 3. Fichiers de logs fictifs : selection, snippet, robustesse
# --------------------------------------------------------------------------- #
def test_latest_log_is_selected_among_several(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Le log le plus recent est retenu pour last_activity et le snippet."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "old.log", "OLD-MARKER\n", age_seconds=500)
    _write_log(log_dir / "new.log", "NEW-MARKER\n", age_seconds=2)

    result = get_antigravity_status()

    assert "NEW-MARKER" in result["snippet"]
    assert "OLD-MARKER" not in result["snippet"]
    assert result["status"] == "reflecting"


def test_snippet_keeps_last_ten_significant_lines(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Le snippet joint les 10 dernieres lignes non vides avec ' | '."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    lines = [f"line-{i}" for i in range(1, 21)]
    _write_log(log_dir / "agy.log", "\n".join(lines) + "\n", age_seconds=1)

    result = get_antigravity_status()

    parts = result["snippet"].split(" | ")
    assert len(parts) == 10
    assert parts[0] == "line-11"
    assert parts[-1] == "line-20"


def test_snippet_ignores_blank_lines(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Les lignes vides ou blanches sont filtrees du snippet."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "agy.log", "alpha\n\n   \nbeta\n\n", age_seconds=1)

    result = get_antigravity_status()

    assert result["snippet"] == "alpha | beta"


def test_empty_log_file_gives_empty_snippet(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Un log vide ne produit pas de snippet mais reste un log valide."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "agy.log", "", age_seconds=1)

    result = get_antigravity_status()

    assert result["snippet"] == ""
    assert result["last_activity"] > 0


# --------------------------------------------------------------------------- #
# 4. Robustesse : exceptions psutil, erreurs CPU, detection par cmdline
# --------------------------------------------------------------------------- #
def test_process_iter_exceptions_are_skipped(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Les processus inaccessibles (AccessDenied, NoSuchProcess) sont ignores."""
    procs = [
        ExplodingProcess(psutil.AccessDenied(pid=1)),
        ExplodingProcess(psutil.NoSuchProcess(pid=2)),
        ExplodingProcess(psutil.ZombieProcess(pid=3)),
        FakeProcess(pid=5046, cpu=0.0),
    ]
    _patch_processes(monkeypatch, procs)
    _write_log(log_dir / "agy.log", "ok\n", age_seconds=1)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["status"] == "reflecting"


def test_cpu_percent_error_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Une erreur de lecture CPU est avalee : le pid reste detecte."""
    proc = FakeProcess(pid=5046, cpu_error=psutil.AccessDenied(pid=5046))
    _patch_processes(monkeypatch, [proc])
    _write_log(log_dir / "agy.log", "ok\n", age_seconds=1)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    # CPU retombe a 0.0, mais le log recent maintient le statut reflecting.
    assert result["status"] == "reflecting"


def test_process_detected_via_cmdline(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Un processus dont le nom differe mais dont la cmdline contient 'agy' est detecte."""
    proc = FakeProcess(pid=7777, name="node", cmdline=["node", "/usr/bin/agy", "--serve"], cpu=0.0)
    _patch_processes(monkeypatch, [proc])
    _write_log(log_dir / "agy.log", "ok\n", age_seconds=1)

    result = get_antigravity_status()

    assert result["pid"] == 7777


def test_unrelated_processes_are_ignored(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Un processus sans 'agy' dans le nom ni la cmdline n'est pas retenu."""
    procs = [
        FakeProcess(pid=11, name="bash", cmdline=["bash", "-l"], cpu=0.0),
        FakeProcess(pid=22, name="python3", cmdline=["python3", "main.py"], cpu=0.0),
    ]
    _patch_processes(monkeypatch, procs)

    result = get_antigravity_status()

    assert result["pid"] is None
    assert result["status"] == "not_running"


def test_missing_log_directories_are_tolerated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Si aucun repertoire de logs n'existe, la fonction retourne un etat coherent."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])

    missing_home = tmp_path / "nowhere"
    real_expanduser = os.path.expanduser

    def fake_expanduser(path: str) -> str:
        if path.startswith("~"):
            return str(missing_home) + path[1:]
        return real_expanduser(path)

    real_glob = glob.glob

    def fake_glob(pattern: str) -> list[str]:
        if os.path.dirname(pattern) == "/tmp":
            return []
        return real_glob(pattern)

    monkeypatch.setattr(ags.os.path, "expanduser", fake_expanduser)
    monkeypatch.setattr(ags.glob, "glob", fake_glob)

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["last_activity"] == 0.0
    assert result["snippet"] == ""
    # Aucun log : age infini, donc pas de reflecting malgre le pid.
    assert result["status"] == "idle"


def test_status_is_always_in_expected_domain(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> None:
    """Le statut retourne appartient toujours au domaine documente."""
    _patch_processes(monkeypatch, [FakeProcess(pid=5046, cpu=0.0)])
    _write_log(log_dir / "agy.log", "x\n", age_seconds=1)

    result = get_antigravity_status()

    assert result["status"] in VALID_STATUSES
    assert isinstance(result["timestamp"], float)
    assert result["timestamp"] > 0
