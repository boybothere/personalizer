"""Start-of-run maintenance: interrupted-run recovery, then retention purge (and its WAL checkpoint), recorded
on the new run.

It runs only when the engine runs (research, search, retrieve, or `python -m engine purge`). Nothing runs while
the application is idle: no scheduler exists, so expired data stays until the next start.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from .claims.scope import redact_run
from .config import EngineConfig
from .failures import Code, explain
from .store import repo


def recover_interrupted(conn: sqlite3.Connection, cfg: EngineConfig, at: datetime | None = None) -> dict:
    """Runs left in 'running' by a crash or kill: mark them failed (stop reason 'interrupted') and give their stored
    text the same end-of-run redaction an ordinary run gets. Finished runs are never touched; a run younger than
    the threshold may still be live (another process) and is left alone."""
    now = at or repo.now()
    after = max(cfg.retention.interrupted_after_seconds, int(cfg.budget.max_wall_seconds) + 60)
    cutoff = repo.iso(now - timedelta(seconds=after))
    recovered = []
    for r in conn.execute("SELECT id, input_json FROM runs WHERE status='running' AND started_at < ? ORDER BY"
                          " started_at", (cutoff,)).fetchall():
        location = ((json.loads(r["input_json"] or "{}").get("input") or {}).get("location"))
        redacted = redact_run(conn, r["id"], now.date(), location)
        gate = {"gate": "run_finished", "passed": False, "code": Code.RUN_INTERRUPTED.value,
                "explanation": explain(Code.RUN_INTERRUPTED), "detail": f"recovered at {repo.iso(now)}"}
        changed = conn.execute("UPDATE runs SET status='failed', stop_reason='interrupted', gates_json=?,"
                               " finished_at=? WHERE id=? AND status='running'",
                               (json.dumps([gate]), repo.iso(now), r["id"])).rowcount
        if changed:
            repo.trace(conn, r["id"], "maintenance", "interrupted_run_recovered", reason=Code.RUN_INTERRUPTED.value,
                       data={"redaction": redacted})
            recovered.append(r["id"])
    return {"runs_recovered": len(recovered), "run_ids": recovered, "threshold_seconds": after}


def startup(conn: sqlite3.Connection, cfg: EngineConfig, at: datetime | None = None) -> dict:
    interrupted = recover_interrupted(conn, cfg, at)
    return {"interrupted": interrupted, "purge": repo.purge(conn, at)}  # purge checkpoints the redaction too


def record(conn: sqlite3.Connection, run_id: str, outcome: dict) -> None:
    """Keep the outcome with the run that triggered it (shown in metrics), instead of discarding it."""
    purge = outcome.get("purge", {})
    reason = "checkpoint_incomplete" if purge.get("checkpoint_complete") is False else "ok"
    repo.trace(conn, run_id, "maintenance", "startup", reason=reason, data=outcome)
