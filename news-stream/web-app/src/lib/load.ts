// A route loader's data: awaited on the server (it is in the server-rendered page, then dehydrated so the browser
// doesn't fetch it again); in the browser started but not awaited, so a tap opens the page at once and it shows
// 載入中 until the data is in (preload on intent usually has it there already).
import type { QueryClient, FetchQueryOptions } from "@tanstack/react-query";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function load(qc: QueryClient, queries: FetchQueryOptions<any, any, any, any>[]): Promise<void> | void {
  // allSettled, not all: one failed read (搜尋's 400 on a bad query) must not end the wait while the others are still
  // pending, or the server renders without them and the browser, which has them, fails to hydrate (2026-10-20)
  const all = Promise.allSettled(queries.map(q => qc.ensureQueryData(q)));
  if (typeof window === "undefined") return all.then(() => undefined);
}
