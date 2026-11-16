# yyc-mcp: YYC's news-monitoring data as a REST API

Deyao, 2026-10-16: an API to search through the database, with authentication. Deyao, 2026-11-04: no MCP: AI uses
REST with skills (YYC AI's `newswatch` skill, through its tools role), so the MCP endpoint, the OAuth server that signed
AI apps in, the connect page and the labels forwarding are gone. Who may use it = the same list as every YYC app
(the access catalogue's `users`, admins = `admins`; "Who may use it"); no hand approvals. The name stays yyc-mcp (and the address
mcp.yyc-internal.com).

- **REST API:** `https://mcp.yyc-internal.com/api/v1/<tool>` (GET query string or POST JSON), `Authorization: Bearer`
  with a personal or service token; spec: `/api/v1/openapi.json` (OpenAPI 3.1, public)
- **Front page:** `https://mcp.yyc-internal.com/` (Traditional Chinese: the API and how to get a token)
- **Personal tokens:** `/tokens` (YYC sign-in; catalogue users only)
- **Call log:** `/admin` and `/admin/api/audit` (the catalogue's `admins`)

Read-only: every tool reads YYC's Postgres in the cluster as role `yyc_readonly` (`DATABASE_URL`, database `app`),
through the site's own modules in `../web-app/src/server/` (search, 數據分析, item page, 串流, 報告). The server's own
state is database `mcp` (role `yyc_mcp`, `MCP_DATABASE_URL`). Nothing writes to the news stream's data and nothing
calls a paid model: this API adds no AI cost.

## How it fits together

```
YYC AI's tools role (data_* tools, service token YYC_JARVIS_MCP_TOKEN + X-YYC-User), scripts (personal tokens)
  ▼
https://mcp.yyc-internal.com (tunnel yyc-stream → cloudflared → Service yyc-mcp:80;
                              Deployment yyc-mcp in namespace yyc, 2 replicas, Node: src/node/server.ts)
  ├─ /api/v1/<tool>: bearer → SERVICE_TOKENS or a personal token (yyc_pat_…, registry.ts) → the catalogue → the tool
  ├─ /tokens, /admin: Cloudflare Access sign-in (OIDC + PKCE, Access for SaaS app "YYC MCP sign-in", signin.ts)
  ├─ catalogue.ts: YYC's access catalogue (Secret yyc-catalogue at /etc/yyc-catalogue/config.json, re-read every 30 s)
  ├─ Registry (database mcp): personal tokens (SHA-256 only), per-account rate limit, call log
  └─ tools.ts: one registry of 28 read-only tools → REST, OpenAPI
       (wisers.ts: the wisers_* tools, through the in-cluster relay http://wisers.yyc.svc.cluster.local:8899 with
        WISERS_INTERNAL_TOKEN)
```

Every call is admitted first (`Registry.admit`): the account must be on the catalogue now (a removal stops its tokens
too, within about a minute and a half: the kubelet's update of the mounted file plus the 30 s re-read;
`SERVICE_TOKENS` identities skip this) and under `RATE_PER_MIN` (120) requests a minute. Then the call is logged
(account, tool, client, status, time, arguments; kept a year). The admins read the log on `/admin` or
`/admin/api/audit`.

Sign-in for the web pages: Access OIDC app `YYC MCP sign-in` (tofu `../deploy/cloudflare/tofu/access.tf`
`mcp_signin`, redirect `https://mcp.yyc-internal.com/callback`, issuer
`https://yyc-hk.cloudflareaccess.com/cdn-cgi/access/sso/oidc/<client id>` = `OIDC_ISSUER` in
`../deploy/apps/yyc-mcp.yaml`); client id/secret = tofu outputs `mcp_oidc_*` → store `yyc` `YYC_MCP_OIDC_CLIENT_*` →
Secret yyc/mcp-env. Access signs in only an address it has verified (a Google account's, or one an e-mailed code
reached). `/tokens` and `/admin` keep a signed 12-hour session cookie.

## Tools

| Tool | What |
|---|---|
| `streams_list` | the streams (id, name, brief, threshold), labels, sentiments, corpus size |
| `posts_search` | full-text search (search.py's port: jieba + OpenCC, 繁/简/English) + stream / min_score / dates / types / outlets / regions / platforms; a stream alone lists its posts |
| `post_get` | one post in full: text, transcripts, picture text, comments, per-stream scores and reasons, same-article posts |
| `stream_items` | a stream's filed items for a day (串流), with scores |
| `analysis_posts`, `analysis_hot_topics`, `analysis_topic`, `analysis_person`, `analysis_themes` | 數據分析: 帖子 (facets incl. people), 熱門話題, one topic, 人物, 主題 |
| `analysis_overview`, `analysis_authors`, `analysis_sources`, `analysis_trend`, `analysis_terms` | 數據分析: 摘要 (metrics, trend, the daily scores SPI/CSS/DVS/DPS/CCS), 作者, 來源, 趨勢, 熱門詞. They count posts one per vote, so their descriptions and every answer (`note`, `note_zh`) say they depend on which sources and keywords YYC follows |
| `daily_summary` | the scheduled daily AI summary of a stream; when there is none, `generated: false` with the reason for each day looked up (from that day's report: e.g. no posts in the stream) and the newest summary's day |
| `reports_list`, `report_file` | the daily report files; text formats as text |
| `sources_list` | every outlet/platform in the corpus |
| `search`, `fetch` | ChatGPT's deep-research / company-knowledge shape (`{results:[{id,title,url}]}`, `{id,title,text,url,metadata}`) |
| `wisers_search`, `wisers_job` | WiseSearch (Wisers' archive of HK and Greater China print, web, social, forums) on YYC's WisersOne account through the relay (`../wisers/relay.py`); a search is a job (state running → `wisers_job`, free). **Costs Wisers search quota**; `reason` required; the same search within 24 h answers from memory |
| `wisers_doc` | up to 10 documents in full: YYC's corpus first (free), else Wisers (**document quota**); heading, same print page, page-image count |
| `wisers_facets` | a search's tab counts, media/regions, keyword match, label counts, hot keywords, negative hot words, similar articles, an excerpt, merge counts (the search's own criteria; cached per search; quota cost unknown, logged) |
| `wisers_options`, `wisers_labels`, `wisers_media`, `wisers_log` | the search options and account switches, the label trees, media lookup and profiles, the relay's usage log (all free) |

**WiseSearch (2026-10-29):** the wisers_* tools call `WISERS_API` (the relay inside the cluster,
http://wisers.yyc.svc.cluster.local:8899/app/wisers/api/) with `Authorization: Bearer` `WISERS_INTERNAL_TOKEN` (store
`yyc` `YYC_WISERS_INTERNAL_TOKEN`; its SHA-256 is in the relay's `WISERS_INTERNAL_TOKENS`, and NetworkPolicy
wisers-ingress admits yyc-mcp) and `X-YYC-User` = the account, or, for a service token only, the staff member its app
names (YYC AI), so the relay's logs say who spent the quota. The relay caps AI callers per person and hour
(`WISERS_AI_CAP`) and relays only WiseSearch's read-only calls (`../wisers/paths.py`). YYC AI lists them in
`yyc-ai/deploy/tools/tools.json` (`wisers_*`).

**A post is one shape everywhere (Deyao, 2026-10-27):** every list of posts, whichever tool returns it (`posts_search`,
`analysis_posts`, `analysis_topic`, `analysis_person` `recent`, `stream_items`, `post_get` `same_article`, `search`),
is built by `post()` in `src/tools.ts`: `id`, `title`, `url`, `dashboard_url`, `item`, `source` (the outlet), `board`,
`author`, `type`, `region`, `platform`, `time`, `date_only`, `extract` (300 characters), `engagement`, then what the
query says about it (`score`, `reason`, `match`, `sentiment`, `labels`, `theme`, `stream`, `category`, `comment`);
empty fields left out. `post_get` is the same post with its full text, comments, transcripts, scores and tags.

**Links to the site (`dashboard_url`, Deyao 2026-10-21):** every item in an answer that has a details page on the
monitoring site carries `dashboard_url`, an absolute link built by one helper (`../web-app/src/lib/dashboard_url.ts`;
Python twin `../dashboard_url.py` for the AI server's /app/api/v1; `tests/test_dashboard_url.py`). The
tool descriptions and the server instructions tell models to link it when they cite an item. Existing fields (`item`,
`url`, `urls`) are unchanged.

| Item | Tools | Page |
|---|---|---|
| post | `posts_search`, `post_get` (+ `same_article`), `analysis_posts`, `analysis_topic`, `analysis_person` (`recent`), `fetch` (`metadata`) | `https://newswatch.yyc-internal.com/item/<id>` |
| filed item (串流) | `stream_items` (`items[]`; the answer's own: the 串流 day) | `/item?p=<stream>&url=<url>` (redirects to `/item/<id>`); `/?p=<stream>&d=<day>` |
| stream | `streams_list` (shown streams: `dashboard_url` = 串流, `analysis_url` = 數據分析) | `/?p=<id>`, `/dashboard?p=<id>` |
| hot topic | `analysis_hot_topics` (`topics[]`; the answer's own: the 熱門話題 view), `analysis_topic` | `/dashboard?v=hot&p=…&from=…&to=…[&filters]&tid=<id>` (the id holds only with the same period and filters, so the link carries them) |
| author | `analysis_authors` (`authors[]`), `analysis_topic` (`top_authors[]`), `analysis_person` | `/dashboard?v=persona&p=…&from=…&to=…&a=<author>` |
| theme | `analysis_themes` (`counts[]`; the answer's own: the 主題 view) | `/dashboard?v=posts&p=…&from=…&to=…&theme=<theme>` |
| source | `analysis_sources` (`sources[]`), `analysis_topic` (`top_sources[]`), `analysis_person` (`websites[]`, with a stream) | `/dashboard?v=posts&p=…&from=…&to=…&site=<outlet>` |
| view | `analysis_posts`, `analysis_overview`, `analysis_authors`, `analysis_sources`, `analysis_themes`, `analysis_trend` (top level) | `/dashboard?v=<view>&p=…&from=…&to=…` |
| report file | `reports_list` (`dashboard_urls`, `dashboard_url` = 報告), `report_file` | `https://yyc-beta.deyaochen.com/reports/<day>/<stream>/<file>` |
| a person's hot term | `analysis_person` (`hot_terms[]`) | 搜尋: `https://yyc-beta.deyaochen.com/search?q="<term>" AND FindIn(author: "<author>")&from=…&to=…&comments=1` (the terms come from all of the author's posts, any stream, a year before the period to its end; FindIn matches the name as a substring) |

Host: 串流, the item page and 數據分析 are on the main site for everyone; 報告 (and 搜尋) are on only on the beta site
(flags.json `reports`/`search` target `yyc-beta.deyaochen.com`, its own Access app), so report links go there (the old
`urls` field still points at the main site, where /reports answers 404). A term (`analysis_terms` `terms[]`) links 數據分析's posts with
that keyword; a trend period (`analysis_trend`, `analysis_overview` `trend[]`, `spi_series[]`) the posts of that day or
week; `post_get`'s `filed_in[]` the 串流 day, `scores[]` and `tags[]` the stream's pages. `sources_list` (no stream to
show it in), a person's boards (數據分析 has no board filter), a topic's `trend[]` days (no page lists one topic's posts on
one day; the answer's `trend_links` says so) and `daily_summary` (the AI summary shows nowhere while the `ai` flag is off)
have no details page, so no link. A term links 數據分析's posts with the term as a phrase (`q="<term>"`), as the site's
熱門詞 cloud does.

`posts_search` with a query of words alone (one term, or terms any of which may match), sorted by date, runs the
search split in two (`web-app/src/server/search/split.ts`, shared with the site's 搜尋): the word matches from the
words index alone, then the posts whose grams index entry holds a term and whose text holds it as a substring. Same
answer as the site's one query, without reading the stored text of every word match (the one query timed out at 20 s
on common words such as 香港). It runs the word matches first: when they fill the page, the substring part (seconds on
a common term even with warm caches) is skipped, `total` counts the word matches only and the answer says
`total_exact: false`. Other queries (AND, NOT, NEARBY, FindIn, IncData, relevance order) run the site's query. A term of only a-z/0-9 (trump,
housing) finds its substring matches in table `post_subs` (the suffixes of each post's Latin runs, GIN prefix lookup;
corpus.SCHEMA), the site's search included, once kv `search:subs` says every post has its row; before that it keeps
the grams + text test, which reads the text of ~30k candidates for such a term.

When the database lacks the search dictionary (kv `search:edge`, published by the poller), `posts_search` falls back to a
substring match and says so (`search_mode`).

## Who may use it

The `users` of YYC's access catalogue (`"@yyc.hk"` = everyone there), the same list as every YYC app; `admins` also read
the call log. The catalogue is the store's `YYC_CATALOGUE_JSON`, never a file in git; `/infra/catalogue.sh` changes it
(`/yyc-ai/README.md` "The staff allowlist"): tofu updates the Access policy "YYC users" and the Secret
yyc/yyc-catalogue is sealed again, which the server re-reads. Someone off the list is refused at /tokens' sign-in (「沒有權限」), and their tokens stop working.

Service tokens (`SERVICE_TOKENS` in `../deploy/apps/yyc-mcp.yaml`: SHA-256 → identity; YYC AI's
`yyc-ai@ai.yyc-internal.com` = store `YYC_JARVIS_MCP_TOKEN`, Deyao's sessions' `YYC_MCP_TOKEN` =
chendeyao000@gmail.com) are outside the catalogue with the same rate limit and audit; only a service token may name
the staff member it acts for in `X-YYC-User`. Call log from a script: `infra/mcp-access.py audit [N] [EMAIL]`, or
`curl -H "Authorization: Bearer $YYC_MCP_TOKEN" "https://mcp.yyc-internal.com/admin/api/audit?limit=50"`.

## Develop, test, deploy

```bash
cd news-stream/mcp-edge && npm ci && npx tsc --noEmit && node build.mjs && npm test
MCP_DB=yyc_dev_own MCP_PG_PORT=5432 python3 ../tests/test_mcp_edge.py   # the Node server + a fake Access OIDC app + a temporary catalogue
```

T04, T05, T08 and T09 need real data in `MCP_DB` (a dev copy with the search dictionary: `kv` and `search_words`); the
others run on the schema alone. Deploy = push: workflow `mcp-image` (`.github/workflows/mcp-image.yml`) builds
`ghcr.io/de0ch/yyc-mcp`, smoke-tests it and pins the tag in `../deploy/apps/kustomization.yaml`; Flux rolls it
(maxSurge 1 / maxUnavailable 0, preStop 10 s, then the server drains its requests and audit writes).
