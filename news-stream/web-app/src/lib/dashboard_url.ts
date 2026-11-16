// dashboard_url: the absolute link to an item's details page on the YYC monitoring site, for the data APIs (yyc-mcp's
// MCP tools and REST API, mcp-edge/src/tools.ts) so a model or script can cite the page a reader opens (Deyao,
// 2026-10-21: "the news monitor api/mcp should include link to the details page on the dashboard"). Python's twin is
// news-stream/dashboard_url.py (the AI server's /app/api/v1); tests/test_dashboard_url.py checks they agree.
// No imports: mcp-edge's node tests load it as is.
//
// Host: the main site (newswatch.yyc-internal.com) serves 串流, the item page and 數據分析 to everyone; 報告 and 搜尋 are on only on
// the beta site (yyc-beta.deyaochen.com, its own Access app: Deyao and the people he adds; flags.json `reports` and
// `search` target only that host), so their links go there. BETA_ONLY follows flags.json (the test checks it).

export const MAIN = "https://newswatch.yyc-internal.com";
export const BETA = "https://yyc-beta.deyaochen.com";
/** the sections whose flag is on only on the beta site (deploy/apps/flags/flags.json) */
export const BETA_ONLY = ["reports", "search"] as const;

type V = string | number | null | undefined;
/** 數據分析's URL params in the page's own order (-DashboardPage.tsx KEYS) */
const DASH_KEYS = ["v", "p", "from", "to", "sent", "label", "theme", "type", "site", "author", "q", "page", "top", "by", "bucket", "a", "tid", "post"] as const;
export type DashParams = Partial<Record<(typeof DASH_KEYS)[number], V>>;
/** 搜尋's URL params in the page's own order (routes/search.tsx KEYS) */
const SEARCH_KEYS = ["q", "type", "site", "region", "from", "to", "sort", "title", "comments", "page"] as const;
export type SearchParams = Partial<Record<(typeof SEARCH_KEYS)[number], V>>;

const qs = (pairs: [string, V][]) => {
  const u = new URLSearchParams();
  for (const [k, v] of pairs) if (v != null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? "?" + s : "";
};
const hostOf = (section: string) => ((BETA_ONLY as readonly string[]).includes(section) ? BETA : MAIN);

/** a post's item page: /item/<id> */
export const postUrl = (id: number | string) => `${hostOf("item")}/item/${encodeURIComponent(String(id))}`;
/** a post known by its url (串流's items): /item?p=<stream>&url=<url>, which redirects to /item/<id> */
export const postByUrl = (url: string, stream?: string | null) => `${hostOf("item")}/item${qs([["p", stream], ["url", url]])}`;
/** 串流: a stream's filed items, optionally on one day */
export const streamUrl = (stream?: string | null, day?: string | null) => `${hostOf("stream")}/${qs([["p", stream], ["d", day]])}`;
/** 數據分析 with the given view and filters (params in the page's order; empty ones left out) */
export const dashboardUrl = (p: DashParams) => `${hostOf("dashboard")}/dashboard${qs(DASH_KEYS.map(k => [k, p[k]]))}`;
/** one hot topic (熱門話題 → the topic); the id is only valid with the same stream, period and filters */
export const topicUrl = (id: number | string, p: DashParams) => dashboardUrl({ ...p, v: "hot", tid: id });
/** one author (人物) */
export const personUrl = (author: string, p: DashParams = {}) => dashboardUrl({ ...p, v: "persona", a: author });
/** a theme's posts (主題 → 帖子 filtered to it) */
export const themeUrl = (theme: string, p: DashParams) => dashboardUrl({ ...p, v: "posts", theme });
/** an outlet's posts in a stream (來源 → 帖子 filtered to it) */
export const sourceUrl = (site: string, p: DashParams) => dashboardUrl({ ...p, v: "posts", site });
/** 搜尋 (the whole corpus, every stream) with the given query and filters */
export const searchUrl = (p: SearchParams) => `${hostOf("search")}/search${qs(SEARCH_KEYS.map(k => [k, p[k]]))}`;
/** one report file (/reports/<day>/<stream>/<file>), or 報告 itself */
export const reportUrl = (path?: string | null) => `${hostOf("reports")}${path ? path.replace(/^https?:\/\/[^/]+/, "") : "/reports/"}`;
