// 串流: one stream, one HKT day per page; today refreshes live. The page mirrors the stream's Telegram/WhatsApp groups
// (Deyao, 2026-10-20): the day, the order (newest first) and each row's time are the notification's, when the item was
// filed and sent (item.seen; server/feed.ts), and under the day picker a row shows the time only (dt.onDay). Stream, day, search text and
// the sentiment/label filters are search params (?p=&d=&q=&sent=&lab=), so every view is a link and Back works.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";
import { useProfile } from "../shell/profile";
import { feedQuery, keys, scoresQuery, useMe } from "../queries";
import { useLive } from "../live";
import { type Feed, type FeedItem, type StreamCounts } from "../api";
import { Icon } from "../lib/icons";
import { AiBadge } from "../lib/AiBadge";
import { LABELS, LABEL_ZH, SENT, SENT_VAR, SENT_ZH } from "../lib/zh";
import { BiasNote } from "../lib/BiasNote";
import { dayStart } from "../lib/time";
import { dt, halfHour } from "../lib/dates";
import { EVERY, RETRY, freshLabel, freshState } from "../lib/freshness";
import { langOf, Txt } from "../lib/lang";

/** when the item was filed and sent to the stream's groups (sending follows filing within seconds) */
const sent = (it: FeedItem) => new Date(it.seen);
const csv = (s?: string) => (s ? s.split(",").filter(Boolean) : []);
const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter(x => x !== v) : list.concat(v));

export function StreamPage() {
  const search = useSearch({ from: "/" });
  const navigate = useNavigate({ from: "/" });
  const qc = useQueryClient();
  const prof = useProfile();
  const me = useMe().data;

  const date = search.d ?? null;  // null = today (live)
  const feedQ = useQuery({
    ...feedQuery(date), placeholderData: keepPreviousData,
    retry: date === null ? false : 1,  // today: the next read is at most 15 s away (a retry waits for focus, and kept the label on 即時更新)
    // today: read every minute (every 15 s while not current), whatever the LiveHub socket says; a past day: only to retry
    refetchInterval: q => (date === null ? (q.state.status === "error" || Date.now() - q.state.dataUpdatedAt > EVERY + RETRY ? RETRY : EVERY)
      : q.state.status === "error" ? 60_000 : false),
  });
  const data = feedQ.data;
  const isToday = !data || data.date === data.today;
  useLive(keys.feed(date), { enabled: date === null });

  // approved accounts: each item's relevance score (0–100) and the model's reason, which the feed (public) leaves out
  // (usually here already: the route's loader reads them with the feed, lib/load.ts loadGated)
  const scoresQ = useQuery({ ...scoresQuery(date), enabled: !!me });
  useLive(keys.scores(date), { enabled: !!me && date === null });
  const scores = scoresQ.data || {};

  // the stream shown = the picker's; an unknown one falls back to the first stream
  const want = search.p ?? prof.effective;
  const stream = data ? (data.streams.some(s => s.id === want) ? want! : data.streams[0]?.id) : want;

  const fSent = csv(search.sent), fLabel = csv(search.lab), q = (search.q || "").toLowerCase();
  const [panel, setPanel] = useState(!!(search.q || search.sent || search.lab));
  const qInput = useRef<HTMLInputElement>(null);

  const setFilter = (patch: { q?: string; sent?: string[]; lab?: string[] }) =>
    navigate({
      search: prev => ({
        ...prev,
        ...("q" in patch ? { q: patch.q || undefined } : {}),
        ...(patch.sent ? { sent: patch.sent.join(",") || undefined } : {}),
        ...(patch.lab ? { lab: patch.lab.join(",") || undefined } : {}),
      }),
      replace: true,
    });

  // the URL always carries the stream shown (?p=), so Back returns to the stream shown at that entry
  useEffect(() => {
    if (!search.p && stream && data) navigate({ search: prev => ({ ...prev, p: stream }), replace: true });
  }, [search.p, stream, data, navigate]);

  // ---- days ----
  const days = data ? data.days.filter(d => d < data.today).concat(data.today) : [];
  const prev = data ? days.filter(d => d < data.date).pop() : undefined;
  const next = data ? days.find(d => d > data.date) : undefined;
  const dayParam = (d: string) => (data && d >= data.today ? undefined : d);
  const goDay = (d?: string) => { if (d && data && d !== data.date) navigate({ search: p => ({ ...p, d: dayParam(d) }) }); };
  const prefetch = (d?: string) => { if (d) qc.prefetchQuery(feedQuery(dayParam(d) ?? null)); };

  // ---- the list ----
  // in the server's order: the groups' send order, newest first
  const all = useMemo(() => (data ? data.items.filter(it => it.stream === stream) : []), [data, stream]);
  const shown = all.filter(it => {
    if (fSent.length && !fSent.includes(it.sentiment || "")) return false;
    if (fLabel.length && !fLabel.some(l => (it.labels || []).includes(l))) return false;
    if (q && !(it.title + " " + (it.original_title || "") + " " + it.source + " " + (it.section || "")).toLowerCase().includes(q)) return false;
    return true;
  });

  // keep the item the reader is looking at in place when today's refresh adds items above it
  const anchor = useRef<{ url: string; top: number } | null>(null);
  const listEl = useRef<HTMLDivElement>(null);
  const measure = () => {
    const el = [...(listEl.current?.querySelectorAll<HTMLElement>(".item") || [])].find(a => a.getBoundingClientRect().top > 84);
    anchor.current = el ? { url: el.dataset.url!, top: el.getBoundingClientRect().top } : null;
  };
  useEffect(() => { addEventListener("scroll", measure, { passive: true }); return () => removeEventListener("scroll", measure); }, []);
  useLayoutEffect(() => {
    const a = anchor.current;
    if (a && scrollY > 0) {
      const el = [...(listEl.current?.querySelectorAll<HTMLElement>(".item") || [])].find(x => x.dataset.url === a.url);
      if (el) scrollBy(0, el.getBoundingClientRect().top - a.top);
    }
    measure();
  }, [data]);
  // another stream or day: from the top (not when the page first renders: a reload or Back keeps the reader's place)
  const shownKey = useRef<string | null>(null);
  useEffect(() => {
    if (!stream) return;
    const k = stream + "|" + (date ?? "");
    if (shownKey.current !== null && shownKey.current !== k) { scrollTo(0, 0); anchor.current = null; }
    shownKey.current = k;
  }, [stream, date]);

  const tools = (
    <button className="yyc-tool" aria-pressed={panel} aria-label="搜尋及篩選" title="搜尋及篩選"
      onClick={() => { setPanel(!panel); if (!panel) setTimeout(() => qInput.current?.focus(), 0); }}>
      <Icon name="find" />
    </button>
  );
  const dayRow = (
    <div className="days" role="group" aria-label="日期">
      <button aria-label="前一日" disabled={!prev} onClick={() => goDay(prev)} onPointerEnter={() => prefetch(prev)} onFocus={() => prefetch(prev)}><Icon name="prev" /></button>
      <label title="選擇日期">
        <Icon name="calendar" /><span className="when">{data ? dt.date(data.date) : "—"}</span>{data && isToday && <span className="tday">今日</span>}
        <input type="date" aria-label="選擇日期" min={days[0]} max={data?.today} value={data?.date ?? ""}
          onChange={e => goDay(e.target.value)}
          onClick={e => { try { (e.target as HTMLInputElement).showPicker(); } catch { /* not supported */ } }} />
      </label>
      <button aria-label="後一日" disabled={!next} onClick={() => goDay(next)} onPointerEnter={() => prefetch(next)} onFocus={() => prefetch(next)}><Icon name="next" /></button>
      {isToday ? <Fresh q={feedQ} />
        : <button className="today" onClick={() => goDay(data!.today)} onPointerEnter={() => prefetch(data?.today)}>回到今日</button>}
    </div>
  );

  const chips = (
    <>
      <div className="chips cellgrid ragged">{SENT.map(s => (
        <button key={s} className="chip" aria-pressed={fSent.includes(s)} onClick={() => setFilter({ sent: toggle(fSent, s) })}><Icon name={s} />{SENT_ZH[s]}</button>
      ))}</div>
      <div className="chips cellgrid ragged">{LABELS.map(l => (
        <button key={l} className="chip" aria-pressed={fLabel.includes(l)} onClick={() => setFilter({ lab: toggle(fLabel, l) })}><Icon name={l} />{LABEL_ZH[l]}</button>
      ))}</div>
    </>
  );

  const st = data?.source_status || {}, bad = Object.entries(st).filter(([, v]) => !v.ok && !v.off);  // a source off on purpose is not failing

  return (
    <Shell section="stream" title="串流" pick="side" tools={tools} rows={dayRow}>
      {panel && (
        <div className="panel">
          <label className="search"><Icon name="find" />
            <input ref={qInput} type="search" placeholder="搜尋標題及來源" autoComplete="off" defaultValue={search.q || ""}
              onChange={e => setFilter({ q: e.target.value.trim() })} />
          </label>
          {chips}
        </div>
      )}
      <div className="wrap">
        <aside className="side" aria-label="監察串流" data-figure="streams">
          <h2>監察串流</h2>
          {data?.streams.map(s => {
            const c = data.counts?.[s.id] || { day: 0 };
            return (
              <Link key={s.id} className="stream" data-p={s.id} to="/" search={prev => ({ ...prev, p: s.id })}
                aria-current={s.id === stream ? "page" : undefined} onClick={() => prof.set(s.id, true)}>
                <span className="n"><span className="name">{s.name}</span><span className="num">{c.day} 則</span></span>
                <span className="sub">{s.title}</span><SplitBar c={c} />
              </Link>
            );
          })}
          <h2>篩選</h2>
          {chips}
        </aside>
        <main>
          {data && <Strip data={data} items={all} isToday={isToday} />}
          {data && <ul className="legend" aria-hidden="true">{SENT.map(s => <li key={s}><span className={"sw " + s} />{SENT_ZH[s]}</li>)}<li><span className="sw none" />未有情緒</li></ul>}
          {isToday && <FreshWhy q={feedQ} />}
          <div className="status">
            {feedQ.isError && !data ? <span>伺服器沒有回應，每分鐘重試。</span>
              : !data ? <span>載入中…</span>
              : <><span>顯示 {shown.length} / {all.length} 則</span>
                  <span>{data.last_poll ? dt.when(data.last_poll) : "—"} 檢查了 {data.sources} 個來源{bad.length ? `，${bad.length} 個失敗` : ""}</span></>}
          </div>
          <div className={"list" + (feedQ.isPlaceholderData ? " dim" : "")} ref={listEl}>
            {data && <ItemList data={data} stream={stream!} shown={shown} total={all.length} isToday={isToday} scores={scores} />}
          </div>
        </main>
        <aside className="summary" aria-label="摘要" data-figure="stream-summary">{data && <Summary data={data} stream={stream!} items={all} isToday={isToday} />}</aside>
      </div>
    </Shell>
  );
}

function ItemList({ data, stream, shown, total, isToday, scores }: {
  data: Feed; stream: string; shown: FeedItem[]; total: number; isToday: boolean; scores: Record<string, { score: number; reason?: string }>;
}) {
  if (!shown.length) {
    return <div className="empty">{total ? "沒有符合這些篩選條件的新聞。" : isToday ? "這個串流今日還沒有新聞，來源發佈後會自動出現。" : "這個串流當日沒有新聞。"}</div>;
  }
  // one list in the groups' send order, newest first, with the half-hour rows, for every stream (Deyao, 2026-10-20: a
  // stream that sorts its items into categories, streams.json `categories` set by the scorer, shows the category as a
  // label on the card, as the group message carries it in bold above the headline; no category headings)
  const hasCats = !!data.streams.find(s => s.id === stream)?.categories?.length;
  const out: React.ReactNode[] = [];
  let slot = "";
  for (const it of shown) {
    const s = halfHour(sent(it));
    if (s !== slot) {
      slot = s;
      out.push(<div key={"s" + s} className="slot">{dt.onDay(s, data.date)}</div>);
    }
    const sc = scores[it.stream + "|" + it.url];
    out.push(
      <Link key={it.stream + it.url} className="item" to="/item" search={{ p: it.stream, url: it.url }} data-url={it.url}>
        <span className={"sm " + (it.sentiment || "none")} aria-hidden="true" />
        {hasCats && it.category && <span className="cat">{it.category}</span>}
        <span className="t"><AiBadge x={it} /><Txt s={it.title} /></span>
        {(it.title_by_ai || it.title_linked) && it.text && <span className="ai-orig" lang={langOf(it.text)}>{it.text.slice(0, 300)}</span>}
        {sc && <span className="why"><b>{sc.score}</b> {sc.reason || ""}</span>}
        <span className="meta"><span className="src">{it.source}</span>{it.section && it.section !== it.source && <span>{it.section}</span>}<span className="num">{dt.onDay(it.seen, data.date)}</span>
          {it.sentiment && <span className={"tag " + it.sentiment}><Icon name={it.sentiment} />{SENT_ZH[it.sentiment]}</span>}
          {(it.labels || []).map(l => <span key={l} className={"tag " + l}><Icon name={l} />{LABEL_ZH[l] || l}</span>)}</span>
      </Link>,
    );
  }
  return <>{out}</>;
}

/** the feed's freshness on the browser's clock (lib/freshness.ts): live, or when the data is from and why it isn't current.
 *  null state = the server render (it says 即時更新, no data-state) */
type FreshQ = { data?: Feed; dataUpdatedAt: number; errorUpdatedAt: number };
function useFresh(q: FreshQ) {
  const [now, setNow] = useState<number | null>(null);  // the browser's clock only
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const t = setInterval(tick, 10_000);
    const evs = ["online", "offline", "pageshow", "focus"];
    for (const e of evs) addEventListener(e, tick);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(t); for (const e of evs) removeEventListener(e, tick); document.removeEventListener("visibilitychange", tick); };
  }, []);
  const d = q.data;
  const okAt = q.dataUpdatedAt, served = d?.served_at ? Date.parse(d.served_at) : NaN;
  const st = now == null ? "live" : freshState({
    okAt, now, online: navigator.onLine !== false, lastPoll: d?.last_poll ?? null,
    failed: q.errorUpdatedAt > okAt ? "failed" : "",
    skew: served > 0 && okAt > 0 ? served - okAt : 0,
  });
  return { ...freshLabel(st, d?.last_poll ?? null), st, ssr: now == null };
}

/** 即時更新, or when the data is from and why it isn't current; on a phone the why goes to a ruled row of its own above
 *  the status row (FreshWhy) so the day row stays one 44px line */
function Fresh({ q }: { q: FreshQ }) {
  const { at, why, title, st, ssr } = useFresh(q);
  return <span className="fresh" role="status" data-state={ssr ? undefined : st} title={ssr ? undefined : title}>
    {st === "live" ? "即時更新" : <span className="lt"><span>資料截至 {at}</span><span className="lw">{why}</span></span>}
  </span>;
}
function FreshWhy({ q }: { q: FreshQ }) {
  const { why, st } = useFresh(q);
  return st === "live" ? null : <p className="fwhy">{why}</p>;
}

/** 00:00–24:00 of the day, one tick per item coloured by sentiment (negative ones taller); a dashed line at now */
function Strip({ data, items, isToday }: { data: Feed; items: FeedItem[]; isToday: boolean }) {
  const ref = useRef<SVGSVGElement>(null);
  const [w, setW] = useState(600);
  const [now, setNow] = useState<number | null>(null);  // the browser's clock only (the server render has no "now" line)
  useEffect(() => { setNow(Date.now()); const t = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(t); }, []);
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth || 600)); ro.observe(el); setW(el.clientWidth || 600);
    return () => ro.disconnect();
  }, []);
  const h = 44, pad = 16, from = dayStart(data.date).getTime(), to = from + 24 * 3600e3;
  const x = (t: number) => pad + ((w - 2 * pad) * (t - from)) / (to - from);
  const col: Record<string, string> = { positive: "var(--sent-pos)", negative: "var(--sent-neg)", mixed: "var(--sent-mixed)", irony: "var(--sent-irony)" };
  return (
    <svg ref={ref} className="strip" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="當日 00:00 至 24:00，每則一條線，顏色代表情緒">
      <line x1={pad} x2={w - pad} y1={28} y2={28} strokeWidth={1} style={{ stroke: "var(--ink)" }} />
      {[0, 6, 12, 18, 24].map(hr => {
        const xx = x(from + hr * 3600e3);
        return <g key={hr}>
          <line x1={xx} x2={xx} y1={28} y2={32} strokeWidth={2} style={{ stroke: "var(--ink)" }} />
          <text x={xx} y={43} fontSize={12} style={{ fill: "var(--muted)" }} textAnchor={hr === 0 ? "start" : hr === 24 ? "end" : "middle"}>{String(hr).padStart(2, "0")}:00</text>
        </g>;
      })}
      {isToday && now != null && <line x1={x(now)} x2={x(now)} y1={2} y2={32} strokeWidth={2} style={{ stroke: "var(--ink)" }} strokeDasharray="4 3"><title>now</title></line>}
      {items.map(it => {
        const t = sent(it).getTime(); if (t < from || t >= to) return null;
        const tall = it.sentiment === "negative" ? 22 : 14;
        return <line key={it.url} x1={x(t)} x2={x(t)} y1={28 - tall} y2={28} strokeWidth={2} style={{ stroke: col[it.sentiment || ""] || "var(--sent-neu)" }}>
          <title>{dt.time(t) + " " + it.title}</title></line>;
      })}
    </svg>
  );
}

/** a stream's sentiment shares that day (the bar under its name) */
function SplitBar({ c }: { c: StreamCounts }) {
  const n = (s: string) => (c as unknown as Record<string, number>)[s] || 0;
  const t = SENT.reduce((a, s) => a + n(s), 0) || 1;
  return (
    <span className="split" aria-hidden="true">
      {SENT.slice().reverse().map(s => <i key={s} style={{ width: `${(100 * n(s)) / t}%`, background: `var(--${SENT_VAR[s]})` }} />)}
    </span>
  );
}

/** the right-hand column: the day's sentiment counts, labels and main sources of the stream shown; they count our
 *  items one per vote, so the column ends with the selection-bias note, once (the stream list's bars beside it are the
 *  same kind of count; Deyao 2026-10-20: one note, not a 「?」 per heading) */
function Summary({ data, stream, items, isToday }: { data: Feed; stream: string; items: FeedItem[]; isToday: boolean }) {
  const lab: Record<string, number> = {}, src: Record<string, number> = {};
  for (const it of items) { for (const l of it.labels || []) lab[l] = (lab[l] || 0) + 1; src[it.source] = (src[it.source] || 0) + 1; }
  const c = (data.counts?.[stream] || {}) as unknown as Record<string, number>;
  return (
    <>
      <h2>情緒（{isToday ? "今日" : dt.date(data.date)}）</h2>
      {SENT.map(s => <div key={s} className="kv"><span><span className={"sw " + s} aria-hidden="true" />{SENT_ZH[s]}</span><span className="num">{c[s] || 0}</span></div>)}
      <h2>標籤</h2>
      {LABELS.filter(l => lab[l]).map(l => <div key={l} className="kv"><span><Icon name={l} />{LABEL_ZH[l]}</span><span className="num">{lab[l]}</span></div>)}
      <h2>主要來源</h2>
      {Object.entries(src).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, n]) => <div key={k} className="kv"><span>{k}</span><span className="num">{n}</span></div>)}
      <BiasNote />
    </>
  );
}
