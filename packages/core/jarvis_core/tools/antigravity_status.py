"""Module d'inspection Antigravity pour jarvis_core."""

from __future__ import annotations

import glob
import os
import time
from typing import Any

import psutil


def get_antigravity_status() -> dict[str, Any]:
    """Recherche le processus 'agy' et inspecte les logs récents d'Antigravity CLI.

    Retourne:
        dict contenant pid, status, last_activity, snippet, timestamp.
    """
    timestamp = time.time()
    pid: int | None = None
    cpu_percent = 0.0

    # 1. Recherche du processus 'agy' en cours
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            cmdline = proc.info.get("cmdline") or []
            cmd_str = " ".join(cmdline).lower()
            if "agy" in name.lower() or "agy" in cmd_str:
                pid = proc.info["pid"]
                try:
                    cpu_percent = proc.cpu_percent(interval=0.0)
                except Exception:
                    pass
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # 2. Trouver le fichier de log le plus récent dans ~/.gemini/antigravity-cli/log/ ou /tmp
    log_dirs = [
        os.path.expanduser("~/.gemini/antigravity-cli/log"),
        "/tmp",
    ]
    latest_log: str | None = None
    latest_mtime = 0.0

    for d in log_dirs:
        if os.path.exists(d) and os.path.isdir(d):
            # Cherche tous les fichiers .log ou dans /tmp des motifs agy
            pattern = os.path.join(d, "*.log")
            for log_path in glob.glob(pattern):
                try:
                    mtime = os.path.getmtime(log_path)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_log = log_path
                except OSError:
                    continue

    last_activity = latest_mtime if latest_mtime > 0 else 0.0
    age = timestamp - last_activity if last_activity > 0 else float("inf")

    # 3. Détermination du statut
    # ('reflecting', 'responded', 'idle', 'not_running')
    status = "not_running"
    if pid is not None:
        if cpu_percent > 1.0 or age < 15.0:
            status = "reflecting"
        elif age < 120.0:
            status = "responded"
        else:
            status = "idle"
    else:
        if age < 60.0:
            status = "idle"
        else:
            status = "not_running"

    # 4. Extraction du dernier prompt / réponse visible (snippet)
    snippet = ""
    if latest_log and os.path.exists(latest_log):
        try:
            with open(latest_log, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 3000), os.SEEK_SET)
                raw_chunk = f.read().decode("utf-8", errors="ignore")
                lines = [line.strip() for line in raw_chunk.splitlines() if line.strip()]
                # Garde les 10 dernières lignes significatives
                snippet = " | ".join(lines[-10:]) if lines else ""
        except Exception:
            pass

    return {
        "pid": pid,
        "status": status,
        "last_activity": last_activity,
        "snippet": snippet,
        "timestamp": timestamp,
    }
