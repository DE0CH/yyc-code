// The proof view (/app/proof; Deyao, 2026-10-20: "click arrow to see each surfaced database entry, so i can check if
// they look correct"): the stored posts of one or more sources (posts.sid), one entry at a time, as the pipeline holds
// them. Approved accounts only (routes under /app, the Access token checked again). Read-only, like the whole site;
// the flag button posts to the AI server (POST /app/proof/flag, proof.py), and the flags are read here.
import type { Row, Sql } from "./db";
import { config, type EdgeConfig } from "./config";
import { aiScores, text, view } from "./item";

/** at most this many entries in one list (newest first within each source) */
export const LIST_MAX = 20000;
const SID = /^[A-Za-z0-9_:.-]{1,120}$/;

export interface ProofSource { sid: string; name: string | null; url: string | null; kind: string | null; catalogue: boolean;
  total: number; stream: number; stage1: number }
/** a stream view (?stream=ID&above=N; Deyao, 2026-10-22: "everything about wang fuk court that scored above 10"):
 *  every post, from any source, whose score for that stream at either stage is over N */
export interface ProofStream { id: string; name: string; above: number }
export interface ProofList { sids: string[]; catalogue: boolean; stream: ProofStream | null; sources: ProofSource[];
  entries: { id: number; sid: string; member: boolean; stage1: boolean }[]; counts: { all: number; stream: number; stage1: number };
  truncated: boolean; screen: number }

/** ?stream=ID&above=N → a stream view, when ID is a scored stream (N: 0–100, default 10) */
export function parseStream(c: EdgeConfig, id: string | null | undefined, above: string | null | undefined): ProofStream | null {
  const st = c.streams.find(s => s.id === id && c.scored.includes(s.id));
  if (!st) return null;
  const n = Number(above ?? 10);
  return { id: st.id, name: st.name || st.id, above: Number.isInteger(n) && n >= 0 && n <= 100 ? n : 10 };
}

/** ?sid=a,b,c → the ids, in the order given, without repeats or malformed ones */
export function parseSids(raw: string | null | undefined): string[] {
  return [...new Set((raw || "").split(",").map(s => s.trim()).filter(s => SID.test(s)))].slice(0, 500);
}

/** the scored streams' thresholds (a score at or over its stream's threshold = in that stream) */
function thresholds(c: EdgeConfig): Record<string, number> {
  return Object.fromEntries(c.streams.filter(s => c.scored.includes(s.id) && c.thresholds[s.id] != null).map(s => [s.id, c.thresholds[s.id]]));
}

/** stage 1's score from which stage 2 scores a pair again (edge:config `screen` = streams.SCREEN; every threshold is at
 *  least that) */
export const screenOf = (c: EdgeConfig) => c.screen ?? Math.min(...Object.values(thresholds(c)));

/** two-stage scoring (streams.confirm), the "Passed stage 1" view (Deyao, 2026-10-22: "another tab that is everything
 *  that pass stage 1"): the post ids whose latest stage-1 event (events.py kind stage1; events are kept 3 days) has a
 *  scored stream at or over the screen and no successful stage-2 event after it: waiting for stage 2, or stage 2 failed
 *  and the post is due again (stage 2 runs before anything is stored, so `scores` does not show these yet). SQL for a
 *  CTE body; $T = the scored streams' thresholds (jsonb), $S = the screen */
const PENDING_SQL = (T: string, S: string) => `SELECT e.post_id FROM (SELECT DISTINCT ON (post_id) id, post_id, data FROM events
      WHERE kind = 'stage1' AND post_id IS NOT NULL ORDER BY post_id, id DESC) e
    LEFT JOIN (SELECT post_id, max(id) AS id FROM events WHERE kind = 'stage2' AND post_id IS NOT NULL AND NOT (data ? 'failed')
               GROUP BY post_id) f ON f.post_id = e.post_id
    WHERE (f.id IS NULL OR f.id < e.id) AND jsonb_typeof(e.data -> 'scores') = 'object'
      AND EXISTS (SELECT 1 FROM jsonb_each_text(e.data -> 'scores') k WHERE (${T}::jsonb ? k.key) AND k.value ~ '^[0-9]{1,3}$' AND k.value::int >= ${S})`;
/** a stored score whose stage 1 reached the screen: stage 2 scored it (screen set, stage 1's score in it), or stage 1
 *  was the only stage (no screen; the stage-1 model's, or no model: scored before 20 Oct 2026). A stage-2 score with no
 *  stage-1 one (a filed post re-checked) did not pass stage 1. Alias s; $T thresholds, $S screen, $M the stage-1 model */
const PASSED1_SQL = (T: string, S: string, M: string) => `(${T}::jsonb ? s.stream) AND coalesce(s.screen, s.score) >= ${S}
    AND (s.screen IS NOT NULL OR s.model IS NULL OR s.model = ${M})`;

/** the list to step through. catalogue: every sid of sites.json (edge:config `catalogue`), source by source in the
 *  catalogue's order, each newest first; otherwise the given sids' posts together, newest first. Comments and replies
 *  are not entries (each entry counts its own). member = the post reached a stream: it was filed (a stream_items row)
 *  or a scored stream's score is at or over that stream's threshold (the item page's 達標); stage1 = stage 1 scored it at
 *  or over the screen for a scored stream (PASSED1_SQL), or it is waiting for stage 2 (PENDING_SQL). streamQ
 *  (?stream=ID&above=N, a scored stream): every source's posts with that stream's score at either stage over N, member
 *  and stage1 about that stream alone, the sources those posts come from (the most posts first) */
export async function list(sql: Sql, sidsIn: string[], catalogue: boolean, streamQ?: { id: string | null; above: string | null }): Promise<ProofList> {
  const c = await config(sql);
  const cat = c.catalogue || [];
  const stream = streamQ?.id ? parseStream(c, streamQ.id, streamQ.above) : null;
  if (stream) catalogue = false;
  const sids = stream ? [] : catalogue ? cat.map(s => s.id) : sidsIn;
  const screen = screenOf(c);
  if (!sids.length && !stream) return { sids: [], catalogue, stream, sources: [], entries: [], counts: { all: 0, stream: 0, stage1: 0 }, truncated: false, screen };
  // a stream view: its posts are those with a score or a stage-1 score over N for that stream ($1 = {id, above}), and
  // the views are about that stream alone (its threshold, filed in it, stage 1 at or over the screen for it); else the
  // sources' posts ($1 = the sids) and every scored stream
  const T = stream ? Object.fromEntries(Object.entries(thresholds(c)).filter(([k]) => k === stream.id)) : thresholds(c);
  const which = stream
    ? `p.id IN (SELECT s.post_id FROM scores s WHERE s.stream = ($1::jsonb ->> 'id') AND greatest(s.score, coalesce(s.screen, 0)) > ($1::jsonb ->> 'above')::int)`
    : `p.sid = ANY(ARRAY(SELECT jsonb_array_elements_text($1::jsonb)))`;
  const filed = stream ? `i.url = p.url AND i.stream = ($1::jsonb ->> 'id')` : `i.url = p.url`;
  const rows = await sql.unsafe(`WITH pend AS (${PENDING_SQL("$2", "$3")})
      SELECT p.id, p.sid, p.source,
        (EXISTS (SELECT 1 FROM stream_items i WHERE ${filed})
         OR EXISTS (SELECT 1 FROM scores s WHERE s.post_id = p.id AND ($2::jsonb ? s.stream) AND s.score >= ($2::jsonb ->> s.stream)::int)) AS member,
        (EXISTS (SELECT 1 FROM scores s WHERE s.post_id = p.id AND ${PASSED1_SQL("$2", "$3", "$4")})
         OR p.id IN (SELECT post_id FROM pend)) AS stage1
      FROM posts p WHERE ${which} AND p.thread IS NULL
      ORDER BY coalesce(p.published, p.seen) DESC NULLS LAST, p.id DESC LIMIT ${LIST_MAX + 1}`,
    [stream ? { id: stream.id, above: stream.above } : sids, T, screen, c.model] as never[]);
  const truncated = rows.length > LIST_MAX;
  const got = rows.slice(0, LIST_MAX);
  const entries = got.map(r => ({ id: r.id as number, sid: r.sid as string, member: !!r.member, stage1: !!r.stage1 }));
  // a stream view's sources: those its posts come from, the source with the most first
  if (stream) {
    const n = new Map<string, number>();
    for (const e of entries) n.set(e.sid, (n.get(e.sid) || 0) + 1);
    sids.push(...[...n.keys()].sort((a, b) => n.get(b)! - n.get(a)!));
  }
  const order = new Map(sids.map((s, i) => [s, i]));
  if (catalogue) entries.sort((a, b) => (order.get(a.sid)! - order.get(b.sid)!));  // stable: newest first within a source
  const byCat = new Map(cat.map(s => [s.id, s]));
  // a source not in the catalogue is named after its posts' source; a sid that holds several (facebook: every page
  // watched) is named after its newest post's and how many more there are
  const seen = new Map<string, Set<string>>();
  for (const r of got) if (r.source) { const k = seen.get(r.sid) || new Set<string>(); k.add(r.source as string); seen.set(r.sid, k); }
  const names = new Map([...seen].map(([sid, k]) => { const [first] = k; return [sid, k.size > 1 ? `${first} and ${k.size - 1} more` : first]; }));
  const sources: ProofSource[] = sids.map(sid => {
    const k = byCat.get(sid);
    const mine = entries.filter(e => e.sid === sid);
    return { sid, name: k?.name ?? names.get(sid) ?? null, url: k?.url ?? null, kind: k?.kind ?? null, catalogue: !!k,
      total: mine.length, stream: mine.filter(e => e.member).length, stage1: mine.filter(e => e.stage1).length };
  });
  return { sids, catalogue, stream, sources, entries, truncated, screen,
    counts: { all: entries.length, stream: entries.filter(e => e.member).length, stage1: entries.filter(e => e.stage1).length } };
}

/** the flags table is made by proof.py on the first flag */
async function hasFlags(sql: Sql) {
  const [t] = await sql`SELECT to_regclass('public.proof_flags') IS NOT NULL AS ok`;
  return !!t.ok;
}
/** a TIMESTAMPTZ column as HKT ISO seconds (proof.py COLS) */
const HKT = (sql: Sql, col: string) => sql.unsafe(`to_char(${col} AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00'`);

function jsonArr(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String);
  try { const x = JSON.parse(String(v ?? "[]")); return Array.isArray(x) ? x.map(String) : []; } catch { return []; }
}

const LINK = /https?:\/\/[^\s<>"'）)\]】」』，。、]+/g;

/** one entry: the post as the pipeline holds it (the stored row, its text and how it was fetched, the comments, every
 *  stream's score) and this post's flags */
export async function entry(sql: Sql, id: number) {
  const c = await config(sql);
  const [v, t, a] = await Promise.all([view(sql, id), text(sql, id), aiScores(sql, id, true)]);
  // every stream's score stored for this post's latest scoring (streams.py, 21 Oct 2026)? to_jsonb: the column may not
  // exist yet in a database the new poller hasn't started on
  const [all] = await sql`SELECT (to_jsonb(s) ->> 'all_scores')::boolean AS v, to_jsonb(s) -> 'answers' AS answers,
      s.tag_model, s.sentiment, s.labels FROM scored s WHERE s.post_id = ${id}`;
  const [ttl] = await sql`SELECT ai_title, ai_title_kind, ai_title_model, ai_title_at FROM posts WHERE id = ${id}`;
  const [raw] = await sql`SELECT p.title, p.published, p.seen, p.day, p.cached_at, p.text_via, p.text_via_url, p.canonical_url,
      p.first_run, p.date_only, p.article_id, p.section, p.url_key, length(p.text) AS text_len,
      (SELECT count(*) FROM posts ch WHERE ch.thread = p.url) AS comments FROM posts p WHERE p.id = ${id}`;
  const k = (c.catalogue || []).find(s => s.id === v.sid);
  const links = [...new Set(((t.text as string) || "").match(LINK) || [])].slice(0, 200);
  // waiting for stage 2 (PENDING_SQL): stage 1's scores and reasons from its event, the pairs at or over the screen
  const screen = screenOf(c), th = thresholds(c);
  const [pe] = await sql`SELECT e.at, e.data FROM events e WHERE e.kind = 'stage1' AND e.post_id = ${id}
      AND NOT EXISTS (SELECT 1 FROM events f WHERE f.kind = 'stage2' AND f.post_id = ${id} AND f.id > e.id AND NOT (f.data ? 'failed'))
      AND e.id = (SELECT max(id) FROM events WHERE kind = 'stage1' AND post_id = ${id})`;
  const pending = !pe ? [] : Object.entries((pe.data?.scores ?? {}) as Record<string, unknown>)
    .filter(([k, v]) => k in th && typeof v === "number" && v >= screen)
    .map(([k, v]) => ({ stream: k, name: c.streams.find(s => s.id === k)?.name ?? k, s1: v as number,
      r1: ((pe.data?.reasons ?? {}) as Record<string, string>)[k] ?? null, threshold: th[k], at: pe.at as string }))
    .sort((a, b) => b.s1 - a.s1);
  const flags: Row[] = !(await hasFlags(sql)) ? [] : await sql`SELECT id, note, email, ${HKT(sql, "at")} AS at,
      ${HKT(sql, "resolved_at")} AS resolved_at, resolved_note FROM proof_flags WHERE post_id = ${id} ORDER BY id DESC`;
  return {
    id: v.id, sid: v.sid, source: v.source, website: v.website, board: v.board, section: raw.section ?? null,
    feed: k ? { name: k.name, url: k.url, kind: k.kind } : null,
    title: v.title, stored_title: raw.title, title_by_ai: v.title_by_ai, author: v.author, platform: v.platform,
    source_type: v.source_type, region: v.region,
    published: raw.published ?? null, date_only: !!raw.date_only, seen: raw.seen, day: raw.day,
    url: v.url, link: v.link, canonical_url: raw.canonical_url ?? null,
    text: (t.text as string) || "", text_len: Number(raw.text_len || 0), cached_at: raw.cached_at ?? null,
    text_via: raw.text_via ?? null, text_via_label: v.text_via_label, text_via_url: raw.text_via_url ?? null, text_due: v.text_due,
    links, comments: Number(raw.comments || 0), first_run: !!raw.first_run, article_id: raw.article_id ?? null,
    streams: v.streams, scoring: v.scoring, not_scored: a.not_scored, scores: a.streams, floor: a.floor, model: a.model,
    all_scores: !!(all && all.v), all_scores_from: c.all_scores_from ?? null,
    // what the models answered (scored.answers, since 21 Oct 2026: each stage's answer as given) and what was kept
    // from it: the post's sentiment and labels (tag_model's), its AI title, the market
    answers: (all?.answers ?? null) as Record<string, { model: string; answer: Record<string, unknown> | null }> | null,
    kept_tags: all ? { sentiment: (all.sentiment as string | null) ?? null, labels: jsonArr(all.labels), tag_model: (all.tag_model as string | null) ?? null } : null,
    ai_title: ttl && ttl.ai_title ? { title: ttl.ai_title as string, kind: (ttl.ai_title_kind as string | null) ?? null,
      model: (ttl.ai_title_model as string | null) ?? null, at: (ttl.ai_title_at as string | null) ?? null } : null,
    flags, screen, pending,
  };
}

/** the monitor's list: open flags (newest first) with each post's title and source */
export async function openFlags(sql: Sql) {
  if (!(await hasFlags(sql))) return { flags: [] };
  const rows = await sql`SELECT f.id, f.post_id, f.url, f.sid, f.note, f.email, ${HKT(sql, "f.at")} AS at, p.title, p.source FROM proof_flags f LEFT JOIN posts p ON p.id = f.post_id
      WHERE f.resolved_at IS NULL ORDER BY f.id DESC LIMIT 500`;
  return { flags: rows.map(r => ({ ...r })) };
}

