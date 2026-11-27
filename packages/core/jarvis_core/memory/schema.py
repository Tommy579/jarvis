"""Schema SQL, migrations SQLite (+ sqlite-vec optionnel) et operations memoire."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import struct
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis_shared.rest_models import (
    AlertRecord,
    ApprovalItem,
    AutonomousTaskRecord,
    AutonomyAuditEntry,
    ChatMessage,
    DailyUsageItem,
    FactCreate,
    FactRecord,
    FactUpdate,
    GoalEventRecord,
    GoalRecord,
    MemoryExport,
    PersonRecord,
    SensitivityLevel,
    SubagentRunRecord,
    SubtaskRecord,
    ToolCallRecord,
    WikiPage,
)

from .embeddings import EmbeddingEngine

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _deterministic_text_embedding(text: str, dims: int = 128) -> list[float]:
    """Produit un vecteur normalise local (128 dims, aligne avec EmbeddingEngine)."""
    return EmbeddingEngine._deterministic_ngram_embed(text, dim=dims)


MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL DEFAULT 'Tom',
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            text TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            confidence REAL NOT NULL DEFAULT 0.9,
            source TEXT NOT NULL DEFAULT 'conversation',
            sensitivity TEXT NOT NULL DEFAULT 'normal',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT,
            superseded_by TEXT,
            embedding BLOB
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            channel TEXT NOT NULL,
            summary TEXT NOT NULL,
            raw_ref TEXT,
            embedding BLOB
        );

        CREATE TABLE IF NOT EXISTS people (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            relation TEXT NOT NULL,
            birthday TEXT,
            contact_ref TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            urgency INTEGER NOT NULL DEFAULT 0,
            handled INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            channel TEXT NOT NULL,
            urgency INTEGER NOT NULL,
            message TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'sent',
            acked_at TEXT,
            too_noisy INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS llm_calls (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            model TEXT NOT NULL,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0.0,
            latency_ms REAL NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            usage_json TEXT,
            tools_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            tool_name TEXT NOT NULL,
            description TEXT NOT NULL,
            args_json TEXT NOT NULL DEFAULT '{}',
            risk INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS onboarding_answers (
            module_id TEXT NOT NULL,
            question_id TEXT NOT NULL,
            field_key TEXT NOT NULL,
            answer TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (module_id, question_id)
        );

        CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
        CREATE INDEX IF NOT EXISTS idx_facts_sensitivity ON facts(sensitivity);
        CREATE INDEX IF NOT EXISTS idx_facts_created_at ON facts(created_at);
        CREATE INDEX IF NOT EXISTS idx_facts_superseded ON facts(superseded_by);
        CREATE INDEX IF NOT EXISTS idx_episodes_ts ON episodes(ts);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_handled ON events(handled);
        CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(ts);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
        CREATE INDEX IF NOT EXISTS idx_chat_messages_ts ON chat_messages(created_at);
        """,
    ),
    (
        2,
        """
        ALTER TABLE facts ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE facts ADD COLUMN embedding_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE episodes ADD COLUMN embedding_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE people ADD COLUMN is_vip INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE notifications ADD COLUMN dedup_key TEXT;
        ALTER TABLE events ADD COLUMN dedup_key TEXT;
        ALTER TABLE llm_calls ADD COLUMN provider TEXT NOT NULL DEFAULT '';
        ALTER TABLE llm_calls ADD COLUMN cached_tokens INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        3,
        """
        ALTER TABLE approvals ADD COLUMN delivered_channels TEXT NOT NULL DEFAULT '';
        ALTER TABLE approvals ADD COLUMN answered_via TEXT;

        CREATE TABLE IF NOT EXISTS notification_orders (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_glob TEXT NOT NULL DEFAULT '*',
            event_type_glob TEXT NOT NULL DEFAULT '*',
            filter_json TEXT NOT NULL DEFAULT '{}',
            text_match TEXT,
            min_urgency INTEGER NOT NULL DEFAULT 1,
            channels_json TEXT NOT NULL DEFAULT '[]',
            message_template TEXT NOT NULL DEFAULT '{summary}',
            expires_at TEXT,
            max_fires INTEGER,
            fired_count INTEGER NOT NULL DEFAULT 0,
            last_fired_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL DEFAULT 'user',
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_orders_active ON notification_orders(active);

        CREATE TABLE IF NOT EXISTS notification_attempts (
            id TEXT PRIMARY KEY,
            notification_id TEXT NOT NULL,
            approval_id TEXT,
            order_id TEXT,
            channel TEXT NOT NULL,
            target TEXT NOT NULL,
            ts TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            external_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_attempts_notif ON notification_attempts(notification_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_approval ON notification_attempts(approval_id);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS autonomous_tasks (
            id TEXT PRIMARY KEY,
            goal TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            iterations INTEGER NOT NULL DEFAULT 0,
            max_iterations INTEGER NOT NULL DEFAULT 30,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            current_step TEXT NOT NULL DEFAULT '',
            result_summary TEXT NOT NULL DEFAULT '',
            error TEXT,
            notify_on_finish INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_auto_tasks_status ON autonomous_tasks(status, created_at);

        CREATE TABLE IF NOT EXISTS autonomy_audit (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            task_id TEXT,
            tool_name TEXT NOT NULL,
            risk INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_auto_audit_ts ON autonomy_audit(ts DESC);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planning',
            success_criteria_json TEXT NOT NULL DEFAULT '[]',
            verified_criteria_json TEXT NOT NULL DEFAULT '[]',
            orchestrator_model TEXT NOT NULL DEFAULT 'gemini-4-argon',
            allowed_models_json TEXT NOT NULL DEFAULT '[]',
            tokens_used INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            max_cost_usd REAL NOT NULL DEFAULT 1.5,
            progress_pct INTEGER NOT NULL DEFAULT 0,
            current_phase TEXT NOT NULL DEFAULT 'Planification',
            final_summary TEXT NOT NULL DEFAULT '',
            pause_reason TEXT,
            notify_on_finish INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_goals_status_created ON goals(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS goal_subtasks (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            instructions TEXT NOT NULL DEFAULT '',
            role_profile TEXT NOT NULL DEFAULT 'general',
            assigned_model TEXT NOT NULL DEFAULT 'deepseek/deepseek-v4.1-flash-1017',
            depends_on_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            attempt INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            result TEXT NOT NULL DEFAULT '',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_goal_subtasks_goal ON goal_subtasks(goal_id, created_at);

        CREATE TABLE IF NOT EXISTS subagent_runs (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            subtask_id TEXT NOT NULL,
            model TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            role_profile TEXT NOT NULL DEFAULT 'general',
            status TEXT NOT NULL DEFAULT 'running',
            iterations INTEGER NOT NULL DEFAULT 0,
            max_iterations INTEGER NOT NULL DEFAULT 20,
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            last_tool TEXT NOT NULL DEFAULT '',
            last_heartbeat_at TEXT NOT NULL DEFAULT '',
            error TEXT,
            result_summary TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_subagent_runs_goal ON subagent_runs(goal_id, started_at);

        CREATE TABLE IF NOT EXISTS goal_events (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            subtask_id TEXT,
            run_id TEXT,
            message TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_goal_events_goal_ts ON goal_events(goal_id, ts DESC);
        """,
    ),
]


class MemoryDatabase:
    """Gestionnaire SQLite chiffre (optionnel) et migrations JARVIS 2.0."""

    def __init__(self, db_path: str | Path, encryption_key: str | None = None) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.parent.exists():
            # Dossier de donnees cree par Jarvis : reserve au proprietaire.
            self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.encryption_key = encryption_key
        self.vec_enabled = False
        self._vec_checked = False
        self._wal_initialized = False
        self._precreate_private_db()
        self.migrate()
        self.secure_file_permissions()

    def _precreate_private_db(self) -> None:
        """Cree la base vide en 0600 : SQLite reprend ces droits pour -wal et -shm."""
        if self.db_path.exists() or self.db_path.is_symlink():
            return
        try:
            fd = os.open(
                str(self.db_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(fd)
        except OSError:
            # Course avec un autre processus ou dossier en lecture seule : SQLite tranchera.
            pass

    def secure_file_permissions(self) -> None:
        """Retire les droits groupe/autres sur la base et ses journaux (installations existantes)."""
        if os.name == "nt":
            return
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path.with_name(self.db_path.name + suffix)
            try:
                if path.is_file() and not path.is_symlink():
                    if path.stat().st_mode & 0o077:
                        os.chmod(path, 0o600)
            except OSError:
                continue

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn: sqlite3.Connection | None = None
        if self.encryption_key:
            try:
                import sqlcipher3  # type: ignore[import-not-found]
            except ImportError as exc:
                # Echec ferme : ouvrir en clair une base censee etre chiffree creerait ou
                # modifierait une base lisible par quiconque accede au fichier.
                raise RuntimeError(
                    "Cle de chiffrement SQLite definie mais sqlcipher3 absent : "
                    "ouverture refusee (installe sqlcipher3 ou retire la cle)."
                ) from exc
            conn = sqlcipher3.connect(str(self.db_path), timeout=10.0)
            escaped_key = self.encryption_key.replace("'", "''")
            conn.execute(f"PRAGMA key = '{escaped_key}'")
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            if not self._wal_initialized:
                conn.execute("PRAGMA journal_mode = WAL")
                self._wal_initialized = True
            conn.execute("PRAGMA foreign_keys = ON")
            if not self._vec_checked or self.vec_enabled:
                self._try_load_sqlite_vec(conn)
                self._vec_checked = True
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _try_load_sqlite_vec(self, conn: sqlite3.Connection) -> None:
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self.vec_enabled = True
        except Exception:
            self.vec_enabled = False

    @staticmethod
    def _split_sql_statements(sql_script: str) -> list[str]:
        statements: list[str] = []
        buf: list[str] = []
        for line in sql_script.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            buf.append(line)
            if stripped.endswith(";"):
                stmt = "\n".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
        tail = "\n".join(buf).strip()
        if tail:
            statements.append(tail)
        return statements

    def migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            applied = {int(r["version"]) for r in rows}
            for version, sql_script in MIGRATIONS:
                if version not in applied:
                    for stmt in self._split_sql_statements(sql_script):
                        try:
                            conn.execute(stmt)
                        except sqlite3.OperationalError as exc:
                            # Rendre les ALTER TABLE ADD COLUMN idempotents en cas de reprise
                            if "duplicate column name" in str(exc).lower():
                                continue
                            raise
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, utc_now_iso()),
                    )
                    conn.commit()
            if self.vec_enabled:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts USING vec0(fact_id TEXT PRIMARY KEY, embedding FLOAT[128])"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(episode_id TEXT PRIMARY KEY, embedding FLOAT[128])"
                )
                conn.commit()

    def table_names(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        return {str(r["name"]) for r in rows}

    @property
    def schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            return int(row["v"] or 0) if row else 0

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value_json"]) if row else default

    def set_setting(self, key: str, value_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (key, value_json, utc_now_iso()),
            )
            conn.commit()

    # === Facts ===

    def create_fact(self, payload: FactCreate, embedding: list[float] | None = None) -> FactRecord:
        now = utc_now_iso()
        fact_id = f"fact_{uuid.uuid4().hex[:12]}"
        vec = embedding or _deterministic_text_embedding(payload.text)
        blob = _pack_vector(vec)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO facts (
                    id, subject, predicate, object, text, category,
                    confidence, source, sensitivity, created_at, updated_at, embedding, embedding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_id,
                    payload.subject,
                    payload.predicate,
                    payload.object,
                    payload.text,
                    payload.category,
                    payload.confidence,
                    payload.source,
                    payload.sensitivity,
                    now,
                    now,
                    blob,
                    json.dumps(vec),
                ),
            )
            if self.vec_enabled:
                conn.execute(
                    "INSERT OR REPLACE INTO vec_facts (fact_id, embedding) VALUES (?, ?)",
                    (fact_id, json.dumps(vec)),
                )
            conn.commit()
        return self.get_fact(fact_id)  # type: ignore[return-value]

    def get_fact(self, fact_id: str) -> FactRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return self._row_to_fact(row) if row else None

    def list_facts(
        self,
        query: str | None = None,
        category: str | None = None,
        sensitivity: SensitivityLevel | None = None,
        include_superseded: bool = False,
        limit: int = 100,
    ) -> list[FactRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if sensitivity:
            sens_variants = {
                "prive": ("prive", "privé"),
                "privé": ("prive", "privé"),
                "tres_prive": ("tres_prive", "très privé"),
                "très privé": ("tres_prive", "très privé"),
            }.get(str(sensitivity), (str(sensitivity),))
            placeholders = ", ".join("?" for _ in sens_variants)
            clauses.append(f"sensitivity IN ({placeholders})")
            params.extend(sens_variants)
        if query:
            clauses.append("(LOWER(text) LIKE ? OR LOWER(subject) LIKE ? OR LOWER(object) LIKE ?)")
            like = f"%{query.lower()}%"
            params.extend([like, like, like])
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM facts {where_sql} ORDER BY updated_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def search_facts_hybrid(
        self, query: str, limit: int = 10, *, include_highly_private: bool = False
    ) -> list[FactRecord]:
        """Combine correspondance textuelle et similarite cosinus sur les embeddings locaux."""
        query_vec = _deterministic_text_embedding(query)
        q_tokens = {tok for tok in query.lower().split() if len(tok) > 1}
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE superseded_by IS NULL").fetchall()
        scored: list[tuple[float, FactRecord]] = []
        for row in rows:
            fact = self._row_to_fact(row)
            if not include_highly_private and str(fact.sensitivity) in {"très privé", "tres_prive"}:
                continue
            text_lower = f"{fact.subject} {fact.predicate} {fact.object} {fact.text}".lower()
            lexical_hits = sum(1 for tok in q_tokens if tok in text_lower)
            lexical_score = lexical_hits / max(1, len(q_tokens))
            vec_score = 0.0
            if row["embedding"]:
                vec_score = _cosine_similarity(query_vec, _unpack_vector(row["embedding"]))
            total = (0.65 * lexical_score) + (0.35 * vec_score)
            if lexical_hits > 0 or vec_score > 0.35:
                scored.append((total, fact))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def update_fact(self, fact_id: str, patch: FactUpdate) -> FactRecord | None:
        existing = self.get_fact(fact_id)
        if not existing:
            return None
        new_text = patch.text if patch.text is not None else existing.text
        new_cat = patch.category if patch.category is not None else existing.category
        new_conf = patch.confidence if patch.confidence is not None else existing.confidence
        new_sens = patch.sensitivity if patch.sensitivity is not None else existing.sensitivity
        new_sup = patch.superseded_by if patch.superseded_by is not None else existing.superseded_by
        now = utc_now_iso()
        vec = _deterministic_text_embedding(new_text)
        blob = _pack_vector(vec)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE facts
                SET text = ?, category = ?, confidence = ?, sensitivity = ?,
                    superseded_by = ?, updated_at = ?, embedding = ?, embedding_json = ?
                WHERE id = ?
                """,
                (
                    new_text,
                    new_cat,
                    new_conf,
                    new_sens,
                    new_sup,
                    now,
                    blob,
                    json.dumps(vec),
                    fact_id,
                ),
            )
            conn.commit()
        return self.get_fact(fact_id)

    def delete_fact(self, fact_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            conn.commit()
            return cur.rowcount > 0

    def weakest_facts(self, limit: int = 10) -> list[FactRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM facts
                WHERE superseded_by IS NULL
                ORDER BY confidence ASC, updated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_fact(r) for r in rows]

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> FactRecord:
        raw_sens = str(row["sensitivity"] or "normal")
        sens_map = {"prive": "privé", "tres_prive": "très privé"}
        sens_val = sens_map.get(raw_sens, raw_sens)
        return FactRecord(
            id=str(row["id"]),
            subject=str(row["subject"]),
            predicate=str(row["predicate"]),
            object=str(row["object"]),
            text=str(row["text"]),
            category=str(row["category"]),
            confidence=float(row["confidence"]),
            source=str(row["source"]),
            sensitivity=sens_val,  # type: ignore[arg-type]
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_used_at=row["last_used_at"],
            superseded_by=row["superseded_by"],
        )

    # === Episodes ===

    def add_episode(self, kind: str, channel: str, summary: str, raw_ref: str | None = None) -> str:
        ep_id = f"ep_{uuid.uuid4().hex[:12]}"
        ts = utc_now_iso()
        vec = _deterministic_text_embedding(summary)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO episodes (id, ts, kind, channel, summary, raw_ref, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ep_id, ts, kind, channel, summary, raw_ref, _pack_vector(vec)),
            )
            conn.commit()
        return ep_id

    # === People ===

    def upsert_person(
        self,
        name: str,
        relation: str,
        birthday: str | None = None,
        contact_ref: str | None = None,
        notes: str | None = None,
        person_id: str | None = None,
    ) -> PersonRecord:
        now = utc_now_iso()
        pid = person_id or f"pers_{uuid.uuid4().hex[:12]}"
        effective_notes = notes or ""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at, notes FROM people WHERE LOWER(name) = LOWER(?)", (name,)
            ).fetchone()
            if existing:
                pid = str(existing["id"])
                effective_notes = notes if notes is not None else str(existing["notes"] or "")
                conn.execute(
                    """
                    UPDATE people
                    SET relation = ?, birthday = COALESCE(?, birthday),
                        contact_ref = COALESCE(?, contact_ref), notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (relation, birthday, contact_ref, effective_notes, now, pid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO people (id, name, relation, birthday, contact_ref, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (pid, name, relation, birthday, contact_ref, effective_notes, now, now),
                )
            conn.commit()
        return PersonRecord(
            id=pid,
            name=name,
            relation=relation,
            birthday=birthday,
            contact_ref=contact_ref,
            notes=effective_notes,
        )

    def list_people(self) -> list[PersonRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM people ORDER BY name ASC").fetchall()
        return [
            PersonRecord(
                id=str(r["id"]),
                name=str(r["name"]),
                relation=str(r["relation"]),
                birthday=r["birthday"],
                contact_ref=r["contact_ref"],
                notes=str(r["notes"]),
            )
            for r in rows
        ]

    def delete_person(self, person_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
            conn.commit()
            return cur.rowcount > 0

    # === Events & Notifications ===

    def add_event(
        self, source: str, event_type: str, payload: dict[str, Any], urgency: int = 0
    ) -> str:
        evt_id = f"evt_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (id, ts, source, type, payload_json, urgency, handled)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (evt_id, utc_now_iso(), source, event_type, json.dumps(payload), urgency),
            )
            conn.commit()
        return evt_id

    def add_notification(
        self, channel: str, urgency: int, message: str, reason: str = ""
    ) -> AlertRecord:
        nid = f"alert_{uuid.uuid4().hex[:12]}"
        ts = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notifications (id, ts, channel, urgency, message, reason, status)
                VALUES (?, ?, ?, ?, ?, ?, 'sent')
                """,
                (nid, ts, channel, urgency, message, reason),
            )
            conn.commit()
        return AlertRecord(
            id=nid,
            ts=ts,
            channel=channel,
            urgency=urgency,  # type: ignore[arg-type]
            message=message,
            reason=reason,
            status="sent",
            acked_at=None,
        )

    def insert_notification(self, record: Any) -> None:
        channel = getattr(record, "channel", "")
        urgency = int(getattr(record, "urgency", 0))
        message = str(getattr(record, "message", ""))
        reason = str(getattr(record, "reason", ""))
        raw_status = str(getattr(record, "status", "sent"))
        status = raw_status if raw_status in {"sent", "acked", "too_noisy"} else "sent"
        dedup_key = getattr(record, "dedup_key", None)
        acked_at = getattr(record, "acked_at", None)
        nid = getattr(record, "id", None) or f"alert_{uuid.uuid4().hex[:12]}"
        ts = getattr(record, "ts", None) or utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notifications (id, ts, channel, urgency, message, reason, status, dedup_key, acked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    channel = excluded.channel,
                    urgency = excluded.urgency,
                    message = excluded.message,
                    reason = excluded.reason,
                    status = excluded.status,
                    dedup_key = COALESCE(excluded.dedup_key, notifications.dedup_key),
                    acked_at = COALESCE(excluded.acked_at, notifications.acked_at)
                """,
                (nid, ts, channel, urgency, message, reason, status, dedup_key, acked_at),
            )
            conn.commit()

    def list_alerts(self, limit: int = 50) -> list[AlertRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            AlertRecord(
                id=str(r["id"]),
                ts=str(r["ts"]),
                channel=str(r["channel"]),
                urgency=int(r["urgency"]),  # type: ignore[arg-type]
                message=str(r["message"]),
                reason=str(r["reason"]),
                status=r["status"] if r["status"] in {"sent", "acked", "too_noisy"} else "sent",
                acked_at=r["acked_at"],
            )
            for r in rows
        ]

    def ack_alert(self, alert_id: str, too_noisy: bool = False) -> AlertRecord | None:
        status = "too_noisy" if too_noisy else "acked"
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notifications
                SET status = ?, acked_at = ?, too_noisy = ?
                WHERE id = ?
                """,
                (status, now, 1 if too_noisy else 0, alert_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM notifications WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return None
        return AlertRecord(
            id=str(row["id"]),
            ts=str(row["ts"]),
            channel=str(row["channel"]),
            urgency=int(row["urgency"]),  # type: ignore[arg-type]
            message=str(row["message"]),
            reason=str(row["reason"]),
            status=row["status"],
            acked_at=row["acked_at"],
        )

    # === LLM Calls & Usage ===

    def log_llm_call(
        self,
        role: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost: float,
        latency_ms: float = 0.0,
    ) -> str:
        cid = f"llm_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls (id, ts, role, model, tokens_in, tokens_out, cost, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cid, utc_now_iso(), role, model, tokens_in, tokens_out, cost, latency_ms),
            )
            conn.commit()
        return cid

    def daily_usage(self, days: int = 14) -> list[DailyUsageItem]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT substr(ts, 1, 10) AS day, model,
                       COUNT(*) AS calls,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cost), 0.0) AS cost
                FROM llm_calls
                WHERE ts >= ?
                GROUP BY day, model
                ORDER BY day DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(days)) * 20),
            ).fetchall()
        return [
            DailyUsageItem(
                date=str(r["day"]),
                model=str(r["model"]),
                calls=int(r["calls"]),
                tokens_in=int(r["tokens_in"]),
                tokens_out=int(r["tokens_out"]),
                cost=float(r["cost"]),
            )
            for r in rows
        ]

    # === Chat Messages & Approvals ===

    def add_message(
        self,
        role: str,
        content: str,
        usage: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        message_id: str | None = None,
    ) -> ChatMessage:
        mid = message_id or f"msg_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO chat_messages (id, role, content, created_at, usage_json, tools_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    role,
                    content,
                    now,
                    json.dumps(usage) if usage else None,
                    json.dumps(tools or []),
                ),
            )
            conn.commit()
        return ChatMessage(
            id=mid,
            role=role,  # type: ignore[arg-type]
            content=content,
            created_at=now,
            usage=usage,  # type: ignore[arg-type]
            tools=[ToolCallRecord(**t) for t in (tools or [])],
        )

    def delete_message(self, message_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))
            conn.commit()
            return cursor.rowcount > 0

    def list_messages(self, limit: int = 100) -> list[ChatMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        items: list[ChatMessage] = []
        for r in reversed(rows):
            usage = json.loads(r["usage_json"]) if r["usage_json"] else None
            tools = [ToolCallRecord(**t) for t in json.loads(r["tools_json"] or "[]")]
            items.append(
                ChatMessage(
                    id=str(r["id"]),
                    role=r["role"],
                    content=str(r["content"]),
                    created_at=str(r["created_at"]),
                    usage=usage,
                    tools=tools,
                )
            )
        return items

    def clear_messages(self) -> int:
        """Supprime tout l'historique des messages de conversation."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM chat_messages")
            conn.commit()
            return cursor.rowcount

    def get_message_tools(self, message_id: str) -> list[ToolCallRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT tools_json FROM chat_messages WHERE id = ?", (message_id,)
            ).fetchone()
        if not row:
            return []
        return [ToolCallRecord(**t) for t in json.loads(row["tools_json"] or "[]")]

    def create_approval(
        self,
        tool_name: str,
        description: str,
        args: dict[str, Any],
        risk: int,
        expires_at: str,
        approval_id: str | None = None,
    ) -> ApprovalItem:
        aid = approval_id or f"appr_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals (id, tool_name, description, args_json, risk, created_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (aid, tool_name, description, json.dumps(args), risk, now, expires_at),
            )
            conn.commit()
        return ApprovalItem(
            id=aid,
            tool_name=tool_name,
            description=description,
            args=args,
            risk=risk,  # type: ignore[arg-type]
            created_at=now,
            expires_at=expires_at,
            status="pending",
        )

    def list_approvals(self, limit: int = 100) -> list[ApprovalItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            ApprovalItem(
                id=str(r["id"]),
                tool_name=str(r["tool_name"]),
                description=str(r["description"]),
                args=json.loads(r["args_json"] or "{}"),
                risk=int(r["risk"]),  # type: ignore[arg-type]
                created_at=str(r["created_at"]),
                expires_at=str(r["expires_at"]),
                status=r["status"],
            )
            for r in rows
        ]

    def decide_approval(self, approval_id: str, approved: bool) -> ApprovalItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if not row:
                return None
            if row["status"] == "pending":
                try:
                    exp = datetime.fromisoformat(str(row["expires_at"]))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    expired = datetime.now(timezone.utc) >= exp
                except ValueError:
                    expired = True
                # Une demande expiree ne peut plus devenir 'approved' en base.
                status = "expired" if expired else ("approved" if approved else "denied")
                conn.execute(
                    "UPDATE approvals SET status = ? WHERE id = ? AND status = 'pending'",
                    (status, approval_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM approvals WHERE id = ?", (approval_id,)
                ).fetchone()
        if not row:
            return None
        return ApprovalItem(
            id=str(row["id"]),
            tool_name=str(row["tool_name"]),
            description=str(row["description"]),
            args=json.loads(row["args_json"] or "{}"),
            risk=int(row["risk"]),  # type: ignore[arg-type]
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            status=row["status"],
        )

    # === Onboarding Answers ===

    def save_onboarding_answer(
        self, module_id: str, question_id: str, field_key: str, answer: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO onboarding_answers (module_id, question_id, field_key, answer, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (module_id, question_id, field_key, answer, utc_now_iso()),
            )
            conn.commit()

    def get_onboarding_answers(self) -> dict[str, dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM onboarding_answers").fetchall()
        result: dict[str, dict[str, str]] = {}
        for r in rows:
            result.setdefault(str(r["module_id"]), {})[str(r["field_key"])] = str(r["answer"])
        return result

    # === Export ===

    def export_all(self, wiki_dir: Path | None = None) -> MemoryExport:
        facts = self.list_facts(include_superseded=True, limit=5000)
        people = self.list_people()
        wiki_pages: list[WikiPage] = []
        if wiki_dir and wiki_dir.exists():
            for md_file in sorted(wiki_dir.glob("*.md")):
                content = md_file.read_text(encoding="utf-8")
                first_line = (
                    content.splitlines()[0].lstrip("# ").strip() if content else md_file.stem
                )
                wiki_pages.append(
                    WikiPage(
                        slug=md_file.stem,
                        title=first_line or md_file.stem,
                        content=content,
                        updated_at=datetime.fromtimestamp(
                            md_file.stat().st_mtime, tz=timezone.utc
                        ).isoformat(),
                    )
                )
        md_lines = ["# Export memoire JARVIS 2.0", "", "## Faits"]
        for f in facts:
            md_lines.append(f"- [{f.category} | {f.sensitivity}] {f.text}")
        md_lines.extend(["", "## Entourage"])
        for p in people:
            md_lines.append(f"- {p.name} ({p.relation})")
        return MemoryExport(
            exported_at=utc_now_iso(),
            facts=facts,
            people=people,
            wiki_pages=wiki_pages,
            markdown_bundle="\n".join(md_lines),
        )

    # === Notifications Policy & Orders ===

    def get_policy_json(self) -> str | None:
        return self.get_setting("notification_policy")

    def set_policy_json(self, policy_json: str) -> None:
        self.set_setting("notification_policy", policy_json)

    def list_orders(self, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM notification_orders"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM notification_orders WHERE id = ?", (order_id,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_order(self, order_data: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_orders (
                    id, created_at, updated_at, source_glob, event_type_glob,
                    filter_json, text_match, min_urgency, channels_json,
                    message_template, expires_at, max_fires, fired_count,
                    last_fired_at, active, origin, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    source_glob = excluded.source_glob,
                    event_type_glob = excluded.event_type_glob,
                    filter_json = excluded.filter_json,
                    text_match = excluded.text_match,
                    min_urgency = excluded.min_urgency,
                    channels_json = excluded.channels_json,
                    message_template = excluded.message_template,
                    expires_at = excluded.expires_at,
                    max_fires = excluded.max_fires,
                    fired_count = excluded.fired_count,
                    last_fired_at = excluded.last_fired_at,
                    active = excluded.active,
                    origin = excluded.origin,
                    note = excluded.note
                """,
                (
                    order_data["id"],
                    order_data.get("created_at") or now,
                    now,
                    order_data.get("source_glob", "*"),
                    order_data.get("event_type_glob", "*"),
                    order_data.get("filter_json", "{}"),
                    order_data.get("text_match"),
                    int(order_data.get("min_urgency", 1)),
                    order_data.get("channels_json", "[]"),
                    order_data.get("message_template", "{summary}"),
                    order_data.get("expires_at"),
                    order_data.get("max_fires"),
                    int(order_data.get("fired_count", 0)),
                    order_data.get("last_fired_at"),
                    1 if order_data.get("active", True) else 0,
                    order_data.get("origin", "user"),
                    order_data.get("note", ""),
                ),
            )
            conn.commit()

    def revoke_order(self, order_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE notification_orders SET active = 0, updated_at = ? WHERE id = ?",
                (utc_now_iso(), order_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def insert_attempt(self, attempt_data: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_attempts (
                    id, notification_id, approval_id, order_id, channel, target, ts, status, error, external_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_data["id"],
                    attempt_data["notification_id"],
                    attempt_data.get("approval_id"),
                    attempt_data.get("order_id"),
                    attempt_data["channel"],
                    attempt_data.get("target", ""),
                    attempt_data.get("ts") or utc_now_iso(),
                    attempt_data.get("status", "queued"),
                    attempt_data.get("error"),
                    attempt_data.get("external_id"),
                ),
            )
            conn.commit()

    def update_attempt_status(
        self,
        attempt_id: str,
        status: str,
        external_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_attempts
                SET status = ?,
                    external_id = COALESCE(?, external_id),
                    error = COALESCE(?, error)
                WHERE id = ?
                """,
                (status, external_id, error, attempt_id),
            )
            conn.commit()

    def list_attempts(
        self, notification_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM notification_attempts"
        params: list[Any] = []
        if notification_id:
            query += " WHERE notification_id = ?"
            params.append(notification_id)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def update_approval_delivery(
        self,
        approval_id: str,
        delivered_channels: str,
        answered_via: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE approvals
                SET delivered_channels = ?,
                    answered_via = COALESCE(?, answered_via)
                WHERE id = ?
                """,
                (delivered_channels, answered_via, approval_id),
            )
            conn.commit()

    # === Autonomie & Taches de fond ===

    def create_autonomous_task(
        self,
        goal: str,
        max_iterations: int = 30,
        notify_on_finish: bool = True,
        task_id: str | None = None,
    ) -> AutonomousTaskRecord:
        tid = task_id or f"task_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO autonomous_tasks (
                    id, goal, status, iterations, max_iterations, tokens_used, cost_usd,
                    current_step, result_summary, error, notify_on_finish, created_at
                ) VALUES (?, ?, 'queued', 0, ?, 0, 0.0, '', '', NULL, ?, ?)
                """,
                (tid, goal, int(max_iterations), 1 if notify_on_finish else 0, now),
            )
            conn.commit()
        return AutonomousTaskRecord(
            id=tid,
            goal=goal,
            status="queued",
            max_iterations=int(max_iterations),
            notify_on_finish=notify_on_finish,
            created_at=now,
        )

    def update_autonomous_task(self, task_id: str, **fields: Any) -> AutonomousTaskRecord | None:
        allowed = {
            "status",
            "iterations",
            "tokens_used",
            "cost_usd",
            "current_step",
            "result_summary",
            "error",
            "started_at",
            "finished_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_autonomous_task(task_id)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = [*updates.values(), task_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE autonomous_tasks SET {set_clause} WHERE id = ?",
                tuple(params),
            )
            conn.commit()
        return self.get_autonomous_task(task_id)

    @staticmethod
    def _row_to_autonomous_task(row: sqlite3.Row) -> AutonomousTaskRecord:
        return AutonomousTaskRecord(
            id=str(row["id"]),
            goal=str(row["goal"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            iterations=int(row["iterations"] or 0),
            max_iterations=int(row["max_iterations"]) if row["max_iterations"] is not None else 0,
            tokens_used=int(row["tokens_used"] or 0),
            cost_usd=float(row["cost_usd"] or 0.0),
            current_step=str(row["current_step"] or ""),
            result_summary=str(row["result_summary"] or ""),
            error=row["error"],
            notify_on_finish=bool(row["notify_on_finish"]),
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def get_autonomous_task(self, task_id: str) -> AutonomousTaskRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM autonomous_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_autonomous_task(row) if row else None

    def list_autonomous_tasks(self, limit: int = 50) -> list[AutonomousTaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomous_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_autonomous_task(r) for r in rows]

    def record_autonomy_audit(
        self,
        tool_name: str,
        risk: int,
        status: str,
        target: str = "",
        summary: str = "",
        task_id: str | None = None,
        error: str | None = None,
    ) -> AutonomyAuditEntry:
        eid = f"aud_{uuid.uuid4().hex[:12]}"
        ts = utc_now_iso()
        safe_risk = int(risk) if int(risk) in (0, 1, 2, 3) else 0
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO autonomy_audit (id, ts, task_id, tool_name, risk, status, target, summary, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eid,
                    ts,
                    task_id,
                    tool_name,
                    safe_risk,
                    status,
                    target[:500],
                    summary[:1000],
                    error,
                ),
            )
            conn.commit()
        return AutonomyAuditEntry(
            id=eid,
            ts=ts,
            task_id=task_id,
            tool_name=tool_name,
            risk=safe_risk,  # type: ignore[arg-type]
            status=status,
            target=target[:500],
            summary=summary[:1000],
            error=error,
        )

    def list_autonomy_audit(self, limit: int = 100) -> list[AutonomyAuditEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomy_audit ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            AutonomyAuditEntry(
                id=str(r["id"]),
                ts=str(r["ts"]),
                task_id=r["task_id"],
                tool_name=str(r["tool_name"]),
                risk=int(r["risk"]) if int(r["risk"]) in (0, 1, 2, 3) else 0,  # type: ignore[arg-type]
                status=str(r["status"]),
                target=str(r["target"] or ""),
                summary=str(r["summary"] or ""),
                error=r["error"],
            )
            for r in rows
        ]

    # === Orchestration par objectifs ===

    def save_goal(self, goal: GoalRecord) -> GoalRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goals (
                    id, title, goal, status, success_criteria_json, verified_criteria_json,
                    orchestrator_model, allowed_models_json, tokens_used, cost_usd, max_cost_usd,
                    progress_pct, current_phase, final_summary, pause_reason, notify_on_finish,
                    created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    status = excluded.status,
                    success_criteria_json = excluded.success_criteria_json,
                    verified_criteria_json = excluded.verified_criteria_json,
                    orchestrator_model = excluded.orchestrator_model,
                    allowed_models_json = excluded.allowed_models_json,
                    tokens_used = excluded.tokens_used,
                    cost_usd = excluded.cost_usd,
                    max_cost_usd = excluded.max_cost_usd,
                    progress_pct = excluded.progress_pct,
                    current_phase = excluded.current_phase,
                    final_summary = excluded.final_summary,
                    pause_reason = excluded.pause_reason,
                    notify_on_finish = excluded.notify_on_finish,
                    updated_at = excluded.updated_at,
                    finished_at = excluded.finished_at
                """,
                (
                    goal.id,
                    goal.title,
                    goal.goal,
                    goal.status,
                    json.dumps(goal.success_criteria, ensure_ascii=False),
                    json.dumps(goal.verified_criteria, ensure_ascii=False),
                    goal.orchestrator_model,
                    json.dumps(goal.allowed_models, ensure_ascii=False),
                    int(goal.tokens_used),
                    float(goal.cost_usd),
                    float(goal.max_cost_usd),
                    int(goal.progress_pct),
                    goal.current_phase,
                    goal.final_summary,
                    goal.pause_reason,
                    1 if goal.notify_on_finish else 0,
                    goal.created_at,
                    goal.updated_at,
                    goal.finished_at,
                ),
            )
            conn.commit()
        return goal

    def upsert_goal_subtask(self, sub: SubtaskRecord) -> SubtaskRecord:
        now = utc_now_iso()
        created = sub.created_at or now
        updated = sub.updated_at or now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goal_subtasks (
                    id, goal_id, title, description, instructions, role_profile,
                    assigned_model, depends_on_json, status, attempt, max_attempts,
                    result, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    instructions = excluded.instructions,
                    role_profile = excluded.role_profile,
                    assigned_model = excluded.assigned_model,
                    depends_on_json = excluded.depends_on_json,
                    status = excluded.status,
                    attempt = excluded.attempt,
                    max_attempts = excluded.max_attempts,
                    result = excluded.result,
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    sub.id,
                    sub.goal_id,
                    sub.title,
                    sub.description,
                    sub.instructions,
                    sub.role_profile,
                    sub.assigned_model,
                    json.dumps(sub.depends_on, ensure_ascii=False),
                    sub.status,
                    int(sub.attempt),
                    int(sub.max_attempts),
                    sub.result,
                    sub.error,
                    created,
                    updated,
                ),
            )
            conn.commit()
        return sub

    def upsert_subagent_run(self, run: SubagentRunRecord) -> SubagentRunRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subagent_runs (
                    id, goal_id, subtask_id, model, provider, role_profile, status,
                    iterations, max_iterations, tokens_in, tokens_out, cost_usd,
                    last_tool, last_heartbeat_at, error, result_summary, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model = excluded.model,
                    provider = excluded.provider,
                    status = excluded.status,
                    iterations = excluded.iterations,
                    tokens_in = excluded.tokens_in,
                    tokens_out = excluded.tokens_out,
                    cost_usd = excluded.cost_usd,
                    last_tool = excluded.last_tool,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    error = excluded.error,
                    result_summary = excluded.result_summary,
                    finished_at = excluded.finished_at
                """,
                (
                    run.id,
                    run.goal_id,
                    run.subtask_id,
                    run.model,
                    run.provider,
                    run.role_profile,
                    run.status,
                    int(run.iterations),
                    int(run.max_iterations),
                    int(run.tokens_in),
                    int(run.tokens_out),
                    float(run.cost_usd),
                    run.last_tool,
                    run.last_heartbeat_at,
                    run.error,
                    run.result_summary,
                    run.started_at,
                    run.finished_at,
                ),
            )
            conn.commit()
        return run

    def append_goal_event(self, event: GoalEventRecord) -> GoalEventRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO goal_events (
                    id, goal_id, ts, kind, subtask_id, run_id, message, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.goal_id,
                    event.ts,
                    event.kind,
                    event.subtask_id,
                    event.run_id,
                    event.message,
                    json.dumps(event.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
        return event

    def _build_goal_record(
        self,
        row: sqlite3.Row,
        sub_rows: list[sqlite3.Row],
        run_rows: list[sqlite3.Row],
        ev_rows: list[sqlite3.Row],
    ) -> GoalRecord:
        gid = str(row["id"])
        runs_by_sub: dict[str, list[SubagentRunRecord]] = {}
        for rr in run_rows:
            rec = SubagentRunRecord(
                id=str(rr["id"]),
                goal_id=str(rr["goal_id"]),
                subtask_id=str(rr["subtask_id"]),
                model=str(rr["model"]),
                provider=str(rr["provider"] or ""),
                role_profile=str(rr["role_profile"] or "general"),
                status=str(rr["status"]),  # type: ignore[arg-type]
                iterations=int(rr["iterations"] or 0),
                max_iterations=int(rr["max_iterations"]) if rr["max_iterations"] is not None else 0,
                tokens_in=int(rr["tokens_in"] or 0),
                tokens_out=int(rr["tokens_out"] or 0),
                cost_usd=float(rr["cost_usd"] or 0.0),
                last_tool=str(rr["last_tool"] or ""),
                last_heartbeat_at=str(rr["last_heartbeat_at"] or ""),
                error=rr["error"],
                result_summary=str(rr["result_summary"] or ""),
                started_at=str(rr["started_at"] or ""),
                finished_at=rr["finished_at"],
            )
            runs_by_sub.setdefault(rec.subtask_id, []).append(rec)

        subtasks = [
            SubtaskRecord(
                id=str(sr["id"]),
                goal_id=str(sr["goal_id"]),
                title=str(sr["title"]),
                description=str(sr["description"] or ""),
                instructions=str(sr["instructions"] or ""),
                role_profile=str(sr["role_profile"] or "general"),
                assigned_model=str(sr["assigned_model"]),
                depends_on=json.loads(sr["depends_on_json"] or "[]"),
                status=str(sr["status"]),  # type: ignore[arg-type]
                attempt=int(sr["attempt"] or 0),
                max_attempts=int(sr["max_attempts"] or 3),
                result=str(sr["result"] or ""),
                error=sr["error"],
                runs=runs_by_sub.get(str(sr["id"]), []),
                created_at=str(sr["created_at"] or ""),
                updated_at=str(sr["updated_at"] or ""),
            )
            for sr in sub_rows
        ]
        events = [
            GoalEventRecord(
                id=str(er["id"]),
                goal_id=str(er["goal_id"]),
                ts=str(er["ts"]),
                kind=str(er["kind"]),  # type: ignore[arg-type]
                subtask_id=er["subtask_id"],
                run_id=er["run_id"],
                message=str(er["message"]),
                metadata=json.loads(er["metadata_json"] or "{}"),
            )
            for er in ev_rows[:80]
        ]
        events.reverse()
        return GoalRecord(
            id=gid,
            title=str(row["title"]),
            goal=str(row["goal"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            success_criteria=json.loads(row["success_criteria_json"] or "[]"),
            verified_criteria=json.loads(row["verified_criteria_json"] or "[]"),
            orchestrator_model=str(row["orchestrator_model"]),
            allowed_models=json.loads(row["allowed_models_json"] or "[]"),
            tokens_used=int(row["tokens_used"] or 0),
            cost_usd=float(row["cost_usd"] or 0.0),
            max_cost_usd=float(row["max_cost_usd"]) if row["max_cost_usd"] is not None else 0.0,
            progress_pct=int(row["progress_pct"] or 0),
            current_phase=str(row["current_phase"] or ""),
            final_summary=str(row["final_summary"] or ""),
            pause_reason=row["pause_reason"],
            notify_on_finish=bool(row["notify_on_finish"]),
            subtasks=subtasks,
            events=events,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            finished_at=row["finished_at"],
        )

    def _hydrate_goal(self, conn: sqlite3.Connection, row: sqlite3.Row) -> GoalRecord:
        gid = str(row["id"])
        sub_rows = conn.execute(
            "SELECT * FROM goal_subtasks WHERE goal_id = ? ORDER BY created_at ASC", (gid,)
        ).fetchall()
        run_rows = conn.execute(
            "SELECT * FROM subagent_runs WHERE goal_id = ? ORDER BY started_at ASC", (gid,)
        ).fetchall()
        ev_rows = conn.execute(
            "SELECT * FROM goal_events WHERE goal_id = ? ORDER BY ts DESC LIMIT 80", (gid,)
        ).fetchall()
        return self._build_goal_record(row, sub_rows, run_rows, ev_rows)

    def get_goal(self, goal_id: str) -> GoalRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if not row:
                return None
            return self._hydrate_goal(conn, row)

    def list_goals(self, limit: int = 30) -> list[GoalRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            if not rows:
                return []
            goal_ids = [str(r["id"]) for r in rows]
            placeholders = ", ".join("?" for _ in goal_ids)
            sub_all = conn.execute(
                f"SELECT * FROM goal_subtasks WHERE goal_id IN ({placeholders}) ORDER BY created_at ASC",
                tuple(goal_ids),
            ).fetchall()
            run_all = conn.execute(
                f"SELECT * FROM subagent_runs WHERE goal_id IN ({placeholders}) ORDER BY started_at ASC",
                tuple(goal_ids),
            ).fetchall()
            ev_all = conn.execute(
                f"SELECT * FROM goal_events WHERE goal_id IN ({placeholders}) ORDER BY ts DESC",
                tuple(goal_ids),
            ).fetchall()
            subs_by_goal: dict[str, list[sqlite3.Row]] = {}
            for sr in sub_all:
                subs_by_goal.setdefault(str(sr["goal_id"]), []).append(sr)
            runs_by_goal: dict[str, list[sqlite3.Row]] = {}
            for rr in run_all:
                runs_by_goal.setdefault(str(rr["goal_id"]), []).append(rr)
            evs_by_goal: dict[str, list[sqlite3.Row]] = {}
            for er in ev_all:
                bucket = evs_by_goal.setdefault(str(er["goal_id"]), [])
                if len(bucket) < 80:
                    bucket.append(er)
            return [
                self._build_goal_record(
                    r,
                    subs_by_goal.get(str(r["id"]), []),
                    runs_by_goal.get(str(r["id"]), []),
                    evs_by_goal.get(str(r["id"]), []),
                )
                for r in rows
            ]
