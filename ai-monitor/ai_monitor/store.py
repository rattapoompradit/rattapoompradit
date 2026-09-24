"""SQLite history: every check result, plus an event whenever a status changes."""

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  detail TEXT,
  extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_provider_ts ON checks(provider, ts);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  provider TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  detail TEXT
);
"""


class Store:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def add_check(self, provider: str, status: str, latency_ms: int | None, detail: str, extra: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO checks (ts, provider, status, latency_ms, detail, extra_json) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), provider, status, latency_ms, detail, json.dumps(extra, ensure_ascii=False, default=str)),
            )

    def add_event(self, provider: str, from_status: str | None, to_status: str, detail: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (ts, provider, from_status, to_status, detail) VALUES (?, ?, ?, ?, ?)",
                (time.time(), provider, from_status, to_status, detail),
            )

    def latencies(self, provider: str, limit: int = 40, extra_key: str | None = None) -> list[float | None]:
        """Recent latency values, or `extra[extra_key]` when given (oldest first)."""
        with self._lock:
            if extra_key:
                rows = self._conn.execute(
                    "SELECT json_extract(extra_json, ?) FROM checks WHERE provider = ? ORDER BY ts DESC LIMIT ?",
                    (f"$.{extra_key}", provider, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT latency_ms FROM checks WHERE provider = ? ORDER BY ts DESC LIMIT ?", (provider, limit)
                ).fetchall()
        return [r[0] for r in reversed(rows)]

    def uptime_pct(self, provider: str, since: float) -> float | None:
        with self._lock:
            total, up = self._conn.execute(
                "SELECT COUNT(*), SUM(status = 'up') FROM checks WHERE provider = ? AND ts >= ? AND status != 'unknown'",
                (provider, since),
            ).fetchone()
        return round(up * 100 / total, 1) if total else None

    def count_detail(self, provider: str, since: float, pattern: str) -> int:
        """How many checks since `since` had a detail matching the SQL LIKE `pattern` (e.g. '%HTTP 429%')."""
        with self._lock:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM checks WHERE provider = ? AND ts >= ? AND detail LIKE ?", (provider, since, pattern)
            ).fetchone()
        return n

    def recent_events(self, limit: int = 8) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, provider, from_status, to_status, detail FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(zip(("ts", "provider", "from", "to", "detail"), r)) for r in rows]

    def purge(self, days: int) -> None:
        cutoff = time.time() - days * 86400
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM checks WHERE ts < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
