# YYC data platform for AI: API, MCP server, skills, paid tools

Design for Deyao (2026-10-13). **The in-site AI 助理 was removed (Deyao, 2026-10-28; git has it): the registry now
serves the REST API only (the MCP server went 2026-10-29); what follows about the AI 助理 is history.** Status: steps 1-5 built (registry + REST, skills, MCP, paid tools + spend ledger, evals); the first live eval run is still to do. Deyao reads this and corrects it as it goes.

## Decisions (Deyao, 2026-10-13)

- **Who uses it:** the in-site AI 助理, and staff's own Claude (Desktop, Code, claude.ai) through MCP, behind the
  same Cloudflare Access login as the site.
- **Model:** the AI 助理 stays on OpenRouter (the `yyc-stream` key keeps YYC's model spend claimable).
- **Data:** the post corpus, the WiseSearch log (past results; Wisers is never searched live by the AI), the social
  search results, the daily reports, plus live search tools: TikHub platforms, AnyAPI Facebook, Exa web search and
  Exa page fetch (one new YYC-only Exa account).
- **Read-only:** the API and the AI only read and export; no tag edits, saved reports or alerts yet.
- **Everything goes through the chat:** the AI 助理 page has no selectors (done: cd6566e).
- **Paid tools:** each paid tool states its price in its description. One line in the system prompt asks the AI to
  say what it will run and what it costs, and to ask before spending. Each paid call's cost is recorded against the
  user (Access email); there is no per-user limit (Deyao, 2026-10-14: budgets are safety stops; the provider refusing
  for its balance is the stop). `infra/control.py tool-spend` shows each user's total.

## Shape: one tool registry, three ways in

```
                 news-stream/tools/  (one registry: function + JSON Schema + description + price)
                 ├── REST  /api/v1/<tool>        OpenAPI 3.1 at /api/v1/openapi.json, generated from the registry
                 └── AI 助理 (agent.py)           OpenRouter function calling over the same registry
```

A tool is written once: a Python function with a typed signature and a docstring written for a model. The registry
derives the JSON Schema, validates arguments (actionable error messages, not tracebacks), records the cost of paid
tools, logs every call, and shapes the response. The REST API, the MCP server and the AI 助理 are thin
adapters, so the three can never disagree.

### Tool design rules (Anthropic, "Writing effective tools for agents")

- Few tools that each do a whole job, not one per endpoint; namespaced by area: `posts_search`, `posts_get`,
  `profiles_list`, `stats_overview`, `live_web_search` …
- Names and readable values, not internal ids (a source's name, a profile's title); every post carries its
  `/item/<id>` link for citing.
- `response_format: "concise" | "detailed"` where both are useful; concise by default.
- Paging with sensible defaults; a truncated answer says how many more there are and how to get them.
- Errors say what to change ("`from` must be YYYY-MM-DD, e.g. 2026-10-06"; "no profile `CEHX`; profiles are CEHK,
  HKIC, …").
- Responses capped well under 25k tokens.

## The tools

Free (YYC's own data):

| Tool | What it does |
| --- | --- |
| `profiles_list` | The monitoring profiles: id, title, what each covers, its keyword query. |
| `posts_search` | Search the corpus: keyword query (YYC's syntax), profile, period, sentiment, label, source type, site, author; newest or most relevant first; paged. |
| `posts_get` | One post in full: text, tags, engagement, comments, its item page. |
| `stats_overview` | A profile's numbers for a period: posts, authors, sites, sentiment index, the daily scores. |
| `topics_hot` | Hot topics (stories) of a profile and period with their posts. |
| `authors_top` / `sources_top` | Most active authors / channels with sentiment. |
| `trend` | Posts and sentiment per day or week. |
| `reports_daily` | The daily AI summary and report of a profile and day. |
| `wisers_results` | Past WiseSearch searches and the articles they returned (the relay's log); never searches Wisers. |
| `social_results` | What the daily social search found (TikHub platforms, Facebook) for a stream. |
| `table_make` | Turn rows into a downloadable CSV. |

Paid (live, each on a YYC-only account; price in the description, recorded against the user):

| Tool | Provider | Price (list) |
| --- | --- | --- |
| `live_social_search` | TikHub: one platform + keyword (TikTok, X, YouTube, Reddit, Threads, Instagram) | per platform, from TikHub's price list |
| `live_facebook_search` | AnyAPI `facebook.search_posts` | USD 0.03 a search |
| `live_web_search` | Exa search (web and news) | Exa's per-request price |
| `live_page_fetch` | Exa contents (a page's full text) | Exa's per-page price |

## MCP server

The Python MCP server (`mcp_server.py`, `/app/mcp`) was deleted (Deyao, 2026-10-29); git has it. YYC's MCP server is
yyc-mcp (`mcp-edge/`). The Python registry above stays reachable through the REST API `/app/api/v1`, paid tools
included.

## Skills (Agent Skills format)

`news-stream/skills/<name>/SKILL.md` (frontmatter `name`, `description`; body: when to use, steps, which tools, the
output format, an example; `references/` for longer material):

- `exec-summary`: executive summary of a subject and period for management.
- `risk-complaints`: PR risk, complaints, accusations, spreading negative posts; CSV.
- `filter-unrelated`: separate spam and same-name-different-subject posts, with reasons.
- `action-plan`: three to five prioritised PR actions backed by posts and figures.
- `authors-channels`: most active authors, KOLs and channels with sentiment; tables; CSV.
- `daily-brief`: the morning brief of a profile (yesterday's posts, scores, what changed).
- `compare-subjects`: two profiles or subjects side by side over a period.

`stats_overview`, `authors_top`, `sources_top` and `trend` count posts one per vote: their descriptions and answers
say the figures depend on which sources and keywords YYC follows, and the prompt and skills have the agent say so
whenever it uses them (README "Figures that depend on our selection").

The AI 助理 sees only each skill's name and description (in the system prompt) and loads a skill's full text with a
`skill_load` tool when a question needs it (progressive disclosure, as Claude Code does). MCP clients get the same
skills as prompts and resources.

## Spend of paid tools

- Ledger `tool_spend (email, at, tool, usd, args, provider_cost)` (`tools/spend.py`); no limit is checked.
- After a call: the provider's own cost when the response carries one (AnyAPI does), else the list price.
- `infra/control.py tool-spend`: each user's total.
- System prompt line: "Paid tools cost money (the price is in each tool's description): before using one, say what
  you will run and what it costs, and ask; use the free data first."

## Quality: evals

`tests/agent_evals.py` (built): 14 real questions (one per profile and analysis, follow-ups, an ambiguous subject, a
subject no profile covers, a paid-tool question), each with checks (which tools ran, the profile and dates used,
citations present and real, no paid call without asking). Run with the model stubbed in CI and live on demand
(cost reported) to compare prompt and tool changes.

## Build order

1. Tool registry + the free tools, REST `/api/v1` + OpenAPI; the AI 助理 moves onto it.
2. Skills + `skill_load`; system prompt trimmed to rules + the skills index.
3. MCP server `/mcp` + Access for MCP clients.
4. Paid tools + spend ledger (Exa account first).
5. Evals.

Each step is shown on a test copy before it goes to main.
