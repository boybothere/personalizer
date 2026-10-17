"""Engine command line.

  python -m engine db status|upgrade|downgrade --to N
  python -m engine retrieve URL [URL ...] [--label L] [--json]
  python -m engine summary RUN_ID [--trace]
  python -m engine search --purpose WHY [--phrase P] [--term T] [--any A] [--site S] [--exclude-site S]
                          [--fresh DAYS] [--max-results N] [--providers brave,tavily,exa] [--no-retrieve]
  python -m engine search --specs-file specs.json [...]
  python -m engine ledger            # this month's provider usage vs free-allowance caps
  python -m engine providers         # capabilities, terms-derived storage policy, pricing
  python -m engine tavily-usage      # Tavily's own account usage counter (read-only)
  python -m engine delete-run RUN_ID
  python -m engine purge
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import EngineConfig
from .failures import explain
from .observe import metrics
from .retrieval import Lead, Retriever
from .store import repo
from .store.db import connect
from .store.migrations import MIGRATIONS, current_version, migrate


def summary(conn, run_id: str, with_trace: bool = False) -> dict:
    run = repo.get_run(conn, run_id)
    if not run:
        raise SystemExit(f"no such run: {run_id}")
    out = {
        "run": {k: run[k] for k in ("id", "kind", "status", "stop_reason", "created_at", "finished_at")},
        "stop_explanation": explain(run["stop_reason"]),
        "gates": run["gates"],
        "metrics": metrics(conn, run_id),
        "urls": [dict(r) for r in conn.execute(
            """SELECT u.id, u.canonical_url, u.decision, u.reject_code,
                      (SELECT failure_code FROM fetch_attempts WHERE url_id=u.id ORDER BY id DESC LIMIT 1) AS final_code,
                      (SELECT COUNT(*) FROM fetch_attempts WHERE url_id=u.id AND outcome!='skipped') AS requests
               FROM urls u WHERE u.run_id=? ORDER BY u.id""", (run_id,))],
        "documents": [dict(r) for r in conn.execute(
            """SELECT id, evidence_kind, source_url, title, author, published_at, published_source, date_conflict,
                      text_chars, usable, failure_code, duplicate_of, substr(text, 1, 160) AS text_preview
               FROM documents WHERE run_id=? ORDER BY created_at""", (run_id,))],
    }
    if with_trace:
        out["trace"] = [dict(r) for r in conn.execute(
            "SELECT at, stage, action, ref_type, ref_id, reason FROM trace_events WHERE run_id=? ORDER BY id", (run_id,))]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    db = sub.add_parser("db")
    db.add_argument("action", choices=["status", "upgrade", "downgrade"])
    db.add_argument("--to", type=int)
    r = sub.add_parser("retrieve")
    r.add_argument("urls", nargs="+")
    r.add_argument("--label")
    r.add_argument("--json", action="store_true")
    s = sub.add_parser("summary")
    s.add_argument("run_id")
    s.add_argument("--trace", action="store_true")
    se = sub.add_parser("search")
    se.add_argument("--purpose")
    se.add_argument("--phrase", action="append", default=[])
    se.add_argument("--term", action="append", default=[])
    se.add_argument("--any", action="append", default=[])
    se.add_argument("--site", action="append", default=[])
    se.add_argument("--exclude-site", action="append", default=[])
    se.add_argument("--fresh", type=int)
    se.add_argument("--max-results", type=int, default=10)
    se.add_argument("--specs-file")
    se.add_argument("--providers")
    se.add_argument("--no-retrieve", action="store_true")
    se.add_argument("--label")
    se.add_argument("--json", action="store_true")
    sub.add_parser("ledger")
    sub.add_parser("providers")
    sub.add_parser("tavily-usage")
    d = sub.add_parser("delete-run")
    d.add_argument("run_id")
    sub.add_parser("purge")
    args = ap.parse_args(argv)

    cfg = EngineConfig.from_env()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "db":
        conn = connect(cfg.db_path, upgrade=False)
        if args.action == "status":
            print(json.dumps({"db": str(cfg.db_path), "version": current_version(conn),
                              "latest": MIGRATIONS[-1].version}))
        elif args.action == "upgrade":
            print(migrate(conn, args.to))
        else:
            if args.to is None:
                raise SystemExit("downgrade requires an explicit --to VERSION (it drops tables)")
            print(migrate(conn, args.to))
        return 0
    conn = connect(cfg.db_path)
    if args.cmd == "retrieve":
        run_id = asyncio.run(Retriever(conn, cfg).run([Lead(u) for u in args.urls], label=args.label))
        out = summary(conn, run_id)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            print(f"run {run_id}: {out['run']['status']} ({out['run']['stop_reason']})")
            for u in out["urls"]:
                print(f"  {u['decision']:8} {u['reject_code'] or u['final_code'] or 'OK':28} {u['canonical_url'][:90]}")
            for doc in out["documents"]:
                print(f"  doc {'usable' if doc['usable'] else doc['failure_code']:16} chars={doc['text_chars']:<6} "
                      f"date={doc['published_at']} ({doc['published_source']}) {doc['source_url'][:70]}")
        return 0 if out["run"]["status"] != "failed" else 1
    if args.cmd == "search":
        from .search.spec import SearchSpec
        from .search_run import search_run

        if args.specs_file:
            specs = [SearchSpec(**d) for d in json.loads(open(args.specs_file, encoding="utf-8").read())]
        else:
            if not args.purpose:
                raise SystemExit("--purpose is required (every query needs a reason)")
            specs = [SearchSpec(purpose=args.purpose, phrases=args.phrase, terms=args.term, any_of=args.any,
                                sites=args.site, exclude_sites=args.exclude_site, freshness_days=args.fresh,
                                max_results=args.max_results)]
        if args.providers:
            cfg.providers.enabled = [p.strip() for p in args.providers.split(",") if p.strip()]
        run_id = asyncio.run(search_run(conn, cfg, specs, retrieve=not args.no_retrieve, label=args.label))
        out = summary(conn, run_id)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            m = out["metrics"]
            print(f"run {run_id}: {out['run']['status']} ({out['run']['stop_reason']})")
            print("search:", json.dumps({k: v for k, v in m["search"].items() if k != "new_urls_by_query"}))
            for name, pm in m["providers"].items():
                print(f"  {name}: requests {pm['requests']['completed']}/{pm['requests']['attempted']} "
                      f"results {pm['results']['returned']} (off-target {pm['results']['off_target']}, "
                      f"exclusive {pm['results']['exclusive_urls']}) usable docs "
                      f"{pm['downstream']['usable_unique_documents']} cost est ${pm['cost']['estimated_list_usd']}"
                      f" reported {pm['cost']['provider_reported_usd']}")
            print("fetch:", json.dumps({k: m["fetch"][k] for k in ("attempts", "urls_retrieved", "urls_failed_by_code")}))
            print("extraction:", json.dumps({k: m["extraction"][k] for k in ("documents", "usable", "failed_by_code")}))
        return 0 if out["run"]["status"] != "failed" else 1
    if args.cmd == "ledger":
        from .search import ledger

        print(json.dumps(ledger.summary(conn, cfg.providers), indent=2))
        return 0
    if args.cmd == "providers":
        from .search.providers import PROVIDERS

        print(json.dumps({n: {"configured": bool(getattr(cfg.providers, f"{n}_api_key")),
                              "enabled": n in cfg.providers.enabled,
                              "capabilities": P.capabilities.__dict__, "terms": P.terms.__dict__,
                              "pricing": P.pricing.__dict__} for n, P in PROVIDERS.items()}, indent=2))
        return 0
    if args.cmd == "tavily-usage":
        import httpx

        if not cfg.providers.tavily_api_key:
            raise SystemExit("TAVILY_API_KEY not configured")
        r = httpx.get("https://api.tavily.com/usage", timeout=20,
                      headers={"Authorization": f"Bearer {cfg.providers.tavily_api_key}"})
        print(json.dumps({"http_status": r.status_code, "body": r.json() if r.status_code == 200 else r.text[:300]},
                         indent=2))
        return 0
    if args.cmd == "summary":
        print(json.dumps(summary(conn, args.run_id, args.trace), indent=2, default=str))
    elif args.cmd == "delete-run":
        print(json.dumps({"deleted": repo.delete_run(conn, args.run_id)}))
    elif args.cmd == "purge":
        print(json.dumps(repo.purge(conn)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
