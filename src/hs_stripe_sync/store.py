"""SQLite state: event idempotency, ID links, echo guard and dead letters."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL,            -- processing | done | failed
    updated_at REAL NOT NULL,
    PRIMARY KEY (source, event_id)
);
CREATE TABLE IF NOT EXISTS links (
    stripe_customer_id TEXT PRIMARY KEY,
    hubspot_contact_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS echo_guard (
    system TEXT NOT NULL,            -- the system we WROTE to
    object_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    written_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    error TEXT NOT NULL,
    created_at REAL NOT NULL,
    resolved_at REAL
);
"""

# A claim that has been "processing" this long is assumed to be from a crashed worker.
STALE_CLAIM_SECONDS = 600


@dataclass
class DeadLetter:
    id: int
    source: str
    event_id: str
    payload: dict
    error: str
    created_at: float


class Store:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    # --- idempotency ----------------------------------------------------
    def claim_event(self, source: str, event_id: str) -> bool:
        """Return True if this worker should process the event.

        New and previously failed events can be claimed; events that are done
        or currently in flight cannot (protects against provider re-delivery).
        """
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT status, updated_at FROM events WHERE source=? AND event_id=?", (source, event_id)
            ).fetchone()
            if row:
                status, updated_at = row
                if status == "done":
                    return False
                if status == "processing" and now - updated_at < STALE_CLAIM_SECONDS:
                    return False
            self._conn.execute(
                "INSERT INTO events(source, event_id, status, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(source, event_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                (source, event_id, "processing", now),
            )
            return True

    def finish_event(self, source: str, event_id: str, ok: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET status=?, updated_at=? WHERE source=? AND event_id=?",
                ("done" if ok else "failed", time.time(), source, event_id),
            )

    # --- id links -------------------------------------------------------
    def link(self, stripe_customer_id: str, hubspot_contact_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO links(stripe_customer_id, hubspot_contact_id) VALUES (?,?)",
                (stripe_customer_id, hubspot_contact_id),
            )

    def hubspot_id_for(self, stripe_customer_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT hubspot_contact_id FROM links WHERE stripe_customer_id=?", (stripe_customer_id,)
            ).fetchone()
        return row[0] if row else None

    def stripe_id_for(self, hubspot_contact_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT stripe_customer_id FROM links WHERE hubspot_contact_id=?", (hubspot_contact_id,)
            ).fetchone()
        return row[0] if row else None

    def all_links(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._conn.execute("SELECT stripe_customer_id, hubspot_contact_id FROM links"))

    # --- echo guard (loop prevention) -----------------------------------
    def record_write(self, system: str, object_id: str, field: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO echo_guard(system, object_id, field, value, written_at) VALUES (?,?,?,?,?)",
                (system, object_id, field, value, time.time()),
            )

    def consume_echo(self, system: str, object_id: str, field: str, value: str, window_seconds: int) -> bool:
        """True if this change is the echo of a write we made recently."""
        cutoff = time.time() - window_seconds
        with self._lock:
            self._conn.execute("DELETE FROM echo_guard WHERE written_at < ?", (cutoff,))
            row = self._conn.execute(
                "SELECT rowid FROM echo_guard WHERE system=? AND object_id=? AND field=? AND value=? "
                "ORDER BY written_at DESC LIMIT 1",
                (system, object_id, field, value),
            ).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM echo_guard WHERE rowid=?", (row[0],))
            return True

    # --- dead letters ---------------------------------------------------
    def add_dead_letter(self, source: str, event_id: str, payload: dict, error: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO dead_letters(source, event_id, payload, error, created_at) VALUES (?,?,?,?,?)",
                (source, event_id, json.dumps(payload), error, time.time()),
            )
            return int(cur.lastrowid)

    def open_dead_letters(self) -> list[DeadLetter]:
        with self._lock:
            rows = list(self._conn.execute(
                "SELECT id, source, event_id, payload, error, created_at FROM dead_letters "
                "WHERE resolved_at IS NULL ORDER BY id"
            ))
        return [DeadLetter(r[0], r[1], r[2], json.loads(r[3]), r[4], r[5]) for r in rows]

    def get_dead_letter(self, dl_id: int) -> DeadLetter | None:
        return next((d for d in self.open_dead_letters() if d.id == dl_id), None)

    def resolve_dead_letter(self, dl_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE dead_letters SET resolved_at=? WHERE id=?", (time.time(), dl_id))
