# YYC news stream: architecture

Three parts, each on an account billed to YYC (EXPENSES.md):

- **The site** is the edge app (`web-app/`): TanStack Start on the Cloudflare Worker `yyc-web` (YYC's Cloudflare
  account, Workers Paid), pages rendered on the server, data read from YYC's Postgres through Hyperdrive. Every page and every
  read of the site is its own.
- **The data** is one Postgres database, `app`, on YYC's own Postgres in the cluster (CloudNativePG Cluster `pg`,
  namespace `db`, on its own node; Deyao, 2026-10-27: self-hosted, not Neon).
- **The cluster** (k3s on Hetzner, project `yyc`) fetches, computes and runs the AI server: the collectors, the poller,
  scoring, alerts, reports, and `web.py`'s AI routes. Nothing on the site's read path calls it.

## The site (`web-app/`)

- **Router:** Worker `yyc-edge` (`edge/worker.js`, YYC's Cloudflare account; routes on the SaaS zone yyc-internal.com, `deploy/cloudflare`) on `newswatch.yyc-internal.com/*` and `yyc-beta.deyaochen.com/*`,
  behind Cloudflare Access (the `/app` gate on the main host, the whole beta host). It sends everything to the edge app
  (with the `X-YYC-Edge` secret and the original host) except the cluster's paths, which go through the tunnel:
  POST `/app/ai/*`, `/app/proof/flag` (the proof view's flag, `proof.py`), GET `/api/ai/estimate`, `/app/api/v1/*`,
  `/app/login`, `/healthz` (the AI server) and `/app/wisers/api/` (the WiseSearch relay's JSON API; the page is the edge app's).
- **Edge app:** 串流, the item page, 數據分析 (and its Excel exports), 搜尋, 報告 (and the report files), Deyao's monitor, `/app/me`, the approved accounts' reads (full text,
  scores). Server functions in `web-app/src/server/*` are the TypeScript ports of the Python modules the pages used
  (feed, item, dashboard, search, reports, agent reads, monitor); the Python side keeps what the pipeline and the
  data API's tools use. Read-only role `yyc_edge`. Deploy: `.github/workflows/web-app.yml` on every push
  touching `web-app/` (an atomic Worker deploy). Details: `web-app/README.md`.
- **What the app needs from Python:** the poller publishes kv `edge:config` (`edge_config.py`: streams, thresholds,
  flags per host, labels, the scorer's constants) and the search dictionary (`searchtext.publish_edge`).
- **Live updates:** Postgres triggers (`corpus.SCHEMA`, `live_*`) announce every change a page shows on channel
  `yyc_live` ("feed:<day>", "scores:<day>", "item:<post id>"); a poller thread (`live.py`) LISTENs and posts the topics
  to the app's LiveHub (a Durable Object holding each open page's WebSocket), which tells those pages to read again.
- **REST for YYC AI and scripts:** Deployment `yyc-mcp` (`mcp-edge/`, https://mcp.yyc-internal.com/api/v1), read-only
  over YYC's Postgres (role `yyc_readonly`) with the app's modules; personal tokens (made at /tokens, sign-in through
  Cloudflare Access) and service tokens (YYC AI's tools role), who may use it = YYC's access catalogue
  (`/yyc-ai/README.md` "The staff allowlist"). The AI server's `/app/api/v1` serves the Python tool registry (`tools/`) to approved accounts.

## Data (YYC's Postgres)

PostgreSQL 17 run by CloudNativePG (`deploy/db/postgres.yaml`): Cluster `pg` in namespace `db`, one instance on the
dedicated node `yyc-pool-db-worker1` (cx33, 4 vCPU / 8 GB, tainted for it alone), a 100 Gi Hetzner Volume. The same
instance holds YYC AI's databases (`jarvis`, `cloudcli`, `jfs_yyc_ai`), each open only to its own role.
`DATABASE_URL` from Secret `db-app` (role `app`, the owner; Service `pg-rw.db`, TLS) in every workload: a direct
session, no pooler (advisory locks and LISTEN need one). Role `yyc_edge` (read-only: SELECT on every table and default
privileges for role `app`) for the edge: Hyperdrive config `yyc-pg` (query cache off) reaches `db.yyc-internal.com`,
which tunnel `yyc-stream` sends to `pg-rw.db`, behind an Access app that admits only the service token
`yyc-hyperdrive` (`deploy/cloudflare/tofu/db.tf`). Backups (Deyao, 2026-10-27), two layers: every day at 03:30 HKT a
pg_dump of each database (CronJob `pg-dump-daily`, `deploy/db/pg-dump-daily.yaml`) into the Hetzner bucket
`yyc-pg-backups` (`dumps/<date>/`), S3 Object Lock in compliance mode for 30 days, so no key or login, nor a script that
deletes everything, can remove one before then; and a restore point every few minutes for a crash or a single wrong
command: the WAL archived as it fills and a nightly base backup into bucket `yyc-pg-pitr` (Barman Cloud plugin, not
locked, 3 days kept). One instance: a Postgres restart
(a minor upgrade, the node's reboot) is a gap of seconds to a minute for every client. From a session:
`deploy/cluster/cluster.sh db-forward` puts it on localhost:15432.

Tables (`corpus.SCHEMA`): `posts`, `tags` (the corpus, as in corpus.py), `kv` (small values, and the jobs' documents:
`file:<name>`), `stream_items` (the stream archive), `poll_seen` (URLs already read) and `poll_state` (last poll,
source status), `profiles` and `settings`, `ai_conversations` + `ai_turns` (the agent's history, per signed-in Access
account), `proof_flags` (the proof view's flags, `proof.py`, made on first use), `ai_spend` and `ai_runs` (charged cost per day and per call), `solver_spend` (each paid captcha solve, at the
provider's figure), `archive_scan` (the archive.today lookups of paywalled posts), `report_files` (reports.py's daily
files: day, profile, name, Content-Type, bytes, created).
**Schema changes:** an empty database gets `corpus.SCHEMA` in one transaction; an existing one gets only the numbered
`corpus.MIGRATIONS` after its kv `schema_version`, each on its own (an index CONCURRENTLY outside a transaction; other
DDL in a short transaction that first locks its tables in name order, lock_timeout 2 s, up to 20 jittered retries),
by one pod at a time (advisory lock `yyc schema`). A schema change is a new migration plus the same object in
`corpus.SCHEMA` (`tests/test_schema.py` checks both end the same). The new pod applies them while the old pods still run. Every
connection runs with `prepare_threshold=None` (no server-side prepared statements), so a column added to `posts` never
breaks the old pod's `SELECT p.*` queries with "cached plan must not change result type" (`tests/test_prepared_plans.py`).
**Articles** (Deyao, 2026-10-14): posts that are the same content share `posts.article_id`, the id of the article's
first post (a post on its own: its own id). Same content = the same URL after cleanup (`url_key`: no tracking
parameters, www./m., fragment or trailing /), the same full text (`text_key`, at least 200 letters/digits; not the
same site under different headlines, which is the site's boilerplate), or a social post linking to the article
(`link_key`). Comments and replies are never grouped. `corpus.assign_articles` sets them on ingest and when the full
text arrives (indexed lookups only); `corpus.py articles` recomputes every post. A story (different articles about
one event) is not stored. Jobs that must run one at a time take a Postgres advisory lock (`corpus.lock`).

## Cluster

- One Hetzner **cx33** (4 vCPU, 8 GB, 80 GB, fsn1), IPv6 only with Cloudflare WARP for outgoing IPv4, daily server
  backups on. Firewall: SSH only; traffic comes in through the Cloudflare tunnel.
- **k3s** (single node) reconciled by **Flux** from this repo (`news-stream/deploy/`: GitRepository `yyc` +
  Kustomization `yyc-apps`); secrets as **Sealed Secrets** (the key exists only in the cluster; the values are also in the
  Jarvis 2 store `yyc`, the source for re-sealing; `deploy/README.md` "Secrets"). Git is the source of truth: no hand edits on the server.
- **Image:** GitHub Actions builds `ghcr.io/de0ch/yyc-news-stream:<sha>` on every push touching `news-stream/`
  (Python + Google Chrome/Patchright + Xvfb + fonts-noto-cjk) and pins the tag in `deploy/`; Flux rolls it out.

### Workloads (namespace `yyc`)

| Kind | Name | What |
|---|---|---|
| Deployment (2 replicas) | `web` | the AI server: `gunicorn app:application` (`web.py`: the persona button and its estimate, the data API for AI, `/app/login`); readiness `/healthz`; RollingUpdate maxUnavailable 0 |
| Deployment (1, rolling, gapless) | `poller` | `python app.py`: sources every 5 min, corpus, scoring (streams.py), Telegram, `edge_config` and `live` threads. One works at a time under a Postgres session lock; a deploy starts the new pod first, it waits on the lock while the old one (SIGTERM) finishes its poll and after_poll, then takes over at once; the old pod keeps the lock (its heartbeat too) until its drain is done (`take_poller_lock`/`keep_lock`/`drain`, grace 1500 s) |
| Deployment | `monitor` | `gunicorn monitor:application`: computes the panels of Deyao's monitor and publishes each snapshot into kv `monitor:data`, which the edge app's `/app/monitor` reads; the provider keys (Secret monitor-keys), a read-only ServiceAccount; nothing reaches it over the network |
| Deployments | `images`, `transcribe`, `whatsapp`, `yyc-mcp`, `wisers`, `flagd` | picture scoring, video/audio transcripts, WhatsApp alerts, the MCP server, the WiseSearch relay, feature flags |
| Deployment | `cloudflared` | the `yyc-stream` tunnel → Service `web` (and `wisers` by path; `yyc-mcp` at mcp.yyc-internal.com) |
| CronJobs | `browser`, `facebook`, `instagram`, `comments`, `replies`, `lihkg`, `tikhub`, `watch`, `cache`, `fulltext`, `paywall`, `reports`, `uptime`, `backfill`, `broadcast` | the collectors and jobs, `concurrencyPolicy: Forbid`, `activeDeadlineSeconds` (deploy/README.md) |

State lives in the application, not on volumes (Deyao, 2026-11-08), so no job is tied to a node: the browser jobs'
Chrome cookies (chrome_state.py, kv `chrome:<profile>`), the uptime probe's failures (uptime_log.py) and everything
else are in Postgres, the built report files too, and the WiseSearch relay's whole state (wisers/store.py).

## Access

Reading is public. Accounts Deyao approves in the Access app on `newswatch.yyc-internal.com/app` use every dashboard function
(AI, the full text); both the edge app and the AI server check Access's signed JWT again. The site is read-only: no
admin page, no edits; Deyao's control plane is scripts (`infra/control.py`, `infra/thresholds.py`, `reports.py`;
Deyao, 2026-10-14). Deyao's own tools are never on the site.

## Feature flags (Deyao, 2026-10-13)

OpenFeature in Python (`flags.py`), flagd in the cluster (`deploy/apps/flagd.yaml`) serving
`deploy/apps/flags/flags.json`. A flag change is a git change: Flux updates the ConfigMap and flagd streams it to the
cluster's processes; the poller republishes the flags per host into kv `edge:config` for the edge app. An off flag
hides its section from the navigation and its routes answer 404 (the edge app's `server/gates.ts`, the AI server's
`web.GATES`); any flag that can't be evaluated is off. A new flag: an entry in `flags.json` and a line in `flags.FLAGS`.
Pipeline flags switch work, not sections: `pictures` (picture analysis) and `transcription` (the speech model),
both OFF since 2026-10-20 (Deyao: cost). While `pictures` is off the
picture worker scores nothing (and Deployment `images` is at 0 replicas) and the stream scorer is text-only; while
`transcription` is off no audio is fetched and nothing goes to the model (CronJob `broadcast` suspended), the
transcriber reads only YouTube clips' own subtitles; queued pictures and clips stay queued, thin.py waits for neither,
the item page leaves the waiting pictures and clips out (NAVIGATION.md "Turned-off features: absent, never announced")
and the monitor says "off: turned off by Deyao (cost)" (`flags.OFF_WHY`).
Turning one back on: the flag on in `flags.json`, and `images` replicas 1 / `broadcast` suspend false.
**Text only (Deyao, 2026-10-20: "images and audio shouldn't be downloaded either", IPRoyal bandwidth):** no other job
downloads a picture, a clip, audio or a font, on any route (yycstream, the ISP IP, the server's IP); a picture or clip
is stored as its URL only. `nomedia.py`: every browser context a job opens (fb_fetch, comments_fetch, browser_fetch,
cache_fetch / fulltext browser pass, paywall, wisers) aborts image / media / font requests, media files
by extension, the media CDNs (googlevideo, ytimg, Facebook's and Instagram's scontent / video hosts, WhatsApp's) and
video player frames (Cloudflare's challenge never; Wisers' login page keeps its captcha); `sources.fetch` never reads
a body whose Content-Type is media and `cache_fetch.get` never requests a media-file URL (HTTP 415).
`tests/test_nomedia.py` (a real Chromium: no media request reaches the server; every context in the code is blocked).
