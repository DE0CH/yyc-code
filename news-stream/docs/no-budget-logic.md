# Budget-shaped decision logic in YYC news-stream: inventory (2026-10-14)

Principle (Deyao, 2026-10-14): a budget is a safety stop, never what the app plans by. Spending limits act only as
hard stops when actually hit (OpenRouter refusing for the key limit or credit, a provider refusing for no balance);
the USD 70/month residential-proxy rule (YYC CLAUDE.md) is an alert line on the monitor. Monitoring (spend and balance
displays, cost reports, per-call cost ledgers) stays.

Line numbers are from origin/main at e93c534 (before the change; later commits moved some lines).

## Removed (money decided what or how much to do)

| file:line | what it did | change |
|---|---|---|
| streams.py:48 `RESERVE_MARGIN`, :50 `BACKFILL_WAIT`, :384 `live_rate()`, :413 `budget()`, :456-460 gate in `backfill_once` | History-scoring backfill only ran while the key's remaining balance minus a reserve (live AI USD/h x hours to reset x 1.2) paid a batch; else state `budget`, recheck every 10 min | Removed. The backfill runs whenever live scoring has nothing due; it stops only when OpenRouter refuses (state `limit`). |
| streams.py:23-26, :31 docstring + `python3 streams.py budget` CLI | describes / prints the reserve | Removed. |
| infra/thresholds.py:16-18, :82-89 | prints the reserve, headroom and "the cap needs USD x more" | Removed; keeps posts scored, spend, per-post cost, projected rest, the key's own balance (monitoring). |
| monitor.html:283-285 | "waiting for budget" pill, "Cap budget ... kept for live AI (headroom)" | Removed; shows "OpenRouter limit reached" when the hard stop is hit. |
| tools/budget.py (whole), tools/__init__.py:93,102,122,133-139,158 | per-account USD 5 paid-tool budget: a paid tool call was refused once the account had spent its limit until Deyao reset it | Removed (user AI is behind the `ai` flag anyway). The per-call ledger `tool_spend` (cost per account) stays as tools/spend.py; table `tool_budget` dropped. |
| infra/control.py:2,17-18,89-94 `budgets`, `budget-reset` | read/reset per-account budgets | Replaced by `tool-spend` (each account's paid-tool spend: monitoring). |
| web.py:521 `/api/ai/budget` | named a budget, returns today's spend | Renamed `/api/ai/spend` (monitoring). |
| README.md:288-289 | "share a daily AI budget (default USD 0.50)" (no code left behind it) | Removed. |
| fulltext_fetch.py:39 `PROXY_PAGES_DAY = 150`, :40 `PROXY_DAYS = 2`, :118 | 信報 / 香港討論區 article pages through the paid residential proxy: at most 150 a day and only from the last 2 days (161 skipped in the 06:22 HKT run on 14 Oct; 438 pages waiting) | Removed. Every proxied page is read like any other host (one request at a time per host, HOST_GAP, RATE stay). The per-day proxied page count stays as a monitor figure. |
| replies_fetch.py:57-58 `DISCUSS_MB_MONTH`/`LIHKG_MB_MONTH`, :79 `OutOfBudget`, :137 `allowance()` | proxy bytes paced to 450 MB/month (discuss) / 60 MB (LIHKG while blocked): requests stopped while bytes used exceeded what had accrued | Removed. Bytes are still counted per day/month (monitor). |
| replies_fetch.py:62 `DISCUSS_RUN_EVERY = 3600` "(proxy cost)" | 香港討論區 replies only every other run | Removed: every run (30 min). |
| replies_fetch.py:60 `DISCUSS_EVERY` (quiet sub-boards every 2 h, "each listing costs ~40 KB of proxy traffic") | | Removed: every board every run. |
| replies_fetch.py:448 `DISCUSS_TAIL = 150` "(proxy cost)" | a long thread seen for the first time: posts 1-100 and the last 150 only, the middle never read | Removed: every post is read (over several runs for a long thread). |
| replies_fetch.py:65 `DISCUSS_MAX_PAGES = 400` | requests a run, sized to the proxy pacing | Removed: RUN_SECONDS (the run must end before the next timer run) bounds a run. |
| broadcast_fetch.py:41,230 `YT_MB_MONTH = 1000` | YouTube audio stopped at 1000 MB of proxy traffic a month (job suspended) | Removed; bytes still counted. |
| streams.py `classify_market` (Galbot split, 4f7dcc7) | any failed model call, including OpenRouter refusing for the limit, fell back to the source's region | LimitReached is re-raised: no silent fallback during a hard stop |

## Kept: site protection (a cap that keeps a site from being hammered or from banning us)

| file:line | what | why kept |
|---|---|---|
| fulltext_fetch.py:35-37 `RATE`, `HOST_GAP`, `THREADS` | 8 req/s overall, 1 s per host, 16 hosts at once | politeness |
| cache_fetch.py:28 `RATE` | 4 req/s | politeness |
| replies_fetch.py:64 `LIHKG_MAX_PAGES`, 1 req/s, Blocked → stop at once | LIHKG bans the server's IP (Cloudflare 1015) after bursts | site protection, documented ban |
| tikhub_fetch.py:55 `MIN_GAP` | TikHub returns 429 when requests fire together | provider rate limit |
| telegram_alerts.py:22 `GAP` | Telegram's ~20 msgs/min per channel | provider rate limit |
| sources_extra.py:652 sitemap cadence | "cost the publishers ~0.3-1 MB a read" | publisher's bandwidth, not ours |
| fb_fetch.py:45 `MAX_REQ`, "Rate limit exceeded" retry | Facebook rate limits | site protection |

## Removed later (coverage audit, 2026-10-14, Deyao lifted the cost limits)

| what | change |
|---|---|
| comments_fetch.py Facebook gaps doubling after empty checks | Removed: every post on the DUE schedule. |
| comments_fetch.py Instagram/Threads comments read once at 6 h (`IG_AGE_H`) | DUE schedule from 1 h, like Facebook; every comment and reply through TikHub when the page shows fewer than the post counts. |
| ig_fetch.py `every_hours` per account (3 h for 64 accounts) | Removed: busy accounts every 15 min, the rest hourly; a gap of more than 12 posts is paged through TikHub. |
| fb_fetch.py `EVERY_MIN` (quiet pages every 60/120 min) | Removed: every page every run (every 15 min). |

## Kept: run-time bounds (a job must finish inside its schedule/deadline; not money)

fulltext_fetch.py `MAX_PAGES`, `DEADLINE`, `RUN_BUDGET`, `BROWSER_PAGES`, `BROWSER_BATCH` (the
time-bounded passes of 6e2a97a: every run ends inside the CronJob's activeDeadlineSeconds); cache_fetch.py:27 `MAX_PAGES`; replies_fetch.py:63 `RUN_SECONDS`;
comments_fetch.py:56 `BUDGET_S` (seconds per platform per run; name aside, a time bound) and :53 `PAGES_MAX`;
fb_fetch.py:49 `RUN_SECONDS`; broadcast_fetch.py:40 `MAX_MIN` (CPU minutes of local speech recognition per run);
streams.py `BATCH`, `TEXT_CHARS` (model context size).

## Kept: monitoring / hard stops

- monitor.py balances and spending series (OpenRouter, TikHub+AnyAPI, IPRoyal), with no cap
  lines, run-out dates or headroom bars (removed 2026-10-15: the IPRoyal USD 70 line went with its rule; projections are
  not held to the key's cap); infra/cost-report.py; ai.py `ai_spend` / `ai_runs` and the
  pre-click cost estimate shown on AI buttons (information to the user, not a gate); `thresholds.py cost`.
- tools/stream_history.py:18,123 `--max-usd` (optional, set by the operator per run; stops on actual spend, no
  projection). Kept: a hard stop the operator chooses; the WFC run of another session uses this tool now.
- Each paid tool's description states its price (tools/__init__.py `describe`): the caller decides, the app does not; kept.
  (The "ask before using it" line went with mcp_server.py, 2026-10-29, and agent.py, 2026-10-28.)
- NEW: OpenRouter refusing for the key limit or credit (HTTP 402, or 403 naming a limit/credit) → `ai.LimitReached`;
  kv `openrouter_limit` records it, the monitor shows it, Deyao gets one Discord message per episode; live scoring
  and the backfill pause and re-try every 10 minutes; the first success clears it.

## Not changed here: cadences chosen for cost whose removal is a large new spend (Deyao decides, exact cost given)

| file:line | what | cost of removing |
|---|---|---|
| tikhub_fetch.py:552 `EVERY = 24 h` (Deyao, 2026-10-13, "to save cost") | each (platform, query) searched once a day | 307 checks a day now; hourly = 24x: list price USD 1.135/day today → USD 27.24/day (≈ USD 845 a month). |
| app.py:41 `DISCUSS_SECONDS = 1800` (香港討論區 search, "paid per GB"); sources.py:521-524 `hkej_instant` every 600 s ("proxied ones cost per GB") | | same |
These are fixed cadences (no budget computed in the app), and the proxy spend is already above the line that YYC's
CLAUDE.md says needs Deyao's OK first; so they wait for his decision.
