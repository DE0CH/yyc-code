// The router (TanStack Start): one QueryClient per router, so a server render never shares another reader's cache;
// the SSR Query integration dehydrates what the loaders fetched into the page and the browser picks it up.
// Preload on intent (Deyao, 2026-10-15): a Link's loader runs when the reader hovers it (defaultPreloadDelay) or
// touches it, filling TanStack Query's cache, so the tap renders from data already loaded or on its way.
import { QueryCache, QueryClient } from "@tanstack/react-query";
import { dataFailed, dataOk, installUpdate } from "./lib/update";
import { startLive } from "./live";
import { createRouter } from "@tanstack/react-router";
import { setupRouterSsrQueryIntegration } from "@tanstack/react-router-ssr-query";
import { routeTree } from "./routeTree.gen";
import { NotFound } from "./routes/-NotFound";

// plain query strings (every value a string): the default JSON-encodes values, e.g. post="2" → post=%222%22, which
// old links and people reading URLs don't expect
const parseSearch = (s: string) => Object.fromEntries(new URLSearchParams(s));
const stringifySearch = (o: Record<string, unknown>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v != null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? "?" + s : "";
};

function saveData() {
  if (typeof navigator === "undefined") return false;
  const c = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  return !!c && (c.saveData === true || /(^|-)2g$/.test(c.effectiveType || ""));
}

export function getRouter() {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({ onError: () => dataFailed(), onSuccess: () => dataOk() }),  // lib/update.ts
    defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 } },
  });
  const router = createRouter({
    routeTree,
    parseSearch,
    stringifySearch,
    context: { queryClient },
    defaultPreload: saveData() ? false : "intent",  // no preloading for a reader who asked to save data
    defaultPreloadDelay: 75,    // ms of hover: a mouse sweeping across the list preloads nothing
    defaultPreloadStaleTime: 0, // the loader runs on every preload; TanStack Query decides whether data is fresh
    scrollRestoration: true,
    defaultNotFoundComponent: NotFound,
  });
  setupRouterSsrQueryIntegration({ router, queryClient });
  installUpdate(router);  // a new deploy: the next page change loads it, never a reload (lib/update.ts)
  startLive();
  return router;
}

declare module "@tanstack/react-router" {
  interface Register { router: ReturnType<typeof getRouter> }
}
