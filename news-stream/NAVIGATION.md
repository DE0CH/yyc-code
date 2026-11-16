# Navigation: the app shell and the information architecture (Deyao, 2026-10-13)

Every page of the YYC monitoring site is a route of the edge app (`web-app/`, `web-app/README.md`) and uses one shared
app shell: `web-app/src/shell/Shell.tsx` (the header, the stream picker and the phone tab bar, the section links),
`web-app/src/shell/profile.ts` (the stream shown: `?p=` and the cookie `yyc-profile`) and
`web-app/src/styles/shell.css` (tokens, base type, the top bar, the tab bar). The root route (`src/routes/__root.tsx`)
renders the shell once; a page never builds its own header or section links.

| Page | Route | Source |
|---|---|---|
| 串流 | `/` | `src/routes/index.tsx`, `-StreamPage.tsx` |
| 搜尋 | `/search` | `src/routes/search.tsx`, `-SearchPage.tsx` |
| 數據分析 | `/dashboard` | `src/routes/dashboard.tsx`, `-DashboardPage.tsx` |
| 報告 | `/reports/` | `src/routes/reports.index.tsx`, `-ReportsPage.tsx` |
| item page | `/item/<id>` | `src/routes/item.$id.tsx` (`item.index.tsx`: `/item?url=`), `-ItemPage.tsx` |
| Deyao's monitor | `/app/monitor` | `src/routes/app.monitor.tsx`, `-MonitorPage.tsx` |
| Proof (the proof view) | `/app/proof?sid=…` / `?catalogue=1` / `?stream=ID&above=N` | `src/routes/app.proof.tsx`, `-ProofPage.tsx` (README "The proof view") |
| Deyao's live dashboard | `/app/live?half=left` + `?half=right` (two displays), `/app/live` (one window) | `src/routes/app.live.tsx`, `-LivePage.tsx`, `lib/liveLayout.ts` (README "The live dashboard") |

## Languages: staff pages in Chinese, Deyao's own pages in English (Deyao, 2026-10-21)

"I'm writing code for users, not myself": a page staff use is in Traditional Chinese with Hong Kong usage, for
everyone, Deyao included (staff preference beats his; no per-viewer language). Only pages Deyao alone uses are in
English: the monitor (`/app/monitor`), the live dashboard (`/app/live`), the proof view (`/app/proof`), the data API's call log
(`https://mcp.yyc-internal.com/admin`) and the sources review page (yyc-ai-visibility `/sources/`). On those, proper nouns stay as the
sources write them (明報, 香港討論區, stream names, the staff pages' names 串流 / 數據分析 / 報告), and post content is
never translated. The English pages format dates with `dtEn` (lib/dates.ts: "Tue 20 Oct 14:05"); the shared shell
around the proof view keeps the staff site's Chinese navigation.

**Deyao-only parts of a staff page** ("for pages with elements only i can see, put it in a box labeled that it's
only visible to me"): everything on a staff page that only Deyao sees goes into ONE `<DeyaoOnly>` box
(`src/lib/DeyaoOnly.tsx`): a dashed box labelled "Only visible to you (Deyao)", in English, rendered only when the
signed-in account is his. Today: the item page's box (the post in the proof view). When a page's
Deyao-only part grows too big for one box, it becomes its own page at its own URL (as the monitor and the proof view
are), never a second box. The box hides, it does not guard: data only Deyao may read is checked on the server.

## Sections (the same names, order and icons everywhere)

| # | Section | URL | Shown to | Sub-pages (the page's own sub-tabs) |
|---|---|---|---|---|
| 1 | 串流 | `/` | everyone | the day row (‹ day ›, 今日), search and filters |
| 2 | 搜尋 | `/search` | everyone | none: the query, then filters (date, media type, outlet, region, sort) and the results |
| 3 | 數據分析 | `/dashboard` | everyone | 摘要 · 帖子 · 熱門話題 · 作者 · 來源 · 趨勢 · 主題 · 人物 (`?v=`; none opens 摘要), then the period row |
| 4 | 報告 | `/reports/` | everyone | none |

- **Desktop (≥ 900 px):** one dark top bar: brand "YYC 新聞監察", the four sections as text links with icons, the
  stream picker on the right. The page's sub-tabs sit in a second row of the same bar.
- **Phone (< 900 px):** the top bar keeps the brand "YYC", the stream picker and the page's sub-tabs; the sections
  move to a bottom tab bar (icon + label, four items), padded by
  `env(safe-area-inset-bottom)` (every page's viewport has `viewport-fit=cover`). Pages reserve the bar's height at
  the bottom (`--tabbar-h`).
  The top bar's colour reaches the top of the screen (Deyao, 2026-10-14): iOS 26 Safari sticks it below the status
  bar and address pill, shows the page in that strip and clips fixed/sticky layers to the area below it; it fills the
  strip with a solid colour only from a `position:fixed`, viewport-wide box at the top edge. The shell adds that box
  (`.yyc-topfill`: 6 px of the bar's colour over the bar's top padding, no taps), the bar still paints upward
  (`.yyc-bar::before`, overscroll), and pads its content by `env(safe-area-inset-top)` (a home-screen web app).
- On touch screens the tab bar steps aside while a text field has the keyboard (`yyc-typing`), and comes back only
  after a tap has ended: tapping a send button blurs the field first, and a bar coming back at once moved the button
  out from under the finger (57 px in WebKit at 390 px) and lost the tap.
- **A new deploy reaches open pages, never by reloading them (Deyao, 2026-10-20: "UI updates should be pushed to
  users quite quickly"; 2026-10-22: "new ui shouldn't refresh the page, because refreshing the page makes my live stream
  exit full screen, and shows the loading bar").** Every open page knows of a new build within seconds (the LiveHub
  socket; `/version`) and keeps running as it is: its next link (or Back inside the app) is a full load of the new
  build. A page nobody navigates (the live dashboard) stays on its build. Only a page whose data reads fail with a newer
  build live shows 已有更新 · 點此重新載入 at the bottom (a tap reloads). Documents are never served from a cache without
  asking; old hashed files stay for 14 days and the data reads stay compatible, so a tab on the previous build never
  breaks. web-app/README.md "Updates reach open pages".
- Deyao's monitor (`/app/monitor`, README) is not a section and no page links to it: it is his alone (404 to anyone
  else) and read-only.
- Deyao's live dashboard (`/app/live`, README "The live dashboard") likewise: his alone (404 to anyone else, gates.ts),
  no shell, no links, read-only; a full-window dark canvas (F = full screen).
- The proof view (`/app/proof`, approved accounts, used by Deyao alone) is not a section either: the sources page links into it; it has the shared
  shell with no section marked and no stream picker (`pick="none"`). ← → step through the entries (also the keyboard's
  arrows and a swipe), a replaced URL (`&at=`), not a history entry each, so Back leaves the view.
- No page is wider than the screen (Deyao, 2026-10-14): long English titles, outlet names, commenter names and
  URLs wrap (`overflow-wrap:anywhere`; buttons that carry an outlet name wrap too), never widen the page. The edge
  app's page tests check every page at 390 px (WebKit iPhone) and 1600 px (Chromium): `tests/test_web_app_pages.py`,
  `test_dashboard_spa.py`, `test_search_spa.py`, `test_agent_page.py`, `test_edge_ops.py`.
- Section names are only these four (the AI 助理 was removed, Deyao, 2026-10-28); there is no 設定 section: the site is read-only and Deyao's control plane
  is scripts (`infra/control.py`, `infra/thresholds.py`, `reports.py`; Deyao, 2026-10-14); page titles are "<section> · YYC 新聞監察".

## The stream picker (Deyao, 2026-10-14)

The streams come from the profiles the poller publishes (kv `edge:config`, read by the app's profiles query): the
monitoring streams (every profile is a stream; its posts on
數據分析 are the ones the AI scored at or above the stream's threshold and the ones filed in the stream, README:
Profiles). There are a handful (CEHK,
HKIC, FSHK, 政治花絮, Galbot, 選委選舉, 宏福苑), so the picker shows them all at once instead of a
`<select>` (Deyao: "probably a wrong UI to use a form <select> in the first place"):

- **One real link per stream** (`<a href="…?p=<id>">`, the stream's name; its title on hover; `Shell.tsx`), in the top bar where
  the select was: a single row of rounded chips that scrolls sideways and never wraps, the right edge fading so a
  cut-off chip shows there is more (Material 3's scrollable tabs for touch screens). The current stream is filled and
  marked `aria-current="page"`, and is scrolled into the row's view on every change. From 960 px the row sits
  right-aligned in the bar; on 串流's desktop the page's side list of streams (also links) is the picker and the bar's
  row is hidden (`data-pick="side"`).
- **The URL is the single source of truth.** The stream shown is `?p=` when the URL has one; the cookie
  `yyc-profile` (the last stream shown; `src/lib/remembered.ts`) is only the default for a URL without it. It is a
  cookie so the server renders a URL without `?p=` with the same stream the browser shows: the first render reads it
  from the root route's loader (the server's reading), never from the browser's own storage. Why (2026-10-20): it was
  localStorage, the server picked another stream, and the item page of a post matched to several streams failed
  hydration. Anything else a first render depends on comes from what the server also sees; the clock while hydrating
  is the server's (`src/lib/clock.ts`). Pages write the stream they show
  into their URL (串流 always, so `/` becomes `/?p=<id>` with `replaceState`), so Back returns to the stream shown at
  that entry; and changing the day (串流 `?d=`) or the period (數據分析) only changes those params, never the stream.
- **Picking a stream is a new history entry** (`pushState`, no reload): Back and forward step through the streams
  picked. A stream link keeps the page's other params (串流's day, 數據分析's view and period), except the drill-down
  params that belong to one stream (`data-pick-drop`: 數據分析's `tid page post`). Ctrl/⌘-click opens the link in a new
  tab.
- **Links into the site carry their stream:** Telegram's 詳情 (`/item/<id>?p=<the channel's stream>`), 串流's cards
  (`/item?p=<stream>&url=…`, kept through the 302), the item page's 數據分析 links. An item link without `?p=` marks
  the remembered stream when it kept or matched the post, else the first stream that kept it, else the first
  profile it matched (`data-pick="page"`; already in the server's HTML), and writes it into the URL.
- Every section link carries the stream shown (`?p=`), so it stays selected when moving between pages.
- The picker follows the router: the stream is a typed search param (`?p=`) of every route (`src/shell/profile.ts`),
  so a URL change re-marks it, and a page's own stream links are made the same way.
- Why (Deyao's bugs on his phone, 2026-10-14): a Telegram 詳情 link opened the item page with the remembered stream
  marked instead of the channel's (the link had no `?p=`); 串流 without `?p=` in its URL showed the remembered stream,
  so Back after a pick could land on a URL that no longer said which stream it showed.

| Page | What the picker controls |
|---|---|
| 串流 | the stream shown. The desktop side list of streams is the same selection. |
| 搜尋 | nothing: search covers the whole corpus, not one profile, so the page has no picker (`data-pick="none"`); the section links still carry the remembered `?p=`. |
| 數據分析 | the profile of every view (`?p=`). |
| 項目頁 (item) | which profile 在數據分析開啟 and 同一媒體/同一作者 use. |
| 報告 | which profile's reports are listed; a "全部設定檔" switch shows every profile's reports. |

## Dates and times: one format everywhere (Deyao, 2026-10-20)

"Why do some pages say (週二) but some don't? They don't use the same template? They should." Every date and time a
reader sees comes from one formatter: `web-app/src/lib/dates.ts` on the site and the MCP server's pages, its Python
twin `datefmt.py` for reports, Discord/alert messages and the sentences the server writes (「最遲 …」). Always
Hong Kong time, Traditional Chinese with Hong Kong usage; the weekday is 「星期二」 (YYC/CLAUDE.md: 星期五), never 周/週.
Deyao's English pages (above) use the same named formats in English, `dtEn` ("Tue 20 Oct 14:05", another year "Mon 20
Oct 2025"); the monitor's times are these.

| Format | Example (seen in 2026) | Used for |
|---|---|---|
| `date` | 10月20日（星期二） (another year: 2025年10月20日（星期一）) | days: 串流's day row and 情緒, 數據分析's periods, 報告's days, search filters |
| `dateYear` | 2026年10月20日（星期二） | reports' headings and subjects |
| `time` | 14:05 | within one day: 檢查了來源 |
| `timeSec` | 14:05:09 | the monitor's "updated" |
| `dateTime` | 10月20日（星期二）14:05 | a moment: item page, 搜尋, 數據分析 posts, WiseSearch |
| `dateTimeYear` | 2026年10月20日（星期二）14:05 | the item page's 詳細資料, the AI's context, report time spans |
| `when` | 14:05 today, else `dateTime` | 即時更新's time, 「最遲 …」, 第二次評分於 …, Discord messages to Deyao |
| `axis` | 20/10 | chart axes only |
| `sortable` | 2026-10-20 14:05 | spreadsheet cells (search export) |

| `onDay(t, day)` | 14:05 on the chosen day, else 10月19日 23:40 | rows under a day picker (below) |

**Under a day picker, rows show only the time (Deyao, 2026-10-20):** "no need to show the date when there's already a
date picker at the top of the page." Where the page shows one chosen day (串流's day row; 數據分析 with the period on
one day, e.g. 今日), the picker keeps the date (`date`) and each row and half-hour row shows `dt.onDay(t, day)`: the
time only when it falls on that day, else the shortest form that can't be misread, 「10月19日 23:40」 (a bare day:
nothing on that day, else 「10月19日」; the year only when it is not this year). Pages without a day picker (the item
page, 搜尋, the monitor, 數據分析 over several days) keep `dateTime`.

**串流 follows the notification (Deyao, 2026-10-20):** 串流 mirrors the stream's WhatsApp/Telegram groups. An item is
sent seconds after it is filed, in `stream_items.id` order (`telegram_alerts.py`, `whatsapp_alerts.py`), and its
filing time is `item.seen`; so 串流's day is the HKT day of `item.seen` (`server/feed.ts` SENT_DAY, index
`stream_items_sent_day`; the live trigger announces that day), the order is the send order, newest first, and each
row's time is `item.seen`. `stream_items.day` (the post's publish day, the row's key) is not 串流's day: a post published
yesterday and filed today is on today's page. The item page (詳情) shows the post's release time as we found it
(`published`, `dateTime`; `dateTimeYear` in 詳細資料), only its date when the source gave only a date (date-only
posts), 「來源沒有提供」 when there is none, and when it was first collected (首次收錄) separately: never a time we made
up. Tests: `tests/test_web_app_pages.py` (`day_picker_rows`, `test_item_release_time`), `tests/test_dates.py` (onDay).

A bare day (the source gave no time) shows as `date` in every time format. `tests/test_dates.py` checks the formats,
that the TypeScript and Python formatters give identical output for the same instants, and that no other file formats
a date by hand (toLocale*, Intl, slicing an ISO string, building 月/日, a weekday table). Machine-readable times (API
and MCP tool JSON, keys, file names) stay ISO.

## Spinners: only for a fetch in flight (Deyao, 2026-10-16)

A spinner means "the page is fetching this and it will appear in seconds": a network or database read, a search, an
AI answer while its stream is alive. Data the pipeline has not produced yet (full text not read yet, a transcript in
the queue, pictures not scored, AI tags or scores pending, comments not read, a second scoring to come) is never a
spinner: it is a static line in plain Chinese saying what is pending and, where known, when or why
(「正在收集內文：系統每 30 分鐘讀取一輪出版機構網頁…」, 「逐字稿排隊中，第 N 個。」,
「第二次評分於 12:49 進行，連同屆時的留言。」, 「AI 尚未評分（排隊中）。」). Live updates redraw the page when the data
lands. Why: the item page of a Threads post (shinninglane, 2026-10-16) spun 「處理中：系統正在收集全文」 for a post whose
whole text is its headline and that no job reads.

- **Reads:** the pages' spinner is `<Wait>` (`web-app/src/lib/Wait.tsx`): a spinner with what is being fetched while the request
  is in flight; when it fails, or after 15 s without an answer, a static note (未能… / …超過 15 秒仍未完成) with 重試.
  Static pending lines are `<Pending>` (same file). Used on the item page (the post, 全文, 評分, 同一事件的其他報道) and
  搜尋 (the outlet list, 搜尋中).
- **Streamed AI answers** (數據分析's persona button; `web-app/src/lib/ndjson.ts`): the AI server sends
  `{"t":"ping"}` at once and every 5 s while the model or a tool works (`web.py` Stream); no headers or no line for
  15 s ends the request with 「15 秒沒有收到伺服器的回應，連線可能已中斷，請再試一次。」, so the answer's spinner
  stops.
- What the server knows about the queues comes with the item (`item.py` / `server/item.ts`): `text_due` (the full-text
  job will read the page: `fulltext_fetch.will_read`; never a social post, whose collector gave its whole text),
  `comments_read` (a fetcher reads this post's comments: `twopass.UNIT_URLS`), a queued clip's `queue` place
  (`media.due`'s order) and `scoring.second_due` (`two_pass.due_at`). `edge_config.py` publishes the full-text rule
  and the URL patterns for the edge app.
- Tests: `tests/test_no_spinners.py` (WebKit iPhone 390 px, Chromium 1600 px: no spinner or running animation once the
  page's reads are done, the static lines, a held read turning into 重試 after 15 s), `tests/test_pending_states.py`
  (the fields, the heartbeat), `tests/test_agent_page.py` (a silent stream stops after 15 s).

## Turned-off features: absent, never announced (Deyao, 2026-10-20)

"Let's not show features that have been turned off — it's not good UI to show a user a feature is off, as opposed to
it just silently missing." A feature that is turned off (a feature flag off, a pipeline switched off for cost) is
absent, never announced, on every page the readers see (串流, the item page, 數據分析, 搜尋) and in what the MCP
tools return: no "off", "paused", "disabled" or 「已關閉」 note, no queue place, no pending line for it; its section,
card, nav entry or route is simply not there (a flag-off section's routes answer 404). Content that exists from before
shows as normal content (pictures analysed before, a transcript, a clip's YouTube subtitles). While picture analysis
and transcription are off (flags `pictures`, `transcription`), the item data leaves out the pictures and clips that
wait on them (`item.ts` media / pictures; Python `item.py`). Only the monitor (`/app/monitor`, Deyao's admin page)
says "off: <why>".

## Drill-downs and Back

Every drill-down is its own history entry, so the browser's Back (and the iPhone swipe-back) returns to where the
reader was; the in-page "‹" button does the same thing.

| From | Drill-down | URL | In-page back |
|---|---|---|---|
| 數據分析 → 熱門話題 | one topic | `/dashboard?v=hot&tid=<id>&…` | "‹ 熱門話題": `history.back()` when the list is the previous entry, else replaces the URL with the list |
| 數據分析 (any author link) | one author (人物) | `/dashboard?v=persona&a=<name>&…` | the 人物 tab lists the most active authors; Back returns to the page the author was opened from |
| 數據分析 → 帖子 | a post expands in place (the chevron at its right) | no URL change | — |
| any post (串流 card, 搜尋 result, 數據分析 post list, topic, persona) | the item page | `/item/<id>` (`/item?url=<url>` → 302 to it) | "‹" in the bar: `history.back()` when the previous page is on this site, else 串流 |
| item page → 在數據分析開啟 | the post list holding it, expanded and scrolled to | `/dashboard?v=posts&p=<pid>&from=<day>&to=<day>&post=<id>` | Back returns to the item page |

## 搜尋 (the article search)

YYC's own replacement for WisersOne's WiseSearch: one query box (query.py's language: AND, OR, NOT, "phrase", NEARBY/n, FindIn…, with 只搜標題 and
包括留言及回覆 switches), filters for the date range (quick 今日 · 7 日 · 30 日 · 1 年 · 全部; default the last 30 days),
media type, outlet (searchable checkbox list grouped by type), region and sort (最新優先 · 最舊優先 · 相關度). Every
search is a URL (`/search?q=…&type=…&site=…&from=…&to=…&sort=…&page=…`) and its own history entry, so Back returns to
the previous search. The result count, the time taken, per-type and per-outlet counts (tap one to narrow to it), then
cards: headline linking to the post's item page (`/item/<id>`), outlet · board · author · time · type, and a short extract around the first
match with the terms marked, and 原文 (the publisher's link). 匯出 Excel downloads the same search (headline, outlet, board, author, type, region, time,
extract, link; at most 5,000 rows). Full text is searched but never shown or exported (publishers' copyright).
Desktop (≥ 960 px): the filters are a sticky column on the left; phone: a collapsed 篩選 panel above the results.


## WiseSearch 轉接 (`/app/wisers/`; `web-app/src/routes/-wisers/`)

The full read-only WiseSearch on YYC's WisersOne account (Deyao, 2026-10-29: WisersOne's layout and flows in his house
style, claude-env `deyao-web-design`: square edges, colour only for a hit or a quota/error state, SVG icons, no motion),
behind its own Access app, so it has its own bar (搜尋 · 紀錄 · 已儲存的回應) and no links to the rest of the site.
Nothing on these pages writes to the account: no saved searches, folders, bookmarks, tags, e-mail or search history.
- **搜尋** (`/app/wisers/?…`, `Form.tsx`): 快速搜尋 (keywords with WisersOne's own suggestions) or 邏輯搜尋 (contains all /
  any, excludes all / any; NOT only is refused), the ? syntax guide, 搜尋範圍 (標題 / 內文, 全文 · 同一段落 · 同一句), 日期
  (WisersOne's periods, 自訂 from/to; the account's limit in years), 排序 (最新 · 最相關 · 追蹤 with negative categories),
  媒體／作者 (types, categories, regions from Wisers, a parent ticking its items, language, 指定媒體: search a medium,
  include + or exclude, its profile at `/app/wisers/media/<id>`, author, column, section), 熱選 when the account has it,
  the six label trees (主題, 上市公司, 行業, 集團／公司, 品牌／產品, 教育: a level loads when opened, type-ahead, a ticked label
  becomes a chip), 負面新聞, 其他選項 (synonyms, merge similar, summaries, page size). 搜尋 first runs WisersOne's checks
  (stop words, illegal characters, the date limit; no search) in a dialog, then opens the results at their own URL; the
  form's fields go into its own history entry, so Back (even after a reload) shows it as it was.
- **Results** (`/app/wisers/search?…`, `Results.tsx`): the criteria as plates with 修改搜尋 and 重新搜尋, WisersOne's tabs
  with their counts (全部 · 報刊 · 網站 · 社交媒體 · 論壇 · 博客; a tab is a new search), 共 n 篇（合併前 m）, the toolbar (全選,
  下載, 列印, 逐篇瀏覽, 排序, 每頁, 合併相似, 摘要, list / cards / grid), each row (number, checkbox, the content icons from
  `displayStyle`: 文字, 印刷版 page image, 網頁, video, audio — each opens the reader in that style — the headline, outlet,
  page, section, column, author, date, words, circulation, engagement, the excerpt with hits, 相似 n opening the merged
  articles as a branch, 原文, 其他欄位), the pager (each page is a search), and 篩選及熱門關鍵字 (the sidebar from 960 px: media
  and regions, keyword match, label trees, 在結果中搜尋 stacked with 上一個篩選; 熱門關鍵字 and 負面熱詞, a word filtering the
  results). The sidebar's lists load when opened. While Wisers works the page says so in words with its time; a failed
  search says whether it may have cost quota, with 再搜尋一次; the page never searches again by itself.
- **Reader** (`/app/wisers/doc/<docId>?job=&i=&style=`, `Reader.tsx`): the headline, the heading plates (來源, 版面, 專欄,
  作者, 字數, 日期, 發行量, 種類), 上一篇 / 下一篇 over the job's items, 標示關鍵字, 列印, the text with the search's hits, the
  印刷版 images (style `img`: the page itself; beside the text: thumbnails), the full-screen viewer (arrows, i/N, zoom,
  下載 JPG, the heading box: page, circulation, ad value, words, area), video or audio when Wisers has them, 原文連結,
  同版其他新聞 (the other articles of the print page as a line diagram, the open one ink-filled) and the footer (document
  number, 全文來源). The text comes from YYC's corpus when the article is there, else from Wisers; the reader is the same
  either way. 逐篇瀏覽 (`/app/wisers/browse?job=&ids=`) shows the selected articles one after another, 列印
  (`/app/wisers/print?…`) lays them out for the browser's print (local: no WisersOne print).
- **下載**: the sheet (標題及內文 · 標題、內文及圖片 · 標題及圖片 · 只有標題 · 只有圖片; PDF, DOC, HTML, RTF, TXT, JPG for images;
  標示關鍵字) for the selected articles (or this page's), a job on the relay, then 儲存檔案.
- **紀錄** (`/app/wisers/log`, `Log.tsx`): this month's searches, documents opened from Wisers, documents answered from
  YYC's corpus, images and downloads; 搜尋 (every search: time, who, reason, keyword and options, count, time, outcome)
  and 文章、圖片及其他 (every other Wisers call, where a document's text came from, and the outlets Wisers had that YYC's
  corpus did not). 已儲存的回應 lists every saved search response; opening one costs nothing.
Scroll is never reset (Back restores it; results are cached for the session). Tests: `tests/test_wisers_page.py`.

## 串流: is it current? (Deyao, 2026-10-15)

The day row's right end says **即時更新** (green dot) only while the page's last read of today's feed succeeded within 2
refresh intervals (2 min) and the server's last check of its sources (`last_poll`) is within 2 poll intervals plus
2 min (12 min), measured on the server's clock (the response's Date header), not the phone's. Otherwise it says when
the data is from and why it isn't current (amber dot; on a phone on two lines): 資料截至 HH:MM · 更新中 (a read on
its way) / 離線 / 未能更新 (an error, or no answer within 15 s) / 登入已過期 (Cloudflare Access answered its login
instead of data) / 伺服器未有新資料 (the reads work but the poller is behind). No refresh button: the page reads every
minute (every 15 s while it isn't current), and at once when it comes back to the screen (`visibilitychange`,
`pageshow` from the back/forward cache, `focus`, `online`), since an iPhone freezes a background tab's timers. Past days
show no label. The feed is a server function, never cached (`src/lib/freshness.ts`). Tests:
`tests/test_stream_fresh_app.py` (Chromium, WebKit iPhone).

## 串流: tags and categories (2026-10-14)

Each card shows the item's sentiment and label tags (icon + name) under the headline, and the coloured rule on its left.
The emoji marks (`marks.py`: 🟢🟡🔴 + 👀/🔥/📌) are Telegram's only; the website doesn't use them (Deyao, 2026-10-14:
web and Telegram show data differently, since Telegram has only text).
**串流 is one list in send order; categories are labels, not sections (Deyao, 2026-10-20).** Every stream, including
one with a `categories` list in streams.json (CEHK), lists its items in the groups' send order, newest first, under the
half-hour rows. The scorer's category is a small label above the card's headline, as the Telegram/WhatsApp message
carries it in bold above the mark and headline; an item without a category has no label. There is no category filter.
Test: `tests/test_web_app_pages.py` (`day_picker_rows`: CEHK order, no `.slot.cat`, each card's `.cat`).

## The item page (`/item/<id>`; `web-app/src/routes/-ItemPage.tsx`, data `web-app/src/server/item.ts`)

THE page for one post (`posts.id`). Every list that shows posts opens it: 串流 cards (`/item?url=<url>`, since stream
items carry the URL, not the id), 搜尋 results, 數據分析's post lists (帖子, a topic, 人物; the chevron still expands a
post in place). `/post/<id>` (links in sent reports) answers 301
to `/item/<id>#text`. Shared shell (no section highlighted, the stream picker shown); one column on phones, and from
960 px the text and the comment thread on the left with a 360 px column on the right.

- **Head:** outlet, board, type and region; the headline; author, published and first-seen times (HKT). Actions:
  **開啟原文** (the publisher's link, the prominent button), **在數據分析開啟** (`/dashboard?v=posts&p=<pid>&from=<day>&to=<day>&post=<id>`,
  pid = the picker's stream when the post matched it, else the first profile it matched; none → the button is
  replaced by 「不在任何設定檔中」), **複製連結** (`<origin>/item/<id>`).
- **內容 (`#text`):** what it says comes from the item's `text_state` (titles.text_state / textState: recorded state,
  never a guess from age; Deyao, 2026-10-20: "don't say source don't provide it anyways, say we are unable to fetch
  it"): `due` (a reader will still read the page, `text_due`): 「正在收集內文：系統每 30 分鐘讀取一輪出版機構網頁…」;
  `failed` (read, nothing kept) or `none` (no reader covers it): 「未能取得內文。」; `whole` (a social post or a
  comment with no text beyond its headline): 「這則帖子的全部文字就是上面的標題。」. No page claims a source gives only a
  title. 「全文」 labels only article text: a body that is only the title again (`titles.title_only`) is no text. Approved accounts (`/app/me`) get the full stored text from `GET /app/item/text?id=` (under `/app/`,
  the Access token checked again by `src/server/access.ts`); everyone else gets a 200-character extract, the saved length, and the publisher link.
- **連結 (`#links`; Deyao, 2026-10-20: "this post has a link, but it's missing"):** the post's links (`item.links`;
  shown only when it has some), each with its outlet's name (else the site) and the address: once link following has
  read them (`follow.py`: the post reached a stream), at the address they lead to (shorteners expanded) with
  「本系統已收錄」 linking our own copy; before that, as they stand in the text. Share buttons, front pages and pictures
  are not listed.
- **圖片 (`#pictures`):** the post's pictures (images.py; shown only when it has some): the ones scored at or above the
  `image_threshold` setting first, each with the model's reason (also on hover) and, for approved accounts, the text
  read from it folded under 圖中文字; the rest folded under 其他圖片（N）; a picture not scored yet says 尚未分析（排隊中） on hover.
- **影片逐字稿 (`#media`):** the post's video and audio clips (media.py; shown only when the post carries one): per
  clip its host and length, and the transcript (one line per piece, `[分:秒] text`) for approved accounts
  (`media` in `/app/item/text`); everyone else gets the transcript's length and the login link, a clip still in the
  queue shows its place (逐字稿排隊中，第 N 個), one waiting to retry 上次未能轉寫，稍後會再試, a clip given up says why. The model's name under the clips.
- **留言及回覆:** the comment/reply rows whose `thread` is the post's URL (the first 200, oldest first, 20 shown
  until 顯示全部); on a comment row, its thread's parent and siblings with this one marked. Public: 200 characters per
  comment; approved accounts: full.
- **AI 分析 (`#ai`):** what the stream scorer (streams.py: the stage-1 model screens, the stage-2 model scores again
  every stream stage 1 put at 20 or more) produced for the post. The heading names both models when stage 2 scored any
  stream (「<stage 1> 篩選 · <stage 2> 覆核」, names from kv `openrouter_models`); such a stream's row shows stage 2's score
  and reason and one small line 「<stage 2> 評分；初步篩選 <stage 1> N 分（stage 1's reason）」. Everyone: its overall
  sentiment and labels (`scoring`), or why it was not scored (`not_scored`: collected before scoring
  started, a comment while comment scoring is off, published over two days before it was collected; else
  AI 尚未評分（排隊中）), and when a unit post's second scoring is still to come, 第二次評分於 HH:MM 進行.
  Approved accounts (`GET /app/item/ai?id=`, the same gate as 串流's scores): every stream, highest
  first, with its 0–100 score on a bar marked at the stream's threshold, 達標/未達標, the category, the model's reason
  and the day's stream it was filed in; a stream it was filed in stays (已收錄) with its score now beside the score it
  was filed on (收錄時 90 分，重新評分後 低於 20 分); streams under the floor (20; a stage-1 score under it is stored but not shown here, only on the proof view) are listed in one line. Below, the
  meaning of every label used on the page (`vocab.label_info`: item.LABEL_INFO, published in `edge:config`). Read-only.
- **AI 標籤:** each profile's tags (relevant, sentiment, labels, theme), read-only, marked 已人手修正 on a row with
  `tags.corrected = TRUE` (`corpus.set_tags` / `set_themes` never write over such a row). Nothing on the site edits
  tags.
- **串流及設定檔:** the streams that kept it (link to that day's stream, with its category) and the profiles that found
  it relevant.
- **同一文章的其他帖子:** the other posts with the post's `article_id` (`article`; up to 50).
- **同一事件的其他報道:** `insights.topics()` over the posts within 3 days whose titles overlap the post's; other
  outlets only, each linking to its own item page.
- **來源及時間, 互動, 更多:** website, board, author, platform, type, region, source, feed sid, times; the engagement
  counts the source gave; 同一媒體 (帖子 filtered by `site`, or 搜尋 by outlet when no profile applies) and 同一作者 (人物).

Data: the item page's server functions (`src/server/item.ts`, a port of item.py, which the data API's posts_get tool and
the MCP server keep using; `tests/edge_parity.py item` compares the two; never the full text in public). Tests:
`tests/test_web_app_pages.py`, `tests/test_item.py`, `tests/test_item_kinds.py`, `tests/test_item_ai.py`.
