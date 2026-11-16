// 搜尋: one query box with its switches, the filters (dates with quick ranges, media type,
// outlets grouped by type with their search box, region, sort; a sticky column from 960 px, a collapsed 篩選 panel on
// phones), the count, time taken and per-type / per-outlet counts (tap one to narrow to it), the cards with the terms
// marked in headline and extract, paging and 匯出 Excel. Every search is a URL and its own history entry.
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";
import { SiteLink } from "../lib/SiteLink";
import { AiBadge } from "../lib/AiBadge";
import { Icon } from "../lib/icons";
import { dayAdd, fmtN } from "../lib/time";
import { dt } from "../lib/dates";
import { searchMetaQuery, searchQuery } from "../queries-search";
import { Wait } from "../lib/Wait";
import type { SearchMeta, SearchResult } from "../api-search";
import { hasSearch, searchQS, toQS, type SearchParams } from "./search";
import { langOf, Txt } from "../lib/lang";

const list = (v?: string) => (v ? v.split(",").map(x => x.trim()).filter(Boolean) : []);
const toggle = (xs: string[], v: string) => (xs.includes(v) ? xs.filter(x => x !== v) : xs.concat(v));
const when = (t: string | null) => dt.dateTime(t);
const QUICK: [string, number | null][] = [["今日", 0], ["7 日", 6], ["30 日", 29], ["1 年", 364], ["全部", null]];

/** the text with every term marked (longest first, case-insensitive) */
function Marked({ text, terms }: { text: string; terms: string[] }) {
  const ts = [...new Set(terms.filter(t => t && t.length))].sort((a, b) => b.length - a.length)
    .map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!ts.length) return <>{text}</>;
  const re = new RegExp("(" + ts.join("|") + ")", "gi");
  const parts = text.split(re);
  return <>{parts.map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>))}</>;
}

export function SearchPage() {
  const s = useSearch({ from: "/search" }) as SearchParams;
  const navigate = useNavigate({ from: "/search" });
  const meta = useQuery(searchMetaQuery);
  const M: SearchMeta | undefined = meta.data;
  const has = hasSearch(s);
  const qs = searchQS(s);
  const res = useQuery({ ...searchQuery(qs, toQS(s)), enabled: has, placeholderData: keepPreviousData });

  const S = { q: s.q || "", types: list(s.type), sites: list(s.site), regions: list(s.region), from: s.from || "", to: s.to || "",
    sort: s.sort || "date", title: s.title === "1", comments: s.comments === "1", page: Math.max(1, +(s.page || 1) || 1) };

  /** a new search: its own history entry; filters reset the page */
  const go = (patch: Partial<typeof S>, keepPage = false) => {
    const n = { ...S, ...patch };
    if (!keepPage && !("page" in patch)) n.page = 1;
    const next: SearchParams = {
      q: n.q || undefined, type: n.types.join(",") || undefined, site: n.sites.join(",") || undefined,
      region: n.regions.join(",") || undefined, from: n.from || undefined, to: n.to || undefined,
      sort: n.sort !== "date" ? n.sort : undefined, title: n.title ? "1" : undefined, comments: n.comments ? "1" : undefined,
      page: n.page > 1 ? String(n.page) : undefined, p: s.p,
    };
    // a search with nothing in its URL (按「搜尋」 on an empty box) still needs one: the default start day, the same search
    if (!hasSearch(next) && M) next.from = dayAdd(M.today, -29);
    navigate({ search: next as never });
  };

  // ---- the query box ----
  const qIn = useRef<HTMLInputElement>(null);
  const [qText, setQText] = useState(S.q);
  useEffect(() => { setQText(S.q); }, [S.q]);
  useEffect(() => { if (!has) qIn.current?.focus({ preventScroll: true }); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const [syntax, setSyntax] = useState(false);

  // ---- the filters: open on phones only when asked; always shown from 960 px (CSS) ----
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState("");
  const today = M?.today ?? "";
  const nf = S.types.length + S.sites.length + S.regions.length;
  const fsum = M ? (S.from ? `${dt.date(S.from)} 至 ${dt.date(S.to || M.today)}` : "近 30 日") + (nf ? `，${nf} 項篩選` : "") : "";
  const groups = M ? M.types.filter(t => !S.types.length || S.types.includes(t.type)) : [];
  const f = find.trim().toLowerCase();
  const tzh: Record<string, string> = Object.fromEntries((M?.types || []).map(t => [t.type, t.zh]));

  // scroll to the top when paging (a new page of results)
  const lastPage = useRef(S.page);
  useEffect(() => { if (S.page !== lastPage.current && S.page > 1) window.scrollTo({ top: 0 }); lastPage.current = S.page; }, [S.page]);

  const filters = (
    <div className={"filters" + (open ? " open" : "")} id="filters">
      <button type="button" className="fsummary" aria-expanded={open} onClick={() => setOpen(!open)}>篩選<span className="n">{fsum}</span></button>
      <div className="fbody">
        <div className="fgroup"><h3>日期</h3>
          <div className="dates range">
            <input type="date" aria-label="由" value={S.from || (M ? dayAdd(M.today, -29) : "")} min={M?.first_day || undefined} max={today || undefined}
              onChange={e => go({ from: e.target.value })} />
            <span>至</span>
            <input type="date" aria-label="至" value={S.to || today} min={M?.first_day || undefined} max={today || undefined}
              onChange={e => go({ to: e.target.value })} />
          </div>
          {M && <div className="chips quick cellgrid ragged">{QUICK.map(([l, n]) => {
            const from = n === null ? M.first_day || "" : dayAdd(M.today, -n);
            const on = S.from === from && (S.to || M.today) === M.today;
            return <button key={l} type="button" className="chip" aria-pressed={on} onClick={() => go({ from, to: "" })}>{l}</button>;
          })}</div>}
        </div>
        <div className="fgroup"><h3>媒體類型<button type="button" onClick={() => go({ types: [] })}>全部</button></h3>
          <div className="chips cellgrid ragged">{M?.types.map(t => (
            <button key={t.type} type="button" className="chip" aria-pressed={S.types.includes(t.type)} onClick={() => go({ types: toggle(S.types, t.type) })}>
              {t.zh}<span className="n">{fmtN(t.posts)}</span></button>
          ))}</div>
        </div>
        <div className="fgroup"><h3>媒體<button type="button" onClick={() => go({ sites: [] })}>清除</button></h3>
          <input className="sites-find" type="search" placeholder="找媒體，例如 明報" aria-label="找媒體" value={find} onChange={e => setFind(e.target.value)} />
          <div className="sites">
            {meta.isError ? <p className="note err">未能載入媒體列表。</p> : !M ? <Wait className="note" what="載入媒體列表" onRetry={() => meta.refetch()} /> : (() => {
              const out: ReactNode[] = [];
              for (const g of groups) {
                const xs = g.sites.filter(x => !f || x.site.toLowerCase().includes(f)).slice(0, f ? 200 : 60);
                if (!xs.length) continue;
                out.push(<div key={"g" + g.type} className="tg">{g.zh}</div>);
                for (const x of xs) out.push(
                  <label key={g.type + "|" + x.site}><input type="checkbox" checked={S.sites.includes(x.site)} onChange={() => go({ sites: toggle(S.sites, x.site) })} />
                    <span>{x.site}</span><small>{fmtN(x.posts)}</small></label>);
              }
              return out.length ? out : <p className="note">沒有符合的媒體。</p>;
            })()}
          </div>
          {S.sites.length > 0 && <div className="picked cellgrid ragged">{S.sites.map(x => (
            <button key={x} type="button" className="chip" aria-pressed="true" onClick={() => go({ sites: toggle(S.sites, x) })}>{x} ✕</button>
          ))}</div>}
        </div>
        <div className="fgroup"><h3>地區<button type="button" onClick={() => go({ regions: [] })}>全部</button></h3>
          <div className="chips cellgrid ragged">{M && Object.entries(M.regions).map(([k, v]) => (
            <button key={k} type="button" className="chip" aria-pressed={S.regions.includes(k)} onClick={() => go({ regions: toggle(S.regions, k) })}>{v}</button>
          ))}</div>
        </div>
        <div className="fgroup"><h3>排序</h3>
          <select className="sort" aria-label="排序" value={S.sort} onChange={e => go({ sort: e.target.value })}>
            <option value="date">最新優先</option><option value="oldest">最舊優先</option><option value="relevance">相關度</option>
          </select>
        </div>
      </div>
    </div>
  );

  const d: SearchResult | undefined = has && !res.isError ? res.data : undefined;
  const busy = has && res.isFetching;
  const err = has && res.isError ? (res.error as Error).message || "未能搜尋" : null;
  const exportMax = M ? M.export_max : 5000;
  const pages = d ? Math.ceil(d.total / d.n) : 0;

  let rhead: ReactNode = null, facets: ReactNode = null, body: ReactNode;
  if (busy) rhead = <Wait inline className="took" what="搜尋" label="搜尋中…" onRetry={() => res.refetch()} />;
  if (err && !busy) body = <p className="note err">{err}</p>;
  else if (!has) body = <p className="note">輸入關鍵字，或直接按「搜尋」列出期間內所有文章。</p>;
  else if (!d) body = null;
  else {
    if (!busy) rhead = <>
      <span className="count">共 {fmtN(d.total)} 篇</span><span className="took">{d.from} 至 {d.to}</span><span className="took">{(d.took_ms / 1000).toFixed(2)} 秒</span><span className="sp" />
      {d.total > 0 && <a className="btn" href={"/api/export/search.xlsx?" + searchQS(s, true)} download>
        <svg className="i" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></svg>
        匯出 Excel{d.total > exportMax ? `（首 ${fmtN(exportMax)} 篇）` : ""}</a>}
    </>;
    const ft = Object.entries(d.facets.types), fs = Object.entries(d.facets.sites).slice(0, 15);
    facets = d.total ? <>
      {S.types.length === 1 ? null : ft.map(([k, n]) => (
        <button key={"t" + k} type="button" className="chip" onClick={() => go({ types: [k] })}>{tzh[k] || k}<span className="n">{fmtN(n)}</span></button>))}
      {S.sites.length === 1 ? null : fs.map(([k, n]) => (
        <button key={"s" + k} type="button" className="chip" onClick={() => go({ sites: [k] })}>{k}<span className="n">{fmtN(n)}</span></button>))}
    </> : null;
    body = d.posts.length ? d.posts.map((x, i) => (
      <Fragment key={x.id}>
        {x.match === "substring" && (i ? d.posts[i - 1].match === "word" : d.page === 1) &&
          <p className="note tier">以下文章只按字串相符（字典未收的名稱或新詞，或字詞分界不同）</p>}
        <article className="hit">
          <h2><SiteLink to={`/item/${x.id}`}><AiBadge x={x} /><Txt s={x.title}><Marked text={x.title || ""} terms={d.terms} /></Txt></SiteLink></h2>
          <div className="m"><b>{x.website || ""}</b>
            {x.board && x.board !== x.website && x.board !== x.author ? <span>{x.board}</span> : null}
            {x.author ? <span>{x.author}</span> : null}
            <span>{when(x.time)}</span>
            <span className="t">{tzh[x.source_type || ""] || x.source_type || ""}</span>{x.comment && <span>留言</span>}
            <a href={x.link} target="_blank" rel="noopener noreferrer">原文</a></div>
          {x.extract ? <p lang={langOf(x.extract)}><Marked text={x.extract} terms={d.terms} /></p> : null}
        </article>
      </Fragment>
    )) : <p className="note">沒有符合的文章。試試放寬日期、媒體類型或改用 OR（空格）連接字詞。</p>;
  }

  return (
    <Shell section="search" title="搜尋" pick="none">
      <main className="search-main">
        <form role="search" autoComplete="off" onSubmit={e => { e.preventDefault(); qIn.current?.blur(); go({ q: qText.trim() }); }}>
          <div className="qrow">
            <label className="qbox"><Icon name="search" />
              <span className="yyc-vh">查詢</span>
              <input ref={qIn} type="search" enterKeyHint="search" value={qText} onChange={e => setQText(e.target.value)}
                placeholder='關鍵字，例如 港投公司 AND (半導體 OR 晶片) NOT "預告"' /></label>
            <button className="go" type="submit">搜尋</button>
          </div>
          <div className="hint">
            <label><input type="checkbox" checked={S.title} onChange={e => go({ title: e.target.checked })} /> 只搜標題</label>
            <label><input type="checkbox" checked={S.comments} onChange={e => go({ comments: e.target.checked })} /> 包括留言及回覆</label>
            <button type="button" className="syntax-toggle" aria-expanded={syntax} onClick={() => setSyntax(!syntax)}>查詢語法</button>
          </div>
          {syntax && <div className="syntax-box">
            <div><code>港投 滙豐</code><span>空格 = 或（任何一個字詞）</span></div>
            <div><code>港投 AND 晶片</code><span>兩個都要有；<code>OR</code> 或；<code>NOT</code> 排除；用括號分組</span></div>
            <div><code>"施政報告"</code><span>完整詞組</span></div>
            <div><code>陳茂波 NEARBY/20 預算</code><span>兩個字詞相距 20 字以內</span></div>
            <div><code>FindIn(title: 房屋)</code><span>只在標題（title）、內文（content）、作者（author）或來源（source）找</span></div>
            <div><code>IncData(明報, 信報)</code><span>只限這些媒體或媒體類型；<code>ExcData(…)</code> 排除</span></div>
          </div>}
        </form>
        <div className="layout">
          {filters}
          <section aria-live="polite">
            <div className="rhead">{rhead}</div>
            <div className="facets">{facets}</div>
            <div className="list">{body}</div>
            {d && pages > 1 && <div className="pager">
              <button type="button" className="btn" disabled={d.page <= 1} onClick={() => go({ page: d.page - 1 })}><Icon name="prev" />上一頁</button>
              <span>第 {d.page} / {fmtN(pages)} 頁</span>
              <button type="button" className="btn" disabled={d.page >= pages} onClick={() => go({ page: d.page + 1 })}>下一頁<Icon name="next" /></button>
            </div>}
            <p className="lic">結果只列標題、一小段摘錄及原文連結；全文按連結到出版機構網站閱讀。搜尋範圍是本系統收集的所有新聞、電視/電台、官方、專欄、討論區及社交媒體帖子。</p>
          </section>
        </div>
      </main>
    </Shell>
  );
}
