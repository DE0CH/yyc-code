// The tools: one registry (name, description written for a model, JSON Schema, handler) behind the REST API
// (/api/v1/<tool>, its OpenAPI from the same schemas). Every handler only reads,
// through the edge site's own data modules (web-app/src/server/): the same SQL and the same answers as the site, and
// the wisers_* tools (wisers.ts) through YYC's WiseSearch relay. Nothing here writes and nothing calls a paid model;
// the wisers_* tools marked as costing quota spend WisersOne quota on YYC's account and need a `reason`.
import type { Row, Sql } from "../../web-app/src/server/db";
import { config, flagsFor } from "../../web-app/src/server/config";
import { corpusStats } from "../../web-app/src/server/site";
import { hktToday, dayAdd } from "../../web-app/src/server/time";
import { feed } from "../../web-app/src/server/feed";
import { streamScores } from "../../web-app/src/server/scores";
import * as item from "../../web-app/src/server/item";
import * as dash from "../../web-app/src/server/dashboard";
import * as reports from "../../web-app/src/server/reports";
import * as search from "../../web-app/src/server/search/search";
import { rankSql, terms as qterms, variants } from "../../web-app/src/server/search/query";
import { splitSql, wordSql } from "../../web-app/src/server/search/split";
import { BIAS_NOTE, BIAS_NOTE_EN } from "../../web-app/src/lib/bias";
import * as dl from "../../web-app/src/lib/dashboard_url";
import { WISERS_TOOLS, WisersError, type Ctx } from "./wisers";
import { timed } from "./timing";

export const SITE = "https://newswatch.yyc-internal.com";
const MAX_CHARS = 100_000;  // one answer, well under the clients' limits (Claude ~150k characters, Claude Code 25k tokens warns)

export class ToolError extends Error {}

type Schema = Record<string, unknown> & { type?: string; properties?: Record<string, Schema>; required?: string[] };
export interface Tool {
  name: string; title: string; description: string; schema: Schema;
  run: (sql: Sql, a: Record<string, any>, ctx: Ctx) => Promise<unknown>;  // eslint-disable-line @typescript-eslint/no-explicit-any
}

const DATE: Schema = { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$", description: "YYYY-MM-DD, Hong Kong time" };
const STREAM: Schema = { type: "string", description: "a stream id from streams_list, e.g. CEHK" };
const PAGE: Schema = { type: "integer", minimum: 1, default: 1 };
const list = (d: string): Schema => ({ type: "array", items: { type: "string" }, description: d });

const itemLink = (id: number | string) => `${SITE}/item/${id}`;
/** what every answer tells the model about dashboard_url (tool descriptions) */
const CITE = " Results carry dashboard_url: the item's details page on the monitoring site; link it when you cite or mention the item.";
const CITE_BETA = " dashboard_url is the file's page on the monitoring site's beta (where 報告 is on; sign-in needed); link it when you cite the report.";
/** the 數據分析 params of an analysis_* call (its stream, period and filters), for links that must keep them */
const dashOf = (a: Record<string, any>, from?: string | null, to?: string | null): dl.DashParams => ({ p: a.stream,  // eslint-disable-line @typescript-eslint/no-explicit-any
  from: from ?? a.from, to: to ?? a.to, sent: a.sentiment, label: a.label, type: a.source_type, site: a.source, author: a.author, q: a.query });
const qOf = (o: Record<string, unknown>): dash.Q =>
  Object.fromEntries(Object.entries(o).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]));

async function knownStream(sql: Sql, id: string) {
  const c = await config(sql);
  if (!c.streams.some(s => s.id === id) && !c.profiles.some(p => p.id === id)) {
    throw new ToolError(`no stream ${JSON.stringify(id)}; streams: ${c.streams.map(s => s.id).join(", ")}`);
  }
  return c;
}

/** a dashboard/search error the site would show → a message the caller can act on */
async function guard<T>(f: () => Promise<T>): Promise<T> {
  try { return await f(); }
  catch (e) {
    if ((e as { code?: string }).code === "42P01") throw new ToolError("this part of the data is not in the database this server reads yet");
    if (e instanceof dash.ApiError || e instanceof search.SearchError || e instanceof RangeError) throw new ToolError(e.message);
    if (e instanceof item.NotFound) throw new ToolError("no such post; take ids from posts_search");
    throw e;
  }
}

/** an answer's object without its empty fields */
const clean = (o: Row): Row => Object.fromEntries(Object.entries(o).filter(([, v]) =>
  v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0)));

/** what a query says about a post, after the post's own fields */
const POST_CONTEXT = ["score", "reason", "match", "sentiment", "labels", "theme", "stream", "category", "comment"] as const;

/**
 * A post (an article, a social post, a forum thread or reply): the same shape in every answer, whatever the query
 * (Deyao, 2026-10-27: one abstraction for a post, every list of posts in this format, each with its details page).
 * Takes any row the site's modules give (search, 數據分析, 串流, the item page) and `ctx`, what the query adds.
 */
export function post(r: Row, ctx: Row = {}): Row {
  const id = r.id ?? r.post_id;
  const text = r.extract ?? r.text;
  const o: Row = {
    id, title: r.title, url: r.url,
    dashboard_url: id != null ? dl.postUrl(id as number) : r.url ? dl.postByUrl(String(r.url), (r.stream as string) ?? null) : undefined,
    item: id != null ? itemLink(id as number) : undefined,
    source: r.website ?? r.source, board: r.board, author: r.author, type: r.source_type ?? r.type, region: r.region,
    platform: r.platform, time: r.time ?? r.published ?? r.seen, date_only: r.date_only || undefined,
    extract: text ? Array.from(String(text)).slice(0, 300).join("") : undefined, engagement: r.engagement,
  };
  for (const k of POST_CONTEXT) { const v = ctx[k] ?? r[k]; if (v != null && v !== "" && v !== false) o[k] = v; }
  return clean(o);
}

// ---- posts_search: the 搜尋 section's search (search.py on the Worker) plus stream, score and platform filters ----

async function postsSearch(sql: Sql, a: Record<string, any>) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const query = String(a.query || "").trim();
  if (!query && !a.stream) throw new ToolError("give a query, a stream, or both");
  if (a.stream) await knownStream(sql, a.stream);
  const size = Math.min(50, a.page_size || 10);
  const qs: search.QS = { q: [query], page: [String(a.page || 1)], n: [String(size)], sort: [a.sort || "date"] };
  if (a.from) qs.from = [a.from];
  if (a.to) qs.to = [a.to];
  if (a.title_only) qs.title = ["1"];
  if (a.include_comments) qs.comments = ["1"];
  if (a.source_types?.length) qs.type = [a.source_types.join(",")];
  if (a.sources?.length) qs.site = [a.sources.join(",")];
  if (a.regions?.length) qs.region = [a.regions.join(",")];
  return guard(async () => {
    let prep;
    try { prep = await timed("prepare", () => search.prepared(sql, qs)); }
    catch (e) {
      if (/not published yet/.test(String((e as Error).message)) && query) return substringSearch(sql, a, query, size);
      throw e;
    }
    const { st, s } = prep;
    // the filters besides the words: period, comments, types, outlets, regions (search.where), platforms, stream
    let x = "", xargs: search.P[] = [];
    if (a.platforms?.length) { x += " AND p.platform = ANY(%s::text[])"; xargs = [...xargs, search.textArr(a.platforms)]; }
    let threshold: number | null = null;
    if (a.stream) {
      const c = await config(sql);
      threshold = a.min_score ?? c.thresholds[a.stream] ?? 50;
      x += " AND EXISTS (SELECT 1 FROM scores sc WHERE sc.post_id = p.id AND sc.stream = %s AND sc.score >= %s)";
      xargs = [...xargs, a.stream, threshold!];
    }
    const terms = query ? variants(st, qterms(s.parsed.node)) : [];
    // a query of words alone (split.ts): the word matches first; when they fill the page, the substring-only matches
    // (b: reads the text of every grams candidate, seconds on a common term) are skipped and total counts word matches
    const words = query ? wordSql(st, s, x, xargs) : null;
    const lim = [s.n, (s.page - 1) * s.n];
    let rows: Row[] = [], total = 0, exact = true;
    const ws = words ? await timed("sql_words", () => search.rows(sql, words[0], [...words[1], ...lim])) : null;
    if (ws && Number(ws[0]?.n ?? 0) >= s.page * s.n) {
      total = Number(ws[0].n);
      exact = false;
      rows = ws.filter(r => r.id != null);
    } else if (ws) {
      const split = splitSql(st, s, x, xargs, "total")!;
      const rs = await timed("sql_split", () => search.rows(sql, split[0], [...split[1], ...lim]));
      total = Number(rs[0]?.total ?? 0);
      rows = rs.filter(r => r.id != null);
    } else {
      let [w, args] = query ? search.where(st, s) : ["p.day BETWEEN %s AND %s" + (s.comments ? "" : " AND p.thread IS NULL"), [s.start, s.end] as search.P[]];
      if (!query) {
        for (const [key, col] of [["types", "source_type"], ["sites", "website"], ["regions", "region"]] as const) {
          if (s[key].length) { w += ` AND p.${col} = ANY(%s::text[])`; args = [...args, search.textArr(s[key])]; }
        }
      }
      w += x; args = [...args, ...xargs];
      const [ord, oargs] = query ? search.order(st, s) : ["coalesce(p.published, p.seen) DESC, p.id DESC", []];
      const [rank, rargs] = query ? rankSql(st, s.parsed) : ["0", []];
      let cnt: Row;
      [rows, [cnt]] = await Promise.all([
        search.rows(sql, `SELECT ${search.COLS}, ${rank} AS word FROM posts p WHERE ${w} ORDER BY ${ord} LIMIT %s OFFSET %s`,
          [...rargs, ...args, ...oargs, s.n, (s.page - 1) * s.n]),
        search.rows(sql, `SELECT count(*) AS n FROM posts p WHERE ${w}`, args),
      ]);
      total = Number(cnt.n);
    }
    const ids = rows.map(r => Number(r.id));
    const scores = new Map<number, { score: number; reason: string | null }>();
    if (a.stream && ids.length) {
      for (const r of await sql`SELECT post_id, score, reason FROM scores WHERE stream = ${a.stream} AND post_id = ANY(${"{" + ids.join(",") + "}"}::bigint[])`) {
        scores.set(Number(r.post_id), { score: Number(r.score), reason: (r.reason as string) ?? null });
      }
    }
    const out: Row = { total, ...(exact ? {} : { total_exact: false }), page: s.page, page_size: s.n, from: s.start, to: s.end,
      sort: s.sort, ...(a.stream ? { stream: a.stream, min_score: threshold } : {}), terms,
      posts: rows.map(r => post(search.view(st, r, terms), scores.get(Number(r.id)))) };
    if (!exact) out.more = (total > s.page * s.n ? `at least ${total - s.page * s.n} more word matches` : "no more word matches")
      + `; posts containing the term only inside longer words are not counted: call again with page=${s.page + 1}`;
    else if (s.page * s.n < total) out.more = `${total - s.page * s.n} more: call again with page=${s.page + 1}`;
    return out;
  });
}

/** before the database carries the search dictionary (kv search:edge): each whitespace-separated word as a substring of
 *  the title or text, any word matching, newest first. Says so in the answer. */
async function substringSearch(sql: Sql, a: Record<string, any>, query: string, size: number) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const words = query.replace(/["()]/g, " ").split(/\s+/).filter(w => w && !/^(AND|OR|NOT)$/.test(w)).slice(0, 10);
  const end = a.to || hktToday(), start = a.from || dayAdd(end, -29), page = a.page || 1;
  const like = words.map(w => "%" + w.replace(/[\\%_]/g, m => "\\" + m) + "%");
  const cond = sql`p.day BETWEEN ${start} AND ${end} AND (${a.include_comments ? sql`TRUE` : sql`p.thread IS NULL`})
    AND (p.title ILIKE ANY(ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(like)}::text::jsonb)))
      OR p.text ILIKE ANY(ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(like)}::text::jsonb))))
    ${a.stream ? sql`AND EXISTS (SELECT 1 FROM scores sc WHERE sc.post_id = p.id AND sc.stream = ${a.stream} AND sc.score >= ${a.min_score ?? 50})` : sql``}`;
  const [rows, [cnt]] = await Promise.all([
    sql`SELECT p.id, p.url, p.title, left(p.text, 400) AS text, p.website, p.board, p.author, p.source_type, p.region, p.platform,
        coalesce(p.published, p.seen) AS time, p.thread FROM posts p WHERE ${cond}
        ORDER BY coalesce(p.published, p.seen) DESC, p.id DESC LIMIT ${size} OFFSET ${(page - 1) * size}`,
    sql`SELECT count(*) AS n FROM posts p WHERE ${cond}`]);
  const total = Number(cnt.n);
  return { total, page, page_size: size, from: start, to: end, search_mode: "substring (the search dictionary is not published in this database yet)",
    posts: rows.map(r => post({ ...r, extract: Array.from(String(r.text || "").replace(/\s+/g, " ")).slice(0, 160).join(""), comment: !!r.thread })),
    ...(page * size < total ? { more: `call again with page=${page + 1}` } : {}) };
}

// ---- post_get: the item page's data with the full text (as signed-in staff see it on the site) ----

async function postGet(sql: Sql, a: Record<string, any>) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const pid = await item.find(sql, { id: a.id ?? null, url: a.url ?? null });
  if (pid == null) throw new ToolError(a.id != null ? `no post ${a.id}; take ids from posts_search` : "no post with that url");
  return guard(async () => {
    const [v, t, sc] = await Promise.all([item.view(sql, pid), item.text(sql, pid), item.aiScores(sql, pid)]);
    const max = Math.min(200_000, a.max_chars || 20_000), off = a.offset || 0;
    const full = Array.from(t.text || "");
    const text = full.slice(off, off + max).join("");
    const comments = (v.thread?.comments || []).map((c: Row) => ({ id: c.id, author: c.author, time: c.time,
      text: Array.from(String((t.comments as Record<string, string>)[c.id] ?? c.text ?? "")).slice(0, 2000).join(""),
      likes: c.likes ?? undefined })).slice(0, a.comments_limit ?? 100);
    const c = await config(sql);
    const { extract: _x, ...head } = post(v);  // eslint-disable-line @typescript-eslint/no-unused-vars
    const o: Row = {
      ...head, published: v.published, seen: v.seen, comment_of: v.thread_url || undefined,
      title_by_ai: v.title_by_ai || undefined, text_via: v.text_via_label || v.text_via || undefined,
      text, text_chars: full.length, ...(off + max < full.length ? { text_more: `call again with offset=${off + max}` } : {}),
      comments_total: v.thread?.total ?? 0, comments,
      transcripts: Object.keys(t.media || {}).length ? Object.values(t.media) : undefined,
      picture_text: Object.values(t.pictures || {}).filter(Boolean).length ? Object.values(t.pictures).filter(Boolean) : undefined,
      // the stream's 數據分析 (a profile's tags), the 串流 day it was filed on, the stream's 串流: each its own page
      tags: (v.tags as Row[] | undefined)?.map(t => c.shown.includes(String(t.profile)) ? { ...t, dashboard_url: dl.dashboardUrl({ p: String(t.profile) }) } : t),
      filed_in: (v.streams as Row[] | undefined)?.map(f => ({ ...f, dashboard_url: dl.streamUrl(String(f.stream), (f.day as string) ?? null) })),
      scores: sc.streams.map((s: Row) => clean({ stream: s.stream, name: s.name, score: s.score, reason: s.reason,
        threshold: s.threshold, passed: s.passed, category: s.category ?? undefined,
        dashboard_url: c.shown.includes(String(s.stream)) ? dl.streamUrl(String(s.stream)) : undefined })),
      not_scored: sc.not_scored || undefined,
      same_article: v.article?.posts?.map((p: Row) => post(p)),
    };
    return Object.fromEntries(Object.entries(o).filter(([, x]) => x !== undefined && x !== null));
  });
}

// ---- streams ----

async function streamsList(sql: Sql) {
  const c = await config(sql);
  const stats = await corpusStats(sql);
  return {
    today: hktToday(),
    streams: c.streams.map(s => ({ id: s.id, name: s.name, title: s.title ?? s.name, shown: c.shown.includes(s.id),
      ...(c.shown.includes(s.id) ? { dashboard_url: dl.streamUrl(s.id), analysis_url: dl.dashboardUrl({ p: s.id }) } : {}),
      scored: c.scored.includes(s.id), threshold: c.thresholds[s.id] ?? null, parent: s.parent, categories: s.categories,
      brief: c.profiles.find(p => p.id === s.id)?.brief })),
    profiles_without_stream: c.profiles.filter(p => !c.streams.some(s => s.id === p.id)).map(p => ({ id: p.id, title: p.title, brief: p.brief })),
    how_streams_work: "An AI scores every collected post 0-100 against each stream's brief; a post at or above the stream's threshold belongs to it (filed items are in stream_items).",
    sentiments: c.sentiments, labels: c.label_info, source_types: c.source_types, regions: c.regions,
    corpus: stats,
  };
}

async function streamItems(sql: Sql, a: Record<string, any>) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (a.stream) await knownStream(sql, a.stream);
  return guard(async () => {
    const f = await feed(sql, a.day || null);
    const sc = await streamScores(sql, f.date);
    const items = (f.items as Row[]).filter(it => !a.stream || it.stream === a.stream).map(it => {
      const s = sc[`${it.stream}|${it.url}`];
      return post(it, { score: s?.score, reason: s?.reason });
    });
    return { day: f.date, today: f.today, stream: a.stream ?? null, dashboard_url: dl.streamUrl(a.stream, f.date), total: items.length, items,
      counts: f.counts, days: f.days.slice(-90), last_poll: f.last_poll };
  });
}

// ---- 數據分析 ----

const ANALYSIS_ARGS: Record<string, Schema> = {
  stream: STREAM, from: DATE, to: DATE,
  query: { type: "string", description: "keywords within the stream's posts (same syntax as posts_search)" },
  sentiment: { type: "string", enum: ["positive", "neutral", "negative", "mixed", "irony"] },
  label: { type: "string", description: "a label id (streams_list labels)" },
  source_type: { type: "string", description: "e.g. News, Forum, Social Media (comma-separated for several)" },
  source: { type: "string", description: "an outlet's name as results show it, e.g. 明報" },
  author: { type: "string" },
};
/** the period an analysis_* call covers (the dashboard's own default when from/to are left out) */
const per = (a: Record<string, any>, days = 7) => dash.period(aq(a), days);  // eslint-disable-line @typescript-eslint/no-explicit-any
/** 數據分析's posts of one trend period: a day, or a week (its first day to the day before the next period) */
function periodUrl(a: Record<string, any>, period: string, all: Row[]): string | null {  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (!/^\d{4}-\d{2}-\d{2}$/.test(period)) return null;
  const i = all.findIndex(x => x.period === period);
  const next = all[i + 1]?.period as string | undefined;
  let to = period;
  if (next && /^\d{4}-\d{2}-\d{2}$/.test(next) && next > period) {
    const d = new Date(next + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() - 1); to = d.toISOString().slice(0, 10);
  } else if (!next && a.bucket === "week") {
    const d = new Date(period + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + 6); to = d.toISOString().slice(0, 10);
  }
  return dl.dashboardUrl({ p: a.stream, from: period, to, v: "posts" });
}

/** a list's rows with their own details page link */
const linked = <T extends Row>(rows: T[] | undefined, f: (r: T) => string | null) => (rows || []).map(r => { const u = f(r); return u ? { ...r, dashboard_url: u } : r; });
const aq = (a: Record<string, any>) => qOf({ p: a.stream, from: a.from, to: a.to, q: a.query, sentiment: a.sentiment,  // eslint-disable-line @typescript-eslint/no-explicit-any
  label: a.label, type: a.source_type, site: a.source, author: a.author });

async function analysisPosts(sql: Sql, a: Record<string, any>) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  await knownStream(sql, a.stream);
  return guard(async () => {
    const { c, start, end, ps } = await dash.selected(sql, aq(a));
    const size = Math.min(100, a.page_size || 20), page = a.page || 1;
    const slice = ps.slice((page - 1) * size, page * size);
    await dash.fillText(sql, slice);
    await dash.markThin(sql, c, slice);
    const count = (key: (x: Row) => string | null) => {
      const m = new Map<string, number>();
      for (const x of ps) { const k = key(x); if (k) m.set(k, (m.get(k) || 0) + 1); }
      return Object.fromEntries([...m.entries()].sort((x, y) => y[1] - x[1]).slice(0, 30));
    };
    return { stream: a.stream, from: start, to: end, dashboard_url: dl.dashboardUrl({ ...dashOf(a, start, end), v: "posts", page: page > 1 ? page : null }),
      total: ps.length, page, page_size: size,
      sentiment_index: dash.sentimentIndex(ps),
      facets: { sentiment: dash.sentimentCounts(ps),
        labels: Object.fromEntries(c.label_order.map(l => [l, ps.filter(x => (x.labels as string[]).includes(l)).length]).filter(([, n]) => (n as number) > 0)),
        themes: count(x => x.theme), sources: count(x => x.website), types: count(x => x.source_type),
        people: count(x => x.author) },
      posts: slice.map(x => post(dash.postView(x))),
      ...(page * size < ps.length ? { more: `call again with page=${page + 1}` } : {}) };
  });
}

// ---- daily_summary ----

/** the scheduled AI summary, or why there is none: what the daily report of each day looked up says (reports.py makes
 *  the summary while it builds the day's report, the morning after; a day with no posts in the stream gets no report
 *  and no summary), and the newest summary there is */
async function dailySummary(sql: Sql, a: Record<string, any>) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const hit = await dash.aiDailyApi(sql, qOf({ p: a.stream, day: a.day })) as Row;
  const c = await config(sql);
  const aiOn = !!flagsFor(c, SITE.replace(/^https:\/\//, "")).ai;
  const shown = aiOn ? "the site shows it (數據分析 摘要)" : "the site does not show it while its ai flag is off; the daily report (報告) carries it";
  if (Object.keys(hit).length) return { stream: a.stream, generated: true, ...hit, shown_on_site: shown };
  const day = a.day || hktToday();
  const looked = [];
  for (const d of [day, dayAdd(day, -1)]) {
    const f = await reports.stored(sql, d, a.stream, "summary.json");
    let why: string;
    if (!f) why = d >= dayAdd(hktToday(), -1) ? `no daily report for ${d} yet: it is made the morning after (HKT)` : `no daily report was made for ${d}`;
    else {
      let j: Row = {};
      try { j = JSON.parse(new TextDecoder().decode(f.body)); } catch { /* not JSON: said below */ }
      why = Number(j.posts) === 0 ? `the daily report for ${d} found no posts in the stream, so it made no summary (${j.note ?? ""})`.replace(" ()", "")
        : `the daily report for ${d} has no AI summary`;
    }
    looked.push({ day: d, why, ...(f ? { report: dl.reportUrl(`/reports/${d}/${a.stream}/summary.json`) } : {}) });
  }
  const [last] = await sql`SELECT k, v->>'heading' AS heading, v->>'at' AS at FROM kv
    WHERE k >= ${`daily:${a.stream}:`} AND k < ${`daily:${a.stream};`} ORDER BY k DESC LIMIT 1`;
  return { stream: a.stream, day, generated: false, summary: null, reason: looked,
    latest: last ? { day: String(last.k).split(":").pop(), heading: last.heading, made_at: last.at, call: "daily_summary with this day" } : null,
    shown_on_site: shown };
}

// ---- reports ----

const TEXT_TYPES = /^(text\/|application\/(json|javascript|xml))/;

async function reportFile(sql: Sql, a: Record<string, any>) {  // eslint-disable-line @typescript-eslint/no-explicit-any
  const path = String(a.path || "").replace(/^https?:\/\/[^/]+/, "");
  const m = reports.FILE_URL.exec(path);
  if (!m) throw new ToolError("path is /reports/<day>/<stream>/<file>, as reports_list gives it");
  const f = await reports.stored(sql, m[1], m[2], m[3]);
  if (!f) throw new ToolError("no such report file");
  const out: Row = { path, url: SITE + path, dashboard_url: dl.reportUrl(path), content_type: f.ctype, bytes: f.body.length };
  if (TEXT_TYPES.test(f.ctype)) {
    let t = new TextDecoder().decode(f.body);
    if (f.ctype.startsWith("text/html")) {
      t = t.replace(/<(script|style)[\s\S]*?<\/\1>/gi, "").replace(/<br\s*\/?>|<\/(p|div|h\d|li|tr)>/gi, "\n")
        .replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\n\s*\n+/g, "\n\n").trim();
    }
    out.text = Array.from(t).slice(0, 80_000).join("");
  } else out.note = "binary file: download it from url (public)";
  return out;
}

// ---- the registry ----

export const TOOLS: Tool[] = [
  { name: "streams_list", title: "Streams and what they cover",
    description: "The monitoring streams (the subjects tracked, e.g. CEHK = Hong Kong's Chief Executive): id, name, the brief an AI scores every post against, the score threshold, plus the label and sentiment vocabulary and corpus size. Call this first to map a person, company or topic to a stream id." + CITE,
    schema: { type: "object", properties: {} }, run: s => streamsList(s) },
  { name: "posts_search", title: "Search posts",
    description: "Full-text search over every collected post (news, forums, social media, video transcripts and the full text of articles; Traditional/Simplified Chinese and English match each other). Filters: stream (only posts the AI scored at or above min_score, default the stream's threshold, for that stream), date range (default the last 30 days), source types, outlets, regions, platforms. Query syntax: words or \"phrases\"; whitespace = OR; AND, NOT, ( ). Without a query, a stream's posts newest first. Results list whole-word matches before posts that contain a term only inside a longer word; when the word matches alone fill the page, total counts only them (a lower bound, total_exact: false). Returns a short extract per post; read the full post with post_get." + CITE,
    schema: { type: "object", properties: {
      query: { type: "string", description: "keywords; optional when a stream is given" },
      stream: STREAM, min_score: { type: "integer", minimum: 0, maximum: 100, description: "with stream: lowest score (default the stream's threshold)" },
      from: DATE, to: DATE,
      source_types: list("e.g. News, Newspaper, Magazine, Forum, Social Media, Video, Blog, Official, Broadcast"),
      sources: list("outlet names as results show them (sources_list), e.g. 明報, LIHKG討論區"),
      regions: list("HK, Macau, Mainland, Taiwan, Global"), platforms: list("e.g. facebook, instagram, youtube, threads"),
      title_only: { type: "boolean", description: "match headlines only" },
      include_comments: { type: "boolean", description: "also comments and forum replies (default false)" },
      sort: { type: "string", enum: ["date", "oldest", "relevance"], default: "date" },
      page: PAGE, page_size: { type: "integer", minimum: 1, maximum: 50, default: 10 } } },
    run: postsSearch },
  { name: "post_get", title: "Read one post",
    description: "One post in full: its whole stored text (article full text, video transcript, text read from its pictures), source, author, time, engagement, the collected comments/replies (full text), the AI's 0-100 score and one-line reason for every stream, the streams that filed it, and other posts of the same article. Long texts come in pieces (offset)." + CITE,
    schema: { type: "object", properties: {
      id: { type: "integer", description: "the post id (posts_search; the number in /item/<id>)" },
      url: { type: "string", description: "or the post's original url" },
      max_chars: { type: "integer", minimum: 1000, maximum: 200000, default: 20000 },
      offset: { type: "integer", minimum: 0, default: 0, description: "where in the text to start (for the next piece)" },
      comments_limit: { type: "integer", minimum: 0, maximum: 500, default: 100 } } },
    run: postGet },
  { name: "stream_items", title: "A stream's filed items by day",
    description: "What a stream filed on one day (串流): each item's headline, source, link, time, category, the AI's score and reason; plus the days that have items and when the sources were last polled. Omit stream for every shown stream; day defaults to today (HKT)." + CITE,
    schema: { type: "object", properties: { stream: STREAM, day: DATE } }, run: streamItems },
  { name: "analysis_posts", title: "Stream analysis: posts and facets",
    description: "數據分析 帖子: a stream's posts in a period (default last 7 days) with facets: sentiment counts and index, labels, themes, sources, source types and people (authors, most active first); optionally narrowed by keywords, sentiment, label, source type, outlet or author. Paged." + CITE,
    schema: { type: "object", properties: { ...ANALYSIS_ARGS, page: PAGE, page_size: { type: "integer", minimum: 1, maximum: 100, default: 20 } }, required: ["stream"] },
    run: analysisPosts },
  { name: "analysis_hot_topics", title: "Stream analysis: hot topics",
    description: "數據分析 熱門話題: a stream's hot topics (stories/threads) in a period, biggest first: post count, participants, outlets, sentiment index and level, views, first and last day. Use analysis_topic for one topic's posts." + CITE,
    schema: { type: "object", properties: { ...ANALYSIS_ARGS, top: { type: "integer", minimum: 1, maximum: 50, default: 10 } }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = per(a);
      const r = await dash.hotTopicsApi(s, { ...aq(a), top: String(a.top || 10) });
      return { dashboard_url: dl.dashboardUrl({ ...dashOf(a, from, to), v: "hot" }), ...r,
        topics: linked(r.topics as Row[], t => dl.topicUrl(t.id as number, dashOf(a, from, to))) };
    })) },
  { name: "analysis_topic", title: "Stream analysis: one topic",
    description: "One hot topic of a stream (id from analysis_hot_topics, same period and filters): its posts, stats and day-by-day trend." + CITE,
    schema: { type: "object", properties: { ...ANALYSIS_ARGS, id: { type: "integer" } }, required: ["stream", "id"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = per(a);
      const r = await dash.topicApi(s, { ...aq(a), id: String(a.id) });
      return { dashboard_url: dl.topicUrl(a.id, dashOf(a, from, to)), ...r, posts: ((r.posts as Row[]) || []).map(x => post(x)),
        trend_links: "no page lists one topic's posts on one day (a topic's id holds only for its own period), so trend[] days have no link; the topic's page (dashboard_url) draws this trend",
        top_authors: linked(r.top_authors as Row[], x => x.author ? dl.personUrl(String(x.author), { p: a.stream, from, to }) : null),
        top_sources: linked(r.top_sources as Row[], x => x.website ? dl.sourceUrl(String(x.website), dashOf(a, from, to)) : null) };
    })) },
  { name: "analysis_person", title: "Stream analysis: a person",
    description: "數據分析 人物: one author's history (every post by them in the year before the period, default last 90 days): how many matched the stream, sentiment on it, their boards and outlets, first/last seen and recent posts. Find authors in analysis_posts facets.people." + CITE,
    schema: { type: "object", properties: { author: { type: "string" }, stream: STREAM, from: DATE, to: DATE }, required: ["author"] },
    run: (s, a) => guard(async () => {
      const q = qOf({ p: a.stream, author: a.author, from: a.from, to: a.to });
      const [from, to] = dash.period(q, 90);
      const r = await dash.personaApi(s, q);
      // the terms come from every post by the author from a year before the period to its end, in any stream: 搜尋 for the
      // term and the author over those days, comments included (FindIn(author: …) matches the name as a substring)
      const termUrl = (t: string) => /"/.test(a.author) || /"/.test(t) ? null
        : dl.searchUrl({ q: `"${t}" AND FindIn(author: "${a.author}")`, from: dayAdd(from, -365), to, comments: 1 });
      return { dashboard_url: dl.personUrl(a.author, { p: a.stream, from, to }), ...r, recent: ((r.recent as Row[]) || []).map(x => post(x)),
        hot_terms: linked(r.hot_terms as Row[], x => x.term ? termUrl(String(x.term)) : null),
        websites: a.stream ? linked(r.websites as Row[], x => x.website ? dl.sourceUrl(String(x.website), { p: a.stream, from, to, author: a.author }) : null) : r.websites };
    }) },
  { name: "analysis_themes", title: "Stream analysis: themes",
    description: "數據分析 主題: a stream's AI-made themes and how many posts each has in a period, with sentiment index." + CITE,
    schema: { type: "object", properties: { stream: STREAM, from: DATE, to: DATE }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = per(a);
      const r = await dash.themesApi(s, aq(a));
      return { dashboard_url: dl.dashboardUrl({ ...dashOf(a, from, to), v: "themes" }), ...r,
        counts: linked(r.counts as Row[], t => dl.themeUrl(String(t.theme), dashOf(a, from, to))) };
    })) },
  { name: "analysis_overview", title: "Stream analysis: overview and daily scores",
    description: "數據分析 摘要: a stream's numbers for a period (default last 7 days): posts, new posts, topics, websites, boards, authors, views, per-day rates, coverage, sentiment index and balance, split user-generated vs news; the posts per day (or week); and the K-Matrix-style daily scores SPI, CSS, DVS, DPS, CCS on the last day with their 7-day series. " + BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
    schema: { type: "object", properties: { stream: STREAM, from: DATE, to: DATE }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const q = qOf({ p: a.stream, from: a.from, to: a.to });
      const sum = await dash.summaryApi(s, q);
      const sc = await dash.scoresApi(s, qOf({ p: a.stream, day: sum.end, n: 7 }));
      const { series: _series, ...scores } = sc.scores as Row;  // eslint-disable-line @typescript-eslint/no-unused-vars
      return { stream: a.stream, from: sum.start, to: sum.end, dashboard_url: dl.dashboardUrl({ p: a.stream, from: sum.start, to: sum.end }), metrics: sum.metrics, user_generated: sum.cgm, news: sum.news,
        trend: linked(sum.trend as Row[], x => periodUrl(a, String(x.period), sum.trend as Row[])), scores_on_last_day: scores,
        spi_series: linked(sc.spi_series as Row[], x => dl.dashboardUrl({ p: a.stream, from: String(x.day), to: String(x.day) })), method: sum.method, note: BIAS_NOTE_EN, note_zh: BIAS_NOTE };
    })),
  },
  { name: "analysis_authors", title: "Stream analysis: most active authors",
    description: "數據分析 作者: the most active authors of a stream in a period (KOLs, forum users, pages): posts, topics, sentiment index and level, share of the stream's posts, views, most active board. " + BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
    schema: { type: "object", properties: { ...ANALYSIS_ARGS, top: { type: "integer", minimum: 1, maximum: 100, default: 20 } }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = per(a);
      const r = await dash.authorsApi(s, { ...aq(a), top: String(a.top || 20) }) as Row;
      return { dashboard_url: dl.dashboardUrl({ ...dashOf(a, from, to), v: "authors" }), ...r,
        authors: linked(r.authors as Row[], x => x.author ? dl.personUrl(String(x.author), { p: a.stream, from, to }) : null),
        note: BIAS_NOTE_EN, note_zh: BIAS_NOTE };
    })),
  },
  { name: "analysis_sources", title: "Stream analysis: main sources",
    description: "數據分析 來源: the outlets (or boards) a stream's posts in a period come from, most posts first: source type, region, posts, topics, participants, sentiment index and level, share. " + BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
    schema: { type: "object", properties: { ...ANALYSIS_ARGS, by: { type: "string", enum: ["website", "board"], default: "website" },
      top: { type: "integer", minimum: 1, maximum: 100, default: 20 } }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = per(a);
      const r = await dash.sourcesApi(s, { ...aq(a), top: String(a.top || 20), by: a.by || "website" }) as Row;
      return { dashboard_url: dl.dashboardUrl({ ...dashOf(a, from, to), v: "sources", by: a.by === "board" ? "board" : null }), ...r,
        sources: linked(r.sources as Row[], x => x.website ? dl.sourceUrl(String(x.website), dashOf(a, from, to)) : null),
        note: BIAS_NOTE_EN, note_zh: BIAS_NOTE };
    })),
  },
  { name: "analysis_trend", title: "Stream analysis: trend",
    description: "數據分析 趨勢: a stream's posts, topics, authors, views and sentiment index per day or ISO week over a period (default the last 30 days). " + BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
    schema: { type: "object", properties: { stream: STREAM, from: DATE, to: DATE, bucket: { type: "string", enum: ["day", "week"], default: "day" } }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = dash.period(qOf({ from: a.from, to: a.to }), 30);
      const r = await dash.trendApi(s, qOf({ p: a.stream, from: a.from, to: a.to, bucket: a.bucket })) as Row;
      return { dashboard_url: dl.dashboardUrl({ p: a.stream, from, to, v: "trend", bucket: a.bucket === "week" ? "week" : null }),
        ...r, trend: linked(r.trend as Row[], x => periodUrl(a, String(x.period), r.trend as Row[])), note: BIAS_NOTE_EN, note_zh: BIAS_NOTE };
    })),
  },
  { name: "analysis_terms", title: "Stream analysis: hot terms",
    description: "數據分析 熱門詞 (Smart Hot Terms): the words and 2-4 character Chinese terms most frequent in a stream's posts in a period relative to the whole corpus, the stream's own search terms left out: term, count, weight. " + BIAS_NOTE_EN + " Say so whenever you use them." + CITE,
    schema: { type: "object", properties: { ...ANALYSIS_ARGS, top: { type: "integer", minimum: 1, maximum: 100, default: 40 } }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(async () => {
      const [from, to] = per(a);
      const r = await dash.termsApi(s, { ...aq(a), top: String(a.top || 40) }) as Row;
      return { dashboard_url: dl.dashboardUrl(dashOf(a, from, to)), ...r,
        terms: linked(r.terms as Row[], x => x.term ? dl.dashboardUrl({ ...dashOf(a, from, to), v: "posts", q: `"${x.term}"` }) : null),
        note: BIAS_NOTE_EN, note_zh: BIAS_NOTE };
    })),
  },
  { name: "daily_summary", title: "Daily AI summary",
    description: "The scheduled daily AI summary of a stream (the morning report: heading, points, recommended action) for a day; today's covers yesterday, so a missing day falls back one day and says which. When there is none: generated false, the reason for each day looked up (e.g. the day's report found no posts in the stream) and the newest summary's day.",
    schema: { type: "object", properties: { stream: STREAM, day: DATE }, required: ["stream"] },
    run: (s, a) => knownStream(s, a.stream).then(() => guard(() => dailySummary(s, a))) },
  { name: "reports_list", title: "Report files",
    description: "The daily report files (HTML, PDF, Excel, Word…) by day and stream, newest first; read one with report_file." + CITE_BETA,
    schema: { type: "object", properties: { days: { type: "integer", minimum: 1, maximum: 120, default: 14 }, stream: STREAM } },
    run: (s, a) => guard(async () => {
      const l = await reports.listing(s, a.days || 14);
      return { reports: l.filter(r => !a.stream || r.profile === a.stream).map(r => ({ ...r, urls: r.files.map(f => SITE + f),
        dashboard_urls: r.files.map(f => dl.reportUrl(f)) })), dashboard_url: dl.reportUrl() };
    }) },
  { name: "report_file", title: "Read a report file",
    description: "One report file: text formats (HTML as plain text, CSV, JSON, TXT) come back as text; others give their public download link." + CITE_BETA,
    schema: { type: "object", properties: { path: { type: "string", description: "/reports/<day>/<stream>/<file> from reports_list" } }, required: ["path"] },
    run: (s, a) => guard(() => reportFile(s, a)) },
  { name: "sources_list", title: "Sources",
    description: "Every outlet and platform in the corpus by media type, with post counts and first day: the names posts_search's sources filter takes.",
    schema: { type: "object", properties: {} }, run: s => guard(() => search.meta(s)) },
  { name: "search", title: "Search (ChatGPT deep research)",
    description: "Search the collected posts and articles (the same search as posts_search, last 30 days). Returns ids for fetch.",
    schema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
    run: async (s, a) => {
      const r = await postsSearch(s, { query: a.query, page_size: 20 }) as Row;
      return { results: (r.posts as Row[]).map(p => ({ ...p, id: String(p.id), title: p.title || p.extract || String(p.id), url: p.url || p.item })) };
    } },
  { name: "fetch", title: "Fetch (ChatGPT deep research)",
    description: "The full text of one result from search, by id.",
    schema: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
    run: async (s, a) => {
      if (!/^\d+$/.test(String(a.id))) throw new ToolError("id is a post id from search");
      const p = await postGet(s, { id: Number(a.id), max_chars: 60000 }) as Row;
      const comments = (p.comments as Row[] || []).map(c => `— ${c.author || ""}: ${c.text}`).join("\n");
      return { id: String(p.id), title: p.title || String(p.id), url: p.url || p.item,
        text: [p.text, ...(p.transcripts as string[] || []), comments].filter(Boolean).join("\n\n"),
        dashboard_url: p.dashboard_url,
        metadata: clean({ source: p.source, board: p.board, author: p.author, type: p.type, region: p.region, platform: p.platform,
          published: p.published, item: p.item, dashboard_url: p.dashboard_url }) };
    } },
  ...WISERS_TOOLS.map(w => ({ ...w, schema: w.schema as Schema, run: (_: Sql, a: Row, ctx: Ctx) => w.run(a, ctx) })),
];

export const byName = new Map(TOOLS.map(t => [t.name, t]));

// ---- arguments: checked against the schema (an error names what to change), coerced from query strings ----

export function check(schema: Schema, v: unknown, path = "arguments"): void {
  const t = schema.type;
  if (t === "object") {
    if (typeof v !== "object" || v === null || Array.isArray(v)) throw new ToolError(`${path} must be an object`);
    const props = schema.properties || {};
    for (const k of schema.required || []) if ((v as Row)[k] == null || (v as Row)[k] === "") throw new ToolError(`${path}.${k} is required`);
    for (const [k, x] of Object.entries(v as Row)) {
      if (!(k in props)) throw new ToolError(`${path}.${k} is not a parameter (allowed: ${Object.keys(props).join(", ") || "none"})`);
      if (x != null) check(props[k], x, `${path}.${k}`);
    }
    return;
  }
  if (t === "string" && typeof v !== "string") throw new ToolError(`${path} must be a string`);
  if (t === "integer" && !Number.isInteger(v)) throw new ToolError(`${path} must be an integer`);
  if (t === "boolean" && typeof v !== "boolean") throw new ToolError(`${path} must be true or false`);
  if (t === "array") {
    if (!Array.isArray(v)) throw new ToolError(`${path} must be an array`);
    v.forEach((x, i) => check((schema.items as Schema) || {}, x, `${path}[${i}]`));
  }
  if (schema.enum && !(schema.enum as unknown[]).includes(v)) throw new ToolError(`${path} must be one of ${JSON.stringify(schema.enum)}`);
  if (typeof v === "string" && schema.pattern && !new RegExp(schema.pattern as string).test(v)) {
    throw new ToolError(`${path} must be ${schema.description || "of the form " + schema.pattern}, e.g. ${hktToday()}`);
  }
  if (typeof v === "number") {
    if (schema.minimum != null && v < (schema.minimum as number)) throw new ToolError(`${path} must be at least ${schema.minimum}`);
    if (schema.maximum != null && v > (schema.maximum as number)) throw new ToolError(`${path} must be at most ${schema.maximum}`);
  }
}

/** GET /api/v1/<tool>?a=b: every value is a string; turn them into the schema's types (check() names what's wrong) */
export function coerce(schema: Schema, sp: URLSearchParams): Row {
  const out: Row = {};
  for (const k of new Set(sp.keys())) {
    const p = schema.properties?.[k];
    const vals = sp.getAll(k);
    let v: unknown = vals[0];
    if (p?.type === "integer" && /^-?\d+$/.test(vals[0])) v = Number(vals[0]);
    else if (p?.type === "boolean" && /^(1|0|true|false|yes|no)$/i.test(vals[0])) v = /^(1|true|yes)$/i.test(vals[0]);
    else if (p?.type === "array") v = vals.flatMap(x => x.split(",")).map(x => x.trim()).filter(Boolean);
    out[k] = v;
  }
  return out;
}

/** keep an answer under MAX_CHARS: shorten its longest list and say how to get the rest */
export function fit(result: unknown): unknown {
  const s = JSON.stringify(result);
  if (s.length <= MAX_CHARS || typeof result !== "object" || result === null || Array.isArray(result)) return result;
  const r = result as Row;
  const key = Object.keys(r).filter(k => Array.isArray(r[k])).sort((a, b) => JSON.stringify(r[b]).length - JSON.stringify(r[a]).length)[0];
  if (!key) return { error: "answer too long; narrow the request (a shorter period, a stream, a filter)" };
  let keep = r[key].length;
  while (keep > 1 && JSON.stringify({ ...r, [key]: r[key].slice(0, keep) }).length > MAX_CHARS) keep = Math.floor(keep * 3 / 4);
  return { ...r, [key]: r[key].slice(0, keep), truncated: `${r[key].length - keep} more ${key} left out: ask for fewer, a later page or a filter` };
}

export async function runTool(sql: Sql, name: string, args: Row, ctx: Ctx) {
  const t = byName.get(name);
  if (!t) throw new ToolError(`unknown tool ${name} (tools: ${TOOLS.map(x => x.name).join(", ")})`);
  const a = Object.fromEntries(Object.entries(args || {}).filter(([, v]) => v !== null && v !== undefined));
  check(t.schema, a);
  try { return fit(await t.run(sql, a, ctx)); }
  catch (e) { if (e instanceof WisersError) throw new ToolError(e.message); throw e; }
}

export { dayAdd };
