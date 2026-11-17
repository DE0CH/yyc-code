// The item page (read-only): everything the system knows about one post (NAVIGATION.md
// "The item page"). Parts the pipeline has not produced yet are a static line saying what is pending (lib/pending.ts);
// the only spinner is <Wait>, for a request in flight (lib/Wait.tsx). Read-only, like the whole site.
import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useCanGoBack, useLocation, useNavigate, useParams, useRouter, useSearch } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";
import { itemDefault, useProfile } from "../shell/profile";
import { DeyaoOnly } from "../lib/DeyaoOnly";
import { itemAIQuery, itemByUrlQuery, itemCoverageQuery, itemQuery, itemTextQuery, itemVideoQuery, keys, useMe, useProfiles } from "../queries";
import { useLive } from "../live";
import { HttpError, type Item, type ItemAI, type ItemPicture, type ItemRef, type ItemTag, type ItemText, type NotScored, type Profile, type StreamScore } from "../api";
import { Icon } from "../lib/icons";
import { SiteLink } from "../lib/SiteLink";
import { AiBadge, AiTranscribedBadge, SourceTitle } from "../lib/AiBadge";
import { langOf, Txt } from "../lib/lang";
import { ENG_ZH, LABEL_ZH, REGION_ZH, SENT_ZH, TYPE_ZH } from "../lib/zh";
import { dayAdd, fmtN } from "../lib/time";
import { dt } from "../lib/dates";
import { anyPending, commentsPending, tagsPending, textPending } from "../lib/pending";
import { Pending, Wait } from "../lib/Wait";

function BackTool() {
  const canGoBack = useCanGoBack(), router = useRouter(), navigate = useNavigate();
  return (
    <button className="yyc-tool back" aria-label="返回" title="返回"
      onClick={() => (canGoBack ? router.history.back() : navigate({ to: "/" }))}><Icon name="prev" /></button>
  );
}

function Missing({ msg, url }: { msg: string; url?: string }) {
  return (
    <Shell section="item" title="找不到這則帖子" pick="page" tools={<BackTool />}>
      <main><section className="card"><h1 style={{ marginTop: 0 }}>找不到這則帖子</h1><p className="sub">{msg}</p>
        {url && /^https?:\/\//.test(url) && <div className="acts"><a className="btn primary" href={url} target="_blank" rel="noopener noreferrer"><Icon name="out" />開啟原文</a></div>}
      </section></main>
    </Shell>
  );
}

function Loading({ onRetry }: { onRetry: () => void }) {
  return <Shell section="item" title="帖子" pick="page" tools={<BackTool />}><main><Wait className="loading" what="載入帖子" onRetry={onRetry} /></main></Shell>;
}

/** /item?url=<publisher url>: find the post, then replace the URL with its /item/<id> */
export function ItemByUrl() {
  const { url } = useSearch({ from: "/item/" });
  const navigate = useNavigate(), qc = useQueryClient(), loc = useLocation();
  const q = useQuery({ ...itemByUrlQuery(url || ""), enabled: !!url });
  useEffect(() => {
    if (!q.data) return;
    qc.setQueryData(keys.item(q.data.id), q.data);
    navigate({ to: "/item/$id", params: { id: q.data.id }, search: (p: Record<string, unknown>) => ({ p: p.p as string | undefined }), hash: loc.hash, replace: true });
  }, [q.data, qc, navigate, loc.hash]);
  if (!url) return <Missing msg="網址缺少帖子編號。" />;
  if (q.data === null) return <Missing url={url} msg="這個連結不在系統收集的帖子之中。" />;
  if (q.isError) return <Missing url={url} msg={q.error instanceof HttpError ? `伺服器回應 ${q.error.status}。` : "伺服器沒有回應，請稍後再試。"} />;
  return <Loading onRetry={() => q.refetch()} />;
}

export function ItemPage() {
  const { id } = useParams({ from: "/item/$id" });
  const loc = useLocation();
  const me = useMe();
  const profiles = useProfiles().data || [];
  const prof = useProfile();
  const itemQ = useQuery({ ...itemQuery(id), enabled: Number.isFinite(id) });
  const D = itemQ.data;
  // while parts are still being processed, check again (the stub refetches; later the Durable Object pushes)
  useLive(keys.item(id), { enabled: !!D && anyPending(D) });

  const user = me.data ?? null;
  // approved accounts: the full text (/app/item/text). Usually here already: the route's loader reads it with the
  // post (lib/load.ts loadGated), into the server's HTML or on the hover before the tap
  const textQ = useQuery({ ...itemTextQuery(id), enabled: !!user && !!D });
  const TEXT = textQ.data ?? null;

  // approved accounts: every stream's score, reason, category and threshold (/app/item/ai), as on 串流; read again
  // when the post has been scored again (its scoring time moved while the page is open)
  const aiQ = useQuery({ ...itemAIQuery(id), enabled: !!user && !!D });
  const AI: ItemAI | null = aiQ.data ?? null;  // a failed read says so (未能載入評分) with 重試
  const qc = useQueryClient();
  const scoredAt = D ? D.scoring?.at ?? "" : null;
  const scoredSeen = useRef<{ id: number; at: string } | null>(null);
  useEffect(() => {
    if (scoredAt === null) return;
    const seen = scoredSeen.current;
    scoredSeen.current = { id, at: scoredAt };
    if (seen && seen.id === id && seen.at !== scoredAt) void qc.invalidateQueries({ queryKey: keys.itemAI(id) });
  }, [id, scoredAt, qc]);

  // #text / #thread: scroll there once the page is drawn
  const scrolled = useRef(false);
  useEffect(() => {
    if (!D || scrolled.current || !loc.hash) return;
    scrolled.current = true;
    document.getElementById(loc.hash)?.scrollIntoView();
  }, [D, loc.hash]);

  // the stream when the link carries none (?p=; shell/profile.ts itemDefault): rendered as such from the server on (it
  // reads only what the server sees), then written into the URL
  const navigate = useNavigate();
  const def = D && profiles.length && !prof.fromURL ? itemDefault(D, profiles, prof.stored) : null;
  useEffect(() => {
    if (!def) return;
    navigate({ to: ".", search: (prev: Record<string, unknown>) => ({ ...prev, p: def }), hash: loc.hash, replace: true });
  }, [def, navigate, loc.hash]);

  if (!Number.isFinite(id)) return <Missing msg="網址缺少帖子編號。" />;
  if (D === null) return <Missing msg="系統沒有這個編號的帖子。" />;
  if (itemQ.isError) {
    const st = itemQ.error instanceof HttpError ? itemQ.error.status : 0;
    return <Missing msg={st === 404 ? "系統沒有這個編號的帖子。" : st ? `伺服器回應 ${st}。` : "伺服器沒有回應，請稍後再試。"} />;
  }
  if (!D) return <Loading onRetry={() => itemQ.refetch()} />;

  const PROFILES: Record<string, Profile> = Object.fromEntries(profiles.map(p => [p.id, p]));
  // the profile the 數據分析 button opens: the picker's when the post matched it, else the first it matched
  const shown = def ?? prof.effective;
  const pid = D.matched.length ? (shown && D.matched.includes(shown) ? shown : D.matched[0]) : null;

  return (
    <Shell section="item" title={D.title.slice(0, 60)} pick="page" pickDefault={def} tools={<BackTool />}>
      <main>
        <Head D={D} pid={pid} />
        <div className="layout">
          <div>
            <TextCard D={D} TEXT={TEXT} user={user} textLoading={!!user && textQ.isPending && textQ.fetchStatus !== "idle"}
              textFailed={!!user && textQ.isError} retry={() => textQ.refetch()} />
            <LinksCard D={D} />
            <PicturesCard D={D} TEXT={TEXT} />
            <VideoCard D={D} user={user} />
            <MediaCard D={D} TEXT={TEXT} user={user} />
            <ThreadCard D={D} TEXT={TEXT} user={user} />
          </div>
          <div>
            <AiCard D={D} AI={AI} user={user} aiFailed={aiQ.isError} retry={() => aiQ.refetch()} />
            <TagsCard D={D} />
            <MatchedCard D={D} />
            <ArticleCard D={D} />
            <CoverageCard D={D} />
            <OriginCard D={D} />
            <EngagementCard D={D} />
            <MoreCard D={D} pid={pid ?? shown} PROFILES={PROFILES} />
            <DeyaoOnly>
              <p>{D.sid ? <a href={`/app/proof?sid=${encodeURIComponent(D.sid)}&at=${D.id}`}>This post in the proof view</a> : "Not from a listed source (no proof view)"}</p>
            </DeyaoOnly>
          </div>
        </div>
      </main>
    </Shell>
  );
}

const dashSearch = (D: Item, pid: string) => ({ v: "posts", p: pid, from: D.day, to: D.day, post: String(D.id) });

function Head({ D, pid }: { D: Item; pid: string | null }) {
  const [toast, setToast] = useState("");
  const type = (D.source_type && TYPE_ZH[D.source_type]) || D.source_type || "";
  const board = D.board && D.board !== D.website && D.board !== D.author ? D.board : "";

  async function copy() {
    const u = location.origin + "/item/" + D.id;
    let ok = false;
    try { await navigator.clipboard.writeText(u); ok = true; } catch {
      const t = document.createElement("textarea"); t.value = u; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select(); try { ok = document.execCommand("copy"); } catch { /* ignore */ } t.remove();
    }
    setToast(ok ? "已複製連結：" + u : "未能複製，請手動複製：" + u);
  }

  return (
    <section className="card" aria-labelledby="h-title">
      <div className="kicker">
        <b>{D.website || D.source || ""}</b>{board && <span>{board}</span>}
        {type && <span>{type}</span>}{D.comment && <span>留言</span>}
        {D.region && <span>{REGION_ZH[D.region] || D.region}</span>}
      </div>
      <h1 id="h-title" className={D.title.length > 90 ? "long" : undefined}><AiBadge x={D} /><Txt s={D.title} /></h1>
      <SourceTitle x={D} />
      <div className="times">
        {D.author && <span className="by">作者 <b>{D.author}</b></span>}
        <div className="stamps">
          <span className="st"><em>發佈</em> <b className={"num" + (D.published ? "" : " none")}>{D.published ? dt.dateTime(D.published) : "來源沒有提供"}</b></span>
          <span className="st"><em>首次收錄</em> <b className="num">{dt.dateTime(D.seen)}</b></span>
        </div>
      </div>
      <div className="acts">
        <a className="btn primary" href={D.link} target="_blank" rel="noopener noreferrer"><Icon name="out" />開啟原文<span className="src">{D.website || "出版機構"}</span></a>
        {pid ? <SiteLink className="btn" to="/dashboard" search={dashSearch(D, pid)}><Icon name="chart" />在數據分析開啟</SiteLink>
          : <span className="note-inline">不在任何設定檔中</span>}
        <button className="btn" onClick={copy}><Icon name="link" />複製連結</button>
      </div>
      <div className="toast" role="status" aria-live="polite">{toast}</div>
    </section>
  );
}

const loginNext = (D: Item, hash = "") => `/app/login?next=${encodeURIComponent("/item/" + D.id + hash)}`;

/** where the stored text came from (paywall.py): the reader's label, an archive.today copy with its link */
function Via({ D }: { D: Item }) {
  if (!D.text_via_label) return null;
  return D.text_via_url ? <a href={D.text_via_url} target="_blank" rel="noopener">{D.text_via_label}</a> : <>{D.text_via_label}</>;
}

/** no text (Deyao, 2026-10-20: never "the source gives only a title"): the recorded state says why (text_state) */
const NO_TEXT = (D: Item) => D.text_state === "whole" && !D.title.endsWith("…") ? "這則帖子的全部文字就是上面的標題。" : "未能取得內文。";
const DUE_LINE = "正在收集內文：系統每 30 分鐘讀取一輪出版機構網頁，讀到後會在這裏顯示。";

function TextCard({ D, TEXT, user, textLoading, textFailed, retry }: {
  D: Item; TEXT: ItemText | null; user: string | null; textLoading: boolean; textFailed: boolean; retry: () => void;
}) {
  const pending = textPending(D);
  let body: React.ReactNode;
  if (TEXT && TEXT.text) {
    body = <><p className="body" lang={langOf(TEXT.text)}>{TEXT.text}</p>{TEXT.cached_at && <p className="sub" style={{ margin: "10px 0 0" }}>全文保存於 {dt.dateTime(TEXT.cached_at)}{D.text_via_label && <>，來自<Via D={D} /></>}，只供已批准的帳戶閱讀。</p>}</>;
  } else if (TEXT && !pending) {
    body = <p className="empty">{NO_TEXT(D)}</p>;
  } else {
    body = <>
      {D.extract ? <p className="extract" lang={langOf(D.extract)}>{D.extract}</p> : D.text_chars || pending ? null : <p className="empty">{NO_TEXT(D)}</p>}
      {pending ? <Pending>{DUE_LINE}</Pending>
        : textLoading ? <Wait what="載入全文" onRetry={retry} /> : textFailed && D.text_chars ? <Wait what="載入全文" failed onRetry={retry} /> : null}
      {!!D.text_chars && !textLoading && (
        <div className="lock">系統保存了全文（{fmtN(D.text_chars)} 字），只供 Deyao 批准的帳戶閱讀；其他人請按「開啟原文」到出版機構網站閱讀。
          {user === null && <><a href={loginNext(D)}>已獲批准請登入</a>。</>}</div>
      )}
    </>;
  }
  const sub = TEXT && TEXT.text ? "全文" : D.extract ? "摘錄" : "";
  return <section className="card" id="text"><h2>內容{sub && <span className="sub">{sub}</span>}</h2>{body}</section>;
}

function PicTile({ p, TEXT }: { p: ItemPicture; TEXT: ItemText | null }) {
  const txt = p.hash ? TEXT?.pictures?.[p.hash] : undefined;
  const tip = p.status === "scored" ? `${p.reason || ""}（${p.score}/100）` : p.status === "failed" ? "未能分析" : "尚未分析（排隊中）";
  return (
    <figure className="pic">
      <a href={p.url} target="_blank" rel="noopener noreferrer"><img src={p.url} alt={p.reason || ""} title={tip} loading="lazy" referrerPolicy="no-referrer" /></a>
      <figcaption>{p.meaningful && <span>{p.reason}</span>}
        {txt ? <details><summary>圖中文字</summary><p className="body">{txt}</p></details>
          : p.chars > 0 && !TEXT ? <span className="sub">圖中文字只供已批准的帳戶閱讀</span> : null}</figcaption>
    </figure>
  );
}

/** 圖片: the post's pictures (images.py), the meaningful ones first, the rest folded under 其他圖片（N） */
function PicturesCard({ D, TEXT }: { D: Item; TEXT: ItemText | null }) {
  const ps = D.pictures?.items || [];
  if (!ps.length) return null;
  const top = ps.filter(p => p.meaningful), rest = ps.filter(p => !p.meaningful);
  return (
    <section className="card" id="pictures"><h2>圖片<span className="sub">{top.length} 張有資訊</span></h2>
      {top.length ? <div className="pics">{top.map(p => <PicTile key={p.url} p={p} TEXT={TEXT} />)}</div> : <p className="empty">沒有帶資訊的圖片。</p>}
      {rest.length > 0 && <details className="more-pics"><summary>其他圖片（{rest.length}）</summary>
        <div className="pics">{rest.map(p => <PicTile key={p.url} p={p} TEXT={TEXT} />)}</div></details>}
      <p className="sub" style={{ margin: "8px 0 0" }}>由 AI 按圖片帶出的資訊評分（0–100，{D.pictures!.meaningful_from} 分或以上列為有資訊）；滑鼠停在圖片上可看原因。</p>
    </section>
  );
}

/** 影片: the TikTok post's video, saved by the system (tiktok_video.py); played and downloaded straight from YYC's
 *  storage through links made for this page load (approved accounts, /app/item/video; valid one hour, renewed while
 *  the page stays open) */
function VideoCard({ D, user }: { D: Item; user: string | null }) {
  const v = D.video;
  const q = useQuery({ ...itemVideoQuery(D.id), enabled: !!user && v?.status === "ok", refetchInterval: 45 * 60e3 });
  if (!v) return null;
  const L = q.data;
  const size = v.bytes ? `${(v.bytes / 1048576).toFixed(1)} MB` : "";
  const meta = [v.duration != null ? mmss(v.duration) : "", v.width && v.height ? `${v.width}×${v.height}` : "", size].filter(Boolean);
  return (
    <section className="card" id="video"><h2>影片{meta.map(x => <span key={x} className="sub">{x}</span>)}</h2>
      {v.status === "queued" ? <p className="empty">影片下載排隊中。</p>
        : v.status === "gone" ? <p className="empty">TikTok 上已沒有這段影片（已刪除或設為私人），未能保存。</p>
        : v.status === "failed" ? <p className="empty">未能下載這段影片。</p>
        : user === null ? <div className="lock tight">系統保存了這段影片，只供 Deyao 批准的帳戶觀看及下載。<a href={loginNext(D, "#video")}>已獲批准請登入</a>。</div>
        : q.isError ? <Wait what="載入影片" failed onRetry={() => q.refetch()} />
        : !L?.url ? <Wait what="載入影片" onRetry={() => q.refetch()} />
        : <>
            <video className="saved-video" src={L.url} controls playsInline preload="metadata" data-testid="saved-video"
              style={{ width: "100%", maxHeight: "70vh", background: "#000" }} />
            <p style={{ margin: "8px 0 0" }}><a href={L.download!} download={L.filename} data-testid="video-download">下載影片</a>
              <span className="sub">（MP4{size ? "，" + size : ""}）</span></p>
          </>}
      {v.status === "ok" && <p className="sub" style={{ margin: "8px 0 0" }}>影片由系統在帖子通過初步評分時從 TikTok 下載保存；TikTok 上的原帖日後刪除，這裏仍可觀看。</p>}
    </section>
  );
}

const mmss = (s: number | null) => s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** 影片逐字稿: the post's video and audio, transcribed (media.py); the text only for approved accounts, like the full text */
function MediaCard({ D, TEXT, user }: { D: Item; TEXT: ItemText | null; user: string | null }) {
  const ms = D.media || [];
  if (!ms.length) return null;
  const full = TEXT?.media || {};
  const capt = TEXT?.media_captions || {};  // the YouTube clips' own subtitles, beside the AI transcript
  const done = ms.some(m => m.status === "done" || m.status === "captions");
  return (
    <section className="card" id="media"><h2>影片逐字稿<span className="sub">{ms.length} 段</span></h2>
      {ms.map((m, i) => (
        <div className="clip" key={m.key}>
          <div className="clip-h"><b>{ms.length > 1 ? `片段 ${i + 1}` : "影片／音訊"}</b>
            {m.status === "done" && <AiTranscribedBadge m={m} />}
            {m.status === "done" && m.by_ai === false && m.model_name && <span className="sub">{m.model_name}</span>}
            {m.host && <span className="sub">{m.host}</span>}{m.duration != null && <span className="sub">{mmss(m.duration)}</span>}</div>
          {m.status === "done" && full[m.key] ? <p className="body transcript" lang={langOf(full[m.key])}>{full[m.key]}</p>
            : m.status === "done" && !m.chars ? <p className="empty">片段沒有說話內容。</p>
            : m.status === "done" ? <div className="lock tight">系統保存了逐字稿（{fmtN(m.chars)} 字），只供 Deyao 批准的帳戶閱讀。
                {user === null && <><a href={loginNext(D)}>已獲批准請登入</a>。</>}</div>
            : m.status === "failed" ? <p className="empty">未能轉寫這段片段{m.error ? `：${m.error}` : ""}。</p>
            : m.status === "no_direct" ? <p className="empty">無法直接下載，未轉錄{m.error ? `（${m.error}）` : ""}。</p>
            : m.status === "captions" ? null
            : <p className="empty" data-pending="">{m.queue ? `逐字稿排隊中，第 ${m.queue} 個。` : "上次未能轉寫，稍後會再試。"}</p>}
          {m.captions_name && <details className="captions"><summary>{m.captions_name}（{fmtN(m.captions_chars)} 字）</summary>
            {capt[m.key] ? <p className="body transcript" lang={langOf(capt[m.key])}>{capt[m.key]}</p> : <p className="sub">字幕全文只供已批准的帳戶閱讀。</p>}</details>}
        </div>
      ))}
      {done && <p className="sub" style={{ margin: "10px 0 0" }}>{ms.some(m => m.status === "done" && m.by_ai) ? "標有「AI 轉錄」的逐字稿由語音模型轉寫（滑鼠停在標籤上可看模型及時間），或有錯字；" : ""}{ms.some(m => (m.captions_name || "").startsWith("YouTube 自動字幕")) ? "「YouTube 自動字幕」是 YouTube 自動產生的字幕，或有錯字；" : ""}[分:秒] 是片段內的時間；全文只供已批准的帳戶閱讀。</p>}
    </section>
  );
}

function ThreadCard({ D, TEXT, user }: { D: Item; TEXT: ItemText | null; user: string | null }) {
  const [all, setAll] = useState(false);
  if (D.thread == null || (commentsPending(D) && !D.comment)) {
    return <section className="card" id="thread"><h2>留言及回覆</h2><Pending>留言尚未收集：系統會在收錄帖子後讀取留言。</Pending></section>;
  }
  const th = D.thread!, full = TEXT?.comments || {};
  const parent = D.comment ? (th.parent
    ? <p style={{ margin: "0 0 10px" }}>這是〈<Link className="ln" to="/item/$id" params={{ id: th.parent.id }}><Txt s={th.parent.title} /></Link>〉的一則留言。</p>
    : <p className="sub" style={{ margin: "0 0 10px" }}>這是一則留言；原帖不在資料庫中。<a href={D.thread_url || undefined} target="_blank" rel="noopener">原帖連結</a></p>) : null;
  if (!th.total && !D.comment) {
    return <section className="card" id="thread"><h2>留言及回覆</h2><p className="empty">{th.stated ? `來源顯示有 ${fmtN(th.stated)} 則留言，系統未有收集。` : "系統沒有收集到這則帖子的留言。"}</p></section>;
  }
  const shown = th.comments.length;
  // a forum thread's rows are replies (LIHKG, 香港討論區); a social post's are comments
  const word = /lihkg\.com|discuss\.com\.hk/.test(D.thread_url || D.url || "") ? "回覆" : "留言";
  return (
    <section className="card" id="thread">
      <h2>留言及回覆<span className="sub">{fmtN(th.total)} 則{th.stated && th.stated > th.total ? `（來源顯示 ${fmtN(th.stated)} 則）` : ""}{shown < th.total ? `，顯示最早的 ${shown} 則` : ""}</span></h2>
      {parent}
      {/* the first 20 shown until 顯示全部 (the row of this comment always): CSS folds the rest */}
      <ul className={"plain" + (shown > 20 && !all ? " folded" : "")} id="cms">{th.comments.map((c, i) => (<Fragment key={c.id}>
        <li className={"cm" + (c.id === D.id ? " me" : "")}>
          <div className="who">{c.author || "（匿名）"}</div>
          <div className="tx" lang={langOf(full[c.id] ?? c.text)}>{full[c.id] ?? c.text}</div>
          <div className="m"><span className="num">{dt.dateTime(c.time)}</span>{!!c.likes && <span>{fmtN(c.likes)} 讚好</span>}{!!c.dislikes && <span>{fmtN(c.dislikes)} 負評</span>}
            {c.id !== D.id ? <Link to="/item/$id" params={{ id: c.id }}>詳情</Link> : <span>（本則）</span>}</div>
        </li>
        {th.scored_at && c.scored && !th.comments[i + 1]?.scored && <li className="scorecut" role="separator">
          {`以上${word}已納入評分（${dt.dateTime(th.scored_at)}）${i + 1 < shown ? `；以下是之後收集的${word}` : ""}`}</li>}
      </Fragment>))}</ul>
      {shown > 20 && !all && <button className="btn" id="unfold" style={{ marginTop: 8 }} onClick={() => setAll(true)}>顯示全部 {shown} 則</button>}
      {!user && th.comments.some(c => c.cut) && <p className="sub" style={{ margin: "8px 0 0" }}>較長的留言只顯示開頭；全文只供已批准的帳戶閱讀。</p>}
    </section>
  );
}

const Face = ({ s }: { s: string | null }) =>
  s ? <span className={"face " + s}><Icon name={s} />{SENT_ZH[s] || s}</span> : <span className="sub">未有情緒</span>;

const Lab = ({ D, l }: { D: Item; l: string }) =>
  <span className={"lab " + l} title={D.vocab?.label_info?.[l]?.meaning}>{LABEL_ZH[l] || l}</span>;

// the stream scorer's analysis (NAVIGATION.md "The item page", AI 分析)
const NOT_SCORED: Partial<Record<NotScored, string>> = {
  before: "這則帖子在串流評分開始前已收錄，所以沒有評分。", comment: "這是一則留言；留言評分未開啟。",
  old: "這則帖子發佈超過兩日才收錄，不作串流評分。",
};
/** why it was not scored; the near-empty gate (thin.py): 「內容不足，未評分」 (rejected) or held while a transcript,
 *  picture text or a link's content may still come, with D.thin.why (units, what it waits for, until when) */
function NotScoredText({ D, k }: { D: Item; k: NotScored | null }) {
  if (k === "thin" || k === "thin_wait")
    return <><b className="thin">{k === "thin" ? "內容不足，未評分" : "內容不足，等待更多內容"}</b>{D.thin?.why ? "：" + D.thin.why : "。"}</>;
  return <>{(k && NOT_SCORED[k]) || "沒有串流評分。"}</>;
}

/** the browser's clock, after hydration (null in the server render and the first browser render) */
function useNow() {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => { setNow(Date.now()); const t = setInterval(() => setNow(Date.now()), 60_000); return () => clearInterval(t); }, []);
  return now;
}

const MARKET_ZH: Record<string, string> = { hk: "香港", mainland: "內地" };
/** a split stream (Galbot): after the relevance score, the binary market classifier (香港 | 內地) chose its stream */
function MarketLine({ s }: { s: StreamScore }) {
  if (!("market" in s) || !s.passed) return null;
  const m = s.market;
  if (m && m.how === "only") return null;  // the classifier is off (one child on): every post goes to that stream
  if (!m) return <div className="mk"><span className="sub">市場：</span><span className="sub">尚未分類（排隊中）</span></div>;
  return (
    <div className="mk"><span className="sub">市場：</span><b>{MARKET_ZH[m.market] || m.market}</b> → {m.stream
      ? <Link to="/" search={{ p: m.stream }}>{m.name}</Link> : <span className="sub">（此串流未啟用）</span>}{" "}
      <span className="sub">{m.how === "model" ? "AI 判斷" : "AI 兩次回答均無效，按來源地區歸類"}</span></div>
  );
}

/** AI 分析: the stream scorer's sentiment and labels (everyone), every stream's 0–100 score against its threshold,
 *  the model's reason and the category (approved accounts, /app/item/ai), and what each label means */
function AiCard({ D, AI, user, aiFailed, retry }: { D: Item; AI: ItemAI | null; user: string | null; aiFailed: boolean; retry: () => void }) {
  const sc = D.scoring;
  const now = useNow();
  const fresh = now != null && now - Date.parse(D.seen) < 30 * 60e3;
  const wait = <Pending>AI 尚未評分（排隊中）。</Pending>;
  const notYet = <p className="empty">AI 尚未為這則帖子評分。</p>;
  const info = D.vocab?.label_info || {};
  let top: React.ReactNode = null, rows: React.ReactNode = null;
  if (sc) top = <>
    <div className="ai-tags"><span className="sub">整體判斷</span><Face s={sc.sentiment} />
      {sc.labels.length ? sc.labels.map(l => <Lab key={l} D={D} l={l} />) : <span className="sub">沒有標籤</span>}</div>
    {sc.rescoring && <p className="sub" style={{ margin: "6px 0 0" }}>內容已更新，AI 會重新評分。</p>}
    {sc.second_due && <Pending>第二次評分於 {dt.when(sc.second_due)} 進行，連同屆時的留言。</Pending>}
  </>;
  if (AI) {
    // a stream the post was filed in stays its stream (filed ⇒ member), shown with its score now beside the score it
    // was filed on; under the threshold now, one sentence (item.py rescored) says what happened, with a neutral badge
    const got = AI.streams.filter(s => s.score != null || s.kept), low = AI.streams.filter(s => s.score == null && !s.kept);
    const nowScore = (s: StreamScore) => (s.score == null ? `低於 ${AI.floor}` : `${s.score}`);
    if (AI.scored_at || got.length) {
      rows = <>
        {got.map(s => (
          <div key={s.stream} className={"sc" + (s.passed ? " pass" : "")}>
            <div className="top">
              <span className={"pip" + (s.passed ? " on" : "")} aria-hidden="true" /><span className="nm">{s.name}</span>{s.title && s.title !== s.name && <span className="sub">{s.title}</span>}
              {s.passed ? <span className="badge yes">達標</span> : s.rescored ? <span className="badge">已收錄（重新評分後低於門檻）</span>
                : s.kept ? <span className="badge yes">已收錄</span> : <span className="badge no">未達標</span>}
              {s.category && <span className="lab">{s.category}</span>}
              <span className="v num">{s.score == null ? `<${AI.floor}` : s.score}<small> / 100</small></span>
            </div>
            <div className="scbar" role="img" aria-label={`評分 ${nowScore(s)}，門檻 ${s.threshold}`}>
              <i style={{ width: `${s.score || 0}%` }} /><b style={{ left: `${s.threshold}%` }} />
            </div>
            <div className="m"><span>門檻 {s.threshold} 分</span>
              {s.kept ? <>
                <Link to="/" search={{ p: s.market?.stream || s.stream, d: s.kept.day }}>已列入 {dt.date(s.kept.day)} 的串流</Link>
                {!s.rescored && s.kept.score != null && s.kept.score !== s.score && <span>收錄時 {s.kept.score} 分，重新評分後 {nowScore(s)} 分</span>}
              </> : s.passed ? <span>未列入串流（同一標題已在串流中或屬留言）</span> : null}</div>
            {s.rescored && <div className="rescored">{s.rescored}</div>}
            {s.reason && <div className="why">{s.reason}</div>}
            {s.screen && <div className="screen sub">{s.model} 評分；初步篩選 {s.screen.model} {s.screen.score} 分{s.screen.reason ? `（${s.screen.reason}）` : ""}</div>}
            <MarketLine s={s} />
          </div>
        ))}
        {low.length > 0 && <p className="sub" style={{ margin: "8px 0 0" }}>{low.map(s => s.name).join("、")}：低於 {AI.floor} 分（與該串流無關，分數不記錄）。</p>}
      </>;
    } else rows = AI.not_scored === "due" ? (fresh ? wait : notYet) : <p className="empty"><NotScoredText D={D} k={AI.not_scored} /></p>;
  } else if (user) rows = <Wait what="載入評分" failed={aiFailed} onRetry={retry} />;
  else if (sc) rows = <div className="lock tight">各串流的評分（0–100）、理由及門檻只供 Deyao 批准的帳戶查看。
    {user === null && <><a href={loginNext(D, "#ai")}>已獲批准請登入</a>。</>}</div>;
  if (!sc && !user) top = D.not_scored && D.not_scored !== "due" ? <p className="empty"><NotScoredText D={D} k={D.not_scored} /></p>
    : fresh ? wait : notYet;
  const used = [...new Set([...(sc ? sc.labels : []), ...(D.tags || []).flatMap(t => t.labels || [])])];
  const when = AI && AI.scored_at ? AI.scored_at : sc && sc.at;
  // the models behind the scores: stage 1 screens every stream, stage 2 scores again those it put at streams.SCREEN or more
  const finals = AI ? [...new Set(AI.streams.filter(s => s.screen).map(s => s.model))] : [];
  const models = AI?.model ? (finals.length ? `${AI.model} 篩選，${finals.join("、")} 覆核` : AI.model) : "";
  return (
    <section className="card" id="ai">
      <h2>AI 分析<span className="sub">串流評分</span>{models && <span className="sub">{models}</span>}{when && <span className="sub">{dt.dateTime(when)}</span>}</h2>
      {top}{top && rows && <div style={{ height: 10 }} />}{rows}
      {used.length > 0 && (
        <details className="legend" open><summary>標籤的意思</summary>
          <dl>{used.map(l => <Fragment key={l}><dt><Lab D={D} l={l} /></dt>
            <dd>{info[l] ? `${info[l].meaning}（${info[l].group_zh}）` : ""}</dd></Fragment>)}</dl>
        </details>
      )}
    </section>
  );
}

function TagsCard({ D }: { D: Item }) {
  const pending = tagsPending(D);
  const tags: ItemTag[] = D.tags || [];
  return (
    <section className="card" id="tags">
      <h2>AI 標籤<span className="sub">每個設定檔各自標記</span></h2>
      {tags.map(t => (
        <div className="prof" key={t.profile}>
          <div className="top">
            <span className="nm"><SiteLink to="/dashboard" search={dashSearch(D, t.profile)}>{t.title}</SiteLink></span><span className="sub">{t.name}</span>
            {t.relevant ? <span className="badge yes">相關</span> : <span className="badge">不相關</span>}
            {t.corrected && <span className="badge fixed" title={(t.corrected_by || "") + " " + dt.dateTime(t.corrected_at)}>已人手修正</span>}
          </div>
          <div className="row"><Face s={t.sentiment} />{(t.labels || []).map(l => <Lab key={l} D={D} l={l} />)}
            {t.theme && <span className="lab">#{t.theme}</span>}</div>
        </div>
      ))}
      {pending ? <Pending>AI 尚未標記（排隊中）。</Pending> : !tags.length && <p className="empty">沒有設定檔標記過這則帖子。</p>}
    </section>
  );
}

function MatchedCard({ D }: { D: Item }) {
  const tagged = (D.tags || []).filter(t => t.relevant);
  const none = !D.streams.length && !tagged.length;
  return (
    <section className="card">
      <h2>串流及設定檔</h2>
      {none ? (tagsPending(D) ? <Pending>AI 尚未判斷這則帖子屬於哪些設定檔（排隊中）。</Pending> : <p className="empty">不在任何設定檔中。</p>) : (
        <ul className="plain">
          {D.streams.map(s => (
            <li key={"s" + s.stream + s.day}>
              <Link className="ln" to="/" search={{ p: s.stream, d: s.day }}>{s.name} 串流</Link>
              <div className="m"><span>{s.day}</span>{s.category && <span>類別：{s.category}</span>}</div>
            </li>
          ))}
          {tagged.map(t => (
            <li key={"p" + t.profile}>
              <SiteLink className="ln" to="/dashboard" search={dashSearch(D, t.profile)}>{t.title}</SiteLink>
              <div className="m"><span>{t.name}</span><span>數據分析</span></div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ---- LINKS (item.links; Deyao, 2026-10-20: "this post has a link") ----
   Every link the post carries: once link following has read them (follow.py: the post reached a stream), at the
   address they lead to with the outlet that published it and our own copy; else as they stand in the text. */
function LinksCard({ D }: { D: Item }) {
  const ls = D.links || [];
  if (!ls.length) return null;
  return (
    <section className="card" id="links">
      <h2>連結<span className="sub">{ls.length} 個</span></h2>
      <ul className="plain">{ls.map((l, i) => (
        <li key={i}>
          <a className="ln clip3" href={l.url} target="_blank" rel="noopener noreferrer">{l.name || l.host || l.url}</a>
          <div className="m"><span className="linkurl">{l.url}</span>
            {l.item_id != null && l.item_id !== D.id && <Link to="/item/$id" params={{ id: l.item_id }}>本系統已收錄</Link>}</div>
        </li>
      ))}</ul>
    </section>
  );
}

const RefList = ({ list }: { list: ItemRef[] }) => (
  <ul className="plain refs">{list.map(x => (
    <li key={x.id}>
      <Link className="ln clip3" to="/item/$id" params={{ id: x.id }}><AiBadge x={x} /><Txt s={x.title} /></Link>
      <div className="m"><span>{x.website || ""}</span>{x.author && x.author !== x.website && <span>{x.author}</span>}<span className="num">{dt.dateTime(x.time)}</span></div>
    </li>
  ))}</ul>
);

/* ---- ARTICLE (posts.article_id) ----
   The other posts that are the same content as this one (the same article syndicated, reposted or quoted in full).
   /api/item sends `article: null` when the post is alone in its article. */
function ArticleCard({ D }: { D: Item }) {
  const posts = (D.article?.posts || []).filter(p => p.id !== D.id);
  if (!D.article || !posts.length) return null;
  return (
    <section className="card" id="article">
      <h2>同一文章的其他帖子<span className="sub">{posts.length} 則</span></h2>
      <RefList list={posts} />
    </section>
  );
}

/** loaded after the rest of the page (the slowest part: titles of every post within 3 days compared) */
function CoverageCard({ D }: { D: Item }) {
  const q = useQuery({ ...itemCoverageQuery(D.id), enabled: !D.comment });
  // the server's prefetch may or may not have answered by the time the server renders: the server and the first
  // browser render both show 載入中, so hydration never depends on that race
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (D.comment) return null;
  const c = (mounted && q.data) || [];
  return (
    <section className="card">
      <h2>同一事件的其他報道{c.length > 0 && <span className="sub">{c.length} 篇</span>}</h2>
      {!mounted || q.isPending ? <Wait what="載入同一事件的其他報道" onRetry={() => q.refetch()} />
        : q.isError ? <Wait what="載入同一事件的其他報道" failed onRetry={() => q.refetch()} />
        : c.length ? <RefList list={c} /> : <p className="empty">前後 3 日內沒有其他媒體報道相同標題的新聞。</p>}
    </section>
  );
}

function OriginCard({ D }: { D: Item }) {
  const rows: [string, React.ReactNode][] = ([
    ["媒體", D.website], ["版面", D.board], ["作者", D.author], ["平台", D.platform !== D.website ? D.platform : ""],
    ["類型", D.source_type && TYPE_ZH[D.source_type] ? `${TYPE_ZH[D.source_type]}（${D.source_type}）` : D.source_type],
    ["地區", (D.region && REGION_ZH[D.region]) || D.region],
    ["來源", D.source && D.source !== D.website ? D.source : ""], ["來源編號", D.sid ? <code>{D.sid}</code> : ""],
    ["發佈時間", D.published ? dt.dateTimeYear(D.published) + "（香港時間）" : "來源沒有提供"], ["首次收錄", dt.dateTimeYear(D.seen) + "（香港時間）"],
    ["全文保存", D.text_chars ? (D.cached_at ? dt.dateTimeYear(D.cached_at) : "隨來源一起收到") : textPending(D) ? "正在收集"
      : D.cached_at ? `未能取得（${dt.dateTimeYear(D.cached_at)} 讀取）` : "未能取得"],
    ["內文來源", D.text_via_label ? <Via D={D} /> : ""],
  ] as [string, React.ReactNode][]).filter(r => r[1]);
  return (
    <section className="card"><h2>來源及時間</h2>
      <dl className="kv">{rows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
    </section>
  );
}

function EngagementCard({ D }: { D: Item }) {
  const e = D.engagement || {}, have = ENG_ZH.filter(([k]) => e[k] != null);
  return (
    <section className="card"><h2>互動</h2>
      {have.length ? <>
        <div className="stats">{have.map(([k, zh]) => <div key={k} className="stat"><div className="v num">{fmtN(e[k])}</div><div className="k">{zh}</div></div>)}</div>
        <p className="sub" style={{ margin: "8px 0 0" }}>數字是來源最近一次提供的。</p>
      </> : <p className="empty">來源沒有提供互動數字。</p>}
    </section>
  );
}

function MoreCard({ D, pid, PROFILES }: { D: Item; pid: string | null; PROFILES: Record<string, Profile> }) {
  const from7 = dayAdd(D.day, -6), from30 = dayAdd(D.day, -29);
  const links: React.ReactNode[] = [];
  if (D.website) links.push(pid
    ? <SiteLink key="site" className="btn" to="/dashboard" search={{ v: "posts", p: pid, site: D.website, from: from7, to: D.day }}>同一媒體：{D.website}</SiteLink>
    : <SiteLink key="site" className="btn" to="/search" search={{ site: D.website, from: from7, to: D.day }}>同一媒體：{D.website}</SiteLink>);
  if (D.author && pid) links.push(<SiteLink key="author" className="btn" to="/dashboard" search={{ v: "persona", p: pid, a: D.author, from: from30, to: D.day }}>同一作者：{D.author}</SiteLink>);
  if (!links.length) return null;
  return (
    <section className="card"><h2>更多</h2><div className="more">{links}</div>
      <p className="sub" style={{ margin: "8px 0 0" }}>{pid ? `按設定檔「${PROFILES[pid]?.title || pid}」列出。` : ""}</p>
    </section>
  );
}
