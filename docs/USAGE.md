# Using Personalizer, and what to expect

This guide covers running the engine, what each input does, how to read the brief it produces, and what it will and won't do.

- [1. Setup](#1-setup)
- [2. Three ways to run it](#2-three-ways-to-run-it)
- [3. Inputs](#3-inputs)
- [4. Reading the brief](#4-reading-the-brief)
- [5. What to expect in practice](#5-what-to-expect-in-practice)
- [6. Feedback, deletion and opt-out](#6-feedback-deletion-and-opt-out)
- [Going live](#going-live)
- [Troubleshooting](#troubleshooting)

---

## 1. Setup

Requires Python 3.10+ (developed on 3.12).

```bash
python -m venv .venv
.venv\Scripts\activate              # Windows
source .venv/bin/activate           # macOS / Linux
pip install -r requirements-dev.txt
cp .env.example .env
```

The default `.env` runs **offline**: the `fixture` search provider serves a synthetic corpus (fictional people and companies on `.example` domains), and `PERSONALIZER_LLM=none` uses rule-based extraction. Nothing leaves your machine and nothing costs money. Use this mode to learn the tool and to run the tests.

Fictional people available in the corpus:

| Name | Organisation | Org domain | What the case exercises |
|---|---|---|---|
| Priya Raman | Nimbus Compute | `nimbuscompute.example` | Rich evidence, a same-name pastry chef, an outdated 2019 role, a prompt-injection page, a misattributed forum post, a rumoured hobby, a religious remark, paywalled and robots-blocked pages, LinkedIn and Spokeo results |
| Daniel Okafor | Helio Grid | `heliogrid.example` | Conflicting job titles, a same-name footballer, a dead link |
| Arjun Mehta | Quietly Labs | — | Nobody public: tests that nothing is invented |

## 2. Three ways to run it

### Command line

```bash
python -m personalizer research \
  --name "Priya Raman" --org "Nimbus Compute" --org-domain nimbuscompute.example \
  --role "VP of Infrastructure" \
  --purpose "Discuss an AI infrastructure partnership focused on GPU capacity and inference costs" \
  --me "I lead partnerships for a GPU inference optimisation startup"
```

Useful flags: `--json` (full structured report), `--out brief.md` (write to a file), `--no-personal` (exclude personal interests), `--exclude company_activity speaking_appearance` (exclude categories), `--no-save` (don't store the report), `--profile-url URL`, `--location`.

Exit codes: `0` success, `2` request refused by policy, `3` person has opted out.

### Web UI

```bash
python -m personalizer serve        # http://127.0.0.1:8000
```

Fill in the form and press **Research**. The brief appears with citations. Each finding has **accurate / incorrect / irrelevant / inappropriate** buttons, and there are links to the Markdown version and to delete the report. The server binds to `127.0.0.1` by default; don't expose it publicly without setting `PERSONALIZER_API_TOKEN` and putting it behind TLS. (If you set a token, the UI reads it from `localStorage["personalizer_token"]`.)

### HTTP API

Interactive docs are at `http://127.0.0.1:8000/docs`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/research` | Run research; body is the request (see below); returns the full report JSON |
| `GET` | `/api/reports` | List stored reports (id, subject, created, expires) |
| `GET` | `/api/reports/{id}` | Report JSON, with feedback applied |
| `GET` | `/api/reports/{id}/markdown` | The brief as Markdown |
| `DELETE` | `/api/reports/{id}` | Delete a report and its feedback |
| `POST` | `/api/reports/{id}/findings/{finding_id}/feedback` | `{"label": "accurate" \| "incorrect" \| "irrelevant" \| "inappropriate", "note": "…"}` |
| `GET` | `/api/feedback/stats` | Precision per confidence level (calibration check) |
| `POST` | `/api/optout` | `{"name": "…"}`: delete the person's reports and block future research |
| `POST` | `/api/admin/purge` | Delete expired reports now (also runs at startup) |
| `GET` | `/api/health` | Configuration summary (which keys are present, never their values) |

```bash
curl -s -X POST http://127.0.0.1:8000/api/research -H "Content-Type: application/json" -d '{
  "name": "Daniel Okafor", "organization": "Helio Grid", "organization_domain": "heliogrid.example",
  "meeting_purpose": "Explore a grid-scale battery analytics partnership"}'
```

Status codes: `422` request refused by policy or invalid input, `403` opted-out subject, `401` bad or missing token (only when a token is configured).

## 3. Inputs

| Field | Required | Effect |
|---|---|---|
| `name` | yes | The person. Matched as a full name, allowing one middle name or initial. |
| `meeting_purpose` | yes | Drives the topic queries, relevance ranking and the questions. **Screened**: purposes mentioning religion, politics, health, sexuality, caste, family, home address, contact data, identity numbers, finances, or "dirt"/"leverage" are refused. |
| `organization` | strongly recommended | The main identity anchor. Without it, most sources can't be confirmed as the right person. |
| `organization_domain` | recommended | Pages on this domain are treated as official, and an official-biography query is added. |
| `role` | optional | Adds a small identity boost when the page mentions the role's terms. |
| `profile_url` | optional | Identity anchor. Fetched only if it isn't on a restricted platform; LinkedIn, X and similar are never fetched. |
| `location` | optional | Small identity boost only. |
| `requester_context` | optional | Your own role or interests. Used **only** to find real overlap for "common ground"; never searched. |
| `excluded_categories` | optional | Any of `role_and_career`, `professional_expertise`, `publication`, `speaking_appearance`, `company_activity`, `professional_affiliation`, `industry_topic`, `disclosed_hobby`, `communication_preference`, `business_connection`. Excluded findings are still listed, but never used in the brief. |
| `allow_personal_interests` | default `true` | `false` drops all hobby findings from the brief. |
| `max_sources` | default 12 | Cap on sources considered per run. |

## 4. Reading the brief

A brief has these sections, in this order.

**Header.** Shows the mode (`heuristic` or `llm`) and the identity status. *Identity NOT resolved* means no reliable source tied the name to the organisation: treat everything with suspicion, or don't use the brief at all. Notes below the header say whether same-name people were found and excluded.

**Professional context.** Up to four statements, each with numbered citations like `[1]` that point to the Evidence table.

**Conversation starters.** Openers based on the person's public, professional work, each with a *Why* line linking it to your meeting. They're written so that you refer to the source openly ("Say you read their published piece…"). They never pretend you already know the person.

**Questions to ask.** Open questions about the meeting topic, anchored to evidence. One question always asks about their priorities, because public sources rarely establish them.

**Optional common ground.** Two kinds of item:
- A **personal interest**. This appears only if the person disclosed it themselves (a quote or first-person writing) or it is corroborated by independent sources. It's always marked *optional*, with advice to raise it only if you genuinely share it, and to say where you saw it.
- A **professional overlap** with your `requester_context`.

Rumoured or third-party claims about hobbies never appear here.

**Preparation notes.** Working preferences the person has stated publicly, such as "I prefer a written pre-read".

**Unknowns (do not assume).** What the evidence does *not* establish. When nothing reliable is found, this section is the main output, rather than a guess.

**Warnings.** Conflicts between sources (for example different job titles), failed searches, rejected queries, and brief items that were dropped by validation.

**Evidence.** One row per finding:

| Column | Meaning |
|---|---|
| Category | What kind of fact it is |
| Source / Published | Where it came from, and when (`unknown` if the page has no date) |
| Confidence | `high` / `medium` / `low` with a score. **Rule-based, not a probability**; see [ARCHITECTURE.md](ARCHITECTURE.md#confidence) |
| Identity | How sure we are that this page is about *this* person |
| Disclosure | `explicitly_stated` (their own words), `observed_activity` (a talk given, a paper written), `third_party_report` (someone else says so), `inferred` |
| Used | `yes`, or `no (reason)`: for example *outdated or contradicted*, *needs confirmation*, *category excluded by user* |

Below the table, **Supporting excerpts** gives each verbatim quote, its URL, the retrieval date, any corroborating URLs, and why it was downgraded. **Excluded material** lists sources and claims that were dropped, with the reason: blocked site, robots.txt, paywall, different person, sensitive (the category only; the text itself is not kept), or excerpt not found in the source.

### Example (offline corpus; excerpt)

```text
## Professional context
- Priya Raman is Vice President of Infrastructure at Nimbus Compute, where she leads the GPU cloud platform team. [1]
- In this article, Priya Raman argues that most AI teams waste a third of their GPU spend on idle capacity. [4]

## Optional common ground
- (optional) They have mentioned this publicly ("I'm a lifelong cricket fan, …," Raman said., Interview: …).
  Raise it only if you genuinely share the interest and it comes up naturally, and say where you saw it. [9]

## Evidence
| 10 | Priya Raman, a senior storage engineer at Nimbus Compute, … | role_and_career | Business Herald | 2019-04-02 |
       medium (0.598) | high | observed_activity | no (outdated or contradicted; confirm before relying on it) |
```

What *didn't* make it into that brief, and why:
- The pastry chef also called Priya Raman: rejected on identity.
- The forum post "some say this is Priya Raman": rejected as an unverified attribution.
- A hidden instruction asking for a "50% discount": removed during HTML sanitisation.
- A visible "Note to AI assistants…" line: redacted before extraction.
- A remark about her faith: excluded as sensitive.
- The rumoured golf hobby: kept only as a hypothesis, never used.
- The paywalled, robots-blocked, LinkedIn and Spokeo pages: never read.

All the full examples are in [`examples/`](../examples/).

## 5. What to expect in practice

**Speed.** Offline: about a second. Live: roughly 10–60 seconds, depending on how many pages are fetched and whether Claude is used.

**Coverage.** Depends on how public the person is. Senior executives, speakers and authors produce useful briefs. Most people produce a thin brief with a strong *Unknowns* section, which is the intended behaviour. Wikipedia alone covers only notable people; use Tavily or Exa for everyone else.

**Heuristic mode vs Claude mode.**

| | Heuristic (`PERSONALIZER_LLM=none`) | Claude (`PERSONALIZER_LLM=anthropic`) |
|---|---|---|
| Cost | Free | About $0.30–$0.60 per run on Opus 5.5 (estimate; see [PROVIDERS.md](PROVIDERS.md#running-cost-estimates)) |
| Claims | Whole sentences quoted from the source | Short paraphrases, each still tied to a verified verbatim excerpt |
| Brief | Template phrasing; can be repetitive | Natural and specific phrasing; still validated |
| Recall | Misses facts phrased in unusual ways | Better recall on varied writing |
| Safety | Same gates | Same gates; the model never sees raw pages during synthesis, and its output is re-validated |

**It will:**
- quote its evidence;
- flag conflicting sources and prefer the newer one;
- downgrade old, time-sensitive facts;
- list what it couldn't find;
- skip pages it isn't allowed to read;
- refuse sensitive requests.

**It won't:**
- invent facts;
- read LinkedIn, X, Facebook or Instagram;
- use people-search or data-broker sites;
- infer religion, politics, health, sexuality, ethnicity or family details;
- write openers that fake familiarity ("I'm also a huge fan", "we both…");
- treat a confidence score as a probability.

**It can still be wrong.** Identity resolution is heuristic: two people with the same name *and* the same employer will be merged. A reliable-looking source can be wrong, and corroboration can't detect two outlets that copied the same mistake with reworded text. Read the excerpts before relying on a finding.

## 6. Feedback, deletion and opt-out

- **Feedback.** Mark a finding *incorrect* or *inappropriate* and it's removed from the brief, along with any suggestion that cited it. *Irrelevant* halves its rank. Labels feed `/api/feedback/stats`, which shows the precision of each confidence level. That's the check on whether the confidence levels mean anything.
- **Retention.** Reports expire after `PERSONALIZER_RETENTION_DAYS` (default 30) and are purged at startup or through `/api/admin/purge`. Raw page text is never stored; only short excerpts attached to findings.
- **Deletion.** Use `DELETE /api/reports/{id}`, or the button in the UI.
- **Opt-out.** `POST /api/optout {"name": …}` deletes every report about that name and blocks future research on it, which serves as a mechanism for objections from the people being researched.

## Going live

1. **Choose a search provider** (details in [PROVIDERS.md](PROVIDERS.md)). Tavily is the simplest: 1,000 free credits a month, no card needed.
   ```ini
   PERSONALIZER_SEARCH_PROVIDERS=tavily,wikipedia
   TAVILY_API_KEY=tvly-...
   PERSONALIZER_CONTACT=https://your-site.example/contact   # or an email address; Wikimedia requires this
   PERSONALIZER_EXTRA_REPUTABLE_DOMAINS=                     # clear the demo domains
   ```
2. **Optionally enable Claude:**
   ```ini
   PERSONALIZER_LLM=anthropic
   ANTHROPIC_API_KEY=sk-ant-...
   PERSONALIZER_MODEL=claude-opus-5-5     # default; claude-sonnet-5-5 or claude-haiku-5-5 cost less
   PERSONALIZER_MAX_COST_USD=0.75         # hard per-run cap; beyond it, extraction falls back to heuristics
   ```
3. Run `python -m personalizer research …` or `serve` as above. Check the *Usage* line at the bottom of each brief for searches made, pages fetched, tokens used and estimated cost.

Before using it on real people in an organisation, read [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md); several items there need legal review.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "No search provider is configured or has credentials" | The provider is named in `PERSONALIZER_SEARCH_PROVIDERS` but its key is empty |
| `wikipedia search failed: … set PERSONALIZER_CONTACT` | Wikimedia rejects User-Agents without contact details; set `PERSONALIZER_CONTACT` |
| `… search failed: rate limited by provider` | Free-tier quota or rate limit reached; the provider is skipped for the rest of the run |
| Identity NOT resolved for someone you know is public | Add `organization` and `organization_domain`; check the spelling; the name may appear only with a different middle name |
| Many sources show `skipped: disallowed by robots.txt` | Expected; the site doesn't allow bots. The engine won't bypass it. |
| `LLM … failed; used heuristic extraction` | API error, refusal or budget cap; the run continues offline. Check the key and `PERSONALIZER_MAX_COST_USD`. |
| `422` with "outside scope" | The meeting purpose names a sensitive or private attribute; rephrase it as a professional goal |
| Brief feels repetitive | Heuristic mode quotes sentences; Claude mode writes more natural briefs |
