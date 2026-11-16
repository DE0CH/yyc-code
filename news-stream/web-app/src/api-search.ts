// 搜尋's data (the same JSON as /api/search and /api/search/meta, server/search/search.ts)
import { fetchSearch, fetchSearchMeta } from "./fns-search";
import { HttpError } from "./api";
import type { AiTitleFields } from "./api";

export interface SearchHit extends AiTitleFields {
  id: number; match: "word" | "substring"; url: string; /** what 開啟原文 / Original link opens: the article's own address (posts.canonical_url, canonical.py), else url */ link: string; title: string; extract: string;
  website: string | null; board: string | null; author: string | null; source_type: string | null; region: string | null;
  platform: string | null; time: string | null; comment: boolean;
}
export interface SearchResult {
  total: number; page: number; n: number; from: string; to: string; sort: string; terms: string[];
  posts: SearchHit[]; facets: { types: Record<string, number>; sites: Record<string, number> }; took_ms: number;
}
export interface SearchMeta {
  types: { type: string; zh: string; posts: number; sites: { site: string; posts: number; first: string }[] }[];
  regions: Record<string, string>; first_day: string | null; today: string; export_max: number;
}
export type SearchQS = Record<string, string[]>;

const check = <T,>(d: unknown): T => {
  const e = d as { status?: number; error?: string } | null;
  if (e && typeof e === "object" && "error" in e && typeof e.status === "number") throw new HttpError(e.status, e.error || "");
  return d as T;
};

export const getSearch = (q: SearchQS) => fetchSearch({ data: q }).then(d => check<SearchResult>(d));
export const getSearchMeta = () => fetchSearchMeta().then(d => check<SearchMeta>(d));
