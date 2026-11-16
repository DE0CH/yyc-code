---
name: authors-channels
description: Who drives the discussion of a subject - the most active authors, KOLs, pages and channels with post counts, sentiment and influence, as tables and a CSV. Use for KOL, 作者, 渠道, 來源, who is talking, influencers.
---

# Authors and channels

## Steps
1. Subject and period as usual.
2. `authors_top` (top 15) and `sources_top` (by website; by board if forums matter).
3. For the top five authors, `posts_search` with author= to see what they say and their tone.
4. `table_make` with the authors table and another with the channels table.

## Output
- One line: scope and dates.
- **作者／KOL** table: 作者 | 渠道 | 帖子數 | 情緒傾向 | 代表帖子.
- **渠道** table: 渠道 | 帖子數 | 情緒指數.
- Three bullets of observations (who leads, who is critical, where discussion concentrates).
- The CSV links.
- Under every figure taken from stats_overview, authors_top, sources_top or trend (counts, sentiment index, scores,
  rankings), one line: 「這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。」 (in English: "These figures depend on which sources and keywords YYC follows,
  so they may not reflect what is actually happening.")
