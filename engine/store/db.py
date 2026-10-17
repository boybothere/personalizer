"""SQLite connection for the engine database (separate from the prototype's data/personalizer.db)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import migrate


def connect(path: Path | str, *, upgrade: bool = True) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit; multi-statement work uses explicit transactions.
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")  # deleted/expired text is overwritten, not left in free pages
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    if upgrade:
        migrate(conn)
    return conn
