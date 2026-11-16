// /app/wisers/doc/<docId>?job=&i=&style=: one document, as WisersOne's documentDetail: the headline, the heading line
// (outlet | page section | words | date, circulation), the toolbar (列印, 下載, 標示關鍵字, 原文), the text with the
// search's hits, the 印刷版 page images (style img: the page itself; thumbnails beside the text, the full-screen viewer
// with zoom and 下載 JPG), the other news on the same print page as ruled rows, and 上一篇 / 下一篇 over the job's items.
// The text comes from YYC's corpus when the article is there (free), else from Wisers (the account's click quota,
// logged); the reader looks the same either way, with one footer line naming the source (Deyao, 2026-10-29).
//   /app/wisers/browse?job=&ids=  the selected documents one after another;  /app/wisers/print?ids=  the same, printable
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearch } from "@tanstack/react-router";
import { Wait } from "../../lib/Wait";
import { dt } from "../../lib/dates";
import { API, KIND_ZH, NO_RETRY, getJSON, nf, type Doc, type JobAnswer } from "../../lib/wisers";
import { Frame, I, useKept } from "./ui";

const docQuery = (id: string, job?: string, style?: string) => ({ queryKey: ["wisers", "doc", id, style || "all"],
  queryFn: () => getJSON<Doc>(`doc/${encodeURIComponent(id)}?${new URLSearchParams({ ...(job ? { job } : {}), ...(style ? { style } : {}) })}`, 120),
  staleTime: Infinity, gcTime: 3_600_000, ...NO_RETRY });

/** the document's images (the image quota gate once per document, then how many) */
function Images({ id, big, open }: { id: string; big: boolean; open: (n: number) => void }) {
  const q = useQuery({ queryKey: ["wisers", "images", id], queryFn: () => getJSON<{ count: number; quota?: { message: string } }>(`doc/${encodeURIComponent(id)}/images`, 120),
    staleTime: Infinity, ...NO_RETRY });
  const [cur, setCur] = useState(1);
  if (q.isPending) return <Wait className="note" what="載入印刷版頁面" />;
  if (q.isError) return <p className="hint">未能載入圖片：{(q.error as Error).message}</p>;
  if (q.data.quota) return <p className="quota"><I n="cross" />{q.data.quota.message}</p>;
  if (!q.data.count) return big ? <p className="hint">這篇文章沒有印刷版頁面。</p> : null;
  const src = (n: number, size: string) => `${API}doc/${encodeURIComponent(id)}/image/${n}?size=${size}`;
  return (
    <section className={"imgs" + (big ? " big" : "")} aria-label="新聞相關圖片">
      <h3>新聞相關圖片 <span className="hint">共 {q.data.count} 張</span></h3>
      {big && <button type="button" className="page-img" onClick={() => open(cur)}><img src={src(cur, "full")} alt={`第 ${cur} 張`} /></button>}
      <ul className="thumbs">{Array.from({ length: q.data.count }, (_, i) => i + 1).map(n => (
        <li key={n} className={big && n === cur ? "on" : ""}>
          <button type="button" onClick={() => (big ? setCur(n) : open(n))}><img src={src(n, "thumb")} alt={`第 ${n} 張縮圖`} loading="lazy" /></button>
        </li>
      ))}</ul>
    </section>
  );
}

/** the full-screen image viewer: arrows, i/N, zoom, drag to pan, the heading box, 下載 (the image itself) */
function Viewer({ id, n, count, d, close }: { id: string; n: number; count: number; d: Doc; close: () => void }) {
  const [i, setI] = useState(n);
  const [z, setZ] = useState(1);
  const [meta, setMeta] = useKept("viewer-meta", true);
  const src = `${API}doc/${encodeURIComponent(id)}/image/${i}?size=full`;
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") close(); if (e.key === "ArrowRight" && i < count) setI(i + 1); if (e.key === "ArrowLeft" && i > 1) setI(i - 1); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [i, count, close]);
  const h = d.heading;
  const m = d.meta || {};
  return (
    <div className="viewer" role="dialog" aria-label="印刷版頁面">
      <div className="vbar">
        <button type="button" className="ib" disabled={i <= 1} onClick={() => setI(i - 1)}><I n="prev" label="上一張" /></button>
        <span className="num">{i} / {count}</span>
        <button type="button" className="ib" disabled={i >= count} onClick={() => setI(i + 1)}><I n="next" label="下一張" /></button>
        <span className="gap" />
        <button type="button" className="ib" onClick={() => setZ(Math.max(0.5, z - 0.25))}><I n="minus" label="縮小" /></button>
        <input type="range" min={0.5} max={3} step={0.25} value={z} onChange={e => setZ(Number(e.target.value))} aria-label="縮放" />
        <button type="button" className="ib" onClick={() => setZ(Math.min(3, z + 0.25))}><I n="plus" label="放大" /></button>
        <button type="button" className="ib" onClick={() => setZ(1)}><I n="reset" label="還原" /></button>
        <a className="ib" href={src} download={`wisers-${id}-${i}.jpg`}><I n="download" label="下載 JPG" /></a>
        <button type="button" className="ib" onClick={() => setMeta(!meta)}><I n="text" label="文章資料" /></button>
        <button type="button" className="ib" onClick={close}><I n="close" label="關閉" /></button>
      </div>
      <div className="vimg"><img src={src} alt={`第 ${i} 張`} style={{ width: `${z * 100}%` }} /></div>
      {meta && h && <dl className="vmeta">
        <dt>標題</dt><dd>{h.headline}</dd><dt>來源</dt><dd>{h.source}</dd>
        <dt>版面</dt><dd>{[h.page, h.section].filter(Boolean).join(" | ") || "-"}</dd><dt>日期</dt><dd className="num">{dt.date(h.date)}</dd>
        <dt>發行量</dt><dd className="num">{nf(h.circulation) || "-"}</dd><dt>廣告價值</dt><dd className="num">{String(m.adValue ?? "-")}</dd>
        <dt>字數</dt><dd className="num">{nf(h.words) || "-"}</dd><dt>面積</dt><dd className="num">{m.imageSize ? `${String(m.imageSize)} cm²` : "-"}</dd>
      </dl>}
    </div>
  );
}

/** the other news on the same print page: ruled rows, the open one marked by an ink edge */
function SamePage({ d, job }: { d: Doc; job?: string }) {
  if (!d.same_page?.length) return null;
  return (
    <section className="samepage" aria-label="同版其他新聞">
      <h3>同版其他新聞 <span className="hint">{d.heading?.source} {d.heading?.page} 版</span></h3>
      <ol className="samelist">{d.same_page.map(x => (
        <li key={x.docId} className={x.docId === d.doc_id ? "here" : ""}>
          {x.docId === d.doc_id ? <b>{x.headline}</b> : <Link to="/app/wisers/doc/$id" params={{ id: x.docId }} search={job ? { job } : {}}>{x.headline}</Link>}</li>
      ))}</ol>
    </section>
  );
}

function Heading({ d }: { d: Doc }) {
  const h = d.heading;
  if (!h) return null;
  const plates: [string, string][] = [["來源", h.source], ["版面", [h.page, h.section].filter(Boolean).join(" ")], ["專欄", h.column], ["作者", h.author !== h.source ? h.author : ""],
    ["字數", h.words != null ? nf(h.words) : ""], ["日期", dt.dateTime(h.date)], ["發行量", h.circulation != null ? nf(h.circulation) : ""],
    ["種類", KIND_ZH[h.kind] || h.kind]];
  return <>
    <h1 className="dh">{h.headline || "（無標題）"}</h1>
    {h.translated && <p className="hint">{h.translated}</p>}
    <div className="plates">{plates.filter(([, v]) => v).map(([k, v]) => <span className="plate" key={k}><span>{k}</span><b>{v}</b></span>)}</div>
  </>;
}

/** one document's body (the reader, browse and print draw it the same way) */
export function DocBody({ id, job, style, hl, viewerOk = true }: { id: string; job?: string; style?: string; hl: boolean; viewerOk?: boolean }) {
  const q = useQuery(docQuery(id, job, style));
  const [view, setView] = useState(0);
  if (q.isPending) return <Wait what="載入文章" slowMs={100_000} />;
  if (q.isError) return <Wait what="載入文章" failed onRetry={() => q.refetch()} />;
  const d = q.data;
  if (d.redirect) return <><Heading d={d} /><p>這篇文章只有原文連結：<a href={d.redirect} target="_blank" rel="noopener noreferrer">{d.redirect}</a></p></>;
  const img = style === "img";
  const m = d.meta || {};
  return (
    <article className={"doc" + (hl ? "" : " nohl")} data-from={d.from || ""}>
      <Heading d={d} />
      {d.quota?.blocking ? <p className="quota"><I n="cross" />{d.quota.message}</p> : <>
        {d.quota && <p className="quota soft">{d.quota.message}</p>}
        <div className={"dgrid" + ((d.images?.has || (d.images?.count || 0) > 0) && !img ? " with-imgs" : "")}>
          <div>
            {img ? <Images id={id} big open={setView} /> : <div className="text" dangerouslySetInnerHTML={{ __html: d.html }} />}
            {(m.videoUrl as string) && <video controls preload="none" src={String(m.videoUrl)} />}
            {(m.audioUrl as string) && <audio controls preload="none" src={String(m.audioUrl)} />}
            {(d.heading?.link || d.post?.url) && <p className="orig"><a href={d.heading?.link || d.post?.url} target="_blank" rel="noopener noreferrer"><I n="link" />原文連結</a></p>}
          </div>
          {!img && (d.images?.has || (d.images?.count || 0) > 0) && <Images id={id} big={false} open={setView} />}
        </div>
      </>}
      <SamePage d={d} job={job} />
      <footer className="dfoot">
        {m.intDocId ? <span>文件編號 <span className="num">{String(m.intDocId)}</span></span> : null}
        <span>全文來源：{d.from === "corpus" ? `${d.post?.website || "原文網站"}（YYC 資料庫）` : "Wisers"}</span>
        {(["industrys", "brands", "products"] as const).map(k => m[k] ? <span key={k}>{({ industrys: "行業", brands: "品牌", products: "產品" })[k]}：{String(m[k])}</span> : null)}
      </footer>
      {view > 0 && viewerOk && <ViewerFor id={id} d={d} n={view} close={() => setView(0)} />}
    </article>
  );
}

function ViewerFor({ id, d, n, close }: { id: string; d: Doc; n: number; close: () => void }) {
  const qc = useQueryClient();
  const c = qc.getQueryData<{ count: number }>(["wisers", "images", id]);
  return <Viewer id={id} n={n} count={c?.count || d.images?.count || 1} d={d} close={close} />;
}

/** the job's items (for 上一篇 / 下一篇): already in the cache when the reader was opened from the results */
function useJobItems(job?: string) {
  const qc = useQueryClient();
  const hit = job ? qc.getQueriesData<JobAnswer>({ queryKey: ["wisers", "search"] }).find(([, v]) => v?.job.id === job)?.[1] : undefined;
  const q = useQuery({ queryKey: ["wisers", "job", job], enabled: !!job && !hit,
    queryFn: () => getJSON<JobAnswer>(`job/${job}`), staleTime: Infinity, ...NO_RETRY });
  return (hit || q.data)?.result?.items || [];
}

export function WisersDoc() {
  const { id } = useParams({ strict: false }) as { id: string };
  const s = useSearch({ strict: false }) as { job?: string; i?: string; style?: string };
  const [hl, setHl] = useKept("hl", true);
  const items = useJobItems(s.job);
  const i = items.findIndex(x => x.doc_id === id);
  const near = (k: number) => items[k] ? { to: "/app/wisers/doc/$id" as const, params: { id: items[k].doc_id }, search: { job: s.job, i: String(k), style: s.style } } : null;
  const prev = i > 0 ? near(i - 1) : null, next = i >= 0 ? near(i + 1) : null;
  return (
    <Frame>
      <div className="dtools">
        <span className="cellrow">
          {prev ? <Link className="tb" {...prev}><I n="prev" />上一篇</Link> : <span className="tb off"><I n="prev" />上一篇</span>}
          {next ? <Link className="tb" {...next}>下一篇<I n="next" /></Link> : <span className="tb off">下一篇<I n="next" /></span>}
        </span>
        <span className="gap" />
        <label className="cb"><input type="checkbox" checked={hl} onChange={e => setHl(e.target.checked)} />標示關鍵字</label>
        <Link className="tb" to="/app/wisers/print" search={{ job: s.job, ids: id }}><I n="print" />列印</Link>
      </div>
      <DocBody id={id} job={s.job} style={s.style} hl={hl} />
    </Frame>
  );
}

export function WisersBrowse() {
  const s = useSearch({ strict: false }) as { job?: string; ids?: string };
  const ids = (s.ids || "").split(",").filter(Boolean).slice(0, 200);
  const [n, setN] = useState(4);
  const [hl, setHl] = useKept("hl", true);
  return (
    <Frame>
      <div className="dtools"><span>逐篇瀏覽 {ids.length} 篇</span><span className="gap" />
        <label className="cb"><input type="checkbox" checked={hl} onChange={e => setHl(e.target.checked)} />標示關鍵字</label>
        <Link className="tb" to="/app/wisers/print" search={s}><I n="print" />列印</Link></div>
      {ids.slice(0, n).map(id => <DocBody key={id} id={id} job={s.job} hl={hl} />)}
      {n < ids.length && <button type="button" className="sec" onClick={() => setN(n + 4)}>再載入 {Math.min(4, ids.length - n)} 篇</button>}
      <p className="hint">YYC 資料庫沒有的文章會由 Wisers 打開，計算閱讀額度。</p>
    </Frame>
  );
}

export function WisersPrint() {
  const s = useSearch({ strict: false }) as { job?: string; ids?: string };
  const ids = (s.ids || "").split(",").filter(Boolean).slice(0, 100);
  const [content, setContent] = useKept<"text" | "img" | "head">("print-content", "text");
  const [paged, setPaged] = useKept("print-paged", true);
  const [hl, setHl] = useKept("hl", true);
  const items = useJobItems(s.job);
  return (
    <div className="page-wisers print-page">
      <div className="dtools noprint">
        <Link className="tb" to="/app/wisers/browse" search={s}><I n="prev" />返回</Link>
        <select value={content} onChange={e => setContent(e.target.value as "text")} aria-label="內容">
          <option value="text">標題及內文</option><option value="img">標題及印刷版頁面</option><option value="head">只有標題</option>
        </select>
        <label className="cb"><input type="checkbox" checked={paged} onChange={e => setPaged(e.target.checked)} />每篇新一頁</label>
        <label className="cb"><input type="checkbox" checked={hl} onChange={e => setHl(e.target.checked)} />標示關鍵字</label>
        <span className="gap" />
        <button type="button" className="go" onClick={() => window.print()}><I n="print" />列印 {ids.length} 篇</button>
      </div>
      <main className={"wmain" + (paged ? " paged" : "")}>
        {content === "head" ? <ol className="heads">{ids.map(id => <li key={id}><DocHead id={id} items={items} /></li>)}</ol>
          : ids.map(id => <DocBody key={id} id={id} job={s.job} hl={hl} style={content === "img" ? "img" : undefined} viewerOk={false} />)}
      </main>
    </div>
  );
}

/** a headline from the job's items (no document is opened for a list of headlines) */
function DocHead({ id, items }: { id: string; items: ReturnType<typeof useJobItems> }) {
  const x = items.find(y => y.doc_id === id);
  return x ? <><b>{x.headline}</b> <span className="hint">{x.source} {x.page} {dt.dateTime(x.date)}</span></> : <span>{id}</span>;
}
