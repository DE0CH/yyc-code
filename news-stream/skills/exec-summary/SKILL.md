---
name: exec-summary
description: Executive summary of what media and netizens said about a subject over a period, for management. Use when the user asks for a summary, overview, briefing, 摘要, 行政摘要, 概覽 or "what's being said about X".
---

# Executive summary

## Steps
1. Settle the subject and period (profile via profiles_list; period from the question, else the last 7 days).
2. `stats_overview` for volume, sentiment index and the daily scores; `trend` (bucket day) to spot peaks.
3. `topics_hot` (top 10) for the stories; `posts_search` (response_format detailed, limit 30-50) for what was said.
4. Read the posts behind each main topic (`posts_get` for the ones you quote). Drop unrelated posts (see the
   filter-unrelated skill) and say how many.
5. Write the summary.

## Output (in the question's language; Hong Kong Traditional Chinese when asked in Chinese)
- One line: subject (profile or keyword search) and dates.
- **整體情況**: volume (posts, change vs the period before if stats show it), sentiment index, main channels.
- **重點話題** (3-5): each a heading, two or three bullets of who said what, each claim with its [source](link).
- **各方觀點**: supporters, critics, neutral reporting, in short bullets.
- **管理層需留意**: risks or opportunities, one line each.
- A markdown table of the topics: 話題 | 帖子 | 情緒 | 代表帖子.

## Example opening
> 範圍：港投公司（HKIC），2026-10-07 至 2026-10-13；共 214 篇相關帖子，剔除 9 篇無關。
- Under every figure taken from stats_overview, authors_top, sources_top or trend (counts, sentiment index, scores,
  rankings), one line: 「這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。」 (in English: "These figures depend on which sources and keywords YYC follows,
  so they may not reflect what is actually happening.")
