"""SQLite store for Config Push history and reusable command templates.

Two tables in one DB (default ~/.scng/config_history.db):
  - `history` — one row per push run (block + hosts + counts + timing +
    pointer to the on-disk transcript folder).
  - `templates` — reusable named command blocks, optionally tagged by
    vendor so the GUI templates panel can filter.

The module is pure stdlib; no Qt imports so the same code can be used
from a future CLI tool. Each public method opens its own short-lived
sqlite3 connection (matching the pattern in creds/schema.py — no real
pool, but threadsafe enough for the workload).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    label TEXT,
    command_block TEXT NOT NULL,
    hosts_json TEXT NOT NULL,
    ok_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    skip_count INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    transcript_dir TEXT
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT UNIQUE NOT NULL,
    command_block TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT 'generic',
    created TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_templates_vendor ON templates(vendor);
"""


class HistoryDBError(Exception):
    """Base class for history DB errors."""


class DuplicateTemplate(HistoryDBError):
    """Raised when saving a template with a label that already exists."""


class TemplateNotFound(HistoryDBError):
    """Raised when a template lookup returns no rows."""


@dataclass
class HistoryEntry:
    id: int
    timestamp: str
    label: Optional[str]
    command_block: str
    hosts: List[str]
    ok_count: int
    fail_count: int
    skip_count: int
    duration_s: float
    transcript_dir: Optional[str]


@dataclass
class TemplateEntry:
    id: int
    label: str
    command_block: str
    vendor: str
    created: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConfigHistoryDB:
    """Connection-per-call SQLite wrapper for history + templates."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".scng" / "config_history.db"
        self.db_path = db_path.expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ----------------------------------------------------------------------
    # History
    # ----------------------------------------------------------------------

    def record_run(
        self,
        *,
        command_block: str,
        hosts: List[str],
        ok_count: int,
        fail_count: int,
        skip_count: int,
        duration_s: float,
        label: Optional[str] = None,
        transcript_dir: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        """Insert a history row and return its id."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO history
                    (timestamp, label, command_block, hosts_json,
                     ok_count, fail_count, skip_count, duration_s,
                     transcript_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp or _now_iso(),
                    label,
                    command_block,
                    json.dumps(hosts),
                    ok_count,
                    fail_count,
                    skip_count,
                    duration_s,
                    transcript_dir,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def list_runs(self, limit: int = 50) -> List[HistoryEntry]:
        """Most recent runs first."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM history ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_history(row) for row in cur]

    def get_run(self, run_id: int) -> Optional[HistoryEntry]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM history WHERE id = ?", (run_id,))
            row = cur.fetchone()
            return _row_to_history(row) if row else None

    def search_runs(self, query: str, limit: int = 50) -> List[HistoryEntry]:
        """Substring search across label + command_block (case-insensitive)."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM history
                WHERE label LIKE ? COLLATE NOCASE
                   OR command_block LIKE ? COLLATE NOCASE
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (pattern, pattern, limit),
            )
            return [_row_to_history(row) for row in cur]

    def delete_run(self, run_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM history WHERE id = ?", (run_id,))
            conn.commit()

    # ----------------------------------------------------------------------
    # Templates
    # ----------------------------------------------------------------------

    def save_template(
        self,
        *,
        label: str,
        command_block: str,
        vendor: str = "generic",
    ) -> int:
        """Insert a new template. Raises DuplicateTemplate on label clash."""
        with self._connect() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO templates (label, command_block, vendor, created)
                    VALUES (?, ?, ?, ?)
                    """,
                    (label, command_block, vendor, _now_iso()),
                )
                conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError as exc:
                raise DuplicateTemplate(
                    f"Template label already exists: {label!r}"
                ) from exc

    def update_template(
        self,
        label: str,
        *,
        command_block: Optional[str] = None,
        vendor: Optional[str] = None,
    ) -> None:
        """Update an existing template by label."""
        sets, params = [], []
        if command_block is not None:
            sets.append("command_block = ?")
            params.append(command_block)
        if vendor is not None:
            sets.append("vendor = ?")
            params.append(vendor)
        if not sets:
            return
        params.append(label)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE templates SET {', '.join(sets)} WHERE label = ?",
                params,
            )
            conn.commit()
            if cur.rowcount == 0:
                raise TemplateNotFound(label)

    def list_templates(self, vendor: Optional[str] = None) -> List[TemplateEntry]:
        with self._connect() as conn:
            if vendor:
                cur = conn.execute(
                    "SELECT * FROM templates WHERE vendor = ? ORDER BY label",
                    (vendor,),
                )
            else:
                cur = conn.execute("SELECT * FROM templates ORDER BY vendor, label")
            return [_row_to_template(row) for row in cur]

    def get_template(self, label: str) -> Optional[TemplateEntry]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM templates WHERE label = ?", (label,))
            row = cur.fetchone()
            return _row_to_template(row) if row else None

    def delete_template(self, label: str) -> None:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM templates WHERE label = ?", (label,))
            conn.commit()
            if cur.rowcount == 0:
                raise TemplateNotFound(label)


def _row_to_history(row: sqlite3.Row) -> HistoryEntry:
    return HistoryEntry(
        id=row["id"],
        timestamp=row["timestamp"],
        label=row["label"],
        command_block=row["command_block"],
        hosts=json.loads(row["hosts_json"]),
        ok_count=row["ok_count"],
        fail_count=row["fail_count"],
        skip_count=row["skip_count"],
        duration_s=row["duration_s"],
        transcript_dir=row["transcript_dir"],
    )


def _row_to_template(row: sqlite3.Row) -> TemplateEntry:
    return TemplateEntry(
        id=row["id"],
        label=row["label"],
        command_block=row["command_block"],
        vendor=row["vendor"],
        created=row["created"],
    )
