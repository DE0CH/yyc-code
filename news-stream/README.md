# YYC news stream

YYC's own news monitoring, built to match K-Matrix's "CI analytic Fixed Profile Package" (checklist: `PARITY.md`):
the realtime streams YYC gets from K-Matrix on WhatsApp (groups "K-Matrix & YYC CEHK / HKIC / FSHK"), the daily
reports and the self-served dashboard. Public pages: **https://newswatch.yyc-internal.com/** (stream),
`/dashboard`, `/search`, `/reports/`, `/item/<id>`. The site is the edge app (`web-app/`: TanStack Start on
the Cloudflare Worker `yyc-web`, reading YYC's Postgres through Hyperdrive); the cluster fetches, computes and runs the AI server
(`web.py`: the persona's AI button, its cost estimate, the data API for AI). ARCHITECTURE.md has the
whole picture. Accounts Deyao approves (Cloudflare Access on `/app*` + each side's own JWT check): AI and the full
text. **The site is read-only (Deyao, 2026-10-14: "no admin page, control plane is script from me manually"):** no page
edits anything; profiles, recipients, settings and paid-tool spend are `infra/control.py`, stream
thresholds `infra/thresholds.py`, reports and themes `reports.py`; `tests/test_readonly.py` checks that the AI server
serves only its AI routes and that no route but the persona note, the proof view's flag and the AI data API takes a POST. Deyao's own tools are never on the site.
Every page shares one app shell (`web-app/src/shell/`: header, profile picker, phone tab bar); the sections, sub-pages,
URLs and back behaviour are in NAVIGATION.md.

- **Streams** (`streams.json`): CEHK (the Chief Executive and his office), HKIC (港投公司 and its portfolio), FSHK
  (Financial Secretary), SJHK (Secretary for Justice Paul Lam and his Department of Justice; Deyao, 2026-11-05), POL 政治花絮 (insider politics and political gossip; the 18 political columns, `col_*` sources in
  sources_extra.py, are watched, but a post is judged by what it says, never by its column: Deyao, 2026-10-23), ECE 選委選舉, GALBOT 銀河通用 (one stream: Galbot 香港 / 內地 folded back in 2026-11-11, below), WFC 宏福苑 (the 大埔 宏福苑 fire of 26 Nov 2025 and everything after it, no end date; YYC's boss, 2026-10-14). Each has a `brief`, a short definition of its subject (what the scorer reads; no rules, no in/out lists: Deyao, 2026-10-23), optional
  `categories` (CEHK), and its **search terms**: `news_queries` (Google News, and the social platforms unless
  `social_queries` is set), `news_queries_en` (Google News en-HK), `discuss_queries` (香港討論區) and
  `social_queries`. Search terms are only what is sent to
  platforms that can only be searched; they never decide what belongs to a stream (Deyao, 2026-10-14: no keyword
  matching anywhere, "that's probably just fancy regex"). The AI score does.
- **Scored streams (Deyao, 2026-10-14; `streams.py`, a poller thread):** no keyword gate. Every post ingested after the
  switch is scored 0–100 against every stream by the stage-1 model on its full text (pairs at 20 or more again by the
  stage-2 model: "Two-stage scoring" below), against one rubric (90 = the stream's
  subject is the main subject, 60–89 it acts or is quoted, 20–59 a passing mention or a neighbouring subject, 0–19
  unrelated), with a one-line reason and, for CEHK, a category. A post whose title or text changes (its article text
  arrives later) is scored again. At or above the stream's threshold (default 50) it becomes a stream item, a member of
  the stream's profile on 數據分析 and a Telegram post. Thresholds, comment scoring (off until switched on),
  the score spread, previews and the scoring cost: `infra/thresholds.py` (Deyao's control; no admin page). Scores and
  reasons are shown only to approved accounts (`/app/stream/scores`; on the item page, every stream's score, reason
  and threshold: `/app/item/ai`); 串流's public feed leaves them out. Google News
  results whose outlet no watched source covers go into kv `source_gaps` (`thresholds.py gaps`) for Deyao to decide.
- **The live scorer is continuous (`streams.loop`; Deyao, 2026-10-23: "the pipeline time could be shortened"):**
  `SCORE_THREADS` (10) model-call slots; a free slot takes the next due batch (`streams.plan`: live posts `BATCH` a
  call and thread units alternating, newest first, none already in a call) within a second, and each batch is filed
  (`streams.file_job`: stream items, Telegram, WhatsApp, alerts; one filing thread) the moment its own calls are done.
  No rounds: a post never waits for another batch's model call. Batching stays for the model calls only (a shared
  prompt: cheaper per post, same verdicts). `streams.run_once` (CLI, tests) scores what is due once, filing each batch
  as it completes.
- **Two-stage scoring (Deyao, 2026-10-20: a cheap screen, then a second model rates again what passes; "this saves
  cost and reduces false positives"; `streams.confirm`):**
  **Models (Deyao, 2026-10-28):** stage 1 = Qwen3.7 Flash (`ai.MODEL`, no thinking, `streams.SCORE_OPTIONS`; its answer
  template names every topic, `streams.answer_example`), stage 2 = DeepSeek V4.1 Flash (`streams.DEEPSEEK_FLASH`,
  thinking low, never a mainland provider's endpoint). Open-weight models only: OpenRouter answers 403 "violation of
  provider Terms Of Service" to every Anthropic and Google model on YYC's account, and restricts the account from
  closed-weight models (Anthropic, Google, OpenAI). `tools/stage1_trial.py` measured the stage-1 candidates on 591
  posts of 27 Oct (all 191 filed): share of filed pairs screened in / USD per call: DeepSeek V4.1 Flash 99.1% /
  0.00099, MiMo v2.6 Flash 99.1% / 0.00109, Qwen3.7 Flash 93.2% / 0.00078 (90.2% / 0.00053 with the one-entry
  template). The daily report's summary and themes: DeepSeek V4.1 Flash, thinking high (`ai.REPORT_MODEL`; Deyao,
  2026-10-28; live: USD 0.0025 and 22 s for one profile's day).
  Stage 1 scores every post against every stream. Every (post, stream) it scores at or above `streams.SCREEN` (20
  since 2026-10-23, Deyao: "make the stage 2 score all above 20"; 40 before; every stream's threshold is at least
  that) is scored again by the stage-2 model (`streams.CONFIRM_MODEL`, env `CONFIRM_MODEL`; also selectable: DeepSeek
  V4 Pro `deepseek/deepseek-v4-pro-0813`, the cheapest non-fp4 endpoint, DeepSeek's own barred by the account's
  no-training policy, StreamLake and Alibaba ignored for their mainland content filters, thinking high, max_tokens
  32,000, 600 s; each one's options in `streams.CONFIRM_OPTIONS`) with the same prompt: brief, rubric, full text, comments for
  units, no pictures while they are off. **Stage 2 judges blind (Deyao, 2026-11-03):** its prompt is stage 1's
  exactly, plus its own title task (`streams.TITLE`); it scores every topic of every post in its batch, and the code
  keeps the checked pairs. Nothing in a prompt says which stage asks, why a post is there or what the other model
  answered: routing is the code's. Until 3 Nov a "Check: post 0: CEHK" list named stage 1's suspects, and on a bare
  headline that hint was the score (「啟程伊斯坦堡」, a Liverpool post: stage 2 at 55-85 in 4-6 of 10 replays with the
  list, 0-10 in 10 of 10 without). Measured on 125 real posts of 2-3 Nov (277 checked pairs, two runs each way): passes
  76/81 with the list, 72/76 blind, the same spread as one way run twice (7 and 6 pairs differ); the one pair changed
  in both runs: CEHK 55 → 30 on a surveyors' institute comment on the building-repair authority. Cost per call +15%
  (every topic answered). Stage 1 answers every stream of every post, 0–19 too (Deyao, 2026-10-21:
  "my proof page should show me the exact score even when below 20"; `streams.SCORE_TOKENS` 8,000), and every score
  is stored with its reason (`scored.all_scores`; kv `scores_all_from` = the first such scoring; posts scored before
  have no row under 20). Nothing under the floor changes what a stream does (the floor 20 = SCREEN 20 <= every
  threshold); the staff pages show a stage-1 score under 20 as 「低於 20 分」 as before (`item.hiddenLow`), and only the
  proof view (`/app/proof`) shows every exact score, stage 1 and stage 2. Stage 2's score, reason and category are the ones stored (`scores.score`, `scores.model`) and compared with the
  threshold, and its sentiment and labels are the post's; stage 1's score and reason stay beside it (`scores.screen`,
  `scores.screen_reason`; in `score_passes` and on the stream item too). Under SCREEN, stage 1's score stands (`scores.model`
  = the stage-1 model). Filing, the Telegram and WhatsApp sends and the alerts come only after stage 2. The same two stages run for
  units (both passes of the two-pass rule), the history backfill and `tools/stream_history.py`. A post stage 2 gives no
  answer for (non-JSON twice) is not stored or filed and is scored again on the next round. Stage-2 calls hold up to
  `CONFIRM_BATCH` (10) posts, only those with a pair to check; ai_spend purpose = stage 1's + `_verify`
  (`scoring_verify`, `scoring_units_verify`, `scoring_lihkg_verify`, `scoring_discuss_verify`,
  `scoring_backfill_verify`). `scored.defs` does not include the stage-2 model, so switching it on made no history
  due again: posts scored before 2026-10-20 keep their one-stage scores, and filed items stay filed (nothing is recalled).
  The item page's AI 分析 shows each stream's model and the screening score (NAVIGATION.md).
  **Tags and titles from the same call (Deyao, 2026-10-20: the tagging is done by the stage-2 model for the ones
  that pass the relevance threshold; the AI title too):** a post that reaches stage 2 keeps stage 2's sentiment and
  labels, never stage 1's (an invalid sentiment is stored as none), and `scored.tag_model` / the stream item's `tag_model` name
  the model they are from. A stream the post is already filed in is checked in stage 2 whatever stage 1 says, so a
  filed post rescored on new content keeps stage 2's tags (stage 2 silent on it: stage 1's entry stands, as before). The same
  call judges each post's title with one general instruction (`streams.TITLE`; Deyao, 2026-10-23: "design the prompt
  so that it achieves the effect"; no case rules, examples or per-language lengths): does the title say what the
  post is about, as a headline? `title_ok`, else `title_kind` + `title`: the post's own opening headline copied
  exactly when it has one, else a short factual headline in the post's language (Traditional Chinese with HK usage
  for a Chinese post). The post is shown as it is (`streams.post_line`): its own title (`titles.own`), or
  `streams.NO_TITLE` 「（無標題）」 for a post with no title of its own (its stored title is only an excerpt, a
  placeholder, a link waiting for its headline, or a title with no word in it such as a sitemap's thread id), and its
  text whenever the text says more than the title. `streams.confirm_title` stores stage 2's title with `titles.store`
  (model = the stage-2 model): `extracted` (checked to be the post's opening), `retitled` (the post has a title of its own,
  `titles.own`, kept as 來源標題) or `written` (it has none); title_ok = no AI title, checked on this content, for a
  post with a title of its own only. A title_ok for a post shown with none counts for nothing (29 Oct 2026: shown with
  no title at all, the model called the text's opening fine for 75 % of such posts, and 369 posts filed in 72 h showed
  their cut excerpt). Typography is code's (`titles.clean`: straight double
  quotes as 「」 in a Chinese title, “” in an English one; `titles.latin_quotes`).
  **Posts with no title of their own (Deyao, 2026-10-20: "Facebook posts have no title"):** Facebook, Instagram,
  Threads, X, Telegram channels and TikTok captions (`corpus.from_body`: titles.TEXT_SIDS / tg_* /
  titles.TEXT_PLATFORMS, not a comment, not an X Article) are stored at ingest with their full text as the text (a
  collected title that isn't the text's opening is kept in it), the title a short excerpt of the opening
  (`corpus.excerpt`: the first line or sentence, cut at 40 Han / 90 other characters with …; the post's own words, no
  badge) and `posts.title_from_body`. The scorer reads such a post with no title and its whole text, so stage 2's
  title judgement gives it one, stored as `written` (or `extracted`); a post that doesn't reach stage 2 shows the
  excerpt (no model call). One stage 2 reached but left untitled (no judgement, a title `titles.clean` refused, a
  title_ok) is titled by `titles.ensure` right after the stage-2 call, before filing, with `titles.PROMPT`; so is
  such a post in 串流. The titles thread takes these posts only once they are scored on their current content (it
  checked them before stage 2 had run and wiped stage 2's title: 7761895, 29 Oct 2026), and its "checked, none" is
  never written over a row another writer stored for since its read (`titles.store`). `python3 titles.py recheck
  [--dry]` titles the ones checked untitled before this (the last 7 days and 串流; a manual run). `tools/fix_body_titles.py` corrected the stored posts from
  `corpus.FLOOR` on (scored/ai_title hashes moved with them, so nothing was rescored). A post with a title of its own
  and no usable judgement, and every such post that doesn't reach stage 2, is titled by `titles.ensure` as before.
  A news, forum or blog post whose collected title has no word in it (香港高登's sitemap rows: the slug is the thread
  number, 「8099523」) is a post with no title of its own too (`titles.needs`, `titles.PLACEHOLDER`), and the full-text
  read replaces such a title with the page's own headline (`fulltext_fetch.save`). Tests: `tests/test_stage2_tags_titles.py`.
  **Evidence for two stages:** a 2026-10-20 trial on the production database (USD 4.94, the models of then; the tool
  and its figures are in git history before the switch to open-weight models): the second model rejected 15.3 % of
  the pairs the screen had filed, mostly passing mentions and namesakes, and batching (10 posts a call) gave the
  same verdicts as one post a call. The real cost: ai_spend `scoring_verify` and the other `_verify` purposes on the monitor.
- **One stream's full history on Deyao's word (`tools/stream_history.py STREAM estimate|run|status`; first 宏福苑 WFC,
  2026-10-14):** scores every non-comment post of every date against that stream only (ai_spend `scoring_history`,
  8 calls at a time, resumable through kv `stream_history:<STREAM>`), files those at or above its threshold into 串流
  under their own publication day (oldest first, one item per headline) and sets `telegram_sent[STREAM]` before the
  stream's first item, so its channel gets every item oldest first at the sender's pace. Other streams are untouched.
- **One Galbot stream (Deyao, 2026-11-11: "there's only one galbot channel"):** GALBOT is an ordinary stream with
  the Telegram channel and WhatsApp group Galbot 香港 had. The market split of 2026-10-14 (Galbot 香港 / Galbot 內地 by
  an AI market classifier, Galbot 內地 behind the flag `mainland`) is gone: `streams.fold_galbot_split()` (run once by
  the poller after it takes its lock, kv `migrated:galbot_one_stream`) moved GALBOT_HK's and GALBOT_CN's stream items,
  hand-corrected tags and report files to GALBOT, dropped their score rows (copies of GALBOT's) and set GALBOT's
  Telegram and WhatsApp cursors to the furthest of the three. Tests: `tests/test_galbot_fold.py`.
- **History scored too (Deyao, 2026-10-14: complete data first; `streams.py` backfill_*, a second poller thread):**
  every non-comment post live scoring never takes (ingested before the switch, or published more than 2 days before
  it came in, e.g. `backfill.py`'s archive rows) is scored with the same model, rubric and batch size, newest first,
  into `scores`, `scored` and the stream profiles' tags, so 數據分析 has full history. It never files a stream item, so
  串流 and Telegram never get a historical post, and it sends no alert. Resumable and idempotent: a post is skipped
  once `scored` holds it with the current content hash and stream definitions (`scored.defs` = streams' ids, names,
  briefs, categories + rubric + model; editing a brief makes the history due again). It scores whenever live
  scoring has nothing due (live has its own thread); no reserve or spend projection holds it back. Spend: ai_spend
  purpose `scoring_backfill`. Status, spend, the projected rest and the key's balance: `thresholds.py backfill`;
  switch: `thresholds.py backfill on|off`.
- **Budgets are safety stops, not plans (Deyao, 2026-10-14):** nothing decides what or how much to do from spend or
  projections (no reserves, no money caps or sampling; caps that keep a site from banning us stay). The hard stop is
  the provider's own refusal: when OpenRouter refuses the key for its limit or credit (HTTP 402, or a 403 naming the
  limit), `ai.LimitReached` is raised, kv `openrouter_limit` records it, the monitor shows a red line, and Deyao gets
  one Discord message per episode (`notify.py`, webhook `DISCORD_WEBHOOK` into `yyc-alerts`). Live scoring, the market
  classifier and the history scoring pause and try again every 10 minutes; the first call that works clears it.
  Every stop record (kv `openrouter_limit`, `social_limit`, `solver_limit`, `proxy_out`) also clears itself once the
  provider answers again (Deyao, 2026-10-20: a 2Captcha stop outlived the top-up because no solve was attempted): while
  one is recorded, the monitor asks that provider's own read-only API at most every 5 min (OpenRouter: the key's limit
  left and the account's credit; TikHub, AnyAPI, CapSolver, 2Captcha: the balance; IPRoyal: yycstream's traffic left)
  and clears the record on a yes (`monitor.clear_answered`; no key in the monitor's env = no probe).
  Nothing falls back to a cheaper path. The list of what was removed and what was kept as site protection, with
  reasons: `docs/no-budget-logic.md`.
- **開啟原文 opens the article's own address (Deyao, 2026-10-20; `canonical.py`):** every page the full-text readers
  fetch gives the address it states (<link rel=canonical>, og:url; same site, not the front page), kept in
  `posts.canonical_url` when it differs from `url` (the post's identity, never changed); the item page, 搜尋 and 數據分析
  link `link` = canonical_url, else url. Cause: Google News linked 信報 articles under a stale section
  (/instantnews/current/… for a hongkong article), which a phone is redirected to m.hkej.com as a 404. Hosts no browser
  can open get the working host at ingest (`canonical.REHOST`: hkcd.com.hk's certificate is video.hkcd.com's).
  Checks: `canonical.py check [PER] [DAYS]` (a phone GET per sampled post and source), `canonical.py fix HOST [DAYS]
  [SID]`, `canonical.py rehost`.
- **Complete data before AI (Deyao, 2026-10-14; `fulltext_now.py`, a poller thread pool):** each new News/Official/Blog
  post whose feed text is short has its page read right after ingest (fulltext_fetch.py's extractors and per-host
  limits; the proxy, browser and render hosts stay with the 30-minute `fulltext` job). **No alert on a headline
  (Deyao, 2026-10-15):** the scorer waits for the page (`cached_at`), whoever reads it, up to `streams.WAIT_TEXT`
  (45 min), and only then scores on the feed text; a page tried with nothing (cached_at set, no text) is scored at
  once, and so is a post on a host we never read (`cache_fetch.SKIP_HOSTS`: the social platforms, LinkedIn, Google
  News links; Reuters, Bloomberg and the paywalled outlets are read, `paywall.py`). So a news post is scored, filed and sent once, on its full text. A post
  the model would see as a headline alone (no text beyond its title, no transcript or picture text) gets its page's
  address in its line (`streams.post_line` 網址: host + decoded path), so the outlet's section (sports, …) is part of
  what the model reads. The bound comes from the page reads of
  14-15 Oct (seen → cached_at, short-feed news posts): the 30-minute job's hosts (信報, 香港討論區, the browser hosts)
  123 of 125 within 30 min, all 125 within 48 min, median 20.9 min; the on-sight hosts' reads that land at all land
  within 30 min, and the rest were retries hours later. Cost in time (the same days' filed items, replayed under the
  rule): 9 of 206 items were short-feed news posts that would wait; 5 of them would have been filed later, median
  12.9 min, 95th percentile 43.6 min; over all 206 items, median 0 min, 95th percentile 0 min. `corpus.ingest`
  computes search columns (jieba) only for the posts it inserts: tokenising every re-fetched known item (thousands
  a poll once the month sitemaps came in) held each poll's new posts uncommitted, and their pages unread, for 12+
  minutes (15 Oct). It depends on fulltext_fetch's save / page_title / SLUG_SIDS / TRANSIENT / HOST_GAP /
  SOURCE_TYPES and cache_fetch's fetch_one / get / article_text / host / Challenge / *_HOSTS: keep those names.
- **No old or undated post as today's news (2026-10-14, after POL sent two am730·Ray Online columns of 5 and 6 Oct
  as new):** a post whose listing gives no publish time gets the one its page states when the page is read
  (`cache_fetch.page_date`: JSON-LD datePublished, article:published_time and the other publish metas, a
  `<time class=published>`, China Daily's "Published: …"; HKET's JavaScript-only invest/ps sites through HKET's
  content API, `cache_fetch.HKET_API`), else a dated post's of the same article; `fulltext_now` reads an undated
  post's page even when its feed carried the text. The scorer holds an undated post until its page has been tried
  (`streams.DATE_WAIT`, 75 min at most); a page date older than 2 days keeps it to 數據分析 only; with no date
  anywhere it is filed on its seen time. A publish time more than 12 hours after the post was seen is not kept
  (熱血時報's MM-DD-YYYY was read as DD-MM). **A source's first run** (its first posts in the corpus: a new or reset
  sid, `posts.first_run`) is a backlog: no stage works on it unless a manual run lets it in ("Backlogs"), and it is
  never filed or sent; the explicit ways to post history are
  `streams.file(…, backpost=True)` and `tools/stream_history.py STREAM` (Deyao's word per stream). Existing undated posts: `python3 fulltext_fetch.py dates` (dates only; nothing is
  re-scored, re-filed or re-sent).
- **Filed means member; nothing is recalled (Deyao, 2026-10-15: "there shouldn't be recalling of messages"):** a post
  is a member of a stream when it scores at or above the stream's threshold OR it was filed in the stream
  (stream_items). Once filed, an item stays in 串流, its Telegram message stays as sent (never deleted or edited:
  Telegram sends every filed item, the channel is 串流's copy), 數據分析 counts it (`insights.members` =
  `corpus.member_ids`; `streams.sync_tags` and a rescore keep its tag), and the item page lists it in the stream; for
  approved accounts the AI card shows its current score beside the score it was filed on ("已收錄", 收錄時 90 分，
  重新評分後 低於 20 分). A lower score only keeps a post that was never filed out. The cause of a filed post scoring
  low later, scoring on the headline, is prevented instead ("No alert on a headline" above). The 15 items a
  withdrawal rule had taken out of 串流 on 15 Oct (09:37 HKT; their Telegram messages were never touched) were put
  back with their original ids (below the Telegram cursor, so not sent again), and that rule, its `withdrawn` table
  and its commands are gone.
- **A date with no time is a date, not 00:00 (Deyao, 2026-10-14: 文匯's 孫中山 160 周年座談會 showed 發佈 00:00; its
  page says 18:57):** a listing that gives only the day (a date in the URL or list, or a feed time of exactly midnight:
  金管局 and 政府新聞網 RSS, 東方's and the e-papers' sitemaps; 37 sources) gives a `corpus.DateOnly`; the post is
  stored with `posts.date_only` and, as `published`, the moment it was first seen within that day (`corpus.within_day`:
  the day's last minute when first seen later), which ordering, the poller's 2-day check and the scorer use. Pages show
  only the day (`corpus.shown_time`: item page, search, exports, coverage; the stream feed's items carry `date_only`).
  The page read (`fulltext_now`, even for a long feed text, and the 30-minute job) replaces the day with the time the
  page states (`cache_fetch.page_date`, now also 文匯/大公's comment-laden JSON-LD with Java dates, 商報's dateline,
  政府新聞處's 香港時間/Issued at HKT); pages that state only a day (`cache_fetch.DAY_ONLY_PAGES`: 東方 print, the
  e-papers, 金管局, 經濟導報, 熱血時報, 號外, 信報 print) are not read again for it. Existing rows:
  `python3 fulltext_fetch.py date-only` (marks the 00:00 rows, and the e-papers' 08:00 = UTC midnight, then reads
  their pages for times). Samples: `tests/date_samples/`, tests: `tests/test_date_only.py`.
- **Sources** (`sources.py` 102 feeds, `sources_extra.py` 33 + 36 for full coverage + 12 English, `browser_fetch.py` 4, `sites.json` (the WiseSearch catalogue's HK and Macau sources and the source-gap outlets, below; 2,247 feeds on 23 Oct 2026), + the streams' Google News queries): RTHK (all five Chinese and five English express-news RSS feeds + YouTube), 政府新聞網,
  政府新聞公報, 東方, 信報 (daily + instant), 明報 (7 sections), 香港01, 星島, 經濟日報, 文匯, 大公, 商報, am730, 晴報,
  都市日報, SCMP, 881903, 新城 (home page + every section page: 本地, 中國, 國際, 財經, English; a story keeps one URL whichever page lists it), NOW (API: 港聞, 國際, 財經, 娛樂, 生活, 科技, + Now Sports' own API, + Telegram), TVB, 有線, 經濟通 (editor's picks RSS + the whole latest-news list, two pages of 20 every poll; busiest 5 minutes on 14 Oct: 5 stories), 熱血時報, FS blog/speeches, FSTB speeches,
  HKMA, SFC. YouTube channels (RTHK and the K-Matrix-linked ones): each channel's uploads playlist (`playlist?list=UU…`, the
  100 newest uploads: videos, Shorts and past live streams; the /videos tab has neither Shorts nor streams) every poll.
  The playlist gives only relative times ("4 分鐘前" is exact to the minute; "3 小時前", "2 日前" only to the hour or
  day), so a video the corpus doesn't hold yet whose time is that coarse gets its exact time from YouTube's player
  answer (`sources.youtube_exact`: microformat publishDate, ~8 KB, no proxy, once per video, kv `ytpub:<id>`; capture-lag
  audit 2026-10-15: such videos were stored up to a day or a week off). `python3 sources.py youtube-dates` corrects
  the stored ones.
  Google News search adds other publishers (and discuss.com.hk via `site:`), with each link decoded to
  the publisher's URL (`app.decode_gnews`): at most 30 links a poll, newest first, each requested once; a link is
  marked read only once decoded. Google rate-limits the cluster's WARP IP (429 → google.com/sorry; from 10-14
  18:00 HKT to the fix on 10-15 almost nothing was decoded while every poll retried all ~450 pending links): its
  first refusal stops direct requests for 30 minutes (`gnews_direct_at` in poll_state) and links are decoded through
  `RESI_PROXY` meanwhile (the `/rss/articles` page, ~120 KB gzipped a link); status line `gnews_decode` on the
  monitor. HKEJ refuses the server's IP, so it goes through `RESI_PROXY` (below), its daily-paper pages every
  30 minutes and its instant news every 10.
  香港討論區 (discuss.com.hk bans datacenter networks): the whole site (below, `replies_fetch.py discuss`), plus the
  mobile site's keyword search (`discuss_queries` in streams.json), newest first, every 30 minutes, both through an
  IPRoyal Hong Kong residential proxy (`RESI_PROXY`, sub-user `yycstream`) on the replies job's sticky session (a walled
  or 403 IP moves to a new session; one failing query doesn't fail the others).
  **One residential proxy for everything (Deyao, 2026-10-15):** every proxied job (信報, 香港討論區, Google News links,
  YouTube/TVB audio, Facebook, Instagram/Threads, Reuters, Bloomberg, archive.today submissions) uses YYC's one IPRoyal
  sub-user `yycstream`. `RESI_PROXY` is its bare login; `resi.py` adds the exit country (Hong Kong by default, GB for
  Reuters, US/HK for Bloomberg) and the sticky session each job needs, and counts every proxied byte per site (kv
  `proxy:bytes:<site>:<hour|day|month|total>`), which the monitor's IPRoyal card shows.
  **Out of traffic is a pause, never a failure (Deyao, 2026-10-15; yycstream ran out ~14:40-15:02, 17:01-18:02 and
  19:41-20:24 HKT that day):** IPRoyal then answers 402 Payment Required (to the CONNECT of an https request; as the
  answer itself to a plain-http one, confirmed by a probe through the proxy). Every proxied client raises
  `resi.ProxyOut` at once (urllib in `sources.fetch`, curl_cffi in `resi.session`, yt-dlp in `media.run`, the replies
  job's sessions, Chrome's ERR_TUNNEL_CONNECTION_FAILED after a probe), and every job treats it as a pause: no try
  counted, no cursor or "checked" time advanced, the item stays queued, the run stops its proxied work, and the next
  run carries on by itself (fulltext, cache, links, the transcriber, broadcasts, the poller's 信報/citymagazine/
  香港討論區 search/Google News decodes, replies, comments, social_twopass's second reads, Facebook pages,
  Instagram/Threads, backfill, paywall/archive.today). Statuses say `proxy_out` (a source: off `proxy_out`, never
  failing). One record for all of them, kv `proxy_out` {since, at, message, jobs}, one Discord message per outage
  (none within 30 min of the last), ONE line on the monitor's stopped providers (IPRoyal (yycstream): out of traffic,
  with the paused jobs); the first proxied request that began after the outage did and worked clears it.
  `tests/test_proxy_out.py` runs every caller against a fake 402 proxy and fails when a new module calls the proxy
  without being covered.
  成報, 亞洲週刊 and 明報月刊 (Cloudflare managed challenge) through a real browser (`browser_fetch.py`: Google Chrome +
  Patchright under Xvfb on the server, every 30 min, ticking the challenge's checkbox; plain HTTP clients are refused
  even with the clearance cookie).
  The rest of the brief's media list (`sources_extra.py`): 英文虎報 The Standard (its news sitemap, every section), 香港仔, 鳳凰網, 號外, 亞洲時報 (English
  feed), 紫荊, 資本, 經濟一週, 經濟導報, iMoney, 明週, FSTB's LinkedIn page (its logged-out page's JSON-LD posts, `sources_extra.linkedin_read`:
  direct with Chrome's TLS fingerprint, then YYC's ISP IP, then the keyless r.jina.ai reader over fresh yycstream exits;
  LinkedIn answers some datacenter IPs 999 and IPRoyal refuses linkedin.com) and the eight political columns, each under its column name.
  Magazines' latest issue counts as current (`MAX_AGE_DAYS`). Not available anywhere online: Wide Angle 廣角鏡.
  **Every article of the HK outlets** (`sources_extra.COVERAGE`, for 搜尋): the publishers' Google News sitemaps
  (`<news:title>` + publication time, every section of the last ~2 days) of 東方 (on.cc instant news of every
  section, weather and the racing site /bknrac/ included, and the Oriental Daily paper), 香港01, 星島頭條, am730, 明報
  (the day's instant-news and daily-paper sitemaps), 都市日報 (`metrohk_sitemap`: the month's article sitemap every 30
  minutes, as its RSS holds the newest 30 and the print edition lands ~90 posts around 03:30; each read keeps the rows of the 3 days before the sitemap's newest, `recent_rows`), 經濟日報 (every HKET site), 大公報 and 文匯報 print editions (the tkww.hk e-paper's daily sitemaps), TVB,
  881903, 橙新聞 (news + finance) and 巴士的報 (its month post sitemap every 15 minutes, the rows of the last 3 days; `<lastmod>` is UTC written as +08:00 and read as UTC; titles from the URL until `fulltext_fetch.py` reads the
  page's own); 文匯網's 香港/內地/國際/財經/評論 pages, 點新聞's home page, SCMP's News/China/Business/World/Opinion
  RSS and its Google News sitemap (every section: sport, tech, lifestyle…, every 10 min), its videos and photo galleries (`scmp_video`, `scmp_photos`: the head of `video-format.xml` and `galleries.xml`, newest first, every 15 min; in none of the other feeds; coverage audit 20 Oct 2026), 信報's other daily-paper sections (兩岸, 國際, 觀點, 地產, 文化; through `RESI_PROXY`), HKFP, 法庭線 The Witness,
  獨立媒體, China Daily HK (home page) and Yahoo 新聞 港聞. The sitemaps whose site lists a story within minutes are
  read every poll; the month sitemaps, e-papers and slow sections every 15 to 60 minutes (`sources.EVERY`, due with
  `EVERY_SLACK`). 橙新聞's sitemaps are rebuilt only every 6 hours, so its 港聞/兩岸/國際/產經/地產 channels come from the
  site's own channel lists (`orangenews_channel`, a POST to api.orangenews.hk, every poll, under the sitemap's URL);
  its finance sitemap (A-share notices) has no other list. Capture-lag audit, 15 Oct 2026: `tests/test_capture_lag_news.py`. **Id walks** (`sources_extra.id_walk`, kv `hk01_walk` / `stheadline_walk`): 香港01 (its article
  API) and 星島頭條 (`/realtime-news/<id>`, which redirects to the article in any channel) are read id by id up to the
  newest id in their feed, every poll, with title, time, channel and the whole text, because their sitemaps leave
  stories out (星島日報's daily-* paper channels and opinion: 66 of 363 stories on 13 Oct 2026; 香港01: 9 of 730 on
  13–14 Oct); an id with no article yet is tried again for 3 days; at most `WALK_PER_RUN` (600) new ids a poll; an article found is offered again on every poll for 6 h (`WALK_KEEP`), so a poll lost before its corpus write (a poller restart) doesn't lose it.
  Not covered: 東周刊 (its site builds every list with JavaScript), 港人講地 (no article links in
  its pages), 852郵報 (the site is gone).
  **Hong Kong English sources** (2026-10-14; `sources.py` + `sources_extra.ENGLISH`): RTHK English (local, finance,
  greater China, international, sport; the RSS carries the story), news.gov.hk in Chinese and English (top stories + every category feed: `sources.NEWSGOV_CATEGORIES`, 9) and
  the English press releases (`general_en`), Dimsum Daily, Hong Kong Business (RSS; its <title> is the dek, so the
  page's og:title replaces it), DotDotNews English (home page), Nikkei Asia (RSS, undated: the page's datePublished; the free
  lead paragraph from the page), FT Hong Kong (headline + RSS summary), Mingtiandi (summary; its CDN serves /feed/ a week
  stale, so a fresh `?t=` per read), FinanceAsia, Asia Financial, Reuters (headlines from its open news sitemap, two
  pages every 10 min, plus the stories only its full sitemap lists, e.g. Breakingviews, titled from the URL;
  article pages answer 401 to plain clients: read through `paywall.py`, below) and Bloomberg (every headline of its news sitemap
  every 10 min, ~300 a day, all scored, plus the one-line summary its section RSS gives some stories; the story from
  the page's own data, `paywall.py`, below), Young Post (youngpostclub.com news pages, plus the first 200 KB of its articles sitemap, newest first, for
  every section: Spark, Posties, learning zone; hourly; `sources.HEAD_BYTES`). Nikkei/FT/Mingtiandi/
  Reuters/Bloomberg are region Global (`corpus.GLOBAL_SIDS`). English items are shown as they are (no translation).
  Google News also runs each stream's `news_queries_en` on the English edition (en-HK, `gnews_en_N`).
- **The WiseSearch catalogue's Hong Kong and Macau sources** (Deyao, 2026-10-20: "add the ones we are missing from
  Macau and Hong Kong"; every source that can be read online) = **`sites.json` + `sites_fetch.py`**: one generic reader
  for data entries, CronJob `sites` every 10 min (`sites_fetch.py run`; `test SID`, `check [SID…]`). An entry: id,
  name, `catalogue` (the WiseSearch title it covers), url, kind (rss/Atom, wp_json, sitemap, sitemap_index, listing with
  an href regex, regex (a title next to its link), xml, json with field paths, `{field}` link templates and a `text`
  path when the list carries the story, epaper with `{yyyy}{mm}{dd}` / `{ddmmyyyy}` and a lookback in days (`back`: a
  dated URL's date that many days before today, for a "from" parameter), parser: a function of
  `sources_extra.SITE_PARSERS` for a list that takes several requests or a signed one), type (Newspaper 報章 / Magazine 雜誌 / News / Official / Forum
  / Blog) and region (HK, Macau 澳門, Mainland, Taiwan 台灣 or Global; the outlet's own when the catalogue misfiles it), plus the route when the
  cluster's IP is refused: `"proxy": "isp"` (YYC's IPRoyal ISP IP, flat rate, one client at a time under the
  'yyc youtube' lock; a host IPRoyal refuses there goes over yycstream hk), `"proxy": "hk" | "mo" | "tw" | …` (yycstream,
  per GB, with that IPRoyal exit country: Taiwan's sites that answer only Taiwan IPs take `tw`, The Japan News `jp`,
  The Malaysian Reserve `my`) or `"browser": true` (CronJob `sites-browser`: `browser_fetch.py catalogue`, Chrome with
  its own profile, the same parsers, document `browser-sites.json`, status line `sites_browser`; with a `proxy` country
  Chrome runs on yycstream with that exit, a profile per country, its bytes counted as `browser-<cc>`). Article pages
  go the way their list goes, on the same exit country (`cache_fetch._catalogue_hosts`, `PROXY_COUNTRY`; a browser
  site's pages in a Chrome on its exit: `cache_fetch.browser_country`). Each read is conditional (ETag / Last-Modified: a 304 is an unchanged read), a
  quiet site is read 2×, 4×, 6× its type's interval (6 h at most; one new link resets it), and nothing is skipped (a
  later read still lists what came meanwhile). The job keeps the links no poll has read yet (`poll_seen`) for 6 h in
  the document `sites.json`; `sources.sites_file` gives one status line per site (monitor filter Catalogue). A site's
  first read is backlog (a new sid: first_run; "Backlogs": nothing works on it, never filed or sent).
  **A site that fails escalates at once** (Deyao, 2026-10-21 "fix the failing sources"): the job tries Chrome's TLS
  fingerprint direct, the ISP IP, then yycstream hk (plain, then as Chrome; never yycstream for YouTube), leaving out
  the route that just failed, a site with its own `proxy` included; a route bringing rows wins over one answering an
  empty list (a challenge page parses as an empty feed), and it is kept for a day (state `auto_route`), then the site's
  own route again. Why: a WARP exit can be
  refused by sites another exit reads (21 Oct: a Helsinki node's WARP exit, colo HEL, got 403/429/409, TLS handshake
  timeouts and challenge pages from about 40 sites the fsn1 nodes, colo PRG, read), and the old rule (escalate on the
  second failure) left quiet sites failing for up to 6 h. The poller's own plain direct
  sources do the same over yycstream hk (`sources.escalates`, kept `ROUTE_KEEP` 6 h in the poller's memory, status
  `route: yycstream`). Every node is in fsn1 with the same WARP colo (deploy/cluster/README.md "Nodes"), so escalation
  is for a site's own refusals.
  How the list was built and checked (helpers' research per title, live reads from the YYC server, the route per
  site): the catalogue session's `~/artifacts/macau-sources/` (`STATE.md`; `cluster/probe.sh` runs entries in a pod
  on the cluster: direct, ISP IP, yycstream hk/mo). The per-title review (status, evidence, feeds, route, collected,
  what we can get) is the gated page `/sources/` on `yyc-ai-visibility` (`docs/sources/sources.json`, regenerated by
  that session's `build_table.py`); print titles with no online version: `~/artifacts/wisers-catalogue/print-only.md`.
  Each stored post can be checked one by one in the proof view, `/app/proof?catalogue=1` (or `?sid=a,b`).
  **The source-gap outlets (Deyao, 2026-10-23: "add the real gaps"):** the hosts Google News brought posts from that no
  source read (kv `source_gaps`, `infra/thresholds.py gaps`; the audit of 23 Oct with the route tested per host: the
  session's `~/artifacts/source-gaps.md`) whose route is a feed, a sitemap or a page listing are sites.json entries
  with `catalogue` "… (source gaps, 2026-10-23)", ids `hk_` / `cn_` / `tw_` / `gl_`: 152 sites, Taiwan 75 (24 RSS, 19
  sitemaps, 5 sitemap indexes, 23 listings, 3 regex, 1 JSON), Global 56 (overseas Chinese papers such as 星島 Canada
  and USA, 世界日報, 大紀元, 文學城, and the rest: 25 RSS, 10 sitemaps, 4 sitemap indexes, 15 listings, 2 regex),
  Mainland 13 (國務院港澳辦, 香港中聯辦, 搜狐, 風聞, China Daily, Xinhua English … 10 listings, 2 RSS, 1 sitemap) and HK 8
  (4 RSS, 3 sitemaps, 1 listing); TechNode is read hourly (its feed is 11.9 MB); 財華網 and 虛詞 (`hk_finet`, `hk_p_articles`) read their listing pages, their sitemaps
  being stale. 自由時報 (news, 財經) and 翻爆 go on the ISP route like their article pages; 自由時報·財富自由 is the JSON its
  page reads (stock-api.ltn.com.tw); The Edge Malaysia, 金門日報, 信吉衛視, 數位時代 and United News Ph are `regex` over
  their pages (a page's own data or JSON-LD). Every one was read once through `sites_fetch.read` before it was added.
  **The walled and code-needing gap hosts** (the audit's "wall" and "parser" rows; `tests/test_gap_walls.py`), each read
  once from a pod on the cluster through its route before it was added: Taiwan sites that refuse foreign IPs on
  yycstream `tw` (華視 CloudFront geo 403, 台灣新聞雲報, 台灣醒報, 很角色 taidaily.com, 是新聞 YesMedia behind Cloudflare to
  every non-Taiwan exit, 警政時報 and 中華日報 timing out / ELB 403); The Japan News on `jp` and The Malaysian Reserve on
  `my` (AWS WAF / Cloudflare elsewhere); Chrome on the cluster's IP for Cloudflare's managed challenge (香港財經時報 HKBT,
  SL886, 星洲日報, OpenGov Asia, Dealroom's news (a regex over the page's own data), 僑新聞 ocacnews.net's Incapsula)
  and Chrome on yycstream for Lifestyle Asia HK (`hk`: CloudFront refuses the cluster's IP, AWS WAF's challenge passes
  on a residential one; its newest post sitemap) and 華人今日網 (`us`); direct: 台灣中評網 (home listing), 創新聞 and
  Law.asia (RSS), Mirage News (its home page lists the newest ~50 of ~600 a day; every 10 min), Eastern Herald (RSS as
  Chrome), 報新聞 Mega News (contentplatform.info is an S3 redirect to www.contentplatform.info, whose feed reads), the
  UAE foreign ministry (its English and Arabic news pages, `regex` over the cards: the Arabic list has items the
  English one lacks), ThinkChina's six section pages on the ISP IP (CloudFront 403 to the cluster's IP; hourly, its
  robots.txt asks for 10 s between requests). New code: parsers 香港風火時報 (`flamingwheels`: the list its pages POST
  for, the story in each row), LINE TODAY (`line_today`: every article list on the 焦點/國內/國際/理財 tabs; an
  aggregator, the story's original from its article API), PressReader (`pressreader`: 文匯報, 大公報, 經濟一週's latest
  issue's contents with PressReader's anonymous token, no account; the text from its article API) and student.hk
  (`student_hk`: its signed list API, 熱話 and 專欄, the story in each row); JSON lists 地球物理氣象局 (cms.smg.gov.mo's
  search from two days back, the story in each row), 廣州日報 新花城 (熱聞, 城事, 首頁, 灣區), 鉅亨號 (its POST list API),
  TradingView's news flow (Hong Kong market, Traditional Chinese and English) and Moomoo's news list; stories whose page
  holds none come from their API (`cache_fetch.api_story`: TradingView, Moomoo posts, LINE TODAY, PressReader,
  地球物理氣象局). Not added: dda.hsinchu.gov.tw (新竹縣 數位發展處: connections from the cluster time out, IPRoyal refuses
  every .gov.tw host on its residential and ISP routes (CONNECT 403), r.jina.ai refuses the cluster's IP; it needs a
  Taiwan exit that is not IPRoyal) and racingnews.hkjc.com (HKJC's racing news answers its maintenance page,
  common.hkjc.com/maint, to every route: the cluster's IP gets Akamai's 403, the ISP IP, yycstream hk plain, as Chrome
  and Chrome on yycstream hk the maintenance page or a 90 s timeout, while www.hkjc.com reads on yycstream hk).
  **Taiwan is a region** (`sites_fetch.REGIONS`, edge config `regions`, the search filter's 台灣, the MCP tools):
  Taiwan sites by sites.json, Google News publishers by `corpus.TAIWAN_PUBLISHER` (聯合新聞網, 自由時報, 中時, ETtoday,
  TVBS, 三立, 中央社 …; 世界日報 stays Global), a linked page on a .tw host by `corpus.link_meta`. Tests: `tests/test_sites.py` (`test_taiwan_region`).
  **Mainland, Taiwan and Global sites are not read (Deyao, 2026-10-28: cost)** (`sites_fetch.OFF_REGIONS`; 20-27 Oct
  they gave 31% of the posts scored and 14 that reached 50): they stay in sites.json, unread, and link following skips
  their hosts as ignored by Deyao (`follow.off_hosts`), so they never show as unknown sites. Read anyway
  (`OFF_EXCEPT`): 港澳辦, 中聯辦, robotbelt.com and Sing Tao Canada. Google News results from those outlets still come
  in (the search itself is the filter). Tests: `tests/test_sites.py` (`OffRegions`).
- **No thinking (Deyao, 2026-10-28: "turn off thinking entirely"):** `ai.chat` / `ai.chat_stream` send reasoning
  disabled unless a caller asks for it, the lowest effort where a model can't turn it off (`ai.THINKING_MANDATORY`, none in use); `tests/test_no_thinking.py`.
- **Full text or nothing, per source (Deyao, 2026-10-20: "so many sources don't capture the full text").** A body that
  is only the title again (empty, the headline plus links and at most `titles.TITLE_SLACK` letters, or a part of it:
  `titles.title_only`, TypeScript twin `titleOnly`) is no text: the page read keeps "" (`fulltext_fetch.save`), and
  the item page never labels it 全文 (it showed Dimsum Daily's headline, moved into the text by an AI title, as 全文).
  An earlier page read's text (junk a broken extractor kept) is replaced by the next read; a longer feed text stays.
  Proof-of-work robot walls are answered in Python (`walls.py`, from `sources.fetch`): SiteGround's sgcaptcha (集誌社
  thecollectivehk.com, Dimsum Daily: HTTP 202 and a meta refresh to /.well-known/sgcaptcha/ from the cluster's IP)
  and eQualit.ie Deflect (追光者 pulsehknews.com: HTTP 429), BytePlus WAF's SHA-256 proof of work (開飯 OpenRice
  since 21 Oct 2026; Chrome looped on it, so 開飯·最新食評 is read without the browser), once per page, the cookie kept per host; a wall still
  standing is a `Challenge` (tried again later), never an empty article. Hosts that refuse the cluster's IP but take
  the ISP IP (自由時報, 翻爆: `cache_fetch.ISP_HOSTS`) are read through it by the `fulltext` job under the ISP lock.
  `fulltext_fetch.py refill [SINCE] [--sids A,B] [--only]` reads again the posts since SINCE (default
  the stage's start point, `corpus.cutoff("fulltext")`) that kept no article text (`--only`: the named sources alone); the scorer scores each
  changed post again (`streams.HASH`). It queues them (kv `fulltext:refill`, checkpointed: a post leaves the queue
  once its read was tried) and reads the first `FULLTEXT_REFILL_CHUNK` (500) at once; every `fulltext` CronJob run
  then reads the next chunk beside its candidates, under its own deadline, so a refill never needs a longer Job
  (refill Jobs with FULLTEXT_DEADLINE raised died at the 29-minute deadline, 2026-10-20). Queue it from a pod of its
  own: `deploy/cluster/run.sh --memory 1Gi --secrets isp-proxy -- python3 /app/fulltext_fetch.py refill …`, or with
  `--dry` to count first. Forum-type posts that are no thread
  (`fulltext_fetch.forum_page`) are read like news; a thread's opening post comes from its collector and, like a
  social post, is its whole text even when it is only its title (`titles.WHOLE_TYPES`). A reCAPTCHA wall page is a
  429 (read again later), never an empty article.
  Per-source routes and readers found from Deyao's proof notes (2026-10-21): an article page may take another route
  than its list (sites.json `"page_proxy": "hk"`: uwants' list reads on the ISP IP, where every thread page is a
  reCAPTCHA wall, so its pages go through yycstream); 華富財經 Quamnet's posts come from its JSON API
  (`cache_fetch.quamnet_text`); a WordPress page's one `entry-content` block is the story when it holds the paragraph
  cluster and more (stories split into blocks) or the cluster is under 200 characters; a MemberPress paywall gives its
  excerpt (`text_via` excerpt); Media OutReach's release is its innerband-content block (the Chinese release stays
  Chinese: the post is the language version its link names). Every poll stores a post under its address without
  tracking parameters (`corpus.strip_tracking`, `TRACKING` incl. uwants' per-read `tr_h`; `corpus.clean_tracking_urls()`
  moved the stored ones and merged uwants' copies of one thread). A post that link following stored as 〔連結〕host
  takes its page's headline when the `fulltext` job reads it.
  **Aggregators** (sites.json `"aggregator": true`: headline4hk.com, a page per story of 星島, NOW, on.cc, RTHK …
  with 「…原文連結」 and og:url on the outlet): the page read names the original (`cache_fetch.original_link`), which is
  followed at once (`follow.pointer`: the corpus's post, a new post of its outlet read then, or an unknown site listed),
  and the aggregator's post carries the original's text (`text_via` original, 「原文出版機構網頁」, `text_via_url` the
  original), so the two are one article (same text key). Tests: `tests/test_fulltext_proofed.py`.
  The monitor's Full text card lists the full-text rate per source over 24 h, worst first (`monitor.fulltext_sources`).
  The scan behind it: `~/artifacts/fulltext-scan/` of the session that ran it (before/after tables).
- **Reuters, Bloomberg and paywalled outlets: full text (`paywall.py`; Deyao, 2026-10-15: "we should be reading all
  texts including reuters bloomberg and FT", "pay captcha solvers … add the costs in", "for bloomberg we can just see
  what happens as a proof of concept, otherwise don't break their terms").** Read by the same full-text readers
  (`fulltext_now.py` on sight, the `fulltext` job), each post's `text_via` saying where its text came from (shown on the
  item page: 內文來源).
  - Reuters (DataDome bot wall: 401 + an interstitial device check to every plain client and every automated Chrome
    we tried, residential IP included): Reuters' own content API (~20 KB a story) with a DataDome cookie CapSolver
    solves (DatadomeSliderTask, ~4 s) through `RESI_PROXY` (sub-user `yycstream`, a sticky GB IP, the
    User-Agent CapSolver requires); the cookie is kept in kv `paywall:datadome` and reused across pages and runs.
    `text_via` 'solver'. USD 0.0025 a solve (CapSolver's price list; its balance confirmed it on 15 Oct: 3 solves,
    USD 0.0075); one solve read every page tried after it.
  - Bloomberg (proof of concept): the story from the page's own `__NEXT_DATA__` (the page is metered, not cut: the
    whole body is there for any logged-out reader), from the cluster's IP. `text_via` 'page_data'; its robot check
    keeps the feed's summary as an excerpt.
  - Paywalled outlets (`paywall.ARCHIVE_PAGES`: FT, Nikkei Asia, Mingtiandi, 信報's paper, HKET's paid sections; and
    any page that declares `isAccessibleForFree: false` yet gives under 1,000 characters; SCMP serves its whole story
    and is not one): the public excerpt is kept (`text_via` 'excerpt'; hidden paid text in a page or API, e.g. HKET's
    `paywall`/`full` fields or Nikkei's page data, is never read: their terms), then CronJob `paywall` (:17/:47,
    `paywall.py archive`) takes the newest archive.today snapshot's text when longer (`text_via` 'archive', the
    snapshot's URL in `text_via_url`), else submits the page once and looks again on later runs (6 looks, 3 days).
    archive.today answers every IP with a reCAPTCHA (HTTP 429): 2Captcha solves it in a real Chrome (USD 0.00299 a
    solve, 2Captcha's own figure) and the clearance cookies are reused; TWOCAPTCHA_API is YYC's own 2Captcha account (EXPENSES.md);
    with no TWOCAPTCHA_API a captcha stops the run with the posts' looks unused, and nothing else solves it; lookups go from the cluster's IP,
    submissions through `RESI_PROXY` (a datacenter IP's submission is taken and never captured). archive.today stops answering
    the cluster's IP on every mirror for a while after a burst of lookups (15 Oct, after ~40 at 2 s apart): mirrors are
    tried in turn, requests are 4 s apart, and a run that finds none answering stops with the posts' looks unused. Reuters has no
    snapshots (archive.today's crawler gets its 401 too, lessons/25).
  - Costs: every solve's charge as the provider states it in table `solver_spend` (the monitor's "Captcha solvers"
    series, with CapSolver's and 2Captcha's balances); the proxy traffic under the sites reuters, bloomberg and archive
    on the monitor's IPRoyal card. A solver or the proxy refusing for its balance is a safety stop: kv `solver_limit`, one Discord
    message, the monitor's stops panel.
- **Facebook pages** (the facebook rows of `watchlist.json`, `fb_fetch.py`, CronJob `facebook` every 15 min): legislators, bureaus, parties, unions,
  commentators and media outlets' pages (K-Matrix links media pages' Facebook posts, so they're on for parity). A page
  that posted within the last 24 h is checked every 30 min, the others every 3 h (`fb_fetch.every_minutes`; Deyao,
  2026-10-29: IPRoyal bandwidth): Facebook's own data request with afterTime = the newest post seen, once an hour 3 hours
  (`OVERLAP_H`) before it, once a day 26 hours (`DEEP_H`), paged back until there are no more, so every post of a page comes in,
  also one that appears behind a newer one (Facebook dates a post by its creation; a video or scheduled post shows up
  later); a post id already read is never filed again (its pfbid link changes between reads). ~11 KB for a page with
  nothing new, ~35 KB a new post, over `RESI_PROXY` (sub-user `yycstream`, Hong Kong exits;
  counted under the site facebook). `facebook.json` keeps the posts of the last 6 hours. `fb_fetch.py backfill H` reads
  every page back H hours once. A page's first read is its backlog: the posts published before it (state `since`) are
  filed as first_run (a backlog: "Backlogs", never a stream item or Telegram post; the watchlist expansion of 15 Oct filed
  ~3,000 such posts as new, with first-seen 15 h to 130 days after publish). **The logged-out session is renewed only
  when Facebook refuses it** (Deyao, 2026-10-20; a browser capture is ~3 MB of Facebook's app code): each run first
  asks for the busiest page's newest post, and no post there (or three error answers in a run) means refused, renew;
  a session is kept however old while it is answered. A page that answers its Page node alone (no timeline logged out:
  9 of 223 pages on 20 Oct, e.g. 鄺俊宇, 鬆仔, 綠豆; `fb_fetch.NoTimeline`) is that page's error and never renews it
  (those pages renewed it on nearly every run, ~12 MB/h). Each new post's id goes into kv `fbpid:<url>` as it is filed
  (the comment reads name the post by it). Not readable logged out:
  房屋局 (facebook.com/THBHK) and 簡慧敏's profile (Facebook shows "content isn't available" without a login).
  Each post's photos and videos come with it (`fb_fetch.media`, from the story's attachments; Deyao, 2026-10-15: photo
  and video posts with a caption of a few hashtags were scored on nothing): a photo post's photo, every photo of an
  album, a link share's preview image and a video's thumbnail go to the picture reader (`images.py`), and the video
  itself to the transcription queue (`fb:<video id>`, its reel/permalink URL, read with yt-dlp over the residential
  proxy). A link share's article URL (the attachment's `external_url`, `fb_fetch.shared_link`) joins the text when
  the caption lacks it, so `links.py` reads a short caption's article and the post groups with it (`link_key`).
- **Instagram and Threads** (the instagram and threads rows of `watchlist.json`, `ig_fetch.py`, CronJob `instagram` every 15 min): the logged-out profile page's 12 newest posts
  (~100 KB per profile: the read stops after the posts' data), times decoded from the media ids, through YYC's IPRoyal
  ISP IP first (flat rate, the 'yyc youtube' lock) and `RESI_PROXY` (sub-user `yycstream`) for an account it can't
  serve (Deyao, 2026-10-29: IPRoyal bandwidth). An account that posted within the last 24 h is read hourly, the others
  every 6 h (`ig_fetch.every_minutes`); when all 12
  posts are newer than the newest one known, the posts in between come from TikHub's paginated user posts
  (`/instagram/v2/fetch_user_posts`, YYC's key). Starts with every account K-Matrix has linked; the K-Matrix report's
  misses show which to add. An account's first read (its 12 newest posts, pinned old ones too) is filed as first_run,
  like a new Facebook page's. Each post's pictures and videos come with it (`tikhub_fetch.meta_media`, also used by the
  Instagram hashtag search): the picture (a carousel's every picture where the data has them; the profile page gives
  the cover) to `images.py`; an Instagram video to the transcription queue as `ig:<code>` (its /reel/ page, yt-dlp over
  the residential proxy: the profile page carries no video URL), a Threads video as its mp4 (`threads:<code>`).
- **Watched accounts (Deyao, 2026-10-14; `watchlist.json`, `watchlist.py`, `watch_fetch.py`, CronJob `watch` every
  10 min):** besides searching the platforms, the stream WATCHES the important accounts directly: every post they
  publish, on every major platform each one uses. `watchlist.json` is the one reviewable list, a row per (account,
  platform): `group` (media = outlets' official accounts; kol = voices that shape HK discussion; politician = the CE,
  officials, ExCo, LegCo, parties, departments; business = tycoons, companies, HKIC, Galbot), `name`, `owner`, `role`,
  `platform`, `handle` / `url` / `id`, `why`, `evidence` (where the account is shown to be theirs), `since` (the day it
  was added). Who reads which platform: Facebook `fb_fetch.py`, Instagram and Threads `ig_fetch.py` (their proxied
  paths, above); YouTube the channel's own RSS feed (free, no proxy, on every 10-minute run; channels `sources.py` already reads
  are left to it); X (`/twitter/web/fetch_user_post_tweet`, USD 0.001; pure retweets skipped; content as below under
  "Posts that are only a link") and TikTok
  (`/tiktok/app/v3/fetch_user_post_videos`, USD 0.001) through TikHub, YYC's key. Each check reads every post since the newest one seen (back 3 h, posts
  already filed are known by URL; X and TikTok page on while every post is new). X and TikTok accounts are checked
  every 30 min (`watch_fetch.EVERY_RUN`; Deyao, 2026-11-11: every 10 min was USD 4.61 a day for 32 accounts, 30 min is
  USD 1.54; 2026-10-15: at 30 min / hourly / 2 h, watched X posts came in a median 32 min, p90 122 min late). Posts go through the poller like every other source
  (`sources.watch_file`, sid `watch_<platform>`, the account as the board), so they get AI titles, scores and streams;
  YouTube posts are transcribed (`media.py`), and their comments come in for the two-pass scoring (`social_twopass.py`).
  No comments are read on X or TikTok (Deyao, 2026-10-20: comments only where the two-pass scoring needs
  them; these platforms are not two-pass units).
  **Safety stop: USD 100 a day** (Deyao, 2026-10-15) of new watching spend (env `WATCH_CAP_USD`; "I'm not optimising cost, it's just so I
  don't find a huge bill tomorrow morning"): every successful TikHub request at its list price, the IPRoyal bytes of
  the newly watched Facebook/Instagram/Threads accounts (their checks and comment pages, at YYC's last order's USD 5.51
  per GB) and the OpenRouter cost the watched posts add (per-post AI by their share of the day's posts; transcription
  by the charged cost of the clips only they carry). At the stop the watching pauses until 00:00 HKT (YouTube too: its
  videos are transcribed; accounts followed before 2026-10-14 are not part of it), kv `watch_paused` shows
  it on the monitor and Deyao gets one Discord message. The monitor's "Watched accounts" card shows accounts per
  platform and group, how many answer, the posts filed and the day's spend against the stop. Test one account:
  `python3 watch_fetch.py test x SCMPNews`. Tests: `tests/test_watch.py`.
- **Comments are read only for the two-pass scoring (Deyao, 2026-10-20: IPRoyal `yycstream` at ~308 MB/h, Facebook
  comment reads ~172 MB/h of it):** at capture and once for the second scoring at published + 30 min, by the poller's
  `social_twopass.py` thread (below), and never again: nothing re-checks a post's comments later, on any platform. The
  CronJob `comments` (it re-checked every post 2 h / 6 h / 24 h / 48 h apart for 7 days) is suspended (the monitor shows
  it off) and `comments_fetch.py` holds only the readers: YouTube's own videos (every channel in `sources.py`, YouTube's
  web API `youtubei/v1/next`, newest first, top-level comments: logged out, YouTube sends no replies), Facebook posts
  (Facebook's own comment request, newest first, plus the replies to comments, with `fb_fetch.py`'s logged-out session
  over `yycstream`; the post named by the id `fb_fetch.py` records in kv `fbpid:<url>`, which reels need; a thread is
  read to its end, and when the post counts more comments than the corpus holds, every comment page and the replies of
  comments whose reply count grew) and Instagram / Threads posts (the logged-out post page's comments over `yycstream`;
  when an Instagram post counts more than that page shows, every comment and reply through TikHub's
  `/instagram/v2/fetch_post_comments` and `fetch_comment_replies`). Each comment is a corpus row (`corpus.add_comments`)
  with its own time, so 搜尋 finds words inside comments, and the scorer reads them with their post.
  `comments_fetch.py capture` re-captures Facebook's comment request when Facebook changes it. Tests:
  `tests/test_comments_twopass_only.py`.
- **The whole LIHKG site (Deyao, 2026-10-14; `replies_fetch.py lihkg`, CronJob `lihkg` every 5 min):** every thread
  of every board with its replies. 最新 (cat 3) lists every board, latest reply first; thread ids are sequential
  site-wide, so an id between the last one seen and the newest listed that 最新 didn't show is read directly and its
  board (one 最新 hides) is listed from then on. A new thread is read at once; a thread that grew is read again 25 min
  after its last read, or as soon as its post time + 30 min has passed since its last read. Measured 2026-10-14
  (LIHKG's API): 444 thread ids a day, 885 threads with a reply in 24 h, 12,776 replies a day (7,349 counted in the
  day's new threads + 5,427 projected from a sample of 45 older threads), replies 29.6 characters on average.
  **One LIHKG client for every reader** (`lihkg_api.py`; Deyao, 2026-10-20): this job, the poller's 時事台 source
  `lihkg_current` and the full-text reader send the web app's X-LI-DEVICE headers (without them LIHKG answers the
  cluster IP with 429: live 2026-10-20 the poller's header-less request got 429 while the job's passed), take turns on
  one clock in Postgres (kv `lihkg:next_at`, one request a second direct across all of them) and share one back-off:
  a 429 / 403 / 1015 on any reader's direct request sets kv `lihkg:blocked_until` (26 h), during which every reader
  goes through RESI_PROXY (the poller retries the same request through it at once; this job stops and its next run
  starts proxied).
  **Scored by thread** (`twopass.py`, shared by every source with comments; `streams.py` unit_due; ai_spend
  `scoring_lihkg`): the opening post and its replies as one unit, at capture and once more 30 min after its post time
  once its replies were re-read (not when first captured later than that). The later score is the current one; both
  are kept in `score_passes`. A thread is one item in 串流 / 數據分析 and one Telegram message per stream (from the
  first scoring that passes); its reply rows are never scored on their own. The item page lists every reply read so
  far with its likes and dislikes, and a divider 「以上回覆已納入評分（time）」 after the replies the latest scoring saw.
  Another source joins by adding its post URL pattern to `twopass.UNIT_URLS` and calling twopass's reading / captured
  / fetched from its fetcher (module doc).
- **Social posts scored with their comments (Deyao, 2026-10-14; `social_twopass.py`, a poller thread, on `twopass.py`
  like LIHKG):** Facebook, Instagram, Threads and YouTube posts (`twopass.SOCIAL_URLS`, every collector's, including
  the social searches') are units: the scorer waits for their capture read (`twopass.FETCHER_URLS`: FIRST_WAIT at most),
  `social_twopass` reads each new post's comments at once (comments_fetch's facebook_post / instagram_post /
  youtube_post; a Facebook read is bounded: at most `FB_PAGES` (50) requests, newest comments first, no pass over every
  older page, and `FB_READ_S` (120 s), so one busy thread never stalls the fetcher), and reads them again when the post's second scoring is due (published + 30 min; none when first caught
  later than that). The later score is the current one; one item and one Telegram message per post and stream; the
  item page shows every comment and the divider 「以上留言已納入評分（time）」. Those two reads are the only ones.
- **The whole 香港討論區 site (Deyao, 2026-10-15; `replies_fetch.py discuss`, CronJob `replies` every 5 min):** the
  LIHKG design on discuss.com.hk. Every board of the archiver's forum index (449 boards on 2026-10-15), each listed
  through its archiver pages (50 threads a page with reply counts, latest reply first, ~6 KB through the proxy), paged
  on until 5 known threads with no new reply were passed; a board whose first page didn't change is listed every 2, 4,
  8, 12 runs (at least hourly), one that changed every run. Thread ids are sequential site-wide, so every id between
  the last one seen and the newest listed that no listing showed is read directly. The first listing is a baseline
  (reply counts recorded; a thread is read in full once it grows). New threads are read at once, newest first; a
  thread whose second scoring is due (post time + 30 min) is read again directly. **Scored by thread** like LIHKG
  (`twopass.DISCUSS_THREAD`, ai_spend `scoring_discuss`): one item in 串流 / 數據分析, one Telegram message per stream,
  the item page lists every reply with 「以上回覆已納入評分（time）」. **Route** (`discuss_route`, one probe request each
  run): the cluster's own IP when the site answers it, else YYC's ISP IP (each request under the 'yyc youtube' lock,
  bytes in kv isp:bytes), else yycstream; on yycstream a page tries `DISCUSS_WALLS` (3) sticky IPs, then the run stops
  (27 Oct - 17 Nov 2026 every residential IP got the reCAPTCHA wall: 8 IPs a page, ~0.7 GB of yycstream a day, no
  reply stored; the ISP IP passed). The poller's keyword search (`sources.discuss_search`) takes the same route. 0.5 s between requests (`DISCUSS_GAP`), on one kept-alive connection per sticky IP (curl_cffi: 0.43 s a request through the proxy instead of 1.25 s on a new connection each time, 2026-10-15). Boards are listed most overdue first, against each board's own interval (`overdue`: strictly longest-ago first listed all 449 boards, the busy ones too, once in 48-71 min, and their replies waited that long); a long thread is read up to the run's deadline and carries on in the next run (a thread of hundreds of pages ran runs past the CronJob's 600 s deadline).
  Measured 2026-10-15: 449 boards in the index (32 of them » links with no threads of their own); thread ids are
  sequential site-wide (150 consecutive ids: 130 readable threads, 18 behind the betting boards' 18+ notice, which the
  job agrees to, 2 deleted); 1,340 new thread ids in the 48 h of 13-14 Oct (≈670 a day); the five news boards alone
  had 1,500-2,300 replies a day. Through the proxy: an archiver page ≈5.5 KB, a new thread's printable view ≈2.9 KB.
- **The catalogue forums' threads (Deyao, 2026-10-20; `forum_fetch.py`, CronJob `forums` every 5 min, data
  `forum_threads.json`):** the forums of `sites.json` ("type": "Forum": 親子王國, 教育王國, Post76, 公仔箱, 著數來, 夜玥,
  YK Forum, Star.Starphoto, 141, 伊莉, GeoExpat, U公屋, Android Central, asiaxpat, review33, BP大平台) come in through
  `sites_fetch.py` as thread listings, one title-only post per thread. This job reads each such thread (posts of the last
  2 days whose URL matches a forum's `thread` regexes): the opening post's full text becomes the post's text (its title
  too when the listing's was a placeholder: a sitemap slug, `forum.php`), every reply a comment row (`corpus.add_comments`,
  thread = the post's URL, the reply's own author and time). Scored by thread like LIHKG (`twopass.FORUM_URLS`, in
  UNIT_URLS and FETCHER_URLS: the first scoring waits for the read, FIRST_WAIT at most; the second one at published + 30
  min after a re-read, `twopass.due_for_fetch`). A followed thread is read again 30 min after its last read, the wait
  doubling after each read that found nothing new (up to 24 h) and starting over after one that did; a thread with no new
  post for 7 days is no longer followed. State per thread: table `forum_threads` (read = the highest post number filed;
  a re-read starts at the page of the next one). Per forum one line of data: the parser (Discuz printable view = posts
  1-100 in one request, then Discuz X / 6 / 7 thread pages; vBulletin printthread; XenForo; NodeBB `/api/topic`; JSON-LD
  DiscussionForumPosting; review33's message frame; WordPress REST post + comments; phpBB), the route its listing uses
  (direct; `isp` = the flat-rate ISP IP under the 'yyc youtube' lock, bytes by resi.count_isp: 親子王國 (pages ~290 KB),
  教育王國, 著數來, 夜玥, YK Forum, 伊莉; `fallback: hk` = yycstream residential, one sticky session, bytes per site, only
  after six tunnel 403s in a row 10 s apart (IPRoyal refuses the host on the ISP IP: 伊莉, 2026-10-20; one 403 is another client on
  the IP, so it waits first)), the client (`chrome` = curl_cffi as Chrome where urllib's TLS gets a bot page), `gap` between
  requests (1 s; forums run in parallel). 伊莉: its 瀏覽器安全檢查 proof of work (SHA-256 with 4 leading zeros, set as
  cookies) is solved and its 18+ notice agreed to (POST agree=yes) on one sticky HK session. A thread behind a login
  (Discuz 提示信息: 無權訪問 / 閱讀權限) is `noaccess`, never retried. AV100fun (Cloudflare's managed challenge on every
  other route) is on the `browser` route: CronJob `forums-browser` (every 15 min, `run --browser`) reads its threads in
  one real Chrome under Xvfb (a fresh profile each run: a kept one got an empty page; nomedia.block). Off (`off` in the
  data, with the measured reason): DCFever (read.php answers an empty body on every route, a real Chromium too). The monitor's Forum replies card has one line per forum (`forums:<forum>:status`: threads
  read, replies stored, pending, proxy MB today, failing; an off forum with its reason). Test one thread:
  `python3 forum_fetch.py test URL`. Tests: `tests/test_forum_fetch.py` (fixtures from the forums, text only).
- **Forum replies** (`replies_fetch.py`, CronJobs `replies` (香港討論區) and `lihkg`, every 5 min): every
  reply in the LIHKG and 香港討論區 threads above, plus the threads the collectors filed (a filed thread is read by the listing's current reply
  count, never the count from when it was filed), each reply a corpus row with its own time
  (`corpus.add_comments`), so an old thread counts on the day its new replies mention a name, as in K-Matrix's
  "1 related in 168 post & comments". LIHKG direct through its own API; 香港討論區 over `yycstream`, every post of a
  long thread (over as many runs as it needs); proxy bytes are counted, never capped.
  `replies_fetch.py stats` shows rows and proxy bytes. LIHKG threads and replies
  carry LIHKG's likes and dislikes (`engagement.likes` / `engagement.dislikes`, shown as 讚好 / 負評 on the item page);
  a thread page re-read refreshes its earlier replies' counts.
- **Social platforms by search term, every 3 hours** (`tikhub_fetch.py --run`, CronJob `tikhub` hourly at :06; each
  TikHub (platform, query) is searched every 3 hours and each Facebook query every hour (`tikhub_fetch.EVERY`; Deyao,
  2026-11-11: hourly TikHub search was USD 4.79 a day), a failed one again on the next run. A busy query's one result
  page can hold less than 3 hours (coverage audit, 2026-10-14: Threads 李家超 20 posts in 24 h), so such a query
  misses posts: newest first where the platform offers it, every stream's `social_queries` (default: its
  `news_queries`) on TikTok, X, YouTube, Reddit, Threads and Instagram hashtags through TikHub, plus **Facebook post search through AnyAPI** (`fb_search.py`, SKU
  `facebook.search_posts`) for streams with `"facebook_search": true` (none since 2026-10-28, Deyao: GALBOT's turned off; AnyAPI is out of credit). Every post a search returns (published in the last 3
  days) is kept, with no keyword filter and no per-query cap (Deyao, 2026-10-14: "all content"): Facebook's search
  matches loosely (銀河通用 brings back 銀河 galaxy posts) and the scorer leaves those out of every stream. A Facebook
  search post brings its image and album preview pictures (to `images.py`) and, for a reel or video link, the video (to
  the transcription queue); a post with no text is titled "(post without text)", never with its author's name.
  Writes the document `tikhub.json` in Postgres (`corpus.file_put`; read by `sources.tikhub_file`); sources show as "<author> (<platform>)". Each post's
  section names the platform and the query; like every other post it goes into the corpus and the scorer decides
  which stream, if any, it belongs to.
  **Mirror bots: the original article wins** (Deyao, 2026-10-20: "why is there a source from scmpauto subreddit but
  not scmp?"). The AutoNewspaper network (r/AutoNewspaper and one r/<outlet>auto per outlet, e.g. r/SCMPauto, r/NYTauto;
  accounts AutoNewsAdmin / AutoNewspaperAdmin; `tikhub_fetch.reddit_mirror`) re-posts outlets' articles as Reddit
  link posts, sometimes a day later as new. Such a post is never collected as a Reddit post: `sources.resolve_mirrors`
  routes its link through link following's routing (`follow.mirror`, below: one path for every link): dropped when
  its article is in the corpus (by `url_key`), else the article itself is collected (its URL, the outlet's own sid and
  name, e.g. scmp.com → 南華早報) with no publish time of the bot's (the page gives it); a site no source reads is
  listed on the monitor (Link following) and the bot's post dropped. A person's Reddit link post
  stays a Reddit post with its link in the text, so it joins the linked article (`posts.article_id`).
  Tests: `tests/test_mirror_posts.py`.
  **Results and run now (Deyao, by script):** `infra/control.py social STREAM` shows per stream each platform's
  last check, the run log with each run's cost, and the posts; `tikhub_fetch.py --run --now STREAM` runs every query
  of that stream on every platform now (one run at a time). Every run is logged in `$DATA_DIR/social_runs.json`.
  Accounts: YYC's own TikHub (chendeyao.uk@gmail.com, key `yyc-news-stream`, scoped to these route families; env
  `TIKHUB_API`) and YYC's own AnyAPI account (env `ANYAPI_API_KEY` in `/etc/yyc-stream/app.env`).
  **Cost** (list prices, 2026-10-13): TikHub USD 0.002 per search on YouTube, Threads and Instagram; 0.001 on TikTok,
  X and Reddit = USD 0.009 per query across the 6 platforms. AnyAPI USD 0.03 per Facebook search (up to 20
  posts; when its first source fails, the backup costs up to USD 0.0661). 22 queries every 3 hours = USD 1.58 a day at
  list price (no Facebook search since 2026-10-28).
  **Safety stop:** when TikHub or AnyAPI refuses for its balance (HTTP 402 "Insufficient balance" / credits), kv
  `social_limit` records it per provider, the monitor shows a red line, Deyao gets one Discord message per episode
  (`notify.py`), that provider's other queries are not sent in that run (their last posts stay), and the first search it
  answers clears it.
  One Galbot button run = USD 0.255 (up to 0.363). TikHub's search endpoints refuse its daily free credit, so nothing
  comes in until the account has a paid balance.
- **TV and radio** (`broadcast_fetch.py`, CronJob `broadcast` hourly at :20; OFF with transcription since 2026-10-20, Deyao: cost, ARCHITECTURE.md "Feature flags"): news programmes as text, one corpus row
  per episode (source type "Broadcast", website = the broadcaster, board = the programme; a transcript over 15,000
  characters is split into parts at `url#t=<seconds>`). Audio only, transcribed by the same model, prompt and settings
  as the articles' clips (`media.py`, below) and filed with a [mm:ss] mark about once a minute. RTHK: Radio 1's
  晨早/午間/傍晚/晚間新聞天地 (archive file of each aired day, per RTHK's timetable), 千禧年代, 自由風自由PHONE, TV 星期六問責,
  新聞天地, 鏗鏘集 (programme pages' episodes). TVB 六點半新聞報道 and 午間新聞, 有線 六點新聞報道 and 午間香港新聞, Now 經緯線
  and 時事全方位: from the broadcasters' own YouTube channels, audio only through YYC's IPRoyal ISP IP
  (`media.youtube_clip_audio`, below: YouTube refuses datacenter IPs; ~10 MB per half-hour bulletin), never yycstream. 新城財經台 (FM 104): the archive's half-hour MP3s of its news programmes. 商業電台
  881903: its archive plays only for a signed-in 881903 account, so its news programmes (新聞專輯, 在晴朗的一天出發,
  經濟脈搏, 政經星期六, 政好星期天) are filed from the archive's section titles and summaries, marked 「節目摘要（非逐字稿）」.
  An episode already in the corpus is skipped; a failed one is retried on the next runs, 3 times at most; no episode
  starts after `BC_RUN_SECONDS` (2,700 s, inside the job's 3,300 s deadline), the rest wait for the next run.
  `broadcast_fetch.py list` shows what the next run would transcribe; `broadcast_fetch.py test <url>` prints the
  transcript of one URL.
- **Video and audio in articles and posts (Deyao, 2026-10-14; `media.py`, Deployment `transcribe`):** transcription by the model is OFF since 2026-10-20 (flag `transcription`, Deyao: cost): queued clips wait, YouTube clips get only their own subtitles (`media.captions_one`), ARCHITECTURE.md "Feature flags". every clip an
  article or post carries is transcribed once and its transcript joins the post: its search text (搜尋, 數據分析's text
  box), the scorer's input (after the text, `streams.TRANSCRIPT_CHARS`; the post is scored again when it lands:
  `streams.HASH` covers it) and the item page's 「影片逐字稿」 (the text for approved accounts, like the full text).
  - **Detection, per host, from the page's HTML** (rules and saved real pages: `tests/test_media.py`,
    `tests/fixtures/media/`): TVB (`__NUXT_DATA__` video_url / vod_video_id of the post's own article; geo-restricted
    clips have no URL outside Hong Kong: status no_direct unless a `MEDIA_ROUTES` "tvb" route is set), Now
    (videoStatus 2 → `news-videos.now.com/nownews/<id>/hls/<id>.m3u8`), 有線 (data-url m3u8; 404s, 429s and a bad TLS
    certificate at vod.i-cable.com), RTHK (the article's own newsstatic videos/*.mp4 and audios/*.mp3), 星島 (ld+json
    contentUrl, video.hkhl.hk), 點新聞 / 大公文匯網 / 文匯網 (the article's storyResource on dams.dotdotnews.com), 香港01 (the
    article's own video: vdo-cdn-static m3u8 or YouTube), 商報 (img.hkcd.com/video mp4), 紫荊 (res.bauhk.com MEDIA/VIDEO
    mp4), The Standard (the article object's video_path), 政府新聞網 (the article's own videos/*.mp4), 鳳凰網 (the page's
    own VideoObject), on.cc (the article's vid → video-cdn.on.cc m3u8), 巴士的報 (video.js mp4 in the body), and on
    every page YouTube embeds (SCMP's body, 巴士的報, am730…; through the ISP IP, below), and Facebook video
    plugins (am730), read by yt-dlp from the server. HKET's player carries its programmes
    (美股FocUS…) on unrelated articles: each programme video becomes a post of its own (`video.hket.com/video/<id>`,
    sid `hket_video`). Text-to-speech readouts of the article are skipped (SCMP articleSpeeches, 文匯 text2Audio,
    巴士的報 article-audio). Related articles' videos on a page are not the article's.
  - **Where the HTML comes from:** the full-text readers (`fulltext_now.py`, `fulltext_fetch.py`) hand over each page
    they read (`cache_fetch.last_page()` → `media.seen_page`); posts whose page they never read (the feed already had
    the text: TVB, Now, RTHK…) are read by the transcriber's scan, 5 minutes after they arrive, one request per host
    at a time, 1 s apart, posts published in the last `SCAN_DAYS` (2) days. Each post is looked at once (`media_scan`).
  - **Queue** (tables `media`, `post_media`): one row per clip, keyed by the provider's clip id (tvb:, now:, dams:,
    hkhl:, hk01:, yt:, fb:, oncc:, hket:) or else the media URL without its query, so a clip on many articles is
    transcribed once and linked to all of them. Newest post first; 3 failures give a clip up; a 429 / 5xx from the
    media host waits 15 minutes and does not count (for 2 days). `python media.py stats` shows the queue.
  - **YouTube through the ISP IP (Deyao, 2026-10-16):** the transcriber's YouTube audio used 5.19 GB of IPRoyal
    yycstream (paid per GB) in 6 hours, and YouTube refuses the server's IP; so every YouTube request (the
    transcriber's clips, the broadcast job's bulletins) goes through YYC's IPRoyal ISP IP 81.181.135.61 (order
    #85121454, USD 6.76 per 30 days, auto-renew on; Fair Usage allowance 100 GB per 30 days, beyond which the IP keeps
    working at reduced speed), never yycstream. Config: env `MEDIA_ROUTES` = `{"youtube": "ISP_PROXY"}` (family → the
    env var holding the proxy URL) and `ISP_PROXY` from Secret `isp-proxy` (`deploy/apps/sealed/isp-proxy.yaml`;
    Jarvis 2 store yyc `YYC_ISP_PROXY`). The ISP IP serves one client at a time (a second answers "Tunnel connection
    failed: 403"), so every YouTube fetch holds `media.youtube_lock` (a process lock plus the Postgres advisory lock
    'yyc youtube'); the model's work runs outside it. One locked session per clip: yt-dlp `-J`, the subtitles, the
    audio-only download reusing the metadata (`--load-info-json`; a 403 is tried again, 3 tries), its bytes measured
    on the wire (`resi.metered_url`) into kv `isp:bytes:<day>` / `isp:jobbytes:<job>:<hour>` (`resi.count_isp`; the
    monitor's IPRoyal card shows today and the last 30 days against the allowance: IPRoyal's API reports no traffic for
    ISP orders). Every YouTube clip is transcribed by the model (「AI 轉錄」; Deyao: "run the transcription for all");
    its own subtitles are kept beside it (`media.captions`, `captions_model`) and shown under the transcript: the
    uploader's in the video's language (「YouTube 上載者字幕（lang）」), else YouTube's auto-generated track of the
    video's language (its `<lang>-orig` track: the auto-translated ones answer 429; 「YouTube 自動字幕（lang）」). A live
    or upcoming video waits like a busy host; a private or removed one is given up at once.
  - **No audio through yycstream:** every other clip (TikTok, Instagram, Facebook, the news sites' video, RTHK /
    新城 broadcasts) is transcribed only when its audio downloads directly from the server. A clip yt-dlp can't read
    from the server's IP, a TVB geo-restricted clip and an HLS stream answering only Hong Kong IPs (kind `proxy`) are
    status `no_direct` (「無法直接下載，未轉錄」), not retried, unless `MEDIA_ROUTES` names a route for that family
    (`tvb`, `hls`, or the site: `facebook`, `instagram`, `tiktok`).
  - **Transcription:** audio only (ffmpeg → 16 kHz mono; MP3 64 kbit/s pieces), cut at the quietest point between
    18 and 29 s (OpenRouter's Qwen3-ASR provider cuts anything longer every 30 s wherever that falls, mid-word: live,
    "聯儲局發表。 反映經濟情況嘅學皮書"), each piece sent to **Qwen3-ASR-1.7B** on OpenRouter (key yyc-stream; Deyao, 2026-10-15: one cheap
    model), 4 pieces at a time. It is a speech-to-text model on OpenRouter's `/audio/transcriptions` (the MP3,
    temperature 0, no prompt: the endpoint takes none; the model detects the language). Its answer has stray Simplified
    characters (消委会), so it goes through OpenCC s2hk. The transcript is one line per piece (about every 25 s), `[mm:ss] text`, in Hong
    Kong Traditional characters with spoken Cantonese as spoken; a piece whose answer runs past 15 characters a second
    is asked again twice, then stands as 「（此段未能轉寫）」. The model is one setting: env `TRANSCRIBE_MODEL` (a chat
    model with audio input goes through /chat/completions with the benchmark's prompt "verbatim").
    Benchmark of 14 Oct (10 HK news clips): Qwen3-ASR-1.7B CER 6.22 %, 59/71 names, USD 0.00045 per audio minute as
    billed (OpenRouter's price: USD 0.0000075 per second). Transcripts made by the model used until 15 Oct are kept. Spend: ai_spend / ai_runs purpose
    `transcribe` (OpenRouter's charged cost per call), on the monitor.
  - **Labelled as AI's (Deyao, 2026-10-15):** on the item page every model-made transcript carries 「AI 轉錄」 (the
    「AI 標題」 badge's style; the model and time on hover); a YouTube clip's own subtitles, shown under it, keep
    「YouTube 上載者字幕」 / 「YouTube 自動字幕」 and no AI label. A broadcast episode (whose text is the transcript) shows
    內文來源 「AI 轉錄（<model>）」 (posts.text_via `asr:<model>`).
  - **The residential proxy out of traffic (live 2026-10-15: IPRoyal answered 402, and 107 YouTube / Facebook / TVB
    clips were given up in 35 minutes):** for a configured route, a pause (`media.ProxyOut`, a `resi.ProxyOut`; curl's 'CONNECT tunnel
    failed, response 402' from yt-dlp's TikTok reads too): the clip waits 15 minutes and tries again, no try counted,
    however old it is, until the proxy has traffic again.
  - **No budget logic:** no caps on minutes or clips. When OpenRouter refuses for credit or the key's limit, ai.py's
    safety stop applies (`ai.limit_hit`: kv `openrouter_limit`, the monitor, one Discord message through notify.py);
    the clip stays queued with no try counted, the transcriber tries again every 10 minutes, and the first call that
    works clears it.
  - **The Deployment** (`deploy/apps/transcribe.yaml`): one replica when on, a rolling update with maxUnavailable 0. Every
    copy transcribes, each clip claimed in the database (`media.claim`: columns `claimed_by`/`claimed_until`, a lease
    renewed while the clip is in hand, so two copies never take one clip and a killed copy's clip is queued again once
    its lease lapses): the new pod works at once while the old one, on SIGTERM, finishes its clips in hand (grace 2 h,
    clips run up to hours). The page scan runs in one copy at a time (Postgres lock `yyc transcribe`, `podlock.py`).
  - **YouTube posts (Deyao, 2026-10-14):** every YouTube post (the YouTube sources and channels; broadcast episodes
    excepted, they are text already) is a clip of its own, `yt:<id>`, the same key as an article's embed of it, queued
    without reading a page. Its uploader's own captions in the video's language are taken when they exist (model
    `youtube-captions:<lang>`, no cost; shown as 「YouTube 上載者字幕」); auto-generated captions never (quality first:
    the chosen model transcribes instead); a video being streamed live or not yet aired waits.
  - **TikTok videos (Deyao, 2026-10-15: "tiktok needs transcript too"):** TikHub search and the watched accounts attach
    each video post's clip when they file it (corpus.attach_media, no page read): `tt:<aweme id>`, kind `ytdlp` (yt-dlp
    from the post's page with no proxy: TikTok's CDN link in the answer expires within a day; the page doesn't; a photo
    post has none). No TikHub request is added. Instagram, Threads and Facebook posts bring their videos the same way
    (`tikhub_fetch.meta_media`, `fb_fetch.media`; Threads keyword search too).
  - **Waiting for a transcript:** `media.pending(post_ids)` → the posts with a clip still queued (post_media → media
    status 'queued'); a done or given-up clip ('done' / 'failed') is no longer pending. Social clips are filed in
    the same ingest as their post; an article's clips only after its page is read (the full-text readers, or the
    scan 5 minutes after it arrives).
  - **Not covered yet:** posts published before the scan window (the history), and text inside video frames and
    images (a separate task).
- **Pictures scored for meaning (Deyao, 2026-10-14; `images.py`, Deployment `images`):** OFF since 2026-10-20 (flag `pictures`, Deyao: cost; `images` at 0 replicas, the scorer text-only), ARCHITECTURE.md "Feature flags". every picture on a post (the
  lead image, the story's own pictures, a YouTube video's thumbnail, which counts as a photo) gets a 0–100 score from a
  vision model for whether it carries information, a one-line reason (Traditional Chinese) and the text read from it.
  Meaningful (50+): text in the picture, infographics, charts, tables, maps, screenshots of statements or documents,
  posters, slides, evidence photos that matter. Not: stock and decorative pictures, logos, building exteriors,
  headshots of the people in the story or interviewees, generic crowd / press-conference shots.
  - **Threshold:** settings `image_threshold` (default 50): `infra/control.py set image_threshold N` (redoes every
    post's picture text at once).
  - **Where pictures come from:** the pages the full-text readers and the transcriber's scan read
    (`media.seen_page` passes the HTML on: og:image / ld+json image, then the `<img>` of the story's own block, the
    parent of its largest paragraph cluster; site icons, SVG/GIF, logos and share buttons left out); posts nobody read a
    page of (News, Official, Blog, Video; the last `SCAN_DAYS` = 7 days, 串流's items first) are read by the images
    worker, one request per site at a time. A JavaScript-only HKET page (ps./invest.hket.com) is read through its
    content API (`cache_fetch.hket_api`): the JSON's thumbnail is the lead, the `<img>` in its HTML the body (read as
    HTML, its escaped quotes had glued `\"https:/static06…` onto the page's URL: HTTP 400, 2026-10-15). A Qiniu
    thumbnail URL (`…?imageMogr2/thumbnail/x320`, 橙新聞) is its picture without the query, and a URL that is the page
    itself (橙新聞's empty og:image) is never a picture.
  - **Once per picture:** table `images` keyed by the sha256 of its bytes (score, reason, text, model, at, size);
    `post_images` links posts to pictures; a picture under 120 px is not sent to the model. 串流's items first, then
    the newest posts; 3 tries give a picture up, counted when the work starts (a picture that kills the worker runs
    out of tries); an error no try can change (HTTP 4xx but 408/425/429, bytes that are not a picture) gives it up at
    once on every post carrying it, and a post that links a given-up URL later starts given up. OpenRouter's credit
    refusal is ai.py's safety stop, not a try. `images.py repair` dropped the rows the two extraction bugs saved.
  - **Memory (2026-10-15: OOMKilled 5 times at 768Mi):** a newspaper photo can be 9504x6336 (60 megapixels); decoding
    it in full took 460 MB, six workers at once 2.2 GB. Now a JPEG decodes at the draft scale (1/2–1/8, never below
    the 1024 px sent on), and every decode is held inside `DECODE_BUDGET` (256 MB of decoded bytes across the workers,
    each picture's cost from measured peaks: a progressive JPEG keeps ~3 bytes a pixel of coefficients at full size,
    WebP ~17 bytes a pixel, RGBA PNGs a premultiplied copy). glibc's dynamic mmap threshold kept the freed picture
    buffers in fragmented heap (the live pod sat at 314 MB between pictures), so the Deployment fixes it at 1 MB
    (`MALLOC_MMAP_THRESHOLD_`). Measured on 298 real pictures (the 40 largest of the live table + 258 from the last
    day), 6 threads, twice each: peak above the start 2,170 MB before, 240 MB after, back to +12 MB at the end
    (`infra/picture-memory.py`). Limit: `deploy/apps/images.yaml`.
  - **What they feed:** `posts.image_text` (the meaningful pictures' text) joins the post's search text and the
    scorer's input (`streams.HASH` covers it, so the post is scored again), and a multimodal scorer
    gets each post's meaningful pictures attached (`images.for_scoring`: up to 2 a post, 768 px).
  - **Item page:** 「圖片」: the meaningful pictures (reason on hover, the text read from each folded under 圖中文字 for
    approved accounts), the rest folded under 「其他圖片（N）」 (the edge app's ItemPage).
  - **Model:** env `IMAGE_MODEL`, unset: no model is chosen and the worker refuses to start without one. The
    2026-10-15 run on 95 hand-labelled real pictures (`tests/fixtures/images/labels.json`: 21 meaningful, 74 not, 7
    left out as ambiguous; meaningful = score >= 50) covered only closed-weight models, which OpenRouter refuses on
    YYC's account. Before pictures go back on, an open-weight model with image input is chosen on the same labelled set.
    Spend: ai_spend purpose `images`, on the monitor's "Pictures scored for meaning" card. No budget logic.
- **Open gaps:** Facebook search for streams other than GALBOT (only followed pages, `fb_fetch.py`).
  LinkedIn (job search only), Telegram (search only inside one channel).
- **Original sources, not re-posts (Deyao, 2026-10-12):** many K-Matrix alerts link to media outlets' Facebook
  posts, which re-post their own website stories. YYC reads the publishers directly instead (a shorter supply chain,
  earlier and with the real article URL), so Facebook is not watched.
- **Titles for posts with none (Deyao, 2026-10-14; `titles.py`):** the collectors put the start of a social post's text
  in its title (Facebook, Instagram, Threads, Telegram, X, TikTok…). Such a post gets a title from
  the stage-1 model, `ai.MODEL` (YYC's key, ai_spend purpose `titles`, its own call per 10 posts; not behind the user-AI flag), stored in
  `posts.ai_title` with its kind, model, time and the content hash it was made from; the collected title and text
  stay as they are. Kind `extracted`: the post opens with its own headline (RTHK即時新聞's Telegram post
  "李家超：孫中山愛國思想激勵中華兒女為國家發展不懈奮鬥" then the body; 【…】 headlines on outlets' Facebook pages),
  surfaced word for word (checked against the post), shown with no mark, the body shown without it. Kind `written`:
  no headline in the post, so the model writes a short factual one in the post's language (Chinese posts in
  Traditional Chinese, HK usage; `titles.PROMPT` is the same general instruction as stage 2's), marked everywhere: 「AI 標題」 badge on the site
  (串流, item page, 數據分析, 搜尋, the React app; model and time on hover, the post's own text under it or on
  expanding) and 〔AI標題〕 before it on Telegram and in the Excel exports. The live scorer titles each batch before
  filing it (so the stream item and its Telegram message carry it); a poller thread titles the rest of the last 7
  days and every post in 串流, and again when a post's text changes. 搜尋 searches the AI title too.
  `python3 titles.py status | once N | recheck [--dry]`. Measured: USD 0.000168 per post (30 real posts, 14 Oct 2026).
  Kind `linked` (Deyao, 2026-10-15): a post whose own text is only a link carries the linked page's title (below),
  with no model call, marked 「連結標題」 on the site and 〔連結標題〕 on Telegram and in the exports.
  Kind `retitled` (Deyao, 2026-10-20: RTHK's YouTube 「1100 新聞簡報」, true but meaningless): a post whose own title
  does not say what it is about. The model that reads the post judges it and writes the title (stage-2
  scoring's call; it files it with `titles.store(p, title, "retitled", model)`; no separate title call); shown as a
  written one (「AI 標題」, 〔AI標題〕), and the post's own title is kept and shown as the source's title: 來源標題 under
  the item page's headline and in the badge's hover (`title_retitled`, `original_title`; tests/test_retitled.py).
- **Posts that are only a link (Deyao, 2026-10-15; `links.py`):** an X post by 伊凡投資心得 reached HKIC with the
  title "https://t.co/3DS8gH0wK7" and no text: an X Article whose title and text TikHub gives and the collectors
  dropped. Now **X** (search, watched accounts, the tweet detail; one parser, `tikhub_fetch.x_tweet`): the tweet's full
  text with t.co links expanded; an X Article is its own post: the article's title is the post's title and its body
  the text (its full text read once through the tweet detail, USD 0.001, kv `x_article:<id>`; no AI title, it has
  its own: `titles.own_title`), the quoted tweet as 〔引用 name @handle〕text, a retweet's original as
  〔轉推 …〕, photos to the picture scorer (`images.link`), videos (best mp4) to the transcription queue
  (`media.file_clips`, key `x:<media id>`), counts incl. quotes and bookmarks, author name and handle. **Every
  platform:** a post whose text is a link and fewer than 12 other characters (Threads posts that are an am730 link,
  Facebook posts that are a YouTube link, an outlet's X post that is its article's URL) gets the linked page's
  og:title / og:description appended as 〔連結內容：title〕description (YouTube by oEmbed, its video queued for
  transcription; an X post's own t.co / X Article link by the tweet detail), by a thread in the poller within seconds;
  a failed read is tried again 2 min, 10 min, 30 min, 1 h, 3 h later, then left (table `link_scan`). Hashtags and
  @mentions are not words there ("https://… #晴報健康 #健康" is only a link). **The scorer holds** a post whose content
  is only URLs, hashtags and @mentions (`links.HELD_SQL` in `streams.due`, `unit_due`, `backfill_due`) until it is
  resolved; once links.py gives up, the near-empty gate below rejects it (it used to stay held, unseen, for ever). `python3 links.py status | once N | backfill-x [DAYS]` (backfill-x reads
  every X post of the last DAYS days again through the tweet detail, USD 0.001 each). Tests: `tests/test_link_posts.py`
  (saved real TikHub answers and pages in `tests/fixtures/x/`).
- **Link following (Deyao, 2026-10-20; `follow.py`, a poller thread):** a post that reaches a stream (scores at or
  above a stream's threshold after stage 2, in stage `follow`'s work ("Backlogs"), not a comment; any source) has every
  link it carries collected: the URLs in its title and text (where every collector keeps a link post's target:
  Reddit's outside link, Facebook's shared link, X's t.co (a t.co X doesn't expand is kept, only the tweet's own
  photo/video link is dropped), Threads' link preview, every href of a Telegram message), and for a news post the
  links in its article body (one more read of its page, the collectors' route; not for solver, browser or API-read
  hosts). Each link is unwrapped (Facebook l.php, Google /url, l.instagram.com, l.threads.com …) and expanded when it
  is on a shortener (t.co, bit.ly, fb.me, lnkd.in … `follow.SHORTENERS`: hop by hop with redirects off, a 200
  interstitial's target read, the destination never requested), cleaned (no fragment or tracking parameters), then:
  **skipped** when it isn't an article (front page, section/tag page, picture/audio/video/file, share button, a social
  platform's page; counted per reason and site), a **duplicate** when the corpus has it (url / url_key, the existing
  dedupe), **new** when a source of ours reads its site (the site of a watched source's feed or posts): `corpus.ingest`
  as a post of that outlet, read in full at once by `fulltext_now.fetch` (the 30-minute job for its hosts), titled with
  the page's headline, then scored and shown like any post; else **unknown**: listed on the monitor with the site, its
  link count and an example post, and taken again once a source reads that site. One hop: a collected article's own
  links are followed only when it reaches a stream itself; comments' links never. A shortener that fails is tried 4
  times (5 min, 30 min, 3 h). Reddit's mirror bots use the same routing. The item page lists the post's links
  (連結: resolved, with the outlet and our copy, once followed; as they stand before). Tables `follow_scan`,
  `follow_links`; monitor card Link following; `python3 follow.py status | once | route URL… | fix-skipped`. The
  catalogue's sites (sites.json) count as sources, each feed's registrable domain too (hk.crntt.com's feed reads
  www.crntt.com). The first list (20 Oct 2026, Deyao: "let's add 'Sites no source reads (7)'") became sources in
  sites.json: 中評社 (RSS), RobotBelt (RSS), 港人講地 (home, news, opinions listings), Taiwan News 中文 (regex on its
  home page), 堅料網 (`regex` with a `link` template over its page data, `impersonate`: Chrome's TLS fingerprint past
  Cloudflare), hongkong-invest.com (the catalogue job's WordPress REST, ISP route); get.surfshark.net is an advert
  (skip reason `ad`). The second list: HKG報 (listings) and Thomasnet Insights (`/insights/?page=1`, impersonate: the
  bare /insights/ is behind DataDome) are sources; chinadailyasia.com (China Daily HK's old domain) and
  www.hkcd.com.hk (canonical.REHOST) are read as the watched site's address (`follow.alias`); Google's /url and AMP
  viewer are unwrapped and its own pages skipped (`google`, the "preferred source" button `share`); app-store and
  app-install links (onelink.to …) are skipped as `app`. A post collected before its headline was known (〔連結〕host) takes the headline its source's
  listing gives (corpus.ingest). **The generic reader (2026-10-21):** a link to a site no source reads is no longer
  just listed: its page is read once (`follow.generic`, cache_fetch.get) and becomes a post of sid `link_<site>`
  (name: the page's og:site_name, else the domain; type Official on a government host, else Blog; region from the
  country domain, else Global: `corpus.link_meta`; never first_run) when it is an article: at least 200 letters of
  article text and no sign of a form (Google Forms data, 5+ text inputs), a shop (Product data, 加入購物車) or a
  document/form service (Google Docs/Drive, Microsoft Forms, Dropbox … `follow.DOC_SERVICES`). Anything else stays
  listed with its reason (`no article text`, `form`, `shop page`, `document or form service`) for Deyao to decide
  (Deyao, 2026-10-21: "if some sources don't fit … don't force it, bring it up to me and let me decide"); nothing is
  ignored without his word. `follow.HOLD` keeps a site listed unread while he decides. What he decides to ignore is
  `follow.IGNORE_HOSTS` / `IGNORE_PAGES` (skip reason "ignored by Deyao"; 21 Oct: "ignore all sites on the table":
  Google Drive/Docs, Microsoft Forms, gbb.shop, mingtakfn.com, geosirius.ifz.ru, familyandwomensummit.hk, the ECSS
  2026 home and candidate-table pages, 經民聯's WeChat page). The third list (14 sites, 21 Oct): The Straits Times East Asia (its Google News sitemap) and the ECSS 2026
  press releases page of elections.gov.hk (a listing of its info.gov.hk links, `"outlet": false` in sites.json: its
  own host's pages are not its articles, so links to elections.gov.hk go to the generic reader) are sources; a
  generic post takes the page's one <h1> as title when the title lacks it, and region HK when its text is Chinese
  (not .cn); a generic post is never first_run; qr.page (→ qrcodes.pro's
  redirect_link) and reurl.cc's hidden `target` are expanded. Tests: `tests/test_follow.py`,
  `tests/test_mirror_posts.py`.
- **Near-empty posts (Deyao, 2026-10-15: "our scrape comes up with almost zero data (e.g. just a few hashtag or a
  url) and the scorer should reject it"; `thin.py`):** what a post says is counted in units: title and text (the
  title once when the text repeats it), transcripts, picture text and, for a post scored with its comments, those
  comments, after taking out URLs, #hashtags, @mentions, emoji, punctuation, the collectors' placeholders
  ("(post without text)", 〔連結〕host) and [mm:ss] marks; a CJK character is one unit, a Latin/digit word one unit. A
  meaningful picture (images.py, score at or above its threshold) is content. A post under **4 units** is not scored
  while content may still come (a clip queued for transcription, pictures not read yet or a page images.py / media.py
  will still scan, a link not read yet or being retried), looked at every 5 min and at once when its content changes,
  for at most **8 h** from when it was seen; then, or as soon as nothing more can come, it is **rejected**: no model
  call, never filed in 串流 or sent to Telegram, and the item page says 「內容不足，未評分」 with the count and what it
  waited for; 數據分析's post lists (帖子, 人物分析) mark it the same. If content still arrives later (a late
  transcript), its content hash changes and it is judged again. Comments and replies are not gated (scored with their
  thread's headline). Filed items stay filed. Table `thin_posts`; `python3 thin.py stats [DAYS] | show ID`.
  **Evidence** (production, posts of 8–15 Oct, 46,108 non-comment posts, title + text): under 4 units 281 posts
  (0.61 %; 203 of them were scored, 7 filed: GALBOT/GALBOT_HK AnyAPI results titled 「Haha」, 「黃錦中」, 「Garett
  Lyu」 (the author's name standing in for an empty text), 「Miss galbot🤣🤣」, 「Sungit ng tindero🤖」; CEHK 「李家超🤣」;
  HKIC 「#tiktoklive #livehighlights」): TikTok 171 (hashtag-only captions), Facebook 67, Instagram 15, AnyAPI
  Facebook 12, other social 9, LIHKG 4 (scored with their replies, so not rejected), news 3 (two Reuters podcast pages
  whose content is audio, 「Diesel diplomacy」 / 「Cashing in chips」, and a 巴士的報 post titled 「Access
  Verification」: a bot wall). At 4–5 units real headlines start (「關於肺鼠疫」, 「睇樓速遞」, 「香港又贏」, "Financial
  imbalances and AI boom"); under 5 would reject 52 more, under 3 would let 40 interjections through (Haha, 好長情,
  開飯嘞). Arrival times behind the 8 h (posts seen 15 Oct 07:00–14:00 HKT): picture text p95 5.3 h / p99 7.0 h
  (images.py's backlog), transcripts p95 3.1 h / p99 4.1 h, a link's last retry 4.7 h after its first.
  Tests: `tests/test_thin_posts.py`.
- **No title is a URL (Deyao, 2026-10-15; `corpus.tidy_title`, applied at ingest and in `corpus.set_content`):** the
  links at a title's start and end are dropped; a title that is only links is 〔連結〕<host> until `links.py` has read
  the page, then the linked page's title (the post's title column too, not only the AI title). The link stays in the
  text, which is what holds the post from scoring. `python3 links.py fix-titles` corrected the posts stored before
  (X posts read again, resolved posts retitled from their 〔連結內容〕 block, comments given their parent's title,
  stream items' stored titles; Telegram messages are never edited) and prints before/after counts. Tests:
  `tests/test_url_titles.py`.
- **No title is a site's name (Deyao, 2026-10-20: an HKGolden post titled 「香港高登討論區」 and tagged 新聞;
  `corpus.site_named`):** a title equal to the source's or website's name, a page's og:site_name / application-name,
  or a known name of the URL's site (`corpus.SITE_NAMES`; generic suffixes such as 電子版 ignored) says nothing about
  the post. At ingest (news, official, blog, forum) such a title is treated as missing: 〔連結〕<host>, the link in the
  text, held from scoring until `links.py` reads the page. `links.headline` never takes a site's name from a page:
  after og:title / twitter:title / <title> come the e-paper's founder-title (澳門日報), <h1> and a `.title` block
  (華僑報); a page that states only its site's name states no title. `fulltext_fetch.page_title` the same (巴士的報's
  video pages: og:title 「巴士的報」, so the slug title stays). **高登討論區 (HKGolden)** threads come in through Google
  News (the thread URL as the title); the thread page is a JavaScript shell whose HTML carries only the site's name, so
  `hkgolden.py` reads HKGolden's own API (the web app's `api.hkgolden.com/v1/view/<id>/1`, no login, answers the
  cluster's IP): title, first post, author, counts, time; `links.py` and `cache_fetch.py` use it. HKGolden is a forum
  (論壇, website 高登討論區): `corpus.FORUM_HOST` classifies forums by host whatever name the item carries. Measured
  2026-10-20 before the fix: 67 stored posts titled with a site's name (澳門日報電子版 45, 華僑報 8, 香港高登討論區 6,
  香討．香港 No.1 6, 巴士的報 2); `python3 links.py fix-site-titles` corrected them (HKGolden by the API, 巴士的報 from
  the slug, the rest from the page's headline) and their stream items' stored title and website (membership kept)
  and prints before/after counts. Tests: `tests/test_site_titles.py`.
- **Tagging:** every scored post gets its sentiment and labels from the scorer above (`ai.LABELS`, K-Matrix's full
  tag set) — one sentiment (😄 positive · 😐 neutral · 😣 negative · 🤔 mixed · 😏 irony) and labels (👑 repute ·
  🛍 goods · 🤝 service · ⚔ rival · ⚠ issue · 🚨 crisis · 👸 kol · 🔥 engaging · 📈 trend · ❓ ask · 📝 review · 😡 accuse ·
  🤩 praise · 🗣 rumor · ⏰ urgent · 💡 chance · 👀 monitor). Stream items are filed in the half-hour batch they were
  scored in, as K-Matrix posts them.
- `app.py` is the poller (`python app.py`: a poll started every 5 min) and, as `gunicorn app:application`, the AI
  server whose routes are in `web.py`. Every piece of state is in Postgres (`DATABASE_URL`, tables in `corpus.SCHEMA`): the stream's
  items (`stream_items`), the URLs already read (`poll_seen`), the last poll and source status (`poll_state`); the
  jobs' outputs that were JSON files are documents in `kv` (`corpus.file_get` / `file_put`, key `file:<name>`), and
  one job run at a time is a Postgres advisory lock (`corpus.lock`). Only built reports and browser profiles are files
  (`$DATA_DIR`). `migrate_to_postgres.py` moved the old server's SQLite corpus and JSON files in (once, counted).
  The connection pool (`corpus.pool`) tests each connection before handing it out: when Postgres restarts (an OOM
  kill, a failover) every open connection dies, and without the test each dead one answered a request 500 once
  (item pages, 15 Oct 2026; `tests/test_item_kinds.py`). The edge app reads the same database through
  Hyperdrive with a read-only role; what it needs from the Python side (streams, thresholds, flags, labels) the poller
  publishes into kv `edge:config` (`edge_config.py`), and `live.py` tells open pages what changed (Postgres triggers →
  NOTIFY → the edge app's LiveHub).

## 搜尋: the article search (`web-app/src/server/search/`; YYC's own WisersOne/WiseSearch)
- **Page** `/search` (public, its own section; layout and URL in NAVIGATION.md): the whole corpus by keyword in
  `query.py`'s language, filtered by date range, media type, outlet, region, titles only, with or without
  comment/reply rows, sorted newest, oldest or by relevance (a term in the headline 5, each occurrence in the text 1,
  at most 10 per term), 25 a page, counts per type and outlet. **Excel** at `/api/export/search.xlsx` (same
  parameters, at most 5,000 rows). JSON: `/api/search`, `/api/search/meta` (types and outlets with post counts).
- **Licensing:** results and the export carry the headline, a 160-character extract around the first match and the
  publisher's link; the full text is only searched.
- **Chinese search (Deyao, 2026-10-14; `searchtext.py`, `query.py`, `search_dict.txt`):** our own pipeline in
  Postgres, no search engine and no extension (the CloudNativePG image has pg_trgm but not pg_bigm, zhparser or pg_jieba). Each post has a hidden search copy; the displayed title and text never change.
  - **Normalised text** (`posts.hay`, `posts.stitle` for the title): NFKC (full-width → half-width), OpenCC `hk2s`
    (Hong Kong Traditional → Simplified, so 裏/裡/里, 綫/線, 衞/衛, 後/后 merge), lower case.
  - **Words** (`posts.words`, GIN): jieba (accurate mode, HMM off) over that text with jieba's dictionary +
    `search_dict.txt` (HK officials, places, organisations, Cantonese written words, the streams' entities; kept in
    Traditional, converted on load) + the streams' Chinese one-word search queries, stored as a tsvector literal built
    in Python (lexeme:position, title words weight A; a long word also indexes the dictionary words inside it at its
    position), so no Postgres parser or text search configuration is involved ('simple' semantics). English words are lower case and not stemmed: the substring
    fallback already finds "elections" for "election", the query language is K-Matrix's exact terms, and the Worker
    needs no stemmer.
  - **Substring fallback** (`posts.grams`, GIN): every 2-character sequence of the normalised text, plus its last
    character, for names and new terms the dictionary cuts differently (如何君堯 is 如何|君|堯, so 何君堯 is not a
    word there) and LIHKG Cantonese; one-character terms (後, 咗, 嘅) are looked up by prefix (`'c':*`).
  - **Queries** go through the same normalisation and segmentation: a term matches by words (`words @@ 'a' <-> 'b'`,
    a phrase) or as a substring of `hay` (`strpos()`, narrowed by `grams`); NEARBY works on the normalised text.
    **Ranking:** posts the query matches by words come first, then substring-only matches (`query.rank_sql`), in 搜尋
    (each result says `match: word|substring`, and the page marks where the substring-only ones start), in
    數據分析's 帖子 list (`insights.select`) and in `corpus.match_ids`; within each group the chosen sort applies.
  - **History:** `posts.search_v` = the `searchtext.VERSION` a row was indexed with (a hash of the dictionaries and
    rules). `corpus.reindex()` rewrites every row with another version in 500-row transactions, skipping rows another
    writer holds, with a pause between batches; the poller runs it in the background at every start (`corpus.py
    reindex` by hand). So editing `search_dict.txt` re-indexes everything on the next deploy. Until a row is reached it
    is still found by the term as typed (the old hay).
  - **The edge app** (`web-app/src/server/search/`, a port of search.py, query.py and searchtext.py) cuts and
    normalises a query with the dictionary and OpenCC data the Python side publishes (`searchtext.publish_edge`: table
    `search_words`, kv `search:edge`, `search:opencc`; the poller republishes when `searchtext.VERSION`, jieba, OpenCC
    or Python's Unicode data change), so query and index cuts never drift apart (`tests/edge_parity.py searchtext`,
    `tests/test_search_edge.py`). The SQL is plain Postgres through Hyperdrive.
  - **Measured live (2026-10-14, 184.6k posts):** the first backfill took 12 minutes beside the live pipeline; the
    indexes are 43 MB (words) and 184 MB (grams). 選舉 = 选举 = 658 posts (the old substring search: 617 and 20),
    後 = 后 = 11,510 (10,949 and 370), 裡面 = 裏面 = 里面 = 131 (61, 67 and 2). 搜尋 (Python search.run, warm process):
    11–280 ms for names, words, phrases, Cantonese (咗: 7,509 posts, 172 ms) and NEARBY; 後 (19,591 posts) 1.1–1.6 s.
    A process's first query also loads jieba (1 s). Each search has a 20 s limit.

## Backfill: older articles from the outlets' own archives (`backfill.py`, CronJob `backfill`)

The live sources only bring in what is new; `backfill.py` reads each outlet's public archive backwards in time and
writes those articles into the corpus (posts only: no stream items, tags or alerts), so 搜尋 reaches back before the
stream started. Rows carry the live source's sid and source name (so they show under the same outlet), the article's
own publish time, and `seen` = that time (that is how a backfilled row is told apart from a live one). Articles whose
archive gives only a headline get their text from the full-text job.

| Outlet | Archive route | Back to | Per day |
|---|---|---|---|
| 東方日報 | `orientaldaily.on.cc/section/sitemap/YYYYMMDD` (the day's paper) | 2012 and earlier | ~200 |
| 明報 (daily paper) | `news.mingpao.com/pns/x/section/YYYYMMDD/s000NN`, 11 sections (Cloudflare lets only a Chrome TLS fingerprint through) | 2023-10-06 | ~130 |
| RTHK 香港電台 | `news.rthk.hk/rthk/ch/news-archive.htm?archive_year=…` | 1 year, rolling | ~100-130 |
| 大公報, 香港文匯報 | `www.tkww.hk/epaper/sitemap/{tkp,wwp}/YYYYMMDD/news.xml` | 2020 and earlier | ~120 each |
| 政府新聞公報 | `www.info.gov.hk/gia/general/YYYYMM/DDc.htm` | 1998 | ~30 |
| 信報 (daily paper) | `www1.hkej.com/dailynews/<section>?date=YYYY-MM-DD`, through `RESI_PROXY` | ~3 months, rolling | ~110 |
| 南華早報 | `www.scmp.com/sitemap/archives/articles/YYYY_mon.xml`, then each page (title, time, text) | 1991 | ~110 |
| 晴報 | `skypost.hk/store/prod/res/sitemap/sitemap_YYYYMM.xml`, then each page | 2016 | ~2 |
| 香港01 | `web-data.api.hk01.com/v2/articles/<id>`, walking ids down (title, time, full text) | ~2025-12 | ~300 |
| 星島頭條 | `www.stheadline.com/realtime-news/<id>` → the article page, walking ids down | 2024 and earlier | ~650 |
| TVB 無綫新聞 | `news.tvb.com/tc/<id>` → the article page (ids below ~1,133,000 are 2012 items) | ~2025-11 | ~200 |
| NOW 新聞 | `news.now.com/home/local/player?newsId=<id>` (text included) | 2021 and earlier | ~120 |
| am730 | its section listing's own cursor (POST to the section page) | ~3 months, rolling | ~180 |

No archive route: 香港經濟日報 (its article pages are rendered in the browser; the HTML holds no title or text),
明報 instant news, 都市日報 (its sitemaps cover the last 3 months, the same as the live feed).

- **Window:** by default the last 365 days (`DEFAULT_DAYS`), never the last 31 days (`RECENT_GAP`), and never past the
  outlet's first live row. Backfilled rows are for 搜尋; the scorer only scores posts published in the 2 days before
  it reads them, so they join no stream. `--since/--until` override it.
- **Resumable:** each outlet's position (day, month and its remaining pages, or article id) is kept in the corpus kv
  `backfill:<outlet>`; a run stops at `--max` requests per outlet or after `--minutes`, and the next run carries on.
  An id walk first finds the id of the window's last day (galloping down, then bisecting).
- **Polite:** one request per second per outlet, a Chrome TLS fingerprint (curl_cffi), outlets in parallel.
- **Runs:** CronJob `backfill` (hourly at :25, 50 minutes, 3000 requests per outlet). By hand:
  `python backfill.py OUTLET --since 2026-01-01`, `python backfill.py all --days 365`, `python backfill.py status`,
  `python backfill.py OUTLET --test DAY|YYYY-MM|ID` (prints what one unit gives, writes nothing).

## Dashboard and profiles (`corpus.py`, `query.py`, `insights.py`; the pages: `web-app/`)
- **Corpus:** every post any collector fetches (not only stream matches) goes into Postgres (`corpus.py`, table `posts`):
  title, text, author, website, board, platform, source type (News, Forum, Social Media, Video, Blog, Official),
  region (HK, Macau, Mainland, Taiwan, Global), engagement counts. `cache_fetch.py` (`yyc-cache`, 30 min) keeps the article text of
  the streams' posts (shown to approved accounts on the post's item page `/item/<id>`; `/post/<id>` redirects there); `corpus.py reclassify` re-runs the source classification.
  `fulltext_fetch.py` (CronJob `fulltext`, :22/:52) fills the text of EVERY News/Official/Blog post that has none or only a feed summary
  (under 500 characters: RTHK's and 明報's RSS, 政府新聞公報), newest first, then older (backfilled) rows; hosts we never read
  (`cache_fetch.SKIP_HOSTS`) are left out in SQL: page reads for at most 4,000 pages or 1,000 s a run, one request at a
  time per site (`cache_fetch.site`: HKET's inews/news/topick/video… subdomains are one queue, 1 s apart; tested at
  2 a second without a 429), 8 a second overall; a 429 / 5xx is retried on a later run (3 runs at most); 信報/香港討論區 pages through `RESI_PROXY` like any other host (no daily
  cap, every date; counted per day for the monitor); Cloudflare-challenged pages (成報, 亞洲週刊, 明報月刊, finance.mingpao.com, and any page whose 4xx answer is the
  challenge, and 明報's instant-news articles: `BROWSER_PATHS`, as some sections answer plain clients 400) in a browser pass of at most 150 pages, in batches of 10 (a fresh Chrome per
  batch, each page in its own tab closed after it, saved after each; one tab over 25 pages reached 1.12 GiB, limit
  2560Mi); HKET's JavaScript-only invest.hket.com / ps.hket.com through their content API. The browser pass opens
  no page that could end after the run's 1,560 s budget (`RUN_BUDGET`; the job's deadline is 1,740 s), so a run always
  finishes and the pages it did not reach wait, untried, for the next one. Every page tried gets `cached_at`, so no page is read twice; network errors are retried up to 3 runs. Paywalled outlets (信報, SCMP, HKET invest) give the
  public part (HKET's content API: its lead and nonPaywall parts); excerpts then go to archive.today (`paywall.py`). `fulltext_fetch.py retry HOST [DAYS]` makes a host's pages of the
  last DAYS days that were read with nothing kept due again (after an extractor fix). 商業電台's AMP links are read on
  the plain page; 資本 (Next.js) from the page's flight data. `fulltext_fetch.py stats` shows posts, posts with text and posts waiting, per website. The text is for
  search only; pages show the headline, an extract and the link.
- **Profiles = the streams** (`streams.json`; delivery settings in table `profiles` via `infra/control.py save`,
  general settings in `settings`). A profile's posts on 數據分析 (every view: 摘要, 帖子, 熱門話題, 作者, 來源, 趨勢, 主題, 人物; the Excel
  exports; the daily reports; the agent's tools) are exactly the stream's members: the posts the scorer gave the
  stream a score at or above its current threshold (table `scores`, at the threshold in force now, so a threshold
  change moves 數據分析 at once) and the posts filed in it (stream_items; "Filed means member" above):
  `insights.members`. The stream page differs only in what it lists: one item per headline, no comment rows. Tags (sentiment, labels, theme) are in table `tags`, kept to exactly the members by `streams.sync_tags()`
  (every poller start, every `thresholds.py set`). There are no keyword profiles and no keyword query decides
  membership (Deyao, 2026-10-14); the text box on 數據分析 (and 搜尋) narrows by words with `query.py`'s operators
  (whitespace = OR, AND, NOT, "phrase", NEARBY/n, FindIn, IncData/ExcData, TopicSearchOnly).
- **Analytics** (`insights.py`, ported to the edge app's `web-app/src/server/dashboard.ts` and `analytics.ts`; shown
  on `/dashboard`, exported to Excel at `/api/export/{summary,posts,hot_topics,authors,sources,trend,themes}.xlsx`):
  Summary, SPI and the CSS/DVS/DPS/CCS scores (YYC's formulas: `insights.METHOD`, shown on the page), Exposure &
  Mentioning Trend, Hot Topics with drill-down, Post Listing, Top Author, Top Source, Smart Hot Terms, Themes, Persona.
  The figures that count posts one per vote carry the selection-bias note: "Figures that depend on our selection"
  below.
- **AI:** the persona note (人物's AI 分析) and the daily report's AI summary shown on 摘要/帖子. The AI 助理 and the
  on-demand summaries of a post or hot-topic listing were removed (Deyao, 2026-10-28; git has them). Every AI call
  costs money, so the AI routes sit under `/app/` (`/app/ai/persona`) behind Cloudflare Access: only accounts Deyao approves one by one in the Access policy can use
  them, until a company-wide login exists (Deyao, 2026-10-13). Others see the buttons greyed out with that note.
  These run on the cluster's AI server (`web.py`); the pages and the reads of past answers are the edge app's.
  Every call's cost is OpenRouter's own figure, logged per day and purpose (`ai.spend()`, table `ai_spend`).
  The WiseSearch page `/app/wisers/` (the edge app's `web-app/src/routes/-WisersPage.tsx`) and the relay's JSON API under
  it, `/app/wisers/api/` (`wisers/relay.py`), have their own, more specific Access app that lets in YYC's users (the one
  access catalogue, below; the rest of /app is admins only); the pod checks the Access token's signature and audience again. Sign-in: Google or a code e-mailed by Access; a sign-in lasts 30 days per browser (session_duration 720h).
- **Agent tools (Deyao, 2026-10-13):** `agent.TOOLS` (what the model is offered) and `agent.HANDLERS` must name the
  same tools (checked when the module loads); every call's arguments are checked against the tool's schema first, and
  a refusal goes back to the model as the tool's result. Each tool needs its end-to-end case in
  `tests/test_tools_downloads.py` (that test fails otherwise). Links the model writes to this site survive only when
  they lead somewhere real (`agent.real_links`: a collected post, or a table that answer made). Turns store tables as
  `{title, id}`; the link is made when shown (`agent.table_url`).
- **Downloads** (the agent's CSV tables, the Excel exports, report files) are the edge app's, always with an RFC 6266
  `filename*` for Chinese names (`web-app/src/server/xlsx.ts` attachment). On the AI server every response header is
  checked as Latin-1 (`app.latin1`): gunicorn silently drops a response with a raw Unicode header, which is how the
  agent's CSV button once did nothing; `tests/test_tools_downloads.py` runs the real gunicorn.

## Figures that depend on our selection (Deyao, 2026-10-14, 2026-10-16 and 2026-10-20)

What we collect is a selection we made: which sources we watch, which search terms we send to platforms that can only
be searched, how many feeds we read per outlet, how often we poll, which comments and replies we fetch. Counting posts
one per vote therefore measures that selection as well as what is happening. These figures were removed on
2026-10-14 and are back since 2026-10-16 ("that's still valuable"), each labelled:

- keyword frequency: Smart Hot Terms / 熱門詞 (摘要, 熱門話題, 人物);
- K-Matrix's daily scores SPI, CSS, DVS, DPS, CCS, their 7-day charts and the rule-based 分數解讀;
- volume and coverage: the 摘要 metrics (posts, new posts, topics, websites, boards, authors, view count, per-day rates,
  website coverage, posts spread, CGM vs News), Exposure & Mentioning Trend and the 趨勢 view;
- profile-wide sentiment shares: 帖子's 情緒分佈, 串流's sentiment bars and its 情緒/標籤/主要來源 column;
- rankings of our own sources and authors: 作者 (Top Author), 來源 (Top Source), a topic's Top Author/Source, the
  most active authors on 人物.

How each is labelled, always with the same sentence (`insights.BIAS_NOTE` = `web-app/src/lib/bias.ts`):
「這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。」 (English, `BIAS_NOTE_EN`: "These figures depend on which
sources and keywords YYC follows, so they may not reflect what is actually happening (selection bias).")

- **Pages:** the sentence once per view, never a 「?」 per figure (Deyao, 2026-10-20: "too many question marks"): one
  quiet line at the top of each 數據分析 view that shows such figures (摘要, 帖子, 熱門話題 and a topic, 作者, 來源, 趨勢,
  人物; not 主題) and one at the foot of 串流's 情緒/標籤/主要來源 column (`web-app/src/lib/BiasNote.tsx`). 摘要's metric
  and score definitions sit in that line's one 「指標說明」 toggle (closed until opened). Each such section is marked
  `data-figure`, the line `data-bias-note`.
- **Exports** (summary, trend, authors, sources): a `Note` line in the header rows.
- **Daily reports:** format1, format2 and report.pdf end the figures with 「註：…」; summary.json has `note`.
- **The data API's tools** (`stats_overview`, `authors_top`, `sources_top`, `trend`) and the **MCP server's**
  (`analysis_overview`, `analysis_authors`, `analysis_sources`, `analysis_trend`, `analysis_terms`): the sentence in
  the description and `note` in every answer; the skills tell a model to say it whenever it uses them. The edge endpoints (`/dashboard` server functions) also answer `note`.

Not labelled (borderline, for Deyao to decide): 熱門話題's ranking with its count and per-topic sentiment columns, a
topic's counts, 主題's per-theme counts and sentiment, 人物's counts and sentiment, the 帖子 filter chips' counts,
搜尋's per-type and per-outlet counts, format1's topic tags and "N 則相關", the negative-burst alerts.
`tests/test_selection_bias.py` checks that every labelled figure carries the note (the code: one note per view, no 「?」 on
a figure; with `WEB_APP_URL`, each page at 390 px and 1600 px shows it once); `tests/edge_parity.py analytics` that the edge port gives insights.py's numbers.

Weighted measures that correct for our selection: ROADMAP.md.

## Daily reports, themes and alerts (`reports.py`)
- **Daily job** (CronJob `reports` hourly at :05; `reports.py daily --if-due` acts once the HKT hour reaches
  the `report_hour` setting (`infra/control.py set report_hour H`), default 09:00, for the day before): first each profile's AI themes over its last 7 days
  (only when there are unthemed posts), then for every profile with posts that day, stored in Postgres (table
  `report_files`, served by the edge app at `/reports/<day>/<profile>/<file>` (報告); built in a temporary folder, then
  removed): `format1.html` (K-Matrix's "Social Profile"), `format2.html` (the
  summary with `spi.png` / `css.png`, the AI summary and recommended action), `report.pdf` (one-page AI report, printed by headless Google Chrome),
  `hot_topics.xlsx`, `posts.xlsx` + `posts.docx` (Galbot sample's table) and `summary.json`. One AI summary call per
  profile and day (`daily:<pid>:<day>` in the corpus kv; 數據分析's AI 每日摘要). Index page: `/reports/`.
  By hand: `reports.py daily --day YYYY-MM-DD`, `reports.py profile CEHK --day …`.
  `reports.py backfill [DIR]` stores an old reports folder (the volume's `$DATA_DIR/reports/<day>/<profile>/`) once.
- **The daily reports are not emailed (Deyao, 2026-10-23: "a functionality that I decide that we no longer need"):**
  they are built and shown on 報告 and 數據分析 only.
- **No e-mail alerts (Deyao, 2026-10-26):** the news stream sends no e-mail; new stream items reach people on Telegram
  and WhatsApp (`telegram_alerts.py`, `whatsapp_alerts.py`).
- **Web and Telegram show an item differently (Deyao, 2026-10-14):** Telegram carries only text, so it gets emoji marks;
  the website keeps its own display (the sentiment and label tags with their icons, and the coloured rule), with no
  emoji marks.
- **Telegram marks (YYC's boss, 2026-10-14: 紅黃綠燈 + 備註, "3+3"; `marks.py`, Telegram only):** a traffic light for the sentiment (🟢 正面 · 🟡 中性/褒貶參半/反諷 · 🔴 負面), then at most one remark, the
  first that applies: 👀 值得留意 (labels urgent, crisis, issue, accuse, rumor; not monitor, which most items get) · 🔥 熱議 (engaging, trend, kol) ·
  📌 重要 (an Official source). A stream with `categories` in
  streams.json (CEHK) gets the scorer's `category` in bold above each Telegram/WhatsApp message, and as a label on
  each 串流 card (串流 stays one list in send order: NAVIGATION.md "串流: tags and categories").
- **Telegram channels (Deyao, 2026-10-13):** one-way, one private channel per stream; every kept item is posted as
  headline + source + link, then 詳情, the post's item page `/item/<id>` (Deyao, 2026-10-14) (`telegram_alerts.py`, a
  thread of the poller). A stream's channel id is `telegram_chat`
  in streams.json; the bot (`TELEGRAM_BOT_TOKEN`, made with @BotFather on Deyao's Telegram account) is an admin of
  each channel. kv `telegram_sent` holds the last `stream_items.id` sent per stream, so delivery resumes after a
  restart or a Telegram outage, and a newly added channel starts at the stream's newest item.
  Timing (Deyao, 2026-10-23: the pipeline tighter): `streams.file` sets `streams.FILED` when it commits new items and
  the sender wakes at once (IDLE 3 s is only the fallback); each channel posts at most one message per `GAP` (3.1 s,
  Telegram's ~20 a minute per chat) on its own clock, so one channel's burst never delays another's first item; a 429
  or network error holds only that channel (`HOLD`) while the others go on.

## English text in a Chinese site: quotation marks and entities (Deyao, 2026-10-21)

"Apostrophe and quotation mark for English sources are wrong" had three causes, each fixed where it starts:

- **The font.** The site is `lang="zh-HK"` in Noto Sans HK, whose ‘ ’ “ ” – — … are full-width CJK glyphs
  ("Netflix’ s"). Every post title, text, excerpt, transcript and comment that is Latin-script is marked `lang="en"`
  (`web-app/src/lib/lang.tsx`: `langOf`, `<Txt>`); under `:lang(en)` (styles/index.css) those marks come from a Noto Sans
  subset of only them (`__root.tsx`, Google Fonts `text=`), every other character still from Noto Sans HK. Chinese text
  keeps its full-width marks. A new place that shows post text uses `<Txt s={…}>` or `lang={langOf(…)}`. The reports and
  alert mails do the same with `reports.el()` (a Latin font first); the Word report's Latin font is Arial.
- **HTML entities and HTML from collectors** (X's `S&amp;P`, Bloomberg's and PR Newswire's double-escaped feeds, SCMP's
  photo pages, the Macau Cultural Affairs Bureau, Blogspot feeds' raw HTML): `corpus.plain()` on every title and text a
  post is stored with (ingest, set_content, set_text, set_transcript, titles.store).
- **The AI-title prompts** asked for 「」 in every title. The prompts say nothing about quotation marks:
  `titles.clean` writes a written title's straight double quotes as 「」 in Chinese and “” in English, and
  `titles.latin_quotes` turns 「」 into “” in a Latin-script title (Chinese and Japanese keep 「」).

`tools/fix_entities.py` repaired the rows stored before (resumable, kv `fix_entities:last_id`; logs every change).
Tests: `tests/test_quotes.py`.

## TikTok videos (`tiktok_video.py`, Deployment `tiktok-videos`; Deyao, 2026-10-22)

Every TikTok post whose **stage-1 score** (`streams.SCREEN` = 20) reaches 20 for any stream gets its video
downloaded once and kept indefinitely: Deyao's explicit exception to "text only" (`nomedia.py` EXCEPTIONS), for TikTok
videos only. Transcription stays off (flag `transcription`); nothing reads the audio.

- **Queue** (table `tiktok_videos`, one row per post): `streams.score_posts` queues the post right after stage 1
  (`tiktok_video.queue_stage1`, how `stage1`), whatever stage 2 then decides. The worker's sweep (every 10 min, last 3
  days) queues any stage-1 pair the hook missed (how `sweep`); `python3 tiktok_video.py backlog [DAYS]` queues the
  older ones (how `backlog`, through `cluster/run.sh`). Stage 1's score of a stored pair = `scores.screen`, or the
  stage-1 score where stage 2 never ran. Only `tiktok.com/@user/video/<id>` URLs (photo posts and comments are not).
- **Download** (`tiktok_video.download`): yt-dlp from the cluster's own IP, never a proxy (no yycstream, no ISP IP:
  `--proxy ""` and no `*_proxy` variable), `-f "bv*+ba/b" -S res,vcodec:h264,acodec:aac,br` (the highest resolution
  with audio; H.264 among formats of equal resolution. TikTok often has 720p only as HEVC and 576p as H.264, so the
  720p HEVC wins and is re-encoded to H.264 at the same resolution (libx264 veryfast CRF 23, AAC kept): HEVC played sound
  with no picture in Chrome without a hardware decoder; a 44 s clip took 5.6 s on 2 threads), remuxed by ffmpeg to MP4 with `+faststart`, checked with ffprobe.
  States: `queued` → `ok` | `gone` (TikTok: removed / private / unavailable) | `failed` (a photo post, or a post TikTok
  shows only when signed in (age-restricted; we hold no TikTok login) or refuses to the cluster's IP ("Your IP address
  is blocked from accessing this post"; no proxy is used), at once; else
  after 5 tries, transient failures waiting 10 min, 30 min, 1 h, 6 h). A video already saved for another post (same
  video id) is not downloaded again (format `same video as post N`). Claims (lease 5 min, renewed) let every copy work,
  so a rollout's new pod starts at once and the old one finishes its downloads (grace 30 min).
- **Storage**: Hetzner Object Storage, bucket `yyc-tiktok-videos` (project `yyc`, fsn1, private; key
  `tiktok/<video id>.mp4`, Content-Type video/mp4), S3 credential `yyc-videos` (store `yyc`:
  `YYC_VIDEO_S3_ACCESS_KEY` / `YYC_VIDEO_S3_SECRET_KEY`; Secret `video-s3`, the worker only). The bucket's CORS allows
  GET/HEAD from newswatch.yyc-internal.com and yyc-beta. `s3.py` is a stdlib SigV4 client (PUT streamed from disk, HEAD,
  ListObjectsV2, presign). EXPENSES.md has the price.
- **Item page** (影片 card, `-ItemPage.tsx` VideoCard): everyone sees the state (排隊中 / 已保存 / 已刪除 / 未能下載); an
  approved account gets `/app/item/video?id=` (the edge app, under /app/ behind Access; `server/video.ts`): two
  presigned GET links made per request, valid 1 hour (one plays in a `<video playsinline>`, one downloads with
  `response-content-disposition: attachment`), refreshed every 45 min while the page stays open. **The browser reads the
  file straight from the bucket**: nothing passes the cluster or the Worker (Deyao, 2026-10-22). The Worker `yyc-web`
  signs with WebCrypto (`server/s3.ts`, same algorithm as `s3.py`); its vars `VIDEO_S3_ENDPOINT/_REGION/_BUCKET`
  (wrangler.jsonc) and secrets `VIDEO_S3_ACCESS_KEY/_SECRET_KEY` (set once with `npx wrangler secret put` and the yyc
  store's `YYC_CF_API_TOKEN`; a new key = put both again).
- **Monitor** (panel `videos`, section "TikTok videos"): per HKT day posts that passed stage 1, videos downloaded and
  their bytes, failures and gone; the queue and retries; failures by reason and the latest ones; the bucket's own object
  count and bytes (ListObjectsV2 by the worker every 10 min, kv `tiktok_video:bucket`).
- Run one by hand: `python3 tiktok_video.py test URL` (download only), `stats`. Tests: `tests/test_tiktok_video.py`,
  `tests/test_nomedia.py` (the exception).

## Hosting (billed to YYC)
Hetzner Cloud project **yyc** (id 16364176, €20 e-mail spend warning), server `yyc-stream` (cx23, fsn1), set up by
`infra/provision.sh`; the page goes out through the Cloudflare tunnel `yyc-stream` (`deploy/cloudflare`, on YYC's Cloudflare account),
nothing listens publicly. The server pulls this repo every 5 minutes, so **a push to main deploys**.
Model spend: OpenRouter API key **yyc-stream** (used only by this server; USD 20/month cap), on Deyao's card.
Data policy (Deyao, 2026-10-20): yyc-stream is the only key on the account that may use paid endpoints which train on
prompts (it sends public news only), so stage 2 can reach DeepSeek's own endpoint (`provider.only: ["deepseek"]`). This
takes the account privacy setting, the workspace's Default guardrail and the key's own guardrail `yyc-stream` all allowing
it; every other key has a guardrail that denies it (`openrouter-dash` skill, "Privacy and guardrails").

## Cost profile (`infra/cost-profile.sh`, `infra/cost-profile.py`, `costlog.py`; Deyao, 2026-10-16)
"See what each component uses so we can decide how to cost-optimise — similar to running a profiler." Information
only: nothing reads it to decide what the pipeline does. What is recorded, all the time:
- every OpenRouter call in `ai_runs` with purpose, model, OpenRouter's charged cost, the **job** (`costlog.job()`: the
  Kubernetes CronJob/Deployment from the pod name) and OpenRouter's **generation id**;
- every TikHub request (`tikhub_fetch.call`, `ig_fetch.tikhub`, `comments_fetch.tikhub`) and AnyAPI search in
  `paid_calls` (job, endpoint, HTTP status, AnyAPI's `costUsd`); TikHub's prices are its own endpoint list;
- proxy bytes per site and per job per HKT hour (kv `proxy:bytes:<site>:<hour>`, `proxy:jobbytes:<job>:<site>:<hour>`);
- captcha solves in `solver_spend` (provider, site);
- the providers' own counters every 5 min in `provider_snapshots` (the monitor's balances panel): OpenRouter key
  yyc-stream's all-time usage, TikHub's day usage and per-endpoint counts (`uri_counts`), IPRoyal yycstream's traffic
  used, AnyAPI's and the solvers' balances.
`news-stream/infra/cost-profile.sh [HOURS | START END]` (a session with the `yyc` store; default the last 24 h) runs
the profile in the monitor pod and writes `~/artifacts/cost-profile/<start>/cost-profile.{md,csv}` + `profile.json`:
per component and part (model × purpose, proxy per site and job, TikHub per job and endpoint, solver per site,
Hetzner per resource at list price), provider figures where they exist with our log beside them, share of the total,
the projection to a 30-day month, the calendar month and the month's end (basis stated), the top items flagged.
The monitor's Spending area shows the same aggregation per job (Deyao, 2026-10-16) for the last 6 h, 24 h and this
month (HKT), sorted by the last 24 h, each job expandable: **IPRoyal traffic** in bytes only, never USD
(`proxy_traffic`: per job → site, with IPRoyal's own figure for yycstream beside our count and the gap; this month's
provider figure is IPRoyal's data-usage-report), **TikHub** and **AnyAPI** USD per job → endpoint with failed calls and
their HTTP statuses apart (`calls_by_job`; failed calls are not charged: TikHub's own per-endpoint counts match our
successful calls), and OpenRouter USD per job. Units are IPRoyal's: decimal, 1 GB = 10^9 bytes, 1 MB = 10^6 bytes (its
data-usage-report in B/KB/MB/GB: 8323 B = 8.323 KB = 0.008323 MB; yycstream's traffic_used, the GB a top-up adds,
matches the report's bytes / 10^9, not / 2^30). Tests: `tests/test_spend_breakdown.py`, `tests/test_edge_monitor_now.py`.

## WhatsApp groups (`whatsapp_alerts.py`, `whatsapp/gateway.js`; Deyao, 2026-10-16)

Route E of `docs/whatsapp/` (the research): a self-hosted unofficial client. YYC has its own WhatsApp number,
**+44 7448 090466** (a UK Lycamobile SIM Deyao holds; registered by SMS 2026-10-16, profile name 「YYC 新聞監察」), whose
primary phone is an Aliyun cloud phone YYC rents: **`acp-hln9ilimxqzruvccu`** (group `ag-hln9ilimxqzrt8xos`, cn-shanghai,
acp.basic.small, CNY 65.00/month prepaid, auto-renew; EXPENSES.md). WhatsApp is blocked in mainland China, so the
phone's whole egress goes through its own eds-aic proxy policy group `pg-0bjrh3takg6ldv6xf` (transparent SOCKS5) to
IPRoyal sub-user `yycstream` with a Hong Kong exit and a 7-day sticky session. The phone is a shared device: take the
`whatsapp-phone` lease (claude-env `scripts/lease.py`; `scripts/ecp.py` refuses without it and never stops or deletes it).

- **Sender:** Deployment `whatsapp` runs `whatsapp_alerts.py run`, which takes the Postgres lock 'yyc whatsapp' and
  starts `whatsapp/gateway.js` (Baileys 7, a linked device of the number, spoken to on 127.0.0.1). The device's keys are
  in table `whatsapp_auth`, so a rollout or a node move continues the same device without re-linking.
- **Delivery:** one group per stream in streams.json (Deyao, 2026-10-16: every stream), named like the Telegram channels (「YYC · <name>」, only admins post or edit),
  jid in `streams.json` `whatsapp_group`. Every filed item, Telegram's content as text (category in bold, mark +
  headline, source + link, 詳情 + item page), in order, as filed (Deyao, 2026-10-16: no pacing or warm-up). Cursor kv
  `whatsapp_sent`, starting at a group's first newest item (no backfill); at most once (the cursor moves before the
  send); nothing is sent while the device is not connected. Never recalled or edited.
- **Who joins:** Deyao, by the groups' invite links (`whatsapp_alerts.py groups` lists them).
- **Monitor:** the WhatsApp panel (kv `whatsapp_status`: linked / connecting / unlinked / logged_out / banned /
  replaced, last disconnect; per group the last item sent and the backlog; kv `whatsapp_error`). Discord (`yyc-alerts`)
  once when the device is logged out or the number banned.
- **Commands** (in the pod: `kubectl -n yyc exec deploy/whatsapp -- python3 whatsapp_alerts.py …`): `status`,
  `groups` (make the missing groups, print jid + invite link), `invite JID`, `test STREAM`, `pair 447448090466`.
- **Re-linking** (after `logged_out`): `pair 447448090466` prints an 8-character code; on the cloud phone, WhatsApp →
  ⋮ → 已关联的设备 → 关联新设备 → 改用电话号码关联, type the code (it expires in about 3 minutes). Claude drives the phone
  with `ecp.py` (UI text via `uiautomator dump`).
- **Cloud phone pitfalls (2026-10-16):** a new policy group has the screenshot policy OFF, so `CreateScreenshot` fails
  "CheckPolicy failed" until `ModifyPolicyGroup` sets `PolicyConfigs.1.ConfigKey=SCREENSHOT_BUTTON`,
  `PolicyConfigs.1.ConfigValue=ON`. The phone's root shell is NOT behind the proxy (it exits from Aliyun Shanghai, where
  f-droid.org times out), so on-device downloads use `curl -x socks5h://…` with the proxy login. The GFW blocked the
  IPRoyal gateway 103.214.44.131:32325 from the phone for a while ~8 minutes after the phone started using it; other
  gateway IPs (`dig geo.iproyal.com`) answered, and the policy group now points at 91.239.130.17. If the device keeps
  dropping to `connecting`, check the gateway IP from the phone (`nc -z -w 4 IP 32325`) and point the policy group at
  one that answers. Apps: Aurora Store (F-Droid build, hash checked) logged in anonymously; WhatsApp from it.

## Deyao's monitor (`/app/monitor`; monitor.py, web-app/src/routes/-MonitorPage.tsx; Deyao, 2026-10-14)
A read-only page for Deyao alone: https://newswatch.yyc-internal.com/app/monitor (behind the /app Access app; the edge app
answers 404 to every identity but his, `ADMIN_EMAIL` in `web-app/src/server/access.ts`), not linked from the site's
navigation. It refreshes itself every
minute. **Link following** (follow.py; card under Pipeline): posts followed and links (new, duplicate, unknown site,
skipped per reason with their top sites, failed, waiting) for the last 24 h and all time, and the sites no source reads
(host, links, posts, an example post and link): the list of sites we're missing. No budget-shaped figures (Deyao, 2026-10-15/16): no cap bar, run-out date, headroom or projection; spend
and balances are facts. **An error is lit only while it is current** (Deyao, 2026-10-16; a send error red, a source's error ink: "State colours" below): a channel's send error once
a later send to that stream worked (any stream's, for an error naming none; kv `telegram_sent_at` /
`whatsapp_sent_at`, written by each send that worked), a source's last error once it answers again, history
scoring's "OpenRouter limit" once OpenRouter's stop (kv `openrouter_limit`) has cleared: each is shown muted with its
time (`monitor.settle`, `error_current`, `state_current`). Panels:
- **Stopped providers** (top of the overview): every provider whose requests fail now for credit, balance or a limit,
  said as what happened with the error it returned: "OpenRouter: out of credit — requests failing with 'Insufficient
  credits' since 21:07 HKT 14 Oct", tagged "out of credit" / "balance used up" / "limit reached" / "HTTP 402" from the
  recorded error (`monitor.stop_tag`); from kv `openrouter_limit` (ai.py) and `social_limit` (tikhub_fetch.py); gone
  once a request works again.
- **Sources:** every entry of `poll_state.source_status`: OK or failing, the error, last OK, polls failed in a row and
  since when, the last error after a recovery (the poller keeps these: `app.track`), proxy/browser/social kind, posts
  published in the last 24 h against the source's usual day (median posts per day by publish time over the 7 days
  before, so a first read's backlog falls on its own days; archive-backfill rows and a source's first day left out);
  "quiet" = answering but under 25% of a usual day of 3+ posts, both under its own id and in its listing's links
  first read in 24 h (status `new_24h`, `app.note_new_24h`: a URL is stored under the first source that lists it, so
  SCMP's sitemap takes most of scmp_hk's stories and 明報's sitemaps most of 港聞's). A source switched
  off on purpose (the watching safety stop) is "off: <reason>", never failing: not
  under Problems, not in the failing count; judged normally again once it is back on. Each poll writes every job
  file's lines afresh; only a scheduled source that wasn't due carries its last status (`sources.scheduled`), so a
  success always replaces an earlier error. A suspended CronJob shows "off: suspended".
- **Jobs & pipeline:** CronJobs (last success, last runs, late or failed) and Deployments from the Kubernetes API;
  the poller's last poll, posts awaiting live scoring, the history scoring's state, the OpenRouter hard stop when hit, full text waiting,
  comments, forum replies, social search, archive backfill positions, database size.
- **Proof flags:** the open flags of the proof view (below), newest first: each with its post's title linked to its
  entry (`/app/proof?sid=<sid>&at=<post id>`), the sid, who flagged it and when, and the note (`/app/monitor/flags`,
  Deyao only; `web-app/src/server/proof.ts` openFlags).
- **Telegram:** per stream, items filed after the last one sent, the last item sent and when, and the last send error
  (kv `telegram_error`).
- **WhatsApp:** the linked device's state (kv `whatsapp_status`: linked / connecting / unlinked / logged_out / banned /
  replaced, since when, the last disconnect), its number, the primary phone (cloud phone `acp-hln9ilimxqzruvccu`), per
  group the backlog and the last item sent and when, and the last send error (kv `whatsapp_error`).
- **Balances,** live from each provider's API (cached 5 min): OpenRouter: the account's credit in USD (bought, used,
  balance; `GET /api/v1/credits`, which answers only to a management key: `yyc-monitor`, `OPENROUTER_MANAGEMENT_KEY` in
  Secret `monitor-keys`, held by the monitor pod alone; Deyao's personal account, so the balance is the whole account's)
  and key yyc-stream's own spend (this month and today, the last 24 h hourly rate); no projection or run-out date.
  TikHub (balance, today's usage), AnyAPI (balance), IPRoyal (sub-user
  yycstream's traffic used/left, per-day usage report, the last order's per-GB price, and our own count of its bytes
  per site, kv `proxy:bytes:<site>`). AWS (Deyao, 2026-11-17: YYC's member account 551897326524, where YYC AI's
  sandbox hosts run; `monitor.bal_aws`): what AWS has billed this month, yesterday and today so far and the month per
  service, from Cost Explorer (`GetCostAndUsage`, UnblendedCost per UTC day and service; AWS marks the days as
  estimates until the month's invoice and completes a day up to 24 h after it ends), and the instances there now (EC2
  `DescribeInstances` in `AWS_YYC_REGIONS`, default eu-central-1). Cost Explorer charges USD 0.01 a request and its
  data changes at most three times a day, so it is read once every 6 h (`AWS_CE_TTL`) and kept (a new pod takes the
  published snapshot's figures; a failed read keeps the last ones and says why); the instances are read every round.
  Key: IAM user `yyc-monitor` (`infra/aws-monitor-iam.py`: those two read calls and nothing else),
  `AWS_YYC_MONITOR_ACCESS_KEY_ID` / `AWS_YYC_MONITOR_SECRET_ACCESS_KEY` in Secret `monitor-keys`. In Spending, AWS is
  a series of the history (its own days), and its bar is the latest closed day's cost spread over its 24 h, per service
  (`aws_window`: Cost Explorer has no finer figure than a day). Hetzner
  (needs a read-only project token `HETZNER_YYC_READ`; its API has list prices, not billing), Exa (needs a service
  key) and Cloudflare account YYC (needs an analytics/billing token) show "not connected" and what they need.
- **Spending** tracks what things cost now (Deyao, 2026-10-16: after transcription moved to Qwen3-ASR-1.7B on
  2026-10-15, ~12× cheaper a minute, the month's totals still showed the old model): the last 6 h and
  24 h per component and its biggest parts (`infra/cost-profile.py`'s `profile`, the cost profile's own logic: the
  provider's counter where its snapshots cover the window, else our log; Hetzner as its fixed monthly card); per AI
  purpose the model in use, since when, the model it replaced, and its rate now = its spend since it took over, over at
  most the last 24 h (`cost_profile.current_models`, ai_runs); this month per purpose × model, replaced models muted as
  history; then the last 30 days by day per provider and month to date (history). Each figure names its source.
The data comes from the Deployment `monitor` (`gunicorn monitor:application`; deploy/apps/monitor.yaml): it
holds the provider keys nothing else has (Secret `monitor-keys`: IPROYAL_API,
OPENROUTER_MANAGEMENT_KEY, CAPSOLVER_API, TWOCAPTCHA_API = YYC's own 2Captcha account, for its balance), reads the namespace with a
read-only ServiceAccount, and nothing reaches it over the network (no Service; NetworkPolicy without ingress). Each panel
is computed by its own background loop into a snapshot and published at once into Postgres (kv `monitor:data`, one
panel per statement); the page (the edge app) reads that, each panel with `computed_at` and `age_seconds`
(shown under every section heading), so it answers at once however slow a panel is and the cluster is off the read
path. A failed refresh keeps the last good snapshot (also one another pod published) and says why. The readiness probe is GET /readyz, 200 only once every panel has its first
snapshot (or after 180 s), so a rollout never sends the page to an empty monitor. Tests: `tests/test_monitor.py`, the
page and its gate `tests/test_edge_ops.py`.

## The proof view (`/app/proof`; `proof.py`, web-app/src/routes/-ProofPage.tsx; Deyao, 2026-10-20)
"Where I can just click arrow to see each surfaced database entry, so I can check if they look correct." Deyao's own
tool, so in English (Deyao, 2026-10-21; NAVIGATION.md "Languages"; titles, outlet names and text as stored). For approved
accounts (under /app: Cloudflare Access, the token checked again by every read):
https://newswatch.yyc-internal.com/app/proof?sid=a,b,c (one or more `posts.sid`; the sources page links each title's card this
way, so the form stays) or `?catalogue=1` (every source of `sites.json`, from edge:config `catalogue`, source by source
in the catalogue's order) or `?stream=ID&above=N` (Deyao, 2026-10-22: "show me everything about wang fuk court that
scored above 10": every post from any source whose score for that scored stream is over N at stage 1 or stage 2, N
default 10; the tabs are about that stream alone, and the sources are those the posts come from, the most posts first);
`&at=<post id>` is the entry shown (written into the URL at each step).
- **One entry = one stored post as the pipeline holds it** (`server/proof.ts` entry, built on the item page's
  `view` / `text` / `aiScores`): the source, sid and its feed URL (sites.json), the shown and the stored title,
  author, published (as the source gave it: a date only when it gave only a date, "not given by the source" when none) and
  first-seen times, the URL with "Open the original", the full stored text, how the text came (`text_via` and its English label,
  `cached_at`, the archive copy), the comments count (rows with `thread` = its URL), the links in its text, Backfill
  (`first_run`), every stream's exact score (Deyao, 2026-10-21: "my proof page should show me the exact score even
  when below 20"; `aiScores(…, exact)`): stage 1 and stage 2 side by side with the threshold, passed/below, filed, the
  reason; streams under 20 on one line ("Under 20: HKIC 3 · …"), and a post scored before every score was kept says
  "not stored (scored before <kv scores_all_from>)"; the streams it was filed in, and its proof.py flags. Comments and
  replies are not entries. **Model answers** (Deyao, 2026-10-21: "i want to see the tags etc that the model returns"):
  each stage's answer for the post as the model gave it (`scored.answers`, since 21 Oct 2026: sentiment, labels, each
  stream's category, stage 2's title judgement title_ok / title_kind / title, and any other field), and what was kept:
  the post's sentiment and labels with their model (`scored.tag_model`), its AI title (kind, model), the market. The
  links in the text are listed in the post card without a click (four lines, scrolling; L: up to twelve).
- **Fits a laptop window** (Deyao, 2026-10-21: "to help me proof without scrolling, design a layout that fits on a
  16:10 screen"; "make texts reasonably small"): from 1200 x 600 one entry fills the window with no page scroll, in a
  dense type scale (text 13 px, details 11–12 px, title 15 px), post left, scores and the check right. Primary target
  his MacBook Air's browser window, 1440 x 830; checked at 1710 x 1010, 1280 x 800, 1680 x 1050, 1920 x 1200 and 1440 x 932
  (full screen). What gives way, in order: the text scrolls in its own panel (a fade at its foot; M = more text:
  the post's details step aside; Space / Shift+Space scroll it); the title is clamped to two lines and the URL,
  canonical and feed lines to one (the whole value on hover); the links in the text show four lines and scroll in their
  box; the collection details scroll in their own box; streams under 20 share one line. The scores (with their reasons,
  wrapped), the model answers, the check and the step buttons are never cut. Phones stack and scroll.
  **Full screen:** F or the button (the Fullscreen API, Safari's webkit one as the fallback); the top bar steps aside.
- **The keyboard** (Deyao, 2026-10-21: "i can use the proof page without using mouse"; `src/lib/proofKeys.ts`, ? shows
  the list): → / J next, ← / K previous, ] / [ next / previous source, X or ! wrong, O correct, 1–9 that stream's
  score is wrong (the number beside it), N note (Enter saves, Shift+Enter new line, Esc cancels), V the view, M more
  text, L all links, Space scroll the text, Enter open the original, F full screen, D download, C clear (asks in the page: Y / Esc).
  Ignored while typing (but the note's Enter and Esc) and with Cmd / Ctrl / Alt. Each action says what it did in a toast.
- **Arrows:** ← → buttons, the keyboard's arrows and a sideways swipe step through the list, newest first, with
  "3 / 20"; the next and previous entries (and the one after) are read ahead, so a step shows at once.
- **Three views, remembered per viewer** (localStorage `yyc-proof-view`; V steps through them): "In a stream" (default) =
  the entries that reached a stream: filed (a `stream_items` row) or a scored stream's score at or over its threshold;
  **"Passed stage 1"** (Deyao, 2026-10-22: "another tab that is everything that pass stage 1") = the entries stage 1
  scored at or over edge:config `screen` (`streams.SCREEN`, 20) for a scored stream, whatever stage 2 did: a `scores` row
  with `screen` set (stage 2 scored it), or with no `screen` and the stage-1 model or no model (stage 1 only, before
  two-stage scoring), or waiting for stage 2 (the post's latest stage-1 event in `events` has a pair at or over the
  screen and no successful stage-2 event follows it: stage 2 not yet run, or failed and due again; events are kept 3
  days). A stage-2 score whose stage 1 was under the screen (a filed post re-checked) does not count. In the entry's
  scores each such pair shows stage 1 and stage 2 with their reasons and its outcome: "filed <day>", "rejected by stage
  2", "passed stage 2", or stage 2 "pending" + "waiting for stage 2" with stage 1's score and reason from the event
  (`entry.pending`); "Everything collected" = every entry, each showing its score/threshold state or why it is not
  scored. The counter, the arrows and the per-source counts follow the view; an empty view says how many posts were
  collected and offers the full view.
- **Your check is kept in this browser** (Deyao, 2026-10-21: "let's not complicate database setup … save notes locally
  and have one button and shortcut to download it as a file so i can process it later"): Wrong / Correct, the streams
  whose score is wrong and a note, per post in localStorage `yyc-proof-notes` (with the URL, title, source, times and
  the scores shown then); nothing is sent. "N not downloaded" counts what changed since the last download; Download (D)
  saves every record as `yyc-proof-notes-YYYY-MM-DD-HHMM.json` (Hong Kong time) and keeps them; Clear (C) asks first.
  The page makes no write: the older `proof_flags` route (POST `/app/proof/flag`, web.py → proof.py) is still there
  for proof.py and the monitor's Flags list (above), but the page no longer calls it.
- **Claude reads and fixes the flags** with `proof.py` (DATABASE_URL = role `app`: with `deploy/cluster/cluster.sh db-forward` running, `postgresql://app:$YYC_PG_APP_PASSWORD@127.0.0.1:15432/app?sslmode=require`):
  `python3 proof.py list` (open flags as JSON lines), `show ID` (the flag and the post as stored), `resolve ID what was
  done`, `delete ID` (a test flag).
- Tests: `tests/test_proof.py` (the flag route and proof.py, the list and entry in Node, the router),
  `tests/test_proof_page.py` (the page in WebKit iPhone 390 px and Chromium 1600 px: steps, keyboard, swipe, preload,
  both views remembered, the local check and the monitor's list, the catalogue walk; test_keys: the laptop sizes fit
  with no page scroll and the whole check by keyboard, the download's file; test_stage1: "Passed stage 1"), `tests/test_proof_keys.py` (the key map
  and the notes in Node), `tests/test_all_scores.py` (the exact scores).

## The live dashboard (`/app/live`; `events.py`, web-app/src/server/live.ts, -LivePage.tsx; Deyao, 2026-10-21)

Deyao watches the pipeline live, with full attention, on two displays side by side: a 1920 x 1200 (left) and a
1920 x 1080 (right). One logical canvas of two 1920-wide halves, each its own window, fullscreen on its display:
**`/app/live?half=left`** and **`/app/live?half=right`**, both reading the same data. **`/app/live`** (no `half`)
shows every panel in one 1440 x 830 window (a laptop, testing), and on a phone (a window under 900 px) one scrolling
column (`phoneLayout`). Deyao's identity only (gates.ts: a plain 404 to anyone else), English, titles and outlets as
written, read-only. Besides the pipeline it shows **YYC AI** live and the **systems' health** (Deyao, 2026-10-28:
"watch live stuff for the AI system as well, and also health of systems … make some components smaller or delete a
few … other system healths like balance etc. … still the two screens"): Duplicates and Links followed were removed for
them (their events too), Full text went from two columns to one.

- **Fixed layout** (lib/liveLayout.ts `LAYOUTS`; Deyao: "text size and layout should be fixed … since we know the
  screen layout"): every panel an absolute px box, every card a fixed height per panel (`CARD_H`) with its text
  clamped to fixed lines, type at fixed px (12.5 / 11 / 10.5 px, line-height 16 / 14 px); no vw/vh/clamp(), nothing
  fitted to the window. Short panels of a few rich cards, not long lists (Deyao: "a list doesn't need to be that
  long"): a 3 x 2 grid per display, no panel across the seam (Deyao: "a panel cannot span two displays"):

  | Display | Row 1 | Row 2 |
  |---|---|---|
  | left 1920 x 1200 (rows 589 px) | 1 Ingest (8 cards) · 2 Full text (5, five lines of text each) · 3 Comments (5, the three most liked), 636 px each | 4 Stage 1, 1276 px wide (5 cards, every topic's score, meter and reason) · 5 Stage 2 (4, stage 1 vs stage 2 with both reasons) |
  | right 1920 x 1080 (rows 532 px; Deyao, 2026-11-12) | 6 Filed (5) · 7 Alerts (8) · 8 Problems (6), 401 px each · 11 Jarvis 4, 720 px (two columns of 16 sessions; 2026-11-17, his sessions went past 16) | 9 YYC AI, 959 px wide (its state in a 479 px column, its activity as cards beside it) · 10 Systems, 959 px wide |

  One window (1440 x 830) is a 5 x 3 grid: the pipeline's five panels, then Filed, Alerts, Problems and Jarvis 4 (two
  columns of 8 sessions), then YYC AI (three columns) and Systems (two).
- **Systems fits its box (Deyao, 2026-11-12:** Postgres, WiseSearch and object storage fell off the bottom of the
  screen): its lines are a list of fixed heights (`fitItems`), cut to the box it has. Red and amber lines always stay;
  when not everything fits, the healthy AWS hosts share one line, then bucket lines, extra provider figures and the
  other lines give way, and a last line says how many are on /app/monitor. A provider's first line is short enough
  for a phone's width beside its name (AWS: the month and yesterday; its services and running instances are two extra
  figures, which give way early); `tests/test_live_dashboard_page.py` test 7 measures, at each layout's size, that no
  line ends below the box, no part of a line is cut at its right edge and AWS's figures are shown whole.
- **Jarvis 4 (Deyao, 2026-11-12:** "a panel for my jarvis 4 status, i just need to know limited information like if
  session is working, idle, or with background … at most 12-16 sessions"): Jarvis 4's router (DE0CH/jarvis4
  `router/livepush.go`) POSTs his running sessions, and the paused ones that will wake by themselves, every 5 s to `/live/jarvis` with `X-Jarvis-Secret` (Worker secret
  `JARVIS_LIVE_SECRET` = store key `YYC_JARVIS_LIVE_SECRET`); the LiveHub Durable Object keeps the last push
  (`server/jarvis.ts`; never Postgres) and `/app/live/data` returns it as `jarvis`. Each session: its state word
  (working ink-filled, needs you and idle amber (Deyao, 2026-11-17), background, scheduled and booting blue), its title, how long in
  that state and its background jobs and subagents; scheduled (Deyao, 2026-11-17: "it should also show scheduled as
  blue, similar to background") = paused or idle with a wakeup, cron or watch armed, as the Jarvis 4 app's Scheduled,
  with its next wake in HKT ("next 14:30", "watching" for a watch alone); at most 32 (16 in one window), in the router's order (needs you, idle, working, background, scheduled, booting), "N more"
  in the header, magenta "no update since" after a minute without a push. YYC holds no Jarvis 4 credential.
- **View only (Deyao, 2026-11-17:** "there can't be any clickable element on the monitor … it's a view only live
  monitor"): no links, buttons or inputs anywhere on /app/live (YYC AI's Schedules link was removed; test 3 counts
  them).

  Each header: the panel's count in the last 10 minutes and hour (read every 30 s).
- **One card at a time** (Deyao: "should do one new item at a time even if it pulled multiples in one update"): each
  panel queues a read's new cards, oldest first, and puts them on top one by one, spread over the time to the next read
  (POLL_MS / queue length, at least 300 ms apart); a queue longer than one read's worth shows "+N behind"; nothing is
  dropped (the read hands a burst over in order: after a cursor it returns the oldest 40, not the newest). Each insert
  is instant: no highlight, fade or animation (Deyao: "no highlight, too busy"); the oldest card leaves at the bottom.
  No unseen counts, no acknowledge key, no sound.
- **State colours (Deyao, 2026-11-17), on /app/live and /app/monitor:** red is only a service lost right now ("immediate
  loss of service"): a Deployment people work through with no pod ready, a CronJob with no success for three of its intervals ("late"), the
  gateway or a host with someone's sandbox on it not answering, a provider stopped for credit or a limit, WiseSearch
  logged out or its health check failed twice in a row (`monitor.wisers_down`), an alert refused for good, YYC AI's
  status "problem". Amber is a caution, still serving: a Deployment short of pods whose others serve ("1 of 2
  ready"), the monitor with none ready (it serves nobody; its process keeps writing live:sys while /readyz waits on a
  slow panel; `healthStates.ts` SERVES_NOBODY), one failed CronJob run, a node not ready or under pressure, an empty host not answering, Postgres near its
  connection limit or under its free-space floor, a failing WAL archive or base backup, and errors that are over (a
  failed sandbox start, a refused call, the counts of the last 15 minutes). Magenta is blind: the page cannot read
  something (a provider's API, Postgres figures, the node list, a bucket, YYC AI's snapshot) or its figures are old
  ("no update since"), so the thing may be fine or down. Blue is something that finishes by itself (a rollout, a
  host booting). A failing news source is quiet ink, on both pages (most likely their end, outside our control): the
  source's tag, the "Sources failing" tile, a full-text fetch that failed ("not fetched"). Tests:
  `test_live_dashboard_page.py` 3 and 3b, `test_edge_monitor_now.py` 6, `test_monitor_wisers.py` `test_down`.
- **Staleness:** no successful read for 30 s, or the poller's last poll older than 12 min: the whole screen greys
  out and says "No data since HH:MM". YYC AI and Systems say "no update since HH:MM:SS" in magenta (blind) when kv live:sys is
  over a minute old. A screen wake lock while visible; F = full screen.
- **Data:** the page reads `/app/live/data?c=<panel:last id,…>` every 3 s; one answer covers every panel in three
  indexed queries (+ three counts every 30 s) and kv `live:sys`.

| # | Panel | Source | Written by |
|---|---|---|---|
| 1 | Ingest | `posts` (thread IS NULL) by id: title, source, type, the text's opening | the existing rows |
| 2 | Full text | events `fulltext`: ok or the failure's reason, characters, the first ~700 characters, route (direct / direct (Chrome TLS) / proxy / ISP IP / browser / captcha solver / archive.today; `cache_fetch.last_route`) | `fulltext_fetch.save` (the job and `fulltext_now`), `cache_fetch.save`, `paywall.save_archive` |
| 3 | Comments | events `comments`: new comment rows, the platform's total, the three most liked | `corpus.add_comments` |
| 4 | Stage 1 | events `stage1`: every scored post, every topic's score and reason (green = at its threshold, amber 40+) | `streams.score_posts` |
| 5 | Stage 2 | events `stage2`: per checked topic stage 1's score and reason → stage 2's, pass or reject at the threshold, sentiment, labels; no answer = red | `streams.confirm` |
| 6 | Filed | `stream_items` by id: stream, score (stage 1's beside it), the reason, category, sentiment, labels | the existing rows |
| 7 | Alerts | events `alert`: Telegram / WhatsApp, stream, the post, sent or the refusal | `telegram_alerts.send_due`, `whatsapp_alerts.send_due` |
| 8 | Problems | events `problem`: a poller source failing 2 polls in a row and its recovery (`app.note_problem`); a CronJob late, a Deployment short of pods, the WiseSearch session (the monitor's Discord alerts, `monitor._alert`) | as named |
| 9 | YYC AI | state: kv `live:sys` `ai` (the status and its reasons, live sandboxes with owner, host, age and "active now" / idle, today's model calls and spend per person and model, the budget used of its limit, errors of the last 15 min, sandbox starts of 24 h with median and p95); cards: events `ai` (model calls per person since the last snapshot with their cost, sandbox started / failed / released, host events, egress errors, a verdict change) | `monitor.live_once` from YYC AI's operator snapshot (below) |
| 10 | Systems | kv `live:sys` `health`: every provider's own figures with the source (OpenRouter credit and each `yyc-*` key's usage today and this month, TikHub, AnyAPI, IPRoyal `yycstream`, CapSolver, 2Captcha, Hetzner list prices, AWS's billed cost this month and yesterday with its services and running instances, Cloudflare, Exa), the stops, the cluster's nodes, Postgres connections and size, the news stream's Deployments and CronJobs, YYC AI's pods and sandbox hosts (gateway, AWS hosts: sandboxes of slots, load, memory) | `monitor.live_once` (its own balances, stops and jobs panels; nodes; Postgres; the snapshot) |

**`live:sys` and events `ai`** (monitor.py `live_once`, a loop of the monitor Deployment every 10 s, not a panel, so it
never holds `/readyz` up): reads YYC AI's operator snapshot, hermit `GET /ops/snapshot?tz=Asia/Hong_Kong` (the data
of https://ai.yyc-internal.com/ops/; `HERMIT_OPS_URL`, by default `http://hermit-bridge.yyc.svc.cluster.local/...`:
hermit runs on the new Talos cluster and is reached through bridge B4 until the monitor moves there, yyc-ai/refactor
DESIGN 15.5; bearer `HERMIT_OPS_TOKEN` = hermit's monitor pair token, sealed into `yyc/monitor-keys`; NetworkPolicy
`hermit-from-bridge` in yyc-ai/deploy/bridges.yaml admits only the bridge's address on :8080), slims it, adds the health
part, and writes kv `live:sys` (a few KB; kv `monitor:data` is 1.3 MB, too big to read every 3 s). The activity is the
difference from the previous snapshot (cursor kv `live:ai:cursor`: per-person and per-model call counts of the HK day,
the newest sandbox start/release, vm_event and egress error seen, the status), inserted as events `ai` in one
transaction with the cursor; a first snapshot only sets the cursor. Only the holder of the lock `live ai` writes (a
rollout's two pods: one). The nodes come from ClusterRole `yyc-monitor-nodes-read` (deploy/apps/monitor.yaml).
Facts only, like the monitor: no headroom or projections; the YYC AI budget is shown as used of its limit because
YYC AI refuses model calls when it is used up.

**`events`** (corpus.SCHEMA; events.py): append-only (id, at, kind, post_id, data JSONB), indexes (kind, id) and (at),
kept 3 days (the writer deletes older rows at most every 10 min). Never in the pipeline's way: `events.emit` only
queues in memory; a daemon thread writes the queue every second in batches on its own pooled connection, and at
exit for at most 5 s; a failed write is logged and dropped, a full queue (20,000) drops new events. Payload strings
are cut to 700 characters; titles and sources are joined at read time.
Tests: `tests/test_live_dashboard.py` (events.py, every emit, YYC AI's activity rounds, the read in Node, the query
plans), `tests/test_live_dashboard_page.py` (the page in Chromium: Deyao only, the fixed px boxes and card heights of
both halves (and the same boxes in a window of another size), no panel crossing the seam, one window at 1440 x 830, a
phone at 390 x 844, every card and the YYC AI and Systems areas, one-at-a-time inserts with no motion and "+N behind"
on a burst, staleness, F, wake lock; `LIVE_PG=<postgres url>` renders live data for screenshots).

## The system health page (`/app/health`; -HealthPage.tsx, lib/healthFit.ts, health.css; Deyao, 2026-11-17)

The systems half of the live dashboard on one phone screen, for Deyao's iPhone app "YYC Systems" (DE0CH/yyc-health-ios: a
WebView of this page). Layout E of the mocks he picked: two columns (one is "too wide"), Jarvis 4's sessions and the
cluster on the left (CronJobs, Deployments, nodes, YYC AI's pods), YYC AI, the providers' own figures, the sandbox hosts,
Postgres, WiseSearch and the object storage on the right; no summary on top. Deyao only (gates.ts, 404 to anyone else),
English, read only (no link, button or input), dark, the wall's pixel font and state colours (health.css).
- **The screen decides the detail** (Deyao: "the screen space should inform the level of details that can be shown";
  lib/healthFit.ts `fit`): every line is a fixed 14 px row with a level. Level 0 (each check, provider, host, session and
  everything lit) always shows; levels 1-3 (what a busy session does, OpenRouter's keys, each AWS host, the pods, who has a
  sandbox, the nodes, …) go in level by level while they fit the column, a group whole or not at all. When level 0 alone
  doesn't fit, the oldest idle sessions fold into "+N idle, longest X" (`jarvisFold`); past that, "N more lines".
- **Colours** (Deyao, 2026-11-17: "dark cockpit principle broken", "colors dont preserve meaning"): one mapping,
  `lib/healthStates.ts`, which every line takes its state from; the wall's rule ("State colours" above) and the house
  style's shapes. Ask "who is stopped right now?".

  | State | Hue | Marker | Lines |
  |---|---|---|---|
  | lost | red | red block with a cross | a Deployment with no pod ready (not the monitor), a late CronJob, the gateway or a host with someone's sandbox not answering, a provider stop, WiseSearch logged out or down, YYC AI "problem" and its red findings (one person's sandbox stuck), the budget used up |
  | caution | amber | amber block | some pods short with others serving, the monitor with none ready, one failed CronJob run, a node not ready or under pressure, an empty host not answering, Postgres at 80% of its connections or under its free-space floor, WAL archive or base backup failing, a few failed WiseSearch calls, the last 15 minutes' errors (over), YYC AI "needs a look"; Jarvis 4 needs you |
  | caution, word only | amber | none | an idle Jarvis 4 session (amber, as on the wall; no block, the block is for what needs him or is wrong) |
  | blind | magenta | crossed eye | a read that failed (a provider, Postgres, nodes, a bucket, YYC AI's snapshot), "no update since" a minute on, YYC AI "can't see" |
  | advisory | blue | blue outline | a host booting, a sandbox starting, YYC AI "warming up", a rollout; Jarvis 4 background, scheduled, booting |
  | working | ink | ink block | a Jarvis 4 session's turn running |
  | normal | none | none | everything else and what has recovered: quiet ink, no green (dark cockpit) |
- **On the pixel:** whole-px sizes, line heights and boxes, the columns split in whole px (`columns`); the site's English
  font rule leaves `.hp` alone (house.css), or the pixel face is replaced and every line lands on a half pixel.
- **Data:** `/app/health/data` every 3 s: kv live:sys and Jarvis 4's last push, nothing else. A read failing for 30 s
  greys the page out with "No data since"; live:sys or the Jarvis push older than a minute puts "no update since" in
  magenta in those headings.
- **Updates:** `/app/health` is the wall's frame shell (`FrameShell`): a new build's frame replaces the old once drawn,
  the WebView never reloads.
- **The app's sign-in:** `/app/health/app-signin?state=…` (Deyao only): the app opens it in the system sign-in sheet,
  Access signs him in, and the page sends the sheet to `dev.de0ch.yycsystems://signed-in?state=…#token=<the Access
  token>`; the app keeps the token in its Keychain and gives the WebView it as the `CF_Authorization` cookie.
- Tests: `tests/test_health_page.py` (Deyao only and the sign-in, the iPhone 17 layout on whole pixels with every lit
  state and no normal marker, detail following the window's height, the fitting in Node, an old part, the shell, the
  colour mapping rule by rule).

## Reimbursement evidence (from the providers)
- **OpenRouter:** `infra/cost-report.py <YYYY-MM> <outdir>` (needs a management key in `OPENROUTER_MGMT`) pulls
  OpenRouter's own activity records for the yyc-stream key into a CSV and a one-page HTML summary (print to PDF).
  Run it early the following month (the activity API covers about 30 days). The same data is on openrouter.ai →
  Activity, filtered to the key, with its CSV export.
- **IPRoyal (every proxied job):** the IPRoyal dashboard's usage for sub-user `yycstream` (screenshot), times the per-GB
  price on the IPRoyal order invoice.
- **TikHub:** the dashboard at user.tikhub.io (overview: balance and spend; Order History: top-up receipts) for
  the YYC account chendeyao.uk@gmail.com, used only by this server.
- **Email:** the interim sender bot@deyaochen.com is Deyao's flat-rate Fastmail mailbox, so YYC has no email cost to
  claim yet. Once YYC's own sender (a free transactional-email account used only for YYC) is set up, its dashboard's
  usage/logs page is the evidence.
- **Hetzner:** the monthly invoice PDF from Hetzner (accounts.hetzner.com → Invoices) lists project `yyc` on its own.

Run locally: the poller `OPENROUTER_API=… DATABASE_URL=… DATA_DIR=./data python3 app.py`; the AI server
`DATABASE_URL=… gunicorn app:application`; the site: `web-app/README.md`.


## WiseSearch 轉接 (`/app/wisers/`; the relay `wisers/relay.py`)

The full WiseSearch, read-only, on YYC's WisersOne account (慧科): searching, narrowing, reading and downloading as
WisersOne's own WiseSearch does (Deyao, 2026-10-13; the full feature set since 2026-10-29: "only the read-only endpoints,
do not relay the write endpoints (e.g. bookmarks) … do not relay tracking components"). **The account is dedicated to
the relay** (company decision, Deyao 2026-10-21): no one else signs in to it, and everything that uses Wisers (the
pages, yyc-mcp's `wisers_*` tools, YYC AI through yyc-mcp) goes through the relay, which holds the session long-term.
The pages are the edge app's (`web-app/src/routes/-wisers/`; NAVIGATION.md "WiseSearch 轉接"); the relay is Deployment
`wisers`: one headless Chrome holding the WisersOne session, behind a JSON API at `/app/wisers/api/` (the relay's
docstring lists it).
- **Only WiseSearch's read-only calls** (`wisers/paths.py`): every call the relay may make is a row (the path, the
  resource, the `method` param exactly as WisersOne's app sends it, what it is, what it costs); anything else is refused
  before it leaves (`tests/test_wisers_full.py` checks every row is a path of WisersOne's own bundle,
  `tests/fixtures/wisers_paths.txt`). Not relayed: writes (folders, bookmarks, tags, saved searches, default searches,
  the thesaurus, saved highlights, the content language), e-mail, the account's saved data (histories, saved searches,
  my media), and the calls that read the account's one shared "current search" (`getDocumentByIndex`, `browseArticle`,
  `getCurrentDocument`, `printDocuments`, `isCurrentDocHasImg`): previous/next, browse and print work from the job's own
  items, downloads always name their documents. `createSearchHistoryFlag` is always false (it was true on page 1 until
  2026-10-29, which wrote YYC's searches into the account's history).
- **No tracking:** the relay's browser aborts WisersOne's tag manager and analytics hosts (`paths.TRACKING_HOSTS`;
  GTM-5VC7489 on its home page; counted on `/stats` as `tracking_blocked`); `search/writeClickLog` and event tracking
  are not relayed. `search/writeClickLogByClickType {clickType: img}` is: its answer books the image quota, as the app
  does it, once per document.
- **Calls run side by side** (at most 6, one search at a time): a call starts a fetch inside the page and the worker
  collects the answers, so a long search no longer holds up a document or a label tree. An expired session lets the
  calls in flight end, logs in once and sends the refused calls again. Images and downloads are fetched with the
  session's cookies outside the page (`nomedia.PICTURES`).
- **What costs quota and the logs:** a search (search quota), a document opened from Wisers (click quota 60003), a
  document's images (60004, booked once per document for everyone, the files kept in its store), a download (download
  quota). The calls scoped to a search (tab counts, the filter panel, hot keywords, similar, excerpts, merge counts) have
  an unknown cost: made only when someone opens them, cached per search, logged with charged "unknown". Searches are
  lines of `searches.jsonl`, every other Wisers call a line of `calls.jsonl` (who, why, what, where a document's text
  came from, the outcome); the page 紀錄 and `api/log`, `api/log/calls` show them, `/stats` counts them. Service callers
  (yyc-mcp) are capped per person and hour (`WISERS_AI_CAP`, default 30 searches and 60 documents).
- **Full text from YYC's corpus first** (Deyao, 2026-10-29: "we have the full text in our own database? for the ones we
  don't, log it"; `wisers/fulltext.py`): a document is looked up in `posts` as the read-only role `yyc_edge`
  (`WISERS_PG_*`): by URL (`url_key`, `url`, `canonical_url`) for web items, by the same headline within a day of the
  item's date (and the same outlet when several match) for print; used only when the text is not title-only and has at
  least 60% of Wisers' word count. Then a document already read from Wisers (`docs/`, free), then Wisers. The reader
  looks the same either way (the heading always from Wisers' search item, `docindex/`), its footer names the source;
  a video or audio item always goes to Wisers. The log page lists, by outlet, what Wisers had that the corpus did not.
- **A search is a job** (Deyao, 2026-10-20: "requests time out too fast"): the search used to be one request that the
  relay cut at 55 s and the page called a connection problem at 15 s, while a real search took 4–36 s (searches.jsonl,
  13–16 Oct; the 16 Oct 12:13 search hit the 55 s cut) and a bigger one, or one behind a login, takes longer.
  `api/search` starts the search (or joins the same one already running, or answers from its saved response of the
  last 24 h, `fresh=1` to search again) and answers within 20 s; the page reads `api/job/<id>` (20 s each) until it
  ends, so no request comes near the tunnel's 100 s. One Wisers call waits at most WISERS_SEARCH_SECS (240). Jobs are
  in its store (`jobs/`, Postgres), so in a rollout the new pod answers for a search the old one finishes while draining
  (terminationGracePeriodSeconds 330). `tests/test_wisers_jobs.py` (a 90 s fake search end to end).
- **Every search costs the account quota (Deyao, 2026-10-20), so nothing repeats one by itself.** A search that timed
  out, or whose connection broke after it was sent, may have been counted by Wisers and ends as an error (the page:
  再搜尋一次, "會再用一次 Wisers 額度"). Only a call Wisers refused because the session was over runs again, once, after
  the login: the API's redirect to the login page (seen with fetch `redirect: "manual"`) or its E4000x codes ("System
  does not have the login record, please re-login"); a refused call was not run. The page never searches again on its
  own either: a lost connection reads the same job again (free). WisersOne shows no search quota or counter: the
  account settings have none, and its app's code checks a "click quota" only when an article is opened, printed,
  downloaded or e-mailed (codes 60001–60009, "Insufficient click quota of your group account" …; read from WisersOne's
  page and `main.*.js`, 2026-10-20), which the relay never does.
- **搜尋紀錄** (`/app/wisers/log`, `api/log`): every search with its time (HKT), who ran it (the Access email; tests and
  checks by Claude carry `reason=` "Claude test: …"), the reason, the keyword and options, Wisers' count, the time taken,
  the outcome and whether it may have cost quota; answers from saved responses are listed but not counted. Today's and
  this month's count are also on the monitor (panel `wisers`, read from the relay's stats port 8898, counts only).
  Lines before 2026-10-20 (UTC, no zone) are shown in HKT and completed from their saved responses; saved responses
  with no line are added.
- **One session per user** (tested 2026-10-16: a login ends the account's older session). The relay is the account's
  only user, so its logins end no one else's session; nothing in the relay logs out or hands the session back.
- **Expired session → the relay logs in by itself** (Deyao, 2026-10-16; kept 2026-10-21): on the login page (opening the page, or a
  call Wisers refused: a redirect to its login page, or E4000x), it first
  reopens from the saved session (`state.json` in its store) if that is newer, else logs in with WISERS_GROUP_ID / WISERS_USER_ID /
  WISERS_PASSWORD and 2Captcha for the image captcha (`wisers/login.py`; with no TWOCAPTCHA_API in Secret `wisers`
  the login fails with that message and the page says so), saves the session and runs the refused call once more. Logins
  are at least 60 s apart (WISERS_LOGIN_GAP). When that login fails the relay is **logged out**: every search answers
  at once 「Wisers 登入已失效，自動重新登入亦失敗，請等候管理員重新登入（已通知 Deyao）。」 (YYC AI's tool passes the same
  message on; the search log records outcome `logged_out`, not charged: Wisers refused it), and the next call after the
  gap tries one login again.
- **Session state** (Deyao, 2026-10-21): the relay records held / logged_out, since when, the last call Wisers answered
  and the last automatic login (time, ok, error) in `session.json` in its store (it opens the saved session as soon as
  it holds the leader lock: WisersOne's home page, no search, no login while the session works) and serves it on the stats port
  (`/stats` → `session`); the monitor's WiseSearch panel shows it, and the monitor Discords Deyao (`yyc-alerts`, its
  usual alert: once when it goes, once when it is back) when the relay is logged out. Nothing else happens on its own.
- **Taking it back by hand** (Deyao): `kubectl -n yyc exec deploy/wisers -- python3 /app/wisers/login.py /tmp/login`
  checks the saved session (no login while it still works), else logs in with the stored credentials + 2Captcha and
  saves the session (`state.json` in the store); the running relay opens it without a restart (on its next
  call, or within seconds when it is logged out) and the panel shows "session held". A login.py login ends the relay's
  current session, which then reopens from it (no second login).
- **Deploys hand the session over, no gap** (Deyao, 2026-10-16): RollingUpdate maxSurge 1 / maxUnavailable 0; a leader
  lock (a Postgres advisory lock) lets one browser drive Wisers; the new pod is ready at once and queues; the old one drains
  on SIGTERM (readiness 503, 5 s of serving, its queue), saves the session and releases the lock; the new one opens the
  saved session, no login. Requests that arrive during the handover wait (measured 2026-10-16 on a deliberate restart:
  102 requests, none failed; the slowest, 28 s, waited 5 s for the lock and then a cold open of WisersOne's home page).
- Tests: `tests/test_wisers_full.py`, `tests/test_wisers_jobs.py`, `tests/test_wisers_relogin.py`, `tests/test_wisers_rollout.py`,
  `tests/test_wisers_access.py`, `tests/test_wisers_page.py` (all against `tests/fake_wisers.py`: `slow90…` takes 90 s, `drop…` breaks the connection
  after the search, `relog…` answers E40001 once).

## Speed: router, preload, streamed AI

- **Router** = Cloudflare Worker `yyc-edge` on `newswatch.yyc-internal.com/*` and `yyc-beta.deyaochen.com/*` (`edge/worker.js`,
  deploy `edge/deploy.sh`): everything goes to the edge app (Worker `yyc-web`) except the cluster's paths: the AI
  server's (POST /app/ai/*, /api/ai/estimate, /app/api/v1/*,
  /app/login, /healthz) and /app/wisers/api/ (the WiseSearch relay; its page is the app's). Header `X-Edge: APP` on what the app answered. Access runs before
  the Worker.
- **Preload:** the edge app's links preload on intent (hover 75 ms, touchstart; `web-app/README.md`).
- **Streamed AI:** the persona note answers as NDJSON (`{"t":"delta"}`… then `{"t":"done", …}`), sent by `web.Stream` through gunicorn (`X-Accel-Buffering: no`).
- **Two sites, one app (feature flags; Deyao, 2026-10-13):** https://newswatch.yyc-internal.com/ is the simple monitor (串流 +
  數據分析); https://yyc-beta.deyaochen.com/ (its own Access app, the whole host: YYC's admins, the
  one access catalogue) has every section. flags.json (flagd) defines `search` and `reports`, each off by
  default and on for the beta Host (targeting on `host`), and `ai`, the user-initiated AI features, OFF on both sites
  (Deyao, 2026-10-14): the dashboard's persona button and AI daily summary (/app/ai, /api/ai).
  Background AI (scoring, tagging, the daily reports job and its summaries and themes) has no flag.
  The edge app answers 404 for an off section (`web-app/src/server/gates.ts` GATES) and its shell hides it (the flags
  per host come from kv `edge:config`, which the poller publishes from flagd: `edge_config.py`); the AI server's own
  `web.GATES` keep its AI routes off where `ai` is. The AI data API (/app/api/v1) isn't flagged.
  A new section = a flag in flags.json + flags.FLAGS, a GATES line in gates.ts (and web.py for an AI route), and the
  shell/page checks.
- **Data API for AI (tools/, docs/agent-platform.md):** one tool registry (`tools/__init__.py`: function + JSON Schema
  + a description written for a model; argument checks with fixable messages; answers capped) used two ways: REST
  `GET|POST /app/api/v1/<tool>` for approved accounts with `/app/api/v1/openapi.json` (OpenAPI 3.1, generated), and
  the MCP server. Free tools (`tools/data.py`):
  profiles_list, posts_search (paged, concise/detailed), posts_get, stats_overview, topics_hot, authors_top,
  sources_top, trend, reports_daily, wisers_results (the WiseSearch log), social_results (stats_overview,
  authors_top, sources_top and trend carry the selection-bias note: "Figures that depend on our selection"). Paid tools
  (`tools/live.py`) go through `tools/spend.py`: each call's cost is recorded against the Access account (`infra/control.py
  tool-spend`); no per-account limit. Every post, topic, author, source and stream in an answer carries `dashboard_url`
  (Deyao, 2026-10-21), its absolute details page on the site from `dashboard_url.py` (twin of yyc-mcp's
  `web-app/src/lib/dashboard_url.ts`; table in `mcp-edge/README.md`; `tests/test_dashboard_url.py`); `item` is the
  relative path.
- **"What the AI read":** the daily summary and the persona note keep the posts the model was shown as a context
  record (kv `ctx:<id>`, `insights.make_context`: ids, total, and a plain line such as 「AI 讀了其中 120 篇（共 412
  篇，最新的優先）」). Under each that line opens the post list (the edge app's server/dashboard.ts, posts the summary
  links to marked 「AI 引用」). Users never see the words "context window".

## Backlogs

Deyao, 2026-10-27: "by default, backlog should not be cleared at all (at any stage of the pipeline), only a manual
trigger should process them"; each stage on its own, after any stop of it. `corpus.py` "Backlog",
`tests/test_backlog.py`, `tests/test_cutoff_work.py`.

- **Stages** (`corpus.STAGES`): scoring (stage 1 and stage 2), titles, follow,
  links, comments, fulltext (the 30-minute job, fulltext_now, YouTube descriptions), cache, archive (paywall.py
  archive), transcription, pictures, tiktok, telegram, whatsapp. Each has its own start point (kv `cutoff:<stage>`,
  never before `corpus.FLOOR`, 20 Oct 09:15 HKT) and works only on posts seen from it on that are not backlog posts.
- **Its start point moves to now by itself** when the stage starts a round after not working (`corpus.stage`): no
  finished round for longer than its gap (45 min for the poller's threads and the Deployments, which outlasts a
  rollout's drain; 75 min for the 30-minute CronJobs: paused, suspended, crashing), or it said it was blocked
  (`corpus.stage_blocked`: its flag off, OpenRouter's limit (`ai.limit_hit`), the proxy out of traffic (`resi.mark`)).
  The senders (Telegram, WhatsApp) back after a stop move their cursors to the newest item: nothing filed before is sent.
- **Backlog posts** (`posts.first_run`): a source's first read (a new sid, a new page or account), and a source's
  first read after more than 3 hours without one (`corpus.COLLECT_GAP`, table `sid_reads`: the stream back from a
  pause, a collector that was failing). They stay in the corpus, 搜尋 and 數據分析; no stage works on them, the scorer
  included, and they are never filed or sent.
- **The manual trigger:** `infra/backlog.py` shows each stage's start point, why it moved, its last round, whether it
  is blocked and what it skips; `infra/backlog.py run STAGE|all --from TIME [--first-run]` lets the stage work from
  TIME on (the backlog posts of that time too with `--first-run`) until its next stop. The score backfill (posts older
  than 2 days when they came in) is its own switch, off by default: `infra/thresholds.py backfill on|off`.

## Outages

- **17–20 Oct 2026** (Deyao, 2026-10-20: "we are going to accept two missing days … just start watching and
  capturing from this point, ignore the past"): OpenRouter key `yyc-stream` hit its cap at 17 Oct 05:51 HKT (no
  scoring after that); the cluster was off 19 Oct 22:42 → 20 Oct 09:15 HKT. Posts seen before 20 Oct 09:15 HKT
  (`corpus.FLOOR`, the first start point of every stage: "Backlogs") are never scored live (first or second scoring), and no comments are read for them
  (social_twopass, twopass.due_for_fetch); kv `scoring`.from_id was raised to the last post
  seen before then (6428210; `from_id_was` keeps the old value). The history backfill is off (kv `score_backfill`.on
  false, its progress kept). Those posts stay in the corpus, 搜尋 and 數據分析 as unscored posts. 53 gap posts were
  filed (and sent) by the restarted scorer before the stop; per "nothing is recalled" they stay.
  No job spends work on them either, and no monitor count shows them as waiting (Deyao, 2026-10-20: "ignore the
  past"; `corpus.seen_cond` / `media_cond`, tests/test_cutoff_work.py): the full-text job (candidates, growing-page
  rereads, YouTube descriptions, `refill`/`retry` never before the cutoff), the cache and archive.today jobs, link
  reads, the clip and picture queues (also the YouTube-subtitles pass), AI titles,
  and the item page's 正在收集內文 (`will_read`, edge config `fulltext.from_seen`). The monitor's queues count only
  posts in each stage's work, and the History scoring card is absent while the backfill is off. Forum crawlers still read new
  replies on older threads (new content; those threads are never scored again).
