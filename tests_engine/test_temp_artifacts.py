"""Corrective pass (plan §2.6.1): no unexpected files persist in the OS temporary directory or the database
directory after a run. The temporary directory is isolated for this test by pointing TMP/TEMP/TMPDIR at an empty
folder. Limitation: SQLite deletes its own temporary files when they close, so a check after the fact can only
detect files that persist; `temp_store = MEMORY` (asserted here) is what keeps row content out of them."""

import os
import tempfile

from world import World, page, run_world

from engine.store.db import connect


def test_research_run_leaves_no_files_in_an_isolated_os_temp_dir(tmp_path, monkeypatch):
    os_temp = tmp_path / "os_temp"
    db_dir = tmp_path / "db"
    os_temp.mkdir()
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(os_temp))
    monkeypatch.setattr(tempfile, "tempdir", None)  # re-read from the environment
    assert tempfile.gettempdir() == str(os_temp)

    conn = connect(db_dir / "t.db")
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY
    w = World(routes=[(lambda q, inc: True, ["https://nimbuscompute.example/team/priya-raman",
                                            "https://profiles.example/priya"])],
              pages={"https://profiles.example/priya": page(
                  "Priya Raman, VP of Infrastructure at Nimbus Compute, spoke about chips. " * 30)})
    _, rid = run_world(w, conn=conn, organization_domain="nimbuscompute.example")
    # a large sort that SQLite would otherwise spill to a temporary file
    conn.execute("CREATE TEMP TABLE big AS WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE"
                 " i < 200000) SELECT i, hex(randomblob(64)) AS b FROM n")
    assert conn.execute("SELECT COUNT(*) FROM (SELECT b FROM big ORDER BY b)").fetchone()[0] == 200000
    conn.execute("DROP TABLE big")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    assert os.listdir(os_temp) == []
    assert sorted(os.listdir(db_dir)) == ["t.db"]  # -wal/-shm removed when the last connection closes
