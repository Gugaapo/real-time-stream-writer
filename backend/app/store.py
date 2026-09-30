"""SQLite transcripts. One row per paragraph, keyed by browser session."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(dest, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    style TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    custom_prompt TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS paragraphs (
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, seq)
                );
                CREATE TABLE IF NOT EXISTS spend (
                    month TEXT PRIMARY KEY,
                    micros INTEGER NOT NULL
                );
                """
            )
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
            if "custom_prompt" not in columns:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN custom_prompt TEXT NOT NULL DEFAULT ''"
                )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_session(self, style: str, custom_prompt: str = "") -> dict:
        session_id = uuid.uuid4().hex
        stamp = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (id, style, created_at, last_seen_at, custom_prompt)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, style, stamp, stamp, custom_prompt or ""),
            )
            self._conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, style, created_at, last_seen_at, custom_prompt
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "style": row["style"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "custom_prompt": row["custom_prompt"] or "",
            "paragraphs": self.paragraphs(session_id),
        }

    def paragraphs(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT seq, text, created_at FROM paragraphs
                WHERE session_id = ? ORDER BY seq
                """,
                (session_id,),
            ).fetchall()
        return [
            {"seq": row["seq"], "text": row["text"], "created_at": row["created_at"]}
            for row in rows
        ]

    def add_paragraph(self, session_id: str, text: str) -> dict:
        clean = " ".join((text or "").split())
        stamp = _now()
        with self._lock:
            current = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM paragraphs WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(current[0]) + 1
            self._conn.execute(
                """
                INSERT INTO paragraphs (session_id, seq, text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, seq, clean, stamp),
            )
            self._conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (stamp, session_id),
            )
            self._conn.commit()
        return {"seq": seq, "text": clean, "created_at": stamp}

    def clear_paragraphs(self, session_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return False
            self._conn.execute(
                "DELETE FROM paragraphs WHERE session_id = ?",
                (session_id,),
            )
            self._conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (_now(), session_id),
            )
            self._conn.commit()
        return True

    def spend_micros(self, month: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT micros FROM spend WHERE month = ?",
                (month,),
            ).fetchone()
        return int(row[0]) if row else 0

    def add_spend_micros(self, month: str, delta: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT micros FROM spend WHERE month = ?",
                (month,),
            ).fetchone()
            updated = max(0, (int(row[0]) if row else 0) + int(delta))
            self._conn.execute(
                """
                INSERT INTO spend (month, micros) VALUES (?, ?)
                ON CONFLICT(month) DO UPDATE SET micros = excluded.micros
                """,
                (month, updated),
            )
            self._conn.commit()
            return updated

    def touch(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (_now(), session_id),
            )
            self._conn.commit()
