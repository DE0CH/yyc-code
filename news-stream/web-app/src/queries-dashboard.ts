// 數據分析's query keys and queries (the queries.ts style). Data comes only from ./api-dashboard.
import { queryOptions } from "@tanstack/react-query";
import * as api from "./api-dashboard";
import type { DashQ } from "./api-dashboard";

const k = (q: DashQ) => JSON.stringify(Object.entries(q).filter(([, v]) => v != null && v !== "").sort());

export const dashKeys = {
  meta: ["dash-meta"] as const,
  posts: (q: DashQ) => ["dash-posts", k(q)] as const,
  summary: (q: DashQ) => ["dash-summary", k(q)] as const,
  scores: (q: DashQ) => ["dash-scores", k(q)] as const,
  authors: (q: DashQ) => ["dash-authors", k(q)] as const,
  sources: (q: DashQ) => ["dash-sources", k(q)] as const,
  trend: (q: DashQ) => ["dash-trend", k(q)] as const,
  terms: (q: DashQ) => ["dash-terms", k(q)] as const,
  hot: (q: DashQ) => ["dash-hot", k(q)] as const,
  topic: (q: DashQ) => ["dash-topic", k(q)] as const,
  themes: (q: DashQ) => ["dash-themes", k(q)] as const,
  persona: (q: DashQ) => ["dash-persona", k(q)] as const,
  daily: (q: DashQ) => ["dash-daily", k(q)] as const,
  context: (id: string) => ["dash-context", id] as const,
};

// the server restarts for a minute after a deploy: a failed request is retried every 10 s (the page says so)
const retryDown = (n: number, e: unknown) => n < 1 && !(e instanceof Error && /^HTTP 4|no such|dates|at most|invalid|unknown/.test(e.message));

export const metaQuery = queryOptions({ queryKey: dashKeys.meta, queryFn: api.getMeta, staleTime: 60_000 });
export const postsQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.posts(q), queryFn: () => api.getPosts(q), retry: retryDown });
export const summaryQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.summary(q), queryFn: () => api.getSummary(q), retry: retryDown });
export const scoresQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.scores(q), queryFn: () => api.getScores(q), retry: retryDown });
export const authorsQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.authors(q), queryFn: () => api.getAuthors(q), retry: retryDown });
export const sourcesQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.sources(q), queryFn: () => api.getSources(q), retry: retryDown });
export const trendQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.trend(q), queryFn: () => api.getTrend(q), retry: retryDown });
export const termsQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.terms(q), queryFn: () => api.getTerms(q), retry: retryDown });
export const hotQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.hot(q), queryFn: () => api.getHotTopics(q), retry: retryDown });
export const topicQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.topic(q), queryFn: () => api.getTopic(q), retry: retryDown });
export const themesQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.themes(q), queryFn: () => api.getThemes(q), retry: retryDown });
export const personaQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.persona(q), queryFn: () => api.getPersona(q), retry: retryDown });
export const dailyQuery = (q: DashQ) => queryOptions({ queryKey: dashKeys.daily(q), queryFn: () => api.getDaily(q).catch(() => ({})), retry: false });
export const contextQuery = (id: string) => queryOptions({ queryKey: dashKeys.context(id), queryFn: () => api.getContext(id), retry: false, staleTime: 5 * 60_000 });
