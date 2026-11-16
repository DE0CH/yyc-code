# YYC AI watch (AI 觀察)

https://aiwatch.yyc-internal.com — what the AI apps Hong Kong people use tell them about a subject (an event, a person,
a company): each app's answer, the sources it cites, where the apps agree and contradict each other, and which
statements a PR team should act on first. YYC's own take on crewio × 元意顧問 (yyc.crewio.ai), which asks the models'
APIs; this asks the consumer apps themselves, as a person in Hong Kong sees them.

Behind Cloudflare Access app "YYC AI watch" (YYC's account; allowed: chendeyao000@gmail.com, Deyao 2026-10-27).

## The apps (Deyao, 2026-10-27: the ones Hong Kong uses; no ChatGPT; free tiers)

| Engine | What is asked | Account |
|---|---|---|
| `google_aio` | Google Search's AI 概覽 on google.com.hk (zh-HK); not every query gets one | none |
| `google_aimode` | Google AI 模式 (udm=50) | none |
| `copilot` | Bing's Copilot Search (「AI 模式」 on bing.com): copilot.microsoft.com is sign-in only from HK, and Microsoft refused an account for YYC's address | none |
| `perplexity` | perplexity.ai, free plan | YYC's address, e-mailed code |
| `dola` | Dola (dola.com), ByteDance's international 豆包 (豆包 itself needs a +86 number) | YYC's Google account |
| (`deepseek`) | not offered (Deyao, 2026-10-27): its sign-up stalls on hCaptcha, also from Deyao's own browser; the collector stays in engines.py | — |

The apps' own pages and APIs go through YYC's residential proxy (IPRoyal sub-user `yycstream`, Hong Kong exit, one sticky IP
per worker for 24 h); public code CDNs, the Turnstile widget and analytics hosts load directly, Chrome's background
services are off and the worker loads no images (aiwatch/browser.py; Deyao, 2026-10-29: IPRoyal bandwidth),
in Chrome for Testing at a pinned version (aiwatch/browser.py: why not Google Chrome), headed on Xvfb. Walls: Google's
/sorry/ and Perplexity's Cloudflare challenge are solved with 2Captcha on YYC's key (aiwatch/captcha.py).

## How a case runs (aiwatch/pipeline.py)

news (Google News, 60 days) → questions (the model: recent issues, about 18 questions from each stakeholder's side; the
user ticks which) → ask (each ticked question × each app) → extract (each answer split into statements) → sources (each
cited page read and checked against the statements citing it) → group (the same statement across apps: agreement,
contradiction, risk) → merge → summary. Each step is a task in the database queue; a case moves on by itself.
追問 adds questions to a case and analyses it again.

The analysis model: DeepSeek V4.1 Flash (Deyao, 2026-10-27) on OpenRouter key `yyc-aiwatch` (USD 30 a month, a safety
stop; EXPENSES.md). Every call's cost (OpenRouter's usage.cost) is in table llm_call and on the pages.

## The case page (crewio parity, 2026-10-27)

總覽 (the facts, a questions × apps grid of tone marks that opens each answer, the riskiest statements, how each
stakeholder fares, how recent each app is, the recent issues, the weekly news count) · 問題樹 (the starting question and
one branch per issue) · 需要處理 (statements ranked by risk, the high ones highlighted) · 共同與分歧 (common,
contradicting, partial, one-app statements, with category and per-app dots; 重新分析) · 來源 (domains by importance =
apps citing × (1 + statements the pages support), in-text vs attached, per app; each domain's pages) · 圖譜 (issues ×
entities; apps → statements → domains; laid out once and drawn still) · 回答 (one question, every app side by side) ·
論壇 (which apps cite forums and social platforms; LIHKG / Threads posts by time span) · 追問 (follow-ups, AI-suggested
next questions, ask again as a new case, re-analyse).
crewio's daily quotas are left out: YYC's budgets are safety stops (the OpenRouter key's cap), not limits on the work.

## Running

- **Edge:** the pages (static/) are Worker `yyc-aiwatch` (edge/) on YYC's Cloudflare account, route
  `aiwatch.yyc-internal.com/*` (news-stream/deploy/cloudflare/tofu/tunnels.tf), deployed by
  .github/workflows/ai-watch-edge.yml: served from the location nearest the reader. It forwards /api/* to the web pods
  through the tunnel and keeps each location's last answer to the three reads a page opens with: a page draws that copy
  at once, then the origin's answer (`X-Fresh: 1`), so a case can show slightly older data for the length of one
  round trip to fsn1 (Deyao, 2026-10-28).
- **Cluster:** namespace `aiwatch` (deploy/app.yaml): web ×2 (the JSON API), worker ×2, cloudflared ×2; Flux Kustomizations
  `yyc-aiwatch` and `yyc-aiwatch-secrets`. Database `aiwatch` on YYC's Postgres (news-stream/deploy/db). Image:
  .github/workflows/ai-watch-image.yml pins the tag in deploy/kustomization.yaml.
- **Secrets** (infra/secrets.txt): aiwatch/app-env (DATABASE_URL, OPENROUTER_API_KEY, RESI_PROXY, TWOCAPTCHA_API),
  aiwatch/tunnel, aiwatch/ghcr-pull, db/pg-role-aiwatch.
- **Logins** (the apps' sessions, table browser_state): `python -m aiwatch.login` in a Claude session (claude-env skill
  `yyc-ai-watch`). Google's own sign-in is tied to the browser it happened in, so the stored state holds only the apps'
  sessions; a worker that finds one signed out marks the app `login_needed` (the pages say 需要重新登入).
- **Local:** a Postgres, `DATABASE_URL=… python -m aiwatch.web` (PORT, the API), the pages with
  `cd edge && npx wrangler dev --var ORIGIN:http://localhost:PORT`, and, with RESI_PROXY, TWOCAPTCHA_API,
  OPENROUTER_API_KEY and CHROME_PATH, `python -m aiwatch.worker` under an X display.
