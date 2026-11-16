// The proof view's data (/app/proof; server/proof.ts). The page's first render reads it on the server, inside the
// request Cloudflare Access let through (the token checked again: approved accounts only); the browser then reads
// /app/proof/data and /app/proof/entry, under /app where Access sends the token (server functions live outside /app).
import { createIsomorphicFn } from "@tanstack/react-start";
import { queryOptions } from "@tanstack/react-query";
import type { ProofList } from "./server/proof";

export type ProofEntry = Awaited<ReturnType<typeof import("./server/proof").entry>>;
export interface ProofFlag { id: number; post_id: number | null; url: string; sid: string | null; note: string | null;
  email: string; at: string; title: string | null; source: string | null }

class HttpError extends Error { constructor(public status: number, msg: string) { super(msg); } }
export const NOT_APPROVED_STATUS = 403;

async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store", credentials: "same-origin" });
  const j = (await r.json().catch(() => ({ error: "HTTP " + r.status }))) as T & { error?: string };
  if (!r.ok) throw new HttpError(r.status, j.error || "HTTP " + r.status);
  return j;
}

/** null: not an approved account (the page says so) */
export const getProofList = createIsomorphicFn()
  .server(async (qs: string): Promise<ProofList | null> => {
    const { getRequest } = await import("@tanstack/react-start/server");
    const { accessUser } = await import("./server/access");
    if (!(await accessUser(getRequest()))) return null;
    const { db } = await import("./server/db");
    const { list, parseSids } = await import("./server/proof");
    const q = new URLSearchParams(qs);
    return list(await db(), parseSids(q.get("sid")), q.get("catalogue") === "1", { id: q.get("stream"), above: q.get("above") });
  })
  .client(async (qs: string): Promise<ProofList | null> => {
    try { return await getJson<ProofList>("/app/proof/data?" + qs); }
    catch (e) { if (e instanceof HttpError && e.status === 403) return null; throw e; }
  });

export const getProofEntry = createIsomorphicFn()
  .server(async (id: number): Promise<ProofEntry | null> => {
    const { getRequest } = await import("@tanstack/react-start/server");
    const { accessUser } = await import("./server/access");
    if (!(await accessUser(getRequest()))) return null;
    const { db } = await import("./server/db");
    const { entry } = await import("./server/proof");
    return entry(await db(), id);
  })
  .client(async (id: number): Promise<ProofEntry | null> => getJson<ProofEntry>("/app/proof/entry?id=" + id));

/** the list's query string, written the same way everywhere (the cache key) */
export function listQS(s: { sid?: string; catalogue?: string; stream?: string; above?: string }) {
  const q = new URLSearchParams();
  if (s.stream) { q.set("stream", s.stream); if (s.above) q.set("above", s.above); }
  else if (s.catalogue === "1") q.set("catalogue", "1");
  else if (s.sid) q.set("sid", s.sid);
  return q.toString();
}

export const proofListQuery = (qs: string) =>
  queryOptions({ queryKey: ["proof-list", qs] as const, queryFn: () => getProofList(qs), staleTime: 60_000, retry: false });
export const proofEntryQuery = (id: number) =>
  queryOptions({ queryKey: ["proof-entry", id] as const, queryFn: () => getProofEntry(id), staleTime: 60_000, retry: 1 });

/** the monitor's open flags (/app/monitor/flags, Deyao only) */
export const monitorFlagsQuery = queryOptions({
  queryKey: ["monitor-flags"] as const, retry: false, staleTime: 30_000,
  queryFn: async () => {
    if (typeof window === "undefined") return { flags: [] as ProofFlag[] };
    return getJson<{ flags: ProofFlag[] }>("/app/monitor/flags");
  },
});

/** stream: reached a stream; stage1: stage 1 scored it at or over the screen for a stream (whatever stage 2 did, or
 *  waiting for it); all: every entry */
export type ProofView = "stream" | "stage1" | "all";
export const VIEWS: ProofView[] = ["stream", "stage1", "all"];
const inV = (e: ProofList["entries"][number], view: ProofView) => view === "all" || (view === "stage1" ? e.stage1 : e.member);
/** the entries of a view, in the list's order */
export const inView = (L: ProofList, view: ProofView) => view === "all" ? L.entries : L.entries.filter(e => inV(e, view));
/** the entry shown: ?at= when it is in the view; else the view's first entry after it in the list (the one before
 *  when none follows); else the view's first. → its index in the view, or -1 for an empty view */
export function pickIndex(L: ProofList, view: ProofView, at: number | null): number {
  const v = inView(L, view);
  if (!v.length) return -1;
  if (!at) return 0;
  const i = v.findIndex(e => e.id === at);
  if (i >= 0) return i;
  const pos = L.entries.findIndex(e => e.id === at);
  if (pos < 0) return 0;
  const after = L.entries.slice(pos).find(e => inV(e, view));
  if (after) return v.indexOf(after);
  return v.length - 1;
}
