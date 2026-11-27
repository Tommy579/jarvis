"""Sauvegardes coherentes de la base memoire (API de backup SQLite).

Une simple copie de fichier est incorrecte en mode WAL : la sauvegarde doit passer par
`sqlite3.Connection.backup`, qui produit un instantane coherent meme si le service ecrit.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BACKUP_PREFIX = "jarvis-"
BACKUP_SUFFIX = ".db"
PRE_RESTORE_PREFIX = "pre-restore-"
DEFAULT_KEEP = 7
DEFAULT_BACKUP_DIRNAME = "backups"


class BackupError(RuntimeError):
    """Sauvegarde ou restauration impossible (base absente, copie invalide...)."""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    size_bytes: int
    created_at: str


def backup_keep() -> int:
    """Nombre de sauvegardes conservees (JARVIS_BACKUP_KEEP, defaut 7)."""
    raw = os.environ.get("JARVIS_BACKUP_KEEP", "").strip()
    if not raw:
        return DEFAULT_KEEP
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_KEEP


def backup_dir_for(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / DEFAULT_BACKUP_DIRNAME


def _timestamp(now: datetime | None = None) -> str:
    ref = now or datetime.now(timezone.utc)
    return ref.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _created_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def check_database_integrity(path: str | Path) -> None:
    """Verifie qu'un fichier est une base SQLite saine (PRAGMA quick_check)."""
    candidate = Path(path)
    if not candidate.is_file():
        raise BackupError(f"Base introuvable : {candidate}")
    conn: sqlite3.Connection | None = None
    try:
        # Connexion en lecture/ecriture : une base en mode WAL ne peut pas toujours
        # etre ouverte en lecture seule quand son fichier -shm n'existe pas.
        conn = sqlite3.connect(str(candidate), timeout=10.0)
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"Base SQLite illisible ({candidate}) : {exc}") from exc
    finally:
        if conn is not None:
            conn.close()
    if not row or str(row[0]).strip().lower() != "ok":
        detail = row[0] if row else "aucun resultat"
        raise BackupError(f"Base SQLite corrompue ({candidate}) : {detail}")


def _private_dir(path: Path) -> None:
    """Cree le dossier en 0700 et retire les droits groupe/autres s'il existe deja."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _create_private_file(path: Path) -> None:
    """Cree (ou tronque) un fichier en 0600 sans suivre de lien symbolique.

    La sauvegarde contient toute la memoire personnelle : elle ne doit jamais
    heriter de l'umask (0644 par defaut) ni ecrire a travers un lien pose a l'avance.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise BackupError(f"Creation du fichier impossible ({path}) : {exc}") from exc
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def backup_database(
    db_path: str | Path,
    backup_dir: str | Path,
    keep: int | None = None,
    now: datetime | None = None,
) -> Path:
    """Cree un instantane coherent de la base et purge les anciennes sauvegardes."""
    source_path = Path(db_path)
    if not source_path.is_file():
        raise BackupError(f"Base introuvable : {source_path}")
    target_dir = Path(backup_dir)
    _private_dir(target_dir)
    target = target_dir / f"{BACKUP_PREFIX}{_timestamp(now)}{BACKUP_SUFFIX}"
    _create_private_file(target)

    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(str(source_path), timeout=30.0)
        destination = sqlite3.connect(str(target))
        with destination:
            source.backup(destination)
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Echec de la sauvegarde : {exc}") from exc
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()

    try:
        check_database_integrity(target)
    except BackupError:
        target.unlink(missing_ok=True)
        raise
    prune_backups(target_dir, keep=backup_keep() if keep is None else keep, protect=target)
    return target


def list_backups(backup_dir: str | Path) -> list[BackupInfo]:
    """Liste les sauvegardes, de la plus recente a la plus ancienne."""
    directory = Path(backup_dir)
    if not directory.is_dir():
        return []
    items: list[BackupInfo] = []
    for path in directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        if not path.is_file():
            continue
        items.append(
            BackupInfo(path=path, size_bytes=path.stat().st_size, created_at=_created_at(path))
        )
    items.sort(key=lambda item: (item.created_at, item.path.name), reverse=True)
    return items


def latest_backup(backup_dir: str | Path) -> BackupInfo | None:
    items = list_backups(backup_dir)
    return items[0] if items else None


def prune_backups(
    backup_dir: str | Path,
    keep: int = DEFAULT_KEEP,
    protect: str | Path | None = None,
) -> list[Path]:
    """Supprime les sauvegardes au-dela de `keep`, en epargnant `protect`."""
    keep = max(1, int(keep))
    protected = Path(protect).resolve() if protect is not None else None
    removed: list[Path] = []
    for info in list_backups(backup_dir)[keep:]:
        if protected is not None and info.path.resolve() == protected:
            continue
        try:
            info.path.unlink()
        except OSError:
            continue
        removed.append(info.path)
    return removed


def _copy_database(source: Path, target: Path) -> None:
    """Copie coherente d'une base, y compris les transactions encore dans le WAL."""
    _create_private_file(target)
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    try:
        src = sqlite3.connect(str(source), timeout=30.0)
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()


def _prune_pre_restore(backup_dir: Path, keep: int) -> None:
    """Retention des copies de securite pre-restauration (sinon elles s'accumulent)."""
    copies = sorted(
        (p for p in backup_dir.glob(f"{PRE_RESTORE_PREFIX}*{BACKUP_SUFFIX}") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in copies[max(1, keep) :]:
        try:
            old.unlink()
        except OSError:
            continue


def restore_database(
    backup_path: str | Path,
    db_path: str | Path,
    backup_dir: str | Path,
    now: datetime | None = None,
) -> Path | None:
    """Restaure une sauvegarde apres avoir mis la base courante de cote.

    Le service doit etre arrete : l'appelant est responsable de cette precondition.
    Retourne le chemin de la copie de securite de l'ancienne base (ou None).
    """
    source = Path(backup_path)
    target = Path(db_path)
    # Un lien symbolique ferait lire ou ecrire hors du dossier de donnees : refus net.
    if source.is_symlink():
        raise BackupError(f"Sauvegarde refusee (lien symbolique) : {source}")
    if target.is_symlink():
        raise BackupError(f"Base cible refusee (lien symbolique) : {target}")
    if not source.is_file():
        raise BackupError(f"Sauvegarde introuvable : {source}")
    check_database_integrity(source)

    safety_dir = Path(backup_dir)
    _private_dir(safety_dir)
    safety: Path | None = None
    if target.exists():
        safety = safety_dir / f"{PRE_RESTORE_PREFIX}{_timestamp(now)}{BACKUP_SUFFIX}"
        # Copie via l'API SQLite : une simple copie de fichier perdrait le WAL.
        _copy_database(target, safety)
        check_database_integrity(safety)

    # Copie dans un fichier temporaire 0600 du meme dossier puis remplacement atomique :
    # la base n'est jamais a moitie ecrite et ne reprend pas les droits du fichier source.
    staging = target.with_name(f".{target.name}.restore-tmp")
    staging.unlink(missing_ok=True)
    try:
        _copy_database(source, staging)
        check_database_integrity(staging)
    except (sqlite3.Error, BackupError) as exc:
        staging.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"Echec de la restauration : {exc}") from exc
    for suffix in ("-wal", "-shm"):
        staging.with_name(staging.name + suffix).unlink(missing_ok=True)

    # Les journaux WAL de l'ancienne base ne doivent pas survivre a la restauration.
    for suffix in ("-wal", "-shm"):
        side = target.with_name(target.name + suffix)
        if side.exists() or side.is_symlink():
            side.unlink()
    os.replace(staging, target)
    _prune_pre_restore(safety_dir, backup_keep())
    return safety
