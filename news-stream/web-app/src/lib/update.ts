// A new deploy reaches every open page without ever reloading it (Deyao, 2026-10-20: "UI updates should be pushed to
// users quite quickly"; 2026-10-22: "new ui shouldn't refresh the page, because refreshing the page makes my live stream
// exit full screen, and shows the loading bar"). README "Updates reach open pages", NAVIGATION.md.
//
// How a page learns of a newer build: the first request a new Worker instance serves announces its build through the
// LiveHub (server.ts), whose socket (live.ts, open on every page) passes it on at once; the hub also says its own build
// when a socket connects and on every ping (50 s), and every (re)connect asks /version. What the hub says is a hint
// (buildHint): /version decides. Cloudflare releases the new code to the Worker first and to the Durable Object
// eventually (measured 2026-10-20: the hub ran the old code for minutes after /version answered the new build), so
// /version is also asked every minute, and when the page boots, comes back on screen, is restored from the
// back/forward cache, gets focus, goes online, or a data request fails.
//
// What the page does then: nothing the reader can see. It keeps running the build it has (its hashed files stay on the
// site for 14 days, and the data it reads stays compatible: README) and the next page change becomes a full load of the
// new build instead of a client-side transition: a link, or a page change the page makes itself (a form, Back). That is
// the moment the reader expects a new page anyway (SvelteKit's `updated` + beforeNavigate pattern, Next.js's hard
// navigation on a deployment-id mismatch). The page is never reloaded in place by itself: a reload would take a
// full-screen page out of full screen and show the loading bar. A page nobody navigates (the live dashboard) stays on its
// build until someone does.
//
// Only when the page truly cannot go on with the new site (a data request fails while a newer build is waiting) does it
// show a small note, 已有更新 · 點此重新載入 at the bottom (no popup); a tap reloads, keeping URL and scroll. A file of
// this build that fails to load (gone from the site) during a page change makes that page change a full load; outside
// a page change it only marks the build as stale. Neither reloads in place, and each full load for a missing file
// happens at most once per URL a minute (no loop).
import { BUILD_ID } from "./build";

export { BUILD_ID };

const SCROLL = "yyc-update-scroll", FILE_NAV = "yyc-update-filenav";
const POLL_MS = 60_000;

let ready = false;     // a newer build is live, or this build's files are gone: the next page change is a full load
let broken = false;    // a data request failed with a newer build waiting: show the note
let failing = false;   // the last data request failed
let latest: string | null = null;
const listeners = new Set<() => void>();
const busy = new Map<string, boolean>();
const dirty = new Set<Element>();
let lastCheck = 0, installed = false;
let asking: AbortController | null = null;
let leaving = false;   // a navigation away has started (it cancels the files still loading, which report as failed)
let nav: NavRouter | null = null;

/** a navigation this module starts; if the page is still here after 5 s it did not happen (e.g. the network dropped) */
function leave(go: () => void) {
  leaving = true;
  setTimeout(() => { leaving = false; }, 5000);
  go();
}

/** for useSyncExternalStore: should the page show 已有更新 (it cannot go on with the new site) */
export const updateBroken = () => broken;
export function subscribeUpdate(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }

/** work in flight that a full load would lose (e.g. an AI answer still streaming): while on, links stay client-side */
export function setBusy(key: string, on: boolean) { busy.set(key, on); }

const textField = (t: EventTarget | null): t is Element => t instanceof Element && (t.matches("textarea, [contenteditable]") ||
  (t.matches("input") && !/^(checkbox|radio|button|submit|reset|range|color|file|date|hidden|image)$/.test((t as HTMLInputElement).type)));

/** would a full load lose something: text typed and not yet sent (not in the URL), work in flight, or a WiseSearch page
 *  (its search costs quota; loading its results page again asks Wisers again) */
export function unsaved(): boolean {
  if ([...busy.values()].some(Boolean)) return true;
  if (location.pathname === "/app/wisers" || location.pathname.startsWith("/app/wisers/")) return true;
  const inUrl = new Set([...new URLSearchParams(location.search).values()].map(v => v.trim()));
  for (const el of dirty) {
    if (!el.isConnected) { dirty.delete(el); continue; }
    const v = ("value" in el ? String((el as HTMLInputElement).value) : el.textContent || "").trim();
    if (v && !inUrl.has(v)) return true;
  }
  return false;
}

function emit() { for (const fn of listeners) fn(); }

function setState() {
  const v = broken ? "broken" : ready ? "ready" : "current";
  if (document.documentElement.dataset.update !== v) document.documentElement.dataset.update = v;
  emit();
}

/** this page's build is behind: the next page change is a full load */
function markReady() {
  if (ready) return;
  ready = true;
  if (failing) broken = true;
  setState();
}

/** the site runs build `id` (/version) */
function seenBuild(id: string | null | undefined) {
  id = (id || "").trim();
  if (!id || id === BUILD_ID) return;
  latest = id;
  markReady();
}

/** the hub named a build (its own, or one a new Worker instance announced): only a hint, since the hub may run older
 *  code than the Worker for minutes after a deploy; /version (the Worker) decides */
export function buildHint(id: string, announce: boolean) {
  if (!id || id === BUILD_ID) return;
  if (!announce) { void checkVersion(); return; }
  // a new version announced itself: the edge that answers this page's /version may still serve the previous one for
  // a few seconds (measured 2026-10-20: the announcement came 2 s before /version changed), so ask until it agrees
  for (const s of [0, 2, 5, 10, 20, 40]) setTimeout(() => { if (!latest) void checkVersion(true); }, s * 1000);
}

/** a data request (TanStack Query, router.tsx) failed or succeeded. A failure with a newer build live means this page
 *  may no longer fit the site: the note offers the reload (a later success takes the note away again) */
export function dataFailed() {
  failing = true;
  if (ready && !broken) { broken = true; setState(); }
  void checkVersion();
}
export function dataOk() {
  failing = false;
  if (broken) { broken = false; setState(); }
}

function saveScroll() {
  try { sessionStorage.setItem(SCROLL, JSON.stringify({ href: location.href, y: Math.round(scrollY), t: Date.now() })); } catch { /* private mode */ }
}

/** the reader tapped 已有更新: reload into the new build, same URL, the scroll put back (restoreScroll) */
export function reloadNow() {
  saveScroll();
  leave(() => location.reload());
}

/** after the reader's reload: back to where they were. Called once the page has hydrated (__root.tsx: after the pages'
 *  own first effects), and kept for 3 s while the page settles, until the reader moves. */
export function restoreScroll() {
  if (typeof window === "undefined") return;
  let saved: { href: string; y: number; t: number } | null = null;
  try { saved = JSON.parse(sessionStorage.getItem(SCROLL) || "null"); sessionStorage.removeItem(SCROLL); } catch { return; }
  if (!saved || saved.href !== location.href || Date.now() - saved.t > 120_000) return;
  const y = saved.y, until = Date.now() + 3000;
  let moved = false;
  const stop = () => { moved = true; };
  for (const ev of ["wheel", "touchstart", "keydown", "pointerdown"]) addEventListener(ev, stop, { once: true, passive: true });
  const step = () => {
    if (moved || Date.now() > until) return;
    if (Math.abs(scrollY - y) > 1) scrollTo(0, y);
    requestAnimationFrame(step);
  };
  step();
}

/** ask /version (at most every 5 s unless forced; a forced ask replaces one still waiting, e.g. from while offline) */
export async function checkVersion(force = false) {
  if (typeof window === "undefined" || (!force && (asking || Date.now() - lastCheck < 5000))) return;
  asking?.abort();
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 10_000);
  asking = ctl;
  lastCheck = Date.now();
  try {
    const r = await fetch("/version", { cache: "no-store", signal: ctl.signal });
    if (r.ok) seenBuild(await r.text());
  } catch { /* offline: asked again when back online */ } finally {
    clearTimeout(timer);
    if (asking === ctl) asking = null;
  }
}

/** a file of this build failed to load (vite:preloadError). TanStack Router's lazy route component reloads the page in
 *  place on such an error (once per message); the key it checks is set first so it never does. During a page change,
 *  that page change becomes a full load (at most once per URL a minute: a missing file never loops); otherwise the
 *  build is only marked stale, so the next page change is a full load. */
function fileFailed(err: unknown) {
  const msg = err instanceof Error ? err.message : String(err);
  try { sessionStorage.setItem(`tanstack_router_reload:${msg}`, "1"); } catch { /* private mode */ }
  if (leaving) return;  // a navigation away cancels the files still loading
  void checkVersion(true);
  const st = nav?.state;
  const moving = !!st && (st.status === "pending" || st.isLoading);
  markReady();
  if (!moving || !st) return;
  const href = new URL(st.location.href, location.href).href;
  try {
    const g = JSON.parse(sessionStorage.getItem(FILE_NAV) || "null") as { href: string; t: number } | null;
    if (g && g.href === href && Date.now() - g.t < 60_000) {  // already tried: say so instead of loading it again
      broken = true; setState(); return;
    }
    sessionStorage.setItem(FILE_NAV, JSON.stringify({ href, t: Date.now() }));
  } catch { /* private mode: at most this one full load */ }
  leave(() => location.replace(href));
}

type NavRouter = {
  subscribe: (ev: "onBeforeNavigate", fn: (e: { pathChanged: boolean; toLocation: { href: string } }) => void) => unknown;
  state: { status: string; isLoading: boolean; location: { href: string } };
};

/** once, in the browser: the listeners above (router.tsx) */
export function installUpdate(router: NavRouter) {
  if (typeof window === "undefined" || installed) return;
  installed = true;
  nav = router;
  document.documentElement.dataset.update = "current";

  addEventListener("vite:preloadError", e => fileFailed((e as Event & { payload?: unknown }).payload));

  // what the reader typed and has not sent; a submit sends it
  document.addEventListener("input", e => { if (textField(e.target)) dirty.add(e.target); }, true);
  document.addEventListener("submit", e => {
    const f = e.target as HTMLFormElement;
    for (const el of dirty) if (f.contains(el)) dirty.delete(el);
  }, true);

  // a link with a newer build waiting: a full load of the new build
  addEventListener("click", e => {
    if (!ready || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a || (a.target && a.target !== "_self") || a.hasAttribute("download")) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || (u.pathname === location.pathname && u.search === location.search && u.hash)) return;
    if (unsaved()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    leave(() => location.assign(u.href));
  }, true);
  // a page change made by the page itself (a form, Back): the router has already written the new URL, so load it
  router.subscribe("onBeforeNavigate", e => {
    if (ready && e.pathChanged && !leaving && !unsaved()) leave(() => location.replace(e.toLocation.href));
  });

  // on screen again (an iPhone freezes a background tab), restored from the back/forward cache, focus, online: ask
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") void checkVersion(true); });
  addEventListener("pagehide", () => { leaving = true; });
  addEventListener("pageshow", e => { leaving = false; if (e.persisted) void checkVersion(true); });
  addEventListener("focus", () => { void checkVersion(); });
  addEventListener("online", () => { void checkVersion(true); });
  setInterval(() => { if (Date.now() - lastCheck > POLL_MS) void checkVersion(true); }, 30_000);
  void checkVersion(true);  // an old page from the HTTP cache (Back without the back/forward cache)
}
