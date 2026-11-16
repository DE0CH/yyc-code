// /app/wisers/search?…: one WiseSearch search, its own URL and history entry, laid out as WisersOne's result page:
// the criteria with 修改搜尋 and 重新搜尋, the media-type tabs with their counts (a tab is a new search), the toolbar
// (select, 下載, 列印, 逐篇瀏覽, sort, merge similar, page size, summaries, list / cards / grid), each result row (number,
// checkbox, the content icons — 文字, 印刷版 image, 網頁, video, audio — the headline to the reader, outlet, page,
// section, column, author, date, words, circulation, engagement, the excerpt with hits, 相似 n, 原文), the pager, and the
// sidebar (篩選: media, keyword match, label trees, search within results; 熱門關鍵字), each loaded when opened.
// A search is a job on the relay (Deyao, 2026-10-20): api/search answers within 20 s and the page reads api/job/<id>
// until it ends. Every search and every derived call may cost quota, so the page never repeats one by itself: a lost
// connection reads the same job again (free), and only 再搜尋一次 sends a new one.
import { Fragment, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Wait } from "../../lib/Wait";
import { dt } from "../../lib/dates";
import { API, KIND_ZH, NO_RETRY, PERIODS, SIZES, SORTS, TABS, TREES, WITHIN, baseOf, getJSON, nf, qsOf, rowsOf, tabCounts,
  type Download, type Failure, type Item, type JobAnswer, type JobInfo, type Result, type WParams } from "../../lib/wisers";
import { rememberForm } from "./Form";
import { CHARGED, Frame, I, Marked, Seg, kept, secs, useKept } from "./ui";

/** the search ended without a result (Wisers' error, a timeout, a broken connection): shown with what it may have cost */
class SearchFailed extends Error {
  constructor(public f: Failure, public job: JobInfo) { super(f.message); this.name = "SearchFailed"; }
}
/** the page lost the relay while the job ran: reading the same job again costs nothing */
class Lost extends Error {
  constructor(public jobId: string, why: string) { super(why); this.name = "Lost"; }
}
type Progress = { job?: JobInfo; at: number; trouble?: string };
const progress = new Map<string, Progress>();
const subs = new Set<() => void>();
const report = (qs: string, p: Progress) => { progress.set(qs, p); subs.forEach(f => f()); };
const useProgress = (qs: string) => useSyncExternalStore(cb => { subs.add(cb); return () => { subs.delete(cb); }; },
  () => progress.get(qs), () => undefined);
const resume = new Map<string, string>();  // query string -> the job to read again (after Lost), instead of searching
const POLL_TRIES = 6;

async function runSearch(qs: string, fresh: boolean): Promise<JobAnswer> {
  let a: JobAnswer;
  const again = resume.get(qs);
  if (again) a = await getJSON<JobAnswer>(`job/${again}?wait=0`, 45);
  else a = await getJSON<JobAnswer>("search?" + qs + (fresh ? "&fresh=1" : ""), 45);  // not repeated by itself
  resume.delete(qs);
  let fails = 0;
  while (a.job.state === "running") {
    report(qs, { job: a.job, at: Date.now() });
    try {
      a = await getJSON<JobAnswer>(`job/${a.job.id}?wait=20`, 45);
      fails = 0;
    } catch (e) {
      if (++fails >= POLL_TRIES) { resume.set(qs, a.job.id); throw new Lost(a.job.id, (e as Error).message); }
      report(qs, { job: a.job, at: Date.now(), trouble: (e as Error).message });
      await new Promise(r => setTimeout(r, 3000));
    }
  }
  progress.delete(qs);
  if (a.job.state === "err" || !a.result) throw new SearchFailed(a.failure || { message: "Wisers 沒有回應。", outcome: "error", charged: "maybe" }, a.job);
  return a;
}

/** the search in flight: a ruled progress strip and, in words, how long Wisers has been at it (no motion) */
function Running({ qs }: { qs: string }) {
  const p = useProgress(qs);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 1000); return () => clearInterval(t); }, []);
  const el = p?.job ? p.job.elapsed + (Date.now() - p.at) / 1000 : 0;
  const steps = Math.min(10, Math.floor(el / 6));
  return (
    <div className="wwait" role="status" data-wait="busy" data-job={p?.job?.id || ""}>
      <div className="strip" aria-hidden="true">{Array.from({ length: 10 }, (_, i) => <span key={i} className={i < steps ? "on" : ""} />)}</div>
      <p>{!p?.job ? "正在把搜尋送到轉接程式…" : p.job.queued ? `排隊中：轉接程式正處理另一個請求或正在登入 WisersOne（${secs(el)}）` : `Wisers 搜尋中（已 ${secs(el)}）`}</p>
      <p className="hint">大型搜尋可能要一至兩分鐘。可以離開這頁再回來；重新整理不會再搜尋一次，也不會再用額度。</p>
      {p?.trouble && <p className="hint">與轉接程式的連線中斷（{p.trouble}），正在重新連線；搜尋仍在進行。</p>}
    </div>
  );
}

/** the search's criteria as label / value plates */
function Criteria({ s }: { s: WParams }) {
  const per = s.period === "custom" ? `${s.from || "…"} 至 ${s.to || "…"}` : (PERIODS.find(([v]) => v === (s.period || "7")) || ["", s.period])[1];
  const labs = TREES.map(t => { try { return (JSON.parse(s[`lab_${t.key}` as keyof WParams] || "[]") as { name: string }[]).map(x => x.name); } catch { return []; } }).flat();
  const kw = s.q || [s.and && `全部：${s.and}`, s.or && `任何：${s.or}`, s.nand && `不含全部：${s.nand}`, s.nor && `不含任何：${s.nor}`].filter(Boolean).join("　");
  const rows: [string, string | undefined][] = [["關鍵字", kw], ["日期", per], ["範圍", s.range === "title" ? "只搜標題" : s.range === "content" ? "只搜內文" : ""],
    ["距離", s.within ? (WITHIN.find(([v]) => v === s.within) || ["", s.within])[1] : ""], ["排序", s.sort ? (SORTS.find(([v]) => v === s.sort) || ["", s.sort])[1] : ""],
    ["媒體", [s.mt, s.mc, s.mr].filter(Boolean).join("、")], ["指定媒體", [s.inc && `包括 ${s.inc.split(",").length}`, s.exc && `排除 ${s.exc.split(",").length}`].filter(Boolean).join("，")],
    ["作者", s.author], ["專欄", s.column], ["版面", s.section], ["負面", s.neg], ["標籤", labs.join("、")], ["相似字詞", s.similar === "0" ? "不搜尋" : ""]];
  return <div className="plates">{rows.filter(([, v]) => v).map(([k, v]) => <span className="plate" key={k}><span>{k}</span><b>{v}</b></span>)}</div>;
}

const selected = (job: string) => (kept.get("sel:" + job) || []) as string[];

/** one result row (all three views draw the same pieces) */
function Row({ x, n, job, checked, check, view, s }: { x: Item; n: number; job: string; checked: boolean; check: (on: boolean) => void;
  view: string; s: WParams }) {
  const [sim, setSim] = useState(false);
  const read = (style: string) => ({ to: "/app/wisers/doc/$id" as const, params: { id: x.doc_id }, search: { job, i: String(n - 1), style } });
  const icons = (["text", "img", "website", "video", "audio"] as const).filter(k => x.has[k]);  // WisersOne's row icons (SimilarItemPanel)
  const ICON_ZH: Record<string, string> = { text: "文字", img: "印刷版頁面", website: "網頁", video: "影片", audio: "聲音" };
  const meta = [x.source, x.page && `${x.page} 版`, x.section, x.column, x.author && x.author !== x.source ? x.author : ""].filter(Boolean);
  return (
    <article className={"wrow " + view} data-doc={x.doc_id}>
      <div className="lead">
        <span className="n num">{n}</span>
        <input type="checkbox" checked={checked} onChange={e => check(e.target.checked)} aria-label={`選擇第 ${n} 篇`} />
      </div>
      <div className="body">
        <h2><Link {...read("all")}>{x.headline || "（無標題）"}</Link></h2>
        <div className="icons">{icons.map(k => (
          <Link key={k} {...read(k === "website" ? "web" : k)} className={"ico ico-" + k} title={ICON_ZH[k]}><I n={k} label={ICON_ZH[k]} /></Link>
        ))}<span className="kind">{KIND_ZH[x.kind] || x.kind}</span></div>
        <p className="meta">{meta.map((t, j) => <span key={j}>{t}</span>)}<span className="num">{dt.dateTime(x.date)}</span>
          {x.words != null && <span className="num">{nf(x.words)} 字</span>}
          {x.circulation != null && <span className="num">發行 {nf(x.circulation)}</span>}
          {Object.entries(x.engagement).map(([k, v]) => <span className="num" key={k}>{({ view_count: "瀏覽", commentCount: "留言", likeCount: "讚", forwardCount: "轉發" } as Record<string, string>)[k] || k} {nf(v)}</span>)}
        </p>
        {s.summary !== "0" && x.excerpt.length > 0 && <p className="ex"><Marked parts={x.excerpt} /></p>}
        <div className="foot">
          {x.link && <a href={x.link} target="_blank" rel="noopener noreferrer" className="lk"><I n="link" />原文</a>}
          {(x.similar || 0) > 0 && <button type="button" className="lk" aria-expanded={sim} onClick={() => setSim(!sim)}>相似 {x.similar}<I n={sim ? "up" : "down"} /></button>}
          <details className="more"><summary>其他欄位</summary>
            <dl className="kv">{Object.entries(x.raw).filter(([, v]) => v != null && v !== "" && typeof v !== "object").map(([k, v]) => (
              <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>
            ))}</dl>
          </details>
        </div>
        {sim && <Similar job={job} x={x} />}
      </div>
    </article>
  );
}

/** the similar articles merged into a row (search/getSimilarDocuments), as ruled rows under it */
function Similar({ job, x }: { job: string; x: Item }) {
  const q = useQuery({ queryKey: ["wisers", "similar", job, x.doc_id], queryFn: () => getJSON<{ data: { items?: Record<string, unknown>[] } }>(
    `derived?job=${job}&kind=similar&doc=${encodeURIComponent(x.doc_id)}&key=${encodeURIComponent(x.cluster_key || "")}`), staleTime: Infinity, ...NO_RETRY });
  if (q.isPending) return <Wait className="note" what="載入相似文章" />;
  if (q.isError) return <Wait className="note" what="載入相似文章" failed onRetry={() => q.refetch()} />;
  const items = (q.data.data?.items || []) as Record<string, unknown>[];
  return <ul className="branch">{items.map((y, i) => (
    <li key={i}>
      <Link to="/app/wisers/doc/$id" params={{ id: String(y.docId) }} search={{ job }}>{String(y.headline || "")}</Link>
      <span className="meta">{String(y.source || "")} <span className="num">{dt.dateTime(String(y.publishDate || ""))}</span></span></li>
  ))}</ul>;
}

/** the media-type tabs with their counts (search/getMediaTypeCount for the job); a tab is a new search */
function Tabs({ job, s }: { job: string; s: WParams }) {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["wisers", "counts", job], queryFn: () => getJSON<{ data: unknown }>(`derived?job=${job}&kind=counts`),
    staleTime: Infinity, ...NO_RETRY });
  const counts = tabCounts(q.data?.data);
  const cur = s.tab || "all";
  return (
    <nav className="tabs" aria-label="媒體種類">
      {TABS.map(([v, t]) => (
        <button type="button" key={v} className={cur === v ? "on" : ""} aria-current={cur === v ? "page" : undefined}
          onClick={() => cur !== v && navigate({ to: "/app/wisers/search", search: { ...s, tab: v === "all" ? undefined : v, page: undefined } as never })}>
          <span>{t}</span><span className="num">{q.isPending ? "…" : counts[v] != null ? nf(counts[v]) : ""}</span>
        </button>
      ))}
      {q.isError && <span className="hint">未能載入各類數目</span>}
    </nav>
  );
}

/** a facet list (media, keyword match, a label tree, hot keywords): checkbox rows with counts, 10 at a time */
function FacetList({ rows, sel, set, more = 10 }: { rows: { text: string; value: string; count: number | null }[]; sel: string[];
  set: (v: string[]) => void; more?: number }) {
  const [n, setN] = useState(more);
  return <>
    <ul className="facet">{rows.slice(0, n).map(r => (
      <li key={r.value}><label className="cb"><input type="checkbox" checked={sel.includes(r.value)}
        onChange={() => set(sel.includes(r.value) ? sel.filter(v => v !== r.value) : sel.concat(r.value))} />{r.text}</label>
        {r.count != null && <span className="num">{nf(r.count)}</span>}</li>
    ))}</ul>
    {rows.length > n && <button type="button" className="lk" onClick={() => setN(n + more)}>再顯示 {Math.min(more, rows.length - n)} 項</button>}
  </>;
}

function Derived({ job, kind, extra = "", children }: { job: string; kind: string; extra?: string;
  children: (d: unknown) => ReactNode }) {
  const q = useQuery({ queryKey: ["wisers", "derived", job, kind, extra], queryFn: () => getJSON<{ data: unknown }>(`derived?job=${job}&kind=${kind}${extra}`),
    staleTime: Infinity, ...NO_RETRY });
  if (q.isPending) return <Wait className="note" what="載入" />;
  if (q.isError) return <p className="hint">未能載入：{(q.error as Error).message}</p>;
  return <>{children(q.data.data)}</>;
}

/** WisersOne's left sidebar: 篩選 (narrow this search: a new search) and 熱門關鍵字 */
function Sidebar({ job, s }: { job: string; s: WParams }) {
  const navigate = useNavigate();
  const [tab, setTab] = useKept<"filter" | "hot">("side-tab", "filter");
  const [open, setOpen] = useKept<Record<string, boolean>>(`side:${job}`, {});
  const [within, setWithin] = useState("");
  const [region, setRegion] = useState<string[]>((s.f_region || "").split(",").filter(Boolean));
  const [labs, setLabs] = useState<string[]>(() => { try { return (JSON.parse(s.f_labels || "[]") as { labelId: string }[]).map(x => x.labelId); } catch { return []; } });
  const stack: { keyword: string; scope: string }[] = (() => { try { return JSON.parse(s.within_kw || "[]"); } catch { return []; } })();
  const apply = (patch: WParams) => navigate({ to: "/app/wisers/search", search: { ...s, ...patch, page: undefined } as never });
  const sec = (k: string, title: string, body: ReactNode) => (
    <section className="fsec" key={k}>
      <button type="button" className="fh" aria-expanded={!!open[k]} onClick={() => setOpen({ ...open, [k]: !open[k] })}><span>{title}</span><I n={open[k] ? "up" : "down"} /></button>
      {open[k] && <div className="fb">{body}</div>}
    </section>
  );
  return (
    <aside className="side">
      <Seg label="側欄" value={tab} set={setTab} options={[["filter", "篩選"], ["hot", "熱門關鍵字"]]} />
      {tab === "filter" ? <>
        <div className="within">
          <label className="fld">在結果中搜尋<input value={within} onChange={e => setWithin(e.target.value)} /></label>
          <button type="button" className="go" disabled={!within.trim()} onClick={() => { apply({ within_kw: JSON.stringify(stack.concat({ keyword: within.trim(), scope: "content" })) }); setWithin(""); }}>篩選</button>
          {stack.length > 0 && <div className="chips">{stack.map((w, i) => <span className="chip" key={i}>{w.keyword}</span>)}
            <button type="button" className="lk" onClick={() => apply({ within_kw: stack.length > 1 ? JSON.stringify(stack.slice(0, -1)) : undefined })}>上一個篩選</button></div>}
        </div>
        {sec("media", "媒體及地區", <Derived job={job} kind="media">{d => <FacetList rows={rowsOf(d)} sel={region} set={setRegion} />}</Derived>)}
        {sec("match", "關鍵字配對", <Derived job={job} kind="match">{d => <FacetList rows={rowsOf(d)} sel={labs} set={setLabs} />}</Derived>)}
        {TREES.filter(t => t.key !== "edu").map(t => sec("lab-" + t.key, t.title,
          <Derived job={job} kind="labels" extra={`&type=${t.group}&id=${t.root}`}>{d => <FacetList rows={rowsOf(d)} sel={labs} set={setLabs} />}</Derived>))}
        <div className="acts">
          <button type="button" className="go" disabled={!region.length && !labs.length} onClick={() => apply({
            f_region: region.length ? region.join(",") : undefined,
            f_labels: labs.length ? JSON.stringify(labs.map(id => ({ labelId: id, labelTypeId: "" }))) : undefined })}>套用篩選</button>
        </div>
        <p className="hint">套用篩選是一次新的 Wisers 搜尋。</p>
      </> : <>
        {sec("hot", "熱門關鍵字", <Derived job={job} kind="hot">{d => <HotWords rows={rowsOf(d)} add={w => apply({ within_kw: JSON.stringify(stack.concat({ keyword: w, scope: "content" })) })} />}</Derived>)}
        {sec("hotneg", "負面熱詞", <Derived job={job} kind="hot_negative">{d => <HotWords rows={rowsOf(d)} add={w => apply({ within_kw: JSON.stringify(stack.concat({ keyword: w, scope: "content" })) })} />}</Derived>)}
      </>}
    </aside>
  );
}

function HotWords({ rows, add }: { rows: { text: string; count: number | null }[]; add: (w: string) => void }) {
  if (!rows.length) return <p className="hint">沒有熱門關鍵字。</p>;
  const max = Math.max(...rows.map(r => r.count || 0), 1);
  return <ol className="hotw">{rows.slice(0, 30).map((r, i) => (
    <li key={r.text}><span className="num rk">{i + 1}</span>
      <button type="button" className="lk" onClick={() => add(r.text)} title="在結果中搜尋這個字詞">{r.text}</button>
      <span className="bar" style={{ width: `${Math.round(((r.count || 0) / max) * 100)}%` }} aria-hidden="true" />
      <span className="num">{nf(r.count)}</span></li>
  ))}</ol>;
}

/** the download sheet (WisersOne's options; explicit documents only) */
function DownloadSheet({ docs, close }: { docs: string[]; close: () => void }) {
  const [content, setContent] = useState("headline-content");
  const [format, setFormat] = useState("pdf");
  const [hl, setHl] = useState(true);
  const [d, setD] = useState<Download | null>(null);
  const [err, setErr] = useState("");
  const imgs = content.includes("image");
  const formats = content === "image" ? ["jpg"] : imgs ? ["pdf"] : ["pdf", "doc", "html", "rtf", "txt"];
  useEffect(() => { if (!formats.includes(format)) setFormat(formats[0]); }, [content]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!d || d.state !== "running") return;
    const t = setTimeout(async () => { try { setD((await getJSON<{ download: Download }>(`download/${d.id}`)).download); } catch (e) { setErr((e as Error).message); } }, 1500);
    return () => clearTimeout(t);
  }, [d]);
  const start = async () => {
    setErr("");
    try {
      const fmt = format === "pdf" && imgs ? "clipbook-pdf" : format;
      setD((await getJSON<{ download: Download }>("download", 60, { method: "POST", body: JSON.stringify({ docs, content, format: fmt, highlight: hl }) })).download);
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="sheet" role="dialog" aria-label="下載">
      <div className="sh"><h3>下載 {docs.length} 篇</h3><button type="button" className="ib" onClick={close}><I n="close" label="關閉" /></button></div>
      <h4>內容</h4>
      <Seg label="內容" value={content} set={setContent} options={[["headline-content", "標題及內文"], ["headline-content-image", "標題、內文及圖片"],
        ["headline-image", "標題及圖片"], ["headline", "只有標題"], ["image", "只有圖片"]]} />
      <h4>格式</h4>
      <Seg label="格式" value={format} set={setFormat} options={formats.map(f => [f, f.toUpperCase()] as [string, string])} />
      <label className="cb"><input type="checkbox" checked={hl} onChange={e => setHl(e.target.checked)} />標示關鍵字</label>
      <p className="hint">下載會計算 Wisers 帳戶的下載額度，並記在紀錄頁。</p>
      {!d ? <button type="button" className="go" onClick={start}><I n="download" />下載</button>
        : d.state === "running" ? <Wait what="準備檔案" slowMs={240_000} />
        : d.state === "ok" ? <a className="go" href={`${API}download/${d.id}?file=1`}><I n="download" />儲存檔案</a>
        : <p>未能下載：{d.error}</p>}
      {err && <p>未能下載：{err}</p>}
    </div>
  );
}

function Toolbar({ r, s, job, sel, setSel }: { r: Result; s: WParams; job: string; sel: string[]; setSel: (v: string[]) => void }) {
  const navigate = useNavigate();
  const [view, setView] = useKept<string>("view", "list");
  const [dl, setDl] = useState(false);
  const ids = r.items.map(x => x.doc_id);
  const all = ids.length > 0 && ids.every(id => sel.includes(id));
  const pick = sel.length ? sel : ids;
  const set = (patch: WParams) => navigate({ to: "/app/wisers/search", search: { ...s, ...patch, page: undefined } as never });
  return <>
    <div className="tools">
      <label className="cb"><input type="checkbox" checked={all} onChange={e => setSel(e.target.checked ? ids : [])} />全選</label>
      <span className="num hint">已選 {sel.length}</span>
      <button type="button" className="tb" onClick={() => setDl(!dl)}><I n="download" />下載</button>
      <Link className="tb" to="/app/wisers/print" search={{ job, ids: pick.join(",") }}><I n="print" />列印</Link>
      <Link className="tb" to="/app/wisers/browse" search={{ job, ids: pick.join(",") }}><I n="text" />逐篇瀏覽</Link>
      <span className="gap" />
      <select aria-label="排序" value={s.sort || "DESC:date"} onChange={e => set({ sort: e.target.value === "DESC:date" ? undefined : e.target.value })}>
        {SORTS.filter(([v]) => v !== "tracking" || s.neg).map(([v, t]) => <option key={v} value={v}>{t}</option>)}
      </select>
      <select aria-label="每頁" value={s.size || "50"} onChange={e => set({ size: e.target.value === "50" ? undefined : e.target.value })}>
        {SIZES.map(v => <option key={v} value={v}>每頁 {v}</option>)}
      </select>
      <label className="cb"><input type="checkbox" checked={s.cluster !== "0"} onChange={e => set({ cluster: e.target.checked ? undefined : "0" })} />合併相似</label>
      <label className="cb"><input type="checkbox" checked={s.summary !== "0"} onChange={e => navigate({ to: "/app/wisers/search", search: { ...s, summary: e.target.checked ? undefined : "0" } as never, replace: true })} />摘要</label>
      <Seg label="顯示方式" value={view} set={setView} options={[["list", <I n="list" label="列表" />], ["cards", <I n="cards" label="卡片" />], ["grid", <I n="grid" label="格狀" />]]} />
    </div>
    {dl && <DownloadSheet docs={pick} close={() => setDl(false)} />}
  </>;
}

function Pager({ s, page, size, total, top }: { s: WParams; page: number; size: number; total: number; top: number }) {
  const last = Math.max(1, Math.ceil(Math.min(total, top) / size));
  if (last <= 1) return null;
  const go = (p: number) => ({ to: "/app/wisers/search" as const, search: { ...s, page: p > 1 ? String(p) : undefined } as never });
  const nums = [...new Set([1, page - 2, page - 1, page, page + 1, page + 2, last].filter(p => p >= 1 && p <= last))].sort((a, b) => a - b);
  return (
    <nav className="pager" aria-label="頁數">
      {page > 1 ? <Link {...go(page - 1)} aria-label="上一頁"><I n="prev" /></Link> : <span className="off"><I n="prev" /></span>}
      {nums.map((p, i) => <Fragment key={p}>{i > 0 && p - nums[i - 1] > 1 && <span className="off">…</span>}
        {p === page ? <span className="on num" aria-current="page">{p}</span> : <Link {...go(p)} className="num">{p}</Link>}</Fragment>)}
      {page < last ? <Link {...go(page + 1)} aria-label="下一頁"><I n="next" /></Link> : <span className="off"><I n="next" /></span>}
      <span className="hint">每一頁是一次 Wisers 搜尋</span>
    </nav>
  );
}

export function WisersSearch() {
  const s = useSearch({ strict: false }) as WParams;
  const qc = useQueryClient();
  const qs = qsOf(s);
  useEffect(() => { rememberForm(baseOf(s)); }, [qs]);  // eslint-disable-line react-hooks/exhaustive-deps
  const has = !!(s.q || s.and || s.or);
  const [fresh, setFresh] = useState(0);
  const key = ["wisers", "search", qs];
  const res = useQuery({ queryKey: key, queryFn: () => runSearch(qs, fresh > 0), enabled: has, staleTime: Infinity, gcTime: 86_400_000, ...NO_RETRY });
  const again = (mode: "resume" | "new" | "fresh") => {
    if (mode !== "resume") resume.delete(qs);
    if (mode === "fresh") setFresh(n => n + 1);
    else qc.resetQueries({ queryKey: key, exact: true });
  };
  useEffect(() => { if (fresh) qc.resetQueries({ queryKey: key, exact: true }); }, [fresh]);  // eslint-disable-line react-hooks/exhaustive-deps
  const job = res.data?.job.id || "";
  const [sel, setSelState] = useState<string[]>([]);
  useEffect(() => { setSelState(job ? selected(job) : []); }, [job]);
  const setSel = (v: string[]) => { kept.set("sel:" + job, v); setSelState(v); };
  const [view] = useKept<string>("view", "list");
  const [side, setSide] = useKept<boolean>("side-open", false);
  const page = Math.max(1, Number(s.page || 1) || 1);
  const r = res.data?.result;
  const size = Number(r?.page_size || s.size || 50);
  const total = Number(r?.total || 0);
  const top = Number(r?.max_docs || 100000);
  const body = useMemo(() => {
    if (!has) return <p>請先輸入關鍵字。<Link to="/app/wisers">返回搜尋</Link></p>;
    if (res.isPending) return <Running qs={qs} />;
    if (res.isError) {
      const e = res.error as Error;
      if (e instanceof Lost) return <div className="wwait"><p>與轉接程式失去連線（{e.message}）。搜尋仍可能在進行。</p>
        <button type="button" className="go" onClick={() => again("resume")}>再讀取結果（不會再搜尋）</button></div>;
      if (e instanceof SearchFailed) return <div className="wwait"><p><I n="cross" />{e.f.message}</p><p className="hint">{CHARGED[e.f.charged]}</p>
        <button type="button" className="sec" onClick={() => again("new")}>再搜尋一次（會再用一次 Wisers 額度）</button></div>;
      return <div className="wwait"><p><I n="cross" />{e.message}</p><button type="button" className="sec" onClick={() => again("new")}>再試一次</button></div>;
    }
    return null;
  }, [has, res.status, res.error, qs]);  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Frame wide>
      <div className="rhead">
        <Link to="/app/wisers" search={baseOf(s) as never} className="tb"><I n="prev" />修改搜尋</Link>
        <Criteria s={s} />
        {r && <button type="button" className="tb" onClick={() => again("fresh")} title="會再用一次 Wisers 額度">重新搜尋</button>}
      </div>
      {body}
      {r && <>
        <Tabs job={job} s={s} />
        <div className="count">
          <span>共 <b className="num">{nf(total)}</b> 篇{r.total_docs && r.total_docs !== r.total ? <>（合併前 <span className="num">{nf(Number(r.total_docs))}</span> 篇）</> : null}</span>
          {total >= top && <span className="hint">只可列出最多 {nf(top)} 篇，請收窄搜尋。</span>}
          {res.data?.job.cached && <span className="hint">24 小時內同一搜尋：由已儲存的結果答覆（{dt.dateTime(res.data.job.searched_at)}），沒有再用額度。</span>}
          <button type="button" className="tb side-btn" aria-expanded={side} onClick={() => setSide(!side)}><I n="filter" />篩選及熱門關鍵字</button>
        </div>
        <div className={"rgrid" + (side ? " with-side" : "")}>
          {side && <Sidebar job={job} s={s} />}
          <div className="rmain">
            <Toolbar r={r} s={s} job={job} sel={sel} setSel={setSel} />
            {r.items.length === 0 && <p className="wrow">沒有結果。</p>}
            <div className={"rows " + view}>
              {r.items.map((x, i) => <Row key={x.doc_id || i} x={x} n={(page - 1) * size + i + 1} job={job} s={s} view={view}
                checked={sel.includes(x.doc_id)} check={on => setSel(on ? sel.concat(x.doc_id) : sel.filter(v => v !== x.doc_id))} />)}
            </div>
            <Pager s={s} page={page} size={size} total={total} top={top} />
          </div>
        </div>
      </>}
    </Frame>
  );
}
