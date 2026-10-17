"""Start-of-run maintenance: retention purge (and its WAL checkpoint), recorded on the new run.

It runs only when the engine runs (research, search, retrieve, or `python -m engine purge`). Nothing runs while
the application is idle: no scheduler exists, so expired data stays until the next start.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .config import EngineConfig
from .store import repo


def startup(conn: sqlite3.Connection, cfg: EngineConfig, at: datetime | None = None) -> dict:
    return {"purge": repo.purge(conn, at)}


def record(conn: sqlite3.Connection, run_id: str, outcome: dict) -> None:
    """Keep the outcome with the run that triggered it (shown in metrics), instead of discarding it."""
    purge = outcome.get("purge", {})
    reason = "checkpoint_incomplete" if purge.get("checkpoint_complete") is False else "ok"
    repo.trace(conn, run_id, "maintenance", "startup", reason=reason, data=outcome)
