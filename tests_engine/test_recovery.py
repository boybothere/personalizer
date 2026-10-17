"""Corrective pass: runs interrupted at different pipeline stages are recovered deterministically on the next start.

Synthetic data, offline fake web, temporary file databases only."""

import json
from datetime import timedelta

import pytest
from conftest import make_cfg
from world import World, page, run_world

from engine import maintenance
from engine.observe import metrics
from engine.store import repo
from engine.store.db import connect

PLACE = "Priya Raman spoke at a summit in Bengaluru."  # kept at storage level, removed by end-of-run redaction
IN_SCOPE = "Priya Raman, VP of Infrastructure at Nimbus Compute, spoke about chips."
HEALTH = "She was treated at a hospital last year."


def _world():
    return World(routes=[(lambda q, inc: True, ["https://nimbuscompute.example/team/priya-raman",
                                                "https://profiles.example/priya"])],
                 pages={"https://profiles.example/priya": page(" ".join(
                     [IN_SCOPE, PLACE, HEALTH] + ["Priya Raman is a member of the Open Compute Project."] * 4))})


def _bytes(d):
    return b"".join(f.read_bytes() for f in d.iterdir())


def _crash(monkeypatch, stage_fn):
    import engine.research_run as rr

    def crash(*a, **k):
        raise RuntimeError(f"simulated crash in {stage_fn}")

    if stage_fn == "origins.compute":
        monkeypatch.setattr(rr.origins, "compute", crash)
    elif stage_fn == "controller":
        async def boom(self):
            raise RuntimeError("simulated crash during research")
        monkeypatch.setattr(rr.AdaptiveController, "run", boom)
    else:
        monkeypatch.setattr(rr, stage_fn, crash)


@pytest.mark.parametrize("stage_fn", ["controller", "scrub_transient", "origins.compute", "resolve_run",
                                      "update_claims", "redact_run"])
def test_interrupted_run_is_marked_failed_and_redacted_at_next_start(tmp_path, monkeypatch, stage_fn):
    db = tmp_path / "i.db"
    conn = connect(db)
    done_conn, done_id = run_world(_world(), conn=conn, organization_domain="nimbuscompute.example")
    before = [tuple(r) for r in conn.execute("SELECT * FROM documents WHERE run_id=? ORDER BY id", (done_id,))]
    done_status = conn.execute("SELECT status, stop_reason, gates_json, finished_at FROM runs WHERE id=?",
                               (done_id,)).fetchone()
    with monkeypatch.context() as m:
        _crash(m, stage_fn)
        with pytest.raises(RuntimeError):
            run_world(_world(), conn=conn, organization_domain="nimbuscompute.example")
    crashed = conn.execute("SELECT id FROM runs WHERE status='running'").fetchone()[0]
    has_docs = conn.execute("SELECT COUNT(*) FROM documents WHERE run_id=?", (crashed,)).fetchone()[0] > 0
    conn.close()
    raw = _bytes(tmp_path)
    assert HEALTH.encode() not in raw  # storage-level content never reached disk, whatever the stage
    if has_docs:
        assert PLACE.encode() in raw  # claim-level content is still there until recovery

    conn = connect(db)
    cfg = make_cfg()
    early = maintenance.startup(conn, cfg)  # immediately: the run may still be live elsewhere, so it is left alone
    assert early["interrupted"]["runs_recovered"] == 0
    out = maintenance.startup(conn, cfg, at=repo.now() + timedelta(hours=1))
    assert out["interrupted"]["run_ids"] == [crashed] and out["purge"]["checkpoint_complete"] is True
    row = conn.execute("SELECT status, stop_reason, gates_json FROM runs WHERE id=?", (crashed,)).fetchone()
    assert (row["status"], row["stop_reason"]) == ("failed", "interrupted")
    assert json.loads(row["gates_json"])[0]["code"] == "RUN_INTERRUPTED"
    # completed run untouched
    assert [tuple(r) for r in conn.execute("SELECT * FROM documents WHERE run_id=? ORDER BY id", (done_id,))] == before
    assert tuple(conn.execute("SELECT status, stop_reason, gates_json, finished_at FROM runs WHERE id=?",
                              (done_id,)).fetchone()) == tuple(done_status)
    conn.close()
    raw = _bytes(tmp_path)
    assert PLACE.encode() not in raw and HEALTH.encode() not in raw
    assert IN_SCOPE.encode() in raw  # ordinary content preserved


def test_next_research_run_recovers_and_records_it(tmp_path, monkeypatch):
    conn = connect(tmp_path / "n.db")
    with monkeypatch.context() as m:
        _crash(m, "update_claims")
        with pytest.raises(RuntimeError):
            run_world(_world(), conn=conn, organization_domain="nimbuscompute.example")
    crashed = conn.execute("SELECT id FROM runs WHERE status='running'").fetchone()[0]
    conn.execute("UPDATE runs SET started_at=? WHERE id=?", (repo.iso(repo.now() - timedelta(hours=2)), crashed))
    _, rid = run_world(_world(), conn=conn, organization_domain="nimbuscompute.example")
    m = metrics(conn, rid)["maintenance"]
    assert m["interrupted"]["run_ids"] == [crashed]
    assert conn.execute("SELECT status FROM runs WHERE id=?", (crashed,)).fetchone()[0] == "failed"
    assert conn.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()[0] != "running"
    conn.close()


def test_recovery_threshold_never_below_the_run_wall_budget():
    cfg = make_cfg()
    cfg.retention.interrupted_after_seconds = 60
    cfg.budget.max_wall_seconds = 3600
    conn = connect(":memory:")
    rid = repo.create_run(conn, "research", {}, {}, run_days=30)
    conn.execute("UPDATE runs SET started_at=? WHERE id=?", (repo.iso(repo.now() - timedelta(minutes=30)), rid))
    out = maintenance.recover_interrupted(conn, cfg)
    assert out["runs_recovered"] == 0 and out["threshold_seconds"] == 3660
