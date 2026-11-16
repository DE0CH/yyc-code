// 數據分析's data access (the api.ts style): public data from the Worker's server function (fns-dashboard.ts); the AI
// buttons' projection, run and stream stay on the cluster at the same URLs (/api/ai/estimate, /app/ai/<name>).
import { fetchDashboard } from "./fns-dashboard";
import { fetchProfiles } from "./fns";
import { HttpError, type Sentiment, type AiTitleFields } from "./api";
import { fetchLive, readNdjson } from "./lib/ndjson";

export type DashQ = Record<string, string | number | null | undefined>;

const clean = (q: DashQ) => {
  const o: Record<string, string> = {};
  for (const [k, v] of Object.entries(q)) if (v != null && v !== "") o[k] = String(v);
  return o;
};

async function call<T>(name: string, q: DashQ): Promise<T> {
  const d = (await fetchDashboard({ data: { name, q: clean(q) } })) as unknown as T & { error?: string; status?: number };
  if (d && typeof d === "object" && "error" in d && typeof d.error === "string" && "status" in d) throw new HttpError(d.status || 400, d.error);
  return d;
}

export interface DashPost extends AiTitleFields {
  id: number; url: string; /** what 開啟原文 / Original link opens: the article's own address (posts.canonical_url, canonical.py), else url */ link: string; title: string; text?: string | null; source?: string | null; website?: string | null;
  board?: string | null; author?: string | null; platform?: string | null; source_type?: string | null; region?: string | null;
  published?: string | null; seen?: string | null; day?: string | null; sentiment?: Sentiment | null; labels?: string[];
  theme?: string | null; engagement?: Record<string, number>; match?: string | null; thin?: string | null; date_only?: boolean;
  cited?: boolean;
}
export interface PostsData {
  total: number; page: number; size: number; post_found: boolean | null; posts: DashPost[];
  facets: { sentiment: Record<string, number>; labels: Record<string, number>; themes: Record<string, number>; websites: string[];
    source_types: Record<string, number> };
}
export interface HotTopic extends AiTitleFields {
  id: number; title: string; url: string; website: string | null; board: string | null; region: string | null;
  sentiment_index: number; sentiment_score: number; sentiment_level: string; contribution: number; all_post_count: number;
  period_post_count: number; matched_post_count: number; participants: number; view_count: number | null;
  correlation_index: number; initial_date: string; end_date: string; day_span: number;
}
export interface TopicData extends AiTitleFields {
  title: string; url: string; /** what 開啟原文 / Original link opens: the article's own address (posts.canonical_url, canonical.py), else url */ link: string; posts: DashPost[]; trend: { period: string; posts: number }[];
  top_authors: AuthorRow[]; top_sources: SourceRow[];
  stats: { posts: number; participants: number; websites: number; sentiment: Record<string, number>; sentiment_index: number };
}
export interface ThemesData { themes: string[]; generated: string | null; counts: { theme: string; posts: number; sentiment_index: number }[] }
export interface PersonaData {
  author: string; posts: number; matched_posts: number; sentiment_index_on_profile: number; sentiment: Record<string, number>;
  boards: { board: string; posts: number }[]; websites: { website: string; posts: number }[];
  first_seen: string | null; last_seen: string | null; recent: DashPost[]; hot_terms: Term[];
}
// the figures that count posts one per vote (server/analytics.ts): each answer carries the selection-bias note
export interface Metrics {
  posts: number; new_posts: number; topics: number; new_topics: number; websites: number; boards: number; authors: number;
  view_count: number | null; by_source_type: Record<string, number>; sentiment: Record<string, number>; sentiment_index: number;
  sentiment_index_100: number; sentiment_balance: number; posts_per_day: number; authors_per_day: number; topics_per_day: number;
  websites_per_day: number; boards_per_day: number; website_coverage: number; posts_spread: number;
}
export interface TrendRow { period: string; posts: number; views?: number; authors?: number; topics?: number; coverage?: number; sentiment_index: number }
export interface SummaryData { start: string; end: string; metrics: Metrics; cgm: Metrics; news: Metrics; trend: TrendRow[]; method: Record<string, string>; note: string }
export interface Score { value: number; level: string; change_pct: number | null }
export interface ScoresData { scores: Record<string, Score>; spi_series: { day: string; SPI: number; CSS: number }[]; note: string }
export interface AuthorRow { author: string; website: string | null; posts: number; topics: number; sentiment_index: number; sentiment_level: string;
  contribution: number; view_count: number | null; most_active_board: string }
export interface SourceRow { website: string | null; board: string | null; source_type: string | null; region: string | null; posts: number;
  topics: number; participants: number; sentiment_index: number; sentiment_level: string; contribution: number }
export interface Term { term: string; count: number; weight: number }
export interface DailyData {
  html?: string; heading?: string; points?: { topic: string; text: string }[]; action?: string; context?: string;
  note?: string | null; day?: string; summary?: unknown; markdown?: string;
}
export interface ContextData { id: string; title: string; label: string; note: string; total: number; read: number; cited: number; posts: DashPost[] }
export interface Meta {
  profiles: { id: string; name: string; title?: string; kind: string; brief?: string }[]; today: string; source_types: string[];
  labels: Record<string, { emoji: string; group: string; meaning: string }>;
}

export const getMeta = () => fetchProfiles().then(d => d as unknown as Meta);
export const getPosts = (q: DashQ) => call<PostsData>("posts", q);
export const getSummary = (q: DashQ) => call<SummaryData>("summary", q);
export const getScores = (q: DashQ) => call<ScoresData>("scores", q);
export const getAuthors = (q: DashQ) => call<{ authors: AuthorRow[]; note: string }>("authors", q);
export const getSources = (q: DashQ) => call<{ sources: SourceRow[]; note: string }>("sources", q);
export const getTrend = (q: DashQ) => call<{ trend: TrendRow[]; note: string }>("trend", q);
export const getTerms = (q: DashQ) => call<{ terms: Term[]; note: string }>("terms", q);
export const getHotTopics = (q: DashQ) => call<{ topics: HotTopic[] }>("hot_topics", q);
export const getTopic = (q: DashQ) => call<TopicData>("topic", q);
export const getThemes = (q: DashQ) => call<ThemesData>("themes", q);
export const getPersona = (q: DashQ) => call<PersonaData>("persona", q);
export const getDaily = (q: DashQ) => call<DailyData>("ai/daily", q);
export const getContext = (id: string) => call<ContextData>("ai/context", { id });

/** an AI button's model and projected cost before the press (no model call; the cluster answers) */
export interface Estimate { model_name: string; usd: number; cached?: boolean; input_tokens?: number; output_tokens?: number }
export async function getEstimate(q: DashQ): Promise<Estimate> {
  const r = await fetch("/api/ai/estimate?" + new URLSearchParams(clean(q)).toString(), { credentials: "same-origin" });
  if (!r.ok) throw new HttpError(r.status, `HTTP ${r.status}`);
  return r.json() as Promise<Estimate>;
}

/** a finished AI answer ({"t":"done", …}, or a cached answer's plain JSON) */
export interface AiDone {
  html?: string; markdown?: string; at?: string; cached?: boolean; context?: string; note?: string | null; posts?: number;
  usage?: { model_name?: string; usd?: number };
}

/** run an AI action (approved accounts; /app/ai/summary or /app/ai/persona on the cluster), the text as it streams */
export async function aiPost(name: string, body: DashQ, onText?: (t: string) => void): Promise<AiDone> {
  const r = await fetchLive(`/app/ai/${name}`, { method: "POST", redirect: "manual", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { const d = (await r.json().catch(() => ({}))) as { error?: string }; throw new Error(d.error || `HTTP ${r.status}`); }
  let text = "";
  return readNdjson<AiDone>(r, ev => { if (ev.t === "delta") { text += ev.text as string; if (onText) onText(text); } });
}
