"""Adaptateur fin exposant l'API de stockage attendue par MemoryEngine au-dessus de MemoryDatabase."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis_shared.models import (
    Episode,
    Fact,
    NotificationRecord,
    Person,
    Sensitivity,
    SystemEvent,
    Usage,
)

from .schema import MemoryDatabase, _pack_vector, _unpack_vector, utc_now_iso

_SENS_MAP = {"privé": "prive", "très privé": "tres_prive"}


def _row_to_fact(row: sqlite3.Row) -> Fact:
    raw_sens = str(row["sensitivity"] or "normal")
    return Fact(
        id=str(row["id"]),
        subject=str(row["subject"]),
        predicate=str(row["predicate"]),
        object=str(row["object"]),
        text=str(row["text"]),
        category=str(row["category"]),
        confidence=float(row["confidence"]),
        source=str(row["source"]),
        sensitivity=Sensitivity(_SENS_MAP.get(raw_sens, raw_sens)),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_used_at=row["last_used_at"],
        superseded_by=row["superseded_by"],
    )


def _decode_embedding(row: sqlite3.Row) -> list[float]:
    raw_json = row["embedding_json"] if "embedding_json" in row.keys() else "[]"
    emb = json.loads(raw_json or "[]")
    return _unpack_vector(row["embedding"]) if not emb and row["embedding"] else emb


class MemoryStorage:
    """Adaptateur fin : expose l'API attendue par MemoryEngine au-dessus de MemoryDatabase."""

    def __init__(
        self,
        db_path: Path | str,
        encryption_key: str | None = None,
        database: MemoryDatabase | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.encryption_key = encryption_key
        self.db = database or MemoryDatabase(db_path, encryption_key=encryption_key)

    def close(self) -> None:
        pass

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.db._connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.db._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def insert_fact(self, fact: Fact, embedding: list[float]) -> Fact:
        blob = _pack_vector(embedding) if embedding else None
        sql = (
            "INSERT OR REPLACE INTO facts (id, subject, predicate, object, text, category, "
            "confidence, source, sensitivity, embedding_json, embedding, created_at, updated_at, "
            "last_used_at, superseded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        self._exec(
            sql,
            (
                fact.id,
                fact.subject,
                fact.predicate,
                fact.object,
                fact.text,
                fact.category,
                fact.confidence,
                fact.source,
                fact.sensitivity.value,
                json.dumps(embedding),
                blob,
                fact.created_at,
                fact.updated_at,
                fact.last_used_at,
                fact.superseded_by,
            ),
        )
        return fact

    def mark_fact_superseded(self, old_fact_id: str, new_fact_id: str) -> None:
        self._exec(
            "UPDATE facts SET superseded_by = ?, updated_at = ? WHERE id = ?",
            (new_fact_id, utc_now_iso(), old_fact_id),
        )

    def mark_fact_used(self, fact_id: str) -> None:
        self._exec(
            "UPDATE facts SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
            (utc_now_iso(), fact_id),
        )

    def list_active_facts(self) -> list[tuple[Fact, list[float], int]]:
        rows = self._fetchall(
            "SELECT * FROM facts WHERE superseded_by IS NULL ORDER BY updated_at DESC"
        )
        return [(_row_to_fact(r), _decode_embedding(r), int(r["use_count"] or 0)) for r in rows]

    def get_fact_by_id(self, fact_id: str) -> Fact | None:
        rows = self._fetchall("SELECT * FROM facts WHERE id = ?", (fact_id,))
        return _row_to_fact(rows[0]) if rows else None

    def insert_episode(self, ep: Episode, embedding: list[float]) -> Episode:
        blob = _pack_vector(embedding) if embedding else None
        self._exec(
            "INSERT OR REPLACE INTO episodes (id, ts, kind, channel, summary, raw_ref, embedding_json, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ep.id,
                ep.ts,
                ep.kind,
                ep.channel,
                ep.summary,
                ep.raw_ref,
                json.dumps(embedding),
                blob,
            ),
        )
        return ep

    def list_episodes(self, limit: int = 200) -> list[tuple[Episode, list[float]]]:
        rows = self._fetchall("SELECT * FROM episodes ORDER BY ts DESC LIMIT ?", (limit,))
        return [
            (
                Episode(
                    id=str(r["id"]),
                    ts=str(r["ts"]),
                    kind=str(r["kind"]),
                    channel=str(r["channel"]),
                    summary=str(r["summary"]),
                    raw_ref=r["raw_ref"],
                ),
                _decode_embedding(r),
            )
            for r in rows
        ]

    def purge_old_raw_episodes(self, cutoff_iso: str) -> int:
        return self._exec(
            "DELETE FROM episodes WHERE ts < ? AND kind != 'consolidated_summary'",
            (cutoff_iso,),
        )

    def upsert_person(self, person: Person) -> Person:
        now = utc_now_iso()
        sql = (
            "INSERT INTO people (id, name, relation, birthday, contact_ref, is_vip, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "relation = excluded.relation, birthday = excluded.birthday, contact_ref = excluded.contact_ref, "
            "is_vip = excluded.is_vip, notes = excluded.notes, updated_at = excluded.updated_at"
        )
        self._exec(
            sql,
            (
                person.id,
                person.name,
                person.relation,
                person.birthday,
                person.contact_ref,
                1 if person.is_vip else 0,
                person.notes,
                now,
                now,
            ),
        )
        return person

    def insert_event(self, event: SystemEvent) -> SystemEvent:
        urg = int(event.urgency) if event.urgency is not None else 0
        self._exec(
            "INSERT INTO events (id, ts, source, type, payload_json, dedup_key, urgency, handled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.ts,
                event.source,
                event.event_type,
                json.dumps(event.payload, ensure_ascii=False),
                event.dedup_key,
                urg,
                1 if event.handled else 0,
            ),
        )
        return event

    def insert_notification(self, notif: NotificationRecord) -> NotificationRecord:
        self._exec(
            "INSERT OR REPLACE INTO notifications (id, ts, channel, urgency, message, reason, dedup_key, status, acked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notif.id,
                notif.ts,
                notif.channel,
                int(notif.urgency),
                notif.message,
                notif.reason,
                notif.dedup_key,
                notif.status,
                notif.acked_at,
            ),
        )
        return notif

    def log_llm_call(self, role: str, usage: Usage) -> None:
        self._exec(
            "INSERT INTO llm_calls (id, ts, role, model, provider, tokens_in, tokens_out, cached_tokens, cost, latency_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"llm_{uuid.uuid4().hex[:12]}",
                utc_now_iso(),
                role,
                usage.model,
                usage.provider,
                usage.tokens_in,
                usage.tokens_out,
                usage.cached_tokens,
                usage.cost_usd,
                usage.latency_ms,
            ),
        )

    def get_today_spend_usd(self) -> float:
        today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self._fetchall(
            "SELECT COALESCE(SUM(cost), 0.0) AS total FROM llm_calls WHERE ts LIKE ?",
            (f"{today_prefix}%",),
        )
        return float(rows[0]["total"] if rows else 0.0)
