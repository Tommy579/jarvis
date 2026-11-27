"""Tests de securite des donnees : permissions, sauvegardes, chiffrement, import legacy."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import types
from pathlib import Path

import jarvis_core.memory.import_legacy as import_legacy
import pytest
from jarvis_core.backup import BackupError, backup_database, restore_database
from jarvis_core.doctor import collect_checks
from jarvis_core.memory.import_legacy import import_legacy_profile
from jarvis_core.memory.schema import MemoryDatabase
from jarvis_core.tools.standard import is_sensitive_read_path, is_sensitive_system_path

posix_only = pytest.mark.skipif(os.name == "nt", reason="permissions POSIX")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _make_db(path: Path, value: str = "bonjour") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS note (texte TEXT)")
        conn.execute("DELETE FROM note")
        conn.execute("INSERT INTO note VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_note(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return str(conn.execute("SELECT texte FROM note").fetchone()[0])
    finally:
        conn.close()


@pytest.fixture
def loose_umask():
    """Simule l'umask par defaut (022) : sans correctif, tout sort en 0644."""
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


# --- O-01 : base memoire et journaux WAL --------------------------------------


@posix_only
def test_new_database_and_wal_files_are_private(tmp_path: Path, loose_umask) -> None:
    data_dir = tmp_path / "donnees"
    db = MemoryDatabase(data_dir / "jarvis.db")
    with db._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        # Le WAL et le SHM existent tant que la connexion est ouverte.
        for suffix in ("", "-wal", "-shm"):
            side = db.db_path.with_name(db.db_path.name + suffix)
            if side.exists():
                assert _mode(side) & 0o077 == 0, f"{side.name} lisible par d'autres"
    assert _mode(data_dir) == 0o700


@posix_only
def test_existing_world_readable_database_is_tightened(tmp_path: Path, loose_umask) -> None:
    db_path = tmp_path / "jarvis.db"
    _make_db(db_path)
    os.chmod(db_path, 0o644)
    MemoryDatabase(db_path)
    assert _mode(db_path) == 0o600


# --- O-02 : chiffrement en echec ferme ----------------------------------------


def test_encryption_key_without_sqlcipher_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force l'absence de sqlcipher3 meme s'il est installe.
    monkeypatch.setitem(sys.modules, "sqlcipher3", None)
    db_path = tmp_path / "chiffree.db"
    with pytest.raises(RuntimeError, match="sqlcipher3"):
        MemoryDatabase(db_path, encryption_key="cle-de-test")
    # Aucune table creee en clair.
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            tables = conn.execute("SELECT name FROM sqlite_master").fetchall()
        finally:
            conn.close()
        assert tables == []


def test_encryption_key_uses_sqlcipher_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_connect(path: str, timeout: float = 10.0) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=timeout)
        real_execute = conn.execute

        class _Proxy:
            def __getattr__(self, name: str):
                return getattr(conn, name)

            def __setattr__(self, name: str, value) -> None:
                setattr(conn, name, value)

            def execute(self, sql: str, *args):
                if sql.startswith("PRAGMA key"):
                    calls.append(sql)
                    return None
                return real_execute(sql, *args)

        return _Proxy()  # type: ignore[return-value]

    monkeypatch.setitem(sys.modules, "sqlcipher3", types.SimpleNamespace(connect=fake_connect))
    MemoryDatabase(tmp_path / "chiffree.db", encryption_key="l'apostrophe")
    assert calls and calls[0] == "PRAGMA key = 'l''apostrophe'"


# --- O-03 / O-04 : sauvegardes et restauration --------------------------------


@posix_only
def test_backup_file_and_directory_are_private(tmp_path: Path, loose_umask) -> None:
    db_path = tmp_path / "jarvis.db"
    _make_db(db_path)
    backups = tmp_path / "backups"
    target = backup_database(db_path, backups, keep=3)
    assert _mode(target) == 0o600
    assert _mode(backups) == 0o700


@posix_only
def test_backup_does_not_follow_planted_symlink(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    db_path = tmp_path / "jarvis.db"
    _make_db(db_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    victim = tmp_path / "victime.txt"
    victim.write_text("intact", encoding="utf-8")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    (backups / "jarvis-20260101T000000Z.db").symlink_to(victim)
    with pytest.raises(BackupError):
        backup_database(db_path, backups, now=now)
    assert victim.read_text(encoding="utf-8") == "intact"


@posix_only
def test_restore_refuses_symlinked_target(tmp_path: Path) -> None:
    backup = tmp_path / "sauvegarde.db"
    _make_db(backup, "restaure")
    outside = tmp_path / "hors-donnees.db"
    _make_db(outside, "original")
    data = tmp_path / "data"
    data.mkdir()
    link = data / "jarvis.db"
    link.symlink_to(outside)
    with pytest.raises(BackupError, match="lien symbolique"):
        restore_database(backup, link, data / "backups")
    assert _read_note(outside) == "original"


@posix_only
def test_restore_refuses_symlinked_source(tmp_path: Path) -> None:
    real = tmp_path / "vraie.db"
    _make_db(real)
    link = tmp_path / "lien.db"
    link.symlink_to(real)
    with pytest.raises(BackupError, match="lien symbolique"):
        restore_database(link, tmp_path / "jarvis.db", tmp_path / "backups")


@posix_only
def test_restore_result_is_private_even_if_source_is_world_readable(
    tmp_path: Path, loose_umask
) -> None:
    backup = tmp_path / "sauvegarde.db"
    _make_db(backup, "restaure")
    os.chmod(backup, 0o644)
    db_path = tmp_path / "jarvis.db"
    _make_db(db_path, "ancienne")
    safety = restore_database(backup, db_path, tmp_path / "backups")
    assert _read_note(db_path) == "restaure"
    assert _mode(db_path) == 0o600
    assert safety is not None and _mode(safety) == 0o600
    assert not list(tmp_path.glob(".jarvis.db.restore-tmp*"))


def test_pre_restore_copies_are_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("JARVIS_BACKUP_KEEP", "2")
    backup = tmp_path / "sauvegarde.db"
    _make_db(backup)
    db_path = tmp_path / "jarvis.db"
    backups = tmp_path / "backups"
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(4):
        _make_db(db_path, f"v{i}")
        restore_database(backup, db_path, backups, now=base + timedelta(minutes=i))
    assert len(list(backups.glob("pre-restore-*.db"))) == 2


# --- O-05 : import d'un profil legacy hostile ---------------------------------


def test_hostile_profile_types_do_not_crash_startup(tmp_path: Path) -> None:
    profile = tmp_path / "jarvis_profile.json"
    profile.write_text(
        json.dumps(
            {
                "identity": ["pas", "un", "objet"],
                "work": "chaine",
                "preferences": {"x": {"imbrique": True}},
                "notes": "pas une liste",
            }
        ),
        encoding="utf-8",
    )
    db = MemoryDatabase(tmp_path / "jarvis.db")
    # Avant correctif : AttributeError sur identity.get / work.get au demarrage.
    assert import_legacy_profile(profile, db, tmp_path / "wiki") == 0
    assert (tmp_path / "wiki" / "moi.md").is_file()


def test_hostile_profile_sizes_are_bounded(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "jarvis.db")
    max_bytes = getattr(import_legacy, "MAX_PROFILE_BYTES", 1_048_576)
    big = tmp_path / "gros.json"
    big.write_text('{"notes": [' + " " * (max_bytes + 1) + "]}", encoding="utf-8")
    assert import_legacy_profile(big, db) == 0

    profile = tmp_path / "jarvis_profile.json"
    profile.write_text(
        json.dumps(
            {
                "habits": {"k" * 10_000: "v" * 100_000},
                "notes": [{"text": f"note {i}"} for i in range(5_000)],
            }
        ),
        encoding="utf-8",
    )
    inserted = import_legacy_profile(profile, db)
    assert 0 < inserted <= 500
    facts = db.list_facts(include_superseded=True, limit=5000)
    assert max(len(f.predicate) for f in facts) <= 64
    assert max(len(f.object) for f in facts) <= 2000


def test_invalid_json_profile_is_ignored(tmp_path: Path) -> None:
    profile = tmp_path / "jarvis_profile.json"
    profile.write_text("{pas du json", encoding="utf-8")
    db = MemoryDatabase(tmp_path / "jarvis.db")
    assert import_legacy_profile(profile, db) == 0


@posix_only
def test_wiki_pages_are_private_and_symlinks_not_followed(tmp_path: Path, loose_umask) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    victim = tmp_path / "victime.txt"
    victim.write_text("intact", encoding="utf-8")
    (wiki / "moi.md").symlink_to(victim)
    db = MemoryDatabase(tmp_path / "jarvis.db")
    import_legacy_profile(tmp_path / "absent.json", db, wiki)
    assert victim.read_text(encoding="utf-8") == "intact"
    assert _mode(wiki / "rules.md") == 0o600


# --- O-06 : doctor signale les permissions ------------------------------------


@posix_only
def test_doctor_warns_on_world_readable_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    os.chmod(data, 0o755)
    checks = {c.name: c for c in collect_checks(data, tmp_path, tmp_path / ".env")}
    assert checks["permissions donnees"].status == "warn"
    os.chmod(data, 0o700)
    checks = {c.name: c for c in collect_checks(data, tmp_path, tmp_path / ".env")}
    assert checks["permissions donnees"].status == "ok"


# --- O-07 : outils fichiers et dossiers prives de Jarvis -----------------------


def test_file_tools_block_custom_data_dir_and_launcher_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "memoire-perso"
    state = tmp_path / "state"
    config = tmp_path / "config"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    for target in (
        data / "jarvis.db",
        data / "backups" / "jarvis-1.db",
        state / "jarvis" / "jarvis-core.log",
        config / "chromium-jarvis" / "Default" / "Cookies",
    ):
        assert is_sensitive_read_path(target), target
        assert is_sensitive_system_path(target), target
    assert not is_sensitive_read_path(tmp_path / "projet" / "notes.md")


def test_expired_approval_cannot_be_approved_in_database(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    db = MemoryDatabase(tmp_path / "jarvis.db")
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    appr = db.create_approval("run_command", "test", {"command": "ls"}, 3, expired_at)
    result = db.decide_approval(appr.id, True)
    assert result is not None
    assert result.status == "expired"
