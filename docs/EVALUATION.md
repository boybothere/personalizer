# Evaluation

## How to run

```bash
pytest                          # 68 automated tests (offline, ~5 s)
python eval/run_eval.py         # metrics on the synthetic corpus -> eval/RESULTS.md, eval/results.json, examples/
python eval/run_eval.py --llm   # same, using Claude (needs ANTHROPIC_API_KEY; costs money; not yet run)
```

## Test dataset

`eval/dataset.py` contains 19 synthetic web pages about fictional people on reserved `.example` domains, and 5 research cases. Each page carries gold labels (`same_person`, `relevant`). Each case lists strings that must never appear in the output, plus its expected behaviour.

| Required test | Where it's covered |
|---|---|
| Identical or similar names | Pastry chef "Priya Raman"; footballer "Daniel Okafor"; live: other Microsoft executives |
| Conflicting information | Daniel Okafor: CTO (January) vs VP of Engineering (September) |
| Outdated articles | 2019 "senior storage engineer" role |
| Incorrectly attributed posts | Forum post "some say this is Priya Raman… unverified" |
| Unsupported preference claims | "Rumoured to be a keen golfer, according to colleagues" |
| Prompt injection | Hidden `display:none` instruction + visible "Note to AI assistants…" |
| Missing or inaccessible sources | 404, 403 paywall, robots.txt disallow |
| Low-confidence findings | Third-party hobby; old role |
| Irrelevant personal details | Religious remark in an interview; the chef's hobbies |
| Sensitive-extraction attempts | Purpose "find out his religion and political views" |
| Restricted sources | LinkedIn and Spokeo results |
| Nobody public | "Arjun Mehta" |

The unit tests add the patterns found in the live run, adversarial LLM output from a scripted fake model (hallucinated excerpts, injected claims, bogus citations, bad tone, a disallowed query), the SSRF and robots logic, provider request and response shapes, the API flow, retention, and `.env` parsing.

## Metrics

| Metric | Definition |
|---|---|
| Source attribution accuracy | Findings whose excerpt occurs verbatim in the cited page ÷ all findings |
| Identity-match accuracy | Retrieved pages where the accept/reject decision matches the gold `same_person` label |
| Factual support / false-positive rate | (Suitable findings from wrong-person pages + forbidden strings that leaked) ÷ suitable findings |
| Relevance precision@5 | Top-5 suitable findings whose page is gold-relevant |
| Cost per run | Search calls × list price + LLM tokens × model price (from `Report.usage`) |

## Results: heuristic mode, synthetic corpus

| Metric | Value |
|---|---|
| Source attribution accuracy | 1.00 (27/27) |
| Identity-match accuracy | 1.00 (24/24 pages); 0 false accepts |
| False-positive rate | 0.00 |
| Relevance precision@5 | 1.00 (14/14) |
| Cases passing all checks | 5/5 |
| Cost per run | $0 |

Full per-case output is in [`eval/RESULTS.md`](../eval/RESULTS.md); the briefs are in [`examples/`](../examples/).

### Read these numbers with caution

- **The corpus and the rules were developed together.** I fixed rules while looking at this corpus, so perfect scores show the designed behaviours work. They do **not** estimate real-world accuracy.
- **Attribution is 1.00 by construction.** Unverifiable excerpts are dropped before scoring. The metric guards against regressions; it doesn't measure how much was missed.
- **Recall is not measured.** There are no gold lists of every fact a page contains.
- **Five cases are a smoke test, not a benchmark.**

### First iteration: problems the evaluation caught and fixed

| Observed | Fix |
|---|---|
| Forum post with a hedged attribution accepted as the right person (2 false accepts) | Hedged-attribution penalty in identity scoring |
| "VP **of** Infrastructure" parsed as an employer, creating a false organisation conflict that wrongly excluded the verified current role | Affiliation patterns use "at", "from" and "joined", not "of" |
| Headings merged into sentences ("Priya Raman Priya Raman is…") | Split on line breaks before sentence punctuation |
| An injection page's legitimate panel fact was lost | Sanitised text kept one sentence per line, so redaction is precise |
| SDK `ValidationError` on a refusal escaped the fallback handling | Converted to `LLMError`; covered by a test using the real SDK with a mock transport |
| `.env` inline comments would have been read as API keys | Comment-aware loader; covered by a test |

### Live run (Wikipedia API, real data)

A run on a well-documented public executive (`examples/live-wikipedia-nadella.md`) **surfaced four weaknesses the synthetic corpus missed**:

1. A passing mention in a list of conference attendees was taken as a role claim about the person.
2. "He led the search…" on *another person's* page was attributed to our subject.
3. Sentences split at initials ("John W.").
4. Wikimedia rejected the User-Agent (no contact details), so every call returned 403.

All four are fixed and covered by regression tests. The final live brief contains 2 findings, both correct and both relevant. **This is the strongest evidence that real-world performance will be lower than the synthetic scores**, and that a real labelled set is needed.

## What has not been verified

- **Claude mode end to end.** The request shape, structured-output parsing, cost accounting, refusal handling and every validation gate are tested with mocks and a scripted adversarial model. Real extraction quality, latency and cost per run are unmeasured, because running them costs money and needs your approval. Expected cost: about $0.55 per run on Opus 5.5 (see [PROVIDERS.md](PROVIDERS.md#running-cost-estimates)).
- **Tavily, Exa and Brave against their live APIs.** The adapters follow the current docs and are tested against mocked responses, but no keys were available.
- **Robots.txt and the fetcher on the open web.** Logic is unit-tested; there has been no live crawl beyond Wikipedia's API.
- **LLM query suggestions.** Whether they improve discovery over the templates is unmeasured; this needs live search and recall labels.

## Known weaknesses

- **Heuristic extraction** is sentence-level and rule-based: it quotes rather than summarises, misses facts phrased in unusual ways, and resolves pronouns naively.
- **Identity** relies on a co-mention of name and organisation. Two people with the same name at the same company would merge, and someone whose employer changed recently may be rejected until sources catch up.
- **Corroboration** uses word overlap. Paraphrased syndication counts as independent, and true restatements with different wording may not match.
- **Relevance** uses keyword overlap with a tiny synonym table, so it is topical rather than semantic.
- **Source tiers** use a static domain list, biased towards English-language and Indian/US outlets.
- **Dates** are often missing, which weakens staleness detection.

## Recommended next steps (in priority order)

1. **Build a real labelled set:** 30–50 consenting colleagues or public executives, with labelled facts per page. Measure recall, precision per confidence level, identity accuracy and the rate of inappropriate suggestions. Everything else should be tuned against this.
2. **Run Claude mode on that set** (with approval), comparing Opus 5.5, Sonnet 5.5 and Haiku 5.5 for extraction, and record real cost per run. Keep the cheapest model that holds quality.
3. **Calibrate confidence:** fit the score weights (or a logistic model on the same features) to the labels, and publish reliability curves before showing any number as a probability.
4. **Improve identity:** add employer history from official bios, and use embeddings for "same person" checks; ask the user to choose when two candidates remain.
5. **Improve corroboration:** use embedding similarity and source-independence checks (shared wire copy, the same author).
6. **Add publication sources:** OpenAlex/ORCID for academics; RSS for company newsrooms; podcast transcripts where the licence allows.
7. **Prepare for production:** user accounts, an audit log (who researched whom and why), encryption at rest, a notice/consent workflow (pending legal review), and identity verification for opt-outs.
8. **Trial an agent step only when 1–2 are in place:** let the model propose follow-up queries after reading the first findings, and keep it only if recall improves at acceptable cost.
