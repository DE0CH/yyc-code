/// <reference types="vite/client" />
// The document every page shares (server-rendered), and the stream (?p=) kept on every link (NAVIGATION.md).
// Its loader reads what the first render may depend on beyond the URL, on the server, so the browser hydrates with the
// same values: the remembered stream (cookie) and the server's clock.
import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, HeadContent, Outlet, retainSearchParams, Scripts } from "@tanstack/react-router";
import css from "../styles/index.css?url";
import { NotFound } from "./-NotFound";
import { readRemembered } from "../lib/remembered";
import { hydrated, hydrateWith } from "../lib/clock";
import { BUILD_ID } from "../lib/build";
import { reloadNow, restoreScroll, subscribeUpdate, updateBroken } from "../lib/update";

const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : typeof v === "number" ? String(v) : undefined);
export interface RootSearch { p?: string }

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  validateSearch: (s: Record<string, unknown>): RootSearch => ({ p: str(s.p) }),
  search: { middlewares: [retainSearchParams(["p"])] },
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1, viewport-fit=cover" },
      // the top bar's colour (styles/house.css --panel: the bar sits on the sheets' ground), light and dark
      { name: "theme-color", content: "#ffffff", media: "(prefers-color-scheme: light)" },
      { name: "theme-color", content: "#1a1d20", media: "(prefers-color-scheme: dark)" },
      { title: "YYC 新聞監察" },
    ],
    links: [
      { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
      { rel: "apple-touch-icon", href: "/apple-touch-icon.png" },
      { rel: "stylesheet", href: css },
    ],
  }),
  loader: () => ({ remembered: readRemembered(), now: Date.now() }),
  component: Root,
  notFoundComponent: NotFound,
  shellComponent: Document,
});

function Root() {
  const { now } = Route.useLoaderData();
  hydrateWith(now);  // the browser's first render: the server's clock (lib/clock.ts)
  useEffect(() => { hydrated(); restoreScroll(); }, []);  // after the reader tapped 已有更新: where they were
  return <><Outlet /><UpdateNote /></>;
}

/** the page cannot go on with the new site (a data request failed while a newer build is live): a small button at the
 *  bottom, a tap reloads (lib/update.ts; no popup). A newer build alone shows nothing: the next page change loads it */
function UpdateNote() {
  const broken = useSyncExternalStore(subscribeUpdate, updateBroken, () => false);
  if (!broken) return null;
  return (
    <button type="button" className="yyc-update" onClick={() => reloadNow()}>
      已有更新<span>點此重新載入</span>
    </button>
  );
}

function Document({ children }: { children: ReactNode }) {
  return (
    // data-update is set by lib/update.ts in the browser, before hydration: not a mismatch
    <html lang="zh-HK" data-build={BUILD_ID} suppressHydrationWarning>
      <head><HeadContent /></head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}
