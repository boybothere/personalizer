"""Offline evaluation over the synthetic corpus.

    python eval/run_eval.py            # heuristic mode, no keys, no cost
    python eval/run_eval.py --llm      # also runs Claude extraction/synthesis (needs ANTHROPIC_API_KEY; costs money)

Writes eval/results.json, eval/RESULTS.md and example reports under examples/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import dataset  # noqa: E402

from personalizer.config import Settings  # noqa: E402
from personalizer.extract import verify_excerpt  # noqa: E402
from personalizer.fetch import FixtureFetcher, html_to_text  # noqa: E402
from personalizer.models import Level, Report, ResearchRequest  # noqa: E402
from personalizer.pipeline import Pipeline  # noqa: E402
from personalizer.providers import FixtureProvider  # noqa: E402
from personalizer.report import to_markdown  # noqa: E402
from personalizer.safety import PolicyViolation  # noqa: E402


def make_pipeline(llm: bool = False) -> Pipeline:
    corpus = {"pages": dataset.PAGES}
    settings = Settings(search_providers=["fixture"], llm_provider="anthropic" if llm else "none",
                        extra_reputable_domains=dataset.EXTRA_REPUTABLE, max_fetches=20,
                        results_per_query=20)
    if llm:
        import os

        settings.model = os.environ.get("PERSONALIZER_MODEL", settings.model)
    return Pipeline(settings, [FixtureProvider(corpus)], FixtureFetcher(dataset.PAGES),
                    today=date.fromisoformat(dataset.TODAY))


def brief_text(r: Report) -> str:
    parts = [s.text for s in r.professional_context]
    for field in ("conversation_starters", "questions", "common_ground", "prep_notes"):
        parts += [s.text + " " + s.rationale for s in getattr(r, field)]
    return " ".join(parts).lower()


def evaluate_case(case: dict, report: Report) -> dict:
    gold_key = case.get("gold_key", case["id"])
    gold = {url: p.get("gold", {}).get(gold_key) for url, p in dataset.PAGES.items()}
    checks: dict[str, bool] = {}

    # source attribution: excerpt present verbatim in the cited source
    attributed = 0
    for f in report.findings:
        page = dataset.PAGES.get(f.source_url)
        if page and verify_excerpt(f.excerpt, html_to_text(page["html"])[1]):
            attributed += 1

    # identity decisions on retrieved documents with a gold label
    id_total = id_correct = id_false_accepts = 0
    for d in report.sources:
        g = gold.get(d.url)
        if d.fetch_status != "ok" or not g:
            continue
        predicted = d.identity.level != Level.LOW
        id_total += 1
        id_correct += predicted == g["same_person"]
        id_false_accepts += predicted and not g["same_person"]

    suitable = [f for f in report.findings if f.suitable_for_personalization]
    wrong_person = [f for f in suitable if not (gold.get(f.source_url) or {}).get("same_person", False)]
    top = sorted(suitable, key=lambda f: -f.rank_score)[:5]
    relevant_top = [f for f in top if (gold.get(f.source_url) or {}).get("relevant", False)]

    finding_text = " ".join(f"{f.claim} {f.excerpt}" for f in report.findings).lower()
    brief = brief_text(report)
    leaks = [s for s in case.get("must_not_appear", []) if s.lower() in finding_text or s.lower() in brief]
    brief_leaks = [s for s in case.get("brief_must_not_appear", []) if s.lower() in brief]
    checks["no forbidden content"] = not leaks and not brief_leaks
    cg = " ".join(s.text for s in report.common_ground).lower()
    if "expect_common_ground" in case:
        exp = case["expect_common_ground"]
        checks["common ground as expected"] = all(e in cg for e in exp) and (bool(exp) or not any(
            f.category.value == "disclosed_hobby" for s in report.common_ground for f in report.findings if f.id in s.finding_ids))
    if "expect_findings_from" in case:
        used = {f.source_url for f in suitable}
        checks["expected sources used"] = all(u in used for u in case["expect_findings_from"])
    if "expect_warning" in case:
        checks["expected warning raised"] = any(case["expect_warning"] in w for w in report.warnings)
    if case.get("expect_no_findings"):
        checks["no findings fabricated"] = not report.findings and not report.professional_context
    if "expect_identity_resolved" in case:
        checks["identity resolution as expected"] = report.identity.resolved == case["expect_identity_resolved"]
    cited_ok = all(
        i in {f.id for f in suitable}
        for items in (report.professional_context, report.conversation_starters, report.questions,
                      report.common_ground, report.prep_notes)
        for s in items for i in s.finding_ids
    )
    checks["every brief item cites suitable evidence"] = cited_ok

    return {
        "case": case["id"],
        "findings": len(report.findings),
        "suitable_findings": len(suitable),
        "attribution": [attributed, len(report.findings)],
        "identity": [id_correct, id_total],
        "identity_false_accepts": id_false_accepts,
        "wrong_person_suitable": len(wrong_person),
        "relevant_in_top5": [len(relevant_top), len(top)],
        "forbidden_leaks": leaks + brief_leaks,
        "excluded": len(report.excluded),
        "cost_usd": report.usage.estimated_cost_usd,
        "llm_tokens": [report.usage.llm_input_tokens, report.usage.llm_output_tokens],
        "checks": checks,
    }


def ratio(pairs: list[list[int]]) -> float | None:
    num, den = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    return round(num / den, 3) if den else None


def run(llm: bool = False, write_examples: bool = True) -> dict:
    pipeline = make_pipeline(llm)
    results = []
    for case in dataset.CASES:
        req = ResearchRequest(**case["request"])
        try:
            report = pipeline.run(req)
        except PolicyViolation as exc:
            results.append({"case": case["id"], "refused": True, "reason": str(exc),
                            "checks": {"refused as expected": bool(case.get("expect_refusal"))}})
            continue
        res = evaluate_case(case, report)
        if case.get("expect_refusal"):
            res["checks"]["refused as expected"] = False
        results.append(res)
        if write_examples:
            out = ROOT / "examples"
            out.mkdir(exist_ok=True)
            suffix = "-llm" if llm else ""
            (out / f"{case['id']}{suffix}.md").write_text(to_markdown(report), encoding="utf-8")
            (out / f"{case['id']}{suffix}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")

    scored = [r for r in results if not r.get("refused")]
    suitable_total = sum(r["suitable_findings"] for r in scored)
    summary = {
        "mode": "llm" if llm else "heuristic",
        "source_attribution_accuracy": ratio([r["attribution"] for r in scored]),
        "identity_match_accuracy": ratio([r["identity"] for r in scored]),
        "identity_false_accepts": sum(r["identity_false_accepts"] for r in scored),
        "false_positive_rate": round(
            sum(r["wrong_person_suitable"] + len(r["forbidden_leaks"]) for r in scored) / suitable_total, 3
        ) if suitable_total else 0.0,
        "relevance_precision_at_5": ratio([r["relevant_in_top5"] for r in scored]),
        "checks_passed": sum(all(r["checks"].values()) for r in results),
        "cases": len(results),
        "avg_cost_usd_per_run": round(sum(r.get("cost_usd", 0) for r in scored) / max(1, len(scored)), 4),
    }
    return {"summary": summary, "cases": results}


def to_md(res: dict) -> str:
    s = res["summary"]
    lines = [f"# Evaluation results ({s['mode']} mode)", "",
             "Synthetic corpus: `eval/dataset.py`. Regenerate with `python eval/run_eval.py`.", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in s.items() if k != "mode"]
    lines += ["", "## Per case", ""]
    for r in res["cases"]:
        status = "PASS" if all(r["checks"].values()) else "FAIL"
        lines.append(f"### {r['case']}: {status}")
        for k, v in r["checks"].items():
            lines.append(f"- [{'x' if v else ' '}] {k}")
        if r.get("refused"):
            lines.append(f"- refused: {r['reason']}")
        else:
            lines.append(f"- findings {r['findings']} (suitable {r['suitable_findings']}), excluded items {r['excluded']}, "
                         f"attribution {r['attribution'][0]}/{r['attribution'][1]}, identity {r['identity'][0]}/{r['identity'][1]}, "
                         f"relevant in top-5 {r['relevant_in_top5'][0]}/{r['relevant_in_top5'][1]}")
            if r["forbidden_leaks"]:
                lines.append(f"- LEAKED: {r['forbidden_leaks']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="use Claude (requires ANTHROPIC_API_KEY; incurs cost)")
    args = ap.parse_args()
    dataset.build()
    res = run(llm=args.llm)
    name = "results-llm" if args.llm else "results"
    (ROOT / "eval" / f"{name}.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    (ROOT / "eval" / f"{name.upper().replace('-', '_')}.md").write_text(to_md(res), encoding="utf-8")
    print(json.dumps(res["summary"], indent=2))
    failed = [r["case"] for r in res["cases"] if not all(r["checks"].values())]
    print("failed cases:", failed or "none")
