# Building watchlist.json (2026-10-14)

How the first list was found; re-run to extend it. Work files go to `$WATCHLIST_BUILD` (default
`~/artifacts/watchlist/build`). TikHub calls use `TIKHUB_API` = YYC's key (cluster Secret `app-env`), never a personal one.

1. `entities.py` — the people and organisations to look up (officials, ExCo, parties, departments, the legislators
   already followed, every outlet the stream reads with its website, companies). Writes `entities.json`.
2. `discover.py` — per entity: YouTube channel search (free), the outlet's own website's social links (free),
   Instagram user search (TikHub, USD 0.002). Resumable (`cache/`). Writes `found.json`. 2026-10-14: USD 0.518.
3. `curate.py` — the accept lists Claude reviewed by hand (name match, size, official badge) → `rows.json`.
4. `samehandle.py` — each Instagram handle checked on Threads, TikTok and X (TikHub, USD 0.004 a handle); kept when
   the profile name matches the owner. 2026-10-14: USD 0.293.
5. `merge.py rows.json samehandle.json …` — merges into `../../watchlist.json` (dedupe by `watchlist.key`, existing
   rows win) and checks `watchlist.problems`.
The KOL group came from a research pass (KOLScanner's HK channel dataset, YouTube About links, search) saved as
`research/kol.json` in that session's artifacts and merged the same way.
