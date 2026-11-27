"""Test d'integration reel du module d'inspection Antigravity.

Ce fichier interroge le vrai systeme (psutil + vrais repertoires de logs).
Il est ignore automatiquement si aucun processus 'agy' n'est en cours,
afin de rester vert en CI.
"""

from __future__ import annotations

import psutil
import pytest
from jarvis_core.tools.antigravity_status import get_antigravity_status

EXPECTED_KEYS = {"pid", "status", "last_activity", "snippet", "timestamp"}
VALID_STATUSES = {"reflecting", "responded", "idle", "not_running"}


def _live_agy_pids() -> list[int]:
    """Retourne les pid des processus 'agy' reellement presents sur la machine."""
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            if "agy" in name or "agy" in cmd:
                pids.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return pids


@pytest.mark.integration
def test_real_status_shape_and_domain() -> None:
    """La fonction reelle retourne toujours la structure et le domaine attendus."""
    result = get_antigravity_status()

    assert set(result) == EXPECTED_KEYS
    assert result["status"] in VALID_STATUSES
    assert isinstance(result["timestamp"], float)
    assert result["timestamp"] > 0
    assert isinstance(result["snippet"], str)
    assert isinstance(result["last_activity"], float)
    assert result["pid"] is None or isinstance(result["pid"], int)


@pytest.mark.integration
def test_real_status_detects_live_agy_process() -> None:
    """Le pid retourne correspond a un processus agy reellement vivant."""
    live = _live_agy_pids()
    if not live:
        pytest.skip("Aucun processus 'agy' en cours sur cette machine.")

    result = get_antigravity_status()

    assert result["pid"] is not None, "Un processus agy est vivant mais pid est None"
    assert result["pid"] in live, f"pid {result['pid']} absent des processus agy reels {live}"
    # Le processus retourne doit etre joignable par psutil.
    assert psutil.pid_exists(result["pid"])
    assert psutil.Process(result["pid"]).is_running()


@pytest.mark.integration
def test_real_status_matches_expected_pid_5046() -> None:
    """Verifie specifiquement l'etat du processus agy pid 5046 demande."""
    if not psutil.pid_exists(5046):
        pytest.skip("Le processus agy pid 5046 n'est plus en cours.")

    proc = psutil.Process(5046)
    if "agy" not in proc.name().lower() and "agy" not in " ".join(proc.cmdline()).lower():
        pytest.skip("Le pid 5046 ne correspond plus a un processus agy.")

    result = get_antigravity_status()

    assert result["pid"] == 5046
    assert result["status"] in VALID_STATUSES
    assert result["status"] != "not_running"
