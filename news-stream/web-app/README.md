# web-app: the YYC 新聞監察 site (the edge app)

Every page of https://newswatch.yyc-internal.com/ (and the beta host). TanStack Start: React 19 pages rendered on the server by
the Cloudflare Worker `yyc-web` (YYC's own Cloudflare account) and hydrated in the browser; TanStack Router (URLs,
typed search params) and TanStack Query (data); Vite with Cloudflare's plugin; TypeScript; Tailwind v4 (theme +
utilities, without preflight: the shell's own base styles apply). Read-only: the site shows data and never writes it.
UI text is Traditional Chinese with Hong Kong usage; the system's CJK face, with Archivo (bundled, `@fontsource/archivo`)
for figures ("Look", below). Where it sits in the whole system: `../ARCHITECTURE.md`.

## How a request is answered

1. Worker `yyc-edge` (`../edge/worker.js`, behind Cloudflare Access) sends every path but the cluster's (the AI
   server's POSTs, `/api/ai/estimate`, `/app/api/v1/*`, `/app/login`, `/healthz`, `/app/wisers/api/`) to this
   Worker's workers.dev address with the `X-YYC-Edge` secret and the original host in `X-Forwarded-Host`.
2. `src/server.ts` refuses anything without the secret (so workers.dev is no way around Access), serves `/live`
   (below), then `src/server/gates.ts`: Deyao's monitor is a plain 404 to every identity but his; a section behind a
   feature flag that is off for the reader's host answers 404 `{"error": "not found"}`.
3. TanStack Start renders the route (`src/routes/`): its loader fetches the page's data through server functions on the
   Worker, the HTML goes out with the data dehydrated into it, and the browser takes over.

- **Server functions** (`src/fns.ts`, `fns-dashboard.ts`, `fns-search.ts`) run on the Worker, next to the database
  (placement `aws:eu-central-1`, Frankfurt: the placement region nearest YYC's Postgres in Hetzner fsn1), and are called from route loaders and from TanStack Query in the
  browser. Public data only; what is for approved accounts is under `/app/` (`app.me.ts`, `app.item.text.ts`,
  `app.item.ai.ts`, `app.item.video.ts`, `app.stream.scores.ts`), where `src/server/access.ts` checks Cloudflare
  Access's signed JWT again.
- **What only approved accounts see comes with the page** (Deyao, 2026-11-17; the full text, comments and transcripts,
  the scores and reasons, the saved video's links): a route's loader reads it together with the public data
  (`src/lib/load.ts` `loadGated`, the queries in `src/queries.ts`), once the reader is known to be an approved account.
  - *The first page (rendered on the server):* it is in the HTML, so it shows before any script runs and the browser
    asks for none of it again. The server learns who is reading in `src/server/reader.ts`: on the beta site Access
    sends its token on every path; a public page of the main site (`/`, `/item/<id>`) is outside the Access app and
    carries only Access's cookie (`CF_Authorization`, set for the whole host: the Access app must keep its cookie
    path attribute off), which the Worker never judges itself: it asks `/app/me` on the same site with that cookie
    (once per request, 1.5 s at most), so Cloudflare Access decides as for the browser's own requests, and `/app/me`
    answers from the token Access then sends. No cookie (nothing is asked), a cookie Access did not take, or no
    answer: the page goes out without saying who reads, and the browser asks `/app/me` as before; so the server only
    ever adds to what a reader gets. These reads are
    plain code inside the render (`createIsomorphicFn` in `src/api.ts`), never server functions: a server function is
    a public URL.
  - *A page change in the browser* (a hover or touch preloading a link, or the tap): the loader requests
    `/app/item/text`, `/app/item/ai` and `/app/stream/scores` at the same moment as the public data, not after the
    page has drawn; `/app/me` is asked first only when its answer is not in the cache yet.
- **Data:** YYC's Postgres through Cloudflare Hyperdrive (binding `HYPERDRIVE`, config `yyc-pg`, read-only role
  `yyc_edge`, query cache off), with the `postgres` client (`src/server/db.ts`: one client per request).
- **What Python publishes for the app:** kv `edge:config` (`../edge_config.py`, every minute from the poller: streams,
  shown and scored ones, thresholds, flags per host, profiles, labels and their Chinese names, the scorer's constants;
  `src/server/config.ts`) and the search dictionary (`searchtext.publish_edge`: table `search_words`, kv `search:edge`,
  `search:opencc`).
- **Live updates:** `GET /live?t=<topic>` is an open page's WebSocket, held by the Durable Object `LiveHub`
  (hibernating); the poller's `live.py` posts `POST /live/notify {"topics": [...]}` (with `X-Live-Secret`) when Postgres
  triggers announce a change, and the hub tells the pages showing those topics to read again (`src/live.ts`:
  `useLive(queryKey)`; every 60 s while the socket is down; at once when the page comes back).
- **A newer deploy:** every open page hears of it within seconds and updates itself at a safe moment (below,
  "Updates reach open pages").

## Where things are

| Path | What |
|---|---|
| `src/server.ts` | the Worker: the edge secret, `/live`, the gates, TanStack Start's handler; class `LiveHub` |
| `src/router.tsx`, `src/routes/` | every route and its search params (plain `?a=b`); `-<Name>Page.tsx` are the pages (the `-` keeps them out of the router), `__root.tsx` renders the shell; preload on intent (`defaultPreload: "intent"`: hover 75 ms, touchstart at once; off on Save-Data/2G), each loader puts its data into the query cache (`ensureQueryData`, not awaited) so the click reuses it; for an approved account the loader reads the approved-only data the same way (`loadGated`) |
| `src/shell/` | the app shell (top bar, sections, stream picker, phone tab bar) and the stream shown (`?p=` + the cookie `yyc-profile`, read on the server: `src/lib/remembered.ts`); `../NAVIGATION.md` |
| `src/api.ts`, `api-dashboard.ts`, `api-search.ts`, `src/queries*.ts` | the pages' data calls and query keys; the persona button calls the cluster (`/api/ai/estimate`, `POST /app/ai/persona`) |
| `src/server/feed.ts` | 串流's feed (one HKT day of every shown stream, the days, source status) |
| `src/server/item.ts` | the item page (a port of `../item.py`: the post, its thread, tags, scoring, coverage, article, media, pictures; `aiScores` for approved accounts) |
| `src/server/dashboard.ts`, `analytics.ts`, `dashboard-text.ts`, `topics.ts`, `dashboard-export.ts`, `xlsx.ts` | 數據分析 (insights.py's select, hot topics, topic, themes, persona, the AI 每日摘要 and "what the AI read"; `analytics.ts`: 摘要's metrics and trend, the daily scores, 作者, 來源, 趨勢, hot terms, each answer with the selection-bias `note`), its text box, its Excel exports (a dependency-free .xlsx writer; `attachment()` names downloads per RFC 6266) |
| `src/server/search/` | 搜尋 (ports of search.py, query.py, searchtext.py with jieba and OpenCC as Python runs them); `split.ts`: a query of words alone, sorted by date, as one statement that reads the stored text of substring-only candidates only (page and facets together; yyc-mcp's posts_search uses it too) |
| `src/server/reports.ts` | 報告: the listing and the stored files (table `report_files`, written by `../reports.py`) |
| `src/server/monitor.ts` | Deyao's monitor: kv `monitor:data` (published by `../monitor.py`), never the cluster |
| `src/server/scores.ts`, `site.ts`, `titles.ts`, `time.ts`, `kv.ts`, `config.ts`, `access.ts`, `reader.ts`, `gates.ts`, `db.ts` | 串流's score line, profiles and flags, AI titles, Python's datetime semantics, kv reads, `edge:config`, Access, who is reading a page rendered on the server, the gates, the database |
| `src/lib/` | client helpers: `clock.ts` (the server's clock while hydrating, so the first render matches its HTML), `bias.ts` + `BiasNote.tsx` (the selection-bias sentence, shown once per view whose figures count posts one per vote: `../README.md` "Figures that depend on our selection"), `freshness.ts` (串流's 即時更新 label: NAVIGATION.md "串流: is it current?"), `pending.ts` (處理中), `build.ts` + `update.ts` (a new deploy reaching open pages, below), `time.ts` |
| `scripts/call.ts` | runs one server function in Node against `DATABASE_URL` and prints its JSON (the Python tests and the parity harness) |

## Look

Deyao's house style (claude-env skill `deyao-web-design`, from DE0CH/china-train): square edges
everywhere, flat white sheets on a grey ground with 1 px rules, no shadows, gradients or motion, inline SVG icons
(`src/lib/icons.tsx`: stroke 2, square caps), controls at least 44 px, no page-level sideways scroll at 375 px, dark mode
designed (not inverted). `src/styles/house.css` holds the tokens and shared parts; each page restyles them in its own file.

- **Colour only means something, one meaning per hue.** On the public pages the only hue category is an article's
  sentiment: 負面 `--c-1` crimson, 正面 `--c-3` green, 混合 `--c-4` ochre, 反諷 `--c-2` blue, 中性 ink, none yet dashed
  muted. Labels, scores and status carry no hue: ink-filled = on / passes / live, hollow = not, dashed = waiting,
  ink text with an SVG cross = failed. Search hits use `--hit`. Deyao's monitor gives its providers `--c-1`..`--c-7`
  (with a legend); the live dashboard keeps its pixel font and uses hue only for bad / ok / warn.
- **Lists are rows, not a line:** nothing on the site is a route, so the train planner's line diagram (station nodes,
  tracks, interchanges) is not used. 串流's list, 數據分析's post lists, the item page's related posts and the Wisers
  lists are ruled rows on a sheet, each time small and muted (`--num`, 0.875 rem). An article row shows its sentiment as
  a 4 px rule down its left edge in the sentiment hue (`.sm`, legend swatch `.sw`; dashed muted while it has none).
  串流's half-hours are plain quiet group header rows. The item page's 發佈 / 首次收錄 are two label/time pairs.
- **Type:** body 16 px, nothing under 12 px; figures in `--num` (Archivo, tabular). No font CDN (blocked in mainland
  China).
- **Exempt from 44 px:** the live dashboard (fixed px canvas) and proof's laptop layout (dense, keyboard-driven).
- **Clean lines (Swiss; Deyao, 2026-10-30):** every bar sits on the one `--panel` ground with a single 1 px `--rule`
  under each row (`.yyc-row` 56 px, `.yyc-sub` / `.yyc-tier` 44 px); cells in a row share their edges (`.cellrow`,
  `.cellgrid`, `.range`, `.seg`), a filled cell keeps its row's rules, level 2's current tab is a 3 px ink bar on the
  baseline; one left edge (`--gutter`) for bars and sheets. Meta strings join with "，" or ", ", never " · ".
- Glyphs inside copy ("‹ 上一頁", "→") are text, not icons, and stay as written.

## Routes

| URL | Page |
|---|---|
| `/?p=<stream>&d=<YYYY-MM-DD>&q=&sent=&lab=` | **串流**: day row, the stream list with each stream's sentiment bar and filters, the timeline strip, the 情緒/標籤/主要來源 column (from 960 px; ending with the selection-bias note, once), one list in send order with CEHK's category as a label on each card, the score/reason line for approved accounts, source status |
| `/item/<id>`, `/item?url=<url>` (302 to `/item/<id>`), `/post/<id>` (301 to `/item/<id>#text`) | **item page**; parts not produced yet show 處理中 (below) |
| `/dashboard?v=&p=&from=&to=&…` | **數據分析**: 摘要 · 帖子 · 熱門話題 · 作者 · 來源 · 趨勢 · 主題 · 人物, drill-downs, Excel at `/api/export/{summary,posts,hot_topics,authors,sources,trend,themes}.xlsx`; the AI buttons (flag `ai`) run on the cluster |
| `/search?q=&type=&site=&region=&from=&to=&sort=&title=1&comments=1&page=` | **搜尋** (flag `search`); lists are comma-separated, an old link with repeated keys gets one 302 to the comma form; `/api/search`, `/api/search/meta`, `/api/export/search.xlsx` |
| `/reports/?p=<stream>&all=1` | **報告** (flag `reports`); files at `/reports/<day>/<profile>/<file>`, `/api/reports` |
| `/app/monitor`, `/app/monitor/data` | **Deyao's monitor** (English, its own bar, not linked; 404 to anyone else; X-Robots-Tag noindex) |
| `/app/wisers/`, `/app/wisers/search?q=…`, `/app/wisers/doc/<docId>`, `/app/wisers/browse`, `/app/wisers/print`, `/app/wisers/media/<id>`, `/app/wisers/log`, `/app/wisers/saved`, `/app/wisers/view/<file>` | **WiseSearch 轉接** (`src/routes/-wisers/`, `src/lib/wisers.ts`; its own bar; only tokens of its own Access app, `WISERS_AUD`, else a plain 404; data from the relay's JSON API `/app/wisers/api/` on the cluster; `../README.md` "WiseSearch 轉接") |
| `/app/me`, `/app/item/text`, `/app/item/ai`, `/app/item/video`, `/app/stream/scores` | approved accounts' reads (403 otherwise) |
| `/version` | the build id |

## Deploy

`.github/workflows/web-app.yml` on every push touching `news-stream/web-app/`: `npm ci`, typecheck, `vite build`,
`scripts/keep-assets.mjs` (below), `wrangler deploy` (repo secrets `YYC_CF_API_TOKEN`, `YYC_CF_ACCOUNT_ID`). A Worker deploy is atomic: requests in flight
finish on the old version. The Worker's secrets (`EDGE_SECRET`, `LIVE_SECRET`) are set with `wrangler secret put`;
`wrangler.jsonc` holds the bindings (Hyperdrive, the LiveHub Durable Object, placement).

## Updates reach open pages (Deyao, 2026-10-20), never by reloading them (Deyao, 2026-10-22)

Deyao: "UI updates should be pushed to users quite quickly, and there should be a mechanism to invalidate stale browser
cache so they see updates when they are pushed"; and "new ui shouldn't refresh the page, because refreshing the page
makes my live stream exit full screen, and shows the loading bar". An open page knows of a new build within seconds of a
deploy and moves to it on the reader's next page change; a deploy never reloads a page in place. Test:
`tests/test_ui_update.py` (two real builds in the Workers runtime, a deploy between them, WebKit iPhone and Chromium;
it logs every document a tab loads and fails on any the reader did not ask for; 2026-10-22: every page knew of B
3.5 s (Chromium) and 3.8 s (WebKit) after B was up, no document was reloaded, the live dashboard stayed in full screen;
the same test fails on the previous code, which reloaded). Live check: `tests/live_ui_update.py`.

- **Prior art.** SvelteKit's `updated` store with `beforeNavigate`: on a new version, the next navigation sets
  `location.href` instead of routing client-side (svelte.dev/docs/kit/$app-navigation, `kit.version.pollInterval`).
  Next.js `deploymentId`: on a deployment mismatch, page transitions are hard navigations (vercel/next.js discussion
  88327; Vercel Skew Protection keeps the old deployment's files). Vite's `vite:preloadError` docs reload in place,
  which can loop when the new file is missing too (vitejs/vite#11804; paulau.dev "Handle version skew after new
  deployment with Vite and Vue Router": navigate to the target instead). TanStack Router's lazy route component also
  reloads in place on a missing chunk (`lazyRouteComponent.js`, once per message): `lib/update.ts` sets the key it
  checks first, so it never does.
- **Caching.** Documents: `private, no-cache` (`/app/…`, and every page rendered for an approved account, which may
  carry the full text: `private, no-store`), set in `server/gates.ts` `finish()`, so
  a reload, a new tab or Back without the back/forward cache always gets the current deploy's page; nothing in front
  caches them (the router `yyc-edge` passes requests through; Cloudflare does not cache Worker responses). Hashed files
  `/assets/*`: `public, max-age=31536000, immutable` (`public/_headers`). `/version`: `no-store`. No service worker.
- **Old files stay.** A Worker deploy serves only the files it uploads, so `scripts/keep-assets.mjs` (after `vite build`)
  reads the live site's `/build-assets.json` (every hashed file and the last time a deploy had it), copies each file of
  the last 14 days that the new build lacks into `dist/client/assets/`, and writes the new list. A tab still on an older
  build loads its lazy files as before. An `/assets/` file nobody has answers a plain 404 (`server.ts`).
- **Old pages keep reading data.** A page that nobody navigates runs its build for as long as it stays open (the live
  dashboard swaps its frame for the new build within a minute, but its frame of the old build reads until then), against the newest server. Its reads are the server functions (`fns*.ts`, URL
  `/_serverFn/<sha256 of file and export name>`: the same id in every build) and plain routes (`/app/live/data`,
  `/api/…`). So a change to what one of them takes or returns stays readable by the previous build for the 14 days its
  files are kept (add fields, never rename or drop one), or it gets a new name and the old one stays for those 14 days.
- **Hearing of a deploy.** Every page holds the LiveHub socket (`src/live.ts`, also on pages without live data). The
  first request each new Worker instance serves sends its build to the hub (`POST https://hub/build`, internal to the
  Worker), and the hub tells every socket `{"build": <id>, "announce": true}` the first time it hears that id; the
  workflow's last step requests the site, so that happens right after `wrangler deploy`. Cloudflare releases new code to
  the Worker at once and to the Durable Object eventually (2026-10-20: the hub ran the old code for minutes after
  `/version` answered the new build), so this path works whichever code the hub runs. The hub also says its own build
  when a socket connects and in reply to every ping (50 s). Any id from the hub is only a hint: the page asks
  `/version` (the Worker), which decides; after an announcement it asks again at 2, 5, 10, 20 and 40 s until `/version`
  agrees (2026-10-20: the announcement reached pages 2 s before `/version` changed). `/version` is also asked on every
  socket (re)connect, every minute, on boot (an old page from the HTTP cache), on coming back on screen, on a
  back/forward-cache restore, focus, online, and when a data request fails. The page's build is `<html data-build>`;
  `data-update` is `current`, `ready` (a newer build is live) or `broken` (the note below is showing).
- **Moving to the new build** (`src/lib/update.ts`): with a newer build live, the next link (or a page change the page
  makes itself: a form, Back inside the app) is a full load of the new build instead of a client-side transition, at
  the moment the reader expects a new page. Nothing else: no reload when the page is hidden, comes back on screen, sits
  idle or is restored from the back/forward cache, and no note. Links stay client-side while a full load would lose
  something: text typed into a field and not sent (not submitted and not in the URL), an AI answer in progress
  (`setBusy`), a WiseSearch page (loading it again would search Wisers again).
- **When a page cannot go on.** A data request (TanStack Query) that fails while a newer build is live shows
  已有更新 · 點此重新載入 at the bottom (`.yyc-update`, no popup; hidden on the live dashboard, which shows its own read
  failure); the next read that works takes it away again. Only a tap reloads (URL kept; the scroll put back from
  sessionStorage `yyc-update-scroll` after hydration).
- **A file that fails to load** (`vite:preloadError`: a file older than the 14 days kept, or a network error) during a
  page change makes that page change a full load of its URL, at most once per URL a minute (sessionStorage
  `yyc-update-filenav`), so a file missing from the new build too never loops; outside a page change it only marks the
  build as behind (the next page change is a full load).
- **Back/forward cache.** The socket closes on `pagehide` (an open socket keeps a page out of Chrome's and Safari's
  cache) and opens again on `pageshow`; a restored page asks `/version` and, on a newer build, its next link loads it.

## Local development

Against a local Postgres holding a copy of real data (never the shared `yyc_dev_spa` itself: `pg_dump -Fc yyc_dev_spa |
pg_restore -d yyc_dev_<own>`), with the search dictionary published into it (`DATABASE_URL=postgresql:///yyc_dev_<own>
python3 -c 'import searchtext; searchtext.publish_edge()'`) and the site's config (`python3 -c 'import edge_config;
edge_config.publish()'`); the dev server reaches Postgres over TCP:

```bash
cd news-stream/web-app
npm ci
cp .dev.vars.example .dev.vars
CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=postgres://claude:dev@127.0.0.1:5432/yyc_dev_own npx vite dev --port 5185
npm run build        # vite build + licences + typecheck
```

`npm run dev` runs the Worker in workerd. Tests sign in with their own key: in `vite dev` (`import.meta.env.DEV`) or a
local `vite preview` (requests to 127.0.0.1 / localhost), `ACCESS_TEST_JWKS` in the dev vars (a JWKS JSON) is the key
Access tokens are checked against (`server/access.ts`); on Cloudflare only Cloudflare's keys count.

## Tests

- **Pages** (Playwright against a dev server or a preview of a build, WebKit iPhone 390 px and Chromium 1600 px):
  `tests/test_web_app_pages.py` (串流 and the item page), `test_dashboard_spa.py`, `test_search_spa.py`,
  `test_agent_page.py` (a preview, `CLOUDFLARE_ENV=agent` with `.dev.vars.agent`, a fake NDJSON cluster),
  `test_edge_ops.py` (報告 and the monitor, its own `vite dev`), `test_spa_preload.py`, `test_stream_fresh_app.py`, `test_gated_preload.py` (what approved accounts see is in the
  server's HTML and read on hover; its own database, production build and stand-in for Access: runs in CI as it is).
- **Server functions from the Python tests:** `tests/edge_call.py` runs one through `scripts/call.ts` on the test's
  own database, so the pipeline's tests check what the site serves (串流's feed and scores, the item page's AI
  analysis, 搜尋, 報告's files, the conversations, the monitor's snapshot).
- **Parity harness:** `tests/edge_parity.py item|searchtext|monitor` compares the ports with the Python modules the
  cluster keeps using (item.py for the agent's tools and the MCP server, searchtext.py for the index, monitor.py) on a
  copy of real data (`PARITY_DATABASE_URL`).
- `tests/test_search_edge.py` (the published dictionary), `tests/test_today_label.py` (the day helpers in Node).

## 處理中

A field the item page's data leaves out or sends as `null` (`tags`, `thread`, `extract`/`text_chars`) is pending; for
a post first seen less than 30 minutes ago an empty value also counts as pending (no tags yet, no full text, no
comments). The page re-reads the post every minute while anything is pending (`src/lib/pending.ts`).

Sections behind a feature flag are hidden from the navigation. There is no 設定 section: the site is read-only, and
Deyao's control plane is scripts (Deyao, 2026-10-14).
