# Research engine (`engine/`)

This is the deterministic rebuild of the research layer. It makes no LLM calls. It's independent of the prototype in `personalizer/`, which is untouched and still works.

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Data flow, modules, storage, metrics |
| [DECISIONS.md](DECISIONS.md) | Decision log: platform access, provenance, privacy, budgets, validation |
| [PLATFORMS.md](PLATFORMS.md) | Platform access matrix with dated probe evidence |
| [PHASE1_RESULTS.md](PHASE1_RESULTS.md) | Phase 1: observability, storage and retrieval: results and limitations |
| [PHASE2_RESULTS.md](PHASE2_RESULTS.md) | Phase 2: search layer, provider metrics, live search, actual costs, access policy |
| [DPDP_ANALYSIS.md](DPDP_ANALYSIS.md) | DPDP Act applicability and commencement: **draft for legal review** |

## Setup

```bash
pip install -r requirements-engine.txt       # in the existing .venv, or a fresh one
```

Configuration is read from environment variables, or from `.env` without changing it:

| Variable | Default | Meaning |
|---|---|---|
| `ENGINE_CONTACT` (falls back to `PERSONALIZER_CONTACT`) | — | Contact details in the User-Agent; Wikimedia requires them |
| `ENGINE_DB_PATH` | `data/engine.db` | Engine database (separate from `data/personalizer.db`) |
| `ENGINE_BUDGET_MAX_FETCH_ATTEMPTS` | 80 | Every content request, retries included |
| `ENGINE_BUDGET_MAX_WALL_SECONDS` | 240 | Run time limit |
| `ENGINE_BUDGET_MAX_TOTAL_BYTES` | 25000000 | Download limit |
| `ENGINE_BUDGET_MAX_LEADS`, `..._MAX_ROBOTS_REQUESTS`, `..._MAX_SEARCH_REQUESTS`, `..._MAX_COST_USD` | 200, 40, 40, 0.25 | Other hard limits |
| `ENGINE_FETCH_*` | see `engine/config.py` | Timeouts, retries, backoff, concurrency, size and politeness |
| `ENGINE_EXTRACT_*`, `ENGINE_RETENTION_*` | see `engine/config.py` | Usability thresholds; retention (30 days for runs, 7 days for text) |
| `ENGINE_PLATFORMS_X_OEMBED_ENABLED`, `..._REDDIT_OEMBED_ENABLED` | false | Connector switches (disabled until terms review) |
| `BRAVE_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY` | — | Provider keys (never stored in the database) |
| `ENGINE_PROVIDERS_ENABLED` | brave,tavily,exa | Providers used by `search` |
| `ENGINE_PROVIDERS_BRAVE_MONTHLY_CAP_REQUESTS`, `..._TAVILY_MONTHLY_CAP_CREDITS`, `..._EXA_MONTHLY_CAP_USD` | 900, 900, 9.0 | Free-tier guard: requests beyond a cap are not sent |

## Commands

```bash
python -m engine db status                      # schema version
python -m engine db upgrade                     # apply migrations
python -m engine db downgrade --to 0            # reversible; drops tables, so needs an explicit target
python -m engine retrieve URL [URL ...] [--json]
python -m engine summary RUN_ID [--trace]       # metrics, gates, URLs, documents, trace
python -m engine search --purpose WHY --phrase P [--site S] [--fresh N] [--providers tavily] [--no-retrieve]
python -m engine search --specs-file docs/engine/live_specs_phase2.json --providers tavily
python -m engine ledger                         # this month's usage vs free-allowance caps
python -m engine providers                      # capabilities, terms-based storage policy, pricing
python -m engine tavily-usage                   # Tavily's own account counter (read-only)
python -m engine delete-run RUN_ID
python -m engine purge                          # clear expired text, delete expired runs

pytest tests_engine                             # deterministic suite (no network)
ENGINE_LIVE=1 pytest tests_engine/test_live.py  # live smoke test (free, keyless endpoints)
```

## Reading a run

- **status**:
  - `completed`: all accepted work was processed. This never means "comprehensive".
  - `partial`: stopped by a budget or time limit, or work was left unprocessed.
  - `failed`: no leads, no usable documents, or every fetch failed.
  - `cancelled`: stopped by the user.
- **gates**: each gate shows whether it passed and, if not, a failure code with an explanation.
- **metrics**: computed from the stored records, never from separate counters:
  - leads, unique URLs and rejections by code
  - fetch attempts, retries, HTTP 2xx, URLs retrieved, failures by code
  - documents, usable documents, extraction failures by code, near-duplicates
  - date sources and conflicts; flags
  - what was blocked or inaccessible, and why
  - time per stage; cost
