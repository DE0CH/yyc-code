// The live dashboard's data (/app/live, /app/live/data; Deyao, 2026-10-21: watch the pipeline live on two displays).
// One read answers every panel. The first read (no cursor) gives each panel its newest PANEL_FIRST cards; a later read
// gives the OLDEST PANEL_MAX cards after the page's cursor, so a burst is handed over in order across reads and none
// is skipped (the page shows them one at a time). Three small indexed queries:
//   ingest    posts (thread IS NULL) by id: posts_live_id
//   filed     stream_items by id (its primary key)
//   the rest  events (news-stream/events.py) by kind and id: events_kind; their post's title and source joined here
//             (kind ai: YYC AI's activity, written by monitor.py's live loop, no post)
// Every read also returns kv live:sys (monitor.py live_once, every 10 s): YYC AI's state from its operator snapshot and
// the systems health (providers' balances with their source, stops, jobs, nodes, Postgres).
// With ?rates=1 (every 10th read) the panels' counts in the last 10 minutes and hour (events_at, posts_seen).
// Deyao only (gates.ts answers 404 to anyone else before this runs). Read-only, like the whole site.
import type { Row, Sql } from "./db";
import { config } from "./config";
import { kvGet } from "./kv";
import type { JarvisLive } from "./jarvis";

export const PANELS = ["ingest", "fulltext", "comments", "stage1", "stage2", "filed", "alert", "problem", "ai"] as const;
export type PanelId = typeof PANELS[number];
export const EVENT_KINDS = PANELS.filter(p => p !== "ingest" && p !== "filed");
/** cards per panel in one answer: a first read only fills the panels (the page keeps 12), a later one hands a burst over */
export const PANEL_MAX = 40;
export const PANEL_FIRST = 12;

export type Cursors = Partial<Record<PanelId, number>>;
export interface Card { id: number; t: number; [k: string]: unknown }
export interface LiveData {
  now: number;                        // the server's clock (ms)
  last_poll: number | null;           // the poller's last poll (poll_state last_poll)
  newest: number | null;              // the newest card's time in this answer
  panels: Record<PanelId, Card[]>;    // newest first
  more: PanelId[];                    // panels with more after these (the next read brings them)
  cursors: Record<PanelId, number>;   // the highest id per panel, to send back as ?c=
  rates?: Record<PanelId, [number, number]>;  // [last 10 min, last hour] (with ?rates=1)
  streams?: { id: string; name: string; threshold: number | null }[];  // the scored streams (on a first read, or ?cfg=1)
  models?: { stage1: string; stage2: string };  // the two stages' models by display name (with streams)
  sys: LiveSys | null;                // kv live:sys (monitor.py live_once): YYC AI's state and the systems health
  jarvis?: JarvisLive | null;         // Jarvis 4's last push (server/jarvis.ts; added by the /app/live/data route)
}

type J = Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
/** kv live:sys as monitor.py writes it (ai_slim, health_slim); every part may instead be {error} */
export interface LiveSys { at: string; ai?: J; health?: J }

/** ?c=ingest:123,dup:45 → cursors (unknown panels and malformed numbers left out) */
export function parseCursors(raw: string | null | undefined): Cursors {
  const out: Cursors = {};
  for (const part of (raw || "").split(",")) {
    const [k, v] = part.split(":");
    if ((PANELS as readonly string[]).includes(k) && /^\d{1,15}$/.test(v || "")) out[k as PanelId] = Number(v);
  }
  return out;
}

const ms = (v: unknown) => (v == null ? null : Math.round(Number(v) * 1000));
const TITLE = (a: string) => `CASE WHEN ${a}.ai_title IS NOT NULL AND ${a}.ai_title <> '' THEN ${a}.ai_title ELSE ${a}.title END`;
const hkIso = (t: number) => new Date(t + 8 * 3600e3).toISOString().slice(0, 19) + "+08:00";

export async function liveData(sql: Sql, cur: Cursors, opts: { config?: boolean; rates?: boolean } = {}): Promise<LiveData> {
  const after = (k: PanelId) => cur[k] ?? 0;
  // a first read: the newest; later: the oldest after the cursor (in order, nothing skipped)
  const dir = (k: PanelId) => (cur[k] == null ? "DESC" : "ASC");
  const firsts = EVENT_KINDS.filter(k => cur[k] == null), laters = EVENT_KINDS.filter(k => cur[k] != null);
  const lim = (k: PanelId) => (cur[k] == null ? PANEL_FIRST : PANEL_MAX + 1);
  const evq = (kinds: PanelId[], d: "ASC" | "DESC") => !kinds.length ? Promise.resolve([] as Row[]) : sql.unsafe(
    `SELECT k.kind, e.id, extract(epoch FROM e.at) AS t, e.post_id, e.data,
            ${TITLE("p")} AS title, p.source, p.platform, p.ai_title_kind = 'written' AS ait
     FROM jsonb_to_recordset($1::jsonb) AS k(kind text, after bigint)
     CROSS JOIN LATERAL (SELECT * FROM events e WHERE e.kind = k.kind AND e.id > k.after ORDER BY e.id ${d} LIMIT ${d === "DESC" ? PANEL_FIRST : PANEL_MAX + 1}) e
     LEFT JOIN posts p ON p.id = e.post_id`,
    [kinds.map(k => ({ kind: k, after: after(k) }))] as never[]) as Promise<Row[]>;
  const now = Date.now();
  const [ingest, filed, evFirst, evLater, poll, sys, rates] = await Promise.all([
    sql.unsafe(`SELECT p.id, extract(epoch FROM p.seen::timestamptz) AS t, p.source, p.platform, p.source_type, p.sid,
                       ${TITLE("p")} AS title, p.ai_title_kind = 'written' AS ait, p.first_run, left(p.text, 300) AS snippet
                FROM posts p WHERE p.thread IS NULL AND p.id > $1 ORDER BY p.id ${dir("ingest")} LIMIT ${lim("ingest")}`,
      [after("ingest")] as never[]) as Promise<Row[]>,
    sql.unsafe(`SELECT si.id, extract(epoch FROM (si.item->>'seen')::timestamptz) AS t, si.stream,
                       coalesce(CASE WHEN p.ai_title IS NOT NULL AND p.ai_title <> '' THEN p.ai_title END, si.item->>'title') AS title,
                       si.item->>'source' AS source, (si.item->>'score')::int AS score, (si.item->>'screen')::int AS screen,
                       si.item->>'reason' AS reason, si.item->>'category' AS category, si.item->>'sentiment' AS sentiment,
                       si.item->'labels' AS labels, (si.item->>'backpost')::boolean AS backpost
                FROM stream_items si LEFT JOIN posts p ON p.id = (si.item->>'post_id')::bigint
                WHERE si.id > $1 ORDER BY si.id ${dir("filed")} LIMIT ${lim("filed")}`, [after("filed")] as never[]) as Promise<Row[]>,
    evq(firsts, "DESC"), evq(laters, "ASC"),
    sql`SELECT v FROM poll_state WHERE k = 'last_poll'`,
    kvGet<LiveSys | null>(sql, "live:sys", null),
    opts.rates ? Promise.all([
      sql.unsafe(`SELECT kind, count(*) FILTER (WHERE at > now() - interval '10 minutes') AS m10, count(*) AS h1
                  FROM events WHERE at > now() - interval '1 hour' GROUP BY kind`),
      sql.unsafe(`SELECT count(*) FILTER (WHERE seen >= $1) AS m10, count(*) AS h1 FROM posts WHERE seen >= $2 AND thread IS NULL`,
        [hkIso(now - 600e3), hkIso(now - 3600e3)] as never[]),
      sql.unsafe(`SELECT count(*) FILTER (WHERE item->>'seen' >= $1) AS m10, count(*) AS h1 FROM stream_items
                  WHERE id > (SELECT coalesce(max(id), 0) - 2000 FROM stream_items) AND item->>'seen' >= $2`,
        [hkIso(now - 600e3), hkIso(now - 3600e3)] as never[]),
    ]) : Promise.resolve(null),
  ]);
  const panels = Object.fromEntries(PANELS.map(p => [p, [] as Card[]])) as Record<PanelId, Card[]>;
  for (const r of ingest)
    panels.ingest.push({ id: r.id, t: ms(r.t)!, source: r.source, platform: r.platform, type: r.source_type, title: r.title,
      ait: !!r.ait || undefined, backlog: !!r.first_run || undefined, snippet: r.snippet });
  for (const r of filed)
    panels.filed.push({ id: r.id, t: ms(r.t)!, stream: r.stream, title: r.title, source: r.source, score: r.score, screen: r.screen,
      reason: r.reason, category: r.category, sentiment: r.sentiment, labels: r.labels, backpost: !!r.backpost || undefined });
  for (const r of [...evFirst, ...evLater]) {
    const d = (r.data || {}) as Record<string, unknown>;
    const c: Card = { ...d, id: r.id, t: ms(r.t)!, post: r.post_id, title: r.title, source: r.source, platform: r.platform,
      ait: !!r.ait || undefined };
    panels[r.kind as PanelId].push(c);
  }
  const more: PanelId[] = [];
  for (const p of PANELS) {
    const list = panels[p];
    if (list.length > PANEL_MAX) {
      // a first read keeps the newest PANEL_MAX; a later one the oldest (the next read brings the rest)
      list.sort((a, b) => (cur[p] == null ? b.id - a.id : a.id - b.id)).splice(PANEL_MAX);
      if (cur[p] != null) more.push(p);
    }
    list.sort((a, b) => b.id - a.id);
  }
  const cursors = Object.fromEntries(PANELS.map(p => [p, Math.max(after(p), ...panels[p].map(c => c.id))])) as Record<PanelId, number>;
  const newest = Math.max(0, ...PANELS.flatMap(p => panels[p].slice(0, 1).map(c => c.t))) || null;
  const lp = (poll as Row[])[0]?.v;
  const out: LiveData = { now, last_poll: typeof lp === "string" ? Date.parse(lp) || null : null, newest, panels, more, cursors, sys };
  if (rates) {
    const [ev, po, fi] = rates as [Row[], Row[], Row[]];
    out.rates = Object.fromEntries(PANELS.map(p => [p, [0, 0]])) as Record<PanelId, [number, number]>;
    for (const r of ev) if ((PANELS as readonly string[]).includes(r.kind)) out.rates[r.kind as PanelId] = [Number(r.m10), Number(r.h1)];
    out.rates.ingest = [Number(po[0]?.m10 || 0), Number(po[0]?.h1 || 0)];
    out.rates.filed = [Number(fi[0]?.m10 || 0), Number(fi[0]?.h1 || 0)];
  }
  if (opts.config || PANELS.some(p => cur[p] == null)) {
    const cfg = await config(sql);
    out.streams = cfg.streams.filter(s => cfg.scored.includes(s.id)).map(s => ({ id: s.id, name: s.name, threshold: cfg.thresholds[s.id] ?? null }));
    if (cfg.models) out.models = cfg.models;
  }
  return out;
}
