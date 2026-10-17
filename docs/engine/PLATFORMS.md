# Platform access matrix

Probes were run on **2026-10-17** from a network in India with User-Agent `PersonalizerResearchBot/0.1 (+contact)`. Only free, keyless requests were made. "robots `*`" means the rules for the generic user-agent. Re-run the probes before relying on this; platforms change their access often.

| Platform | Legitimate access routes | Tested result | Extractable content | Limits / terms | Mode |
|---|---|---|---|---|---|
| **Instagram** | (a) web pages; (b) Graph API *Business Discovery* (other business or creator accounts, via your own business account and an approved app); (c) oEmbed (needs an app token) | robots `*`: `Disallow: /`. oEmbed without a token: **400 OAuthException**. Graph API not tested: needs app review and a business account. | Via (b), if approved: captions and metadata of business or creator accounts only | Meta Platform Terms; app review | `discovery_only` (until a Meta app is approved) |
| **Facebook** | Web pages; Graph API with *Page Public Content Access* (app review) | robots `*`: `Disallow: /` (also on `graph.facebook.com`). API not tested: needs review. | Public Page posts, if approved | Meta Platform Terms | `discovery_only` |
| **LinkedIn** | Web pages; partner APIs only | robots `*`: `Disallow: /`. No public API for other members' profiles. | None | User agreement prohibits automated access | `discovery_only` |
| **X** | (a) web pages; (b) API v2, pay-per-use; (c) **oEmbed** `publish.x.com/oembed` | robots `*`: `Disallow: /`. **oEmbed: 200, returned post text, author and date** for a known post URL (no key). API not tested: paid. | Text of one known post via oEmbed; search and timelines via the paid API (about $0.005 per post read; free tier closed Feb 2026) | Developer terms; oEmbed is intended for embedding ⚖ | `oembed_record` (connector, **off by default**, ⚖) + `content_api` (paid, off) |
| **Reddit** | (a) web pages; (b) Data API (OAuth, approval needed; commercial use needs an agreement); (c) oEmbed | robots `*`: `Disallow: /`. **oEmbed: 200, returned post title and author** (no key). API not tested: needs approval. | One known post via oEmbed; more via the approved API | Responsible Builder Policy ⚖ | `oembed_record` (**off by default**, ⚖) |
| **TikTok** | Web pages; oEmbed; Research API (academics only) | robots.txt request: 302 redirect. oEmbed request: **redirected to `/in/about`** (geo-blocked from India) | Untestable from this network | — | `unavailable_here` |
| **YouTube** | Watch pages (robots allows); **oEmbed** (no key); Data API v3 (free key; 10,000 units/day; search = 100 units, `videos.list` = 1 unit) | robots `*` disallows only `/api/`, `/comment`, … **oEmbed: 200** (title, channel). Data API not tested: no key. | Title, channel, description (Data API); **no official route to other people's transcripts** | YouTube API ToS | `oembed_record` + `content_api` (with a key) |
| **Bluesky** | AppView XRPC API | `getProfile`: **200**; `getAuthorFeed`: **200** (full posts); `searchActors`: **200**; `searchPosts` without auth: **403** | Profile bio and links; every public post by a known account | Rate limits apply | `content_api` |
| **Mastodon** | Instance REST API | `mastodon.social/api/v2/search`: **200** without auth | Public posts and profiles (varies by instance) | Per-instance rules | `content_api` |
| **Medium** | Pages; **RSS** `medium.com/feed/@user` | robots allows profiles and posts; RSS: **200**. **Phase 2 live run: article pages returned 403 to our bot on 5 of 5** (bot protection despite robots allowing) | Full or partial articles via RSS | Not bypassed; provider-extract fallback is not used after a 403 | `feed` (pages: expect 403) |
| **Substack** | Pages; **RSS** `<pub>.substack.com/feed` | robots allows posts; RSS: **200** | Full or partial posts | — | `feed` + `fetch` |
| **Personal sites and blogs** | Pages, RSS, JSON-LD, `sameAs` links | Per site (robots.txt checked on every host) | Articles, `/about`, `/now`, `/uses` pages | Site terms | `fetch` + `feed` |
| **Podcasts** | iTunes Search API; show RSS | iTunes Search: **200** | Episode titles, descriptions, show notes | — | `content_api` + `feed` |
| **GitHub** | REST API | `/users/{u}`: **200** (60 requests/hour without a key) | Bio, links, repositories, README files | API ToS | `content_api` |
| **Spotify** | oEmbed | **200** (show or episode title) | Titles only | — | `oembed_record` (metadata) |
| **Wikipedia / Wikidata** | MediaWiki API, Wikidata API | MediaWiki: **200** (needs contact details in the User-Agent; 403 without) | Biography intros; official websites and social handles (Wikidata) | CC BY-SA attribution | `content_api` |
| People-search and data brokers | — | not probed (excluded by policy) | — | — | `excluded` |

## How modes work in the engine

- `discovery_only`: search results stay **leads**, of evidence kind `search_snippet`, with their limitations listed. The engine never requests the page. A snippet can support a finding at most at `tentative` (decision D3.2).
- `oembed_record` and `content_api`: the engine calls the official endpoint for a *specific* URL or account found during discovery. The response is stored as `oembed_record` or `api_record`, with the endpoint recorded.
- `unavailable_here`: the run reports "not accessible from this network", which is distinct from "no evidence".
- The mode table lives in `engine/platforms.py`. Changing a mode needs a code change with a note here: the date, the probe, and the reason.

## Re-running the probes

The phase-4 connector work adds `python -m engine probe-platforms` to repeat these checks and write a dated JSON record. Until then, the requests above can be repeated with `curl` using the URLs shown.
