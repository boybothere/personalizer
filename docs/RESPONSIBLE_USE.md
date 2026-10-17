# Responsible use, security and privacy

**Principle:** information being public does not by itself make it appropriate to collect or use. The engine is built to prepare for *professional* conversations using what people have chosen to make public about their work. It is not built to profile people.

## Safeguards implemented

| Risk | Safeguard | Where |
|---|---|---|
| Requests aimed at sensitive or private data | Meeting purpose screened; refused with 422 / exit code 2 | `safety.request_policy` |
| Search queries used as dorks for exposed data | Query policy blocks `filetype:`, `inurl:`, `intitle:`, `index of`, credential and personal-data terms (LLM-suggested queries too) | `safety.query_policy`, `queries.finalize` |
| Prohibited scraping / platform terms | Restricted platforms excluded at the search API and policy level, never fetched; robots.txt honoured; paywalls and logins skipped, never bypassed | `sources.py`, `fetch.py`, `providers.EXCLUDE` |
| People-search and data brokers | Domains blocked | `sources.DATA_BROKERS` |
| SSRF / internal network access | Public IPs only, checked on every redirect hop; credentials in URLs rejected | `fetch.is_public_host` |
| Untrusted HTML | Scripts, comments, hidden elements and invisible Unicode stripped; the UI renders text with `textContent`, never as HTML | `fetch.html_to_text`, `static/index.html` |
| Prompt injection | Redaction, per-page isolation, verbatim-excerpt verification, synthesis without page text, output validation, no model tools | See [ARCHITECTURE.md](ARCHITECTURE.md#prompt-injection-defence-in-depth) |
| Hallucinated facts | Excerpt must exist verbatim in the source; every brief item must cite a suitable finding | `extract.verify_excerpt`, `personalize.validate` |
| Identity confusion | Identity scoring with conflict detection; weak-identity pages excluded; "identity NOT resolved" banner | `identity.py` |
| Sensitive inferences | Special-category and private-life content excluded (religion, politics, health, sexuality, caste/ethnicity, family, finances/legal, location/identifiers); text not retained | `safety.sensitive_reason` |
| Creepy or deceptive use | Tone guard rejects fake familiarity and covert-research phrasing; personal interests only if self-disclosed or corroborated, always optional, with advice to cite the source openly | `safety.tone_problems`, `personalize.py` |
| Data minimisation | No hobby-targeted searches; raw page text never stored; excluded items stored as URL + reason only | `queries.py`, `models.SourceDocument` |
| Retention and deletion | 30-day default expiry, purge endpoint, per-report delete | `store.py` |
| Objection by the person researched | Opt-out list blocks future research and deletes existing reports | `/api/optout` |
| Correction | Feedback labels remove incorrect or inappropriate findings from the brief | `store.apply_feedback` |
| API keys | Read only from the environment or `.env` (git-ignored); `/api/health` reports whether a key is present, never its value | `config.py`, `.gitignore` |
| Exposure of reports | Binds to 127.0.0.1; optional bearer token compared in constant time | `api.py` |
| Runaway cost | Query, source and fetch caps; per-run LLM budget | `config.py`, `llm.py` |

## Known gaps (not yet implemented)

- No per-user accounts or audit log of who researched whom. Needed before any shared or multi-user deployment.
- Opt-out matches on name only. It can block another person with the same name, and can be evaded by spelling variants.
- DNS-rebinding protection is partial (resolve-then-connect, without pinning the IP).
- No encryption at rest for the SQLite file.
- The sensitive-content filter is keyword-based. It is deliberately over-inclusive, but it can still miss euphemisms. Claude mode adds a model-side `sensitive` flag; both should be measured on a larger labelled set.
- Wikipedia text is CC BY-SA. If excerpts are redistributed outside the organisation, attribution and licence terms apply.

## Privacy law: research inputs, not legal advice

> **Items marked ⚖ need review by qualified counsel before real-world use.** The summaries below are a starting point drawn from public sources as of October 2026.

### India: Digital Personal Data Protection Act, 2023 and DPDP Rules, 2025

- **Status.** MeitY notified the Rules on 13–14 November 2025. Most operational obligations (notice, security safeguards, breach reporting, erasure) take effect about 18 months later, around **May 2027**; consent-manager provisions take effect after about 12 months. Confirm the dates against the Gazette text.
- **Public-data exemption (s. 3(c)(ii)).** The Act does not apply to personal data *made publicly available by the data principal themselves* (or by someone under a legal obligation to publish it). A person's own bio, their own posts and their own talks arguably fall within this. **Articles written about them by others do not obviously fall within it.** ⚖
- **Lawful basis for the rest.** Outside the exemption, processing needs consent or a listed "legitimate use" (s. 7). Commercial meeting preparation does not plainly fit the legitimate uses. ⚖ This is the main open question for Indian deployment. Options: restrict to self-published sources, obtain consent (for example by telling meeting invitees), or rely on another basis counsel identifies.
- **Duties that would apply** if the Act applies: notice, purpose limitation, accuracy (the feedback and correction loop helps), reasonable security safeguards, erasure when the purpose is served (the retention default helps), breach notification, and grievance redress. ⚖ The engine has no notice mechanism today.

### EU / UK GDPR (if researching people in the EU/UK, or operating there)

- Lawful basis would likely be **legitimate interests (Art. 6(1)(f))**, which requires a documented balancing test. ⚖
- **Art. 14 notice** applies when data is not collected from the person. The "disproportionate effort" exemption is narrow. ⚖
- **Art. 9 special categories** are excluded by design. Art. 17 (erasure) and Art. 21 (objection) are supported by delete and opt-out, though they would need identity verification for real requests.
- A **DPIA** is likely advisable, since this is profiling of individuals. ⚖

### United States (CCPA/CPRA and state laws)

- "Publicly available information" is largely excluded from "personal information", but **inferences drawn** to profile someone can be personal information. ⚖ The engine avoids inferences, and its "hypothesis" tier is not shown in briefs.

### Other obligations to check ⚖

- Each search provider's terms on caching, storage and AI use (Brave storage rights especially).
- Employer policy: this tool should be limited to legitimate business meetings, with a stated purpose, and not used for vetting, hiring decisions or monitoring. Using it for employment screening could trigger additional law (for example FCRA in the US).
- Cross-border transfers: sending page text to an LLM API moves personal data to that provider's region. Review the data-processing terms. ⚖

## Acceptable use (recommended policy)

Use it to prepare for a scheduled professional meeting with a stated purpose. Don't use it to:

- research people who aren't meeting you;
- screen candidates or employees;
- monitor anyone;
- look into anyone's personal life;
- probe for negotiation "leverage".

Be open in the meeting about where you learned something. Correct or delete findings that turn out to be wrong.
