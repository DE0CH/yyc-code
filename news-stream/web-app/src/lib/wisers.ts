// WiseSearch's client side (the pages in routes/-wisers/): the relay's JSON API at /app/wisers/api/ (../../wisers/
// relay.py; yyc-edge sends that path to the cluster), its answers' types, and the search URL's parameters
// (relay query.criteria() reads the same keys). Every search and every document Wisers opens costs YYC's account
// quota, so nothing here repeats one by itself.

export const API = "/app/wisers/api/";

/** one read of the relay's API, aborted after `secs` (the relay answers every request within 20 s; a lookup that needs
 *  a WisersOne login within 90 s) */
export async function getJSON<T>(path: string, secs = 100, init?: RequestInit): Promise<T> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), secs * 1000);
  let r: Response;
  try {
    r = await fetch(API + path, { credentials: "same-origin", headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
      signal: ctl.signal, ...init });
  } catch (e) {
    throw new Error(ctl.signal.aborted ? `轉接程式 ${secs} 秒沒有回應。` : `連線中斷（${(e as Error).message}）。`);
  } finally { clearTimeout(t); }
  let d: { error?: string } & T;
  try { d = await r.json(); } catch { throw new Error(`伺服器回應無法讀取（HTTP ${r.status}）`); }
  if (!r.ok || d.error) throw Object.assign(new Error(d.error || `HTTP ${r.status}`), { status: r.status, body: d });
  return d;
}
export const NO_RETRY = { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } as const;

// ---------------------------------------------------------------- the search URL
export const KEYS = ["q", "and", "or", "nand", "nor", "range", "within", "period", "from", "to", "sort", "mt", "mc", "mr", "tab",
  "lang", "doclang", "inc", "exc", "author", "column", "section", "similar", "neg", "hot", "db", "lab_subject", "lab_listed",
  "lab_industry", "lab_group", "lab_brand", "lab_edu", "within_kw", "f_labels", "f_region", "f_pubregion", "f_important",
  "f_match", "f_sensitive", "cluster", "summary", "page", "size"] as const;
export type WParams = Partial<Record<(typeof KEYS)[number], string>>;
const str = (v: unknown) => (typeof v === "string" ? v : typeof v === "number" ? String(v) : undefined);
export const validate = (s: Record<string, unknown>): WParams =>
  Object.fromEntries(KEYS.map(k => [k, str(s[k])]).filter(([, v]) => v !== undefined && v !== ""));
/** the search's query string: fixed key order, empty values left out */
export function qsOf(s: WParams): string {
  const u = new URLSearchParams();
  for (const k of KEYS) { const v = s[k]; if (v != null && v !== "") u.set(k, v); }
  return u.toString();
}
/** the same search without what only narrows or pages its results (the result filter, search within, the tab, page) */
export const baseOf = (s: WParams): WParams => Object.fromEntries(Object.entries(s).filter(([k]) =>
  !k.startsWith("f_") && !["within_kw", "tab", "page"].includes(k))) as WParams;

export const PERIODS: [string, string][] = [["today", "今日"], ["yesterday", "昨日"], ["2", "最近 2 日"], ["3", "最近 3 日"],
  ["7", "最近 1 星期"], ["30", "最近 1 個月"], ["182", "最近 6 個月"], ["365", "最近 1 年"], ["year-1", "去年"], ["custom", "自訂日期"]];
export const SORTS: [string, string][] = [["DESC:date", "最新"], ["DESC:score", "最相關"], ["tracking", "追蹤（負面）"]];
export const WITHIN: [string, string][] = [["document", "全文"], ["paragraph", "同一段落"], ["sentence", "同一句"]];
export const LANGS: [string, string][] = [["all", "全部"], ["cn", "中文媒體"], ["en", "英文媒體"]];
export const SIZES = ["20", "30", "50"];
export const TREES = [
  { key: "subject", title: "主題", group: "subject", root: "0" },
  { key: "listed", title: "上市公司", group: "group", root: "35" },
  { key: "industry", title: "行業", group: "industry", root: "0" },
  { key: "group", title: "集團／公司", group: "group", root: "0" },
  { key: "brand", title: "品牌／產品", group: "brand", root: "0" },
  { key: "edu", title: "教育", group: "group", root: "101" }] as const;
// WisersOne's result tabs (folderListing.tab.<type>), in its order
export const TABS: [string, string][] = [["all", "全部"], ["print", "報刊"], ["web", "網站"], ["social", "社交媒體"],
  ["wlforum", "論壇"], ["wlblog", "博客"]];
export const KIND_ZH: Record<string, string> = { print: "報刊", "web-site": "網站", app: "新聞 App", social: "社交", wlforum: "論壇",
  wlblog: "博客", weixin: "微信公眾號", "micro-blog": "微博", rednote: "小紅書", tiktok: "抖音", facebook: "Facebook",
  instagram: "Instagram", youtube: "YouTube", bilibili: "B站", kuaishou: "快手" };

// ---------------------------------------------------------------- answers
export type Opt = { text: string; value: string; items?: Opt[] };
export type Options = { media: { typeList?: Opt[]; categoryList?: Opt[]; regionList?: Opt[] }; negative: Opt[];
  hotpicks: unknown; databases: unknown; defaults: unknown; trees: { key: string; title: string; group: string; root: string }[] };
export type Label = { id: string; node: string; type: string; name: string; kids: number };
export type Chosen = { id: string; type: string; name: string };
export type Has = { text: boolean; img: boolean; website: boolean; video: boolean; audio: boolean; pdf: boolean };
export type Item = { doc_id: string; headline: string; source: string; date: string; author: string; kind: string; pub_type: string;
  pub_id?: string; pub_code?: string; page: string; section: string; column: string; words: number | null; circulation: number | null;
  link: string; excerpt: [string, boolean][]; has: Has; cluster_key?: string; similar: number | null;
  engagement: Record<string, number | null>; translated: string | null; raw: Record<string, unknown> };
export type Result = { at: string; crit: Record<string, unknown>; raw: string; page_size?: number; total?: string; total_docs?: string;
  doc_count?: string; max_docs?: string; media_type?: string; media_count?: unknown;
  progress?: { progress?: string; percent?: string; finished?: boolean }; criteria_id?: string; items: Item[]; page?: number; cached?: boolean };
export type JobInfo = { id: string; state: "running" | "ok" | "err"; cached: boolean; created: string; elapsed: number; queued: boolean;
  wisers_secs?: number | null; pending_jobs?: number; searched_at?: string };
export type Failure = { message: string; outcome: string; charged: "yes" | "no" | "maybe" };
export type JobAnswer = { job: JobInfo; result: Result | null; failure: Failure | null };
export type Quota = { code: string; blocking: boolean; message: string };
export type Doc = { doc_id: string; heading: Item | null; style: string; job: string | null; index: number | null;
  same_page: { docId: string; headline: string; pageNo?: string }[] | null; images: { has: boolean; count: number | null } | null;
  redirect: string | null; quota: Quota | null; from: "corpus" | "wisers" | null; html: string;
  post: { id: number; url: string; website: string; match: string } | null; meta: Record<string, unknown> | null };
export type Download = { id: string; state: "running" | "ok" | "err"; file?: string; error?: string; format: string; content: string;
  bytes?: number; quota?: Quota };

/** the counts of WisersOne's tabs from search/getMediaTypeCount's answer, whatever its exact shape */
export function tabCounts(d: unknown): Record<string, number> {
  const src = (d && typeof d === "object" && "mediaCount" in (d as object) ? (d as { mediaCount: unknown }).mediaCount : d) as unknown;
  const out: Record<string, number> = {};
  if (Array.isArray(src)) for (const x of src as { key?: string; value?: string; type?: string; count?: unknown }[]) {
    const k = x.key || x.value || x.type; if (k != null && x.count != null) out[k] = Number(x.count);
  } else if (src && typeof src === "object") for (const [k, v] of Object.entries(src as object)) {
    const n = typeof v === "object" && v ? Number((v as { count?: unknown }).count) : Number(v);
    if (!Number.isNaN(n)) out[k] = n;
  }
  return out;
}

/** a list of {text, value, count} rows from a facet answer, whatever its exact shape (WisersOne's filter calls) */
export function rowsOf(d: unknown): { text: string; value: string; count: number | null }[] {
  const pick = (x: unknown): unknown[] | null => {
    if (Array.isArray(x)) return x;
    if (x && typeof x === "object") for (const v of Object.values(x as object)) { const r = pick(v); if (r && r.length) return r; }
    return null;
  };
  return (pick(d) || []).filter(x => x && typeof x === "object").map(x => {
    const o = x as Record<string, unknown>;
    const text = String(o.text ?? o.name_hk ?? o.name ?? o.labelName ?? o.keyword ?? o.word ?? o.value ?? "");
    const value = String(o.value ?? o.labelId ?? o.id ?? o.keyword ?? o.word ?? text);
    const c = o.count ?? o.docCount ?? o.num ?? o.weight;
    return { text, value, count: c == null ? null : Number(c) };
  }).filter(r => r.text);
}

export const nf = (n: number | null | undefined) => (n == null || Number.isNaN(n) ? "" : n.toLocaleString("en-US"));

/** getUserCustomConfig's answer ({config: [{key, value}]}) as one flat object of the account's switches */
export function configOf(d: unknown): Record<string, string> {
  const list = (d && typeof d === "object" && Array.isArray((d as { config?: unknown }).config)) ? (d as { config: { key: string; value: unknown }[] }).config : [];
  return Object.fromEntries(list.map(x => [x.key, String(x.value)]));
}
