# K-Matrix parity: "CI analytic Fixed Profile Package"

The target is every feature in K-Matrix's brochure (`K-Matrix_Fixed_Profile_Package_R1.pdf`, 39 pages, on the YYC
server in `/root/yyc-docs/`, next to their Galbot monitoring report sample `Galbot_Daily_News_Monitoring.docx`).
WhatsApp delivery is out of scope for now (Deyao). Reading is public; anything that changes what is watched or sent
(profiles, settings, recipients) is Deyao's, through scripts (`infra/control.py`; Deyao, 2026-10-14: no admin page,
the website is read-only).

Status: ✅ done and live · 🔧 being built · ⬜ not started.

## 1. Realtime alert (pp.3–5)
- ✅ 24/7 monitoring of the brief's sources (README: sources), half-hour batches, stream page `/`
- ✅ AI tagging with K-Matrix's full tag set: sentiments 😄positive 😐neutral 😣negative 🤔mixed 😏irony; relevance
  👑repute 🛍goods 🤝service ⚔rival ⚠issue 🚨crisis 👸kol; engagement 🔥engaging 📈trend ❓ask; intent 📝review 😡accuse
  🤩praise 🗣rumor; action ⏰urgent 💡chance 👀monitor (`ai.py` LABELS; used by the streams and the profiles)
- ❌ instant e-mail alerts: removed (Deyao, 2026-10-26); new stream items go out on Telegram and WhatsApp
- ✅ engagement counts (likes, comments, shares, views, replies) where the source gives them, for 🔥engaging and View Count;
  authors and full post text from the collectors; a cached copy of every relevant post (`cache_fetch.py`, yyc-cache
  timer every 30 min). TikHub platforms carry them once the TikHub balance is topped up (all endpoints 402 on 2026-10-12)

## 2. Daily social monitoring e-mail (pp.6–11)
- ✅ Format 1 "Social Profile": AI-generated summary of the day's discussion, all topics by media channel with links
  ("related in N posts", cached copy, a 轉寄 mailto link)
- ✅ Format 2: e-mail summary (SPI + CSS/DVS/DPS/CCS with levels and day-on-day change, 7-day SPI and CSS charts,
  the AI summary and recommended action), one-page PDF AI report (scores, 分數解讀, AI summary, recommended action,
  top 5 hot topics, participants and websites), Excel "Hot Topic Listing"; the scores and counts carry the
  selection-bias footnote (README "Figures that depend on our selection")
- ✅ Post listing in the Galbot sample's table form (Date, Media outlet, Category, Sentiment, Format, Topic, Full
  text, Link) as Excel and Word
- ✅ Daily job `reports.py daily` (CronJob `reports`, at the report hour (`infra/control.py set report_hour`), default 09:00 HKT, for the day before)
  builds every profile's files under `/reports/<day>/<profile>/`, listed at `/reports/`
- Not emailed: Deyao dropped the daily email reports on 2026-10-23 (no longer needed); the files stay on 報告

## 3. Self-served dashboard (pp.12–19)
- Keyword profiles: not built, by decision (Deyao, 2026-10-14: no keyword query decides what belongs to a profile,
  "that's probably just fancy regex"). The profiles are the streams, and a profile's posts are those the AI scored at
  or above the stream's threshold (README: Profiles). K-Matrix's query operators (AND / OR / NOT, "phrases",
  NEARBY/n, FindIn, IncData / ExcData, TopicSearchOnly; `query.py`) are the text box of 數據分析 and 搜尋, which
  narrows what is shown
- ✅ Corpus of every collected post (`corpus.py`, Postgres), scored against every stream and searched by 搜尋
- ✅ Analytics API (`insights.py`; the pages: `web-app/src/server/dashboard.ts`, `analytics.ts`): Summary (CGM / News
  / Analysis metrics), SPI and the four scores, Exposure & Mentioning Trend, Hot Topics with K-Matrix's columns +
  drill-down (the topic's counts and sentiment, posts per day, top sources and authors, post listing, original link,
  cached copy = the item page `/item/<id>`), Post Listing, Top Author, Top Source, Trend, Smart Hot Terms, Themes,
  Persona; Excel exports of all of them. Each view whose figures count posts one per vote shows the selection-bias note once
  (README "Figures that depend on our selection")
- ✅ Dashboard page `/dashboard` (public; Traditional Chinese, phone and desktop, light and dark; state in the URL):
  profile + period pickers (today / 7 / 30 / 90 days / custom); Summary (trend chart, metrics, daily scores, hot
  terms, the AI daily summary); Post Listing (the scheduled AI daily summary when
  AI is on; paged, filters by sentiment, label, theme, source type, website, author and a text search, content inline
  with original link + cached copy, Export); Hot Topic (Top 10/50/100, K-Matrix's columns, Export,
  drill-down: counts, sentiment, posts per day, top sources and authors, posts, original link, cached); Top
  Author; Top Source (by website or board); Trend (day/week, posts/topics/authors, views, table); Themes; Persona; links to the
  stream, reports and the agent; the stream page links to it
- ✅ Profile setup by script (Deyao only; no admin page): a stream's scope is its brief in `streams.json` and its
  threshold `infra/thresholds.py` (with `preview`); `infra/control.py`: delivery (crisis alerts and their
  recipients), settings (report hour, alert labels, burst thresholds, sender name), today's AI spend; run report /
  themes now with `reports.py profile|themes`

## 4. Persona analysis (pp.20–21)
- ✅ API: an author's history, boards, websites, sentiment toward the profile; AI persona note
- ✅ page (dashboard tab Persona): author box, history, boards, websites, sentiment toward the profile, activity,
  recent posts, hot terms, the most active authors, "AI 分析" button

## 5. Benchmarking (pp.22–23)
- Not built: YYC's streams are separate watches, so there is nothing to compare between them (Deyao, 2026-10-14)

## 6. AI summaries and themes (pp.24–31)
- Removed (Deyao, 2026-10-28; git has it): the dynamic AI summary by hot topics and by post list, the "AI Summary"
  buttons on the dashboard's Post Listing and Hot Topic tabs
- ✅ Themes on the dashboard: the profile's AI themes with post counts and sentiment, click → Post Listing filtered by
  theme, theme filter in Post Listing, Export (`/api/export/themes.xlsx`)
- ✅ Dynamic AI theme generation per profile (daily job over the last 7 days, ≤300 posts; regenerate by hand:
  `reports.py themes ID`) + filter/sort by theme

## 7. Conversational AI agent (pp.32–38): removed (Deyao, 2026-10-28; git has it)
- ✅ Agent backend (`agent.py`): natural-language questions over the live corpus through tools, citations to the
  original posts, CSV tables, follow-up suggestions, server-side chat history
- ✅ Chat page `/agent` with history, quick-analysis sidebar (filter unrelated content, executive summary, risk &
  complaint analysis, strategic action items, KOL & channel analysis)

## Methods
YYC's own formulas for K-Matrix's SPI, CSS, DVS, DPS and CCS: `insights.METHOD` (shown on the page). The sentiment
index is 100 × (positive − negative) ÷ all tagged posts of the group. They count posts one per vote, so they carry the
selection-bias note (README "Figures that depend on our selection").

## Another implementation to reuse (2026-10-12)
A background agent in the session that proposed this one built parts of parity in parallel before this session's
work landed; it never touched main. Its commit is on branch `parity-alt` (16d0c97). Pieces this file lists as not built
yet that are done and tested there: the daily report as format-1 email + one-page PDF + Excel (`report.py`,
`mailer.py`, sender bot@deyaochen.com, recipients empty by default), crisis alerts on crisis/urgent tags and negative
bursts (`alerts.py`), article-text enrichment (`enrich.py`), per-feature AI cost logging with a USD 15 pause
(`ai.py`), and dashboard/agent/admin/report pages under /kmatrix/ (written against its own APIs). Port what helps;
this session owns parity.
