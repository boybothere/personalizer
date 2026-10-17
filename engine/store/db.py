"""SQLite connection for the engine database (separate from the prototype's data/personalizer.db)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import MIGRATIONS, current_version, migrate


class SchemaOutdated(RuntimeError):
    pass


def connect(path: Path | str, *, upgrade: bool | str = "if_new") -> sqlite3.Connection:
    """Open the engine database.

    upgrade="if_new" (default): migrate only a brand-new, empty database. An existing database on an older
    schema is never migrated implicitly (it may hold research records); `python -m engine db upgrade` does
    that explicitly. upgrade=True migrates unconditionally; upgrade=False never migrates.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit; multi-statement work uses explicit transactions.
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")  # deleted/expired text is overwritten, not left in free pages
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    version = current_version(conn)
    head = MIGRATIONS[-1].version
    if upgrade is True or (upgrade == "if_new" and version == 0):
        migrate(conn)
    elif upgrade == "if_new" and version < head:
        conn.close()
        raise SchemaOutdated(f"{path} is at schema v{version}, code expects v{head}. Existing records are never "
                             f"migrated implicitly; run `python -m engine db upgrade` after taking a copy.")
    return conn
