// Live updates. Pages say WHAT stays live (`useLive(queryKey)`); this module keeps ONE WebSocket per page to the
// site's LiveHub (Durable Object, server.ts: GET /live?t=<topic>…), subscribed to the topics of the live queries.
// The cluster announces each change (news-stream/live.py: "feed:<day>", "scores:<day>", "item:<post id>"), and the
// queries on that topic are read again (TanStack Query invalidation). While the socket is down, the queries are
// refreshed every minute instead, so a page never goes stale.
// Every page keeps the socket open, live queries or not: the hub says which build the site runs when the socket connects
// and on every ping, and every (re)connect asks /version, which is how an open page learns of a new deploy (lib/update.ts). The socket is
// closed when the page is left, so the page stays eligible for the back/forward cache.
import { useEffect } from "react";
import { buildHint, checkVersion } from "./lib/update";
import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";

export const LIVE_INTERVAL_MS = 60_000;

const hktDay = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);

/** the LiveHub topic a query key stands for, or null (not live-updated) */
export function topicOf(key: QueryKey): string | null {
  const [kind, arg] = key as [string, unknown];
  const day = (d: unknown) => (typeof d === "string" && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : hktDay());
  if (kind === "feed") return "feed:" + day(arg);
  if (kind === "scores") return "scores:" + day(arg);
  if (kind === "item" && typeof arg === "number") return "item:" + arg;
  return null;
}

type Sub = { key: QueryKey; topic: string | null };
const subs = new Map<number, Sub>();
let nextId = 1, qcRef: QueryClient | null = null, ws: WebSocket | null = null, wsTopics = "", timer = 0, retry = 0;
let away = false;  // the page is in the back/forward cache
let open = false, heard = 0;  // heard: when the socket last said anything (a reply to every ping at least)

function invalidate(pred: (s: Sub) => boolean) {
  for (const s of subs.values()) if (pred(s)) qcRef?.invalidateQueries({ queryKey: s.key, exact: true });
}

function wanted() { return [...new Set([...subs.values()].map(s => s.topic).filter((t): t is string => !!t))].sort(); }

function connect() {
  if (away) return;
  const topics = wanted(), sig = topics.join(" ");
  if (sig === wsTopics && ws) return;
  ws?.close(); ws = null; open = false; wsTopics = sig;
  if (typeof WebSocket === "undefined") return;
  const url = new URL("/live", location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  for (const t of topics) url.searchParams.append("t", t);
  const s = new WebSocket(url);
  ws = s;
  // (re)connected: a deploy shuts the hub down, which closes every socket; the hub can still run the old code for a few
  // minutes after the Worker runs the new one (Cloudflare releases Durable Objects eventually), so ask the Worker too
  s.onopen = () => { open = true; retry = 0; heard = Date.now(); void checkVersion(); };
  s.onmessage = e => {
    heard = Date.now();
    try {
      const m = JSON.parse(String(e.data));
      if (m.changed) invalidate(x => x.topic === m.changed);
      if (m.build) buildHint(m.build, !!m.announce);
    } catch { /* not JSON */ }
  };
  s.onclose = () => {
    if (ws !== s) return;
    ws = null; open = false; wsTopics = "";
    window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** retry++));  // back off, then try again
  };
}

function schedule() { clearTimeout(timer); timer = window.setTimeout(connect, 250); }

/** open the socket on every page (router.tsx), so the page hears of a new build */
export function startLive() { if (typeof window !== "undefined") schedule(); }

if (typeof window !== "undefined") {
  // the fallback while the socket is down, and a refresh when the tab comes back
  window.setInterval(() => { if (!open && document.visibilityState === "visible") invalidate(() => true); }, LIVE_INTERVAL_MS);
  // back on screen (an iPhone freezes a background tab: timers stop, a socket can die without a close), restored from
  // the back/forward cache, or back online: read again at once, and replace a socket that has gone quiet
  const wake = () => {
    if (document.visibilityState === "hidden") return;
    invalidate(() => true);
    retry = 0;
    if (ws && open && Date.now() - heard > 120_000) ws.close();  // onclose reconnects
    else if (!ws) connect();
  };
  document.addEventListener("visibilitychange", wake);
  // an open socket keeps a page out of the back/forward cache (Chrome, Safari): closed when the page is left, opened
  // again when it is restored
  window.addEventListener("pagehide", () => {
    away = true;
    const s = ws;
    ws = null; open = false; wsTopics = "";
    s?.close();
  });
  window.addEventListener("pageshow", e => { away = false; if (e.persisted) wake(); });
  window.addEventListener("online", wake);
  window.setInterval(() => { if (open) ws?.send("ping"); }, 50_000);  // keeps proxies from closing an idle socket
}

export function useLive(queryKey: QueryKey, opts: { enabled?: boolean } = {}) {
  const qc = useQueryClient();
  const enabled = opts.enabled ?? true;
  const key = JSON.stringify(queryKey);
  useEffect(() => {
    if (!enabled) return;
    qcRef = qc;
    const id = nextId++;
    subs.set(id, { key: JSON.parse(key), topic: topicOf(JSON.parse(key)) });
    schedule();
    return () => { subs.delete(id); schedule(); };
  }, [qc, key, enabled]);
}
