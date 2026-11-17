// A route loader's data: awaited on the server (it is in the server-rendered page, then dehydrated so the browser
// doesn't fetch it again); in the browser started but not awaited, so a tap opens the page at once and it shows
// 載入中 until the data is in (preload on intent usually has it there already).
import type { QueryClient, FetchQueryOptions } from "@tanstack/react-query";
import { getMe } from "../api";
import { keys, meQuery } from "../queries";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function load(qc: QueryClient, queries: FetchQueryOptions<any, any, any, any>[]): Promise<void> | void {
  // allSettled, not all: one failed read (搜尋's 400 on a bad query) must not end the wait while the others are still
  // pending, or the server renders without them and the browser, which has them, fails to hydrate (2026-10-20)
  const all = Promise.allSettled(queries.map(q => qc.ensureQueryData(q)));
  if (typeof window === "undefined") return all.then(() => undefined);
}

/** What the page shows only to approved accounts (the full text, the scores), read with the page's public data once
 *  the reader is known to be one (Deyao, 2026-11-17).
 *  In the browser (a hover or touch preloading a link, or the tap itself): started, not awaited; /app/me is asked
 *  first only when the answer is not in the cache yet.
 *  On the server: awaited, so the page's HTML carries it (server/reader.ts says who is reading). Nothing that failed
 *  stays in the cache: the render sends every query to the browser, and a failed one arrives there as an unhandled
 *  error; what is missing the browser reads itself, as it did before. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function loadGated(qc: QueryClient, queries: FetchQueryOptions<any, any, any, any>[]): Promise<void> | void {
  if (typeof window !== "undefined") {
    qc.ensureQueryData(meQuery).then(me => { if (me) for (const q of queries) void qc.prefetchQuery(q); }, () => undefined);
    return;
  }
  return (async () => {
    const me = await getMe();
    if (me === undefined) return;  // cannot tell: the browser asks /app/me
    qc.setQueryData(keys.me, me);
    if (!me) return;
    await Promise.all(queries.map(async q => {
      await qc.prefetchQuery(q);
      if (qc.getQueryState(q.queryKey)?.status !== "success") qc.removeQueries({ queryKey: q.queryKey, exact: true });
    }));
  })();
}
