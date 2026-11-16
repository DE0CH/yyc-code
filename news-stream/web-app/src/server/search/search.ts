// The 搜尋 section's data. GET /api/search (run), /api/search/meta (meta),
// /api/export/search.xlsx (exportXlsx): the same parameters, SQL, ordering, facets, extract and JSON. Licensing: a
// result shows the headline, a short extract around the first match and the publisher's link; the full text is
// searched but never sent, here or in the export.
import type { Row, Sql } from "../db";
import { display } from "../titles";
import { shownTime } from "../titles";
import { dayAdd, hktToday } from "../time";
import { dt } from "../../lib/dates";
import { attachment, xlsx, type Cell } from "../xlsx";
import { learn, parse, pg, PyTypeError, pyRepr, QueryError, rankSql, terms as qterms, toSql, variants, type Parsed } from "./query";
import { pyStrip, searchText, type SearchText } from "./searchtext";
import { splitSql } from "./split";

export const EXTRACT = 160;
export const EXPORT_MAX = 5000;
const PAGE_MAX = 100;
export const TYPE_ZH: Record<string, string> = { News: "新聞", Broadcast: "電視/電台", Official: "官方", Blog: "專欄/網誌",
  Forum: "討論區", "Social Media": "社交媒體", Video: "影片" };
export const REGION_ZH: Record<string, string> = { HK: "香港", Macau: "澳門", Mainland: "內地", Taiwan: "台灣", Global: "海外" };
const SORTS = ["date", "oldest", "relevance"];
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** an error answer: 400 {"error"} (a bad query), 404 {"error": "no such profile"} (a PyTypeError) */
export class SearchError extends Error { constructor(public status: number, message: string) { super(message); } }

/** the query string: blank values dropped, every value of a key */
export type QS = Record<string, string[]>;
export function qsFrom(sp: URLSearchParams): QS {
  const q: QS = {};
  for (const [k, v] of sp) if (v !== "") (q[k] ??= []).push(v);
  return q;
}
const one = (q: QS, k: string): string | undefined => q[k]?.[0];
const list = (q: QS, k: string) => (q[k] || []).flatMap(v => v.split(",").map(x => pyStrip(x)).filter(Boolean));

/** int(): Python's accepted forms, its error otherwise */
function pyInt(v: string): number {
  const s = pyStrip(v);
  if (!/^[+-]?\d+(_\d+)*$/.test(s)) throw new SearchError(400, `invalid literal for int() with base 10: ${pyRepr(v)}`);
  return parseInt(s.replace(/_/g, ""), 10);
}

/** datetime.fromisoformat(end) - 29 days, as a day (the default start) */
function minus29(end: string): string {
  const m = /^(\d{4})-?(\d{2})-?(\d{2})(?:[T ].*)?$/.exec(end);
  if (!m) throw new SearchError(400, `Invalid isoformat string: ${pyRepr(end)}`);
  const [y, mo, d] = [+m[1], +m[2], +m[3]];
  if (mo < 1 || mo > 12) throw new SearchError(400, "month must be in 1..12");
  if (d < 1 || d > new Date(Date.UTC(y, mo, 0)).getUTCDate()) throw new SearchError(400, "day is out of range for month");
  if (y < 1) throw new SearchError(400, `year ${y} is out of range`);
  return dayAdd(`${m[1]}-${m[2]}-${m[3]}`, -29);
}

export interface Search {
  q: string; parsed: Parsed; types: string[]; sites: string[]; regions: string[]; start: string; end: string;
  sort: string; title_only: boolean; comments: boolean; page: number; n: number;
}

export function params(st: SearchText, q: QS): Search {
  const today = hktToday();
  const end0 = one(q, "to") || today;
  let start = one(q, "from") || minus29(end0), end = end0;
  if (!(DAY.test(start) && DAY.test(end))) throw new SearchError(400, "日期格式應為 YYYY-MM-DD");
  if (start > end) [start, end] = [end, start];
  const sort = one(q, "sort") ?? "date";
  if (!SORTS.includes(sort)) throw new SearchError(400, "排序只可以是 date、oldest 或 relevance");
  const text = pyStrip(one(q, "q") || "");
  if (Array.from(text).length > 1000) throw new SearchError(400, "查詢太長（最多 1,000 字）");
  let parsed: Parsed;
  try { parsed = parse((one(q, "title") === "1" ? "TopicSearchOnly " : "") + text, st); }
  catch (e) { if (e instanceof QueryError) throw new SearchError(400, `查詢有誤：${e.message}`); throw e; }
  const page = Math.max(1, pyInt(one(q, "page") || "1"));
  const n = Math.min(PAGE_MAX, Math.max(1, pyInt(one(q, "n") || "25")));
  return { q: text, parsed, types: list(q, "type"), sites: list(q, "site"), regions: list(q, "region"), start, end, sort,
    title_only: one(q, "title") === "1", comments: one(q, "comments") === "1", page, n };
}

export type P = string | number;
/** a text[] literal (the client sends parameters as text: fetch_types is off), every element quoted */
export const textArr = (xs: string[]) => "{" + xs.map(x => '"' + x.replace(/["\\]/g, "\\$&") + '"').join(",") + "}";

export function where(st: SearchText, s: Search): [string, P[]] {
  const [w, wargs] = toSql(st, s.parsed);
  let sql = `p.day BETWEEN %s AND %s AND (${w})`;
  const args: P[] = [s.start, s.end, ...wargs];
  for (const [key, col] of [["types", "source_type"], ["sites", "website"], ["regions", "region"]] as const) {
    if (s[key].length) {
      // a real array, so the planner sees the values (a rare outlet: posts_website, not a scan of the period)
      sql += ` AND p.${col} = ANY(%s::text[])`;
      args.push(textArr(s[key]));
    }
  }
  // (IS NULL) IS TRUE, the same rows: checked on each row the words index finds, not as a bitmap of posts_thread ANDed
  // in (576k rows, ~45 of a search's ~100 ms on pg-1, 2026-10-29; Postgres simplifies IS NOT DISTINCT FROM NULL back)
  if (!s.comments) sql += " AND (p.thread IS NULL) IS TRUE";
  return [sql, args];
}

export function order(st: SearchText, s: Search): [string, P[]] {
  const [rank, rargs] = rankSql(st, s.parsed);
  const tier = s.parsed.node ? `${rank} DESC, ` : "";
  if (s.sort === "oldest") return [tier + "coalesce(p.published, p.seen) ASC, p.id ASC", tier ? rargs : []];
  if (s.sort === "relevance") {
    const ts = [...new Set(qterms(s.parsed.node).map(t => st.norm(t)))].filter(Boolean).slice(0, 10);
    if (ts.length) {
      const parts: string[] = [], args: P[] = [];
      for (const t of ts) {
        parts.push("5 * (strpos(coalesce(p.stitle, lower(p.title)), %s) > 0)::int"
          + " + least(10, (length(p.hay) - length(replace(p.hay, %s, ''))) / %s)");
        args.push(t, t, Array.from(t).length);
      }
      return [`${tier}(${parts.join(" + ")}) DESC, coalesce(p.published, p.seen) DESC, p.id DESC`, [...rargs, ...args]];
    }
  }
  return [tier + "coalesce(p.published, p.seen) DESC, p.id DESC", tier ? rargs : []];
}

export const COLS = "p.id, p.url, p.title, p.text, p.website, p.board, p.author, p.source_type, p.region, p.platform, p.published, "
  + "p.seen, p.date_only, p.thread, p.ai_title, p.ai_title_kind, p.canonical_url";

const WS_RUN = /[\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g;

/** ≤ EXTRACT characters of the text around the first matched term (the start of the text when no term is in it) */
export function extract(st: SearchText, text: string | null, title: string | null, terms: string[]): string {
  const t = pyStrip((text || "").replace(WS_RUN, " "));
  if (!t || t === title) return "";
  const low = st.lower(t), tc = Array.from(t);
  const hits = terms.filter(Boolean).map(x => { const i = low.indexOf(x); return i < 0 ? -1 : Array.from(low.slice(0, i)).length; })
    .filter(i => i >= 0);
  if (!hits.length) return tc.slice(0, EXTRACT).join("") + (tc.length > EXTRACT ? "…" : "");
  const i = Math.min(...hits);
  const a = Math.max(0, i - Math.floor(EXTRACT / 3)), b = Math.min(tc.length, a + EXTRACT);
  return (a ? "…" : "") + tc.slice(a, b).join("") + (b < tc.length ? "…" : "");
}

export function view(st: SearchText, r: Row, terms: string[]) {
  r = display({ ...r });
  // link: what 原文 opens, the article's own address when its page states another (canonical.py)
  return { id: r.id, match: r.word ? "word" : "substring", url: r.url, link: r.canonical_url || r.url, title: r.title,
    title_by_ai: !!r.title_by_ai,
    title_linked: !!r.title_linked, extract: extract(st, r.text, r.title, terms),
    website: r.website, board: r.board, author: r.author, source_type: r.source_type,
    region: r.region, platform: r.platform, time: shownTime(r) || r.seen, comment: !!r.thread };
}

export async function rows(sql: Sql, text: string, args: P[]): Promise<Row[]> {
  // one trip (each costs ~60 ms from the Worker to Postgres, 2026-10-29): no transaction (the timeout is the role's own,
  // yyc_edge statement_timeout 30s: Hyperdrive drops a connection's), and not prepared: no parse step on the request's
  // new connection, and Postgres plans every search with its own values (a prepared statement's generic plan read
  // whole days: 224 ms → 10.9 s)
  return (await sql.unsafe(pg(text), args as never[], { prepare: false })) as unknown as Row[];
}

export async function prepared(sql: Sql, q: QS) {
  const st = await searchText(sql);
  const s = params(st, q);
  await learn(sql, st, s.parsed);
  return { st, s };
}

/** /api/search → {total, page, n, from, to, sort, terms, posts, facets, took_ms}. A query of words alone sorted by
 *  date runs as one split statement (split.ts: page and facets together); `split: false` (the parity tests) runs the
 *  one query for every search, as other queries do. */
export async function run(sql: Sql, q: QS, split = true) {
  const { st, s } = await prepared(sql, q);
  const t0 = Date.now();
  const terms = variants(st, qterms(s.parsed.node));
  const sp = split ? splitSql(st, s, "", [], "facets") : null;
  let rs: Row[], facets: Row[];
  if (sp) {
    const out = await rows(sql, sp[0], [...sp[1], s.n, (s.page - 1) * s.n]);
    facets = (out[0]?.facets ?? []) as Row[];
    rs = out.filter(r => r.id != null);
  } else {
    const [w, args] = where(st, s);
    const [ord, oargs] = order(st, s);
    const [rank, rargs] = rankSql(st, s.parsed);
    [rs, facets] = await Promise.all([
      rows(sql, `SELECT ${COLS}, ${rank} AS word FROM posts p WHERE ${w} ORDER BY ${ord} LIMIT %s OFFSET %s`,
        [...rargs, ...args, ...oargs, s.n, (s.page - 1) * s.n]),
      rows(sql, `SELECT p.source_type, p.website, count(*) AS n FROM posts p WHERE ${w} GROUP BY 1, 2`, args),
    ]);
  }
  const types = new Map<string, number>(), sites = new Map<string | null, number>();
  for (const r of facets) {
    const k = r.source_type ?? "null";  // json.dumps writes a None key as "null"
    types.set(k, (types.get(k) || 0) + Number(r.n));
    sites.set(r.website, (sites.get(r.website) || 0) + Number(r.n));
  }
  const total = [...types.values()].reduce((a, b) => a + b, 0);
  const byCount = <K,>(m: Map<K, number>) => [...m.entries()].map((e, i) => [e, i] as const)
    .sort((a, b) => b[0][1] - a[0][1] || a[1] - b[1]).map(x => x[0]);
  return { total, page: s.page, n: s.n, from: s.start, to: s.end, sort: s.sort, terms,
    posts: rs.map(r => view(st, r, terms)),
    facets: { types: Object.fromEntries(byCount(types)),
      sites: Object.fromEntries(byCount(sites).filter(([k]) => k).slice(0, 40)) },
    took_ms: Date.now() - t0 };
}

let metaCache: { at: number; v: unknown } | null = null;

/** /api/search/meta: the media types and the outlets of each (posts in the corpus), for the filters; cached 10 minutes */
export async function meta(sql: Sql) {
  if (metaCache && Date.now() - metaCache.at < 600_000) return metaCache.v;
  const rs = await rows(sql, "SELECT source_type, website, count(*) AS n, min(day) AS first FROM posts WHERE thread IS NULL "
    + "AND website IS NOT NULL GROUP BY 1, 2", []);
  const by = new Map<string, { site: string; posts: number; first: string }[]>();
  for (const r of rs) {
    const k = r.source_type || "News";
    if (!by.has(k)) by.set(k, []);
    by.get(k)!.push({ site: r.website, posts: Number(r.n), first: r.first });
  }
  const order = Object.keys(TYPE_ZH);
  const idx = (k: string) => (order.includes(k) ? order.indexOf(k) : 99);
  const stable = <T,>(xs: T[], key: (x: T) => number) => xs.map((x, i) => [x, i] as const)
    .sort((a, b) => key(a[0]) - key(b[0]) || a[1] - b[1]).map(x => x[0]);
  const types = stable([...by.entries()], ([k]) => idx(k)).map(([t, xs]) => ({ type: t, zh: TYPE_ZH[t] ?? t,
    posts: xs.reduce((a, x) => a + x.posts, 0), sites: stable(xs, x => -x.posts) }));
  const [f] = await rows(sql, "SELECT min(day) AS d FROM posts WHERE thread IS NULL", []);
  const v = { types, regions: REGION_ZH, first_day: f?.d ?? null, today: hktToday(), export_max: EXPORT_MAX };
  metaCache = { at: Date.now(), v };
  return v;
}

export const EXPORT_COLS = ["標題", "媒體", "欄目/版面", "作者", "媒體類型", "地區", "發佈時間", "摘錄", "連結"];

/** titles.plain: the title as text, 〔AI標題〕 before an AI-written title, 〔連結標題〕 before a linked page's */
const plain = (x: Row) => (x.title_by_ai ? "〔AI標題〕" : x.title_linked ? "〔連結標題〕" : "") + (x.title || "");

/** the export's time: the 匯出時間 cell (lib/dates.ts) and the file name's YYYYMMDD-HHMM (HKT) */
const fileStamp = () => new Date(Date.now() + 8 * 3600e3).toISOString().replace(/:\d\d\.\d+Z$/, "").replace(/[-:]/g, "").replace("T", "-");

/** search.export → [file name, the rows as Python builds them (for the parity test), meta rows]; split as run() */
export async function exportRows(sql: Sql, q: QS, split = true) {
  const { st, s } = await prepared(sql, q);
  const terms = variants(st, qterms(s.parsed.node));
  const sp = split ? splitSql(st, s, "", [], null) : null;
  let found: Row[];
  if (sp) found = await rows(sql, sp[0], [...sp[1], EXPORT_MAX, 0]);
  else {
    const [w, args] = where(st, s);
    const [ord, oargs] = order(st, s);
    found = await rows(sql, `SELECT ${COLS} FROM posts p WHERE ${w} ORDER BY ${ord} LIMIT %s`, [...args, ...oargs, EXPORT_MAX]);
  }
  const rs = found.map(r => display({ ...r }));
  const out: Cell[][] = rs.map(r => [plain(r), r.website, r.board, r.author, TYPE_ZH[r.source_type] ?? r.source_type,
    REGION_ZH[r.region] ?? r.region, dt.sortable(shownTime(r) || r.seen),
    extract(st, r.text, r.title, terms), r.url]);
  const names: Record<string, string> = { date: "最新優先", oldest: "最舊優先", relevance: "相關度" };
  const metaRows: [string, Cell][] = [["查詢", s.q || "（全部）"], ["只搜標題", s.title_only ? "是" : "否"],
    ["媒體類型", s.types.map(t => TYPE_ZH[t] ?? t).join("、") || "全部"],
    ["媒體", s.sites.join("、") || "全部"], ["地區", s.regions.map(r => REGION_ZH[r] ?? r).join("、") || "全部"],
    ["期間", `${s.start} 至 ${s.end}`], ["排序", names[s.sort]],
    ["結果", `${out.length} 篇` + (out.length >= EXPORT_MAX ? `（只匯出首 ${EXPORT_MAX} 篇）` : "")],
    ["匯出時間", dt.dateTimeYear(Date.now())], ["說明", "只列標題、摘錄及原文連結；全文請按連結到出版機構網站閱讀。"]];
  return { name: `search_${s.start}_${s.end}_${fileStamp()}.xlsx`, rows: out, meta: metaRows };
}

/** /api/export/search.xlsx → [file name, bytes] */
export async function exportXlsx(sql: Sql, q: QS): Promise<[string, Uint8Array]> {
  const e = await exportRows(sql, q);
  return [e.name, await xlsx([["搜尋結果", EXPORT_COLS, e.rows]], e.meta)];
}

export { attachment };

// ---- for tests/edge_parity.py search (scripts/call.ts passes JSON) ----
export const runJson = (sql: Sql, q: QS, split = true) => run(sql, q, split);
/** whether run() answers this search with the split statement */
export const splits = async (sql: Sql, q: QS) => { const { st, s } = await prepared(sql, q); return !!splitSql(st, s, "", [], "facets"); };
export const metaFresh = (sql: Sql) => { metaCache = null; return meta(sql); };
/** the export written to a file (Node only: the parity test reads it back with openpyxl) → its name */
export async function exportFile(sql: Sql, q: QS, path: string) {
  const [name, data] = await exportXlsx(sql, q);
  const fs = await import("node:fs");
  fs.writeFileSync(path, data);
  return name;
}

/** many searches in one process (the parity test): each one's JSON without took_ms, or {status, error}; `split` as run() */
export async function runMany(sql: Sql, qs: QS[], split = true) {
  const out: unknown[] = [];
  for (const q of qs) {
    try { const r = await run(sql, q, split) as Record<string, unknown>; delete r.took_ms; out.push(r); }
    catch (e) { out.push(errorJson(e)); }
  }
  return out;
}

/** an error → {status, error} (anything else is rethrown) */
export function errorJson(e: unknown): { status: number; error: string } {
  if (e instanceof SearchError) return { status: e.status, error: e.message };
  if (e instanceof PyTypeError) return { status: 404, error: "no such profile" };
  throw e;
}
