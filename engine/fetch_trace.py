"""Per-URL fetch trace: policy decision, robots.txt, every attempt (status, pinned IP, timing, failure),
and what extraction produced. Runs in an in-memory database: nothing is stored, no existing database
is opened or migrated.
"""

from __future__ import annotations

import asyncio
import json

from .config import EngineConfig
from .failures import explain
from .observe import metrics
from .retrieval import Lead, Retriever
from .store import repo
from .store.db import connect


def trace(urls: list[str], cfg: EngineConfig, **retriever_kw) -> dict:
    conn = connect(":memory:")
    run_id = asyncio.run(Retriever(conn, cfg, **retriever_kw).run([Lead(u) for u in urls], label="fetch-trace"))
    run = repo.get_run(conn, run_id)
    robots = {r["origin"]: dict(r) for r in conn.execute("SELECT origin, http_status, result, failure_code, detail,"
                                                         " crawl_delay FROM robots_checks WHERE run_id=?", (run_id,))}
    out = []
    for lead in conn.execute("SELECT url, url_id, is_duplicate FROM leads WHERE run_id=? ORDER BY id", (run_id,)):
        u = conn.execute("SELECT * FROM urls WHERE id=?", (lead["url_id"],)).fetchone()
        entry = {"input": lead["url"], "duplicate_of_earlier_input": bool(lead["is_duplicate"]),
                 "canonical": u["canonical_url"], "policy": u["decision"],
                 "policy_code": u["reject_code"], "policy_explanation": explain(u["reject_code"])}
        if not lead["is_duplicate"]:
            origin = "/".join(u["canonical_url"].split("/")[:3]).lower()
            entry["robots"] = robots.get(origin)
            entry["attempts"] = [{k: a[k] for k in ("attempt_no", "request_url", "outcome", "http_status",
                                                    "connected_ip", "content_type", "bytes", "elapsed_ms",
                                                    "failure_code", "detail")}
                                 for a in conn.execute("SELECT * FROM fetch_attempts WHERE url_id=? ORDER BY id",
                                                       (u["id"],))]
            final = entry["attempts"][-1]["failure_code"] if entry["attempts"] else None
            entry["outcome"] = final or ("OK" if entry["attempts"] else u["reject_code"])
            entry["outcome_explanation"] = explain(final) if final else None
            entry["documents"] = [
                {"kind": d["evidence_kind"], "source_url": d["source_url"], "usable": bool(d["usable"]),
                 "failure_code": d["failure_code"], "failure_explanation": explain(d["failure_code"]),
                 "text_chars": d["text_chars"], "title": d["title"], "author": d["author"], "language": d["language"],
                 "site_name": d["site_name"], "canonical_link": d["canonical_link"],
                 "published_at": d["published_at"], "published_source": d["published_source"],
                 "flags": json.loads(d["flags_json"]) if d["flags_json"] else [],
                 "text_preview": (d["text"] or "")[:200]}
                for d in conn.execute("SELECT * FROM documents WHERE url_id=? ORDER BY created_at LIMIT 5", (u["id"],))]
        out.append(entry)
    m = metrics(conn, run_id)
    return {"run_status": run["status"], "stop_reason": run["stop_reason"], "urls": out,
            "summary": {"fetch_attempts": m["fetch"]["attempts"], "http_2xx": m["fetch"]["http_2xx"],
                        "urls_retrieved": m["fetch"]["urls_retrieved"], "usable_documents": m["extraction"]["usable"],
                        "usable_rate_of_retrieved_pages": m["extraction"]["usable_rate_of_retrieved_pages"],
                        "metadata_coverage_of_usable": m["extraction"]["metadata_coverage_of_usable"],
                        "blocked_or_inaccessible": m["blocked_or_inaccessible"],
                        "extraction_failures": m["extraction"]["failed_by_code"]}}


def render(t: dict) -> str:
    lines = [f"run: {t['run_status']} ({t['stop_reason']})"]
    for e in t["urls"]:
        lines.append(f"\n{e['input']}")
        if e["duplicate_of_earlier_input"]:
            lines.append("  duplicate of an earlier input (same canonical URL): not fetched again")
            continue
        lines.append(f"  policy: {e['policy']}" + (f" {e['policy_code']}: {e['policy_explanation']}" if e["policy_code"] else ""))
        if e.get("robots"):
            r = e["robots"]
            lines.append(f"  robots.txt: HTTP {r['http_status']} -> {r['result']}" + (f" ({r['failure_code']})" if r["failure_code"] else ""))
        for a in e.get("attempts", []):
            lines.append(f"  attempt {a['attempt_no']}: {a['outcome']:8} HTTP {a['http_status']} via {a['connected_ip']} "
                         f"{a['bytes']}B {a['elapsed_ms']}ms {a['failure_code'] or ''} {a['detail'] or ''}".rstrip())
        lines.append(f"  outcome: {e.get('outcome')}" + (f": {e['outcome_explanation']}" if e.get("outcome_explanation") else ""))
        for d in e.get("documents", []):
            if d["usable"]:
                lines.append(f"  document: usable, {d['text_chars']} chars; title={d['title']!r}; author={d['author']!r}; "
                             f"lang={d['language']}; published={d['published_at']} ({d['published_source']}); "
                             f"canonical={d['canonical_link']}; flags={d['flags']}")
            else:
                lines.append(f"  document: NOT usable: {d['failure_code']}: {d['failure_explanation']}")
    lines.append("\nsummary: " + json.dumps(t["summary"]))
    return "\n".join(lines)
