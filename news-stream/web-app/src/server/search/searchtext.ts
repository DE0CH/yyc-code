// news-stream/searchtext.py for QUERY terms on the Worker: norm() (NFKC → OpenCC hk2s → lower case), variants()
// (as typed, Simplified, HK and standard Traditional), tokens() (jieba, accurate mode, HMM off) and tsquery(). The
// posts' columns (hay, stitle, words, grams) are written by Python; here only the query is cut, with the dictionary
// and OpenCC data the Python side publishes (searchtext.publish_edge: kv search:edge + search:opencc, table
// search_words). A dictionary edit (search_dict.txt, a stream's query) or a jieba/OpenCC upgrade reaches the Worker
// with the next publish (the poller, every minute): the stamp in search:edge changes and this module follows it.
import type { Sql } from "../db";
import { candidates, cut } from "./jieba";
import { converters, type Converter, type OpenccData } from "./opencc";

export interface EdgeDict {
  stamp: string; version: string; logtotal: number; maxlen: number; opencc: string;
  unicode: { version: string; assigned: [number, number][]; alnum: [number, number][] };
  /** kv search:subs (query.subs_ready): every post has its post_subs row */
  subs?: boolean;
}

const MAX_LEXEME = 60;
const TTL_MS = 30_000;
let meta: { at: number; v: EdgeDict } | null = null;
let occ: { hash: string; conv: Record<string, Converter> } | null = null;

const inRanges = (rs: [number, number][], c: number) => {
  let lo = 0, hi = rs.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (c < rs[mid][0]) hi = mid - 1; else if (c > rs[mid][1]) lo = mid + 1; else return true;
  }
  return false;
};

/** Python's whitespace (str.isspace / str.strip / re's \s) */
export const PY_WS = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000";
const WS_SET = new Set(PY_WS);
export const isSpace = (ch: string) => WS_SET.has(ch);
export const pyStrip = (s: string) => {
  let a = 0, b = s.length;
  while (a < b && WS_SET.has(s[a])) a++;
  while (b > a && WS_SET.has(s[b - 1])) b--;
  return s.slice(a, b);
};
export const cpLen = (s: string) => { let n = 0; for (const _ of s) n++; return n; };  // eslint-disable-line @typescript-eslint/no-unused-vars

export class SearchText {
  private words = new Map<string, number>();
  private asked = new Set<string>();
  constructor(readonly d: EdgeDict, private conv: Record<string, Converter>) {}

  /** how many words it knows (searchText() starts a new one past a limit) */
  size() { return this.words.size + this.asked.size; }

  /** a string transformed run by run of the code points Python's Unicode data knows; others kept as they are */
  private byRuns(s: string, f: (x: string) => string) {
    let out = "", run = "";
    for (const ch of s) {
      if (inRanges(this.d.unicode.assigned, ch.codePointAt(0)!)) run += ch;
      else { if (run) { out += f(run); run = ""; } out += ch; }
    }
    return out + (run ? f(run) : "");
  }
  /** str.lower() */
  lower(s: string) { return this.byRuns(s, x => x.toLowerCase()); }
  isalnum(ch: string) { return inRanges(this.d.unicode.alnum, ch.codePointAt(0)!); }

  /** the search form of a text: half-width, Simplified, lower case */
  norm(s: string | null | undefined): string {
    if (!s) return "";
    return this.lower(this.conv.hk2s.convert(this.byRuns(s, x => x.normalize("NFKC"))));
  }

  /** the forms a term may take in the displayed (original) text: as typed, Simplified, HK and standard Traditional */
  variants(term: string): string[] {
    const n = this.norm(term);
    return [...new Set([this.lower(term), n, this.conv.s2hk.convert(n), this.conv.s2t.convert(n)])].filter(Boolean);
  }

  /** read the dictionary words the given (already norm()ed) texts can contain: one query for all of them */
  async learn(sql: Sql, texts: string[]) {
    const c = new Set<string>();
    for (const t of texts) if (!this.asked.has(t)) { candidates(t, this.d.maxlen, c); this.asked.add(t); }
    const want = [...c].filter(w => !this.words.has(w));
    if (!want.length) return;
    // the same terms' words from the shared cache (per dictionary stamp), else one query
    const rows = await shared(`words:${this.d.stamp}:${want.sort().join("\u0001")}`, 600, async () =>
      (await sql`SELECT w, lp FROM search_words WHERE v = ${this.d.stamp} AND w = ANY(ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(want)}::text::jsonb)))`)
        .map(r => ({ w: r.w as string, lp: Number(r.lp) })));
    for (const r of rows) this.words.set(r.w, r.lp);
  }

  private keep(w: string) {
    return cpLen(w) <= MAX_LEXEME && [...w].some(ch => this.isalnum(ch));
  }

  /** the words of an already norm()ed text, in order (learn() it first) */
  tokens(s: string): string[] {
    if (!s) return [];
    if (!this.asked.has(s)) throw new Error("searchtext: tokens() of a text not learned");
    return cut(s, this.words, this.d.logtotal, this.d.maxlen).filter(w => this.keep(pyStrip(w)));
  }

  /** a norm()ed query term as a phrase tsquery literal; null when it has no word (only punctuation) */
  tsquery(term: string, titleOnly = false): string | null {
    const ws = this.tokens(term).map(pyStrip);
    if (!ws.length) return null;
    return ws.map(w => lex(w) + (titleOnly ? ":A" : "")).join(" <-> ");
  }
}

export const lex = (w: string) => "'" + w.replace(/\\/g, "\\\\").replace(/'/g, "''") + "'";

// Cloudflare starts a fresh isolate for most requests (2026-10-29: 6 of 7 searches in a row found nothing cached), and
// each trip from the Worker to Postgres costs ~60 ms, the 1.26 MB of OpenCC data 250-800 ms: what every isolate needs
// is kept in Workers KV (binding SEARCH_CACHE, shared by the site's and yyc-mcp's Workers; hot keys are read from the
// datacenter in a few ms). The Cache API would not do: it does nothing on workers.dev. Elsewhere (Node: the tests,
// scripts) straight from the database.
type KV = { get(k: string, t: "json"): Promise<unknown>; put(k: string, v: string, o: { expirationTtl: number }): Promise<void> };
async function kv(): Promise<KV | null> {
  try { return ((await import("cloudflare:workers")).env as unknown as { SEARCH_CACHE?: KV }).SEARCH_CACHE ?? null; }
  catch { return null; }
}
async function shared<T>(key: string, ttl: number, load: () => Promise<T>): Promise<T> {
  const c = await kv();
  if (!c) return load();
  // keys stay under KV's 512 bytes: long ones (the learned words' terms) by their SHA-256
  const k = key.length > 200 ? key.slice(0, 40) + ":" + [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key)))]
    .map(b => b.toString(16).padStart(2, "0")).join("") : key;
  try {
    const hit = await c.get(k, "json");
    if (hit != null) return hit as T;
  } catch { /* a KV error: read the database */ }
  const v = await load();
  const put = c.put(k, JSON.stringify(v), { expirationTtl: Math.max(60, ttl) }).catch(() => {});
  try { (await import("cloudflare:workers")).waitUntil(put); } catch { await put; }
  return v;
}

/** the published dictionary's description (kv search:edge), re-read every 30 s */
export async function edgeDict(sql: Sql): Promise<EdgeDict> {
  if (meta && Date.now() - meta.at < TTL_MS) return meta.v;
  const v = await shared("edge", TTL_MS / 1000, async () => {
    const rs = await sql`SELECT k, v FROM kv WHERE k IN ('search:edge', 'search:subs')`;
    const r = rs.find(x => x.k === "search:edge");
    if (!r) throw new Error("the search dictionary is not published yet (searchtext.publish_edge)");
    return { ...(r.v as EdgeDict), subs: rs.some(x => x.k === "search:subs" && x.v === true) };
  });
  meta = { at: Date.now(), v };
  return v;
}

/** a SearchText on the current dictionary (OpenCC's data loaded once per isolate and per published hash) */
export async function searchText(sql: Sql): Promise<SearchText> {
  const d = await edgeDict(sql);
  if (!occ || occ.hash !== d.opencc) {
    const data = await shared(`opencc:${d.opencc}`, 86400, async () => {  // by its hash: a new publish is a new key
      const [r] = await sql`SELECT v FROM kv WHERE k = 'search:opencc'`;
      if (!r) throw new Error("OpenCC data is not published yet (searchtext.publish_edge)");
      return r.v as OpenccData;
    });
    occ = { hash: data.hash, conv: converters(data) };
  }
  // one SearchText per dictionary and isolate: the words it has learned stay (a repeated term costs no query)
  if (!stCache || stCache.stamp !== d.stamp || stCache.occ !== occ.hash || stCache.st.size() > 50_000) {
    stCache = { stamp: d.stamp, occ: occ.hash, st: new SearchText(d, occ.conv) };
  }
  return stCache.st;
}
let stCache: { stamp: unknown; occ: string; st: SearchText } | null = null;

/** for tests: drop the cached dictionary description */
export const forget = () => { meta = null; stCache = null; };

/** tests/edge_parity.py searchtext: each term as Python's searchtext gives it (norm, variants, tokens, tsqueries) */
export async function parity(sql: Sql, terms: string[]) {
  const st = await searchText(sql);
  const ns = terms.map(t => st.norm(t));
  await st.learn(sql, ns);
  return terms.map((t, i) => ({ term: t, norm: ns[i], variants: st.variants(t), tokens: st.tokens(ns[i]),
    tsquery: st.tsquery(ns[i]), tsquery_a: st.tsquery(ns[i], true) }));
}
