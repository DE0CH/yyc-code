# Roadmap

## Analyses that correct for our selection (Deyao, 2026-10-14)

The figures below count posts one per vote, so they measure YYC's own choices as well as what is happening: which
sources we watch, the search terms we send to search-only platforms, how many feeds we read per outlet, how often we
poll, which comments and replies we fetch. They are shown with the selection-bias note (README "Figures that depend on
our selection", Deyao 2026-10-16). The next step is weighted measures that correct for that selection.

### Corrections to build

- **Weight by engagement:** each post counts by the reactions its platform reports (likes, shares, comments, views,
  dislikes; `posts.engagement`), so a post nobody read weighs little and a widely shared one weighs a lot. Needs
  per-platform scales (a LIHKG like ≠ a Facebook like ≠ a YouTube view) and a rule for sources that report nothing
  (most news sites).
- **Weight by source reputability and reach:** an outlet's or account's audience (circulation, ratings, followers,
  site traffic) and standing, so one SCMP article and one small Facebook page are not equal.
- **De-duplicate the same story:** one story carried by several feeds of one outlet, re-posted on the outlet's own
  Facebook page, or syndicated across outlets counts once per outlet (or once in total, by measure), using the
  topic grouping (`insights.topics`, `posts.article_id`).
- **Normalise by how much we sample:** divide by how much of each source and platform we read (feeds per outlet,
  polling interval, search terms per platform, comment fetching on or off, backfill coverage per outlet and day), so
  adding a source or a feed does not look like a rise in discussion. Needs a per-source, per-day record of what we
  watched.

### The figures to give weighted versions of

- Smart Hot Terms (keyword frequency)
- SPI, DVS (volume), DPS (participation), CCS (coverage), CSS (sentiment) daily scores and their 7-day charts
- Exposure & Mentioning Summary (posts, new posts, topics, websites, boards, authors, view count, per-day rates,
  website coverage, posts spread, CGM vs News)
- Exposure & Mentioning Trend and the 趨勢 view (posts, topics, authors, views, sentiment index per day or week)
- Profile-wide sentiment distribution and sentiment index (情緒分佈; 串流's sentiment bars and counts)
- Top Author (作者) and the most active authors; Top Source (來源, by website or board) and 主要來源
- The daily report parts built on these (format1's header figures, format2's scores and charts, report.pdf's scores,
  分數解讀, top participants and websites) and the agent tools `stats_overview`, `authors_top`, `sources_top`, `trend`

### Decisions pending (shown without the note for now, borderline)

熱門話題's ranking and count columns, per-topic sentiment, a topic's counts, 主題's per-theme counts and sentiment,
人物's counts and sentiment, the 帖子 filter chips' counts, 搜尋's per-type and per-outlet counts, format1's topic tags
and "N 則相關". If they get the note, they join the list above.
