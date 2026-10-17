"""Command line: python -m personalizer research --name ... --purpose ...  |  python -m personalizer serve"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Settings
from .models import Category, ResearchRequest
from .pipeline import Pipeline
from .report import to_markdown
from .safety import PolicyViolation
from .store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="personalizer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("research", help="research a person and print a brief")
    r.add_argument("--name", required=True)
    r.add_argument("--org")
    r.add_argument("--org-domain")
    r.add_argument("--role")
    r.add_argument("--location")
    r.add_argument("--profile-url")
    r.add_argument("--purpose", required=True)
    r.add_argument("--me", help="your own role/interests, for genuine common ground")
    r.add_argument("--exclude", nargs="*", default=[], choices=[c.value for c in Category])
    r.add_argument("--no-personal", action="store_true", help="exclude personal interests")
    r.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    r.add_argument("--out", type=Path, help="write output to this file")
    r.add_argument("--no-save", action="store_true", help="do not persist the report")

    s = sub.add_parser("serve", help="run the web UI and API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    args = ap.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("personalizer.api:app", host=args.host, port=args.port)
        return 0

    req = ResearchRequest(
        name=args.name, organization=args.org, organization_domain=args.org_domain, role=args.role,
        location=args.location, profile_url=args.profile_url, meeting_purpose=args.purpose,
        requester_context=args.me, excluded_categories=[Category(c) for c in args.exclude],
        allow_personal_interests=not args.no_personal,
    )
    store = Store(settings.db_path, settings.retention_days)
    if store.is_opted_out(req.name):
        print("This person has asked not to be researched.", file=sys.stderr)
        return 3
    try:
        report = Pipeline.from_settings(settings).run(req)
    except PolicyViolation as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    if not args.no_save:
        store.save(report)
    text = report.model_dump_json(indent=2) if args.json else to_markdown(report)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} (report id {report.id})")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
