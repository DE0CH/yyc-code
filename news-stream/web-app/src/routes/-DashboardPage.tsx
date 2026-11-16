// 數據分析: 摘要 · 帖子 · 熱門話題 · 作者 · 來源 · 趨勢 · 主題 · 人物 (?v=), then the period row. Every state is in the URL
// (KEYS); a drill-down (a topic, an author) is its own history entry, so Back returns to where the reader was
// (NAVIGATION.md "Drill-downs and Back"). Every view with figures that count posts one per vote (摘要's metrics,
// trend and scores, 熱門詞, 情緒分佈, 作者, 來源, 趨勢, a topic's top authors and sources, the most active authors) opens
// with one line saying they depend on which sources and keywords we follow (<BiasNote />, once per view, no 「?」 per
// figure: Deyao 2026-10-16 and 2026-10-20; README "Figures that depend on our selection"); each such section is marked
// data-figure. The persona's AI button (approved accounts, the `ai` flag) runs on the cluster: /api/ai/estimate,
// /app/ai/persona.
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery, useQueryClient, type FetchQueryOptions } from "@tanstack/react-query";
import { useLocation, useNavigate, useSearch } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";
import { useRemembered } from "../shell/profile";
import { useFlags, useMe } from "../queries";
import { authorsQuery, contextQuery, dailyQuery, hotQuery, metaQuery, personaQuery, postsQuery, scoresQuery, sourcesQuery, summaryQuery, termsQuery, themesQuery, topicQuery, trendQuery } from "../queries-dashboard";
import { aiPost, getEstimate, type AiDone, type AuthorRow, type DailyData, type DashPost, type DashQ, type HotTopic, type Meta, type PersonaData, type PostsData, type ScoresData, type SourceRow, type SummaryData, type Term, type ThemesData, type TopicData, type TrendRow } from "../api-dashboard";
import { BiasNote } from "../lib/BiasNote";
import { HttpError } from "../api";
import { Icon } from "../lib/icons";
import { SiteLink } from "../lib/SiteLink";
import { AiBadge } from "../lib/AiBadge";
import { dayAdd } from "../lib/time";
import { dt } from "../lib/dates";
import { langOf, Txt } from "../lib/lang";

// ---------------- vocabulary ----------------
const VIEWS = [["summary", "摘要", "Summary"], ["posts", "帖子", "Post Listing"], ["hot", "熱門話題", "Hot Topic"], ["authors", "作者", "Top Author"],
  ["sources", "來源", "Top Source"], ["trend", "趨勢", "Trend"], ["themes", "主題", "Themes"], ["persona", "人物", "Persona"]] as const;
const SENT = ["positive", "neutral", "negative", "mixed", "irony"];
const SENT_ZH: Record<string, string> = { positive: "正面", neutral: "中性", negative: "負面", mixed: "褒貶參半", irony: "反諷" };
const SENT_C: Record<string, string> = { positive: "--sent-pos", neutral: "--sent-neu", negative: "--sent-neg", mixed: "--sent-mixed", irony: "--sent-irony" };
const LABELS = ["repute", "goods", "service", "rival", "issue", "crisis", "kol", "engaging", "trend", "ask", "review", "accuse", "praise", "rumor", "urgent", "chance", "monitor"];
const LABEL_ZH: Record<string, string> = { repute: "聲譽", goods: "商品", service: "服務", rival: "競爭對手", issue: "議題", crisis: "危機", kol: "KOL", engaging: "熱議",
  trend: "趨勢", ask: "訴求", review: "評價", accuse: "指控", praise: "讚揚", rumor: "傳聞", urgent: "緊急", chance: "機遇", monitor: "監察" };
const LEVEL_ZH: Record<string, string> = { "Very Low": "極低", "Low": "低", "Medium": "中", "High": "高", "Very High": "極高", "Very Negative": "非常負面", "Slightly Negative": "略為負面", "Neutral": "中性", "Slightly Positive": "略為正面", "Very Positive": "非常正面" };
const SCORE_ZH: Record<string, string> = { SPI: "社交熱度指數", CSS: "消費者情緒", DVS: "討論量", DPS: "討論參與度", CCS: "消費者覆蓋" };
const NOTE: Record<string, string> = {  // what each metric is (insights.metrics); scores and the sentiment index use the server's METHOD text
  posts: "期內符合設定的帖子數。", new_posts: "期內帖子之中，開始日前 30 日未曾出現的帖子。",
  topics: "按標題相似度把帖子歸為話題（同一論壇主題，或多個媒體報道的同一新聞）。", new_topics: "首篇帖子在開始日前 30 日未曾出現的話題。",
  websites: "帖子來自的不同網站數目。", boards: "帖子來自的不同版面（網站 + 版面）數目。",
  authors: "發帖的不同作者數目；沒有作者名稱的帖子，每個網站算作一位作者。", view_count: "來源提供的瀏覽數總和；沒有來源提供瀏覽數時顯示 N/A。",
  website_coverage: "本設定的網站數 ÷ 同期資料庫所有網站數。", posts_spread: "本設定的帖子數 ÷ 同期資料庫所有帖子數。",
  per_day: "總數 ÷ 期內日數。", sentiment_balance: "正面帖子數 − 負面帖子數。" };
const TYPE_ZH: Record<string, string> = { "News": "新聞", "Newspaper": "報章", "Magazine": "雜誌", "Forum": "論壇", "Social Media": "社交媒體", "Video": "影片", "Blog": "網誌", "Official": "官方" };

// the page's own icons beyond lib/icons
const P: Record<string, string> = {
  ai: '<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/><path d="M19 16v5M16.5 18.5h5"/>',
  link: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  cached: '<path d="M4 7h16v13H4zM3 4h18v3H3zM10 11h4"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
  back: '<path d="m15 5-7 7 7 7"/>',
  next: '<path d="m9 5 7 7-7 7"/>',
  search: '<path d="M4 4h11v11H4z"/><path d="m15 15 5 5"/>',
  person: '<path d="M8 4h8v8H8z"/><path d="M4 21v-5h16v5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  stream: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
};
function I({ n }: { n: string }) {
  if (!P[n]) return <Icon name={n} />;
  return <svg className="i" viewBox="0 0 24 24" aria-hidden="true" dangerouslySetInnerHTML={{ __html: P[n] }} />;
}

// ---------------- helpers ----------------
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
/** toLocaleString("en-US") by hand (hydration-safe): thousands grouped, up to 3 decimals */
function grouped(v: number) {
  const neg = v < 0, r = Math.round(Math.abs(v) * 1000) / 1000;
  const [i, f] = String(r).split(".");
  return (neg ? "-" : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (f ? "." + f : "");
}
const fmt = (v: unknown) => (v == null ? "N/A" : typeof v === "number" ? grouped(v) : String(v));
const pct = (v: number | null | undefined) => (v == null ? "N/A" : `${v > 0 ? "+" : ""}${v}%`);
const dm = dt.axis, dlong = dt.date;
/** a post's time; with the period on one day (今日, a day picked) the time only, the picker says the day (dt.onDay;
 *  Deyao, 2026-10-20) */
const when = (x: DashPost, S?: S) => (S && S.from === S.to ? dt.onDay(x.published || x.seen, S.to) : dt.dateTime(x.published || x.seen));
const isDay = (d?: string) => /^\d{4}-\d{2}-\d{2}$/.test(d || "");
const daysOf = (from: string, to: string) => (Date.parse(to) - Date.parse(from)) / 864e5 + 1;
const isDown = (e: unknown) => !(e instanceof HttpError) || e.status >= 500;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Face({ s, txt = true }: { s?: string | null; txt?: boolean }) {
  if (!s) return null;
  return <span className={`face ${s}`} title={SENT_ZH[s] || s}><Icon name={s} />{txt ? SENT_ZH[s] || s : ""}</span>;
}
function Tag({ l, meta }: { l: string; meta: Meta }) {
  return <span className={`tag ${l}`} title={(meta.labels[l] || { meaning: "" }).meaning || ""}><Icon name={l} />{LABEL_ZH[l] || l}</span>;
}
function SiCell({ si, level }: { si: number; level: string }) {
  const k = si > 5 ? "positive" : si < -5 ? "negative" : "neutral";
  return <span className={`face ${k}`} title={LEVEL_ZH[level] || level || ""}><Icon name={k} /><span className="n">{si}</span></span>;
}
function SplitBar({ c }: { c: Record<string, number> }) {
  const t = SENT.reduce((a, s) => a + (c[s] || 0), 0);
  if (!t) return <div className="split" aria-hidden="true" />;
  return (
    <div className="split" role="img" aria-label={SENT.map(s => `${SENT_ZH[s]} ${c[s] || 0}`).join("，")}>
      {SENT.filter(s => c[s]).map(s => <i key={s} style={{ width: `${100 * c[s] / t}%`, background: `var(${SENT_C[s]})` }} title={`${SENT_ZH[s]} ${c[s]}`} />)}
    </div>
  );
}

// ---------------- state in the URL ----------------
export const KEYS = ["v", "p", "from", "to", "sent", "label", "theme", "type", "site", "author", "q", "page", "top", "by", "bucket", "a", "tid", "post"] as const;
type Key = (typeof KEYS)[number];
export type DashSearch = Partial<Record<Key, string>>;
type S = DashSearch & { v: string; p: string; from: string; to: string };
const FILTERS = ["sent", "label", "theme", "type", "site", "author", "q"] as const;

/** the defaults: the view, the profile (the URL's, else the remembered one, else the first), the period */
export function effective(search: DashSearch, meta: Meta, stored: string | null): S {
  const s: DashSearch = { ...search };
  if (!VIEWS.some(v => v[0] === s.v)) s.v = "summary";
  if (!meta.profiles.some(p => p.id === s.p)) s.p = stored && meta.profiles.some(p => p.id === stored) ? stored : meta.profiles[0].id;
  if (!isDay(s.to) || s.to! > meta.today) s.to = meta.today;
  if (!isDay(s.from)) s.from = dayAdd(s.to!, -6);
  if (s.from! > s.to!) [s.from, s.to] = [s.to, s.from];
  return s as S;
}

/** the URL's params in a fixed order */
const canon = (s: DashSearch) => {
  const o: DashSearch = {};
  for (const k of KEYS) if (s[k] != null && s[k] !== "") o[k] = s[k];
  return o;
};

/** the API query for the current profile, period and filters */
function params(S: S, extra: DashQ = {}): DashQ {
  const o: DashQ = { p: S.p, from: S.from, to: S.to };
  if (S.sent) o.sentiment = S.sent;
  for (const k of ["label", "theme", "type", "site", "author", "q"] as const) if (S[k]) o[k] = S[k];
  return Object.assign(o, extra);
}
const postsArgs = (S: S) => params(S, S.post && !S.page ? { n: 25, post: S.post } : { page: +(S.page || 1), n: 25 });
const byOf = (S: S) => (S.by === "board" ? "board" : "website");
const bucketOf = (S: S) => (S.bucket === "week" ? "week" : "day");
const qs = (o: DashQ) => new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)])).toString();

/** the queries a view needs (its route loader awaits them on the server) */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function viewQueries(S: S, aiOn: boolean): FetchQueryOptions<any, any, any, any>[] {
  const base = { p: S.p, from: S.from, to: S.to };
  if (S.v === "summary") return [summaryQuery(base), scoresQuery({ p: S.p, day: S.to, n: 7 }), termsQuery({ ...base, top: 60 }),
    ...(aiOn ? [dailyQuery({ p: S.p, day: S.to })] : [])];
  if (S.v === "authors") return [authorsQuery(params(S, { top: 100 }))];
  if (S.v === "sources") return [sourcesQuery(params(S, { top: 100, by: byOf(S) }))];
  if (S.v === "trend") return [trendQuery({ ...base, bucket: bucketOf(S) })];
  if (S.v === "hot") return S.tid ? [topicQuery(params(S, { id: S.tid }))] : [hotQuery(params(S, { top: S.top || "10" })), termsQuery(params(S, { top: 60 }))];
  if (S.v === "themes") return [themesQuery({ p: S.p, from: S.from, to: S.to }), postsQuery({ p: S.p, from: S.from, to: S.to, n: 1 })];
  if (S.v === "persona") return [authorsQuery({ ...base, top: 50 }), ...(S.a ? [personaQuery({ p: S.p, author: S.a, from: S.from, to: S.to })] : [])];
  return [postsQuery(postsArgs(S)), ...(aiOn ? [dailyQuery({ p: S.p, day: S.to })] : [])];
}

type Go = (patch: Partial<Record<Key, string | null>>, push?: boolean) => void;

interface Ctx {
  S: S; meta: Meta; go: Go; aiOn: boolean; aiUser: string | null; costTick: number; bumpCosts: () => void; here: string;
}

// a topic opened from the list on this page: its back button is the browser's Back
let topicFromList = false;

// ---------------- the page ----------------
export function DashboardPage() {
  const search = useSearch({ from: "/dashboard" }) as DashSearch;
  const navigate = useNavigate();
  const loc = useLocation();
  const metaQ = useQuery({ ...metaQuery, refetchInterval: q => (q.state.status === "error" ? 10_000 : false) });
  const meta = metaQ.data;
  const aiOn = !!(useFlags().data || {}).ai;
  const aiUser = useMe().data ?? null;
  const stored = useRemembered();  // the server read it too (shell/profile.ts)
  const [costTick, setCostTick] = useState(0);

  const S = meta && meta.profiles.length ? effective(search, meta, stored) : null;
  if (!S?.tid) topicFromList = false;

  const go: Go = (patch, push = true) => {
    if (!S) return;
    const next: DashSearch = { ...S };
    if (!("post" in patch)) delete next.post;  // ?post= (from an item page) only marks the list it opened with
    for (const [k, v] of Object.entries(patch)) { if (v == null || v === "") delete next[k as Key]; else next[k as Key] = v; }
    navigate({ to: "/dashboard", search: canon(next) as never, replace: !push, resetScroll: false });
  };

  // the URL carries the state shown (its defaults written in, with replace): Back returns to exactly this view
  const want = S ? JSON.stringify(canon(S)) : "", have = JSON.stringify(canon(search));
  useEffect(() => {
    if (!S || want === have) return;
    const t = setTimeout(() => navigate({ to: "/dashboard", search: canon(S) as never, replace: true, resetScroll: false }), 0);
    return () => clearTimeout(t);
  }, [want, have]);  // eslint-disable-line react-hooks/exhaustive-deps

  const tabs = (
    <nav className="yyc-sub" aria-label="數據分析">
      {VIEWS.map(([k, zh, en]) => (
        <button key={k} className="tab" aria-current={S ? k === S.v : false} title={en}
          onClick={() => go({ v: k, tid: null, page: null })}>{zh}</button>
      ))}
    </nav>
  );

  let body: ReactNode;
  if (!meta) {
    body = metaQ.isError
      ? <div className="sec"><div className="empty">伺服器沒有回應（{errText(metaQ.error)}），每 10 秒重試。</div></div>
      : <p className="sub">載入中…</p>;
  } else if (!S) {
    body = <div className="sec"><div className="empty">沒有設定檔。</div></div>;
  }
  const ctx: Ctx | null = S && meta ? { S, meta, go, aiOn, aiUser, costTick, bumpCosts: () => setCostTick(t => t + 1), here: loc.pathname + loc.searchStr } : null;

  return (
    <Shell section="dashboard" title="數據分析" pickDrop={["tid", "page", "post"]} rows={tabs}>
      <div className="dash">
        {S && meta && <Period S={S} today={meta.today} go={go} />}
        <div className="wrap">{ctx ? <View c={ctx} /> : <div id="view">{body}</div>}</div>
        <Tip />
      </div>
    </Shell>
  );
}

function Period({ S, today, go }: { S: S; today: string; go: Go }) {
  const days = daysOf(S.from, S.to);
  const pick = (e: React.MouseEvent<HTMLInputElement>) => { try { (e.target as HTMLInputElement & { showPicker(): void }).showPicker(); } catch { /* not supported */ } };
  return (
    <div className="period yyc-tier" id="period">
      <div className="seg" id="quick" role="group" aria-label="Quick period">
        {[1, 7, 30, 90].map(n => (
          <button key={n} data-days={n} aria-pressed={S.to === today && days === n}
            onClick={() => go({ to: today, from: dayAdd(today, -(n - 1)), page: null, tid: null })}>{n === 1 ? "今日" : n + " 日"}</button>
        ))}
      </div>
      <div className="dates range">
        <input type="date" id="from" aria-label="From" value={S.from} max={today} onClick={pick}
          onChange={e => e.target.value && go({ from: e.target.value, page: null, tid: null })} />
        <span>至</span>
        <input type="date" id="to" aria-label="To" value={S.to} max={today} onClick={pick}
          onChange={e => e.target.value && go({ to: e.target.value, page: null, tid: null })} />
      </div>
      <span className="note" id="pnote">{`${days} 日，資料至 ${dt.date(today)}`}</span>
    </div>
  );
}

// ---------------- the views ----------------
/** the view shown; while the next one loads, the last one stays, dimmed (#view.loading) */
function View({ c }: { c: Ctx }) {
  const last = useRef<ReactNode>(null);
  const { node, loading, error, retry } = useViewNode(c);
  useEffect(() => {
    if (!error || !isDown(error)) return;
    const t = setTimeout(retry, 10_000);
    return () => clearTimeout(t);
  }, [error, retry]);
  let shown: ReactNode;
  if (error) {
    const down = isDown(error);
    shown = <div className="sec"><div className={`empty ${down ? "" : "err"}`}>{down ? `伺服器沒有回應（${errText(error)}），每 10 秒重試。` : errText(error)}</div></div>;
    last.current = shown;
  } else if (node) last.current = shown = node;
  else shown = last.current ?? <p className="sub">載入中…</p>;
  return <div id="view" className={loading || (!node && !error) ? "loading" : undefined}>{shown}</div>;
}

function useViewNode(c: Ctx): { node: ReactNode; loading: boolean; error: unknown; retry: () => void } {
  const { S } = c;
  const opt = { placeholderData: keepPreviousData };
  const isPosts = !VIEWS.some(v => v[0] === S.v && v[0] !== "posts");
  const postsQ = useQuery({ ...postsQuery(postsArgs(S)), ...opt, enabled: isPosts });
  const dailyQ = useQuery({ ...dailyQuery({ p: S.p, day: S.to }), enabled: isPosts && c.aiOn });
  const hotQ = useQuery({ ...hotQuery(params(S, { top: S.top || "10" })), ...opt, enabled: S.v === "hot" && !S.tid });
  const topicQ = useQuery({ ...topicQuery(params(S, { id: S.tid })), enabled: S.v === "hot" && !!S.tid });
  const themesQ = useQuery({ ...themesQuery({ p: S.p, from: S.from, to: S.to }), ...opt, enabled: S.v === "themes" });
  const totalQ = useQuery({ ...postsQuery({ p: S.p, from: S.from, to: S.to, n: 1 }), ...opt, enabled: S.v === "themes" });
  const personaQ = useQuery({ ...personaQuery({ p: S.p, author: S.a, from: S.from, to: S.to }), ...opt, enabled: S.v === "persona" && !!S.a });
  const base = { p: S.p, from: S.from, to: S.to };
  const summaryQ = useQuery({ ...summaryQuery(base), ...opt, enabled: S.v === "summary" });
  const scoresQ = useQuery({ ...scoresQuery({ p: S.p, day: S.to, n: 7 }), ...opt, enabled: S.v === "summary" });
  const sumTermsQ = useQuery({ ...termsQuery({ ...base, top: 60 }), ...opt, enabled: S.v === "summary" });
  const sumDailyQ = useQuery({ ...dailyQuery({ p: S.p, day: S.to }), enabled: S.v === "summary" && c.aiOn });
  const hotTermsQ = useQuery({ ...termsQuery(params(S, { top: 60 })), ...opt, enabled: S.v === "hot" && !S.tid });
  const authorsQ = useQuery({ ...authorsQuery(params(S, { top: 100 })), ...opt, enabled: S.v === "authors" });
  const activeQ = useQuery({ ...authorsQuery({ ...base, top: 50 }), ...opt, enabled: S.v === "persona" });
  const sourcesQ = useQuery({ ...sourcesQuery(params(S, { top: 100, by: byOf(S) })), ...opt, enabled: S.v === "sources" });
  const trendQ = useQuery({ ...trendQuery({ ...base, bucket: bucketOf(S) }), ...opt, enabled: S.v === "trend" });

  const pick = (qs: { data?: unknown; isFetching: boolean; isPlaceholderData?: boolean; error: unknown; refetch: () => unknown }[], render: () => ReactNode) => {
    const error = qs.find(q => q.error && !q.data)?.error ?? null;
    const ready = qs.every(q => q.data !== undefined);
    return { node: ready && !error ? render() : null, loading: qs.some(q => q.isFetching), error,
      retry: () => qs.forEach(q => q.refetch()) };
  };
  if (S.v === "summary") {
    const r = pick([summaryQ, scoresQ, sumTermsQ], () => <SummaryView c={c} d={summaryQ.data as SummaryData} sc={scoresQ.data as ScoresData}
      terms={(sumTermsQ.data as { terms: Term[] }).terms} daily={c.aiOn ? (sumDailyQ.data as DailyData | undefined) ?? null : null} />);
    if (c.aiOn && sumDailyQ.isPending && sumDailyQ.fetchStatus !== "idle" && !r.error) return { ...r, node: null, loading: true };
    return r;
  }
  if (S.v === "authors") return pick([authorsQ], () => <AuthorsView c={c} rows={(authorsQ.data as { authors: AuthorRow[] }).authors} />);
  if (S.v === "sources") return pick([sourcesQ], () => <SourcesView c={c} rows={(sourcesQ.data as { sources: SourceRow[] }).sources} />);
  if (S.v === "trend") return pick([trendQ], () => <TrendView c={c} rows={(trendQ.data as { trend: TrendRow[] }).trend} />);
  if (S.v === "hot" && S.tid) return pick([topicQ], () => <TopicView c={c} d={topicQ.data as TopicData} />);
  if (S.v === "hot") return pick([hotQ], () => <HotView c={c} d={(hotQ.data as { topics: HotTopic[] })} terms={hotTermsQ} />);
  if (S.v === "themes") return pick([themesQ, totalQ], () => <ThemesView c={c} d={themesQ.data as ThemesData} total={(totalQ.data as PostsData).total} />);
  if (S.v === "persona") {
    const active = (activeQ.data as { authors: AuthorRow[] } | undefined)?.authors ?? [];
    if (!S.a) return pick([activeQ], () => <PersonaEmpty c={c} active={active} />);
    return pick([personaQ], () => <PersonaView c={c} d={personaQ.data as PersonaData} active={active} />);
  }
  const r = pick([postsQ], () => <PostsView c={c} d={postsQ.data as PostsData} daily={c.aiOn ? (dailyQ.data as DailyData | undefined) ?? null : null} />);
  // the old page waited for the daily summary with the list (Promise.all)
  if (c.aiOn && dailyQ.isPending && dailyQ.fetchStatus !== "idle" && !r.error) return { ...r, node: null, loading: true };
  return r;
}

// ---------------- AI ----------------
const AI_NOTE = (here: string) => (
  <>AI 功能只供 Deyao 批准的帳戶使用，公司統一登入建設中。請向 Deyao 申請，<a href={`/app/login?next=${encodeURIComponent(here)}`}>已獲批准請登入</a>。</>
);
const usd = (x: unknown) => `USD ${Number(x || 0).toFixed(4)}`;

function aiArgs(c: Ctx, _id: string): [string, DashQ] {  // [endpoint, body] of an AI button: the same for its projection and its run
  const { S } = c;
  return ["persona", { p: S.p, author: S.a, from: S.from, to: S.to }];
}

/** before a press: the model and the projected cost (no model call); not cached: a run changes it */
function Cost({ c, id }: { c: Ctx; id: string }) {
  const [kind, body] = aiArgs(c, id);
  const key = JSON.stringify([kind, body]);
  const [est, setEst] = useState<{ text: string; title: string }>({ text: "", title: "" });
  useEffect(() => {
    let live = true;
    getEstimate(Object.assign({ kind }, body)).then(e => {
      if (live) setEst({ text: e.cached ? `${e.model_name}，已有結果，不收費` : `${e.model_name}，預計 ${usd(e.usd)}`,
        title: e.cached ? "" : `估算：約 ${e.input_tokens} 輸入 token、${e.output_tokens} 輸出 token，按 OpenRouter 的標價` });
    }).catch(() => { if (live) setEst({ text: "", title: "" }); });
    return () => { live = false; };
  }, [key, c.costTick]);  // eslint-disable-line react-hooks/exhaustive-deps
  return <span className="ai-cost" data-est={id} title={est.title || undefined}>{est.text}</span>;
}

/** live preview while text streams in (the server's HTML replaces it at the end) */
function mdLive(md: string) {
  const inl = (s: string) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  let out = "", ul = false;
  for (const raw of md.replace(/^FOLLOWUPS:.*$/m, "").split("\n")) {
    const s = raw.trim(), li = s.match(/^[-*•]\s+(.*)/), h = s.match(/^#{1,4}\s+(.*)/);
    if (li) { if (!ul) { out += "<ul>"; ul = true; } out += `<li>${inl(li[1])}</li>`; continue; }
    if (ul) { out += "</ul>"; ul = false; }
    if (h) out += `<h3>${inl(h[1])}</h3>`; else if (s) out += `<p>${inl(s)}</p>`;
  }
  return out + (ul ? "</ul>" : "") + '<span class="caret"></span>';
}

type AiOut = { live?: string; done?: AiDone; error?: string } | null;

/** an AI button (approved accounts) and its projected cost; the answer goes to the section's #ai-out */
function AiBlock({ c, id, label, onRun }: { c: Ctx; id: string; label: string; onRun: (o: AiOut) => void }) {
  const [busy, setBusy] = useState(false);
  if (!c.aiOn) return null;
  if (!c.aiUser) return (
    <>
      <button className="btn ai" disabled title="只供 Deyao 批准的帳戶使用"><I n="ai" />{label}</button><Cost c={c} id={id} />
      <span className="ai-note">{AI_NOTE(c.here)}</span>
    </>
  );
  const run = async () => {
    if (busy) return;
    setBusy(true);
    const [name, body] = aiArgs(c, id);
    try {
      const d = await aiPost(name, body, t => onRun({ live: t }));
      onRun({ done: d });
      c.bumpCosts();
    } catch (e) { onRun({ error: errText(e) }); }
    setBusy(false);
  };
  return (
    <>
      <button className="btn ai" data-ai={id} disabled={busy} onClick={run}>{busy ? <><span className="spin" />{label}</> : <><I n="ai" />{label}</>}</button>
      <Cost c={c} id={id} />
    </>
  );
}

function CostLine({ d }: { d: AiDone }) {  // after a run: what OpenRouter charged
  const u = d.usage || {};
  if (d.cached) return <p className="sub">{u.model_name ? u.model_name + "，" : ""}之前產生的結果，這次不收費</p>;
  return u.model_name ? <p className="sub">{u.model_name}，實際 {usd(u.usd)}（OpenRouter 計費）</p> : null;
}

function AiOutput({ c, out }: { c: Ctx; out: AiOut }) {
  if (!out) return <div id="ai-out" />;
  if (out.error) return <div id="ai-out"><div className="ai-out err">{out.error}</div></div>;
  if (out.live != null && !out.done) return <div id="ai-out"><div className="ai-out" dangerouslySetInnerHTML={{ __html: mdLive(out.live) }} /></div>;
  const d = out.done!;
  return (
    <div id="ai-out"><div className="ai-out">
      {d.html ? <div dangerouslySetInnerHTML={{ __html: d.html }} /> : d.markdown || ""}
      {d.at ? <p className="sub">產生於 {dt.dateTime(d.at)}</p> : null}
      <CostLine d={d} />
      <CtxLine c={c} context={d.context} note={d.note} />
    </div></div>
  );
}

/** what an AI summary read: a line that opens the list of those posts */
function CtxLine({ c, context, note }: { c: Ctx; context?: string | null; note?: string | null }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({ ...contextQuery(context || ""), enabled: open && !!context });
  if (!c.aiOn || !context || !note) return null;
  return (
    <>
      <div className="ctx">
        <button className="read" aria-expanded={open} onClick={() => setOpen(!open)}><I n="chev" />{note}</button>
      </div>
      <div className="ctx-list" hidden={!open}>
        {!open ? null : q.isError ? <p className="sub err">{errText(q.error)}</p> : !q.data ? <p className="sub">載入中…</p> : (
          <>
            <p className="sub">這些是 AI 寫這份摘要時讀過的帖子，最新的在前。{q.data.cited ? `標示「AI 引用」的 ${q.data.cited} 篇是摘要中直接引用的。` : ""}</p>
            <PostList c={c} list={q.data.posts} />
          </>
        )}
      </div>
    </>
  );
}

function ExportLink({ c, kind, extra = {} }: { c: Ctx; kind: string; extra?: DashQ }) {
  return <a className="btn" href={`/api/export/${kind}.xlsx?${qs(Object.assign(params(c.S), extra))}`} download><I n="download" />匯出 Excel</a>;
}

// ---------------- posts ----------------
function PostItem({ c, x, here, autoOpen }: { c: Ctx; x: DashPost; here?: boolean; autoOpen?: boolean }) {
  const [open, setOpen] = useState(!!autoOpen);
  const meta = c.meta;
  return (
    <div className={"post" + (here ? " here" : "")} data-id={x.id} aria-expanded={open}>
      <SiteLink className="pl" to={`/item/${x.id}`}>
        <span className={`sm ${x.sentiment || "none"}`} aria-hidden="true" />
        <span className="tt"><AiBadge x={x} /><Txt s={x.title} /></span>
        <span className="mt">
          {x.author && <span>{x.author}</span>}
          <span>{x.website || x.source || ""}</span>
          {x.board && x.board !== x.website && <span>{x.board}</span>}
          <span className="n">{when(x, c.S)}</span>
          {x.source_type && <span>{TYPE_ZH[x.source_type] || x.source_type}</span>}
          {x.cited && <span className="tag cited" title="AI 的摘要或回答引用了這篇">AI 引用</span>}
          {x.thin && <span className="tag thin" title="除連結、#標籤、@提及和表情符號外幾乎沒有內容，也沒有逐字稿或圖片文字：不作串流評分">{x.thin}</span>}
          <Face s={x.sentiment} />
          {(x.labels || []).map(l => <Tag key={l} l={l} meta={meta} />)}
          {x.theme && <span className="tag">#{x.theme}</span>}
        </span>
      </SiteLink>
      <button className="exp" data-exp="1" aria-label="展開" title="在這裡展開" onClick={() => setOpen(!open)}><I n="chev" /></button>
      {open && (
        <div className="pbody">
          <div className="txt" lang={langOf(x.text)}>{x.text || "（未能取得內文。）"}</div>
          <div className="links">
            <a href={x.link} target="_blank" rel="noopener"><I n="link" />原文連結 Original link</a>
            <SiteLink to={`/item/${x.id}`}><I n="cached" />詳情頁</SiteLink>
            {x.author && <button className="tag" data-persona={x.author} onClick={() => { c.go({ v: "persona", a: x.author!, tid: null }); scrollTo(0, 0); }}><I n="person" />作者分析</button>}
          </div>
        </div>
      )}
    </div>
  );
}

function PostList({ c, list, here }: { c: Ctx; list: DashPost[]; here?: string }) {
  if (!list.length) return <div className="empty">沒有符合這些條件的帖子。</div>;
  return <>{list.map(x => <PostItem key={x.id} c={c} x={x} here={here === String(x.id)} autoOpen={here === String(x.id)} />)}</>;
}

function DailySection({ c, daily }: { c: Ctx; daily: DailyData | null }) {  // the scheduled AI daily summary (reports.py) for the period's last day
  const { S } = c;
  let dl: ReactNode = <div className="empty">{dlong(S.to)}的 AI 摘要尚未產生（每日報告排程產生後會顯示在這裡）。</div>;
  if (daily && (daily.html || daily.heading || daily.points)) {
    dl = (
      <>
        {daily.day && daily.day !== S.to && <p className="sub">顯示 {dlong(daily.day)} 的摘要</p>}
        {daily.html ? <div className="daily" dangerouslySetInnerHTML={{ __html: daily.html }} /> : (
          <div className="daily"><p><b>{daily.heading || ""}</b></p>
            <ul>{(daily.points || []).map((p, i) => <li key={i}><b>{p.topic}：</b>{p.text}</li>)}</ul>
            {daily.action ? <p style={{ marginTop: 8 }}><b>建議行動：</b>{daily.action}</p> : null}</div>
        )}
        {daily.context && <CtxLine c={c} context={daily.context} note={daily.note} />}
      </>
    );
  }
  return <section className="sec"><header><h2>AI 每日摘要</h2><span className="sub">{dlong(S.to)}</span></header>{dl}</section>;
}

function PostsView({ c, d, daily }: { c: Ctx; d: PostsData; daily: DailyData | null }) {
  const { S, go, meta } = c;
  const page = d.page, f = d.facets, pages = Math.max(1, Math.ceil(d.total / d.size));
  const qc = useQueryClient();
  // ?post=<id> (the item page's 「在數據分析開啟」): the server picked the page holding it; it opens expanded, scrolled to
  const [missing] = useState(() => !!S.post && d.post_found === false);
  useEffect(() => {
    if (S.post && !S.page) {
      qc.setQueryData(postsQuery(params(S, { page: page, n: 25 })).queryKey, { ...d, post_found: null });
      go({ page: String(page), post: S.post }, false);
    }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (!S.post) return;
    const here = document.querySelector(`.post[data-id="${CSS.escape(S.post)}"]`);
    if (here) { here.scrollIntoView({ block: "start" }); scrollBy(0, -((document.querySelector(".yyc-bar") as HTMLElement)?.offsetHeight + 8)); }
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const qIn = useRef<HTMLInputElement>(null), aIn = useRef<HTMLInputElement>(null);
  const chip = (k: "sent" | "label", v: string, label: ReactNode, n: number) => (
    <button key={v} className={`chip ${n ? "" : "zero"}`} data-f={k} data-val={v} aria-pressed={S[k] === v}
      onClick={() => go({ [k]: S[k] === v ? null : v, page: null })}>{label}<span className="n">{n ?? ""}</span></button>
  );
  const sel = (k: "type" | "site" | "theme", label: string, opts: [string, string][]) => (
    <select className="sel" data-fsel={k} aria-label={label} value={S[k] || ""} onChange={e => go({ [k]: e.target.value, page: null })}>
      <option value="">{label}：全部</option>
      {opts.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
    </select>
  );
  const active = FILTERS.filter(k => S[k]);
  return (
    <>
      <BiasNote />
      {c.aiOn && <DailySection c={c} daily={daily} />}
      <div className="grid gpost">
      <section className="sec">
        <header>
          <h2>Post Listing 帖子列表</h2><span className="sub">共 {fmt(d.total)} 篇，第 {page}／{pages} 頁</span>
          <ExportLink c={c} kind="posts" />
        </header>
        {S.post && missing && <p className="sub">這則帖子不在這個列表中（可能已被篩選條件排除）。<SiteLink to={`/item/${S.post}`}>回到帖子詳情</SiteLink></p>}
        <div className="filters">
          <form className="frow" id="qform" key={`${S.q || ""}|${S.author || ""}`}
            onSubmit={e => { e.preventDefault(); go({ q: qIn.current!.value.trim(), author: aIn.current!.value.trim(), page: null }); }}>
            <label className="search"><I n="search" /><input id="qin" ref={qIn} type="search" defaultValue={S.q || ""}
              placeholder='關鍵字，例如 施政報告 AND (房屋 OR 土地) NOT "預告"' aria-label="Keyword query" /></label>
            <input className="txt" id="ain" ref={aIn} defaultValue={S.author || ""} placeholder="作者 Author" aria-label="Author" />
            <button className="btn" type="submit">搜尋</button>
          </form>
          <div className="frow">{SENT.map(s => chip("sent", s, <><Icon name={s} />{SENT_ZH[s]}</>, f.sentiment[s] || 0))}</div>
          <div className="frow">{LABELS.map(l => chip("label", l, <><Icon name={l} />{LABEL_ZH[l]}</>, f.labels[l] || 0))}</div>
          <div className="frow">
            {sel("type", "Source Type", meta.source_types.map(t => [t, `${TYPE_ZH[t] || t} ${t}`]))}
            {sel("site", "Web Site", f.websites.map(w => [w, w]))}
            {sel("theme", "Theme", Object.keys(f.themes).map(t => [t, `${t} (${f.themes[t]})`]))}
            {active.length ? <button className="btn" data-clear="1" onClick={() => go(Object.fromEntries([["page", null], ...FILTERS.map(k => [k, null])]))}><I n="x" />清除篩選</button> : null}
          </div>
          <details className="sub"><summary style={{ cursor: "pointer" }}>關鍵字語法</summary><div className="qnote">空格 = OR；AND、OR、NOT（大寫）及 ( )；"…" 為完整詞組；甲 NEARBY/20 乙 = 兩詞相距 20 字內；FindIn(title: 詞) 只搜標題（title、content、author、source）；IncData(Forum) / ExcData(News) 只包括／排除某類來源或網站；TopicSearchOnly 只搜話題標題。</div></details>
        </div>
        <div style={{ marginTop: 10 }}><PostList c={c} list={d.posts} here={S.post} /></div>
        {pages > 1 && (
          <div className="pager">
            <button className="btn" data-page={page - 1} disabled={page <= 1} onClick={() => { go({ page: String(page - 1) }); scrollTo(0, 0); }}><I n="back" />上一頁</button>
            <span className="n">{page} / {pages}</span>
            <button className="btn" data-page={page + 1} disabled={page >= pages} onClick={() => { go({ page: String(page + 1) }); scrollTo(0, 0); }}>下一頁<I n="next" /></button>
          </div>
        )}
      </section>
      <section className="sec" data-figure="sentiment">
        <header><h2>情緒分佈</h2></header>
        <SplitBar c={f.sentiment} />
        <div className="kvl" style={{ marginTop: 8 }}>{SENT.map(x => <div key={x}><span><Face s={x} /></span><span className="n">{f.sentiment[x] || 0}</span></div>)}</div>
        <h3 style={{ marginTop: 14 }}>來源類型</h3>
        <div className="kvl">{Object.entries(f.source_types || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => <div key={k}><span>{TYPE_ZH[k] || k} {k}</span><span className="n">{n}</span></div>)}</div>
      </section>
      </div>
    </>
  );
}

// ---------------- hot topics ----------------
interface Col<T> { h: string; f: (r: T, i: number) => ReactNode; c?: boolean; cls?: string }
function Table<T>({ cols, rows, rowProps }: { cols: Col<T>[]; rows: T[]; rowProps?: (r: T, i: number) => React.HTMLAttributes<HTMLTableRowElement> & { [k: `data-${string}`]: string | number } }) {
  if (!rows.length) return <div className="empty">這段期間沒有資料。</div>;
  return (
    <div className="tbox"><table className="t">
      <thead><tr>{cols.map(c => <th key={c.h} className={c.c ? "c" : ""}>{c.h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={i} {...(rowProps ? rowProps(r, i) : {})}>
          {cols.map(c => <td key={c.h} className={[c.c ? "c" : "", c.cls || ""].join(" ")} data-h={c.h}>{c.f(r, i)}</td>)}
        </tr>
      ))}</tbody>
    </table></div>
  );
}

function HotView({ c, d, terms }: { c: Ctx; d: { topics: HotTopic[] }; terms: { data?: unknown; error: unknown } }) {
  const { S, go } = c;
  const top = S.top || "10";
  const cols: Col<HotTopic>[] = [{ h: "Top", f: (_r, i) => i + 1, c: true, cls: "mhide" }, { h: "Region", f: r => r.region || "" },
    { h: "Title", cls: "title", f: r => <span className="lnk clip2"><AiBadge x={r} /><Txt s={r.title} /></span> },
    { h: "Web Site", f: r => r.website }, { h: "Sentiment Index (%)", c: true, f: r => <SiCell si={r.sentiment_index} level={r.sentiment_level} /> },
    { h: "Sentiment Score", c: true, f: r => r.sentiment_score }, { h: "Contribution (%)", c: true, f: r => r.contribution },
    { h: "All Post Count", c: true, f: r => fmt(r.all_post_count) }, { h: "Period Post Count", c: true, f: r => fmt(r.period_post_count) },
    { h: "Matched Post Count", c: true, f: r => fmt(r.matched_post_count) }, { h: "Participants", c: true, f: r => fmt(r.participants) },
    { h: "View Count", c: true, f: r => fmt(r.view_count) }, { h: "Correlation Index (%)", c: true, f: r => r.correlation_index },
    { h: "Initial Date", c: true, f: r => r.initial_date }, { h: "End Date", c: true, f: r => r.end_date }, { h: "Day Span", c: true, f: r => r.day_span }];
  const open = (id: number) => { topicFromList = true; go({ tid: String(id) }); scrollTo(0, 0); };
  return (
    <>
      <BiasNote />
    <section className="sec">
      <header>
        <h2>Hot Topic Listing 熱門話題</h2>
        <select className="sel" id="topsel" aria-label="Top" value={String(+top)} onChange={e => go({ top: e.target.value })}>
          {[10, 50, 100].map(n => <option key={n} value={String(n)}>Top {n}</option>)}
        </select>
        <ExportLink c={c} kind="hot_topics" extra={{ top }} />
      </header>
      <p className="sub" style={{ margin: "0 0 8px" }}>數字及日期以所選期間計算。點擊話題查看詳情。</p>
      <Table cols={cols} rows={d.topics} rowProps={r => ({ className: "go", "data-tid": r.id, tabIndex: 0, onClick: () => open(r.id),
        onKeyDown: e => { if (e.key === "Enter") open(r.id); } })} />
    </section>
    <section className="sec" data-figure="terms">
      <header><h2>Smart Hot Terms 熱門詞</h2></header>
      {terms.data ? <Cloud c={c} terms={(terms.data as { terms: Term[] }).terms} />
        : terms.error ? <div className="empty err">{errText(terms.error)}</div> : <div className="empty">載入中…</div>}
    </section>
    </>
  );
}

function Stat({ v, l }: { v: ReactNode; l: string }) {
  return <div className="score"><div className="k"><span>{l}</span></div><div className="v"><span className="big">{v}</span></div></div>;
}
const siColor = (si: number) => `var(${si > 5 ? "--sent-pos" : si < -5 ? "--sent-neg" : "--ink"})`;

function TopicView({ c, d }: { c: Ctx; d: TopicData }) {
  const m = d.stats;
  const back = () => { if (topicFromList) { topicFromList = false; history.back(); return; } c.go({ tid: null }, false); };
  return (
    <>
      <BiasNote />
      <section className="sec">
        <header>
          <button className="btn" data-back="1" onClick={back}><I n="back" />熱門話題</button><h2 style={{ marginRight: "auto" }} />
          <span className="cellrow">
            <a className="btn" href={d.link} target="_blank" rel="noopener"><I n="link" />Original link</a>
            {d.posts[0] ? <SiteLink className="btn" to={`/item/${d.posts[d.posts.length - 1].id}`}><I n="cached" />詳情頁</SiteLink> : null}
          </span>
        </header>
        <h2 style={{ fontSize: 18, lineHeight: 1.45, marginBottom: 10, overflowWrap: "anywhere" }}><AiBadge x={d} /><Txt s={d.title} /></h2>
        <div className="scores">
          <Stat v={fmt(m.posts)} l="Posts 帖子" /><Stat v={fmt(m.participants)} l="Participants 參與者" />
          <Stat v={fmt(m.websites)} l="Websites 網站" />
          <Stat v={<span style={{ color: siColor(m.sentiment_index) }}>{pct(m.sentiment_index)}</span>} l="Sentiment Index 情緒指數" />
        </div>
        <div style={{ marginTop: 10 }}><SplitBar c={m.sentiment} /></div>
      </section>
      <div className="grid g2">
        <section className="sec" data-figure="topic-trend"><header><h2>Trend 趨勢</h2></header><BarChart id="c-topic" rows={d.trend} /></section>
        <section className="sec" data-figure="topic-sources"><header><h2>Top Source 來源</h2></header>
          <Table cols={[{ h: "Web Site", cls: "title", f: (r: SourceRow) => r.website }, { h: "Posts", c: true, f: r => r.posts }, { h: "Participants", c: true, f: r => r.participants },
            { h: "Sentiment Index (%)", c: true, f: r => <SiCell si={r.sentiment_index} level={r.sentiment_level} /> }]} rows={d.top_sources} /></section>
        <section className="sec" data-figure="topic-authors"><header><h2>Top Author 作者</h2></header>
          <Table cols={[{ h: "Author", cls: "title", f: (r: AuthorRow) => <span className="lnk">{r.author}</span> }, { h: "Web Site", f: r => r.website },
            { h: "Posts", c: true, f: r => r.posts }, { h: "Sentiment Index (%)", c: true, f: r => <SiCell si={r.sentiment_index} level={r.sentiment_level} /> }]}
            rows={d.top_authors} rowProps={r => personaRow(c, r.author)} /></section>
        <section className="sec"><header><h2>Post Listing 帖子</h2><span className="sub">{d.posts.length} 篇</span></header><PostList c={c} list={d.posts} /></section>
      </div>
    </>
  );
}

// ---------------- the figures that depend on our selection: 摘要, 作者, 來源, 趨勢 (each view with its <BiasNote />) ----------------
const personaRow = (c: Ctx, author: string) => ({ className: "go", tabIndex: 0, "data-persona": author,
  onClick: () => { c.go({ v: "persona", a: author, tid: null }); scrollTo(0, 0); },
  onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter") { c.go({ v: "persona", a: author, tid: null }); scrollTo(0, 0); } } });

/** Smart Hot Terms: a word cloud; a term opens the posts that have it */
function Cloud({ c, terms }: { c: Ctx; terms: Term[] }) {
  if (!terms || !terms.length) return <div className="empty">這段期間沒有足夠的帖子產生熱門詞。</div>;
  const max = Math.max(...terms.map(t => t.weight)), min = Math.min(...terms.map(t => t.weight));
  const hash = (s: string) => [...s].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
  const shown = terms.slice().sort((a, b) => hash(a.term) - hash(b.term));
  return (
    <>
      <div className="cloud">{shown.map(t => {
        const k = max === min ? 1 : (t.weight - min) / (max - min);
        return <button key={t.term} data-term={t.term} title={`${t.term}：${t.count} 次`}
          style={{ fontSize: `${(13 + 23 * Math.sqrt(k)).toFixed(1)}px`, fontWeight: k > .45 ? 700 : 500, color: k < .2 ? "var(--muted)" : undefined }}
          onClick={() => c.go({ v: "posts", q: `"${t.term}"`, page: null, tid: null })}>{t.term}</button>;
      })}</div>
      <p className="sub" style={{ margin: "4px 0 0" }}>點擊詞語搜尋相關帖子。</p>
    </>
  );
}

function Mrow({ val, label, link, c }: { val: ReactNode; label: string; link?: Partial<Record<Key, string | null>>; c: Ctx }) {
  return (
    <div className="mrow"><span className="n">{val}</span>
      <span>{link ? <a href="#" onClick={e => { e.preventDefault(); c.go({ ...link, page: null }); }}>{label}</a> : label}</span></div>
  );
}

function ScoreCard({ k, s }: { k: string; s?: { value: number; level: string; change_pct: number | null } }) {
  if (!s) return null;
  const up = (s.change_pct || 0) > 0, dn = (s.change_pct || 0) < 0;
  return (
    <div className={`score ${k === "SPI" ? "spi" : ""}`} data-score={k}>
      <div className="k"><span><b>{k}</b> {SCORE_ZH[k]}</span></div>
      <div className="v"><span className="big">{s.value}</span><span className="lv">{LEVEL_ZH[s.level] || s.level}</span>
        <span className="ch" title="與前一日比較">{up ? <I n="up" /> : dn ? <I n="down" /> : null}{s.change_pct == null ? "—" : Math.abs(s.change_pct) + "%"}</span></div>
    </div>
  );
}

const weekly = (rows: TrendRow[]) => rows.length > 1 && dayAdd(rows[0].period, 7) === rows[1].period;
const pickPeriod = (c: Ctx, r: TrendRow, week: boolean) =>
  c.go({ v: "posts", from: r.period, to: week ? [dayAdd(r.period, 6), c.meta.today].sort()[0] : r.period, page: null });

function SummaryView({ c, d, sc, terms, daily }: { c: Ctx; d: SummaryData; sc: ScoresData; terms: Term[]; daily: DailyData | null }) {
  const { S } = c;
  const m = d.metrics, g = d.cgm, nw = d.news, M = d.method;
  const week = weekly(d.trend);
  const n = (v: unknown) => <span className="n">{fmt(v)}</span>;
  const siC = (si: number, neutral = "--muted") => `var(${si > 5 ? "--sent-pos" : si < -5 ? "--sent-neg" : neutral})`;
  const defs = ([["Posts 帖子", NOTE.posts], ["New Posts 新帖子", NOTE.new_posts], ["Topics 話題", NOTE.topics],
    ["New Topics 新話題", NOTE.new_topics], ["Websites 網站", NOTE.websites], ["Boards 版面", NOTE.boards], ["Authors 作者", NOTE.authors],
    ["View Count 瀏覽數", NOTE.view_count], ["Websites Coverage 網站覆蓋率", NOTE.website_coverage], ["Posts Spread 帖子佔比", NOTE.posts_spread],
    ["Per Day 每日", NOTE.per_day], ["Sentiment Index 情緒指數", M.sentiment_index], ["Sentiment Balance 情緒差額", NOTE.sentiment_balance],
    ...["SPI", "CSS", "DVS", "DPS", "CCS"].map((k): [string, string] => [`${k} ${SCORE_ZH[k]}`, M[k]])] as [string, string][]).filter(x => x[1]);
  return (
    <>
    <BiasNote defs={defs} />
    <div className="grid gsum">
      <div className="stack">
        <section className="sec" data-figure="exposure">
          <header><h2>Exposure &amp; Mentioning Trend 曝光及提及趨勢</h2><ExportLink c={c} kind="trend" extra={{ bucket: week ? "week" : "day" }} /></header>
          <div className="sub" style={{ marginBottom: 6 }}>{dlong(S.from)} 至 {dlong(S.to)}，每{week ? "週" : "日"}。點擊一點查看當{week ? "週" : "日"}的帖子。</div>
          <ExposureChart id="c-exp" rows={d.trend} week={week} onPick={r => pickPeriod(c, r, week)} />
        </section>
        <section className="sec" data-figure="summary">
          <header><h2>Exposure &amp; Mentioning Summary 曝光及提及摘要</h2><ExportLink c={c} kind="summary" /></header>
          <p className="lede">{n(m.authors)} 人在 {n(m.websites)} 個網站的 {n(m.topics)} 個話題／{n(m.posts)} 篇帖子中發表了意見。情緒指數為{" "}
            <span className="n" style={{ color: siC(m.sentiment_index) }}>{pct(m.sentiment_index)}</span>。
            {m.view_count ? <>多於 {n(m.view_count)} 人次瀏覽過這些意見。</> : "來源未提供瀏覽數 (N/A)。"}</p>
          <div className="cols" style={{ marginTop: 12 }}>
            <div className="col"><h3>CGM 用戶生成內容</h3>
              <Mrow c={c} val={fmt(g.posts)} label="Posts 帖子" link={{ v: "posts", type: "Forum,Social Media,Video,Blog" }} />
              <Mrow c={c} val={fmt(g.new_posts)} label="New Posts 新帖子" />
              <Mrow c={c} val={fmt(g.topics)} label="Topics 話題" link={{ v: "hot" }} />
              <Mrow c={c} val={fmt(g.new_topics)} label="New Topics 新話題" />
              <Mrow c={c} val={fmt(g.websites)} label="Websites 網站" link={{ v: "sources", by: "website" }} />
              <Mrow c={c} val={fmt(g.boards)} label="Boards 版面" link={{ v: "sources", by: "board" }} />
              <Mrow c={c} val={fmt(g.authors)} label="Authors 作者" link={{ v: "authors" }} />
              <Mrow c={c} val={fmt(g.view_count)} label="View Count 瀏覽數" /></div>
            <div className="col"><h3>News 新聞</h3>
              <Mrow c={c} val={fmt(nw.posts)} label="Posts 帖子" link={{ v: "posts", type: "News,Official" }} />
              <Mrow c={c} val={fmt(nw.websites)} label="Websites 網站" />
              <Mrow c={c} val={fmt(nw.boards)} label="Boards 版面" />
              <Mrow c={c} val={fmt(nw.topics)} label="Topics 話題" />
              <Mrow c={c} val={fmt(nw.authors)} label="Authors 作者" /></div>
            <div className="col"><h3>Analysis 分析</h3>
              <Mrow c={c} val={m.website_coverage + "%"} label="Websites Coverage 網站覆蓋率" />
              <Mrow c={c} val={m.posts_spread + "%"} label="Posts Spread 帖子佔比" />
              <Mrow c={c} val={m.posts_per_day} label="Posts per Day 每日帖子" />
              <Mrow c={c} val={m.authors_per_day} label="Authors per Day 每日作者" />
              <Mrow c={c} val={m.topics_per_day} label="Topics per Day 每日話題" />
              <Mrow c={c} val={m.websites_per_day} label="Websites per Day 每日網站" />
              <Mrow c={c} val={m.boards_per_day} label="Boards per Day 每日版面" />
              <Mrow c={c} val={<span style={{ color: siC(m.sentiment_index, "--ink") }}>{pct(m.sentiment_index)}</span>} label="Sentiment Index 情緒指數" />
              <Mrow c={c} val={fmt(m.sentiment_balance)} label="Sentiment Balance 情緒差額" /></div>
          </div>
        </section>
      </div>
      <div className="stack">
        <section className="sec" data-figure="scores">
          <header><h2>每日分數</h2><span className="sub">{dlong(S.to)}</span></header>
          <div className="scores s2">{["SPI", "CSS", "DVS", "DPS", "CCS"].map(k => <ScoreCard key={k} k={k} s={sc.scores[k]} />)}</div>
          <p className="sub" style={{ margin: "8px 0 0" }}>{M.levels || ""}</p>
        </section>
        {c.aiOn && <DailySection c={c} daily={daily} />}
        <section className="sec" data-figure="terms"><header><h2>Smart Hot Terms 熱門詞</h2></header><Cloud c={c} terms={terms} /></section>
      </div>
    </div>
    </>
  );
}

function AuthorsView({ c, rows }: { c: Ctx; rows: AuthorRow[] }) {
  const cols: Col<AuthorRow>[] = [{ h: "Author", cls: "title", f: r => <span className="lnk">{r.author}</span> }, { h: "Web Site", f: r => r.website },
    { h: "Sentiment Index (%)", c: true, f: r => <SiCell si={r.sentiment_index} level={r.sentiment_level} /> }, { h: "Contribution (%)", c: true, f: r => r.contribution },
    { h: "Post Count", c: true, f: r => r.posts }, { h: "Topic Count", c: true, f: r => r.topics }, { h: "View Count", c: true, f: r => fmt(r.view_count) },
    { h: "Most Active Board", cls: "wide", f: r => r.most_active_board || "—" }];
  return (
    <>
    <BiasNote />
    <section className="sec" data-figure="authors">
      <header><h2>Top Author 作者排行</h2><ExportLink c={c} kind="authors" extra={{ top: 100 }} /></header>
      <p className="sub" style={{ margin: "0 0 8px" }}>只計有作者名稱的帖子。點擊作者查看人物分析 (Persona)。</p>
      <Table cols={cols} rows={rows} rowProps={r => personaRow(c, r.author)} />
    </section>
    </>
  );
}

function SourcesView({ c, rows }: { c: Ctx; rows: SourceRow[] }) {
  const by = byOf(c.S);
  const cols: Col<SourceRow>[] = [{ h: "Web Site", cls: "title", f: r => <span className="lnk">{r.website}</span> },
    ...(by === "board" ? [{ h: "Board", f: (r: SourceRow) => r.board || "—" }] : []),
    { h: "Source Type", f: r => TYPE_ZH[r.source_type || ""] || r.source_type || "" }, { h: "Region", f: r => r.region || "" },
    { h: "Sentiment Index (%)", c: true, f: r => <SiCell si={r.sentiment_index} level={r.sentiment_level} /> }, { h: "Contribution (%)", c: true, f: r => r.contribution },
    { h: "Post Count", c: true, f: r => r.posts }, { h: "Topic Count", c: true, f: r => r.topics }, { h: "Participants", c: true, f: r => r.participants }];
  const site = (r: SourceRow) => c.go({ v: "posts", site: r.website, page: null });
  return (
    <>
    <BiasNote />
    <section className="sec" data-figure="sources">
      <header><h2>Top Source 來源排行</h2>
        <div className="seg" role="group" aria-label="By">
          <button data-by="website" aria-pressed={by === "website"} onClick={() => c.go({ by: "website" })}>Web Site 網站</button>
          <button data-by="board" aria-pressed={by === "board"} onClick={() => c.go({ by: "board" })}>Board 版面</button>
        </div>
        <ExportLink c={c} kind="sources" extra={{ top: 100, by }} /></header>
      <p className="sub" style={{ margin: "0 0 8px" }}>點擊網站查看其帖子。</p>
      <Table cols={cols} rows={rows} rowProps={r => ({ className: "go", tabIndex: 0, "data-site": r.website || "", onClick: () => site(r),
        onKeyDown: (e: React.KeyboardEvent) => { if (e.key === "Enter") site(r); } })} />
    </section>
    </>
  );
}

function TrendView({ c, rows }: { c: Ctx; rows: TrendRow[] }) {
  const bucket = bucketOf(c.S), week = bucket === "week";
  const per = rows.map(r => r.period), hasViews = rows.some(r => r.views);
  const cols: Col<TrendRow>[] = [{ h: "Period", f: r => r.period }, { h: "Posts", c: true, f: r => fmt(r.posts) }, { h: "Topics", c: true, f: r => fmt(r.topics) },
    { h: "Authors", c: true, f: r => fmt(r.authors) }, { h: "View Count", c: true, f: r => (r.views ? fmt(r.views) : "N/A") },
    { h: "Sentiment Index (%)", c: true, f: r => <SiCell si={r.sentiment_index} level="" /> }];
  return (
    <>
      <BiasNote />
      <section className="sec" data-figure="trend">
        <header><h2>Trend 趨勢</h2>
          <div className="seg" role="group" aria-label="Bucket">
            <button data-bucket="day" aria-pressed={!week} onClick={() => c.go({ bucket: "day" })}>每日</button>
            <button data-bucket="week" aria-pressed={week} onClick={() => c.go({ bucket: "week" })}>每週</button>
          </div>
          <ExportLink c={c} kind="trend" extra={{ bucket }} /></header>
        <ExposureChart id="c-exp" rows={rows} week={week} onPick={r => pickPeriod(c, r, week)} />
      </section>
      <div className="grid g2">
        <section className="sec" data-figure="pta"><header><h2>Posts, Topics &amp; Authors 帖子、話題及作者</h2></header>
          <LineChart id="c-pta" periods={per} week={week} label="Posts, topics and authors" series={[
            { name: "帖子 Posts", line: "solid", values: rows.map(r => r.posts) }, { name: "話題 Topics", line: "dash", values: rows.map(r => r.topics || 0) },
            { name: "作者 Authors", line: "muted", values: rows.map(r => r.authors || 0) }]} /></section>
        <section className="sec" data-figure="views"><header><h2>View Count 瀏覽數</h2></header>
          {hasViews ? <LineChart id="c-views" periods={per} week={week} h={140} label="Views" series={[{ name: "瀏覽數 Views", line: "solid", values: rows.map(r => r.views || 0) }]} />
            : <div className="empty">這段期間的來源未提供瀏覽數 (N/A)。</div>}</section>
      </div>
      <section className="sec" data-figure="table"><header><h2>數據表</h2></header><Table cols={cols} rows={rows} /></section>
    </>
  );
}

// ---------------- themes ----------------
function ThemesView({ c, d, total }: { c: Ctx; d: ThemesData; total: number }) {
  const counts = new Map(d.counts.map(x => [x.theme, x]));
  const names = d.themes && d.themes.length ? d.themes : d.counts.map(x => x.theme);
  const unthemed = total - d.counts.reduce((a, x) => a + x.posts, 0);
  return (
    <section className="sec">
      <header><h2>Themes AI 主題</h2>{names.length ? <ExportLink c={c} kind="themes" /> : null}</header>
      <p className="sub" style={{ margin: "0 0 6px" }}>
        {d.generated ? `AI 主題產生於 ${dt.dateTime(String(d.generated))}。` : ""}點擊主題查看該主題的帖子。{names.length && unthemed > 0 ? ` 期內有 ${unthemed} 篇帖子未分配主題。` : ""}
      </p>
      {!names.length ? <div className="empty">這個設定還未有 AI 主題。主題由每日排程按帖子內容產生，產生後會在這裡列出，並可用來篩選帖子列表。</div>
        : names.map(t => {
          const x = counts.get(t) || { posts: 0, sentiment_index: 0 };
          return (
            <button key={t} className="theme" data-theme-go={t} onClick={() => c.go({ v: "posts", theme: t, page: null })}>
              <span className="nm" style={{ fontWeight: 600 }}>{t}</span><span className="n"><span>{x.posts} 篇</span><span>情緒 {pct(x.sentiment_index)}</span></span>
              <span className="split" style={{ background: "var(--rule)" }}><i style={{ width: `${total ? 100 * x.posts / total : 0}%`, background: "var(--ink)" }} /></span>
            </button>
          );
        })}
    </section>
  );
}

// ---------------- persona ----------------
function PersonaForm({ c, active }: { c: Ctx; active: AuthorRow[] }) {
  const inp = useRef<HTMLInputElement>(null);
  return (
    <form className="frow" id="pform" key={c.S.a || ""} onSubmit={e => { e.preventDefault(); c.go({ a: inp.current!.value.trim() }); }}>
      <label className="search"><I n="person" /><input id="pin" ref={inp} list="plist" defaultValue={c.S.a || ""} placeholder="作者名稱 Author" aria-label="Author" /></label>
      <datalist id="plist">{active.map(a => <option key={`${a.author}|${a.website}`} value={a.author}>{`${a.website}，${a.posts} 篇`}</option>)}</datalist>
      <button className="btn" type="submit">分析</button>
    </form>
  );
}

function PersonaEmpty({ c, active }: { c: Ctx; active: AuthorRow[] }) {
  return (
    <>
    <BiasNote />
    <section className="sec" data-figure="active"><header><h2>Persona 人物分析</h2></header><PersonaForm c={c} active={active} />
      <p className="sub" style={{ margin: "10px 0 0" }}>輸入作者名稱，或在帖子列表展開一篇帖子後按「作者分析」。</p>
      <h3 style={{ marginTop: 14 }}>期內最活躍作者</h3>
      {active.length ? <div className="chk">{active.slice(0, 24).map(a => (
        <button key={`${a.author}|${a.website}`} className="chip" data-persona={a.author} onClick={() => { c.go({ v: "persona", a: a.author, tid: null }); scrollTo(0, 0); }}>
          {a.author}<span className="n">{a.posts}</span></button>))}</div>
        : <div className="empty">這段期間沒有具名作者。</div>}
    </section>
    </>
  );
}

function PersonaView({ c, d, active }: { c: Ctx; d: PersonaData; active: AuthorRow[] }) {
  const { S } = c;
  const [ai, setAi] = useState<AiOut>(null);
  const span = useMemo(() => {
    const byDay: Record<string, number> = {};
    d.recent.forEach(x => { if (x.day) byDay[x.day] = (byDay[x.day] || 0) + 1; });
    const days = Object.keys(byDay).sort(), out: { period: string; posts: number }[] = [];
    if (days.length) for (let x = days[0]; x <= days[days.length - 1] && out.length < 400; x = dayAdd(x, 1)) out.push({ period: x, posts: byDay[x] || 0 });
    return out;
  }, [d]);
  return (
    <>
      <BiasNote />
      <section className="sec">
        <header>
          <h2>Persona 人物分析</h2><AiBlock c={c} id="persona" label="AI 分析" onRun={setAi} />
          <button className="btn" onClick={e => { e.preventDefault(); c.go({ page: null, tid: null, v: "posts", author: S.a! }); }}><I n="stream" />在帖子列表查看</button>
        </header>
        <PersonaForm c={c} active={active} />
        <h2 style={{ fontSize: 20, margin: "14px 0 4px", overflowWrap: "anywhere" }}>{d.author}</h2>
        <p className="sub" style={{ margin: "0 0 10px" }}>
          {d.first_seen ? `首次出現 ${dt.date(d.first_seen)}，最近 ${dt.date(d.last_seen)}` : "資料庫內沒有這位作者的帖子。"}（資料庫內 {dlong(dayAdd(S.from, -365))} 至 {dlong(S.to)} 的全部帖子）
        </p>
        <div className="scores">
          <Stat v={fmt(d.posts)} l="全部帖子" /><Stat v={fmt(d.matched_posts)} l="與此設定相關" />
          <Stat v={<span style={{ color: siColor(d.sentiment_index_on_profile) }}>{pct(d.sentiment_index_on_profile)}</span>} l="對此設定的情緒指數" />
          <Stat v={fmt(d.websites.length)} l="網站" />
        </div>
        <div style={{ marginTop: 10 }}><SplitBar c={d.sentiment} /></div>
        <AiOutput c={c} out={ai} />
      </section>
      <div className="grid g3">
        <section className="sec"><header><h2>Activity 活躍度</h2></header>{span.length ? <BarChart id="c-pa" rows={span} /> : <div className="empty">沒有資料。</div>}</section>
        <section className="sec"><header><h2>Boards 版面</h2></header>
          <div className="kvl">{d.boards.length ? d.boards.map((b, i) => <div key={i}><span>{b.board}</span><span className="n">{b.posts}</span></div>) : <div className="empty">沒有資料。</div>}</div>
          <h3 style={{ marginTop: 14 }}>Websites 網站</h3>
          <div className="kvl">{d.websites.map((b, i) => <div key={i}><span>{b.website}</span><span className="n">{b.posts}</span></div>)}</div>
        </section>
        <section className="sec" data-figure="persona-terms"><header><h2>Hot Terms 熱門詞</h2></header><Cloud c={c} terms={d.hot_terms || []} /></section>
      </div>
      <section className="sec"><header><h2>Recent Posts 近期帖子</h2><span className="sub">最多 100 篇</span></header><PostList c={c} list={d.recent.slice(0, 100)} /></section>
    </>
  );
}

// ---------------- charts (inline SVG, one y-axis) ----------------
let tipEl: HTMLDivElement | null = null;
function Tip() { return <div className="tip" id="tip" role="tooltip" ref={el => { tipEl = el; }} />; }
function showTip(e: React.PointerEvent, html: string) {
  const tip = tipEl; if (!tip) return;
  tip.innerHTML = html; tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY - h - 10;
  if (x + w > innerWidth - 8) x = e.clientX - w - 14;
  if (x < 8) x = 8;
  if (y < 8) y = e.clientY + 16;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { if (tipEl) tipEl.style.display = "none"; };
if (typeof window !== "undefined") addEventListener("scroll", hideTip, { passive: true });

function niceMax(v: number) { if (v <= 0) return 4; const p = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p; return 10 * p; }
function xLabels(n: number, w: number) { const step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(w / 54)))); return (i: number) => i % step === 0 || (i === n - 1 && (n - 1) % step > step / 2); }

/** the small bar chart (a topic's trend, an author's activity): drawn at the box's width, redrawn on resize */
function BarChart({ id, rows, label = "帖子數" }: { id: string; rows: { period: string; posts: number }[]; label?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = box.current; if (!el) return;
    let t = 0;
    const set = () => setW(el.clientWidth);
    set();
    const ro = new ResizeObserver(() => { clearTimeout(t); t = window.setTimeout(set, 120); });
    ro.observe(el);
    return () => { ro.disconnect(); clearTimeout(t); };
  }, []);
  const L = 30, R = 6, H1 = 90, B = 18, T = 6, H = T + H1 + B;
  if (!w) return <div className="chart" id={id} ref={box} style={{ height: H }} />;
  const W = Math.max(260, w), n = rows.length, bw = (W - L - R) / Math.max(1, n);
  const ymax = niceMax(Math.max(1, ...rows.map(r => r.posts))), lab = xLabels(n, W - L - R), bwid = Math.max(2, Math.min(22, bw * 0.62));
  return (
    <div className="chart" id={id} ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img" aria-label={label}>
        {[0, 1, 2].map(k => { const v = ymax * k / 2, y = T + H1 - H1 * v / ymax; return (
          <g key={k}><line x1={L} x2={W - R} y1={y} y2={y} stroke="var(--rule)" /><text x={L - 5} y={y + 4} textAnchor="end">{v}</text></g>
        ); })}
        {rows.map((r, i) => {
          const h = H1 * r.posts / ymax, x = L + bw * i + bw / 2;
          return (
            <g key={r.period}>
              {r.posts ? <rect x={x - bwid / 2} y={T + H1 - Math.max(1, h)} width={bwid} height={Math.max(1, h)} fill="var(--ink)" /> : null}
              {lab(i) ? <text x={x} y={H - 3} textAnchor="middle">{dm(r.period)}</text> : null}
              <rect x={L + bw * i} y={0} width={bw} height={T + H1} fill="transparent" className="hit"
                onPointerMove={e => showTip(e, `<div class="d">${dlong(r.period)}</div><div class="r"><i class="ln-bar"></i><b>${fmt(r.posts)}</b><span>${esc(label)}</span></div>`)}
                onPointerLeave={hideTip} />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** the width of a chart's box, read again on resize (the charts draw at it) */
function useBoxWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = box.current; if (!el) return;
    let t = 0;
    const set = () => setW(el.clientWidth);
    set();
    const ro = new ResizeObserver(() => { clearTimeout(t); t = window.setTimeout(set, 120); });
    ro.observe(el);
    return () => { ro.disconnect(); clearTimeout(t); };
  }, []);
  return [box, w];
}
let clipN = 0;
/** a tip row: the series' swatch (its line style class, dashboard.css i.ln-*), the value, the name */
const tipRow = (cls: string, label: string, val: string) => `<div class="r"><i class="${cls}"></i><b>${val}</b><span>${esc(label)}</span></div>`;
/** a line series' style: series are told apart by line style, never by hue */
const LINE: Record<string, { stroke: string; dash?: string; cls: string }> = {
  solid: { stroke: "var(--ink)", cls: "" }, dash: { stroke: "var(--ink)", dash: "6 4", cls: "ln-dash" }, muted: { stroke: "var(--muted)", cls: "ln-muted" } };

/** Exposure & Mentioning Trend: posts bars on top, the sentiment index area below (green above 0, red below) */
function ExposureChart({ id, rows, week, onPick }: { id: string; rows: TrendRow[]; week: boolean; onPick?: (r: TrendRow) => void }) {
  const [box, w] = useBoxWidth();
  const [cross, setCross] = useState<number | null>(null);
  const [cid] = useState(() => ++clipN);
  const L = 38, R = 8, H1 = 150, G = 30, H2 = 84, B = 20, T = 22, H = T + H1 + G + H2 + B;
  if (!w) return <div className="chart" id={id} ref={box} style={{ height: H }} />;
  const W = Math.max(280, w), n = rows.length, bw = (W - L - R) / Math.max(1, n);
  const ymax = niceMax(Math.max(...rows.map(r => r.posts), 1));
  const smax = Math.max(20, ...rows.map(r => Math.abs(r.sentiment_index || 0)));
  const sm = smax <= 20 ? 20 : smax <= 50 ? 50 : 100;
  const y1 = (v: number) => T + H1 - H1 * v / ymax, top2 = T + H1 + G, y2 = (v: number) => top2 + H2 / 2 - (H2 / 2) * v / sm;
  const cx = (i: number) => L + bw * i + bw / 2, barW = Math.max(2, Math.min(26, bw * 0.62));
  const lab = xLabels(n, W - L - R);
  const pts = rows.map((r, i) => [cx(i), y2(r.sentiment_index || 0)]);
  if (n === 1) { pts.unshift([L + 2, pts[0][1]]); pts.push([W - R - 2, pts[pts.length - 1][1]]); }
  const line = pts.length ? "M" + pts.map(p => p.join(",")).join("L") : "", z = y2(0);
  const area = pts.length ? `${line}L${pts[pts.length - 1][0]},${z}L${pts[0][0]},${z}Z` : "";
  const tip = (e: React.PointerEvent, r: TrendRow) => showTip(e, `<div class="d">${week ? "由 " : ""}${dlong(r.period)}${week ? " 起一週" : ""}</div>`
    + tipRow("ln-bar", "帖子", fmt(r.posts)) + (r.topics != null ? tipRow("ln-muted", "話題", fmt(r.topics)) : "")
    + (r.authors != null ? tipRow("ln-muted", "作者", fmt(r.authors)) : "")
    + tipRow((r.sentiment_index || 0) >= 0 ? "ln-pos" : "ln-neg", "情緒指數", pct(r.sentiment_index || 0))
    + (onPick ? `<div class="h">點擊查看這${week ? "週" : "日"}的帖子</div>` : ""));
  return (
    <div className="chart" id={id} ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img" aria-label="Exposure & Mentioning Trend">
        {[0, 1, 2, 3, 4].map(k => { const v = ymax * k / 4, y = y1(v); return (
          <g key={"a" + k}><line x1={L} x2={W - R} y1={y} y2={y} stroke="var(--rule)" /><text x={L - 6} y={y + 4} textAnchor="end">{grouped(v)}</text></g>); })}
        <text x={4} y={10} textAnchor="start">帖子數</text>
        {rows.map((r, i) => { const h = H1 * r.posts / ymax; return r.posts
          ? <rect key={"b" + i} x={cx(i) - barW / 2} y={y1(0) - Math.max(1, h)} width={barW} height={Math.max(1, h)} fill="var(--ink)" className="bar" /> : null; })}
        {[sm, sm / 2, 0, -sm / 2, -sm].map(v => { const y = y2(v); return (
          <g key={"s" + v}><line x1={L} x2={W - R} y1={y} y2={y} stroke={v === 0 ? "var(--ink)" : "var(--rule)"} /><text x={L - 6} y={y + 4} textAnchor="end">{v}%</text></g>); })}
        <text x={4} y={top2 - 12} textAnchor="start">情緒指數</text>
        <clipPath id={`ca${cid}`}><rect x={0} y={top2 - 2} width={W} height={Math.max(0, z - top2 + 2)} /></clipPath>
        <clipPath id={`cb${cid}`}><rect x={0} y={z} width={W} height={H2} /></clipPath>
        {area && <path d={area} fill="var(--sent-pos)" fillOpacity={.45} clipPath={`url(#ca${cid})`} />}
        {area && <path d={area} fill="var(--sent-neg)" fillOpacity={.45} clipPath={`url(#cb${cid})`} />}
        {line && <path d={line} fill="none" stroke="var(--ink)" strokeWidth={2} strokeLinejoin="miter" />}
        {rows.map((r, i) => (lab(i) ? <text key={"x" + i} x={cx(i)} y={H - 4} textAnchor="middle">{dm(r.period)}</text> : null))}
        {cross != null && <line x1={cx(cross)} x2={cx(cross)} y1={T} y2={top2 + H2} stroke="var(--ink)" strokeDasharray="2 2" />}
        {rows.map((r, i) => <rect key={"h" + i} x={L + bw * i} y={0} width={bw} height={H - B} fill="transparent" className="hit" data-i={i}
          style={{ cursor: onPick ? "pointer" : "default" }} onPointerMove={e => { setCross(i); tip(e, r); }}
          onPointerLeave={() => { setCross(null); hideTip(); }} onClick={() => { if (onPick) { hideTip(); onPick(r); } }} />)}
      </svg>
    </div>
  );
}

/** a multi-series line chart, one shared y-axis (all series in the same unit) */
function LineChart({ id, periods, series, week, h = 200, label }: { id: string; periods: string[]; week: boolean; h?: number; label: string;
  series: { name: string; line: "solid" | "dash" | "muted"; values: number[] }[] }) {
  const [box, w] = useBoxWidth();
  const [cross, setCross] = useState<number | null>(null);
  const ytop = niceMax(Math.max(1, ...series.flatMap(s => s.values.map(v => v || 0))));
  // the y-axis labels (Archivo 12px, ~7px a figure) fit left of the plot: "1,500,000" needs more room than "25"
  const L = Math.max(40, 12 + 7 * Math.max(...[0, 1, 2, 3, 4].map(k => grouped(+(ytop * k / 4).toFixed(1)).length))), R = 10, H1 = h, B = 20, T = 10, H = T + H1 + B;
  const legend = <div className="legend">{series.map(se => <span key={se.name}><i className={LINE[se.line].cls || undefined} />{se.name}</span>)}</div>;
  if (!w) return <div className="chart" id={id} ref={box}>{legend}<div style={{ height: H }} /></div>;
  const W = Math.max(280, w), n = periods.length;
  const cx = (i: number) => (n === 1 ? (L + W - R) / 2 : L + (W - L - R) * i / (n - 1));
  const ymax = ytop;
  const y = (v: number) => T + H1 - H1 * (v || 0) / ymax, lab = xLabels(n, W - L - R);
  const step = n === 1 ? W - L - R : (W - L - R) / (n - 1);
  return (
    <div className="chart" id={id} ref={box}>
      {legend}
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img" aria-label={label}>
        {[0, 1, 2, 3, 4].map(k => { const v = ymax * k / 4; return (
          <g key={k}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--rule)" /><text x={L - 6} y={y(v) + 4} textAnchor="end">{grouped(+v.toFixed(1))}</text></g>); })}
        {periods.map((p, i) => (lab(i) ? <text key={p} x={cx(i)} y={H - 4} textAnchor="middle">{dm(p)}</text> : null))}
        {series.map(se => (
          <g key={se.name}>
            <path d={"M" + se.values.map((v, i) => `${cx(i)},${y(v)}`).join("L")} fill="none" stroke={LINE[se.line].stroke} strokeDasharray={LINE[se.line].dash} strokeWidth={2} strokeLinejoin="miter" strokeLinecap="square" />
            {n <= 31 && se.values.map((v, i) => <rect key={i} x={cx(i) - 3.5} y={y(v) - 3.5} width={7} height={7} fill={LINE[se.line].stroke} stroke="var(--panel)" strokeWidth={1.5} />)}
          </g>
        ))}
        {cross != null && <line x1={cx(cross)} x2={cx(cross)} y1={T} y2={T + H1} stroke="var(--ink)" strokeDasharray="2 2" />}
        {periods.map((p, i) => <rect key={"h" + p} x={cx(i) - step / 2} y={0} width={step} height={T + H1} fill="transparent" className="hit"
          onPointerMove={e => { setCross(i); showTip(e, `<div class="d">${week ? "由 " : ""}${dlong(p)}${week ? " 起一週" : ""}</div>`
            + series.map(se => tipRow(LINE[se.line].cls, se.name, fmt(se.values[i]))).join("")); }}
          onPointerLeave={() => { setCross(null); hideTip(); }} />)}
      </svg>
    </div>
  );
}
