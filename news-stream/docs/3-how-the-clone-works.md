# How the clone gets and searches data

Read from the code in `YYC/news-stream/` on 2026-10-13, before the daily-search change above. Section 1h and the Galbot section are updated for that change. The live TikHub status (HTTP 402 on every platform) is from the public feed.json on 2026-10-13.

## Big picture

- One Python process, `app.py`, polls every 5 minutes (`POLL_SECONDS`, app.py:28, loop at app.py:256). Each poll reads the HTTP sources directly and also reads the documents the separate jobs write (browser.json, instagram.json, tikhub.json, facebook.json; documents in Postgres, `corpus.file_get`).
- Two stores:
  - **stream_items + poll_seen** (Postgres): the alert stream only, meaning items that matched a stream's keywords and passed the AI check, filed by HKT day, and every URL a poll has read.
  - **posts** (Postgres): every post any collector fetched, plus comments and forum replies stored as rows of their own. The dashboard, profiles, reports and agent all read this.
- Every timer is defined in `infra/provision.sh`. The server pulls main every 5 min (provision.sh:361-366), so a push to main deploys.

## 1. Ingestion

### 1a. Direct feeds and per-site parsers (sources.py)
- **How it works:** stdlib `urllib` with a Chrome User-Agent (sources.py:16-27). There is one parser per site: RSS, regex over HTML, or the site's own JSON API (hk01, TVB, NOW, 881903, LIHKG). Parsers are at sources.py:55-169, the list at sources.py:172-243.
- **Count:** `SOURCES` has 68 entries. The README still says 48 because it predates the YouTube, Telegram and LIHKG additions.
  - 16 YouTube channel pages (`/videos` and `/streams`), parsed from `ytInitialData`.
  - 4 Telegram public channel pages (`t.me/s/…`).
  - LIHKG 時事台 newest threads (sources.py:242).
  - Google News `site:discuss.com.hk` (sources.py:221).
  - The rest are HK news, radio/TV and government feeds.
- **Schedule:** every 5 min, 12 threads in parallel, each source retried once (sources.py:480-499). Exceptions (`EVERY`, sources.py:473-474): the HKEJ daily pages run every 30 min and HKEJ instant news every 10 min.
- **Proxy:** only HKEJ (5 entries) goes through `RESI_PROXY` = IPRoyal HK residential, sub-user **yycstream** (sources.py:181-185, 429). Everything else connects directly.
- **Streams:** all of them, by keyword match.

### 1b. Extra media list (sources_extra.py)
- **What it covers:** 22 entries in `EXTRA` (sources_extra.py:265-301; the README says 23):
  - The Standard (sitemap), 香港仔 (e-paper API), 鳳凰網 港澳
  - The magazines: 號外, Asia Times, 紫荊, 資本, 經濟一週, 經濟導報 ×2, iMoney ×2, 明週
  - FSTB's LinkedIn, its logged-out company page read directly (then the ISP IP, then r.jina.ai: `sources_extra.linkedin_read`)
  - The 8 political columns that feed the POL stream
- **Proxy:** 號外 and the two HKEJ columns use yycstream.
- **Known gap (bug):** each item carries `max_age_days` (10 or 40 days for magazines, sources.py:278-279), but `app.py` never reads it. The stream drops anything published more than 2 days ago (`MAX_AGE`, app.py:31, 203). So in practice the README's "a magazine's latest issue counts as current" doesn't happen for the stream. Those items still reach the corpus.

### 1c. Google News search
- **How it works:** one RSS feed per stream `news_queries` entry, `q=<query>+when:1d`, hl=zh-HK (sources.py:357-360, built at app.py:46). 14 queries today: CEHK 3, HKIC 4, FSHK 2, STABLE 2, GALBOT 3. Every 5 min, no proxy.
- **Link decoding:** a matched item's news.google.com link is decoded to the publisher's URL with Google's batchexecute call (app.py:56-79).
- **Corpus:** raw Google News results don't go in; only decoded items that matched a stream do (insights.py:234-235).

### 1d. Real-browser job (browser_fetch.py)
- **Sites:** 成報, 亞洲週刊, 明報月刊, all behind Cloudflare's managed challenge.
- **How it works:** Patchright drives Google Chrome under Xvfb with a persistent profile and clicks the challenge checkbox (browser_fetch.py:21-37, 92-93). It writes browser.json. If that file is more than 2 h old, sources.py marks every site in it as failed (sources.py:282-299).
- **Schedule:** `yyc-browser` timer, every 30 min (provision.sh:112-117).
- **Proxy:** none (the server IP passes); `BROWSER_PROXY` is optional.
- **Limitation:** 成報 lists have no dates, so the job keeps the newest 150 article IDs (browser_fetch.py:18, 66).

### 1e. 香港討論區 keyword search
- **How it works:** `discuss_search` reads the mobile site's search, newest first, one page per query (sources.py:249-274).
- **Queries:** the streams' `discuss_queries`, 10 in total.
- **Schedule:** every 30 min, inside the app loop (`DISCUSS_SECONDS`, app.py:29, 184-191).
- **Proxy:** yycstream (the site bans datacenter IPs).
- **Limitation:** results carry no dates, so threads more than 1,800 IDs (about 2 days) behind the newest are dropped (sources.py:246, 274).

### 1f. Facebook pages (fb_fetch.py)
- **How it works:** it replays Facebook's own logged-out GraphQL timeline request, `ProfileCometTimelineFeedRefetchQuery`, with curl_cffi impersonating Chrome (fb_fetch.py:244-268).
  - Each request asks for posts after the newest one already seen (`afterTime`) and pages back with the cursor, 3 posts per answer, up to 10 answers per page per run. Anything beyond that is saved as a gap and read on later runs (fb_fetch.py:44, 270-334).
  - A browser session (Patchright) is captured on the same proxy only when Facebook refuses the one held: the busiest page's newest post doesn't come back, or three error answers in a run; a page with no public timeline doesn't count (fb_fetch.py `_poll`, `NoTimeline`).
- **Count:** 147 followed pages (the facebook rows of watchlist.json dated 2026-10-12): 87 legislators, 26 media, 23 government, 7 parties, 4 commentators.
- **Schedule:** CronJob `facebook` every 15 min; every page is checked on every run, reading back `OVERLAP_H` (3 h) before its newest post once an hour and once a day `DEEP_H` (26 h), so posts that appear behind a newer one come in; a post id already read is never filed again. Each run is limited to 300 s.
- **Proxy:** IPRoyal sub-user **yycfacebook** (`FB_PROXY`), used for Facebook only.
- **Limitations:**
  - It follows a fixed page list only. Facebook keyword search is separate: AnyAPI, for Galbot only (section 1h).
  - Matching against streams uses only the first 160 characters of the post text as the title (fb_fetch.py:342; see section 2).
- **Doc mismatch:** the README says both "Facebook pages … every 30 min" and, a few lines later, "so Facebook is not watched". The code and timer show it is watched. The `facebook_file` docstring still says "fb_apify.py (Apify)" (sources.py:346), which is stale.

### 1g. Instagram and Threads profiles (ig_fetch.py)
- **How it works:** fetches the logged-out profile page with curl_cffi and reads the 12 newest posts from `polaris_ordered_timeline_connection`. Post times come from the media-ID epoch (ig_fetch.py:28-54). The download stops right after the script that holds the posts, which saves bytes (ig_fetch.py:84-97). Threads profiles are parsed via `taken_at` (ig_fetch.py:57-81).
- **Count:** 70 accounts: 69 Instagram and 1 Threads (passiontimes). 6 are checked hourly (am730hk, tang_earthquake, weather.hk, rthk.vnews, icable.news, passiontimes) and 64 every 3 h.
- **Schedule:** `yyc-instagram` hourly (provision.sh:239-244).
- **Proxy:** IPRoyal sub-user **yycinstagram** (`IG_PROXY`). The intervals are set to fit a USD 70/month proxy limit (ig_fetch.py:14-15).
- **Limitation:** profiles only, no keyword search. Keyword search on Instagram goes through TikHub hashtags (section 1h).

### 1h. Social keyword search: TikHub + AnyAPI (tikhub_fetch.py, fb_search.py)
- **How it works:** one REST adapter per platform on api.tikhub.io, sorting newest first where the endpoint allows it. Facebook goes through AnyAPI's `facebook.search_posts` instead.
- **Platforms (17):** 小紅書, 抖音, 微博, B站, 快手, 知乎, 西瓜視頻, 微信公眾號, TikTok, X, YouTube, Reddit, Threads, Lemon8, 皮皮蝦, Instagram hashtags, plus Facebook for streams with `facebook_search` (GALBOT only). 視頻號 is excluded because its results have no link.
- **Queries:** each stream's `social_queries`, which defaults to its `news_queries`. Duplicates are removed, leaving 14 queries.
- **Schedule (since 2026-10-13):** each platform and query once a day. The timer is still hourly and makes no requests when nothing is due. Failed searches are retried after 3 hours. The run-now button on /kmatrix/admin/social runs one stream at once.
- **Caps and filters:**
  - At most 10 posts per query, or 5 on the loose platforms X, Reddit, TikTok, YouTube, Threads and Lemon8, and 20 on Facebook. Posts that contain the query words are sorted first.
  - Posts older than 3 days are dropped. Facebook posts must contain one of the stream's keywords.
  - Instagram turns the query into a hashtag by stripping spaces and punctuation.
- **Known gap:** every TikHub endpoint answered HTTP 402 (no paid balance) on 2026-10-13, so this family brings in nothing until the account is funded. Facebook needs a YYC AnyAPI key on the server.

### 1i. Forum replies (replies_fetch.py)
- **LIHKG:** six boards (時事台, 政事台, 財經台, 上班台, 房屋台, 吹水台; replies_fetch.py:51), listed in latest-reply order, plus LIHKG threads the collectors filed in the last 2 days. Requests go direct, with LIHKG's own headers, at one per second. If LIHKG blocks the server, the job switches to yycstream for 26 h, capped at 60 MB/month (replies_fetch.py:6-12, 59).
- **香港討論區:** five boards (news, finance, 時政文化, 突發報料, 立法會事務; replies_fetch.py:56), plus threads filed by discuss_search or Google News. Goes through yycstream on a sticky session and moves to a new session when it hits a reCAPTCHA. Runs hourly and is paced to `DISCUSS_MB_MONTH` = 450 MB (replies_fetch.py:13-19, 57, 63).
- **Schedule:** the `yyc-replies` timer runs every 30 min (provision.sh:302-307).
- **Where replies go:** each reply is a corpus row with its own timestamp (`corpus.add_comments`, corpus.py:208-233). Replies go to the corpus only, never to the alert stream.

### 1j. Comments (comments_fetch.py readers, called by social_twopass.py)
- Read only for the two-pass scoring: at capture and once for the second scoring at published + 30 min; no later re-checks on any platform (Deyao, 2026-10-20). The `comments` CronJob is suspended.
- **YouTube:** the channels in sources.py, via `youtubei/v1/next`, newest first. Top-level comments only, because YouTube sends no replies to logged-out clients. No proxy.
- **Facebook:** Facebook's own comment query plus replies to comments, over yycstream, the post named by the id fb_fetch records (kv `fbpid:<url>`).
- **Instagram/Threads:** the post page over yycstream; every comment and reply through TikHub when the page shows fewer than the post counts.
- **Gap:** posts found through TikHub never get comments fetched.

### 1k. Article-text cache (cache_fetch.py)
- **What it does:** for posts a profile tagged relevant in the last 3 days that have no text yet, it fetches the article body. It tries ld+json `articleBody` first, then the largest cluster of `<p>` paragraphs. Up to 300 pages per run, 4 requests/s (cache_fetch.py:1-38).
- **Proxy:** HKEJ and discuss.com.hk use yycstream. The three Cloudflare sites are read in one browser pass. Social sites are skipped.
- **Schedule:** `yyc-cache` every 30 min (provision.sh:154-159).
- **Limitation:** the cached text improves the corpus and dashboard but is not used for stream matching, because matching happens before caching.

### 1l. K-Matrix WhatsApp watch (kmatrix/)
- **How it works:** `watch.py` opens Deyao's linked WhatsApp Web in Patchright under Xvfb and reads the three groups: K-Matrix & YYC CEHK, HKIC and FSHK (kmatrix/compare.py:20).
  - It archives every alert, because the groups delete messages after 7 days.
  - `compare.py` gives each alert a verdict against the stream's items and the URLs read: HAVE, OTHER, DROPPED or MISSING, with our lag versus K-Matrix.
  - `kmatrix/watch.py show` prints the report on the server (no web page). `link.py` re-links WhatsApp via a QR when needed.
- **Schedule:** `yyc-kmatrix` every 30 min (provision.sh:198-203).
- **Not a data source:** it only measures parity. There is no Galbot group, so Galbot is never compared.

## 2. Processing

- **Stream matching:** case-insensitive substring match of a stream's `keywords` against **title + section** only (app.py:84-92, 207).
  - POL has no keywords: every post is scored against its brief, whatever its source.
  - Text beyond the title is not checked. For Facebook, Instagram and TikHub posts the title is the first 160 characters.
  - For TikHub items the section is "platform · query" (tikhub_fetch.py:596). So an item found by the query "Galbot" always passes the keyword step, and the AI decides relevance.
- **Dedupe:**
  - By URL across all polls (the `seen` set; app.py:199-201). A URL seen once is never judged again.
  - By normalised headline (first 24 characters, suffixes removed) per stream, so Google News and the direct feed don't double up (app.py:95-97, 180, 206).
  - In the corpus, `url` is UNIQUE. A re-seen post refreshes its engagement counts and fills in missing text or author (corpus.py:186-204).
- **AI tagging:** `ai.tag_posts` sends batches of 30 to OpenRouter with the model `ai.MODEL` (an open-weight model: the account is refused closed-weight ones).
  - It returns relevant (yes/no), one of K-Matrix's 5 sentiments and the 17 labels (ai.py:124-158). Irrelevant items are dropped from the stream (app.py:216-230).
  - It is called on every poll for new stream matches. After each poll, `insights.run_custom` also tags admin-profile matches and the streams' new comment/reply rows from the last 2 days (insights.py:232-272). A thread's comments are tagged as one item per thread per day (insights.py:275-292).
  - If tagging fails, items are kept untagged.
- **`ai.SMART_MODEL` / `ai.REPORT_MODEL`** (DeepSeek V4.1 Flash) handle persona notes and the daily report summary and themes.
- **Storage:** Postgres with tables `posts`, `tags` (per profile and post) and `kv`.
  - Indexes exist only on day, author and thread (corpus.py:24-52). **There is no FTS index.**
  - Each post has a precomputed lowercase `hay` column (title + text + author + source). Comment rows use only their own text + author (corpus.py:161-166).
  - Source type and region are derived by `classify` (corpus.py:87-139).

## 3. Search

- **Query language** (query.py:1-11):
  - Whitespace means OR. AND, OR, NOT and parentheses work.
  - `"phrase"`, `NEARBY/n` (default 10 characters), `FindIn(title|content|author|source: …)`, `IncData(...)`/`ExcData(...)` (by source type, website, sid or platform) and `TopicSearchOnly` (titles only).
- **Engine:** compiled to SQL using `strpos(hay, term) > 0`, i.e. plain substring matching (`query.to_sql`). NEARBY is the SQL function `near()` that `corpus.SCHEMA` creates. Measured 2026-10-13 on 229k posts: 90–330 ms a query (a scan of the date range). Every search is a scan of the date range, filtered only by the day index. There is no tokeniser and no relevance score.
- **Ranking:** newest first (`ORDER BY coalesce(published, seen) DESC`, corpus.py:306). Hot topics group posts by thread URL or by 50% Jaccard similarity of title bigrams (insights.py:356-395) and sort by post count.
- **Time windows:**
  - Profile day range: today, 7, 30 or 90 days, or custom.
  - Dashboard `/api/search`: whole corpus, default 7 days, at most 2,000 hits (web.py:397-403).
  - Admin preview: default 30 days (web.py:471-477).
- **What a profile searches:**
  - Stream profiles query `stream_query` = their keywords as quoted OR terms (query.py:253-260). The dashboard shows only **tagged relevant** posts (`insights.select` uses `tagged_ids`, insights.py:315-323). For streams that means what app.py tagged plus matching comments, not every corpus post containing a keyword.
  - Custom admin profiles are matched over the whole corpus for their period (insights.py:258-259).
- **Agent `/agent` (removed 2026-10-28):** `agent.py` runs a tool loop on SMART_MODEL, at most 8 steps (agent.py:11, 149-164). Its tools:
  - `search_posts`: a profile's tagged posts filtered by query, or a corpus-wide `match_ids` search; default 7 days, at most 150 posts.
  - `hot_topics`, `stats_overview`, `authors_top`, `sources_top`, `trend`, `make_table` (CSV). (The counting tools
    say their figures depend on which sources and keywords YYC follows: README "Figures that depend on our selection".)
  - Answers must cite post URLs.

## 4. Outputs

- **Stream page `/`** plus **`/feed.json?date=YYYY-MM-DD`**: one day's archive across all streams, with each stream's item count and per-source status (app.py:306-325). Items are filed in half-hour slots (app.py:109-113).
- **Dashboard `/dashboard`:** post listing, hot topics, themes, persona, plus Excel exports at `/api/export/{posts,hot_topics,themes}.xlsx`. Also `/post/<id>` (the cached copy) and `/kmatrix/admin` (Deyao only).
- **Daily reports** (`reports.py daily --if-due`, run hourly at :05): at the report hour (default 09:00 HKT, for the previous day) each profile gets format1.html, format2.html, report.pdf, hot_topics.xlsx, posts.xlsx/.docx (in the Galbot sample's table layout) and summary.json, all under `/reports/`. They are not emailed (Deyao, 2026-10-23).

## 5. Costs the repo documents

- **TikHub** (tikhub_fetch.py:35-39; README):
  - USD 0.01 per search on 小紅書, 抖音, 快手 and 微信.
  - USD 0.002 on YouTube, Threads and Instagram.
  - USD 0.001 elsewhere.
  - With 14 queries: USD 10.64 a day (USD 6.72 for 小紅書 + 抖音 hourly, USD 3.92 for the rest), about USD 319 a month before volume discounts.
- **IPRoyal residential:**
  - The 香港討論區 replies budget is 450 MB/month, "≈ USD 3.15 a month at IPRoyal's USD 7/GB" (replies_fetch.py:29).
  - Instagram intervals are set to fit a "USD 70/month proxy limit" (ig_fetch.py:14).
  - Facebook: about 11 KB per check of a page with nothing new, 20–35 KB per new post (fb_fetch.py:7-9).
  - Apify was rejected at USD 0.007 per page per run (about USD 120/month for 12 pages; fb_fetch.py:19).
- **OpenRouter:**
  - Key yyc-stream has a USD 20/month cap (README).
  - Public on-demand AI calls stop at the daily cap, default USD 0.50 (insights.py:67; ai.py:38-45). The admin can set 0 to 20 (insights.py:94).
  - Background tagging, themes and reports don't count toward that cap. Every call's cost is logged per day and per purpose (ai.py:28-35).
- **Hosting:** Hetzner cx23, with a €20 spend warning on the project (README). No per-GB figure is given for the yycfacebook or yycinstagram sub-users.

## 6. GALBOT stream specifically

From streams.json, the GALBOT stream has 17 keywords: Galbot/GALBOT/galbot, 銀河通用/银河通用, 銀河機械人/银河机器人, 銀河太空艙/银河太空舱, Galaxy General, 機械人店長/机器人店长, 機械人商店, 機械人零售店, robot shopkeeper, robot-run store, robot-operated store.

**Where Galbot is actively searched**
- **Google News:** 3 queries, "銀河通用", "Galbot", "機械人店長 香港", each `when:1d`, zh-HK, every 5 min.
- **香港討論區 site search:** 3 queries, "銀河通用", "Galbot", "機械人 便利店", every 30 min over yycstream.
- **TikHub + AnyAPI (since 2026-10-13):** the same 3 news queries on 16 TikHub platforms and on Facebook, once a day, plus the run-now button. Instagram is searched by hashtag: #銀河通用, #galbot and #機械人店長香港. Nothing comes in until the TikHub balance is paid and the AnyAPI key is in place.

**Where Galbot is caught only passively** (a keyword appearing in an item already being collected)
- The 90 entries in SOURCES + EXTRA (news sites, columns, the 16 YouTube and 4 Telegram channels, LIHKG 時事台 new threads), the 3 browser sites, the 147 Facebook pages and the 70 Instagram/Threads accounts, but only when a keyword appears in the **title** (or the first 160 characters of social text).
- Forum replies and comments, matched on the reply or comment text from the last 2 days, then tagged for GALBOT (insights.py:260-261).

**Not covered or weak**
- **Facebook:** keyword search for Galbot only, through AnyAPI (2026-10-13). None of the 147 followed pages is Galbot's own page or a robotics/retail page.
- **Instagram:** no Galbot account among the 70 followed. TikHub hashtags are the only route.
- **Other platforms:** no 今日頭條, LinkedIn or Telegram keyword search, and 視頻號 is excluded.
- **Titles that don't name Galbot:** the 香港討論區 query "機械人 便利店" and similar Google News results only reach the stream if the title contains a GALBOT keyword. For example, "機械人 便利店" in a title does not match "機械人商店". The same goes for any article that names Galbot only in its body, since cached article text is not used for stream matching.
- **No K-Matrix comparison:** K-Matrix's WhatsApp watch has no Galbot group.
- **No comments on TikHub posts:** comments are never fetched for posts found through TikHub (小紅書 etc.).
- **No email delivery:** per the README, CEHK is the only profile with a recipient, and alerts are off by default (insights.py:124-126), so GALBOT's reports are built under `/reports/` but not mailed. The live admin settings are in the server's profiles.json, which I didn't check.
- **Overlap with HKIC:** 銀河通用 is also an HKIC keyword, so the same item can land in both streams.
