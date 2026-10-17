"""Adaptive research controller (phase 3): picks each next query from measured signals and decides when
to stop. Deterministic: the same inputs and the same responses produce the same decisions.

Every step writes a planner_decisions row: candidate scores, why this objective and query were chosen,
what the query contributed, which follow-ups it triggered, and why the run continued or stopped.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .budget import BudgetExceeded, RunBudget
from .config import EngineConfig
from .failures import BUDGET_CODES, Code
from .fetch.client import Fetcher
from .identity_signal import assess, org_slug
from .plan import Objective, ResearchInput, Variant
from .retrieval import Retriever
from .search.runner import SearchRunner
from .search.spec import SearchSpec
from .store import repo

STOP_LIMIT_CODES = BUDGET_CODES | {Code.CANCELLED}
DISCOVERY_BOOST = 1.6
PROFILE_REJECTS = {Code.URL_PLATFORM_DISCOVERY_ONLY.value, Code.URL_PLATFORM_CONNECTOR_ONLY.value,
                   Code.URL_PLATFORM_UNAVAILABLE.value}


@dataclass
class Contribution:
    results: int = 0
    duplicates: int = 0
    off_target: int = 0
    new_urls: int = 0
    blocked: int = 0
    fetched_usable: int = 0
    relevant_docs: list[str] = field(default_factory=list)  # newly found relevant documents (new evidence)
    relevant_known: list[str] = field(default_factory=list)  # relevant documents found earlier, returned again
    known_profile_leads: list[str] = field(default_factory=list)
    weak_docs: list[str] = field(default_factory=list)
    irrelevant_docs: list[str] = field(default_factory=list)
    near_duplicates: int = 0
    new_domains: list[str] = field(default_factory=list)
    profile_leads: list[str] = field(default_factory=list)
    query_status: str = "completed"
    failure_code: str | None = None

    def gain(self, discovery_only: bool) -> bool:
        return bool(self.relevant_docs) or (discovery_only and bool(self.profile_leads))

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("relevant_docs", "relevant_known", "known_profile_leads", "weak_docs", "irrelevant_docs",
                  "profile_leads"):
            d[k] = len(d[k])
        return d


class AdaptiveController:
    def __init__(self, conn: sqlite3.Connection, cfg: EngineConfig, run_id: str, budget: RunBudget,
                 runner: SearchRunner, retriever: Retriever, fetcher: Fetcher, inp: ResearchInput,
                 objectives: list[Objective]):
        self.conn, self.cfg, self.run_id, self.budget = conn, cfg, run_id, budget
        self.runner, self.retriever, self.fetcher, self.inp = runner, retriever, fetcher, inp
        self.objectives = objectives
        self.s = cfg.planner
        self.step = 0
        self.no_gain_streak = 0
        self.fail_streak = 0
        self.seen_keys: set[str] = set()
        self.supported: dict[str, str] = {}  # doc id -> registrable domain (run-wide)
        self.official_supported = False
        self.weak_seen = 0
        self.discovered_domain: str | None = None
        self.handles_seen: set[str] = set()
        self.key_docs: dict[str, list[str]] = {}  # url_key -> relevant doc ids (credit for repeat results)
        self.key_profiles: set[str] = set()  # url_keys that are relevant profile leads
        self.disambiguation_added = False
        for o in objectives:
            self._save_objective(o, insert=True)

    # ------------------------------------------------------------------ identity

    @property
    def identity_status(self) -> str:
        domains = set(self.supported.values())
        if self.official_supported or (len(self.supported) >= self.s.identity_target_docs
                                       and len(domains) >= self.s.identity_target_domains):
            return "supported"
        if self.supported:
            return "partial"  # some support, not yet enough documents/domains
        return "weak" if self.weak_seen else "unknown"

    def _identity_gate(self) -> bool:
        ident = self._obj("identity")
        return ident is not None and not ident.terminal and self.identity_status != "supported"

    # ------------------------------------------------------------------ main loop

    async def run(self) -> Code:
        while True:
            stop, why = self._check_stop()
            if stop:
                self._decision(None, None, [], None, None, "stop", f"{stop.value}: {why}")
                return stop
            obj, scores, reason = self._select()
            variant = obj.next_variant()
            obj.used.add(variant.signature)
            obj.status = "active"
            self.step += 1
            before = set(self.seen_keys)
            leads = await self.runner.execute([variant.spec])
            qids = list(self.runner.last_query_ids)
            fetchable = [l for l in leads if l.url]
            if fetchable and not self.runner.stop:
                await self.retriever.process(self.run_id, fetchable, self.budget, self.fetcher)
            c = self._measure(qids, before)
            if c.query_status != "completed":
                # the provider never answered this query: it did not test the objective, so the variant
                # is not consumed and the objective is not penalised (provider faults are tracked separately)
                obj.used.discard(variant.signature)
                self.fail_streak = self.fail_streak + 1 if c.query_status == "failed" else self.fail_streak
                if obj.status == "active":
                    self._set_status(obj, "pending", f"query {c.query_status}: {c.failure_code}")
                self._decision(obj, variant, qids, scores, c, "continue",
                               f"query {c.query_status} ({c.failure_code}); variant kept for retry. Provider "
                               f"failure streak {self.fail_streak}/{self.s.max_provider_failures}", reason)
                continue
            followups = self._update(obj, c)
            cont_reason = self._continue_reason(obj, c)
            self._decision(obj, variant, qids, scores, c, "continue", cont_reason, reason, followups)

    # ------------------------------------------------------------------ stopping

    def _check_stop(self) -> tuple[Code | None, str]:
        if repo.cancel_requested(self.conn, self.run_id):
            self.budget.cancelled = True
        try:
            self.budget.check()
        except BudgetExceeded as exc:
            return exc.code, str(exc)
        limit = self.runner.stop or self.retriever.stop
        if limit in STOP_LIMIT_CODES:
            return limit, "run budget reached before the planner finished"
        if not self.runner.available_providers():
            causes = {n: c.value for n, (c, _) in self.runner.disabled.items()}
            return Code.NO_PROVIDER_AVAILABLE, f"providers unavailable: {causes or 'none configured'}"
        if self.fail_streak >= self.s.max_provider_failures:
            return Code.PROVIDER_ERRORS, f"{self.fail_streak} consecutive failed queries"
        ident = self._obj("identity")
        if ident is not None and ident.terminal and self.identity_status != "supported":
            return Code.IDENTITY_UNRESOLVED, (f"identity objective {ident.status} with identity signal "
                                              f"'{self.identity_status}' ({len(self.supported)} supporting documents)")
        if self.no_gain_streak >= self.s.global_window:
            return Code.DIMINISHING_RETURNS, f"last {self.no_gain_streak} queries added no relevant evidence"
        if self.step >= self.s.max_steps:
            return Code.WORK_UNPROCESSED, f"planner step ceiling ({self.s.max_steps}) reached"
        if all(o.terminal or o.next_variant() is None for o in self.objectives):
            for o in self.objectives:
                if not o.terminal:
                    self._set_status(o, "exhausted", "no query variants left")
            return Code.PLAN_RESOLVED, "; ".join(f"{o.key}={o.status}" for o in self.objectives)
        return None, ""

    # ------------------------------------------------------------------ selection

    def _score(self, o: Objective) -> tuple[float, dict]:
        if o.terminal or o.next_variant() is None:
            return 0.0, {"skipped": o.status if o.terminal else "no variants left"}
        if self._identity_gate() and o.key != "identity":
            return 0.0, {"skipped": f"waiting for identity (signal '{self.identity_status}')"}
        target = max(1, o.target_docs)
        gap = max(0.0, 1 - o.coverage() / target)
        domain_short = max(0, o.target_domains - len(o.domains)) if not o.discovery_only else 0
        if gap == 0 and domain_short:
            gap = 0.5  # enough documents, too few independent domains
        diversity = 1 + 0.5 * domain_short / max(1, o.target_domains)
        # a specific lead from a discovery (handle, official domain, disambiguation) outranks generic plan queries
        discovery = DISCOVERY_BOOST if o.next_variant().origin.startswith("followup:") else 1.0
        score = o.priority * gap * diversity * discovery / (1 + o.low_yield_streak)
        return round(score, 4), {"priority": o.priority, "gap": round(gap, 3), "diversity": round(diversity, 3),
                                 "discovery_boost": discovery, "low_yield_streak": o.low_yield_streak,
                                 "score": round(score, 4)}

    def _select(self) -> tuple[Objective, dict, str]:
        scored = [(self._score(o), i, o) for i, o in enumerate(self.objectives)]
        scores = {o.key: parts for (_, parts), _, o in scored}
        best = max(((s, -i, o) for (s, _), i, o in scored if s > 0), key=lambda t: (t[0], t[1]), default=None)
        if best is None:  # every objective has score 0 but some still have variants (e.g. gate) -> take identity
            o = next(o for o in self.objectives if not o.terminal and o.next_variant() is not None)
            return o, scores, f"{o.key}: only objective with remaining variants"
        s, _, o = best
        p = scores[o.key]
        gate = f"; others wait for identity (signal '{self.identity_status}')" if self._identity_gate() else ""
        boost = f" x discovery {p['discovery_boost']}" if p["discovery_boost"] != 1.0 else ""
        return o, scores, (f"{o.key}: highest score {s} (priority {p['priority']} x coverage gap {p['gap']} x "
                           f"diversity {p['diversity']}{boost} / (1+{p['low_yield_streak']} low-yield)){gate}")

    # ------------------------------------------------------------------ measurement

    def _measure(self, qids: list[int], before: set[str]) -> Contribution:
        c = Contribution()
        if not qids:
            c.query_status = "not_sent"
            return c
        q = self.conn.execute(f"SELECT status, failure_code FROM search_queries WHERE id IN ({','.join('?' * len(qids))})",
                              qids).fetchall()
        statuses = {r["status"] for r in q}
        c.query_status = "completed" if "completed" in statuses else ("failed" if "failed" in statuses else "skipped")
        c.failure_code = next((r["failure_code"] for r in q if r["failure_code"]), None)
        rows = self.conn.execute(
            f"SELECT url_key, off_target FROM search_results WHERE query_id IN ({','.join('?' * len(qids))})",
            qids).fetchall()
        c.results = len(rows)
        c.off_target = sum(r["off_target"] for r in rows)
        keys = {r["url_key"] for r in rows if not r["off_target"]}
        new = keys - before
        c.duplicates = c.results - c.off_target - len(new)
        c.new_urls = len(new)
        self.seen_keys |= keys
        for key in sorted(keys & before):  # repeats: not new evidence, but they do cover this objective
            c.relevant_known += self.key_docs.get(key, [])
            if key in self.key_profiles:
                c.known_profile_leads.append(key)
        name = self.inp.name
        for key in sorted(new):
            u = self.conn.execute("SELECT id, decision, reject_code, registrable_domain, domain FROM urls"
                                  " WHERE run_id=? AND url_key=?", (self.run_id, key)).fetchone()
            if u is None:
                continue
            if u["decision"] == "rejected":
                c.blocked += 1
                if u["reject_code"] in PROFILE_REJECTS:
                    lead = self.conn.execute("SELECT url, snippet FROM leads WHERE url_id=? ORDER BY id LIMIT 1",
                                             (u["id"],)).fetchone()
                    hay = f"{lead['url'] if lead else ''} {lead['snippet'] or '' if lead else ''}".lower()
                    if all(part.lower() in hay for part in name.split()) or \
                            "".join(name.lower().split()) in hay.replace("-", "").replace("_", ""):
                        c.profile_leads.append(key)
                        self.key_profiles.add(key)
                continue
            for d in self.conn.execute("SELECT id, title, text, source_url, usable, duplicate_of, metadata_json"
                                       " FROM documents WHERE url_id=?", (u["id"],)):
                if not d["usable"]:
                    continue
                if d["duplicate_of"]:
                    c.near_duplicates += 1
                    continue
                c.fetched_usable += 1
                a = assess(text=d["text"] or "", title=d["title"], url=d["source_url"], domain=u["domain"],
                           name=name, organization=self.inp.organization,
                           organization_domain=self.inp.organization_domain or self.discovered_domain,
                           role=self.inp.role, known_urls=self.inp.known_urls)
                if a.level == "supported":
                    c.relevant_docs.append(d["id"])
                    self.key_docs.setdefault(key, []).append(d["id"])
                    if u["registrable_domain"] not in self.supported.values():
                        c.new_domains.append(u["registrable_domain"])
                    self.supported[d["id"]] = u["registrable_domain"]
                    official = self.inp.organization_domain or self.discovered_domain
                    if official and (u["domain"] == official or u["domain"].endswith("." + official)):
                        self.official_supported = True
                elif a.level == "weak":
                    c.weak_docs.append(d["id"])
                    self.weak_seen += 1
                else:
                    c.irrelevant_docs.append(d["id"])
        return c

    # ------------------------------------------------------------------ state updates and follow-ups

    def _update(self, o: Objective, c: Contribution) -> list[dict]:
        o.queries_run += 1
        self.fail_streak = 0
        before = (o.coverage(), len(o.domains))
        o.relevant_docs.update(c.relevant_docs + c.relevant_known)
        o.domains.update(self.supported[d] for d in c.relevant_docs + c.relevant_known)
        o.profile_leads.update(c.profile_leads + c.known_profile_leads)
        objective_gain = (o.coverage(), len(o.domains)) != before  # this objective learned something
        new_evidence = c.gain(o.discovery_only)  # the run as a whole learned something new
        if c.query_status == "completed":
            o.low_yield_streak = 0 if objective_gain else o.low_yield_streak + 1
            self.no_gain_streak = 0 if new_evidence else self.no_gain_streak + 1
        followups = self._followups(o, c)
        if o.key == "identity":
            covered = self.identity_status == "supported"
        else:
            covered = o.coverage() >= o.target_docs and (o.discovery_only or len(o.domains) >= o.target_domains)
        if covered:
            self._set_status(o, "covered", f"{o.coverage()} relevant, {len(o.domains)} domains (targets "
                                           f"{o.target_docs}/{o.target_domains})")
        elif o.low_yield_streak >= self.s.saturation_window:
            self._set_status(o, "saturated", f"{o.low_yield_streak} consecutive queries without new relevant evidence")
        elif o.queries_run >= self.s.max_queries_per_objective:
            self._set_status(o, "exhausted", f"per-objective query cap ({self.s.max_queries_per_objective}) reached")
        elif o.next_variant() is None:
            self._set_status(o, "exhausted", "no query variants left")
        else:
            self._save_objective(o)
        return followups

    def _followups(self, o: Objective, c: Contribution) -> list[dict]:
        added: list[dict] = []

        def add(target_key: str, v: Variant) -> None:
            t = self._obj(target_key)
            if t is None or t.queries_run >= self.s.max_queries_per_objective:
                return
            if t.add_variant(v):
                if t.terminal and t.status != "covered":
                    t.low_yield_streak = 0
                    self._set_status(t, "pending", f"reopened: {v.reason}")
                added.append({"objective": target_key, "spec": v.spec.as_dict(), "reason": v.reason,
                              "origin": v.origin})

        N = self.inp.name
        # 1. weak identity -> disambiguation
        if o.key == "identity" and c.weak_docs and self.identity_status != "supported" and not self.disambiguation_added:
            self.disambiguation_added = True
            words = [w for w in (self.inp.organization or "").split() if len(w) > 3][:2]
            if words:
                add("identity", Variant(SearchSpec(purpose="identity: disambiguation (organisation words)",
                                                   phrases=[N], terms=words, max_results=self.s.results_per_query),
                                        f"weak identity: {len(c.weak_docs)} page(s) name the person without the "
                                        f"organisation, domain or role", "followup:weak_identity"))
            if self.inp.role:
                add("identity", Variant(SearchSpec(purpose="identity: disambiguation (role)", phrases=[N, self.inp.role],
                                                   max_results=self.s.results_per_query),
                                        "weak identity: exact role phrase may separate namesakes",
                                        "followup:weak_identity"))
        # 2. official domain discovered from supporting documents
        slug = org_slug(self.inp.organization)
        if slug and not self.inp.organization_domain and not self.discovered_domain:
            for doc_id in c.relevant_docs:
                dom = self.supported[doc_id]
                if slug in dom.split(".")[0].replace("-", ""):
                    self.discovered_domain = dom
                    add("official_profile", Variant(
                        SearchSpec(purpose="official profile on discovered domain", phrases=[N], sites=[dom],
                                   max_results=self.s.results_per_query),
                        f"discovered likely official domain {dom} (organisation name in domain) in document {doc_id}",
                        "followup:official_domain"))
                    break
        # 3. handles on writing / profile platforms (from metadata links and profile leads)
        for link in self._links(c):
            handle = _platform_handle(link)
            if handle and handle not in self.handles_seen:
                self.handles_seen.add(handle)
                target = "public_profiles" if handle.startswith("github.com/") else "writing"
                add(target, Variant(SearchSpec(purpose=f"follow discovered profile {handle}", phrases=[N],
                                               sites=[handle], max_results=self.s.results_per_query),
                                    f"profile link {handle} found in this step's results", "followup:handle"))
        # 4. diversity: enough documents, too few independent domains
        if (not o.discovery_only and o.key != "identity" and o.coverage() >= o.target_docs
                and len(o.domains) < o.target_domains):
            base = o.variants[0].spec if o.variants else None
            if base:
                add(o.key, Variant(SearchSpec(purpose=f"{o.key}: diversity", phrases=base.phrases, terms=base.terms,
                                              any_of=base.any_of, exclude_sites=sorted(o.domains),
                                              max_results=self.s.results_per_query),
                                   f"coverage met from only {len(o.domains)} domain(s); excluding them to find "
                                   f"independent sources", "followup:diversity"))
        return added

    def _links(self, c: Contribution) -> list[str]:
        links: list[str] = []
        for doc_id in c.relevant_docs:
            row = self.conn.execute("SELECT metadata_json FROM documents WHERE id=?", (doc_id,)).fetchone()
            meta = json.loads(row[0]) if row and row[0] else {}
            links += meta.get("same_as", []) + meta.get("rel_me", [])
        for key in c.profile_leads:
            row = self.conn.execute("SELECT l.url FROM leads l JOIN urls u ON u.id=l.url_id WHERE u.run_id=?"
                                    " AND u.url_key=? ORDER BY l.id LIMIT 1", (self.run_id, key)).fetchone()
            if row:
                links.append(row[0])
        return sorted(set(links))

    def _continue_reason(self, o: Objective, c: Contribution) -> str:
        open_ = [x.key for x in self.objectives if not x.terminal and x.next_variant() is not None]
        what = (f"query {c.query_status}" + (f" ({c.failure_code})" if c.failure_code else "") +
                f"; {c.new_urls} new URLs, {len(c.relevant_docs)} relevant, {len(c.weak_docs)} name-only, "
                f"{len(c.irrelevant_docs)} irrelevant, {c.duplicates} duplicates ({len(c.relevant_known)} relevant, credited "
                f"to this objective only), {c.off_target} off-target, "
                f"{c.blocked} blocked")
        return (f"{what}. {o.key} -> {o.status}. Identity signal '{self.identity_status}'. "
                f"No-gain streak {self.no_gain_streak}/{self.s.global_window}. Open objectives: {open_ or 'none'}")

    # ------------------------------------------------------------------ persistence

    def _obj(self, key: str) -> Objective | None:
        return next((o for o in self.objectives if o.key == key), None)

    def _set_status(self, o: Objective, status: str, reason: str) -> None:
        o.status, o.status_reason = status, reason
        self._save_objective(o)

    def _save_objective(self, o: Objective, insert: bool = False) -> None:
        values = (o.status, o.status_reason, o.queries_run, len(o.relevant_docs), len(o.domains), len(o.profile_leads),
                  o.low_yield_streak, json.dumps([v.summary() for v in o.variants]), repo.iso())
        if insert:
            self.conn.execute(
                "INSERT INTO research_objectives (run_id, key, category, priority, target_docs, target_domains, status,"
                " status_reason, queries_run, relevant_docs, domains, profile_leads, low_yield_streak, variants_json,"
                " updated_at, created_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, o.key, o.category, o.priority, o.target_docs, o.target_domains, *values, o.created_reason))
        else:
            self.conn.execute(
                "UPDATE research_objectives SET status=?, status_reason=?, queries_run=?, relevant_docs=?, domains=?,"
                " profile_leads=?, low_yield_streak=?, variants_json=?, updated_at=? WHERE run_id=? AND key=?",
                (*values, self.run_id, o.key))

    def _decision(self, o: Objective | None, v: Variant | None, qids: list[int], scores: dict | None,
                  c: Contribution | None, decision: str, why: str, selection: str | None = None,
                  followups: list | None = None) -> None:
        self.conn.execute(
            "INSERT INTO planner_decisions (run_id, step, objective_key, query_ids_json, spec_json, selection_reason,"
            " scores_json, contribution_json, followups_json, identity_signal, decision, decision_reason, at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, self.step, o.key if o else None, json.dumps(qids), json.dumps(v.spec.as_dict()) if v else None,
             selection, json.dumps(scores) if scores else None, json.dumps(c.as_dict()) if c else None,
             json.dumps(followups or []), self.identity_status, decision, why, repo.iso()))
        repo.trace(self.conn, self.run_id, "planner", decision, ref_type="objective", ref_id=o.key if o else None,
                   reason=why[:500], data={"step": self.step})


def _platform_handle(url: str) -> str | None:
    """github.com/<user>, medium.com/@<user> or <name>.substack.com from a profile-ish URL."""
    from urllib.parse import urlsplit

    p = urlsplit(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower().removeprefix("www.")
    parts = [x for x in p.path.split("/") if x]
    if host == "github.com" and parts and parts[0] not in {"orgs", "topics", "search", "features", "about"}:
        return f"github.com/{parts[0].lower()}"
    if host == "medium.com" and parts and parts[0].startswith("@"):
        return f"medium.com/{parts[0].lower()}"
    if host.endswith(".substack.com") and host.count(".") == 2:
        return host
    return None
