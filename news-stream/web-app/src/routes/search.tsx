// 搜尋: /search?q=…&type=…&site=…&region=…&from=…&to=…&sort=…&title=1&comments=1&page=….
// Every search is a URL and its own history entry. Lists are comma-separated (the search reads both forms); an old
// link with a repeated key (?type=a&type=b) is redirected once to the same search written with commas
// (server/gates.ts: the router keeps only one value of a key).
import { createFileRoute } from "@tanstack/react-router";
import { load } from "../lib/load";
import { flagsQuery, profilesQuery } from "../queries";
import { searchMetaQuery, searchQuery } from "../queries-search";
import { SearchPage } from "./-SearchPage";

export const KEYS = ["q", "type", "site", "region", "from", "to", "sort", "title", "comments", "page"] as const;
export type SearchParams = Partial<Record<(typeof KEYS)[number] | "p", string>>;

/** the search as the page's query string: fixed key order, empty values left out */
export function searchQS(s: SearchParams, noPage = false): string {
  const u = new URLSearchParams();
  for (const k of KEYS) {
    const v = s[k];
    if (v == null || v === "") continue;
    if (k === "page" && (noPage || v === "1")) continue;
    if (k === "sort" && v === "date") continue;
    u.set(k, v);
  }
  return u.toString();
}

/** a search runs when the URL names a query, type, outlet or start day */
export const hasSearch = (s: SearchParams) => s.q !== undefined || !!s.type || !!s.site || !!s.from;

/** the query string as /api/search reads it (blank values dropped) */
export const toQS = (s: SearchParams) => {
  const q: Record<string, string[]> = {};
  for (const k of KEYS) if (s[k]) q[k] = [s[k]!];
  return q;
};

const str = (v: unknown) => (typeof v === "string" ? v : typeof v === "number" ? String(v) : undefined);

export const Route = createFileRoute("/search")({
  validateSearch: (s: Record<string, unknown>): SearchParams =>
    Object.fromEntries([...KEYS, "p"].map(k => [k, str(s[k])]).filter(([, v]) => v !== undefined)),
  loaderDeps: ({ search }) => ({ qs: searchQS(search), has: hasSearch(search), s: search }),
  loader: ({ context, deps }) => load(context.queryClient, [searchMetaQuery, profilesQuery, flagsQuery,
    ...(deps.has ? [searchQuery(deps.qs, toQS(deps.s))] : [])]),
  head: () => ({ meta: [{ title: "搜尋｜YYC 新聞監察" }] }),
  component: SearchPage,
});
