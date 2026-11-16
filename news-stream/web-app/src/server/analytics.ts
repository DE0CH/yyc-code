// 數據分析's figures that count posts one per vote (Deyao, 2026-10-16: back, each with the selection-bias note): 摘要's
// metrics and Exposure & Mentioning Trend, the daily scores SPI/CSS/DVS/DPS/CCS and their 7-day series, 趨勢, 作者,
// 來源, Smart Hot Terms. The same logic as insights.py metrics, day_counts, scores, spi_series, trend, top_authors,
// top_sources, hot_terms and summary_view (which the daily reports and the data API's tools keep using;
// tests/edge_parity.py analytics compares the two). Every answer carries `note`: they depend on which sources and
// keywords YYC follows (lib/bias.ts).
import type { Row, Sql } from "./db";
import { config, type EdgeConfig } from "./config";
import { dayAdd, hktToday } from "./time";
import { weekday } from "../lib/dates";
import { topics } from "./topics";
import { BIAS_NOTE } from "../lib/bias";
import {
  ApiError, authorsOf, cmpStr, counter, daysBetween, fillText, memberIds, period, posts, pyInt, pyRound, selected,
  sentimentCounts, sentimentIndex, sentimentLevel, sortDesc, strptime, thresholdOf, views, type Q,
} from "./dashboard";

const one = (q: Q, k: string, d?: string) => (q[k] != null && q[k] !== "" ? q[k] : d);

/** insights.METHOD: how the scores and the sentiment index are made (shown on the page) */
export const METHOD = {
  sentiment_index: "100 × (positive − negative) ÷ (positive + neutral + negative + mixed + irony) posts, in %"
    + " (−100 to +100, as K-Matrix's dashboard); the 0–100 form is 50 + half of it.",
  DVS: "Discussion Volume Score: the day's matched posts against the profile's average over the 30 days before:"
    + " 50 at the average, 100 × r ÷ (r + 1) where r = posts ÷ average (the average counts as at least 3, so a"
    + " quiet profile's few posts don't score as a surge).",
  DPS: "Discussion Participation Score: the same formula on unique authors (posts without an author count as"
    + " one author per website).",
  CCS: "Consumer Coverage Score: the same formula on distinct websites + boards.",
  CSS: "Consumer Sentiment Score: the 0–100 sentiment index.",
  SPI: "Social Popularity Index: the mean of DVS, DPS and CCS (volume, people and spread: boards, topics, posts"
    + " and authors, as K-Matrix describes its SPI).",
  levels: "Very Low < 20 ≤ Low < 40 ≤ Medium < 60 ≤ High < 80 ≤ Very High",
};

const noProfile = () => new ApiError(404, "no such profile");
function known(c: EdgeConfig, pid: string | undefined): string {
  if (!pid || !c.profiles.some(p => p.id === pid)) throw noProfile();
  return pid;
}

/** a set of Python values (None, str, tuples) by a key that keeps them apart */
const key = (v: unknown) => JSON.stringify(v ?? null);
const boardKeys = (ps: Row[]) => ps.filter(x => x.board).map(x => key([x.website ?? null, x.board]));

/** the corpus's websites and posts in [start, end] (website coverage, posts spread) */
async function corpusTotals(sql: Sql, start: string, end: string): Promise<[number, number]> {
  // grouped, not count(DISTINCT website): a hash of the websites, not a sort of every post's website on disk (insights.metrics)
  const [r] = await sql`SELECT count(website) AS s, coalesce(sum(n), 0)::bigint AS n FROM (SELECT website, count(*) AS n
    FROM posts WHERE day BETWEEN ${start} AND ${end} GROUP BY website) w`;
  return [Number(r.s) || 1, Number(r.n) || 1];
}

/** insights.metrics */
export function metrics(ps: Row[], start: string, end: string, prior: Set<number>, total: [number, number]) {
  const nd = Math.max(1, daysBetween(start, end).length);
  const tops = topics(ps as never[]) as unknown as { posts: Row[] }[];
  const sites = new Set(ps.map(x => key(x.website)));
  const boards = new Set(boardKeys(ps));
  const auth = authorsOf(ps);
  const byType: Record<string, number> = {};
  for (const x of ps) { const k = String(x.source_type ?? null); byType[k] = (byType[k] || 0) + 1; }
  const [totalSites, totalPosts] = total;
  const c = sentimentCounts(ps), si = sentimentIndex(ps);
  return {
    posts: ps.length, new_posts: ps.filter(x => !prior.has(x.id)).length, topics: tops.length,
    new_topics: tops.filter(t => !prior.has(t.posts[t.posts.length - 1].id)).length,
    websites: sites.size, boards: boards.size, authors: auth.size, view_count: views(ps),
    by_source_type: byType, sentiment: c, sentiment_index: si,
    sentiment_index_100: pyRound(50 + si / 2, 1), sentiment_balance: c.positive - c.negative,
    posts_per_day: pyRound(ps.length / nd, 1), authors_per_day: pyRound(auth.size / nd, 1),
    topics_per_day: pyRound(tops.length / nd, 1), websites_per_day: pyRound(sites.size / nd, 1),
    boards_per_day: pyRound(boards.size / nd, 1),
    website_coverage: pyRound(100 * sites.size / totalSites, 1),
    posts_spread: pyRound(100 * ps.length / totalPosts, 2),
  };
}

/** insights.level */
export const level = (x: number) => (x < 20 ? "Very Low" : x < 40 ? "Low" : x < 60 ? "Medium" : x < 80 ? "High" : "Very High");

/** insights._score: the average counts as at least 3, so a quiet profile's 2 posts aren't "Very High" */
function score(v: number, avg: number) {
  const r = v / Math.max(avg, 3.0);
  return pyRound(100 * r / (r + 1), 1);
}

interface DayCount { posts: number; authors: number; coverage: number; sentiment_index: number; topics: number; views: number }

/** the profile's posts in [start, end] (insights.members → corpus.posts) */
async function memberPosts(sql: Sql, c: EdgeConfig, pid: string, start: string, end: string) {
  return posts(sql, await memberIds(sql, pid, thresholdOf(c, pid), start, end), pid);
}

/** insights.day_counts: per day, posts, authors, websites+boards, sentiment index, topics, views (one pass) */
async function dayCounts(sql: Sql, c: EdgeConfig, pid: string, start: string, end: string, withTopics = true) {
  const ps = await memberPosts(sql, c, pid, start, end);
  const by = new Map<string, Row[]>();
  for (const x of ps) { if (!by.has(x.day)) by.set(x.day, []); by.get(x.day)!.push(x); }
  const out = new Map<string, DayCount>();
  for (const d of daysBetween(start, end)) {
    const xs = by.get(d) || [];
    out.set(d, { posts: xs.length, authors: xs.length ? authorsOf(xs).size : 0,
      coverage: new Set([...xs.map(x => key(x.website)), ...boardKeys(xs)]).size,
      sentiment_index: sentimentIndex(xs), topics: withTopics && xs.length ? topics(xs as never[]).length : 0,
      views: views(xs) || 0 });
  }
  return out;
}

let firstCache: { at: number; v: string | null } | null = null;
/** the corpus's first day (no score baseline before it); read at most every 10 minutes per isolate */
async function firstDay(sql: Sql): Promise<string | null> {
  if (firstCache && Date.now() - firstCache.at < 600_000) return firstCache.v;
  const [r] = await sql`SELECT min(day) AS d FROM posts`;
  firstCache = { at: Date.now(), v: (r.d as string) ?? null };
  return firstCache.v;
}

type Scores = Record<"SPI" | "CSS" | "DVS" | "DPS" | "CCS", number>;

/** one day's five scores from the day counts (insights.scores' inner loop) */
function scoresOn(dc: Map<string, DayCount>, d: string, first: string | null): Scores {
  const f = first || d;
  const lo = [f, dayAdd(d, -30)].sort(cmpStr)[1];
  const base = daysBetween(lo, dayAdd(d, -1)).filter(x => dc.has(x)).map(x => dc.get(x)!);
  const avg = (k: "posts" | "authors" | "coverage") => (base.length ? base.reduce((a, b) => a + b[k], 0) / base.length : 0);
  const cur = dc.get(d) || { posts: 0, authors: 0, coverage: 0, sentiment_index: 0 };
  const dvs = score(cur.posts, avg("posts")), dps = score(cur.authors, avg("authors")), ccs = score(cur.coverage, avg("coverage"));
  const css = cur.posts ? pyRound(50 + cur.sentiment_index / 2, 1) : 50.0;
  return { SPI: pyRound((dvs + dps + ccs) / 3, 1), CSS: css, DVS: dvs, DPS: dps, CCS: ccs };
}

/** insights.scores from day counts that cover [day - 31, day] */
function scoresFrom(dc: Map<string, DayCount>, day: string, first: string | null) {
  const t = scoresOn(dc, day, first), y = scoresOn(dc, dayAdd(day, -1), first);
  const res: Record<string, unknown> = {};
  for (const k of Object.keys(t) as (keyof Scores)[]) {
    res[k] = { value: t[k], level: level(t[k]), change_pct: y[k] ? pyRound(100 * (t[k] - y[k]) / y[k], 1) : null };
  }
  return res;
}

/** insights.scores (K-Matrix's daily email scores for one day, with the change from the day before) */
export async function scores(sql: Sql, c: EdgeConfig, pid: string, day: string) {
  const start = dayAdd(day, -31);
  const [dc, first] = await Promise.all([dayCounts(sql, c, pid, start, day, false), firstDay(sql)]);
  const res = scoresFrom(dc, day, first);
  res.series = Object.fromEntries([...dc.entries()].map(([d, v]) => [d, { posts: v.posts, sentiment_index: v.sentiment_index }]));
  return res;
}

/** insights.spi_series: the last n days' SPI and CSS (one read of the days they need) */
export async function spiSeries(sql: Sql, c: EdgeConfig, pid: string, end: string, n = 7) {
  const days = daysBetween(dayAdd(end, -n + 1), end);
  const [dc, first] = await Promise.all([dayCounts(sql, c, pid, dayAdd(days[0] || end, -31), end, false), firstDay(sql)]);
  return days.map(d => { const s = scoresOn(dc, d, first); return { day: d, SPI: s.SPI, CSS: s.CSS }; });
}

/** the Monday of a day's ISO week (Python's d - timedelta(days=d.weekday())) */
const monday = (d: string) => dayAdd(d, -((weekday(d) + 6) % 7));

/** insights.trend: exposure & mentioning trend: posts, views and sentiment index per day or ISO week */
export async function trend(sql: Sql, c: EdgeConfig, pid: string, start: string, end: string, bucket = "day") {
  if (bucket === "day") {
    const dc = await dayCounts(sql, c, pid, start, end);
    return [...dc.entries()].map(([d, v]) => ({ period: d, ...v }));
  }
  const ps = await memberPosts(sql, c, pid, start, end);
  const by = new Map<string, Row[]>();
  for (const x of ps) { const w = monday(x.day); if (!by.has(w)) by.set(w, []); by.get(w)!.push(x); }
  const out = [];
  for (const d of daysBetween(start, end)) {
    if (monday(d) === d || d === start) {
      const wk = monday(d), xs = by.get(wk) || [];
      out.push({ period: wk, posts: xs.length, views: views(xs) || 0, authors: xs.length ? authorsOf(xs).size : 0,
        sentiment_index: sentimentIndex(xs), topics: xs.length ? topics(xs as never[]).length : 0 });
    }
  }
  return out;
}

/** insights.top_authors */
export function topAuthors(ps: Row[], top = 20) {
  const by = new Map<string, Row[]>();
  for (const x of ps) if (x.author) { const k = key([x.author, x.website ?? null]); if (!by.has(k)) by.set(k, []); by.get(k)!.push(x); }
  const rows = [...by.values()].map(xs => {
    const boards = counter(xs.map(x => x.board || ""));
    const si = sentimentIndex(xs);
    return { author: xs[0].author, website: xs[0].website ?? null, posts: xs.length, topics: topics(xs as never[]).length,
      sentiment_index: si, sentiment_level: sentimentLevel(si), contribution: pyRound(100 * xs.length / Math.max(1, ps.length), 1),
      view_count: views(xs), most_active_board: boards.length ? boards[0][0] : "" };
  });
  return sortDesc(rows, r => r.posts).slice(0, top);
}

/** insights.top_sources: by website, or by board (website + board) */
export function topSources(ps: Row[], top = 20, by = "website") {
  const groups = new Map<string, Row[]>();
  for (const x of ps) {
    const k = by === "website" ? key(x.website) : key([x.website ?? null, x.board || ""]);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(x);
  }
  const rows = [...groups.values()].map(xs => {
    const si = sentimentIndex(xs);
    return { website: xs[0].website ?? null, board: by === "website" ? null : xs[0].board || "",
      source_type: xs[0].source_type ?? null, region: xs[0].region ?? null, posts: xs.length,
      topics: topics(xs as never[]).length, participants: authorsOf(xs).size, sentiment_index: si,
      sentiment_level: sentimentLevel(si), contribution: pyRound(100 * xs.length / Math.max(1, ps.length), 1) };
  });
  return sortDesc(rows, r => r.posts).slice(0, top);
}

// ---- Smart Hot Terms ----

const STOP = new Set(Array.from("的了是在和與及也都就而及其為以於之或但這那有一個我們你他她它不人會要說對到被將從並等中上下來去"));
const CJK = /[一-鿿]{2,}/g;
const LATIN = /[A-Za-z][A-Za-z0-9+.-]{2,}/g;
const LATIN_STOP = new Set(["the", "and", "for", "with", "that", "this", "from", "are", "was", "has", "have", "will", "https", "http",
  "www", "com", "not", "you", "your", "its", "our", "but", "all", "more"]);

function termGrams(text: string): Set<string> {
  const out = new Set<string>();
  for (const run of text.match(CJK) || []) {
    for (const n of [2, 3, 4]) {
      for (let i = 0; i + n <= run.length; i++) {
        const g = run.slice(i, i + n);
        if (!(n === 2 && Array.from(g).some(ch => STOP.has(ch)))) out.add(g);
      }
    }
  }
  for (const w of text.match(LATIN) || []) { const l = w.toLowerCase(); if (!LATIN_STOP.has(l)) out.add(l); }
  return out;
}

/** insights.hot_terms: K-Matrix's Smart Hot Terms / word cloud: the profile's most frequent 2–4 character Chinese terms
 *  and words, relative to their frequency in the whole corpus over the same days (so common words drop out); the
 *  stream's own search terms and their pieces left out. ps needs its text (fillText). */
export async function hotTerms(sql: Sql, c: Pick<EdgeConfig, "profiles">, pid: string | null | undefined, start: string, end: string,
  top: number, ps: Row[], background?: string[]) {  // background: the titles to compare with (tests/edge_parity.py), else a random 3000 of the period's
  const p = c.profiles.find(x => x.id === pid);
  const qterms = new Set((p?.search_terms || []).flatMap(w => w.split(/\s+/).filter(Boolean)).map(t => t.toLowerCase()));
  const mine = new Map<string, number>();
  for (const x of ps) {
    for (const g of termGrams((x.title ?? "") + " " + Array.from(String(x.text || "")).slice(0, 500).join(""))) mine.set(g, (mine.get(g) || 0) + 1);
  }
  if (!mine.size) return [];
  const rows = background ? background.map(title => ({ title }))
    : await sql`SELECT title FROM posts WHERE day BETWEEN ${start} AND ${end} ORDER BY random() LIMIT 3000`;
  const bg = new Map<string, number>();
  for (const r of rows) for (const g of termGrams(String(r.title ?? ""))) bg.set(g, (bg.get(g) || 0) + 1);
  const nbg = Math.max(1, rows.length);
  const scored: [number, string, number][] = [];
  for (const [g, n] of mine) {
    if (n < 2 || [...qterms].some(t => t.includes(g.toLowerCase()))) continue;
    const lift = (n / ps.length) / (((bg.get(g) || 0) + 1) / nbg);
    if (lift < 2) continue;  // about as common everywhere: not a hot term for this profile
    scored.push([n * Math.log(lift), g, n]);
  }
  scored.sort((a, b) => b[0] - a[0] || -cmpStr(a[1], b[1]) || b[2] - a[2]);  // Python's sort(reverse=True) of the tuples
  let picked: [number, string, number][] = [];
  for (const [s, g, n] of scored) {
    if (picked.some(([, q]) => q.includes(g) && q.length > g.length && mine.get(q)! >= n * 0.8)) continue;  // a shorter piece of a longer term
    picked = picked.filter(x => !(g.includes(x[1]) && mine.get(g)! >= x[2] * 0.8));
    picked.push([s, g, n]);
    if (picked.length >= top) break;
  }
  return picked.map(([s, g, n]) => ({ term: g, count: n, weight: pyRound(s, 2) }));
}

// ---- the endpoints (each answer carries the note) ----

const CGM = new Set(["Forum", "Social Media", "Video", "Blog"]), NEWS = new Set(["News", "Newspaper", "Magazine", "Official"]);

/** insights.summary_view: the 摘要 view: trend + metrics split CGM / News / Analysis */
export async function summaryApi(sql: Sql, q: Q) {
  const { c, pid, start, end, ps } = await selected(sql, { p: one(q, "p"), from: one(q, "from"), to: one(q, "to") });
  const [priorIds, total] = await Promise.all([memberIds(sql, pid, thresholdOf(c, pid), dayAdd(start, -30), dayAdd(start, -1)),
    corpusTotals(sql, start, end)]);
  const prior = new Set(priorIds);
  const p = c.profiles.find(x => x.id === pid)!;
  return { profile: { id: p.id, name: p.name, title: p.title, kind: p.kind, brief: p.brief }, start, end,
    metrics: metrics(ps, start, end, prior, total),
    cgm: metrics(ps.filter(x => CGM.has(x.source_type)), start, end, prior, total),
    news: metrics(ps.filter(x => NEWS.has(x.source_type)), start, end, prior, total),
    trend: await trend(sql, c, pid, start, end, daysBetween(start, end).length <= 45 ? "day" : "week"),
    method: METHOD, note: BIAS_NOTE };
}

export async function scoresApi(sql: Sql, q: Q) {
  const c = await config(sql);
  const pid = known(c, one(q, "p"));
  const day = strptime(one(q, "day", hktToday())!);
  const n = pyInt(one(q, "n", "7"));
  const [sc, series] = await Promise.all([scores(sql, c, pid, day), spiSeries(sql, c, pid, day, n)]);
  return { scores: sc, spi_series: series, note: BIAS_NOTE };
}

export async function authorsApi(sql: Sql, q: Q) {
  const { ps } = await selected(sql, q);
  return { authors: topAuthors(ps, pyInt(one(q, "top", "50"))), note: BIAS_NOTE };
}

export async function sourcesApi(sql: Sql, q: Q) {
  const { ps } = await selected(sql, q);
  return { sources: topSources(ps, pyInt(one(q, "top", "50")), one(q, "by", "website")), note: BIAS_NOTE };
}

export async function trendApi(sql: Sql, q: Q) {
  const c = await config(sql);
  const pid = known(c, one(q, "p"));
  const [start, end] = period(q, 30);
  return { trend: await trend(sql, c, pid, start, end, one(q, "bucket", "day")), note: BIAS_NOTE };
}

export async function termsApi(sql: Sql, q: Q) {
  const { c, pid, start, end, ps } = await selected(sql, q);
  await fillText(sql, ps);
  return { terms: await hotTerms(sql, c, pid, start, end, pyInt(one(q, "top", "60")), ps), note: BIAS_NOTE };
}
