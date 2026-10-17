"""Markdown rendering of a Report, with numbered citations back to evidence."""

from __future__ import annotations

from .models import Report


def to_markdown(r: Report) -> str:
    ref = {f.id: i + 1 for i, f in enumerate(r.findings)}

    def cite(ids: list[str]) -> str:
        return "".join(f"[{ref[i]}]" for i in ids if i in ref)

    req = r.request
    lines = [
        f"# Meeting brief: {req.name}" + (f" ({req.organization})" if req.organization else ""),
        "",
        f"**Meeting purpose:** {req.meeting_purpose}  ",
        f"**Generated:** {r.created_at:%Y-%m-%d %H:%M} UTC · mode: {r.mode} · "
        f"identity: {'resolved' if r.identity.resolved else 'NOT resolved'} ({r.identity.confidence.value})",
        "",
    ]
    for n in r.identity.notes:
        lines.append(f"> {n}")
    if r.identity.notes:
        lines.append("")

    def section(title: str, items, fmt):
        lines.append(f"## {title}")
        lines.extend(fmt(i) for i in items) if items else lines.append("_Nothing supported by the evidence._")
        lines.append("")

    section("Professional context", r.professional_context, lambda s: f"- {s.text} {cite(s.finding_ids)}")
    sug = lambda s: f"- {'(optional) ' if s.optional else ''}{s.text} {cite(s.finding_ids)}  \n  _Why:_ {s.rationale}"  # noqa: E731
    section("Conversation starters", r.conversation_starters, sug)
    section("Questions to ask", r.questions, sug)
    section("Optional common ground", r.common_ground, sug)
    if r.prep_notes:
        section("Preparation notes", r.prep_notes, sug)
    section("Unknowns (do not assume)", r.unknowns, lambda u: f"- {u}")
    if r.warnings:
        section("Warnings", r.warnings, lambda w: f"- {w}")

    lines += ["## Evidence", "",
              "| # | Finding | Category | Source | Published | Confidence | Identity | Disclosure | Used |",
              "|---|---|---|---|---|---|---|---|---|"]
    for f in r.findings:
        used = "yes" if f.suitable_for_personalization else f"no ({f.exclusion_reason})"
        claim = f.claim.replace("|", "\\|")
        lines.append(
            f"| {ref[f.id]} | {claim} | {f.category.value} | [{f.source_name[:50]}]({f.source_url}) | "
            f"{f.published_at or 'unknown'} | {f.confidence.value} ({f.confidence_score}) | "
            f"{f.identity_confidence.value} | {f.disclosure.value} | {used} |"
        )
    lines.append("")
    lines.append("### Supporting excerpts")
    for f in r.findings:
        extra = f" Corroborated by: {', '.join(f.corroborating_urls)}." if f.corroborating_urls else ""
        down = f" Downgraded: {'; '.join(f.downgrade_reasons)}." if f.downgrade_reasons else ""
        lines.append(f"{ref[f.id]}. “{f.excerpt}” — <{f.source_url}> (retrieved {f.retrieved_at:%Y-%m-%d}).{extra}{down}")
    lines.append("")
    if r.excluded:
        lines.append("### Excluded material")
        for e in r.excluded:
            lines.append(f"- {e.source_url} — {e.reason}")
        lines.append("")
    u = r.usage
    lines.append(f"_Usage: {sum(u.search_calls.values())} searches {dict(u.search_calls)}, {u.pages_fetched} pages fetched, "
                 f"{u.llm_calls} LLM calls ({u.llm_input_tokens} in / {u.llm_output_tokens} out tokens), "
                 f"est. cost ${u.estimated_cost_usd:.4f}._")
    lines.append("")
    lines.append("_Confidence levels are rule-based ordinal scores, not probabilities. Verify before relying on any finding._")
    return "\n".join(lines)
