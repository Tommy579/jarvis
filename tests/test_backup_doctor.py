"""Tests des sauvegardes, du diagnostic et des endpoints de version."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jarvis_core.api.server import JarvisCoreState, create_app
from jarvis_core.backup import (
    BackupError,
    backup_database,
    check_database_integrity,
    list_backups,
    restore_database,
)
from jarvis_core.doctor import collect_checks, worst_status
from jarvis_core.scheduler.routines import RoutineScheduler
from jarvis_core.updates import is_newer
from jarvis_shared.version import app_version


def _create_database(path: Path, value: str = "bonjour") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS note (id INTEGER PRIMARY KEY, texte TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM note")
        conn.execute("INSERT INTO note (texte) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _note(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT texte FROM note").fetchone()
    finally:
        conn.close()
    return str(row[0])


def test_backup_snapshot_is_coherent_with_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    _create_database(db_path)
    target = backup_database(db_path, tmp_path / "backups")
    assert target.is_file()
    assert _note(target) == "bonjour"
    check_database_integrity(target)


def test_backup_prunes_old_files_and_keeps_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    _create_database(db_path)
    backup_dir = tmp_path / "backups"
    start = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    for index in range(5):
        backup_database(db_path, backup_dir, keep=3, now=start + timedelta(days=index))
    items = list_backups(backup_dir)
    assert len(items) == 3
    assert items[0].created_at > items[-1].created_at


def test_restore_replaces_database_and_keeps_safety_copy(tmp_path: Path) -> None:
    db_path = tmp_path / "jarvis.db"
    backup_dir = tmp_path / "backups"
    _create_database(db_path, "ancien")
    target = backup_database(db_path, backup_dir)

    _create_database(db_path, "nouveau")
    assert _note(db_path) == "nouveau"

    safety = restore_database(target, db_path, backup_dir)
    assert safety is not None and safety.is_file()
    assert _note(db_path) == "ancien"
    assert _note(safety) == "nouveau"


def test_backup_rejects_corrupted_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"ceci n'est pas une base sqlite" * 10)
    with pytest.raises(BackupError):
        check_database_integrity(broken)


def test_doctor_flags_public_env_permissions(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=test\n", encoding="utf-8")
    os.chmod(env_file, 0o644)
    checks = collect_checks(data_dir, tmp_path, env_file)
    env_check = next(check for check in checks if check.name == "fichier .env")
    assert env_check.status == "warn"

    os.chmod(env_file, 0o600)
    checks = collect_checks(data_dir, tmp_path, env_file)
    env_check = next(check for check in checks if check.name == "fichier .env")
    assert env_check.status == "ok"


def test_doctor_detects_corrupted_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "jarvis.db").write_bytes(b"pas une base" * 20)
    checks = collect_checks(data_dir, tmp_path)
    db_check = next(check for check in checks if check.name == "base memoire")
    assert db_check.status == "fail"
    assert worst_status(checks) == "fail"


def test_scheduler_registers_daily_backup() -> None:
    async def noop() -> None:
        return None

    scheduler = RoutineScheduler()
    scheduler.configure_defaults(
        morning_briefing=noop,
        pre_meeting_check=noop,
        midday_check=None,
        evening_review=noop,
        nightly_consolidation=noop,
        daily_backup=noop,
    )
    assert "daily_backup" in scheduler.registered_jobs
    assert "nightly_consolidation" in scheduler.registered_jobs


def test_version_endpoint_exposes_version_and_schema(tmp_path: Path) -> None:
    state = JarvisCoreState(data_dir=tmp_path)
    with TestClient(create_app(state)) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["version"] == app_version()

        response = client.get("/api/version")
        assert response.status_code == 200
        payload = response.json()
        assert payload["version"] == app_version()
        assert payload["schema_version"] >= 1
        assert payload["uptime_seconds"] >= 0
        assert payload["data_dir"] == str(tmp_path)


def test_backup_endpoints_create_and_list(tmp_path: Path) -> None:
    state = JarvisCoreState(data_dir=tmp_path)
    with TestClient(create_app(state)) as client:
        empty = client.get("/api/backups")
        assert empty.status_code == 200
        assert empty.json()["backups"] == []

        created = client.post("/api/backups")
        assert created.status_code == 200
        payload = created.json()
        assert len(payload["backups"]) == 1
        assert payload["last_backup_at"] is not None
        assert (tmp_path / "backups" / payload["backups"][0]["name"]).is_file()


def test_version_comparison() -> None:
    assert is_newer("2.1.0", "2.0.0")
    assert is_newer("v2.0.1", "2.0.0")
    assert not is_newer("2.0.0", "2.0.0")
    assert not is_newer("1.9.9", "2.0.0")
    assert not is_newer("", "2.0.0")
