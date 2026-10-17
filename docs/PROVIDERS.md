# Providers, limits and running costs

Prices and terms were checked on **2026-10-17** against the providers' own documentation. They change often, so re-check before committing to one.

## Search providers

| Provider | Free tier | Paid rate | Notes and restrictions | Status in prototype |
|---|---|---|---|---|
| **Tavily** ([docs](https://docs.tavily.com/documentation/api-credits)) | 1,000 credits/month, no card | Pay-as-you-go $0.008/credit; plans from $30/month for 4,000 credits | Basic search = 1 credit, advanced = 2. Returns cleaned page text (`include_raw_content`), so most pages need no fetch. `include_domains`/`exclude_domains` lists. | **Default.** Implemented; tested against mocked HTTP |
| **Exa** ([pricing](https://exa.ai/docs/reference/pricing.md)) | $10 credit/month, no card | $7 per 1,000 searches (auto/fast), $4 per 1,000 (instant); contents $1 per 1,000 pages | Neural index that works well for "people who wrote about X". Returns page text and dates. | Implemented; tested against mocked HTTP |
| **Brave** ([API](https://brave.com/search/api/)) | $5 credit/month (about 1,000 searches); card required | $5 per 1,000 | Independent index, supports `site:` and quotes. **Storing results requires a plan with storage rights**, so the adapter keeps no Brave result text: pages are fetched directly and excerpts come from the pages themselves. | Implemented; tested against mocked HTTP |
| **Wikipedia** (MediaWiki API) | Free, no key | — | Notable people only. **Requires contact details in the User-Agent** (`PERSONALIZER_CONTACT`), otherwise 403. Intro extracts only. | Implemented; **verified live** |
| Google Custom Search JSON API | — | — | **Closed to new customers; shuts down 1 Jan 2027.** New Programmable Search Engines are limited to 50 domains. | Excluded |
| Scraping Google/Bing/DuckDuckGo HTML | — | — | No permitted API; against those services' terms | Excluded |
| LinkedIn, X, Facebook, Instagram, TikTok… | — | — | Terms prohibit automated collection. Profile URLs you supply are used as identity anchors but never fetched. | Excluded at query and policy level |
| People-search and data-broker sites | — | — | Inappropriate sources whatever the access terms | Blocked |
| OpenAlex (publications) | About $0.10/day keyless, $1/day with a free key (since Feb 2026) | Usage-based | Good for academic subjects | Not implemented; future work |

**Why Tavily is the default.** It's the only provider that is free without a card, its free tier covers about 140 runs a month, and it returns page text, so the engine makes fewer direct fetches.

**Limits enforced by the engine:** at most 9 queries, `PERSONALIZER_RESULTS_PER_QUERY` results each, `max_sources` (12) sources, and `PERSONALIZER_MAX_FETCHES` (12) direct fetches per run. A rate-limit response stops that provider for the rest of the run.

## LLM (optional)

| Model | Input / output per 1M tokens | Use |
|---|---|---|
| `claude-opus-5-5` (default) | $4 / $20 | Best extraction and synthesis quality |
| `claude-sonnet-5-5` | $2 / $10 | Likely good enough; evaluate first |
| `claude-haiku-5-5` | $0.10 / $0.50 | Candidate for the per-page extraction step |

The client uses structured outputs (Pydantic schemas), `effort: low`, and the server-side refusal fallback (`fallbacks: "default"`). Every call's tokens are counted, and `PERSONALIZER_MAX_COST_USD` (default $0.75) caps LLM spending per run. Past the cap, extraction continues with heuristics.

## Running-cost estimates

Per research run, assuming 7 queries and 12 sources. **These LLM figures are estimates from token budgets, not measurements:** Claude mode has not been run against the live API yet. Run `python eval/run_eval.py --llm` with a key to measure the real cost; each brief's *Usage* line reports it.

| Configuration | Search | LLM | Total per run | Runs/month on free tiers |
|---|---|---|---|---|
| Heuristic + Wikipedia | $0 | $0 | **$0** | Unlimited (within Wikimedia etiquette) |
| Heuristic + Tavily | $0 (free tier) / $0.056 | $0 | **$0–0.06** | About 140 |
| Tavily + Claude Haiku 5.5 | $0–0.06 | ~$0.02 | **~$0.02–0.08** | About 140 (search-bound) |
| Tavily + Claude Sonnet 5.5 | $0–0.06 | ~$0.30 | **~$0.30–0.36** | — |
| Tavily + Claude Opus 5.5 | $0–0.06 | ~$0.55 | **~$0.55–0.61** | — |

LLM token assumptions: about 50k input and 19k output tokens per run. That is 12 extraction calls at about 3.7k input and 1.3k output each (thinking at low effort included), plus synthesis at about 5k input and 3k output.

## Provider limitations to keep in mind

- **Dates are often missing.** Tavily returns `published_date` only on request, and only as a best estimate; Wikipedia gives no date. Undated findings get neutral freshness (0.5).
- **Search ranking hides people who aren't notable.** Quoted-name queries return few results for most people, so expect thin briefs.
- **Brave and Exa snippets** are useful for discovery only; evidence always comes from the page itself.
- **Free tiers can change without notice**, and Brave's 2026 pricing change removed its old free plan for new users.
- **Terms of use** for each provider govern caching and use with AI. Review them for your deployment (see [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md)).
