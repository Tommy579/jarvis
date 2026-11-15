"""Schema SQL, migrations SQLite (+ sqlite-vec optionnel) et operations memoire."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omi_shared.rest_models import (
    AlertRecord,
    ApprovalItem,
    ChatMessage,
    DailyUsageItem,
    FactCreate,
    FactRecord,
    FactUpdate,
    MemoryExport,
    PersonRecord,
    SensitivityLevel,
    ToolCallRecord,
    WikiPage,
)


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


def _deterministic_text_embedding(text: str, dims: int = 16) -> list[float]:
    """Produit un vecteur normalise local leger pour les tests et la recherche fallback."""
    vec = [0.0] * dims
    for idx, ch in enumerate(text.lower().encode("utf-8")):
        vec[(idx + ch) % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


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
]


class MemoryDatabase:
    """Gestionnaire SQLite chiffre (optionnel) et migrations OMI 2.0."""

    def __init__(self, db_path: str | Path, encryption_key: str | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.encryption_key = encryption_key
        self.vec_enabled = False
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        if self.encryption_key:
            try:
                import sqlcipher3  # type: ignore[import-not-found]

                conn = sqlcipher3.connect(str(self.db_path))
                conn.execute("PRAGMA key = ?", (self.encryption_key,))
                conn.row_factory = sqlite3.Row
                return conn
            except ImportError:
                pass
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        self._try_load_sqlite_vec(conn)
        return conn

    def _try_load_sqlite_vec(self, conn: sqlite3.Connection) -> None:
        try:
            import sqlite_vec  # type: ignore[import-not-found]

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self.vec_enabled = True
        except Exception:
            self.vec_enabled = False

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
                    conn.executescript(sql_script)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, utc_now_iso()),
                    )
            if self.vec_enabled:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_facts USING vec0(fact_id TEXT PRIMARY KEY, embedding FLOAT[16])"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(episode_id TEXT PRIMARY KEY, embedding FLOAT[16])"
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
            clauses.append("sensitivity = ?")
            params.append(sensitivity)
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

    def search_facts_hybrid(self, query: str, limit: int = 10) -> list[FactRecord]:
        """Combine correspondance textuelle et similarite cosinus sur les embeddings locaux."""
        query_vec = _deterministic_text_embedding(query)
        q_tokens = {tok for tok in query.lower().split() if len(tok) > 1}
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE superseded_by IS NULL").fetchall()
        scored: list[tuple[float, FactRecord]] = []
        for row in rows:
            fact = self._row_to_fact(row)
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
        blob = _pack_vector(_deterministic_text_embedding(new_text))
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE facts
                SET text = ?, category = ?, confidence = ?, sensitivity = ?,
                    superseded_by = ?, updated_at = ?, embedding = ?
                WHERE id = ?
                """,
                (new_text, new_cat, new_conf, new_sens, new_sup, now, blob, fact_id),
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
        notes: str = "",
        person_id: str | None = None,
    ) -> PersonRecord:
        now = utc_now_iso()
        pid = person_id or f"pers_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM people WHERE LOWER(name) = LOWER(?)", (name,)
            ).fetchone()
            if existing:
                pid = str(existing["id"])
                conn.execute(
                    """
                    UPDATE people
                    SET relation = ?, birthday = ?, contact_ref = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (relation, birthday, contact_ref, notes, now, pid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO people (id, name, relation, birthday, contact_ref, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (pid, name, relation, birthday, contact_ref, notes, now, now),
                )
            conn.commit()
        return PersonRecord(
            id=pid,
            name=name,
            relation=relation,
            birthday=birthday,
            contact_ref=contact_ref,
            notes=notes,
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
        status = str(getattr(record, "status", "sent"))
        dedup_key = getattr(record, "dedup_key", None)
        nid = getattr(record, "id", None) or f"alert_{uuid.uuid4().hex[:12]}"
        ts = getattr(record, "ts", None) or utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO notifications (id, ts, channel, urgency, message, reason, status, dedup_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (nid, ts, channel, urgency, message, reason, status, dedup_key),
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
                status=r["status"],
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
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT substr(ts, 1, 10) AS day, model,
                       COUNT(*) AS calls,
                       COALESCE(SUM(tokens_in), 0) AS tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS tokens_out,
                       COALESCE(SUM(cost), 0.0) AS cost
                FROM llm_calls
                GROUP BY day, model
                ORDER BY day DESC
                LIMIT ?
                """,
                (days * 10,),
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

    def list_approvals(self) -> list[ApprovalItem]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created_at DESC").fetchall()
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
        status = "approved" if approved else "denied"
        with self._connect() as conn:
            conn.execute("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))
            conn.commit()
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
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
        md_lines = ["# Export memoire OMI 2.0", "", "## Faits"]
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
