---
name: risk-complaints
description: PR risk and complaint analysis for a subject and period - negative peaks, complaints, accusations, rumours and negative posts that are spreading, with severity and a CSV. Use for 風險, 投訴, 負面, 危機, 公關風險, crisis or "is anything blowing up".
---

# PR risk and complaints

## Steps
1. Subject and period as usual (last 7 days if none; "today" means today).
2. `trend` (bucket day) to find negative peaks; `stats_overview` for the sentiment index.
3. `posts_search` with sentiment negative, then with labels accuse, crisis, issue, rumor, urgent (one call each,
   limit 30); merge and de-duplicate.
4. `topics_hot` to see which negative stories are spreading (many posts or participants, recent end_date).
5. Group into issues. For each: what it is, where it appears (channels), how many posts, first and last seen,
   severity (high: spreading, many channels, accusations of wrongdoing; medium; low).
6. `table_make` with columns 帖子, 渠道, 日期, 問題描述, 連結.

## Output
- One line: scope and dates.
- **風險總覽**: the number of issues by severity, the peak days.
- One section per issue, highest severity first: description, channels, posts, severity and why, two or three
  representative posts with links.
- **建議**: one line per high-severity issue on what to watch or do.
- The CSV link.
- Under every figure taken from stats_overview, authors_top, sources_top or trend (counts, sentiment index, scores,
  rankings), one line: 「這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。」 (in English: "These figures depend on which sources and keywords YYC follows,
  so they may not reflect what is actually happening.")
