// The wisers_* tools (Deyao, 2026-10-29: "update the mcp/api endpoint for that"): WiseSearch on YYC's WisersOne
// account through the relay (../../wisers/relay.py), inside the cluster: Service wisers:8899 with the caller token
// WISERS_INTERNAL_TOKEN (the relay's WISERS_INTERNAL_TOKENS; NetworkPolicy wisers-ingress admits only yyc-mcp and
// cloudflared), the person it acts for in X-YYC-User, so the relay's logs name them. Read-only like everything here: the relay relays no write, no tracking and no e-mail.
// Searches and Wisers document opens spend the account's quota, so those tools require a `reason`, the relay caps
// AI callers per person and hour, and the same search within a day is answered from memory (free).
import { fetch as relayFetch } from "undici";
import type { Env } from "./env";
import { internal } from "./node/egress";

export class WisersError extends Error {}
export interface Ctx { env: Env; who: string }
type Row = Record<string, unknown>;
type Schema = Record<string, unknown> & { type?: string; properties?: Record<string, Schema>; required?: string[] };

const DATE: Schema = { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$", description: "YYYY-MM-DD (Hong Kong)" };
const REASON: Schema = { type: "string", maxLength: 200, description: "why, in a few words (logged against the person; Wisers quota is shared)" };
const JOB: Schema = { type: "string", pattern: "^[\\w-]{1,40}$", description: "job_id from wisers_search" };
const list = (d: string): Schema => ({ type: "array", items: { type: "string" }, description: d });
const QUOTA = " Costs WisersOne quota on the shared account (logged against you; AI callers are capped per hour).";

async function relay(ctx: Ctx, path: string, secs = 60): Promise<Row> {
  const { env } = ctx;
  if (!env.WISERS_API || !env.WISERS_INTERNAL_TOKEN) throw new WisersError("WiseSearch is not configured on this server");
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), secs * 1000);
  try {
    const r = await relayFetch(env.WISERS_API + path, { signal: ctl.signal, dispatcher: internal, headers: { Accept: "application/json",
      Authorization: "Bearer " + env.WISERS_INTERNAL_TOKEN, "X-YYC-User": ctx.who } });
    let d: Row;
    try { d = await r.json() as Row; } catch { throw new WisersError(`the WiseSearch relay answered HTTP ${r.status} (not JSON)`); }
    if (!r.ok || d.error) throw new WisersError(String(d.error || `HTTP ${r.status}`));
    return d;
  } catch (e) {
    if (e instanceof WisersError) throw e;
    throw new WisersError(ctl.signal.aborted ? `the WiseSearch relay did not answer within ${secs} s` : `the WiseSearch relay could not be reached: ${(e as Error).message}`);
  } finally { clearTimeout(t); }
}

const qs = (o: Row) => new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)])).toString();
const text = (parts: unknown) => Array.isArray(parts) ? parts.map(p => (Array.isArray(p) ? (p[1] ? `«${p[0]}»` : p[0]) : "")).join("") : "";
const plain = (html: string) => html.replace(/<\/p>|<br\s*\/?>/gi, "\n").replace(/<mark>/g, "«").replace(/<\/mark>/g, "»").replace(/<[^>]+>/g, "")
  .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"").replace(/&#39;/g, "'").replace(/\n{3,}/g, "\n\n").trim();
const READER = "https://newswatch.yyc-internal.com/app/wisers/doc/";

/** a search's items, concise (hits marked «like this»), each with its reader page */
function items(res: Row, job: string): Row[] {
  return ((res.items || []) as Row[]).map((x, i) => Object.fromEntries(Object.entries({
    doc_id: x.doc_id, headline: x.headline, source: x.source, kind: x.kind, date: x.date, page: x.page, section: x.section,
    column: x.column, author: x.author, words: x.words, circulation: x.circulation, url: x.link, excerpt: text(x.excerpt),
    has_print_page: (x.has as Row)?.img || undefined, similar_count: x.similar, engagement: Object.keys((x.engagement as Row) || {}).length ? x.engagement : undefined,
    reader_url: `${READER}${encodeURIComponent(String(x.doc_id))}?job=${job}&i=${i}` }).filter(([, v]) => v != null && v !== "")));
}

function answer(a: Row): Row {
  const job = a.job as Row, res = a.result as Row | null, f = a.failure as Row | null;
  if (job.state === "running") return { job_id: job.id, state: "running", elapsed_s: job.elapsed, note: "Wisers is still searching: call wisers_job with job_id (free) until it ends" };
  if (job.state === "err") throw new WisersError(`Wisers search failed: ${f?.message}${f?.charged && f.charged !== "no" ? ` (quota may have been used: ${f.charged})` : ""}`);
  return { job_id: job.id, state: "ok", from_memory: !!job.cached, total: Number(res?.total ?? 0), total_before_merging: Number(res?.total_docs ?? 0) || undefined,
    page: res?.page, page_size: res?.page_size, items: items(res || {}, String(job.id)) };
}

async function waitJob(ctx: Ctx, a: Row, budgetMs = 40_000): Promise<Row> {
  const t0 = Date.now();
  while ((a.job as Row).state === "running" && Date.now() - t0 < budgetMs) a = await relay(ctx, `job/${(a.job as Row).id}?wait=15`, 45);
  return answer(a);
}

export const WISERS_TOOLS = [
  { name: "wisers_search", title: "WiseSearch: search Wisers' news archive",
    description: "Search Wisers (慧科 WisersOne), the archive of Hong Kong, Mainland, Taiwan and Macau newspapers (print editions with page numbers), magazines, news sites, social media, forums and blogs, through the shared account. Query syntax as WisersOne: words (space = AND), OR, NOT, \"exact phrase\", ( ). Default period: the last 7 days. Returns up to page_size items (headline, outlet, page/section for print, date, words, the excerpt with hits marked «like this», has_print_page when the newspaper page image exists) and job_id for wisers_facets / wisers_doc. A big search can take minutes: if state is running, call wisers_job." + QUOTA + " The same search within 24 h is answered from memory (free).",
    schema: { type: "object", required: ["reason"], properties: {
      query: { type: "string", description: "keywords; or use all/any/none instead (logic mode)" },
      all: list("logic mode: every word must appear"), any: list("logic mode: at least one must appear"), none: list("logic mode: none may appear"),
      period: { type: "string", enum: ["today", "yesterday", "2", "3", "7", "30", "182", "365", "year-1", "custom"], description: "days back, or custom with from/to" },
      from: DATE, to: DATE, in: { type: "string", enum: ["all", "title", "content"], description: "headline, text or both (default)" },
      within: { type: "string", enum: ["document", "paragraph", "sentence"], description: "how near the words must be" },
      sort: { type: "string", enum: ["date", "relevance"] },
      media_types: list("WisersOne media type values from wisers_options (e.g. print, web, social)"), categories: list("media category values"),
      regions: list("media region values (e.g. hk, cn, tw, mc)"), tab: { type: "string", description: "one result tab (media type): print, web, social, wlforum, wlblog" },
      language: { type: "string", enum: ["all", "cn", "en"], description: "publication language" },
      include_media: list("media ids from wisers_media to search only in"), exclude_media: list("media ids to leave out"),
      author: { type: "string" }, column: { type: "string" }, section: { type: "string" },
      synonyms: { type: "boolean", default: true, description: "also search WisersOne's similar words" },
      merge_similar: { type: "boolean", default: true }, page: { type: "integer", minimum: 1, default: 1 },
      page_size: { type: "integer", enum: [20, 30, 50], default: 20 }, reason: REASON } },
    run: async (a: Row, ctx: Ctx) => {
      const p: Row = { q: a.query, and: (a.all as string[] | undefined)?.join(" "), or: (a.any as string[] | undefined)?.join(" "),
        nor: (a.none as string[] | undefined)?.join(" "), period: a.period || (a.from ? "custom" : "7"), from: a.from, to: a.to,
        range: a.in && a.in !== "all" ? a.in : undefined, within: a.within, sort: a.sort === "relevance" ? "DESC:score" : undefined,
        mt: (a.media_types as string[] | undefined)?.join(","), mc: (a.categories as string[] | undefined)?.join(","),
        mr: (a.regions as string[] | undefined)?.join(","), tab: a.tab, lang: a.language, inc: (a.include_media as string[] | undefined)?.join(","),
        exc: (a.exclude_media as string[] | undefined)?.join(","), author: a.author, column: a.column, section: a.section,
        similar: a.synonyms === false ? "0" : undefined, cluster: a.merge_similar === false ? "0" : undefined, page: a.page,
        size: a.page_size || 20, reason: a.reason };
      if (!p.q && !p.and && !p.or) throw new WisersError("give query, or all / any");
      return waitJob(ctx, await relay(ctx, "search?" + qs(p), 45));
    } },
  { name: "wisers_job", title: "WiseSearch: a running search",
    description: "The state or result of a wisers_search that was still running (free: no new search).",
    schema: { type: "object", required: ["job_id"], properties: { job_id: JOB } },
    run: async (a: Row, ctx: Ctx) => waitJob(ctx, await relay(ctx, `job/${a.job_id}?wait=15`, 45)) },
  { name: "wisers_doc", title: "WiseSearch: read documents in full",
    description: "The full text of Wisers documents (doc_id from wisers_search), with the heading (outlet, page, section, words, date, circulation), the other news on the same print page, and how many page images exist. The monitoring system's own corpus answers first when it has the article (free; from = corpus); otherwise Wisers opens it (from = wisers)." + QUOTA,
    schema: { type: "object", required: ["doc_ids", "reason"], properties: {
      doc_ids: { type: "array", items: { type: "string", pattern: "^[\\w-]{1,80}$" }, minItems: 1, maxItems: 10 }, job_id: JOB, reason: REASON } },
    run: async (a: Row, ctx: Ctx) => ({ documents: await Promise.all((a.doc_ids as string[]).map(async id => {
      try {
        const d = await relay(ctx, `doc/${encodeURIComponent(id)}?${qs({ job: a.job_id, reason: a.reason })}`, 120);
        const h = (d.heading || {}) as Row;
        return Object.fromEntries(Object.entries({ doc_id: id, from: d.from, headline: h.headline, source: h.source, page: h.page, section: h.section,
          date: h.date, words: h.words, circulation: h.circulation, url: h.link || (d.post as Row)?.url, quota: (d.quota as Row)?.message,
          redirect_url: d.redirect, text: plain(String(d.html || "")), print_page_images: (d.images as Row)?.count ?? ((d.images as Row)?.has ? "yes (count unknown)" : undefined),
          same_page: ((d.same_page || []) as Row[]).map(x => ({ doc_id: x.docId, headline: x.headline })),
          reader_url: READER + encodeURIComponent(id) }).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length)));
      } catch (e) { return { doc_id: id, error: (e as Error).message }; }
    })) }) },
  { name: "wisers_facets", title: "WiseSearch: break down a search",
    description: "Views of a wisers_search's results, as WisersOne's result page shows them: counts = how many per media type (the result tabs); media = outlets and regions; match = keyword-match groups; labels = a label tree's counts (type: subject|industry|group|brand, id: 0 for the top); hot = hot keywords; hot_negative = negative hot words; similar = the articles merged into one result (doc_id + cluster_key from the item); excerpt = one item's excerpt; merge = similar-article counts of the page. Uses the search's own criteria only. Each call is cached per search; whether Wisers counts these against quota is unknown, so they are logged.",
    schema: { type: "object", required: ["job_id", "kind"], properties: { job_id: JOB,
      kind: { type: "string", enum: ["counts", "media", "match", "labels", "hot", "hot_negative", "similar", "excerpt", "merge"] },
      type: { type: "string", enum: ["subject", "industry", "group", "brand"] }, id: { type: "string" }, doc_id: { type: "string" }, cluster_key: { type: "string" } } },
    run: async (a: Row, ctx: Ctx) => (await relay(ctx, "derived?" + qs({ job: a.job_id, kind: a.kind, type: a.type, id: a.id ?? (a.kind === "labels" ? "0" : undefined),
      doc: a.doc_id, key: a.cluster_key }), 100)).data },
  { name: "wisers_options", title: "WiseSearch: the search options",
    description: "WiseSearch's option lists for wisers_search (free): media types, categories and regions with their values, negative-news categories, hot picks, the account's feature switches (config) and default search options.",
    schema: { type: "object", properties: { config: { type: "boolean", description: "the account's feature switches instead" } } },
    run: async (a: Row, ctx: Ctx) => a.config ? relay(ctx, "config", 100) : relay(ctx, "options", 100) },
  { name: "wisers_labels", title: "WiseSearch: label trees",
    description: "WisersOne's label trees (subjects, listed companies, industries, groups/companies, brands/products, education), free: a level (group + id, 0 = the top; listed companies: group 'group', id '35'; education: group 'group', id '101'), or labels whose name matches q.",
    schema: { type: "object", required: ["group"], properties: { group: { type: "string", enum: ["subject", "industry", "group", "brand"] },
      id: { type: "string", default: "0" }, q: { type: "string", maxLength: 50 } } },
    run: async (a: Row, ctx: Ctx) => a.q ? relay(ctx, "labels/suggest?" + qs({ group: a.group, q: a.q }), 100) : relay(ctx, "labels?" + qs({ group: a.group, id: a.id || "0" }), 100) },
  { name: "wisers_media", title: "WiseSearch: find media",
    description: "WisersOne's media (newspapers, sites, accounts), free: find by name (q), names of ids, or one publication's profile (id).",
    schema: { type: "object", properties: { q: { type: "string", maxLength: 80 }, ids: list("media ids"), id: { type: "string" }, page: { type: "integer", minimum: 0 } } },
    run: async (a: Row, ctx: Ctx) => a.id ? relay(ctx, `media/${encodeURIComponent(String(a.id))}`, 100)
      : a.ids ? relay(ctx, "media/names?" + qs({ ids: (a.ids as string[]).join(",") }), 100)
      : relay(ctx, "media?" + qs({ q: a.q, page: a.page }), 100) },
  { name: "wisers_log", title: "WiseSearch: the usage log",
    description: "Every Wisers search (kind searches: who, why, query, results, outcome, whether it cost quota; today's and this month's counts) or every other Wisers call (kind calls: documents with where the text came from, images, downloads, facets). Free.",
    schema: { type: "object", properties: { kind: { type: "string", enum: ["searches", "calls"], default: "searches" }, limit: { type: "integer", minimum: 1, maximum: 500, default: 50 } } },
    run: async (a: Row, ctx: Ctx) => {
      const n = Number(a.limit || 50);
      if (a.kind === "calls") return { rows: ((await relay(ctx, "log/calls", 60)).rows as Row[]).slice(0, n) };
      const d = await relay(ctx, "log", 60);
      return { summary: d.summary, rows: (d.rows as Row[]).slice(0, n) };
    } },
] as const;

export const WISERS_NAMES = WISERS_TOOLS.map(t => t.name);
