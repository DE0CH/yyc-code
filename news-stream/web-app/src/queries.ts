// Query keys and the shared queries (TanStack Query). Data comes only from ./api. The QueryClient is made per router
// (router.tsx): one per server render, one in the browser.
import { queryOptions, useQuery } from "@tanstack/react-query";
import * as api from "./api";

export const keys = {
  feed: (date: string | null) => ["feed", date ?? "today"] as const,
  profiles: ["profiles"] as const,
  flags: ["flags"] as const,
  me: ["me"] as const,
  scores: (date: string | null) => ["scores", date ?? "today"] as const,
  item: (id: number) => ["item", id] as const,
  itemByUrl: (url: string) => ["item-url", url] as const,
  itemCoverage: (id: number) => ["item-coverage", id] as const,
  itemText: (id: number) => ["item-text", id] as const,
  itemVideo: (id: number) => ["item-video", id] as const,
  itemAI: (id: number) => ["item-ai", id] as const,
  reports: ["reports"] as const,
};

export const itemQuery = (id: number) =>
  queryOptions({ queryKey: keys.item(id), queryFn: () => api.getItem({ id }), retry: false, staleTime: 30_000 });
export const itemByUrlQuery = (url: string) =>
  queryOptions({ queryKey: keys.itemByUrl(url), queryFn: () => api.getItem({ url }), retry: false, staleTime: 30_000 });
export const itemCoverageQuery = (id: number) =>
  queryOptions({ queryKey: keys.itemCoverage(id), queryFn: () => api.getItemCoverage(id), retry: 1, staleTime: 5 * 60_000 });

export const feedQuery = (date: string | null) =>
  queryOptions({ queryKey: keys.feed(date), queryFn: () => api.getFeed(date), staleTime: 30_000 });

export const profilesQuery = queryOptions({ queryKey: keys.profiles, queryFn: api.getProfiles, staleTime: 10 * 60_000 });
export const flagsQuery = queryOptions({ queryKey: keys.flags, queryFn: api.getFlags, staleTime: 2 * 60_000 });

export const useProfiles = () => useQuery(profilesQuery);
export const useFlags = () => useQuery(flagsQuery);
/** the approved account's email, or null (browser only: Access answers /app/me) */
export const useMe = () => useQuery({ queryKey: keys.me, queryFn: api.getMe, staleTime: 10 * 60_000, enabled: typeof window !== "undefined" });

export const reportsQuery = queryOptions({ queryKey: keys.reports, queryFn: api.getReports, staleTime: 60_000 });
