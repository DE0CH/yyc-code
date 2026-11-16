// What server.ts checks before TanStack Start sees a request, in this order:
//  1. Deyao's monitor (/app/monitor…) and live dashboard (/app/live…): his Access identity only; anyone else, signed in or not, gets a plain 404 before
//     anything renders (the page is not linked anywhere and doesn't admit it exists). Unknown paths under it: 404.
//  1b. The WiseSearch page (/app/wisers…): only a token of its own Access app (WISERS_AUD: the WiseSearch accounts);
//     anything else gets a plain 404. Its data comes from the relay at /app/wisers/api/ (yyc-edge sends that to the
//     cluster; the relay checks the token and the accounts again).
//  2. Sections behind a feature flag (GATES): off for the site the reader is on → 404 {"error": "not found"}.
import { accessUser, ADMIN_EMAIL, WISERS_AUD } from "./access";
import { config, flagsFor } from "./config";
import { db } from "./db";

/** path (or path prefix) → flag; add a section's paths here when it moves into this app */
export const GATES: [string, string][] = [["/reports", "reports"], ["/api/reports", "reports"],
  ["/search", "search"], ["/api/search", "search"], ["/api/export/search.xlsx", "search"]];

/** the site the reader is on (yyc-edge passes the original Host as X-Forwarded-Host) */
export const hostOf = (req: Request) => (req.headers.get("x-forwarded-host") || req.headers.get("host") || "").split(",")[0].trim();

export const MONITOR_PATHS = new Set(["/app/monitor", "/app/monitor/", "/app/monitor/data", "/app/monitor/flags"]);
/** Deyao's live dashboard (/app/live; his identity only, like the monitor) */
export const LIVE_PATHS = new Set(["/app/live", "/app/live/", "/app/live/data"]);
const deyaoOnly = (path: string) => path === "/app/monitor" || path.startsWith("/app/monitor/") || path === "/app/live" || path.startsWith("/app/live/");
const NOINDEX = { "X-Robots-Tag": "noindex" };

const plain404 = (extra: Record<string, string> = {}) =>
  new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", "Cache-Control": "no-store", ...extra } });

export function flagOf(path: string): string | null {
  const p = path.replace(/\/+$/, "") || "/";
  const hit = GATES.find(([prefix]) => p === prefix || p.startsWith(prefix + "/"));
  return hit ? hit[1] : null;
}

/** a Response that ends the request here, or null to let the app answer */
export async function gate(req: Request): Promise<Response | null> {
  const path = new URL(req.url).pathname;
  if (deyaoOnly(path)) {
    if ((await accessUser(req)) !== ADMIN_EMAIL) return plain404(NOINDEX);
    return MONITOR_PATHS.has(path) || LIVE_PATHS.has(path) ? null : plain404(NOINDEX);
  }
  if (path === "/app/wisers" || path.startsWith("/app/wisers/")) {
    if (!(await accessUser(req, [WISERS_AUD]))) return plain404(NOINDEX);
    return null;
  }
  const flag = flagOf(path);
  if (flag === "search" && (path === "/search" || path === "/search/")) {
    // old links write lists as repeated keys (?type=a&type=b); the app writes them with commas, which the search also
    // reads, and its router keeps one value per key: an old link is sent once to the same search, written with commas
    const raw = new URL(req.url).searchParams;
    if (["type", "site", "region"].some(k => raw.getAll(k).length > 1)) {
      const out = new URLSearchParams();
      for (const k of new Set(raw.keys())) out.set(k, raw.getAll(k).join(","));
      if (flagsFor(await config(await db()), hostOf(req)).search)
        return new Response(null, { status: 302, headers: { Location: "/search?" + out.toString(), "Cache-Control": "no-store" } });
    }
  }
  if (flag && !flagsFor(await config(await db()), hostOf(req))[flag]) {
    return new Response(JSON.stringify({ error: "not found" }), { status: 404,
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
  }
  return null;
}

/** is the flag on for the site of this request (server functions: the page's loader in the browser) */
export async function flagOn(flag: string, host: string) {
  return !!flagsFor(await config(await db()), host)[flag];
}

/** headers added to what the app answered: a document is never used from a cache without asking (no-cache: Back and
 *  a reload get this deploy's page; under /app, no-store: approved accounts' pages stay off the disk), and Deyao's
 *  monitor and the WiseSearch page are never indexed */
export function finish(req: Request, res: Response): Response {
  const path = new URL(req.url).pathname;
  const app = path === "/app" || path.startsWith("/app/");
  const doc = (res.headers.get("Content-Type") || "").startsWith("text/html");
  const noindex = deyaoOnly(path) || path === "/app/wisers" || path.startsWith("/app/wisers/");
  if (!doc && !noindex) return res;
  const out = new Response(res.body, res);
  if (doc) out.headers.set("Cache-Control", app ? "private, no-store" : "private, no-cache");
  if (noindex) out.headers.set("X-Robots-Tag", "noindex");
  return out;
}

/** these have a trailing slash in links in sent emails (/reports/); the router's routes have none, and its
 *  answer to the slash would be a redirect, so the request is passed on without it (the URL the reader sees stays) */
const SLASHED = new Set(["/reports/", "/app/wisers/"]);
export function unslash(req: Request): Request {
  const url = new URL(req.url);
  if (!SLASHED.has(url.pathname)) return req;
  url.pathname = url.pathname.slice(0, -1);
  return new Request(url, req);
}
