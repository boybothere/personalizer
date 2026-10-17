"""Run metrics, derived from stored records only, plus quality gates and stage timing.

Because every number is a query over leads/urls/fetch_attempts/documents, metrics cannot disagree
with what actually happened. Search-stage metrics are reported as unavailable until the search
stage (migration 0002) exists.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from .failures import BLOCKED_CODES, BUDGET_CODES, NATURAL_STOPS, Code, explain
from .store import repo


def _one(conn: sqlite3.Connection, sql: str, *args) -> int:
    return conn.execute(sql, args).fetchone()[0] or 0


def _by(conn: sqlite3.Connection, sql: str, *args) -> dict[str, int]:
    return {str(k): v for k, v in conn.execute(sql, args).fetchall() if k is not None}


def metrics(conn: sqlite3.Connection, run_id: str) -> dict:
    r = run_id
    url_rejects = _by(conn, "SELECT reject_code, COUNT(*) FROM urls WHERE run_id=? AND decision='rejected'"
                            " GROUP BY reject_code", r)
    # final outcome per URL = its last attempt row (skipped rows have attempt_no 0, so order by id)
    final = _by(conn, """
        SELECT COALESCE(fa.failure_code, 'OK'), COUNT(*) FROM fetch_attempts fa
        WHERE fa.run_id=? AND fa.id = (SELECT MAX(id) FROM fetch_attempts WHERE url_id = fa.url_id)
        GROUP BY 1""", r)
    fetch_failures = {k: v for k, v in final.items() if k != "OK"}
    doc_fail = _by(conn, "SELECT failure_code, COUNT(*) FROM documents WHERE run_id=? AND usable=0 GROUP BY 1", r)
    blocked: dict[str, int] = {}
    for source in (url_rejects, fetch_failures, doc_fail):
        for k, v in source.items():
            if k in {c.value for c in BLOCKED_CODES}:
                blocked[k] = blocked.get(k, 0) + v
    stages = {row["stage"]: row["s"] for row in conn.execute(
        "SELECT stage, ROUND(SUM(seconds), 3) AS s FROM stage_events WHERE run_id=? GROUP BY stage", (r,))}
    usable_docs = _one(conn, "SELECT COUNT(*) FROM documents WHERE run_id=? AND usable=1", r)
    near_dupes = _one(conn, "SELECT COUNT(*) FROM documents WHERE run_id=? AND duplicate_of IS NOT NULL", r)
    accepted = _one(conn, "SELECT COUNT(*) FROM urls WHERE run_id=? AND decision='accepted'", r)
    leads = _one(conn, "SELECT COUNT(*) FROM leads WHERE run_id=?", r)
    unique_urls = _one(conn, "SELECT COUNT(*) FROM urls WHERE run_id=?", r)
    return {
        "search": search_metrics(conn, r),
        "planner": planner_metrics(conn, r),
        "identity": identity_metrics(conn, r),
        "claims": claim_metrics(conn, r),
        "maintenance": maintenance_metrics(conn, r),
        "providers": provider_metrics(conn, r),
        "leads": {
            "total": leads,
            "by_kind": _by(conn, "SELECT lead_kind, COUNT(*) FROM leads WHERE run_id=? GROUP BY 1", r),
            "duplicate_leads": _one(conn, "SELECT COUNT(*) FROM leads WHERE run_id=? AND is_duplicate=1", r),
            "with_snippet": _one(conn, "SELECT COUNT(*) FROM leads WHERE run_id=? AND snippet IS NOT NULL", r),
            "snippet_only": _one(conn, """SELECT COUNT(*) FROM leads l JOIN urls u ON u.id=l.url_id
                                         WHERE l.run_id=? AND l.snippet IS NOT NULL AND NOT EXISTS
                                         (SELECT 1 FROM documents d WHERE d.url_id=u.id AND d.usable=1)""", r),
        },
        "urls": {
            "unique_candidates": unique_urls,
            "accepted": accepted,
            "rejected": sum(url_rejects.values()),
            "rejected_by_code": url_rejects,
            "duplicate_rate": round(1 - unique_urls / leads, 3) if leads else None,
        },
        "fetch": {
            "attempts": _one(conn, "SELECT COUNT(*) FROM fetch_attempts WHERE run_id=? AND outcome!='skipped'", r),
            "retries": _one(conn, "SELECT COUNT(*) FROM fetch_attempts WHERE run_id=? AND outcome='retry'", r),
            "redirect_hops": _one(conn, "SELECT COUNT(*) FROM fetch_attempts WHERE run_id=? AND outcome='redirect'", r),
            "requests_with_http_response": _one(
                conn, "SELECT COUNT(*) FROM fetch_attempts WHERE run_id=? AND http_status IS NOT NULL", r),
            "http_2xx": _one(conn, "SELECT COUNT(*) FROM fetch_attempts WHERE run_id=? AND http_status BETWEEN 200 AND 299", r),
            "urls_retrieved": final.get("OK", 0),
            "urls_not_requested": _one(conn, """SELECT COUNT(DISTINCT url_id) FROM fetch_attempts fa WHERE run_id=?
                                              AND NOT EXISTS (SELECT 1 FROM fetch_attempts b WHERE b.url_id=fa.url_id
                                              AND b.outcome!='skipped')""", r),
            "urls_failed_by_code": fetch_failures,
            "robots_requests": _one(conn, "SELECT COUNT(*) FROM robots_checks WHERE run_id=?", r),
            "bytes": _one(conn, "SELECT SUM(bytes) FROM fetch_attempts WHERE run_id=?", r),
        },
        "extraction": {
            "documents": _one(conn, "SELECT COUNT(*) FROM documents WHERE run_id=?", r),
            "usable": usable_docs,
            "failed_by_code": doc_fail,
            "near_duplicates": near_dupes,
            "by_evidence_kind": _by(conn, "SELECT evidence_kind, COUNT(*) FROM documents WHERE run_id=? GROUP BY 1", r),
            "with_published_date": _one(conn, "SELECT COUNT(*) FROM documents WHERE run_id=? AND published_at IS NOT NULL", r),
            "date_sources": _by(conn, "SELECT published_source, COUNT(*) FROM documents WHERE run_id=? GROUP BY 1", r),
            "date_conflicts": _one(conn, "SELECT COUNT(*) FROM documents WHERE run_id=? AND date_conflict=1", r),
            "metadata_coverage_of_usable": {
                field: _one(conn, f"SELECT COUNT(*) FROM documents WHERE run_id=? AND usable=1 AND {field} IS NOT NULL"
                                  f" AND {field} != ''", r)
                for field in ("title", "author", "language", "canonical_link", "published_at", "site_name")},
            "usable_rate_of_retrieved_pages": _usable_rate(conn, r),
            "flags": _by(conn, """SELECT CASE WHEN instr(j.value, ':') > 0 THEN substr(j.value, 1, instr(j.value, ':') - 1)
                                         ELSE j.value END, COUNT(*)
                                  FROM documents d, json_each(d.flags_json) j WHERE d.run_id=? GROUP BY 1""", r),
        },
        "blocked_or_inaccessible": blocked,
        "validation": {
            "documents_passed": _one(conn, "SELECT COUNT(*) FROM documents WHERE run_id=? AND usable=1"
                                           " AND duplicate_of IS NULL", r),
            "evidence_sources_retained": None,  # validation stage (phase 7)
        },
        "time_by_stage_s": stages,
        "cost": {
            "estimated_usd": round(conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM provider_usage WHERE run_id=?",
                                                (r,)).fetchone()[0], 6),
            "by_provider": _by(conn, "SELECT provider, COUNT(*) FROM provider_usage WHERE run_id=? GROUP BY 1", r),
        },
    }


def _usable_rate(conn: sqlite3.Connection, r: str) -> float | None:
    """Share of successfully retrieved URLs that produced at least one usable document."""
    row = conn.execute("""SELECT COUNT(DISTINCT fa.url_id),
                                 COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM documents d WHERE d.url_id = fa.url_id
                                                                  AND d.usable = 1) THEN fa.url_id END)
                          FROM fetch_attempts fa WHERE fa.run_id = ? AND fa.outcome = 'success'""", (r,)).fetchone()
    return round(row[1] / row[0], 3) if row[0] else None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def maintenance_metrics(conn: sqlite3.Connection, r: str) -> dict:
    """Retention purge and WAL checkpoint outcome recorded when this run started (never discarded)."""
    row = conn.execute("SELECT reason, data_json FROM trace_events WHERE run_id=? AND stage='maintenance' AND"
                       " action='startup' ORDER BY id LIMIT 1", (r,)).fetchone()
    if not row:
        return {"available": False, "reason": "no start-of-run maintenance recorded for this run"}
    return {"available": True, "status": row["reason"], **json.loads(row["data_json"])}


def claim_metrics(conn: sqlite3.Connection, r: str) -> dict:
    if not _has_table(conn, "claims"):
        return {"available": False, "reason": "database schema predates claims (migration 0006)"}
    from .claims.store import claims_metrics

    out = claims_metrics(conn, r)
    if out["claims"] == 0 and out["last_action"] is None:
        return {"available": False, "reason": "no claim extraction for this run"}
    return {"available": True, "advisory": True, **out}


def identity_metrics(conn: sqlite3.Connection, r: str) -> dict:
    if not _has_table(conn, "identity_resolutions"):
        return {"available": False, "reason": "database schema predates identity resolution (migration 0005)"}
    from .identity.store import latest

    result = latest(conn, r)
    if result is None:
        return {"available": False, "reason": "no identity resolution for this run"}
    return {"available": True, "advisory": True, **result}


def planner_metrics(conn: sqlite3.Connection, r: str) -> dict:
    if not _has_table(conn, "research_objectives"):  # older schema (e.g. phase 1/2 databases)
        return {"available": False, "reason": "database schema predates the planner (migration 0003)"}
    objectives = conn.execute("SELECT key, status, status_reason, queries_run, relevant_docs, domains, profile_leads,"
                              " target_docs, target_domains FROM research_objectives WHERE run_id=? ORDER BY id",
                              (r,)).fetchall()
    if not objectives:
        return {"available": False, "reason": "no adaptive planner in this run"}
    last = conn.execute("SELECT identity_signal, decision_reason FROM planner_decisions WHERE run_id=?"
                        " ORDER BY id DESC LIMIT 1", (r,)).fetchone()
    return {
        "available": True,
        "steps": _one(conn, "SELECT COUNT(*) FROM planner_decisions WHERE run_id=? AND decision='continue'", r),
        "followups_added": _one(conn, "SELECT COALESCE(SUM(json_array_length(followups_json)),0) FROM planner_decisions"
                                      " WHERE run_id=?", r),
        "objectives": {o["key"]: {"status": o["status"], "reason": o["status_reason"], "queries": o["queries_run"],
                                  "relevant_docs": o["relevant_docs"], "domains": o["domains"],
                                  "profile_leads": o["profile_leads"],
                                  "targets": [o["target_docs"], o["target_domains"]]} for o in objectives},
        "coverage_gaps": [o["key"] for o in objectives if o["status"] != "covered"],
        "final_identity_signal": last["identity_signal"] if last else None,
        "stop_explanation": last["decision_reason"] if last else None,
    }


def search_metrics(conn: sqlite3.Connection, r: str) -> dict:
    if not _has_table(conn, "search_queries"):
        return {"available": False, "reason": "database schema predates the search layer (migration 0002)"}
    planned = _one(conn, "SELECT COUNT(*) FROM search_queries WHERE run_id=?", r)
    if planned == 0:
        return {"available": False, "reason": "no search stage in this run"}
    return {
        "available": True,
        "queries_planned": planned,
        "queries_attempted": _one(conn, "SELECT COUNT(*) FROM search_queries WHERE run_id=? AND status!='skipped'", r),
        "queries_completed": _one(conn, "SELECT COUNT(*) FROM search_queries WHERE run_id=? AND status='completed'", r),
        "queries_failed_by_code": _by(conn, "SELECT failure_code, COUNT(*) FROM search_queries WHERE run_id=?"
                                            " AND status='failed' GROUP BY 1", r),
        "queries_skipped_by_code": _by(conn, "SELECT failure_code, COUNT(*) FROM search_queries WHERE run_id=?"
                                             " AND status='skipped' GROUP BY 1", r),
        "provider_requests_including_retries": _one(conn, "SELECT SUM(attempts) FROM search_queries WHERE run_id=?", r),
        "results_returned": _one(conn, "SELECT COUNT(*) FROM search_results WHERE run_id=?", r),
        "results_off_target": _one(conn, "SELECT COUNT(*) FROM search_results WHERE run_id=? AND off_target=1", r),
        "unique_result_urls": _one(conn, "SELECT COUNT(DISTINCT url_key) FROM search_results WHERE run_id=?", r),
        "new_urls_by_query": [row[0] for row in conn.execute(
            "SELECT new_urls FROM search_queries WHERE run_id=? AND status='completed' ORDER BY id", (r,))],
        "results_withheld_by_terms": _one(conn, "SELECT COUNT(*) FROM search_results WHERE run_id=? AND withheld=1", r),
    }


def provider_metrics(conn: sqlite3.Connection, run_id: str | None = None) -> dict:
    """Per-provider cost and quality. With run_id=None, aggregates every stored run (provider comparison)."""
    if not _has_table(conn, "search_queries"):
        return {}
    where, args = ("q.run_id = ?", (run_id,)) if run_id else ("1=1", ())
    providers = [row[0] for row in conn.execute(f"SELECT DISTINCT provider FROM search_queries q WHERE {where}", args)]
    out = {}
    for p in providers:
        pa = args + (p,)
        q = conn.execute(f"""SELECT COUNT(*), SUM(status!='skipped'), SUM(status='completed'), SUM(attempts),
                                    AVG(CASE WHEN status!='skipped' THEN latency_ms END),
                                    MAX(CASE WHEN status!='skipped' THEN latency_ms END),
                                    SUM(results_returned), SUM(results_off_target), SUM(new_urls)
                             FROM search_queries q WHERE {where} AND provider=?""", pa).fetchone()
        res = f"FROM search_results r JOIN search_queries q ON q.id = r.query_id WHERE {where} AND r.provider=?"
        unique = _one(conn, f"SELECT COUNT(DISTINCT r.run_id || r.url_key) {res} AND r.off_target=0", *pa)
        exclusive = _one(conn, f"""SELECT COUNT(DISTINCT r.run_id || r.url_key) {res} AND r.off_target=0
            AND NOT EXISTS (SELECT 1 FROM search_results o WHERE o.run_id=r.run_id AND o.url_key=r.url_key
                            AND o.provider != r.provider AND o.off_target=0)""", *pa)
        # downstream outcomes for URLs this provider returned (shared credit when several providers found a URL)
        down = conn.execute(f"""
            SELECT COUNT(DISTINCT u.id), SUM(u.decision='rejected'),
                   COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM fetch_attempts fa WHERE fa.url_id=u.id
                                                    AND fa.outcome='success') THEN u.id END)
            FROM urls u WHERE u.id IN (SELECT DISTINCT uu.id FROM urls uu JOIN search_results r
                                        ON r.run_id=uu.run_id AND r.url_key=uu.url_key
                                        JOIN search_queries q ON q.id=r.query_id
                                        WHERE {where} AND r.provider=? AND r.off_target=0)""", pa).fetchone()
        docs = conn.execute(f"""
            SELECT COUNT(*), SUM(d.usable=1 AND d.duplicate_of IS NULL), SUM(d.usable=1 AND d.published_at IS NOT NULL),
                   SUM(d.evidence_kind='provider_extract')
            FROM documents d WHERE d.url_id IN (SELECT DISTINCT uu.id FROM urls uu JOIN search_results r
                                                 ON r.run_id=uu.run_id AND r.url_key=uu.url_key
                                                 JOIN search_queries q ON q.id=r.query_id
                                                 WHERE {where} AND r.provider=? AND r.off_target=0)""", pa).fetchone()
        rejected = _by(conn, f"""SELECT u.reject_code, COUNT(DISTINCT u.id) FROM urls u JOIN search_results r
                                 ON r.run_id=u.run_id AND r.url_key=u.url_key JOIN search_queries q ON q.id=r.query_id
                                 WHERE {where} AND r.provider=? AND u.decision='rejected' GROUP BY 1""", *pa)
        cost = conn.execute(f"""SELECT COALESCE(SUM(pu.units),0), MAX(pu.unit), COALESCE(SUM(pu.cost_usd),0),
                                       SUM(pu.reported_cost_usd)
                                FROM provider_usage pu JOIN runs q ON q.id = pu.run_id
                                WHERE {where.replace('q.run_id', 'pu.run_id')} AND pu.provider=?""", pa).fetchone()
        attempted = q[1] or 0
        usable = docs[1] or 0
        out[p] = {
            "requests": {"planned": q[0], "attempted": attempted, "completed": q[2] or 0,
                         "http_requests_including_retries": q[3] or 0,
                         "failed_by_code": _by(conn, f"SELECT failure_code, COUNT(*) FROM search_queries q WHERE {where}"
                                                     " AND provider=? AND status='failed' GROUP BY 1", *pa),
                         "skipped_by_code": _by(conn, f"SELECT failure_code, COUNT(*) FROM search_queries q WHERE {where}"
                                                      " AND provider=? AND status='skipped' GROUP BY 1", *pa)},
            "latency_ms": {"avg": round(q[4]) if q[4] is not None else None, "max": q[5]},
            "results": {"returned": q[6] or 0, "off_target": q[7] or 0, "unique_on_target_urls": unique,
                        "exclusive_urls": exclusive, "new_urls_credited": q[8] or 0,
                        "off_target_rate": round((q[7] or 0) / q[6], 3) if q[6] else None},
            "downstream": {"urls": down[0] or 0, "rejected_by_policy": rejected, "retrieved": down[2] or 0,
                           "documents": docs[0] or 0, "usable_unique_documents": usable,
                           "usable_with_date": docs[2] or 0, "provider_extract_documents": docs[3] or 0},
            "cost": {"units": round(cost[0], 6), "unit": cost[1], "estimated_list_usd": round(cost[2], 6),
                     "provider_reported_usd": None if cost[3] is None else round(cost[3], 6),
                     "estimated_usd_per_usable_document": round(cost[2] / usable, 6) if usable else None},
            "yield": {"usable_documents_per_request": round(usable / attempted, 3) if attempted else None,
                      "new_urls_per_request": round((q[8] or 0) / attempted, 3) if attempted else None},
        }
    return out


def evaluate_gates(conn: sqlite3.Connection, run_id: str, stop_code: Code | None) -> tuple[str, list[dict]]:
    """Return (status, gates). COMPLETED means all planned work was processed, never 'comprehensive'."""
    m = metrics(conn, run_id)
    leads, accepted = m["leads"]["total"], m["urls"]["accepted"]
    attempted = m["fetch"]["attempts"]
    # An accepted URL is processed when its *final* attempt row is a definitive outcome: success, failure,
    # or a skip for a substantive reason (robots, policy). Budget/cancel skips and no row at all = unprocessed.
    stop_codes = ",".join(f"'{c.value}'" for c in BUDGET_CODES | {Code.CANCELLED})
    unprocessed = _one(conn, f"""
        SELECT COUNT(*) FROM urls u
        LEFT JOIN fetch_attempts fa ON fa.id = (SELECT MAX(id) FROM fetch_attempts WHERE url_id = u.id)
        WHERE u.run_id=? AND u.decision='accepted'
          AND (fa.id IS NULL OR fa.outcome IN ('retry', 'redirect')
               OR (fa.outcome='skipped' AND fa.failure_code IN ({stop_codes}))
               OR (fa.outcome='failure' AND fa.failure_code IN ({stop_codes})))""", run_id)
    run = conn.execute("SELECT input_json, kind FROM runs WHERE id=?", (run_id,)).fetchone()
    retrieve = json.loads(run[0]).get("retrieve", True) if run and run[0] else True
    s = m["search"]
    gates = []
    essential = {"has_leads", "usable_documents", "some_fetch_succeeded", "search_succeeded", "identity_resolved"}
    if run and run["kind"] == "research":
        # the planner finishing by design is not "unprocessed work"; limits and faults are
        gates.append(_gate("identity_resolved", stop_code != Code.IDENTITY_UNRESOLVED, Code.IDENTITY_UNRESOLVED,
                           f"provisional identity signal at stop: {m['planner'].get('final_identity_signal')}"))
        if stop_code != Code.IDENTITY_UNRESOLVED:
            gates.append(_gate("planner_finished", stop_code in NATURAL_STOPS, stop_code or Code.WORK_UNPROCESSED,
                               f"stop reason: {stop_code.value if stop_code else 'none'}"))
        if stop_code in NATURAL_STOPS or stop_code == Code.IDENTITY_UNRESOLVED:
            stop_code = None  # recorded by the gates above
    if s["available"]:
        failures = {**s["queries_failed_by_code"], **s["queries_skipped_by_code"]}
        worst = max(failures, key=failures.get) if failures else None
        gates.append(_gate("search_succeeded", s["queries_completed"] > 0,
                           Code(worst) if worst else Code.NO_LEADS,
                           f"{s['queries_completed']} of {s['queries_planned']} queries completed"))
        stop_or_guard = {c.value for c in BUDGET_CODES} | {Code.CANCELLED.value, Code.SEARCH_FREE_TIER_GUARD.value}
        unrun = sum(v for k, v in s["queries_skipped_by_code"].items() if k in stop_or_guard)
        gates.append(_gate("all_queries_processed", unrun == 0, Code.WORK_UNPROCESSED,
                           f"{unrun} queries not sent because of budget, cancellation or the free-tier guard"))
    gates += [_gate("has_leads", leads > 0, Code.NO_LEADS, f"{leads} leads")]
    if retrieve:
        gates += [
            _gate("usable_documents", m["extraction"]["usable"] > 0, Code.NO_USABLE_DOCUMENTS,
                  f"{m['extraction']['usable']} usable of {m['extraction']['documents']} documents"),
            _gate("some_fetch_succeeded", accepted == 0 or attempted == 0 or m["fetch"]["urls_retrieved"] > 0,
                  Code.ALL_FETCHES_FAILED, f"{m['fetch']['urls_retrieved']} of {accepted} accepted URLs retrieved"),
        ]
        gates.append(_gate("all_work_processed", unprocessed == 0 and stop_code is None,
                           stop_code or Code.WORK_UNPROCESSED,
                           f"{unprocessed} accepted URLs not processed"
                           + (f"; stopped: {stop_code.value}" if stop_code else "")))
    elif stop_code:
        gates.append(_gate("all_work_processed", False, stop_code, f"stopped: {stop_code.value}"))
    failed = {g["gate"] for g in gates if not g["passed"]}
    if stop_code == Code.CANCELLED:
        status = "cancelled"  # user decision; partial results remain inspectable, gates still reported
    elif failed & essential:
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "completed"
    return status, gates


def _gate(name: str, passed: bool, code: Code, detail: str) -> dict:
    return {"gate": name, "passed": passed, "code": None if passed else code.value,
            "explanation": None if passed else explain(code), "detail": detail}


@contextmanager
def stage(conn: sqlite3.Connection, run_id: str, name: str):
    event = repo.stage_start(conn, run_id, name)
    start = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException as exc:
        outcome = f"error:{type(exc).__name__}"
        raise
    finally:
        repo.stage_end(conn, event, time.monotonic() - start, outcome)
