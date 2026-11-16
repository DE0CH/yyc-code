// 數據分析's data (summary, scores, posts, hot_topics, topic, authors, sources, trend, terms, themes, persona, ai/daily,
// ai/context; the same logic as insights.py select, hot_topics, persona; corpus.posts / member_ids; thin.mark;
// agent.context_view). The figures that count posts one per vote (摘要, the scores, 作者, 來源, 趨勢, hot terms, a
// topic's top authors and sources, persona's hot terms) are server/analytics.ts and carry `note` (lib/bias.ts).
// A profile's posts = the posts the stream scorer gave at least the stream's threshold, plus those filed in the stream
// (insights.members); the filters only narrow that set. Read-only: nothing here writes.
import type { Row, Sql } from "./db";
import { config, type EdgeConfig } from "./config";
import { kvGet } from "./kv";
import { display, shown, shownTime } from "./titles";
import { dayAdd, hktToday, parseIso, pubKey } from "./time";
import { norm, topics } from "./topics";
import { TextError, textMatch } from "./dashboard-text";
import { pairs, tsq } from "./search/query";
import { searchText } from "./search/searchtext";
import { authorsApi, hotTerms, scoresApi, sourcesApi, summaryApi, termsApi, topAuthors, topSources, trendApi } from "./analytics";
import { BIAS_NOTE } from "../lib/bias";

export { authorsApi, scoresApi, sourcesApi, summaryApi, termsApi, trendApi };

/** an error the page shows: 404 ("no such profile") or 400 (a bad query: its message) */
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
const noProfile = () => new ApiError(404, "no such profile");
const bad = (m: string) => new ApiError(400, m);

/** the query string (blank values dropped, the first value of each key) */
export type Q = Record<string, string | undefined>;
const one = (q: Q, k: string, d?: string) => (q[k] != null && q[k] !== "" ? q[k] : d);

/** Python's int() of a query value (ValueError → 400 with Python's message; None → TypeError → 404) */
export function pyInt(v: string | undefined): number {
  if (v == null) throw noProfile();
  const s = v.trim().replace(/_/g, "");
  if (!/^[+-]?\d+$/.test(s)) throw bad(`invalid literal for int() with base 10: '${v}'`);
  return Number(s);
}

/** Python's round(x, nd): the exact binary value rounded, an exact half to even. toFixed rounds the exact value too
 *  but sends a half up, so an exact half (its exact decimal expansion, toFixed(nd + 60), ends in 5 then zeros) is
 *  decided here; 61.45 is no half (its binary value is 61.4500000000000028…), so it rounds up, as in Python. */
export function pyRound(x: number, nd = 1): number {
  if (!Number.isFinite(x)) return x;
  const a = Math.abs(x), digits = Math.min(100, nd + 60);
  const [, frac = ""] = a.toFixed(digits).split(".");
  if (/^50*$/.test(frac.slice(nd))) {  // an exact tie
    const m = 10 ** nd, f = Math.floor(a * m);
    return Math.sign(x) * (f % 2 === 0 ? f : f + 1) / m;
  }
  return Math.sign(x) * Number(a.toFixed(nd)) || 0;
}

/** Python's str sort order (code points); JS's default compares UTF-16 units */
export const cmpStr = (a: string, b: string) => {
  if (a === b) return 0;
  const A = Array.from(a), B = Array.from(b);
  for (let i = 0; i < Math.min(A.length, B.length); i++) {
    if (A[i] !== B[i]) return A[i].codePointAt(0)! - B[i].codePointAt(0)!;
  }
  return A.length - B.length;
};

/** a stable sort, descending by key (Python's sort(key, reverse=True) keeps the original order of equal items) */
export function sortDesc<T>(xs: T[], key: (x: T) => string | number): T[] {
  return xs.map((x, i) => [x, i] as const).sort((a, b) => {
    const ka = key(a[0]), kb = key(b[0]);
    const c = typeof ka === "number" ? (kb as number) - ka : ka < kb ? 1 : ka > kb ? -1 : 0;
    return c || a[1] - b[1];
  }).map(p => p[0]);
}

// ---- helpers ----

/** insights.days_between */
export function daysBetween(a: string, b: string): string[] {
  const out: string[] = [];
  for (let d = a; d <= b; d = dayAdd(d, 1)) out.push(d);
  return out;
}

/** web.period: [from, to] (default the last `defaultDays` days to today, HKT); dates are YYYY-MM-DD, at most 400 days */
export function period(q: Q, defaultDays = 7): [string, string] {
  const end = one(q, "to") ?? hktToday();
  let start = one(q, "from") ?? dayAdd(strptime(end), -defaultDays + 1);
  let e = end;
  for (const d of [start, e]) if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) throw bad("dates are YYYY-MM-DD");
  if (start > e) [start, e] = [e, start];
  strptime(start); strptime(e);
  if (daysBetween(start, e).length > 400) throw bad("at most 400 days");
  return [start, e];
}

/** datetime.strptime(d, "%Y-%m-%d") as a check: ValueError (400) with Python's message */
export function strptime(d: string): string {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(d);
  if (!m || +m[2] < 1 || +m[2] > 12 || +m[3] < 1 || +m[3] > 31) throw bad(`time data '${d}' does not match format '%Y-%m-%d'`);
  const days = new Date(Date.UTC(+m[1], +m[2], 0)).getUTCDate();
  if (+m[3] > days) throw bad("day is out of range for month");
  return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
}

// ---- corpus.posts ----

/** an int array as a Postgres array literal (sql.unsafe sends parameters as text) */
const pgArr = (ids: number[]) => "{" + ids.map(i => String(Math.trunc(Number(i)))).join(",") + "}";

// every column corpus.posts keeps (p.* without the search columns, the transcript and the picture text), in table order
const COLS = ["id", "url", "sid", "source", "website", "board", "author", "platform", "source_type", "region", "title", "text",
  "published", "seen", "day", "engagement", "cached_at", "section", "thread", "article_id", "url_key", "text_key",
  "first_run", "date_only", "link_key", "ai_title", "ai_title_kind", "ai_title_model", "ai_title_at", "ai_title_hash",
  "text_via", "text_via_url", "canonical_url"];
const LIGHT = COLS.filter(c => c !== "text").map(c => "p." + c).join(", ");

/** corpus.posts without the text (it is filled with fillText for the rows that are shown or exported): full rows with
 *  the profile's tags (sentiment and labels from the scorer when the profile has no row), newest first */
export async function posts(sql: Sql, ids: number[], profile: string | null): Promise<Row[]> {
  if (!ids.length) return [];
  const rows = profile
    ? await sql.unsafe(`SELECT ${LIGHT}, t.relevant, coalesce(t.sentiment, sc.sentiment) AS sentiment,
          coalesce(t.labels, sc.labels) AS labels, t.theme FROM posts p
          LEFT JOIN tags t ON t.post_id=p.id AND t.profile=$1 LEFT JOIN scored sc ON sc.post_id=p.id
          WHERE p.id = ANY($2::bigint[]) ORDER BY p.id`, [profile, pgArr(ids)] as never[])
    : await sql.unsafe(`SELECT ${LIGHT}, t.relevant, t.sentiment, t.labels, t.theme FROM posts p
          LEFT JOIN LATERAL (SELECT * FROM tags WHERE post_id=p.id ORDER BY tagged_at DESC, profile LIMIT 1) t ON TRUE
          WHERE p.id = ANY($1::bigint[]) ORDER BY p.id`, [pgArr(ids)] as never[]);
  const out = rows.map(r => {
    const d: Row = { ...r, text: undefined };
    noText.add(d);
    d.labels = d.labels ? JSON.parse(d.labels) : [];
    d.engagement = d.engagement ? JSON.parse(d.engagement) : {};
    return display(d);
  });
  return sortDesc(out, pubKey);
}

const noText = new WeakSet<Row>();  // rows from posts() whose text is not read yet

/** the text of these rows, as corpus.posts gives it (a title from AI: the text without its own headline) */
export async function fillText(sql: Sql, rows: Row[]): Promise<Row[]> {
  const need = rows.filter(r => noText.has(r));
  if (!need.length) return rows;
  const got = await sql`SELECT id, title, text, ai_title, ai_title_kind FROM posts WHERE id = ANY(${pgArr(need.map(r => r.id as number))}::bigint[])`;
  const by = new Map(got.map(r => [r.id as number, r]));
  for (const x of need) {
    const r = by.get(x.id);
    noText.delete(x);
    x.text = !r ? null : r.ai_title ? shown(r.ai_title, r.ai_title_kind ?? null, r.title ?? null, r.text ?? null).text : r.text;
  }
  return rows;
}

/** corpus.member_ids */
export async function memberIds(sql: Sql, stream: string, threshold: number, start: string, end: string): Promise<number[]> {
  const rows = await sql`SELECT s.post_id FROM scores s JOIN posts p ON p.id=s.post_id
      WHERE s.stream=${stream} AND s.score >= ${threshold} AND p.day BETWEEN ${start} AND ${end}
      UNION SELECT p.id FROM stream_items si JOIN posts p ON p.url=si.url
      WHERE si.stream=${stream} AND p.day BETWEEN ${start} AND ${end} ORDER BY 1`;
  return rows.map(r => Number(r.post_id));
}

const profileOf = (c: EdgeConfig, pid: string | undefined) => c.profiles.find(p => p.id === pid) || null;
export const thresholdOf = (c: EdgeConfig, pid: string) => c.thresholds[pid] ?? 50;

export interface Filters {
  theme?: string; sentiment?: string; label?: string; source_type?: string; website?: string; author?: string;
  text?: string; topic_type?: string;
}

/** insights.select */
export async function select(sql: Sql, c: EdgeConfig, pid: string | undefined, start: string, end: string, f: Filters = {}): Promise<Row[]> {
  if (!pid || !profileOf(c, pid)) throw noProfile();
  let ps = await posts(sql, await memberIds(sql, pid, thresholdOf(c, pid), start, end), pid);
  if (f.text) {  // the posts the words match first, then the substring-only matches, each newest first
    const m = await textMatch(sql, f.text, start, end, ps.map(x => x.id as number)).catch(e => {
      throw e instanceof TextError ? new ApiError(e.status, e.message) : e;
    });
    const order = new Map(m.order.map((id, n) => [id, n]));
    ps = ps.filter(x => order.has(x.id)).sort((a, b) => order.get(a.id)! - order.get(b.id)!);
    for (const x of ps) x.match = m.words.has(x.id) ? "word" : "substring";
  }
  if (f.source_type) {
    const types = new Set(f.source_type.split(","));
    ps = ps.filter(x => types.has(x.source_type));
  }
  for (const [key, val] of [["theme", f.theme], ["sentiment", f.sentiment], ["website", f.website], ["author", f.author]] as const) {
    if (val) ps = ps.filter(x => (x[key] || "") === val);
  }
  if (f.label) ps = ps.filter(x => (x.labels as string[]).includes(f.label!));
  if (f.topic_type === "new") {
    const first = new Set(topics(ps as never[]).map(t => (t.posts as Row[])[t.posts.length - 1].id));
    ps = ps.filter(x => first.has(x.id));
  }
  return ps;
}

/** web.selected */
export async function selected(sql: Sql, q: Q) {
  const c = await config(sql);
  const pid = one(q, "p");
  const [start, end] = period(q);
  const ps = await select(sql, c, pid, start, end, { theme: one(q, "theme"), sentiment: one(q, "sentiment"),
    label: one(q, "label"), source_type: one(q, "type"), website: one(q, "site"), author: one(q, "author"),
    text: one(q, "q"), topic_type: one(q, "topic_type") });
  return { c, pid: pid!, start, end, ps };
}

// ---- thin.mark, web.post_view ----

/** thin.mark: 「內容不足，未評分」 on the posts the near-empty gate rejected on their current content */
export async function markThin(sql: Sql, c: EdgeConfig, rows: Row[]): Promise<Row[]> {
  const ids = rows.map(p => p.id).filter(i => Number.isInteger(i));
  if (!ids.length) return rows;
  const got = new Set((await sql.unsafe(`SELECT t.post_id FROM thin_posts t JOIN posts p ON p.id = t.post_id
      WHERE t.post_id = ANY($1::bigint[]) AND t.status = 'rejected' AND t.hash = ${c.hash_sql}`, [pgArr(ids)] as never[])).map(r => Number(r.post_id)));
  for (const p of rows) if (got.has(p.id)) p.thin = c.thin.label;
  return rows;
}

const VIEW_KEYS = ["id", "url", "title", "text", "source", "website", "board", "author", "platform", "source_type", "region",
  "published", "seen", "day", "sentiment", "labels", "theme", "engagement", "cached_at", "match", "title_by_ai",
  "title_extracted", "title_linked", "title_retitled", "original_title", "ai_title_model", "ai_title_at", "thin"];

/** web.post_view: a post for 數據分析 and its exports (a date-only post's publish time is its bare day) */
export function postView(x: Row): Row {
  const o: Row = {};
  for (const k of VIEW_KEYS) o[k] = x[k] ?? null;
  o.link = x.canonical_url || x.url;  // what Original link opens: the article's own address (canonical.py)
  o.published = shownTime(x);
  o.date_only = !!x.date_only;
  return o;
}

// ---- insights figures ----

const SENTS = ["positive", "neutral", "negative", "mixed", "irony"];

export function sentimentCounts(ps: Row[]): Record<string, number> {
  const c: Record<string, number> = {};
  for (const x of ps) c[String(x.sentiment)] = (c[String(x.sentiment)] || 0) + 1;
  return Object.fromEntries(SENTS.map(k => [k, c[k] || 0]));
}

export function sentimentIndex(ps: Row[]): number {
  const c = sentimentCounts(ps);
  const tot = SENTS.reduce((a, k) => a + c[k], 0);
  return tot ? pyRound(100 * (c.positive - c.negative) / tot, 1) : 0.0;
}

export function views(ps: Row[]): number | null {
  const v = ps.map(x => Math.trunc(Number((x.engagement || {}).views || 0) || 0));
  return v.some(Boolean) ? v.reduce((a, b) => a + b, 0) : null;
}

export const authorsOf = (ps: Row[]) => new Set(ps.map(x => x.author || "@" + (x.website || "?")));

export function sentimentLevel(si: number) {
  return si <= -50 ? "Very Negative" : si < -5 ? "Slightly Negative" : si <= 5 ? "Neutral" : si < 50 ? "Slightly Positive" : "Very Positive";
}

/** (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days + 1; 1 when Python would fail (unreadable, or
 *  one time with an offset and one without) */
function daySpan(first: string, last: string): number {
  const aware = (s: string) => /(Z|[+-]\d{2}:?\d{2})$/.test(s.trim()) && /T|\s\d/.test(s);
  const a = parseIso(first), b = parseIso(last);
  if (!a || !b || aware(first) !== aware(last)) return 1;
  // naive times read as HKT both: the difference is the same as Python's naive difference
  return Math.floor((b.ms - a.ms) / 864e5) + 1;
}

/** insights._all_post_lookup: the index lookup that narrows a topic's all-posts count, from the title in its search
 *  form: the grams index (every character pair of the first 12 characters), or, for a prefix of only ASCII, its whole
 *  words (the words index): an English title's pairs are in nearly every English post, and that lookup alone ran
 *  past the 30 s statement timeout. The count may then differ by a post or two (Deyao, 2026-11-13). */
export function allPostLookup(title: string): { by: "words" | "grams"; q: string } {
  const cs = Array.from(title), term = cs.slice(0, 12).join("");
  if (/^[\x00-\x7f]*$/.test(term)) {
    const next = cs[12] ?? "", ws: string[] = [];
    for (const m of term.matchAll(/[a-z0-9]+/g))
      if ((m.index! + m[0].length < term.length || !/^[a-z0-9]$/.test(next)) && !ws.includes(m[0])) ws.push(m[0]);
    if (ws.length) return { by: "words", q: ws.map(tsq).join(" & ") };
  }
  return { by: "grams", q: pairs(term).map(tsq).join(" & ") };
}

/** insights._all_post_count for several topics: every corpus post on the topic (not only the profile's), i.e. whose
 *  search text holds the title's first 12 characters in their search form (Simplified, as hay is). An index narrows
 *  the substring test, as in Python: one strpos pass over all of posts for every topic ran past the 30 s statement
 *  timeout (analysis_hot_topics, 13 Nov 2026). */
export async function allPostCounts(sql: Sql, tops: { title: string; posts: unknown[] }[]): Promise<number[]> {
  const st = await searchText(sql);
  const titles = tops.map(t => {
    const title = st.norm(t.title);
    return Array.from(norm(t.title)).slice(0, 12).length < 6 || Array.from(title).slice(0, 12).length < 2 ? null : title;
  });
  const term = (title: string) => Array.from(title).slice(0, 12).join("");
  const need = new Map<string, string>();  // the 12 characters → the title they are cut from
  for (const t of titles) if (t !== null && !need.has(term(t))) need.set(term(t), t);
  const counts = new Map<string, number>();
  await Promise.all([...need].map(async ([k, title]) => {
    const l = allPostLookup(title);
    const [r] = l.by === "words"
      ? await sql`SELECT count(*) AS n FROM posts WHERE (words @@ ${l.q}::tsquery OR grams IS NULL) AND strpos(hay, ${k}) > 0`
      : await sql`SELECT count(*) AS n FROM posts WHERE (grams @@ ${l.q}::tsquery OR grams IS NULL) AND strpos(hay, ${k}) > 0`;
    counts.set(k, Number(r.n));
  }));
  return tops.map((t, i) => (titles[i] === null ? t.posts.length : Math.max(counts.get(term(titles[i]!))!, t.posts.length)));
}

/** insights.hot_topics (K-Matrix's Hot Topic Listing) */
export async function hotTopicRows(sql: Sql, ps: Row[], top: number): Promise<Row[]> {
  const tops = topics(ps as never[]).slice(0, Math.max(0, top)) as unknown as { id: number; title: string; title_by_ai: boolean; title_linked: boolean; url: string; posts: Row[]; first: string | null; last: string | null }[];
  const total = Math.max(1, ps.length);
  const alls = await allPostCounts(sql, tops);
  return tops.map((t, i) => {
    const xs = t.posts, si = sentimentIndex(xs), c = sentimentCounts(xs), all = alls[i];
    const first = t.first || "", last = t.last || "", lead = xs[xs.length - 1];
    return { id: t.id, title: t.title, title_by_ai: t.title_by_ai, title_linked: t.title_linked, url: t.url,
      website: lead.website ?? null, board: lead.board ?? null, author: lead.author ?? null, source_type: lead.source_type ?? null,
      region: lead.region ?? null, sentiment_index: si, sentiment_score: c.positive - c.negative,
      sentiment_level: sentimentLevel(si), contribution: pyRound(100 * xs.length / total, 1),
      all_post_count: all, period_post_count: xs.length, matched_post_count: xs.length, participants: authorsOf(xs).size,
      view_count: views(xs), correlation_index: pyRound(100 * xs.length / Math.max(1, all), 1),
      initial_date: Array.from(first).slice(0, 10).join(""), start_date: Array.from(first).slice(0, 10).join(""),
      end_date: Array.from(last).slice(0, 10).join(""), day_span: daySpan(first, last),
      websites: [...new Set(xs.map(x => x.website || ""))].sort(cmpStr), post_ids: xs.map(x => x.id) };
  });
}

/** insights.persona: an author's history: every corpus post by them in [start - 365 days, end] (not only matches) */
export async function personaData(sql: Sql, c: EdgeConfig, pid: string | undefined, author: string | undefined, start: string, end: string) {
  const rows = author == null ? [] : await sql`SELECT id FROM posts WHERE author=${author} AND day BETWEEN ${dayAdd(start, -365)} AND ${end}
      ORDER BY coalesce(published,seen) DESC, id DESC`;
  const allp = await posts(sql, rows.map(r => Number(r.id)), pid ?? null);
  const matched = allp.filter(x => x.relevant);
  const boards = counter(allp.map(x => `${x.website == null ? "None" : x.website}${x.board ? " - " + x.board : ""}`));
  const sites = counter(allp.map(x => x.website ?? null));
  const recent = allp.slice(0, 100);
  await fillText(sql, allp);  // the hot terms read every post's text
  await markThin(sql, c, recent);
  const terms = allp.length ? await hotTerms(sql, c, pid, start, end, 30, allp) : [];
  const t = (x: Row | undefined) => (x ? x.published || x.seen || null : null);
  return { author: author ?? null, posts: allp.length, matched_posts: matched.length,
    sentiment_index_on_profile: sentimentIndex(matched), sentiment: sentimentCounts(matched),
    boards: boards.slice(0, 10).map(([k, v]) => ({ board: k, posts: v })),
    websites: sites.slice(0, 10).map(([k, v]) => ({ website: k, posts: v })),
    first_seen: allp.length ? t(allp[allp.length - 1]) : null, last_seen: allp.length ? t(allp[0]) : null,
    hot_terms: terms, note: BIAS_NOTE, recent };
}

/** Counter(...).most_common(): by count, ties in first-seen order */
export function counter<K>(xs: K[]): [K, number][] {
  const m = new Map<K, number>();
  for (const x of xs) m.set(x, (m.get(x) || 0) + 1);
  return [...m.entries()].map((e, i) => [e, i] as const).sort((a, b) => b[0][1] - a[0][1] || a[1] - b[1]).map(x => x[0]);
}

// ---- the endpoints ----

export async function postsApi(sql: Sql, q: Q) {
  const { c, ps } = await selected(sql, q);
  let pageN = pyInt(one(q, "page", "0")) || 0;
  const size = Math.min(200, pyInt(one(q, "n", "25")));
  const post = one(q, "post", "")!;
  const at = /^\d+$/.test(post) ? ps.findIndex(x => String(x.id) === post) : -1;
  const found = /^\d+$/.test(post) && at >= 0 ? at : null;
  pageN = pageN || (found !== null ? Math.floor(found / size) + 1 : 1);
  const slice = pySlice(ps, (pageN - 1) * size, pageN * size);
  await fillText(sql, slice);
  await markThin(sql, c, slice);
  const labels = c.label_order.map(l => [l, ps.filter(x => (x.labels as string[]).includes(l)).length] as [string, number]);
  const themes: string[] = [];
  for (const x of ps) if (x.theme && !themes.includes(x.theme)) themes.push(x.theme);
  return {
    total: ps.length, page: pageN, size, post_found: !post ? null : found !== null,
    posts: slice.map(postView),
    facets: {
      sentiment: sentimentCounts(ps),
      labels: Object.fromEntries(labels.map((e, i) => [e, i] as const).sort((a, b) => b[0][1] - a[0][1] || a[1] - b[1]).map(x => x[0])),
      themes: Object.fromEntries(themes.map(t => [t, ps.filter(x => x.theme === t).length] as [string, number])
        .map((e, i) => [e, i] as const).sort((a, b) => b[0][1] - a[0][1] || a[1] - b[1]).map(x => x[0])),
      websites: [...new Set(ps.map(x => x.website).filter(Boolean) as string[])].sort(cmpStr),
      source_types: Object.fromEntries(counter(ps.map(x => String(x.source_type ?? null)))),
    },
    note: BIAS_NOTE,  // the 情緒分佈 panel (the profile-wide sentiment shares)
  };
}

/** Python's list slicing ps[a:b] (negative indexes count from the end) */
function pySlice<T>(xs: T[], a: number, b: number): T[] {
  const n = xs.length, f = (i: number) => (i < 0 ? Math.max(0, n + i) : Math.min(n, i));
  return xs.slice(f(a), Math.max(f(a), f(b)));
}

export async function hotTopicsApi(sql: Sql, q: Q) {
  const { ps } = await selected(sql, q);
  const rows = await hotTopicRows(sql, ps, pyInt(one(q, "top", "10")));
  for (const r of rows) delete r.post_ids;
  return { topics: rows };
}

export async function topicApi(sql: Sql, q: Q) {
  const { c, start, end, ps } = await selected(sql, q);
  const tid = pyInt(one(q, "id"));
  const t = topics(ps as never[]).find(t => t.id === tid) as unknown as { title: string; title_by_ai: boolean; title_linked: boolean; url: string; posts: Row[] } | undefined;
  if (!t) throw bad("no such topic in this period");
  const xs = t.posts;
  const byDay = new Map<string, number>();
  for (const x of xs) byDay.set(x.day, (byDay.get(x.day) || 0) + 1);
  await fillText(sql, xs);
  await markThin(sql, c, xs);
  const lead = xs.find(x => x.url === t.url);
  return { title: t.title, title_by_ai: t.title_by_ai, title_linked: t.title_linked, url: t.url,
    link: lead?.canonical_url || t.url, stats: { posts: xs.length, participants: authorsOf(xs).size, websites: new Set(xs.map(x => x.website ?? null)).size,
      sentiment: sentimentCounts(xs), sentiment_index: sentimentIndex(xs) },
    posts: xs.map(postView), top_authors: topAuthors(xs, 10), top_sources: topSources(xs, 10),
    trend: daysBetween(start, end).map(d => ({ period: d, posts: byDay.get(d) || 0 })), note: BIAS_NOTE };
}

export async function themesApi(sql: Sql, q: Q) {
  const { pid, ps } = await selected(sql, q);
  const groups = new Map<string, Row[]>();
  for (const x of ps) { const k = x.theme || ""; if (!groups.has(k)) groups.set(k, []); groups.get(k)!.push(x); }
  const kv = await kvGet<{ themes?: string[]; at?: string }>(sql, "themes:" + pid, {});
  return { themes: kv.themes ?? [], generated: kv.at ?? null,
    counts: sortDesc([...groups.entries()], e => e[1].length).filter(([k]) => k)
      .map(([k, v]) => ({ theme: k, posts: v.length, sentiment_index: sentimentIndex(v) })) };
}

export async function personaApi(sql: Sql, q: Q) {
  const c = await config(sql);
  const [start, end] = period(q, 90);
  return personaData(sql, c, one(q, "p"), one(q, "author"), start, end);
}

/** ai/daily: the scheduled AI summary (reports.py) of a profile and day; today's report covers yesterday, so it falls
 *  back one day and says which. (The cluster's web.py also backfills `context` on summaries made before they kept
 *  what they read; that write stays on the cluster: such a summary shows here without its 「AI 讀了…」 line.) */
export async function aiDailyApi(sql: Sql, q: Q) {
  const pid = one(q, "p"), day = one(q, "day", hktToday())!;
  for (const d of [day, null]) {
    const dd = d ?? dayAdd(strptime(day), -1);
    let hit = await kvGet<unknown>(sql, `daily:${pid ?? "None"}:${dd}`, null);
    if (hit) {
      if (typeof hit === "object" && !Array.isArray(hit)) {
        const h = hit as Row;
        if (h.context) hit = { ...h, note: (await kvGet<Row | null>(sql, "ctx:" + h.context, null) || {}).note ?? null };
        return { ...(hit as Row), day: (hit as Row).day || dd };
      }
      return { summary: hit, day: dd };
    }
  }
  return {};
}

const URL_RE = /\]\((https?:\/\/[^)\s]+)\)/g;
const ITEM_RE = /\]\(\/item\/(\d+)\)/g;

/** agent.context_view: the posts an AI summary read, newest first, "cited" when the summary links to one */
export async function aiContextApi(sql: Sql, q: Q) {
  const cid = one(q, "id", "")!;
  const c = await kvGet<Row | null>(sql, "ctx:" + cid, null);
  if (!c) throw bad("no such context");
  const summary = String(c.summary || "");
  const cited = new Set([...summary.matchAll(URL_RE)].map(m => m[1]));
  const citedIds = new Set([...summary.matchAll(ITEM_RE)].map(m => Number(m[1])));
  const keep = ["id", "title", "url", "website", "board", "author", "published", "seen", "source_type", "sentiment", "labels", "theme"];
  const ps = (await posts(sql, (c.ids || []).map(Number), c.profile ?? null)).map(p => {
    const o: Row = {};
    for (const k of keep) o[k] = p[k] ?? null;
    o.published = shownTime(p);
    o.cited = cited.has(p.url) || citedIds.has(p.id);
    return o;
  });
  return { id: cid, kind: c.kind ?? null, title: c.title ?? null, label: c.label ?? null, note: c.note ?? null,
    total: c.total ?? null, read: (c.ids || []).length, cited: ps.filter(p => p.cited).length, posts: ps };
}

export const ENDPOINTS = {
  summary: summaryApi, scores: scoresApi, posts: postsApi, hot_topics: hotTopicsApi, topic: topicApi,
  authors: authorsApi, sources: sourcesApi, trend: trendApi, terms: termsApi, themes: themesApi, persona: personaApi,
  "ai/daily": aiDailyApi, "ai/context": aiContextApi,
} as const;
export type Endpoint = keyof typeof ENDPOINTS;

/** one endpoint → {status, body} (tests; the server functions throw instead) */
export async function apiJson(sql: Sql, name: Endpoint, q: Q) {
  try {
    return { status: 200, body: await ENDPOINTS[name](sql, q) };
  } catch (e) {
    if (e instanceof ApiError) return { status: e.status, body: { error: e.message } };
    throw e;
  }
}

/** several endpoints in one go (the parity tests) */
export async function apiBatch(sql: Sql, calls: [Endpoint, Q][]) {
  const out = [];
  for (const [name, q] of calls) out.push(await apiJson(sql, name, q));
  return out;
}
