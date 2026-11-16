// news-stream/query.py on the Worker: the keyword language (K-Matrix's operators) parsed into the same tree and compiled
// into the same Postgres WHERE clause (word match on posts.words, substring fallback on posts.hay with the posts.grams
// prefilter, NEARBY = the SQL function near()). SQL is built with %s placeholders in Python's order and numbered at
// the end (pg()). Every term's words come from searchtext (learn() first: one dictionary read for the whole query).
import { PY_WS, pyStrip, type SearchText } from "./searchtext";

export const FIELDS = ["title", "content", "author", "source"];
export const SOURCE_TYPES = ["News", "Newspaper", "Magazine", "Forum", "Social Media", "Video", "Blog", "Official", "Broadcast"];

export class QueryError extends Error {}
/** what Python raises as a TypeError (a None where the compiler expects a node: `NOT TopicSearchOnly`, `FindIn(title:)`);
 *  it answers 404 {"error": "no such profile"} */
export class PyTypeError extends Error {}

/** Python's repr() of a str */
export function pyRepr(s: string): string {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = "";
  for (const ch of s) {
    const c = ch.codePointAt(0)!;
    if (ch === "\\") out += "\\\\";
    else if (ch === q) out += "\\" + q;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (c < 0x20 || c === 0x7f) out += "\\x" + c.toString(16).padStart(2, "0");
    else if (/[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u.test(ch) || (/\p{Zs}/u.test(ch) && ch !== " ")) {
      out += c <= 0xff ? "\\x" + c.toString(16).padStart(2, "0") : c <= 0xffff ? "\\u" + c.toString(16).padStart(4, "0") : "\\U" + c.toString(16).padStart(8, "0");
    } else out += ch;
  }
  return q + out + q;
}

export type Node =
  | ["term", string]
  | ["near", string, string, number]
  | ["not", Node | null]
  | ["field", string, Node | null]
  | ["and" | "or", (Node | null)[]];
export interface Parsed { node: Node | null; topic_only: boolean; inc: string[]; exc: string[] }

type Tok = [string, string];
const WS = PY_WS.replace(/[\\\]^-]/g, "\\$&");
const TOKEN = new RegExp(`[${WS}]*(?:(\\()|(\\))|(,)|(:)|"([^"]*)"|([^${WS}(),:"]+))`, "y");
const KINDS = ["lp", "rp", "comma", "colon", "phrase", "word"];
const cps = (s: string) => Array.from(s);

export function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  const s = src.replace(/“/g, '"').replace(/”/g, '"').replace(/（/g, "(").replace(/）/g, ")").replace(/，/g, ",").replace(/：/g, ":");
  let pos = 0;
  while (pos < s.length) {
    if (pyStrip(s.slice(pos)) === "") break;
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(s);
    if (!m || m.index + m[0].length === pos) throw new QueryError(`cannot read the query at: ${cps(s.slice(pos)).slice(0, 20).join("")}`);
    pos = m.index + m[0].length;
    const g = KINDS.findIndex((_, i) => m[i + 1] !== undefined);
    out.push([KINDS[g], m[g + 1]]);
  }
  return out;
}

const head = (v: string) => v.split("/")[0].toUpperCase();

class Parser {
  t: Tok[]; i = 0; topic_only = false; inc: string[] = []; exc: string[] = [];
  constructor(s: string) { this.t = tokenize(s); }
  peek(k?: string, v?: string) {
    if (this.i >= this.t.length) return false;
    const [tk, tv] = this.t[this.i];
    return (k === undefined || tk === k) && (v === undefined || tv === v);
  }
  take() { this.i++; return this.t[this.i - 1]; }
  wordIs(...names: string[]) { return this.peek("word") && names.includes(head(this.t[this.i][1])); }
  parse(): Node | null {
    const node = this.i < this.t.length ? this.or_() : null;
    if (this.i < this.t.length) throw new QueryError(`unexpected ${pyRepr(this.t[this.i][1])}`);
    return node;
  }
  startsOperand() {
    if (this.i >= this.t.length) return false;
    const [k, v] = this.t[this.i];
    if (k === "lp" || k === "phrase") return true;
    return k === "word" && !["AND", "OR", "NEARBY", "NEAR"].includes(head(v));
  }
  or_(): Node | null {
    const parts: (Node | null)[] = [];
    for (;;) {
      if (this.wordIs("OR")) this.take();
      else if (this.wordIs("NOT") && parts.some(p => p !== null)) {
        this.take();
        const rest = this.or_();
        const left = Parser.combine(parts);
        return rest !== null ? ["and", [left, ["not", rest]]] : left;
      } else if (this.startsOperand()) parts.push(this.and_());
      else break;
    }
    return Parser.combine(parts);
  }
  static combine(parts: (Node | null)[]): Node | null {
    const ps = parts.filter((p): p is Node => p !== null);
    return ps.length === 1 ? ps[0] : ps.length ? ["or", ps] : null;
  }
  and_(): Node | null {
    let parts: (Node | null)[] = [this.near()];
    while (this.wordIs("AND")) { this.take(); parts.push(this.near()); }
    parts = parts.filter(p => p !== null);
    return parts.length === 1 ? parts[0] : parts.length ? ["and", parts] : null;
  }
  near(): Node | null {
    let left = this.not_();
    while (this.wordIs("NEARBY", "NEAR")) {
      const w = this.take()[1];
      const seg = w.split("/");
      const n = w.includes("/") && /^[0-9]+$/.test(seg[1]) ? parseInt(seg[1], 10) : 10;
      const right = this.not_();
      if (!(left && left[0] === "term" && right && right[0] === "term")) throw new QueryError("NEARBY joins two words or phrases");
      left = ["near", left[1], right[1], n];
    }
    return left;
  }
  not_(): Node | null {
    if (this.wordIs("NOT")) { this.take(); return ["not", this.not_()]; }
    return this.atom();
  }
  args(): string[] {
    if (!this.peek("lp")) throw new QueryError("expected ( after IncData/ExcData");
    this.take();
    const out: string[] = [];
    let cur: string[] = [];
    while (!this.peek("rp")) {
      if (this.i >= this.t.length) throw new QueryError("missing )");
      const [k, v] = this.take();
      if (k === "comma") { out.push(cur.join(" ")); cur = []; } else cur.push(v);
    }
    this.take();
    if (cur.length) out.push(cur.join(" "));
    return out.map(a => pyStrip(a)).filter(Boolean);
  }
  atom(): Node | null {
    if (this.i >= this.t.length) throw new QueryError("the query ends too early");
    const [k, v] = this.t[this.i];
    if (k === "lp") {
      this.take();
      const node = this.or_();
      if (!this.peek("rp")) throw new QueryError("missing )");
      this.take();
      return node;
    }
    if (k === "phrase") { this.take(); return ["term", pyLower(v)]; }
    if (k === "word") {
      const u = v.toUpperCase();
      if (u === "TOPICSEARCHONLY") { this.take(); this.topic_only = true; return null; }
      const nextLp = this.i + 1 < this.t.length && this.t[this.i + 1][0] === "lp";
      if ((u === "INCDATA" || u === "EXCDATA") && nextLp) {
        this.take();
        (u === "INCDATA" ? this.inc : this.exc).push(...this.args());
        return null;
      }
      if (u === "FINDIN" && nextLp) {
        this.take(); this.take();
        if (!this.peek("word")) throw new QueryError("FindIn(field: words)");
        const field = pyLower(this.take()[1]);
        if (!FIELDS.includes(field)) throw new QueryError(`FindIn field must be one of ${FIELDS.join(", ")}`);
        if (!this.peek("colon")) throw new QueryError("FindIn(field: words) needs a colon");
        this.take();
        const node = this.or_();
        if (!this.peek("rp")) throw new QueryError("missing ) after FindIn");
        this.take();
        return ["field", field, node];
      }
      this.take();
      return ["term", pyLower(v)];
    }
    throw new QueryError(`unexpected ${pyRepr(v)}`);
  }
}

// str.lower() for the parser (before any SearchText exists): the terms are lowered again by norm() for matching;
// variants() lowers with Python's Unicode table
let lowerFn: (s: string) => string = s => s.toLowerCase();
const pyLower = (s: string) => lowerFn(s);

export function parse(s: string, st?: SearchText): Parsed {
  if (st) lowerFn = x => st.lower(x);
  const p = new Parser(s || "");
  const node = p.parse();
  return { node, topic_only: p.topic_only, inc: p.inc, exc: p.exc };
}

const need = <T,>(x: T | null | undefined): T => { if (x == null) throw new PyTypeError("'NoneType' object is not subscriptable"); return x; };

/** every positive search term (for highlighting) */
export function terms(node: Node | null): string[] {
  if (node === null) return [];
  const k = node[0];
  if (k === "term") return [node[1]];
  if (k === "near") return [node[1], node[2]];
  if (k === "not") return [];
  if (k === "field") return terms(node[2]);
  return node[1].flatMap(n => terms(need(n)));
}

/** every term in the tree, NOT and FindIn included: what learn() must read the words of */
function allTerms(node: Node | null, out: string[] = []): string[] {
  if (node === null) return out;
  const k = node[0];
  if (k === "term") out.push(node[1]);
  else if (k === "near") out.push(node[1], node[2]);
  else if (k === "not") allTerms(node[1], out);
  else if (k === "field") allTerms(node[2], out);
  else for (const n of node[1]) allTerms(n, out);
  return out;
}

/** read the dictionary words every term of the query needs (one database read) */
export async function learn(sql: import("../db").Sql, st: SearchText, q: Parsed) {
  await st.learn(sql, allTerms(q.node).map(t => st.norm(t)));
}

/** query.to_sql's port for one query string → [where, params] (tests/test_search_subs.py compares it with Python's) */
export async function sqlFor(sql: import("../db").Sql, q: string): Promise<Q> {
  const { searchText } = await import("./searchtext");
  const st = await searchText(sql);
  const p = parse(q, st);
  await learn(sql, st, p);
  return toSql(st, p);
}

export const tsq = (g: string) => "'" + g.replace(/\\/g, "\\\\").replace(/'/g, "''") + "'";
export function pairs(t: string): string[] {
  const c = cps(t), s = new Set<string>();
  for (let i = 0; i < c.length - 1; i++) s.add(c[i] + c[i + 1]);
  return [...s].sort(pyCmp);
}
/** Python's str ordering (by code point) */
export const pyCmp = (a: string, b: string) => {
  const x = cps(a), y = cps(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
};
const uniq = (xs: string[]) => [...new Set(xs)];

export function variants(st: SearchText, ts: string[]): string[] {
  return uniq(ts.flatMap(t => st.variants(t)));
}

const RAW: Record<string, string> = { content: "lower(coalesce(p.text,''))", author: "lower(coalesce(p.author,''))",
  source: "lower(p.source || ' ' || p.website)" };
const TITLE_COL = "coalesce(p.stitle, lower(p.title))";
type P = string | number;
type Q = [string, P[]];

function forms(st: SearchText, t: string, ctx: string): string[] {
  if (ctx in RAW) return st.variants(t);
  return uniq([st.norm(t), t]);
}
/** searchtext.SUB_LEN: a Latin term up to this long is looked up in post_subs */
export const SUB_LEN = 32;
const SUB_TERM = new RegExp(`^[a-z0-9]{1,${SUB_LEN}}$`);
/** query.SUBS_IDS / SUBS_TEST: the posts post_subs names for a term (one GIN lookup), and one post's exact test */
const SUBS_IDS = "p.id = ANY(ARRAY(SELECT s.post_id FROM post_subs s WHERE s.subs @@ %s::tsquery OR s.subs IS NULL))";
const SUBS_TEST = "coalesce((SELECT s.subs @@ %s::tsquery FROM post_subs s WHERE s.post_id = p.id), ({strpos}))";
/** query._sub: the term's prefix tsquery on post_subs (only a-z/0-9 once normalised, in the search text), else null */
function sub(st: SearchText, t: string, ctx: string): string | null {
  if (ctx !== "all") return null;
  const n = st.norm(t);
  return SUB_TERM.test(n) ? tsq(n) + ":*" : null;
}
function word(st: SearchText, t: string, ctx: string): string | null {
  if (ctx in RAW) return null;
  return st.tsquery(st.norm(t), ctx === "title");
}

export function prefilter(st: SearchText, q: Parsed): Q | null {
  if (q.topic_only) return null;
  const t = (node: Node | null, ctx = "all"): Q | null => {
    node = need(node);
    const k = node[0];
    if (k === "term") {
      const fs = uniq([...st.variants(node[1]), ...forms(st, node[1], ctx)]).filter(Boolean);
      if (!fs.length) return null;
      const g = fs.map(f => (cps(f).length > 1 ? "(" + pairs(f).map(tsq).join(" & ") + ")" : tsq(f) + ":*")).join(" | ");
      const w = word(st, node[1], "all");
      const n = sub(st, node[1], ctx);
      if (n && st.d.subs) return ["(" + (w ? "p.words @@ %s::tsquery OR " : "") + SUBS_IDS + ")", [...(w ? [w] : []), n]];
      if (w) return ["(p.words @@ %s::tsquery OR p.grams @@ %s::tsquery)", [w, g]];
      return ["p.grams @@ %s::tsquery", [g]];
    }
    if (k === "near") return t(["and", [["term", node[1]], ["term", node[2]]]], ctx);
    if (k === "not") return null;
    if (k === "field") return node[1] === "content" || node[1] === "author" ? t(node[2], node[1]) : null;
    const parts = node[1].map(n => t(n, ctx));
    if (k === "and") {
      const ps = parts.filter((x): x is Q => !!x);
      if (!ps.length) return null;
      return ["(" + ps.map(x => x[0]).join(" AND ") + ")", ps.flatMap(x => x[1])];
    }
    if (!parts.length || parts.some(x => x === null)) return null;
    return ["(" + parts.map(x => x![0]).join(" OR ") + ")", parts.flatMap(x => x![1])];
  };
  return q.node ? t(q.node) : null;
}

/** the condition a query tree puts on a post (exported for yyc-mcp's posts_search, which splits word and substring matches) */
export function cond(st: SearchText, node: Node | null, ctx: string): Q {
  node = need(node);
  const k = node[0];
  const col = ctx === "all" ? "p.hay" : ctx === "title" ? TITLE_COL : RAW[ctx];
  if (k === "term") {
    const parts: string[] = [], args: P[] = [];
    const w = word(st, node[1], ctx);
    if (w) { parts.push("coalesce(p.words @@ %s::tsquery, FALSE)"); args.push(w); }
    const fs = forms(st, node[1], ctx), ss = fs.map(() => `strpos(${col}, %s) > 0`);
    const n = sub(st, node[1], ctx);
    if (n) { parts.push(SUBS_TEST.replace("{strpos}", ss.join(" OR "))); args.push(n, ...fs); }
    else { parts.push(...ss); args.push(...fs); }
    return ["(" + parts.join(" OR ") + ")", args];
  }
  if (k === "near") {
    const fa = forms(st, node[1], ctx), fb = forms(st, node[2], ctx);
    const ps: [string, string][] = fa.length !== fb.length ? fa.flatMap(x => fb.map(y => [x, y] as [string, string]))
      : fa.map((x, i) => [x, fb[i]]);
    const seen = new Set<string>(), parts: string[] = [], args: P[] = [];
    for (const [a, b] of ps) {
      const key = JSON.stringify([a, b]);
      if (seen.has(key)) continue;
      seen.add(key);
      parts.push(`(strpos(${col}, %s) > 0 AND strpos(${col}, %s) > 0 AND near(${col}, %s, %s, %s))`);
      args.push(a, b, a, b, node[3]);
    }
    return ["(" + parts.join(" OR ") + ")", args];
  }
  if (k === "not") { const [s, a] = cond(st, node[1], ctx); return [`NOT (${s})`, a]; }
  if (k === "field") return cond(st, node[2], node[1]);
  const parts = node[1].map(n => cond(st, n, ctx));
  return ["(" + parts.map(x => x[0]).join(` ${k.toUpperCase()} `) + ")", parts.flatMap(x => x[1])];
}

export function toSql(st: SearchText, q: Parsed): Q {
  const ctx = q.topic_only ? "title" : "all";
  let [where, params] = q.node ? cond(st, q.node, ctx) : ["TRUE", [] as P[]];
  for (const [lst, neg] of [[q.inc, false], [q.exc, true]] as [string[], boolean][]) {
    if (!lst.length) continue;
    const conds: string[] = [];
    for (const name of lst) {
      conds.push("(p.source_type = %s OR p.website = %s OR p.sid = %s OR p.platform = %s OR p.source = %s)");
      params = [...params, name, name, name, name, name];
    }
    const s = "(" + conds.join(" OR ") + ")";
    where = `(${where}) AND ` + (neg ? `NOT ${s}` : s);
  }
  const pre = prefilter(st, q);
  if (pre) {
    where = `(${pre[0]} OR p.grams IS NULL) AND (${where})`;
    params = [...pre[1], ...params];
  }
  return [where, params];
}

export function rankSql(st: SearchText, q: Parsed): Q {
  const r = (node: Node | null, ctx: string): Q => {
    node = need(node);
    const k = node[0];
    if (k === "term") {
      const w = word(st, node[1], ctx);
      return w ? ["coalesce(p.words @@ %s::tsquery, FALSE)", [w]] : ["FALSE", []];
    }
    if (k === "near") return r(["and", [["term", node[1]], ["term", node[2]]]], ctx);
    if (k === "not") return ["TRUE", []];
    if (k === "field") return node[1] === "title" ? r(node[2], node[1]) : ["TRUE", []];
    const parts = node[1].map(n => r(n, ctx));
    return ["(" + parts.map(x => x[0]).join(` ${k.toUpperCase()} `) + ")", parts.flatMap(x => x[1])];
  };
  if (!q.node) return ["1", []];
  const [s, a] = r(q.node, q.topic_only ? "title" : "all");
  return [`(${s})::int`, a];
}

/** %s placeholders → $1, $2, … for postgres.js */
export function pg(sqlText: string): string {
  let n = 0;
  return sqlText.replace(/%s/g, () => "$" + ++n);
}
