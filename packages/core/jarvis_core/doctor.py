"""Diagnostic `jarvis doctor` : verifie l'environnement avant de declarer l'appli prete."""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backup import BackupError, backup_dir_for, check_database_integrity, latest_backup

STATUS_ORDER = {"ok": 0, "warn": 1, "fail": 2}
LLM_KEY_ENV = ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "OLLAMA_BASE_URL")
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # ok | warn | fail
    detail: str


def _permissions_are_private(path: Path) -> bool:
    mode = path.stat().st_mode
    return stat.S_IMODE(mode) & 0o077 == 0


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def is_service_running(port: int = DEFAULT_PORT) -> bool:
    """Vrai si jarvis-core accepte une connexion sur 127.0.0.1:<port>."""
    return _port_in_use(port)


def _database_check(db_path: Path) -> CheckResult:
    if not db_path.is_file():
        return CheckResult(
            "base memoire",
            "warn",
            f"{db_path.name} absent : elle sera creee au premier demarrage.",
        )
    try:
        check_database_integrity(db_path)
    except BackupError as exc:
        return CheckResult("base memoire", "fail", str(exc))
    try:
        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error as exc:
        return CheckResult("base memoire", "fail", f"Lecture du schema impossible : {exc}")
    if version <= 0:
        return CheckResult("base memoire", "warn", "Base presente mais sans migration appliquee.")
    return CheckResult("base memoire", "ok", f"Intege, schema v{version}.")


def _backup_check(data_dir: Path) -> CheckResult:
    latest = latest_backup(backup_dir_for(data_dir))
    if latest is None:
        return CheckResult(
            "sauvegardes",
            "warn",
            "Aucune sauvegarde : lance 'jarvis backup now'.",
        )
    try:
        created = datetime.fromisoformat(latest.created_at)
    except ValueError:
        return CheckResult("sauvegardes", "warn", f"Sauvegarde illisible : {latest.path.name}")
    age_hours = (
        datetime.now(timezone.utc) - created.astimezone(timezone.utc)
    ).total_seconds() / 3600
    if age_hours > 48:
        return CheckResult(
            "sauvegardes",
            "warn",
            f"Derniere sauvegarde il y a {age_hours:.0f} h ({latest.path.name}).",
        )
    return CheckResult(
        "sauvegardes",
        "ok",
        f"{latest.path.name}, il y a {age_hours:.1f} h.",
    )


def _disk_check(data_dir: Path) -> CheckResult:
    probe = data_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    free_gb = usage.free / (1024**3)
    if free_gb < 0.5:
        return CheckResult("espace disque", "fail", f"{free_gb:.1f} Go libres : insuffisant.")
    if free_gb < 2:
        return CheckResult("espace disque", "warn", f"{free_gb:.1f} Go libres.")
    return CheckResult("espace disque", "ok", f"{free_gb:.1f} Go libres.")


def collect_checks(
    data_dir: str | Path,
    base_root: str | Path,
    env_file: str | Path | None = None,
) -> list[CheckResult]:
    """Execute tous les controles et renvoie leurs resultats, sans effet de bord."""
    data_path = Path(data_dir).expanduser()
    root = Path(base_root).expanduser()
    env_path = Path(env_file).expanduser() if env_file is not None else root / ".env"
    checks: list[CheckResult] = []

    version = sys.version_info
    if version >= (3, 12):
        checks.append(
            CheckResult("python", "ok", f"{version.major}.{version.minor}.{version.micro}")
        )
    else:
        checks.append(
            CheckResult(
                "python",
                "fail",
                f"{version.major}.{version.minor} detecte, 3.12+ requis.",
            )
        )

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        checks.append(CheckResult("environnement", "ok", f"venv {sys.prefix}"))
    else:
        checks.append(
            CheckResult(
                "environnement", "warn", "Interpreteur hors venv : utilise .venv/bin/python."
            )
        )

    if env_path.is_file():
        if _permissions_are_private(env_path):
            checks.append(CheckResult("fichier .env", "ok", "present, permissions 600."))
        else:
            mode = stat.S_IMODE(env_path.stat().st_mode)
            checks.append(
                CheckResult(
                    "fichier .env",
                    "warn",
                    f"permissions {mode:o} : les cles sont lisibles par d'autres (chmod 600).",
                )
            )
    else:
        checks.append(CheckResult("fichier .env", "warn", f"{env_path} absent."))

    configured = [name for name in LLM_KEY_ENV if os.environ.get(name, "").strip()]
    if configured:
        checks.append(CheckResult("cle LLM", "ok", ", ".join(configured)))
    else:
        checks.append(
            CheckResult("cle LLM", "warn", "aucune cle : JARVIS demarre en mode hors-ligne.")
        )

    try:
        data_path.mkdir(parents=True, exist_ok=True)
        probe = data_path / ".jarvis-doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(CheckResult("donnees", "ok", f"{data_path} accessible en ecriture."))
    except OSError as exc:
        checks.append(CheckResult("donnees", "fail", f"{data_path} non accessible : {exc}"))

    checks.append(_database_check(data_path / "jarvis.db"))
    checks.append(_backup_check(data_path))

    ui_index = root / "web" / "dist" / "index.html"
    if ui_index.is_file():
        checks.append(CheckResult("interface web", "ok", "web/dist compile."))
    else:
        checks.append(
            CheckResult("interface web", "warn", "web/dist absent : 'make install' pour compiler.")
        )

    if _port_in_use(DEFAULT_PORT):
        checks.append(
            CheckResult("port 8765", "ok", f"service actif sur 127.0.0.1:{DEFAULT_PORT}.")
        )
    else:
        checks.append(CheckResult("port 8765", "ok", "libre : le service n'est pas demarre."))

    checks.append(_disk_check(data_path))
    return checks


def worst_status(checks: list[CheckResult]) -> str:
    return max(
        (check.status for check in checks), key=lambda s: STATUS_ORDER.get(s, 0), default="ok"
    )


def format_report(checks: list[CheckResult]) -> str:
    width = max((len(check.name) for check in checks), default=0)
    lines = []
    for check in checks:
        marker = {"ok": "OK", "warn": "ATTENTION", "fail": "ECHEC"}.get(check.status, "?")
        lines.append(f"[{marker:<9}] {check.name.ljust(width)} : {check.detail}")
    summary = worst_status(checks)
    lines.append("")
    lines.append(
        {
            "ok": "Diagnostic complet : aucun probleme detecte.",
            "warn": "Diagnostic termine avec des avertissements (l'appli peut demarrer).",
            "fail": "Diagnostic en echec : corrige les lignes ECHEC avant de demarrer.",
        }[summary]
    )
    return "\n".join(lines)
