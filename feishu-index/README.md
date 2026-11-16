# feishu-index

Keeps a searchable index of one Feishu folder: every Doc, Sheet, Base and uploaded file (PDF, Word, Excel,
PowerPoint, text) below it, as text chunks with a keyword index and embeddings, in Postgres. Other YYC systems read it
through the HTTP API, which also writes to the folder as the app (Docs, Sheets, Base, uploads, move, delete). The
index covers that one folder; the API reaches whatever the app reaches in Feishu, which is more than the folder (below;
Deyao, 2026-11-11, 2026-11-12, 2026-11-16).

## How it works

- **Feishu access:** a custom app (自建應用) in YYC's Feishu tenant, as the app itself (tenant_access_token). To Feishu
  the app is a member of the organisation, so it reaches what it owns, what is shared with its bot or with a group the
  bot is in, and every file whose owner set link sharing to the whole organisation (`link_share_entity`
  `tenant_readable` 「組織內獲得鏈接的人可閱讀」 = view, `tenant_editable` 「…可編輯」 = edit; Feishu's 權限常見問題,
  question 4). This is the expected behaviour (Deyao, 2026-11-16): the whole organisation includes the bot. The app
  cannot search or list other people's spaces, so outside the folder it needs the file's or folder's link. Why it
  reaches an item: `GET drive/v1/permissions/{token}/public?type=file|docx|…` (`link_share_entity`; a folder is not
  taken, ask a file inside) and `…/members/auth?type=&action=view|edit|…`. Scopes: Drive, Docs, Sheets, Base (read and
  write).
- **Scan:** every `SCAN_SECONDS` (60) the indexer walks the whole folder tree (`GET /open-apis/drive/v1/files`) and
  re-indexes each item whose `modified_time` or path changed (or `INDEX_VERSION`); items gone from the walk are deleted
  with their chunks. A walk that fails part-way deletes nothing. A file that fails is recorded (`files.status = error`)
  and tried again after an edit or 30 minutes.
- **Text:** Docs `docx/v1 raw_content` (old Docs `doc/v2`); Sheets cell values per worksheet, header row repeated in each
  chunk; Base records as `field: value` lines per table; uploads downloaded and extracted (pdftotext, OOXML, calamine).
  Folders, shortcuts, mind notes, slides and other uploads are listed (`status = skipped`), not read. Shortcuts are not
  followed (their target may be outside the folder).
- **Chunks:** about 1500 characters, never across sections (a sheet, a table, a PDF page).
- **Keyword index:** built in Rust (`src/text.rs`), no Postgres text-search parser: NFKC, Traditional to Simplified,
  lower case; CJK as unigrams and bigrams, other scripts as words; a query term is a phrase. `計劃` finds `计划`.
- **Embeddings:** OpenRouter `/embeddings`, model `EMBED_MODEL` (`qwen/qwen3-embedding-8b`, asked for 3072 dimensions,
  stored as `halfvec(3072)` with an HNSW index). Keyed by the hash of (model, text), so an edit re-embeds only changed
  chunks. A model that can't return 3072 is a migration (`embed::DIM` and the column). Google's and OpenAI's embedding
  models answer YYC's OpenRouter account with 403 "violation of provider Terms Of Service" (2026-11-11).
- **Search:** keyword leg (`ts_rank_cd`) and semantic leg (cosine), 50 candidates each, fused by reciprocal rank.
- **Replicas:** every replica serves the API; one at a time holds a Postgres advisory lock and indexes. A rolling update
  or a lost pod hands the lock over within seconds. A Postgres switchover drops the lock's connection; the indexer takes
  the lock again.

## API (ClusterIP `feishu-index.feishu-index.svc.cluster.local`, port 80)

`/healthz` and `/readyz` are open. Everything under `/v1` needs `Authorization: Bearer <API_TOKEN>`.

| | |
|---|---|
| `GET /v1/search?q=…&k=10&mode=hybrid\|keyword\|semantic` | hits: file, path, Feishu URL, section, chunk text, ranks |
| `GET /v1/files` | every item: kind, path, status, error, chunk count |
| `GET /v1/files/{token}` | one item and its text, chunk by chunk |
| `GET /v1/status` | the shared folder's token, which pod indexes, counts by status, the last 10 scans |
| `POST /v1/docs` `{title, markdown, folder?}` | a new Doc with the markdown as its body (Feishu converts it); `folder` defaults to the shared folder |
| `POST /v1/docs/{id}/append` `{markdown}`, `PUT /v1/docs/{id}` `{markdown}` | add to the end of a Doc; replace its body |
| `POST /v1/upload?name=…&folder=…` (the bytes as the body, at most 20 MB) | a file into a folder |
| `ANY /v1/feishu/open-apis/…` | the request passed on to Feishu as the app, the answer returned as it is: Drive, Docs, Sheets and Base only (`drive`, `docx`, `doc`, `sheets`, `bitable`); sharing, ownership and subscriptions refused (403) |

The passthrough is how a caller creates folders, Sheets and Bases, writes cells and records, downloads, moves and
deletes. It takes any file or folder token, in the shared folder or not: Feishu decides whether the app may (Feishu
access, above). The index follows a write in the folder at the next scan (within `SCAN_SECONDS`). Writes are logged
with the caller's `X-Jarvis-Conversation`. An answer that is not JSON (a download) is passed on piece by piece as
it arrives, never held whole: its size costs no memory, and it has no time limit beyond 120 s of silence.

### Callers

A caller gets a NetworkPolicy by name and the bearer token.

- **YYC AI**: egress's route `feishu-files` (`yyc-ai/deploy/egress/egress.json`; pair token store key
  `YYC_TOKEN_GATEWAY_FEISHU_FILES` = `API_TOKEN` here, `EGRESS_TOKEN_FEISHU_FILES` in egress), NetworkPolicy
  `ingress-yyc-ai-egress`, managed skill `yyc-ai/deploy/hermit/skills/feishu-files/`. Every conversation has the same
  read and write access to the whole folder, deleting and moving included (Deyao, 2026-11-12), and, given its link,
  to any other file the app reaches (a staff member's file shared with the whole organisation: 2026-11-16).

## Configuration (env)

| | |
|---|---|
| `DATABASE_URL` | role `feishu_index`, database `feishu_index` in Cluster `pg` (Secret `env`) |
| `API_TOKEN` | the bearer token of `/v1` (Secret `env`) |
| `OPENROUTER_API_KEY` | key `yyc-feishu-index` on YYC's OpenRouter account, USD 20/month cap (Secret `env`) |
| `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_FOLDER_TOKEN` | the custom app and the shared folder's token (Secret `feishu-app`) |
| `EMBED_MODEL`, `SCAN_SECONDS`, `FEISHU_BASE`, `OPENROUTER_BASE`, `LISTEN`, `POD_NAME` | defaults in `src/main.rs` |

Store keys (`yyc`): `YYC_FEISHU_INDEX_DATABASE_URL`, `YYC_FEISHU_INDEX_OPENROUTER_KEY`, `YYC_PG_FEISHU_INDEX_PASSWORD`,
`YYC_FEISHU_APP_ID`, `YYC_FEISHU_APP_SECRET`, `YYC_FEISHU_FOLDER_TOKEN`, `YYC_TOKEN_GATEWAY_FEISHU_FILES`
(`infra/secrets.txt`).

The app is 「YYC 檔案機械人」 (`cli_aa7f953df4785d24`, version 1.0.0 approved 2026-11-12); the folder is
「YYC 檔案機械人共享資料夾」 in Deyao's My Space, shared with the bot (can edit) and with the YYC organisation (can edit). Scopes beyond the first release:
`docx:document.block:convert` (markdown to blocks; no approval needed).

## Deploy

- Image: `.github/workflows/feishu-index-image.yml` (clippy, unit + end-to-end tests against Postgres 17 + pgvector
  with a mock Feishu and OpenRouter, the amd64 image, the pin in
  `news-stream/deploy/clusters/talos/feishu-index/kustomization.yaml`, then yyc-apps.yml).
- Manifests: `news-stream/deploy/clusters/talos/feishu-index/` (yyc-apps.yml); the namespace and the deployer's rights
  in `../apps-access.yaml`; the database, role and ingress rule in `../pg/` (talos-cluster.yml). The daily dump and the
  weekly restore test include `feishu_index`.

## Tests

```bash
cargo test --lib                                                               # unit
FEISHU_INDEX_TEST_PG=postgres://USER:PW@127.0.0.1:5432/postgres cargo test     # + end to end (needs pgvector, pdftotext)
FEISHU_LIVE_APP_ID=… FEISHU_LIVE_APP_SECRET=… FEISHU_LIVE_FOLDER=… cargo test --test live   # the write API on the real tenant
```

`tests/api.rs` (the bearer token, the passthrough's allowlist, Docs, uploads) needs no database.
