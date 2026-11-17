// /app/wisers/log: 紀錄 (relay.py api/log, api/log/calls): every search sent to Wisers (who, why, what, how many, how
// long, the outcome, whether it cost quota) and every other Wisers call: documents (from YYC's corpus, from a copy
// already read, or from Wisers), images, downloads, the derived calls (Deyao, 2026-10-29: "log it"), plus the
// documents Wisers had that YYC's corpus did not (by outlet: the corpus's gaps).
// /app/wisers/saved, /view/<file>: the saved search responses, opened without a new search; /media/<id>: a publication.
import { Fragment, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { Wait } from "../../lib/Wait";
import { dt } from "../../lib/dates";
import { KIND_ZH, NO_RETRY, SORTS, getJSON, nf, type Result } from "../../lib/wisers";
import { CHARGED, Frame, I, Marked, Seg, secs, useKept } from "./ui";

type LogRow = { at: string; job?: string; who: string | null; reason: string; keyword: string; page: number; options: Record<string, unknown>;
  cached: boolean; ok: boolean; outcome: string; charged?: string; total?: string | null; raw?: string | null; secs?: number | null;
  error?: string | null; backfilled?: string };
type CallRow = { at: string; kind: string; path: string; who: string | null; reason: string; outcome: string; charged: string;
  secs?: number | null; error?: string | null; served?: string; doc?: string; headline?: string; post_id?: number; match?: string;
  quota?: string; corpus_miss?: string; derived?: string; docs?: number; format?: string; n?: number; source?: string };
type Summary = Record<string, number | string | null>;
const S = (v: unknown) => (v == null ? "" : typeof v === "string" ? v : JSON.stringify(v));
const OUTCOME: Record<string, string> = { ok: "完成", cached: "由已儲存的答覆（不計額度）", error: "Wisers 回應錯誤", timeout: "Wisers 逾時",
  neterr: "連線中斷", lost: "轉接程式重啟", logged_out: "Wisers 已登出", quota: "額度不足" };
const OPT_ZH: Record<string, string> = { dateRangePeriod: "日期", dateFrom: "由", dateTo: "至", rangeTitle: "標題", rangeContent: "內文",
  withinScope: "字詞距離", sortOrder: "排序", pubType: "媒體種類", mediaType: "分頁", mediaCategory: "媒體類別", mediaRegion: "地區",
  pubLanguage: "語言", language: "文件語言", author: "作者", column: "專欄", section: "版面", thesaurusEnable: "相似字詞",
  negativeCategories: "負面", subjects: "主題", listedCompanies: "上市公司", industries: "行業", companies: "集團／公司", brands: "品牌／產品",
  includePubids: "包括媒體", excludePubids: "排除媒體", hotPickIds: "熱選", keyWordScope: "在結果中搜尋", labelFilterList: "篩選標籤",
  clusterType: "合併相似", pageSize: "每頁", startIdx: "由第幾篇" };
function optText(o: Record<string, unknown>): string {
  const x = { ...o };
  const parts: string[] = [];
  if (x.dateRangePeriod === "custom" || x.dateFrom) { parts.push(`${S(x.dateFrom)} 至 ${S(x.dateTo)}`); delete x.dateFrom; delete x.dateTo; delete x.dateRangePeriod; }
  else if (x.dateRangePeriod === "today") { parts.push("今日"); delete x.dateRangePeriod; }
  for (const [k, v] of Object.entries(x)) {
    const val = Array.isArray(v) ? `${v.length} 個` : typeof v === "boolean" ? (v ? "是" : "否")
      : k === "sortOrder" ? (SORTS.find(([s]) => `DESC:${s}` === v || s === v) || ["", S(v)])[1] : S(v);
    parts.push(`${OPT_ZH[k] || k}：${val}`);
  }
  return parts.join("，");
}
const CALL_ZH: Record<string, string> = { doc: "文章", image: "圖片", download: "下載", derived: "結果分析", lookup: "選項", file: "檔案" };

function Searches({ rows }: { rows: LogRow[] }) {
  if (!rows.length) return <p className="wrow">還沒有。</p>;
  return <>{rows.map((r, i) => (
    <article className="wrow logrow" key={(r.job || r.raw || "") + r.at + i} data-outcome={r.outcome}>
      <p className="meta"><span className="num">{dt.dateTime(r.at)}</span><span>{r.who || "（未記錄使用者）"}</span>{r.reason && <span>{r.reason}</span>}</p>
      <h2>{r.raw ? <Link to="/app/wisers/view/$name" params={{ name: r.raw }}>{r.keyword}</Link> : r.keyword}{r.page > 1 ? `（第 ${r.page} 頁）` : ""}</h2>
      {Object.keys(r.options || {}).length > 0 && <p className="hint">{optText(r.options)}</p>}
      <p className="meta"><span className={"pip" + (r.ok ? " on" : "")} aria-hidden="true" /><span>{OUTCOME[r.outcome] || r.outcome}</span>
        {r.total != null && <span>符合 <span className="num">{nf(Number(r.total))}</span> 篇</span>}
        {r.secs != null && !r.cached && <span>用時 {secs(r.secs)}</span>}
        {!r.cached && r.charged && r.charged !== "yes" && <span>{CHARGED[r.charged]}</span>}
        {r.backfilled && <span>（由已儲存的回應補回）</span>}</p>
      {r.error && <p className="hint">{r.error}</p>}
    </article>
  ))}</>;
}

function Calls({ rows }: { rows: CallRow[] }) {
  if (!rows.length) return <p className="wrow">還沒有。</p>;
  return <>{rows.map((r, i) => (
    <article className="wrow logrow" key={r.at + i} data-kind={r.kind} data-served={r.served || ""}>
      <p className="meta"><span className="num">{dt.dateTime(r.at)}</span><span>{r.who || "（未記錄使用者）"}</span><span>{CALL_ZH[r.kind] || r.kind}</span>{r.reason && <span>{r.reason}</span>}</p>
      <h2>{r.doc ? <Link to="/app/wisers/doc/$id" params={{ id: r.doc }}>{r.headline || r.doc}</Link> : r.derived || r.path}</h2>
      <p className="meta"><span className={"pip" + (r.outcome === "ok" || r.outcome === "cached" ? " on" : "")} aria-hidden="true" />
        <span>{r.served === "corpus" ? "由 YYC 資料庫答覆（不用 Wisers）" : r.served === "cache" ? "由已讀過的副本答覆（不計額度）" : OUTCOME[r.outcome] || r.outcome}</span>
        {r.served !== "corpus" && r.served !== "cache" && <span>{CHARGED[r.charged] || r.charged}</span>}
        {r.quota && <span>額度代碼 {r.quota}</span>}{r.docs != null && <span>{r.docs} 篇{r.format ? ` ${r.format.toUpperCase()}` : ""}</span>}
        {r.n != null && <span>第 {r.n} 張</span>}{r.secs != null && <span>用時 {secs(r.secs)}</span>}</p>
      {r.corpus_miss && <p className="hint">YYC 資料庫：{r.corpus_miss}</p>}
      {r.error && <p className="hint">{r.error}</p>}
    </article>
  ))}</>;
}

/** documents Wisers had and YYC's corpus did not, by outlet (from the documents log): where the corpus has gaps */
function Gaps({ rows }: { rows: CallRow[] }) {
  const by = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows) if (r.kind === "doc" && r.served === "wisers" && r.outcome === "ok") m.set(r.source || "（未知來源）", (m.get(r.source || "（未知來源）") || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);
  if (!by.length) return null;
  return <section className="wrow"><h2>由 Wisers 打開、YYC 資料庫沒有的文章（按來源）</h2>
    <ul className="facet">{by.map(([k, n]) => <li key={k}><span>{k}</span><span className="num">{n}</span></li>)}</ul></section>;
}

export function WisersLog() {
  const [tab, setTab] = useKept<"search" | "calls">("log-tab", "search");
  const q = useQuery({ queryKey: ["wisers", "log"], queryFn: () => getJSON<{ rows: LogRow[]; summary: Summary }>("log", 45), staleTime: 0, gcTime: 86_400_000, ...NO_RETRY });
  const c = useQuery({ queryKey: ["wisers", "calls"], queryFn: () => getJSON<{ rows: CallRow[] }>("log/calls", 45), enabled: tab === "calls", staleTime: 0, ...NO_RETRY });
  const sm = q.data?.summary;
  return <Frame>
    <h2 className="ttl">紀錄</h2>
    <p className="sub">每一次送到 Wisers 的搜尋、打開的文章及圖片、下載及其他呼叫（香港時間）。由 YYC 資料庫或已儲存的答覆回應的，不計 Wisers 額度。</p>
    {sm && <div className="plates sum" data-today={S(sm.today)} data-month={S(sm.month)}>
      <span className="plate"><span>今日搜尋</span><b className="num">{S(sm.today)}</b></span>
      <span className="plate"><span>本月搜尋</span><b className="num">{S(sm.month)}</b></span>
      <span className="plate"><span>本月由 Wisers 打開的文章</span><b className="num">{S(sm.doc_month)}</b></span>
      <span className="plate"><span>本月由 YYC 資料庫答覆</span><b className="num">{S(sm.corpus_month)}</b></span>
      <span className="plate"><span>本月圖片</span><b className="num">{S(sm.image_month)}</b></span>
      <span className="plate"><span>本月下載</span><b className="num">{S(sm.download_month)}</b></span>
    </div>}
    <p className="hint">WisersOne 沒有顯示搜尋或閱讀額度的剩餘數目；以上是轉接程式自己的紀錄。</p>
    <Seg label="紀錄" value={tab} set={setTab} options={[["search", "搜尋"], ["calls", "文章、圖片及其他"]]} />
    {tab === "search" ? (q.isPending ? <Wait what="載入紀錄" /> : q.isError ? <Wait what="載入紀錄" failed onRetry={() => q.refetch()} /> : <Searches rows={q.data.rows} />)
      : (c.isPending ? <Wait what="載入紀錄" /> : c.isError ? <Wait what="載入紀錄" failed onRetry={() => c.refetch()} /> : <><Gaps rows={c.data.rows} /><Calls rows={c.data.rows} /></>)}
  </Frame>;
}

type Saved = { file: string; at: string; keyword: string; period: string; total: string; items: number; startIdx: number };

export function WisersSaved() {
  const q = useQuery({ queryKey: ["wisers", "saved"], queryFn: () => getJSON<{ saved: Saved[] }>("saved"), staleTime: 0, ...NO_RETRY });
  return <Frame>
    <h2 className="ttl">已儲存的回應</h2>
    <p className="sub">每次搜尋的請求及 Wisers 的完整回應。打開不會再搜尋，不用 Wisers 額度。</p>
    {q.isPending ? <Wait what="載入已儲存的回應" /> : q.isError ? <Wait what="載入已儲存的回應" failed onRetry={() => q.refetch()} />
      : q.data.saved.length === 0 ? <p className="wrow">還沒有。</p>
      : q.data.saved.map(x => (
        <Link key={x.file} className="wrow rowlink" to="/app/wisers/view/$name" params={{ name: x.file }}>
          <b>{x.keyword}</b>
          <span className="meta"><span>{x.period.replace(/\d{4}-\d{2}-\d{2}/g, d => dt.date(d))}</span><span>符合 <span className="num">{nf(Number(x.total))}</span> 篇，存了 {x.items} 篇{x.startIdx > 1 ? `（第 ${x.startIdx} 篇起）` : ""}</span><span className="num">{dt.dateTime(x.at)}</span></span>
        </Link>
      ))}
  </Frame>;
}

export function WisersView() {
  const { name } = useParams({ strict: false }) as { name: string };
  const q = useQuery({ queryKey: ["wisers", "saved", name], queryFn: () => getJSON<Result>("saved/" + encodeURIComponent(name)), staleTime: Infinity, ...NO_RETRY });
  return <Frame>
    {q.isPending ? <Wait what="載入已儲存的回應" /> : q.isError ? <Wait what="載入已儲存的回應" failed onRetry={() => q.refetch()} /> : <>
      <h2 className="ttl">「{S(q.data.crit.keyword)}」</h2>
      <p className="sub">已儲存的回應（{dt.dateTime(q.data.at)}），沒有再搜尋。符合 <span className="num">{nf(Number(q.data.total))}</span> 篇。</p>
      {q.data.items.map((x, i) => (
        <article className="wrow" key={x.doc_id || i}>
          <h2><Link to="/app/wisers/doc/$id" params={{ id: x.doc_id }}>{x.headline || "（無標題）"}</Link></h2>
          <p className="meta"><span>{x.source}</span>{x.page && <span>{x.page} 版</span>}<span>{KIND_ZH[x.kind] || x.kind}</span><span className="num">{dt.dateTime(x.date)}</span>
            {x.words != null && <span className="num">{nf(x.words)} 字</span>}</p>
          {x.excerpt.length > 0 && <p className="ex"><Marked parts={x.excerpt} /></p>}
          {x.link && <div className="foot"><a className="lk" href={x.link} target="_blank" rel="noopener noreferrer"><I n="link" />原文</a></div>}
        </article>
      ))}
    </>}
  </Frame>;
}

export function WisersMedia() {
  const { id } = useParams({ strict: false }) as { id: string };
  const q = useQuery({ queryKey: ["wisers", "publication", id], queryFn: () => getJSON<{ media: Record<string, unknown> }>("media/" + encodeURIComponent(id)), staleTime: Infinity, ...NO_RETRY });
  const rows = Object.entries((q.data?.media || {}) as Record<string, unknown>).filter(([, v]) => v != null && v !== "" && typeof v !== "object");
  return <Frame>
    <h2 className="ttl">媒體資料</h2>
    {q.isPending ? <Wait what="載入媒體資料" /> : q.isError ? <Wait what="載入媒體資料" failed onRetry={() => q.refetch()} />
      : <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>}
  </Frame>;
}
