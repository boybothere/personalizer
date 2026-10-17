# Personalizer

An AI-assisted engine that researches **public, professional** information about a person you are about to meet and turns it into an **evidence-backed meeting brief**: professional context, conversation starters, questions, optional common ground, and explicit unknowns. Every item in the brief cites a finding, and every finding carries a verbatim excerpt and a source URL.

It is not a CRM, a transcription tool, or a people-search engine. It refuses requests aimed at sensitive or private attributes, does not use people-search or data-broker sites or restricted social platforms, and treats every web page as untrusted data.

```
input ─► policy check ─► queries ─► search ─► source policy ─► fetch + sanitise ─► identity
      ─► extraction ─► verbatim-excerpt check ─► sensitive filter ─► corroboration / conflicts
      ─► confidence ─► preference tiers ─► relevance + ranking ─► brief (+ validation) ─► report ─► feedback
```

## Quick start (no keys, no cost, about 2 minutes)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                  # defaults: offline synthetic corpus, heuristic mode

python -m personalizer research --name "Priya Raman" --org "Nimbus Compute" \
  --org-domain nimbuscompute.example --purpose "Discuss an AI infrastructure partnership focused on GPU capacity"

python -m personalizer serve          # web UI at http://127.0.0.1:8000, API docs at /docs
pytest                                # 68 tests
python eval/run_eval.py               # evaluation metrics -> eval/RESULTS.md
```

The default `.env` uses a **synthetic corpus**: fictional people on reserved `.example` domains. To research real people, configure a search provider (see [docs/USAGE.md](docs/USAGE.md#going-live)).

## Documentation

| Document | What it covers |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | **How to use it and what to expect**: inputs, CLI, web UI, API, reading a brief, feedback, troubleshooting |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline stages, data model, confidence method, design decisions |
| [docs/PROVIDERS.md](docs/PROVIDERS.md) | Search and LLM provider comparison, terms, limits, running-cost estimates |
| [docs/RESPONSIBLE_USE.md](docs/RESPONSIBLE_USE.md) | Safeguards, prompt-injection defences, privacy (India DPDP, GDPR, CCPA), items for legal review |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Test dataset, metrics, results, known weaknesses, recommended improvements |
| [examples/](examples/) | Example briefs (Markdown and JSON), including one live Wikipedia run |

## Project layout

```
personalizer/          the engine
  models.py            Pydantic schemas: request, finding, report
  pipeline.py          orchestrates the stages
  queries.py           operator-based query templates + policy filter
  providers.py         Tavily, Exa, Brave, Wikipedia, offline fixture (all provider logic lives here)
  fetch.py             SSRF-safe, robots-aware fetcher; HTML sanitiser
  identity.py          identity resolution (same-name disambiguation)
  extract.py           heuristic and Claude extractors; verbatim-excerpt verification
  safety.py            request/query policy, injection detection, sensitive filter, tone guard
  scoring.py           corroboration, conflicts, confidence, preference tiers, relevance, ranking
  personalize.py       template and Claude brief writers + validation
  sources.py           blocked / official / reputable / professional domain policy
  llm.py               Anthropic SDK wrapper: structured outputs, cost accounting, budget cap
  store.py, api.py, cli.py, report.py, static/index.html
eval/                  synthetic dataset + evaluation runner
tests/                 pytest suite
```

## Status

Working prototype. Heuristic mode, the fixture corpus, the providers' request and parse logic (against mocked HTTP), and the Wikipedia provider (live) have all been run. **Claude mode has not been run against the live API**: that costs money and needs your approval and key. Its request shape and its validation of model output are covered by tests. See [docs/EVALUATION.md](docs/EVALUATION.md#what-has-not-been-verified).
