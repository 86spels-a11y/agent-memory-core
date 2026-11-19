"""agent-memory-core: Sovereign memory core for AI agents on SQLite."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Optional


class MemoryCore:
    def __init__(self, db_path: str = "memory.db") -> None:
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_wal()
        self._init_db()

    def _ensure_wal(self) -> None:
        """Включает WAL один раз на файл БД.

        WAL — это свойство файла, а не соединения. Повторная установка
        на каждом соединении создаёт лишнюю конкуренцию за эксклюзивный
        лок, который не подчиняется busy_timeout.
        """
        if self.db_path == ":memory:":
            return
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            if str(mode).lower() == "wal":
                return
            last_err: Optional[Exception] = None
            for attempt in range(5):
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    return
                except sqlite3.OperationalError as e:
                    last_err = e
                    if "locked" not in str(e).lower():
                        raise
                    time.sleep(0.1 * (attempt + 1))
            if last_err is not None:
                raise last_err
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=30.0
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000;")
            self._conn = conn
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agent_id, category, key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    context TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_agent_ts "
                "ON events(agent_id, timestamp DESC)"
            )
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            if "parent_event_id" not in cols:
                conn.execute(
                    "ALTER TABLE events ADD COLUMN parent_event_id INTEGER "
                    "REFERENCES events(id)"
                )
            if "relation" not in cols:
                conn.execute(
                    "ALTER TABLE events ADD COLUMN relation TEXT"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_parent "
                "ON events(parent_event_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fact_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fact_history_lookup "
                "ON fact_history(agent_id, category, key, valid_to)"
            )

    def add_fact(
        self,
        agent_id: str,
        category: str,
        key: str,
        value: Any,
        confidence: float = 1.0,
    ) -> bool:
        conn = self._get_connection()
        now = datetime.now(timezone.utc).isoformat()
        if not isinstance(value, str):
            value = json.dumps(value)
        with conn:
            existing = conn.execute(
                "SELECT value, confidence, updated_at FROM facts "
                "WHERE agent_id=? AND category=? AND key=?",
                (agent_id, category, key),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "INSERT INTO fact_history "
                    "(agent_id, category, key, value, confidence, "
                    " valid_from, valid_to) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        agent_id, category, key,
                        existing["value"], existing["confidence"],
                        existing["updated_at"], now,
                    ),
                )
            conn.execute(
                """
                INSERT INTO facts (agent_id, category, key, value, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, category, key) DO UPDATE SET
                    value = excluded.value,
                    confidence = excluded.confidence,
                    updated_at = excluded.updated_at
                """,
                (agent_id, category, key, value, confidence, now, now),
            )
        return True

    def get_fact_as_of(
        self, agent_id: str, category: str, key: str, timestamp: str
    ) -> Optional[dict]:
        """Возвращает факт, который был актуален в момент timestamp (ISO-8601).

        Сначала ищет в истории (valid_from <= ts < valid_to), затем в
        текущей таблице facts (если факт создан до ts и не перезаписан).
        """
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM fact_history "
            "WHERE agent_id=? AND category=? AND key=? "
            "  AND valid_from <= ? AND valid_to > ? "
            "ORDER BY valid_to ASC LIMIT 1",
            (agent_id, category, key, timestamp, timestamp),
        ).fetchone()
        if row is not None:
            d = dict(row)
            d["value"] = d["value"]
            return d
        current = conn.execute(
            "SELECT * FROM facts WHERE agent_id=? AND category=? AND key=?",
            (agent_id, category, key),
        ).fetchone()
        if current is not None and current["created_at"] <= timestamp:
            return dict(current)
        return None

    def get_fact_history(
        self, agent_id: str, category: str, key: str
    ) -> list[dict]:
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT * FROM fact_history "
            "WHERE agent_id=? AND category=? AND key=? "
            "ORDER BY valid_from ASC",
            (agent_id, category, key),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_fact(self, agent_id: str, category: str, key: str) -> Optional[dict]:
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM facts WHERE agent_id=? AND category=? AND key=?",
            (agent_id, category, key),
        ).fetchone()
        return dict(row) if row else None

    def log_event(
        self,
        agent_id: str,
        event_type: str,
        summary: str,
        context: Optional[dict] = None,
        caused_by: Optional[int] = None,
        relation: str = "caused",
    ) -> int:
        if relation not in ("caused", "enabled", "prevented"):
            raise ValueError(
                f"relation must be caused/enabled/prevented, got {relation!r}"
            )
        conn = self._get_connection()
        ts = datetime.now(timezone.utc).isoformat()
        ctx_json = json.dumps(context) if context is not None else None
        if caused_by is not None:
            parent = conn.execute(
                "SELECT id FROM events WHERE id=?", (caused_by,)
            ).fetchone()
            if parent is None:
                raise ValueError(f"parent event {caused_by} does not exist")
        with conn:
            cur = conn.execute(
                "INSERT INTO events "
                "(agent_id, timestamp, event_type, summary, context, "
                " parent_event_id, relation) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    agent_id, ts, event_type, summary, ctx_json,
                    caused_by, relation if caused_by is not None else None,
                ),
            )
        return int(cur.lastrowid)

    def trace_cause(self, event_id: int) -> list[dict]:
        conn = self._get_connection()
        chain: list[dict] = []
        seen: set[int] = set()
        current: Optional[int] = event_id
        while current is not None:
            if current in seen:
                break
            seen.add(current)
            row = conn.execute(
                "SELECT * FROM events WHERE id=?", (current,)
            ).fetchone()
            if row is None:
                break
            d = dict(row)
            d["context"] = json.loads(d["context"]) if d["context"] else None
            chain.append(d)
            current = d.get("parent_event_id")
        return chain

    def get_recent_events(
        self, limit: int = 10, agent_id: Optional[str] = None
    ) -> list[dict]:
        conn = self._get_connection()
        if agent_id is None:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE agent_id=? "
                "ORDER BY timestamp DESC, id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            d["context"] = json.loads(d["context"]) if d["context"] else None
            out.append(d)
        return out
