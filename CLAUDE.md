# YYC's code

This repository is public (README.md "Everything here is public"): never commit a secret, a staff member's address
or data, or anything that is not meant to be read by anyone. YYC's other rules (reports, expenses, the company) are
in the private DE0CH/YYC's CLAUDE.md.

YYC is a Hong Kong company. Any Chinese text written for YYC (pages, emails, model output for staff) uses
**Traditional Chinese with Hong Kong usage** (繁體中文，香港用語: 檔案, 程式, 網絡, 軟件, 伺服器, 電郵, 星期五…),
never Simplified Chinese. Fonts: Noto Sans HK / Noto Serif HK.

## Staff preference beats Deyao's preference (Deyao, 2026-10-21)

Deyao writes YYC's tools for its staff, not for himself. Whenever staff and Deyao would want different things on
something staff use (language, layout, wording, behaviour), the staff's preference wins, always. Example: staff read
Traditional Chinese (HK) more fluently and Deyao reads English more fluently, so every page staff use is in Chinese for
everyone (no per-viewer English); only pages Deyao alone uses (the monitor, the proof view, operator tools) are in
English, with proper nouns (outlets, people, organisations, places) kept in Chinese.
On a staff page, anything only Deyao can see sits inside one box labelled as visible only to him (its content in
English). When that part grows too big for a box, it becomes its own page at its own URL.

## The news stream is YYC's own product, not a K-Matrix copy (2026-10-14)

`news-stream/` follows YYC's own design: its own streams, scopes and methodology (watch every source that can be
watched, score every post 0–100 against each stream, thresholds Deyao sets). K-Matrix is not the model to copy, and
its streams and alerts are not the definition of a stream or the target to match.
Coverage stays complete: cover every source in YYC's brief (`News Monitoring & Monthly Report`). Never drop or defer
a source as "not worth it", "too much work", "it exists elsewhere" or "not valuable"; only a prohibitive cost is a
reason, and then give Deyao the exact cost and let him decide. Blocked sites are an engineering problem (proxies,
captcha solvers, a real browser on the YYC server), not a reason to stop.

## The news stream is a proof of concept (Deyao, 2026-10-15)

newswatch.yyc-internal.com and its pipeline are a proof of concept, not a production service: build and try things in
the pipeline directly rather than holding them back as "not for production".

## Build first, then profile and optimise (Deyao, 2026-10-14)

Setting a cost limit first and building around it is premature optimisation. Build each feature the best way without
weighing its cost (complete coverage, best model, every cadence the work needs), then measure what it really costs,
like a profiler (the monitor page, providers' own usage figures), and optimise the biggest items. Cost projections are
information for Deyao, never a reason to hold work back or to ask before building.

## Budgets are safety stops, not plans (Deyao, 2026-10-14)

A spending limit (an API key's cap, a provider's balance) only stops work when it is actually
hit. YYC's code never decides what or how much to do from budget projections: no reserves, no "wait for budget", no
caps or sampling chosen to save money (caps that protect a site from being hammered are fine). When a limit is hit,
log it, show it on the monitor and Discord Deyao to raise it or recharge.

Residential proxy traffic for YYC runs on YYC's one IPRoyal sub-user `yycstream` (Deyao, 2026-10-15), never on a
personal or shared login.

## A prompt carries the task, never the pipeline (Deyao, 2026-11-03)

Which posts reach a model call, why, and which of its answers are used is decided in code. Nothing about it goes into
a prompt: not the stage that asks, not what another model answered, not "check these topics for this post". A model
told what to look for finds it. Stage 2's "Check: post 0: CEHK" list named stage 1's suspects, and on a bare headline
that hint was the score (a Liverpool post filed in CEHK at 85). Each stage gets the same prompt for the same task; a
stage's own extra task (stage 2's titles) is the only addition.
