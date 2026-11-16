---
name: filter-unrelated
description: Separate posts that are not about the subject (spam, ads, the same name meaning something else) from the relevant ones, with reasons and counts. Use when asked to clean, filter, 剔除無關, 過濾, or when a keyword clearly catches noise.
---

# Filter unrelated posts

## Steps
1. `posts_search` for the subject and period (detailed, limit 50; page with offset if total is larger, up to 150).
2. Judge each post against the profile's "covers" text (profiles_list) or the user's subject:
   - spam / ads / giveaways;
   - same name, different thing (e.g. "galbot" = to uproot in Cebuano; 銀河 = the galaxy);
   - only a passing mention with nothing about the subject.
3. Keep the rest.

## Output
- One line: scope, dates, total checked.
- **剔除** table: 帖子 | 原因 (spam / 同名但無關 / 只是順帶提及) | 連結.
- **保留**: how many relevant posts remain and, in three to five bullets, what they mainly say.
