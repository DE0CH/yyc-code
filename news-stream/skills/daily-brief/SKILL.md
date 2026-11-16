---
name: daily-brief
description: The morning brief for a profile - yesterday's posts, the daily scores and what changed - in the format of YYC's daily report. Use for 今日簡報, daily brief, morning report, 昨日, what happened yesterday.
---

# Daily brief

## Steps
1. `reports_daily` for the profile and today (it falls back to yesterday's report and says which day).
2. `stats_overview` for that day (from = to = the day) and the day before, to compare.
3. `topics_hot` for the day (top 5) and `posts_search` for the day (limit 20).

## Output
- **日期及範圍** line.
- **重點** (three to five bullets): the main stories with links.
- **分數**: today's SPI/CSS/DVS/DPS/CCS and the change from the day before.
- **建議行動**: from the daily report if it has one, else one line of your own marked as such.
- Under every figure taken from stats_overview, authors_top, sources_top or trend (counts, sentiment index, scores,
  rankings), one line: 「這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。」 (in English: "These figures depend on which sources and keywords YYC follows,
  so they may not reflect what is actually happening.")
