// 搜尋's query keys and queries (TanStack Query)
import { queryOptions } from "@tanstack/react-query";
import * as api from "./api-search";

export const searchKeys = {
  search: (qs: string) => ["search", qs] as const,
  meta: ["search-meta"] as const,
};

/** qs = the search's canonical query string (searchQS in -SearchPage.tsx), q = the same as lists */
export const searchQuery = (qs: string, q: api.SearchQS) =>
  queryOptions({ queryKey: searchKeys.search(qs), queryFn: () => api.getSearch(q), retry: false, staleTime: 60_000 });
export const searchMetaQuery = queryOptions({ queryKey: searchKeys.meta, queryFn: api.getSearchMeta, staleTime: 10 * 60_000, retry: 1 });
