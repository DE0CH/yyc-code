// The proof view (/app/proof; Deyao, 2026-10-20): the stored posts of the chosen sources, one entry at a
// time as the pipeline holds them, so Deyao can check each looks right. ← → (buttons, the keyboard, a swipe on a
// phone) step through the list, newest first; the neighbours are read ahead so a step is instant. Three views,
// remembered per viewer (localStorage yyc-proof-view): the entries that reached a stream (the default), the entries
// stage 1 scored at or over the screen for a stream (Deyao, 2026-10-22: "another tab that is everything that pass
// stage 1"; the scores show each pair's outcome: filed, rejected by stage 2, waiting for stage 2), and every entry collected. Approved accounts only (the reads answer 403 to anyone else). Deyao's own tool, so English (Deyao,
// 2026-10-21); outlet names, titles and the posts' text stay as the sources write them.
// On a laptop screen (from 1200 x 600) one entry fits the window with no page scroll (Deyao, 2026-10-21: "to help me
// proof without scrolling, design a layout that fits on a 16:10 screen"; primary target his MacBook Air's browser
// window, 1440 x 830): the post on the left, the scores and his notes on the right. What gives way when there is too
// much, in this order: (1) the text scrolls inside its own panel (a fade at its foot; M shows more: the panel takes the
// whole left column, Space scrolls it); (2) the title is clamped to two lines and the URL / canonical / feed lines to
// one; (3) the collection details and the links list scroll inside their own box under the notes; (4) streams under
// 20 sit on one compact line. The scores (stage 1 and stage 2, threshold, passed/below, filed) and the verdict are
// never cut. Phones stack and scroll. Every action has a key (proofKeys.ts KEYS, ? shows them), and the notes, flags
// and "correct" marks are kept in this browser only (Deyao, 2026-10-21: "save notes locally and have one button and
// shortcut to download it as a file"), downloaded as one JSON file (D).
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";
import { Icon } from "../lib/icons";
import { Wait } from "../lib/Wait";
import { dtEn } from "../lib/dates";
import { VIEWS, inView, listQS, pickIndex, proofEntryQuery, proofListQuery, type ProofEntry, type ProofView } from "../proofData";
import type { ProofList } from "../server/proof";
import { langOf } from "../lib/lang";
import { KEYS, exportNotes, isTyping, keyAction, loadNotes, pending, saveNotes, upsert, type KeyAction, type Notes,
  type ProofRecord, type ProofScore } from "../lib/proofKeys";

const VIEW_KEY = "yyc-proof-view";
const NOT_APPROVED = "Only for accounts Deyao has approved.";
/** the views (tabs), in their order (V steps through them) */
const VIEW_NAME: Record<ProofView, string> = { stream: "In a stream", stage1: "Passed stage 1", all: "Everything collected" };
const srcCount = (s: ProofList["sources"][number], v: ProofView) => v === "all" ? s.total : v === "stage1" ? s.stage1 : s.stream;
const posts = (n: number) => `${n} post${n === 1 ? "" : "s"}`;
/** how the text was read (paywall.py VIA, media.py's transcripts and captions) */
const VIA_EN: Record<string, string> = { feed: "the source's feed", page: "the publisher's page", browser: "the publisher's page (browser)",
  solver: "the publisher (captcha solver)", page_data: "the publisher's page data", excerpt: "the paywall's public excerpt",
  archive: "archive.today snapshot" };
function viaEn(via: string | null) {
  if (!via) return null;
  if (via.startsWith("asr:")) return `AI transcript (${via.slice(4)})`;
  if (via.startsWith("youtube-captions:")) return `YouTube uploader's subtitles (${via.slice(17)})`;
  if (via.startsWith("youtube-auto-captions:")) return `YouTube auto subtitles (${via.slice(22)})`;
  return VIA_EN[via] ?? null;
}

function readView(): ProofView {
  try { const v = localStorage.getItem(VIEW_KEY) as ProofView; return VIEWS.includes(v) ? v : "stream"; } catch { return "stream"; }
}
function saveView(v: ProofView) {
  try { localStorage.setItem(VIEW_KEY, v); } catch { /* private mode: the choice lasts for this page */ }
}
const store = () => { try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; } };

// ---- full screen (the Fullscreen API, Safari's webkit-prefixed one as the fallback) ----
type FsDoc = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> | void };
type FsEl = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void };
const fsElement = () => (document as FsDoc).fullscreenElement ?? (document as FsDoc).webkitFullscreenElement ?? null;
async function toggleFullscreen() {
  const d = document as FsDoc, el = document.documentElement as FsEl;
  if (fsElement()) await (d.exitFullscreen ? d.exitFullscreen() : d.webkitExitFullscreen?.());
  else await (el.requestFullscreen ? el.requestFullscreen() : el.webkitRequestFullscreen?.());
}
function useFullscreen() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const f = () => { const v = !!fsElement(); setOn(v); document.documentElement.classList.toggle("proof-fs", v); };
    document.addEventListener("fullscreenchange", f); document.addEventListener("webkitfullscreenchange", f);
    return () => { document.removeEventListener("fullscreenchange", f); document.removeEventListener("webkitfullscreenchange", f);
      document.documentElement.classList.remove("proof-fs"); };
  }, []);
  return on;
}

export function ProofPage() {
  const search = useSearch({ from: "/app/proof" });
  const qs = listQS(search);
  const listQ = useQuery(proofListQuery(qs));
  const L = listQ.data;
  // the server renders the default view; the viewer's own choice is read once the page is live
  const [view, setView] = useState<ProofView>("stream");
  useEffect(() => { setView(readView()); }, []);

  const body = (() => {
    if (!qs) return <Intro />;
    if (listQ.isPending) return <Wait className="loading" what="load the sources' posts" label="Loading the sources' posts…" en onRetry={() => listQ.refetch()} />;
    if (listQ.isError) return <Wait className="loading" what="load the sources' posts" en failed onRetry={() => listQ.refetch()} />;
    if (L === null) return <section className="card"><p>{NOT_APPROVED}</p></section>;
    if (!L) return null;
    if (search.stream && !L.stream && !L.sids.length && !L.catalogue)
      return <section className="card"><h1>Proof</h1><p>No scored stream has the id <code>{search.stream}</code>. Stream ids are those of <code>streams.json</code>, e.g. <code>/app/proof?stream=WFC&amp;above=10</code>.</p></section>;
    return <Walk L={L} view={view} setView={v => { setView(v); saveView(v); }} at={Number(search.at) || null} />;
  })();
  return <Shell section="proof" title="Proof" pick="none"><main lang="en" className={qs ? "fit" : undefined}>{body}</main></Shell>;
}

function Intro() {
  return (
    <section className="card">
      <h1>Proof</h1>
      <p className="sub">Step through the posts as the system stores them and check each is right. Put the source ids in the address: <code>/app/proof?sid=SOURCE_ID</code> (several separated by commas), a stream's posts with <code>/app/proof?stream=WFC&amp;above=10</code>, or check every catalogue source.</p>
      <div className="acts"><Link className="btn primary" to="/app/proof" search={{ catalogue: "1" }}>Check every catalogue source</Link></div>
    </section>
  );
}

/** what the entry on screen does for the keyboard (Entry fills it in) */
interface EntryApi { id: number; flag(): void; ok(): void; stream(n: number): void; note(): void; noteOpen(): boolean; noteSave(): void;
  noteCancel(): void; more(): void; links(): void; pageText(dir: 1 | -1): boolean; open(): void }

function Walk({ L, view, setView, at }: { L: ProofList; view: ProofView; setView: (v: ProofView) => void; at: number | null }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const list = inView(L, view);
  const i = pickIndex(L, view, at);
  const cur = i >= 0 ? list[i] : null;
  const fs = useFullscreen();

  // the notes kept in this browser
  const [notes, setNotes] = useState<Notes>({});
  const [kept, setKept] = useState(true);  // false: the browser refused to store them (they last for this tab)
  useEffect(() => { setNotes(loadNotes(store())); }, []);
  const putNotes = useCallback((n: Notes) => { setNotes(n); setKept(saveNotes(store(), n)); }, []);
  const [toast, setToast] = useState<{ t: string; n: number } | null>(null);
  const say = useCallback((t: string) => setToast(o => ({ t, n: (o?.n || 0) + 1 })), []);
  useEffect(() => { if (!toast) return; const h = setTimeout(() => setToast(null), 2200); return () => clearTimeout(h); }, [toast]);
  const [help, setHelp] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const nPending = pending(notes), nAll = Object.keys(notes).length;

  const download = () => {
    if (!nAll) { say("No notes to download yet"); return; }
    const now = new Date().toISOString();
    const { name, body, notes: marked } = exportNotes(notes, now);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([body], { type: "application/json" }));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
    putNotes(marked);
    say(`Downloaded ${nAll} note${nAll === 1 ? "" : "s"}: ${name}`);
  };
  const clearAll = () => { putNotes({}); setConfirmClear(false); say("Notes cleared from this browser"); };

  const go = (j: number) => {
    if (j < 0 || j >= list.length) return;
    navigate({ to: ".", search: (prev: Record<string, unknown>) => ({ ...prev, at: String(list[j].id) }), replace: true, resetScroll: false });
    window.scrollTo({ top: 0 });
  };
  // the entry shown is always in the URL (a reload or a shared link opens it)
  useEffect(() => {
    if (cur && cur.id !== at) navigate({ to: ".", search: (prev: Record<string, unknown>) => ({ ...prev, at: String(cur.id) }), replace: true, resetScroll: false });
  }, [cur?.id, at]);  // eslint-disable-line react-hooks/exhaustive-deps
  // read the neighbours ahead, so a step is instant
  useEffect(() => {
    for (const d of [1, -1, 2]) { const e = list[i + d]; if (e) qc.prefetchQuery(proofEntryQuery(e.id)); }
  }, [i, view, L]);  // eslint-disable-line react-hooks/exhaustive-deps

  const src = cur ? L.sources.find(s => s.sid === cur.sid) : null;
  const srcIdx = src ? L.sources.indexOf(src) : -1;
  const firstOf = (sid: string) => list.find(e => e.sid === sid);
  const toSource = (sid: string) => { const e = firstOf(sid); if (e) go(list.indexOf(e)); };
  const otherSrc = (d: number) => { for (let j = srcIdx + d; j >= 0 && j < L.sources.length; j += d) if (firstOf(L.sources[j].sid)) return L.sources[j]; return null; };
  const prevSrc = otherSrc(-1), nextSrc = otherSrc(1);

  // the keyboard (proofKeys.keyAction); the entry on screen answers its own keys through `api`
  const api = useRef<EntryApi | null>(null);
  const act = useRef<(x: KeyAction, e: KeyboardEvent) => void>(() => {});
  act.current = (x, e) => {
    const A = api.current;
    const done = () => e.preventDefault();
    switch (x.a) {
      case "next": done(); if (i < list.length - 1) go(i + 1); else say("Last entry"); return;
      case "prev": done(); if (i > 0) go(i - 1); else say("First entry"); return;
      case "nextSource": done(); if (nextSrc) { toSource(nextSrc.sid); say(`Source: ${nextSrc.name || nextSrc.sid}`); } else say("Last source"); return;
      case "prevSource": done(); if (prevSrc) { toSource(prevSrc.sid); say(`Source: ${prevSrc.name || prevSrc.sid}`); } else say("First source"); return;
      case "view": done(); { const v = VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length]; setView(v); say(VIEW_NAME[v]); } return;
      case "fullscreen": done(); toggleFullscreen().catch(() => say("Full screen is not available here")); return;
      case "download": done(); download(); return;
      case "clear": done(); if (nAll) setConfirmClear(true); else say("No notes to clear"); return;
      case "help": done(); setHelp(h => !h); return;
      case "escape": if (help) { done(); setHelp(false); } else if (confirmClear) { done(); setConfirmClear(false); } return;
      case "noteSave": if (A?.noteOpen()) { done(); A.noteSave(); } return;
      case "noteCancel": if (A?.noteOpen()) { done(); A.noteCancel(); } return;
      case "pageText": if (A?.pageText(x.dir)) done(); return;
    }
    if (!A) return;
    done();
    if (x.a === "flag") A.flag(); else if (x.a === "ok") A.ok(); else if (x.a === "stream") A.stream(x.n);
    else if (x.a === "note") A.note(); else if (x.a === "more") A.more(); else if (x.a === "open") A.open();
    else if (x.a === "links") A.links();
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.isComposing) return;  // an input method (Chinese) choosing characters
      const typing = isTyping(e.target);
      if (confirmRef.current && !typing && !e.metaKey && !e.ctrlKey) {
        if (e.key === "y" || e.key === "Y" || e.key === "Enter") { e.preventDefault(); clearRef.current(); return; }
        if (e.key === "Escape" || e.key === "n") { e.preventDefault(); setConfirmClear(false); return; }
      }
      const t = e.target as HTMLElement | null;
      if (!typing && t && t.closest("a, button, summary, select") && (e.key === "Enter" || e.key === " ")) return;  // the focused control's own key
      const x = keyAction(e, typing);
      if (x) act.current(x, e);
    };
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, []);
  const confirmRef = useRef(false); confirmRef.current = confirmClear;
  const clearRef = useRef(clearAll); clearRef.current = clearAll;

  // a sideways swipe on a phone
  const touch = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart = (e: React.TouchEvent) => { const t = e.touches[0]; touch.current = { x: t.clientX, y: t.clientY }; };
  const onTouchEnd = (e: React.TouchEvent) => {
    const s = touch.current; touch.current = null;
    if (!s) return;
    const t = e.changedTouches[0], dx = t.clientX - s.x, dy = t.clientY - s.y;
    if (Math.abs(dx) < 60 || Math.abs(dy) > Math.abs(dx) * 0.6) return;
    if ((e.target as HTMLElement).closest("textarea, pre, .scroll-x")) return;
    go(dx < 0 ? i + 1 : i - 1);
  };

  return (
    <>
      <section className="card head">
        <div className="top">
          <h1>Proof</h1>
          <div className="views" role="group" aria-label="Show (V)">
            <button type="button" data-view="stream" aria-pressed={view === "stream"} onClick={() => setView("stream")}
              title={L.stream ? `Filed in ${L.stream.name}, or its score is at or over ${L.stream.name}'s threshold` : undefined}>{L.stream ? `In ${L.stream.name}` : "In a stream"} <b className="num">{L.counts.stream}</b></button>
            <button type="button" data-view="stage1" aria-pressed={view === "stage1"} onClick={() => setView("stage1")}
              title={`Stage 1 scored it ${L.screen} or more for ${L.stream ? L.stream.name : "a stream"}, whatever stage 2 then did (or it is waiting for stage 2)`}>Passed stage 1 <b className="num">{L.counts.stage1}</b></button>
            <button type="button" data-view="all" aria-pressed={view === "all"} onClick={() => setView("all")}>Everything collected <b className="num">{L.counts.all}</b></button>
          </div>
          <nav className="steps" aria-label="Step through">
            <button type="button" className="btn step" id="prev" aria-label="Previous post (← or K)" title="Previous (← or K)" disabled={i <= 0} onClick={() => go(i - 1)}><Icon name="prev" /></button>
            <span className="count num" aria-live="polite">{list.length ? `${i + 1} / ${list.length}` : "0 / 0"}</span>
            <button type="button" className="btn step" id="next" aria-label="Next post (→ or J)" title="Next (→ or J)" disabled={i < 0 || i >= list.length - 1} onClick={() => go(i + 1)}><Icon name="next" /></button>
          </nav>
          <div className="tools">
            <span className={"pend num" + (nPending ? " on" : "")} title="Notes in this browser not yet downloaded">{nPending ? `${nPending} not downloaded` : nAll ? "all downloaded" : "no notes"}{!kept && " (this tab only)"}</span>
            <span className="srcbtns">
            <button type="button" className="btn small" id="dl" onClick={download} title="Download the notes (D)">Download <kbd>D</kbd></button>
            <button type="button" className="btn small" id="clr" disabled={!nAll} onClick={() => setConfirmClear(true)} title="Clear the notes (C)">Clear <kbd>C</kbd></button>
            <button type="button" className="btn small" id="fs" aria-pressed={fs} onClick={() => toggleFullscreen().catch(() => say("Full screen is not available here"))} title="Full screen (F)">{fs ? "Exit full screen" : "Full screen"} <kbd>F</kbd></button>
            <button type="button" className="btn small" id="keys" onClick={() => setHelp(true)} title="Keyboard shortcuts (?)"><kbd>?</kbd></button>
            </span>
          </div>
        </div>
        <p className="sub">{L.stream ? `Everything ${L.stream.name} scored above ${L.stream.above} (stage 1 or 2), from ${L.sources.length} source${L.sources.length === 1 ? "" : "s"}` : L.catalogue ? `Every catalogue source (${L.sources.length})` : `Sources: ${L.sources.map(s => s.name ? `${s.name} (${s.sid})` : s.sid).join(", ")}`}
          {", "}newest first{L.catalogue ? ", source by source" : ""}{L.truncated ? ` (only the newest ${L.entries.length})` : ""}
          <span className="hint">. Keys: <kbd>←</kbd><kbd>→</kbd> step, <kbd>X</kbd> wrong, <kbd>O</kbd> correct, <kbd>N</kbd> note, <kbd>M</kbd> more text, <kbd>F</kbd> full screen, <kbd>?</kbd> all keys</span></p>
        {L.sources.length > 1 && src && (
          <div className="srcrow">
            <span>Source <b className="num">{srcIdx + 1} / {L.sources.length}</b>: {src.name || src.sid} ({posts(srcCount(src, view))})</span>
            <span className="srcbtns">
              <button type="button" className="btn small" disabled={!prevSrc} onClick={() => prevSrc && toSource(prevSrc.sid)}>‹ Previous source <kbd>[</kbd></button>
              <button type="button" className="btn small" disabled={!nextSrc} onClick={() => nextSrc && toSource(nextSrc.sid)}>Next source › <kbd>]</kbd></button>
            </span>
            <details className="srclist"><summary>All sources</summary>
              <ol>{L.sources.map(s => {
                const n = srcCount(s, view);
                return <li key={s.sid}>{n ? <button type="button" className="linkish" onClick={() => toSource(s.sid)}>{s.name || s.sid}</button> : <span className="muted">{s.name || s.sid}</span>}
                  <span className="muted num"> {s.sid}, {posts(n)}{view !== "all" ? ` (of ${s.total})` : ""}</span></li>;
              })}</ol>
            </details>
          </div>
        )}
        {confirmClear && (
          <div className="confirm" role="alertdialog" aria-label="Clear the notes">
            <span>Clear all {nAll} note{nAll === 1 ? "" : "s"} from this browser?{nPending ? ` ${nPending} not downloaded yet.` : ""}</span>
            <button type="button" className="btn small danger" id="clr-yes" onClick={clearAll}>Clear <kbd>Y</kbd></button>
            <button type="button" className="btn small" onClick={() => setConfirmClear(false)}>Keep <kbd>Esc</kbd></button>
          </div>
        )}
      </section>
      {!list.length ? (
        <section className="card empty">
          <p>{emptyText(L, view)}</p>
          {view !== "all" && L.counts.all > 0 && <div className="acts"><button type="button" className="btn primary" onClick={() => setView("all")}>See everything collected ({posts(L.counts.all)})</button></div>}
        </section>
      ) : cur && (
        <div className="entry-wrap" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
          <Entry key={cur.id} id={cur.id} api={api} rec={notes[cur.id] || null} setNotes={fn => putNotes(fn(notes))} say={say} />
        </div>
      )}
      {help && <Help onClose={() => setHelp(false)} />}
      {toast && <div className="toast" role="status" key={toast.n}>{toast.t}</div>}
    </>
  );
}
/** what an empty view says: of the sources' posts, or of the stream view's */
function emptyText(L: ProofList, view: ProofView) {
  const S = L.stream;
  const none = S ? `Nothing has a ${S.name} score above ${S.above}.` : "Nothing has been collected from these sources.";
  if (!L.counts.all || view === "all") return none;
  const have = S ? `${posts(L.counts.all)} with a ${S.name} score above ${S.above}` : `${posts(L.counts.all)} collected from these sources`;
  if (view === "stream") return S
    ? `${have}; none reached ${S.name} (none met its threshold, and none was filed in it).`
    : `${have}; none reached a stream (no stream score met its threshold, and none was filed in a stream).`;
  return `${have}; stage 1 scored none of them ${L.screen} or more for ${S ? S.name : "any stream"}.`;
}

function Help({ onClose }: { onClose: () => void }) {
  return (
    <div className="keys-overlay" role="dialog" aria-label="Keyboard shortcuts" onClick={onClose}>
      <div className="keys-box" onClick={e => e.stopPropagation()}>
        <h2>Keyboard shortcuts</h2>
        <dl>{KEYS.map(([k, v]) => <div key={k}><dt><kbd>{k}</kbd></dt><dd>{v}</dd></div>)}</dl>
        <p className="sub">Notes, flags and "correct" marks stay in this browser only (nothing is sent); <kbd>D</kbd> downloads them as one JSON file.</p>
        <button type="button" className="btn small" onClick={onClose}>Close <kbd>Esc</kbd></button>
      </div>
    </div>
  );
}

/** stage 1 and stage 2 of one stream's score as the entry holds it (item.aiScores exact: screen = stage 1 when stage 2
 *  scored the pair; a score by the stage-1 model is stage 1's) */
function stages(E: ProofEntry, s: ProofEntry["scores"][number]) {
  if (s.score == null) return { s1: null as number | null, s2: null as number | null, r1: null as string | null };
  if (s.screen) return { s1: s.screen.score as number, s2: s.score as number, r1: s.screen.reason as string | null };
  if (s.model && s.model !== E.model) return { s1: null, s2: s.score as number, r1: null };
  return { s1: s.score as number, s2: null, r1: null };
}

function Entry({ id, api, rec, setNotes, say }: { id: number; api: React.MutableRefObject<EntryApi | null>; rec: ProofRecord | null;
  setNotes: (fn: (n: Notes) => Notes) => void; say: (t: string) => void }) {
  const q = useQuery(proofEntryQuery(id));
  const [more, setMore] = useState(false);
  const [allLinks, setAllLinks] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const E = q.data;

  const order = E ? E.scores : [];  // highest first (item.aiScores): the number keys follow this order
  const change = (fn: (r: ProofRecord) => void) => {
    if (!E) return;
    const scores: ProofScore[] = order.map(s => { const st = stages(E, s); return { stream: s.stream, name: s.name, score: s.score ?? null,
      stage1: st.s1, threshold: s.threshold ?? null, passed: !!s.passed }; });
    const now = new Date().toISOString();
    setNotes(n => upsert(n, { id: E.id, url: E.url, title: E.title, source: E.source || E.website || null, sid: E.sid,
      published: E.published, seen: E.seen, scores }, fn, now));
  };
  const saveNote = () => { change(r => { r.note = draft.trim(); }); setNoteOpen(false); say(draft.trim() ? "Note saved (this browser)" : "Note removed"); };
  useEffect(() => { if (noteOpen) noteRef.current?.focus(); }, [noteOpen]);

  // what this entry does, for its own buttons and (through `api`, set after each commit) for the keyboard. Never
  // assigned during render: React may render and throw away, and development's mount-unmount-mount left `api` null
  // after the unmount half, so a click on Wrong did nothing until the entry happened to render again
  const actions: EntryApi | null = !E ? null : {
    id,
    flag: () => { const on = rec?.verdict !== "wrong"; change(r => { r.verdict = on ? "wrong" : null; }); say(on ? "Flagged as wrong" : "Flag removed"); },
    ok: () => { const on = rec?.verdict !== "ok"; change(r => { r.verdict = on ? "ok" : null; }); say(on ? "Marked correct" : "Correct mark removed"); },
    stream: n => {
      const s = order[n - 1];
      if (!s) { say(`No stream ${n}`); return; }
      const on = !(rec?.wrong_streams || []).includes(s.stream);
      change(r => { r.wrong_streams = on ? [...r.wrong_streams, s.stream] : r.wrong_streams.filter(x => x !== s.stream); if (on && r.verdict === "ok") r.verdict = null; });
      say(on ? `${s.name}: score flagged as wrong` : `${s.name}: flag removed`);
    },
    note: () => { setDraft(rec?.note || ""); setNoteOpen(true); },
    noteOpen: () => noteOpen,
    noteSave: saveNote,
    noteCancel: () => { setNoteOpen(false); say("Note not changed"); },
    more: () => { setMore(m => !m); say(more ? "Text: less" : "Text: more"); },
    links: () => { if (!E.links.length) { say("No links in the text"); return; } setAllLinks(v => !v); say(allLinks ? "Links: fewer" : "Links: all"); },
    pageText: dir => {
      const el = textRef.current;
      if (!el || el.scrollHeight <= el.clientHeight + 2 || getComputedStyle(el).overflowY === "visible") return false;
      el.scrollBy({ top: dir * el.clientHeight * 0.85, behavior: "smooth" });
      return true;
    },
    open: () => { window.open(E.link, "_blank", "noopener,noreferrer"); },
  };
  useLayoutEffect(() => {
    api.current = actions;
    return () => { if (api.current === actions) api.current = null; };
  });

  if (q.isPending) return <Wait className="loading" what="load the post" label="Loading the post…" en onRetry={() => q.refetch()} />;
  if (q.isError) return <Wait className="loading" what="load the post" en failed onRetry={() => q.refetch()} />;
  if (!E) return <section className="card"><p>{NOT_APPROVED}</p></section>;
  return (
    <article className={"entry" + (more ? " more" : "")} data-id={E.id}>
      <div className="postcol">
        <PostCard E={E} allLinks={allLinks} setAllLinks={setAllLinks} />
        <TextCard E={E} more={more} setMore={m => setMore(m)} textRef={textRef} />
      </div>
      <div className="sidecol">
        <ScoresCard E={E} rec={rec} />
        <AnswersCard E={E} />
        <NoteCard E={E} rec={rec} open={noteOpen} draft={draft} setDraft={setDraft} noteRef={noteRef}
          onOpen={() => { setDraft(rec?.note || ""); setNoteOpen(true); }} onSave={saveNote} onCancel={() => setNoteOpen(false)}
          onFlag={() => actions?.flag()} onOk={() => actions?.ok()} />
        <StateCard E={E} />
      </div>
    </article>
  );
}

const Row = ({ k, children }: { k: string; children: ReactNode }) => <><dt>{k}</dt><dd>{children}</dd></>;
const none = <span className="muted">(none)</span>;
const ext = (url: string, label?: string) => <a href={url} target="_blank" rel="noopener noreferrer" title={url}>{label ?? url}</a>;

function PostCard({ E, allLinks, setAllLinks }: { E: ProofEntry; allLinks: boolean; setAllLinks: (v: boolean) => void }) {
  const pub = E.published ? (E.date_only ? `${dtEn.date(E.published)} (day only)` : dtEn.dateTimeYear(E.published)) : "not given by the source";
  return (
    <section className="card post">
      <div className="kicker"><b>{E.source || E.website || E.sid}</b><span className="pill">{E.sid}</span>
        {E.source_type && <span>{E.source_type}</span>}{E.region && <span>{E.region}</span>}
        {E.first_run && <span className="pill">backfill</span>}</div>
      <h2 className="title" lang={langOf(E.title)} title={E.title || undefined}>{E.title || <span className="muted">(no title)</span>}</h2>
      {E.stored_title !== E.title && <p className="sub one" title={E.stored_title || ""}>Stored title: {E.stored_title || "(empty)"}{E.title_by_ai ? " (the title above is written by AI)" : ""}</p>}
      <dl className="kv meta">
        <Row k="Author">{E.author || none}</Row>
        <Row k="Published">{pub}<span className="muted">, first seen {E.seen ? dtEn.dateTimeYear(E.seen) : "—"}</span></Row>
        <Row k="URL"><span className="url one">{ext(E.url)}</span></Row>
        {E.canonical_url && <Row k="Canonical"><span className="url one">{ext(E.canonical_url)}</span></Row>}
        <Row k="Website">{E.website || none}{E.board ? `, ${E.board}` : ""}{E.section ? `, ${E.section}` : ""}</Row>
        <Row k="Feed">{E.feed ? <span className="one"><span>{E.feed.name}</span> <span className="muted">({E.feed.kind})</span> <span className="url">{ext(E.feed.url)}</span></span> : <span className="muted">not a catalogue source</span>}</Row>
      </dl>
      {E.links.length > 0 && (
        <div className={"links" + (allLinks ? " all" : "")}>
          <span className="lk">Links in the text ({E.links.length}){E.links.length > 2 && <>, <button type="button" className="linkish" onClick={() => setAllLinks(!allLinks)}>{allLinks ? "fewer" : "all"} <kbd>L</kbd></button></>}</span>
          <ul>{E.links.map(u => <li key={u} className="url one">{ext(u)}</li>)}</ul>
        </div>
      )}
      <div className="acts">
        <span className="srcbtns">
          <a className="btn primary" href={E.link} target="_blank" rel="noopener noreferrer"><Icon name="out" />Open the original <kbd>↵</kbd></a>
          <Link className="btn" to="/item/$id" params={{ id: E.id }}>Item page</Link>
        </span>
      </div>
    </section>
  );
}

function TextCard({ E, more, setMore, textRef }: { E: ProofEntry; more: boolean; setMore: (m: boolean) => void; textRef: React.RefObject<HTMLDivElement | null> }) {
  const [over, setOver] = useState(false);  // the text is longer than its panel (fit layout): the fade and "Show more"
  useLayoutEffect(() => {
    const el = textRef.current; if (!el) return;
    const f = () => setOver(el.scrollHeight > el.clientHeight + 2 && getComputedStyle(el).overflowY !== "visible");
    f();
    const ro = new ResizeObserver(f); ro.observe(el);
    return () => ro.disconnect();
  }, [E.id, more, textRef]);
  const [atEnd, setAtEnd] = useState(false);
  return (
    <section className={"card textcard" + (over && !atEnd ? " over" : "")} id="text">
      <h2>Stored full text <span className="sub num">{Array.from(E.text).length} characters</span>
        {(over || more) && <button type="button" className="linkish more-btn" onClick={() => setMore(!more)}>{more ? "Show less" : "Show more"} <kbd>M</kbd></button>}
        {over && <span className="sub">scrolls (<kbd>Space</kbd>)</span>}</h2>
      <div className="textpanel" ref={textRef} onScroll={e => { const t = e.currentTarget; setAtEnd(t.scrollTop + t.clientHeight >= t.scrollHeight - 4); }}>
        {E.text ? <pre className="body" lang={langOf(E.text)}>{E.text}</pre> : <p className="sub">No text stored.</p>}
      </div>
    </section>
  );
}

function StateCard({ E }: { E: ProofEntry }) {
  return (
    <section className="card state">
      <h2>Collection</h2>
      <dl className="kv">
        <Row k="Backfill">{E.first_run ? "yes: collected on the source's first read (never filed in a stream or sent)" : "no"}</Row>
        <Row k="Text from">{viaEn(E.text_via) || E.text_via || <span className="muted">not recorded</span>}{E.text_via && viaEn(E.text_via) ? <span className="muted"> ({E.text_via})</span> : null}</Row>
        <Row k="Full text read">{E.cached_at ? dtEn.dateTimeYear(E.cached_at) : E.text_due ? "not yet (queued)" : "not read"}</Row>
        {E.text_via_url && <Row k="Archived copy"><span className="url one">{ext(E.text_via_url)}</span></Row>}
        <Row k="Comments, replies"><span className="num">{E.comments}</span></Row>
        <Row k="Links in the text"><span className="num">{E.links.length}</span></Row>
        {E.article_id && E.article_id !== E.id ? <Row k="Same article">as post <Link to="/item/$id" params={{ id: E.article_id }}>#{E.article_id}</Link></Row> : null}
      </dl>
      {E.flags.some(f => !f.resolved_at) && <ul className="flags">{E.flags.filter(f => !f.resolved_at).map(f => <li key={f.id}>Flag #{f.id}, {dtEn.dateTime(f.at)}{f.note ? `: ${f.note}` : ""}</li>)}</ul>}
    </section>
  );
}

function ScoresCard({ E, rec }: { E: ProofEntry; rec: ProofRecord | null }) {
  const why: Record<string, string> = { before: "collected before scoring began", comment: "comments are not scored", old: "over two days old when collected",
    thin: "too little content to score", thin_wait: "too little content, waiting for more", due: "not scored yet (queued)" };
  const wrong = new Set(rec?.wrong_streams || []);
  const since = E.all_scores_from ? dtEn.dateTime(E.all_scores_from) : "21 Oct";
  // a pair waiting for stage 2 (server/proof.ts entry `pending`): stage 1's score and reason from its event, stage 2 "pending"
  const pend = new Map(E.pending.map(p => [p.stream, p]));
  const st = E.scores.map((s, k) => {
    const p = pend.get(s.stream);
    return p ? { s, k: k + 1, s1: p.s1 as number | null, s2: null as number | null, r1: p.r1 as string | null, pending: true }
      : { s, k: k + 1, ...stages(E, s), pending: false };
  });
  // two-stage outcome of a pair stage 1 scored at or over the screen: filed, rejected by stage 2, passed stage 2, pending
  const outcome = (x: typeof st[number]) => x.s.kept ? null : x.pending ? "pending" : x.s1 != null && x.s1 >= E.screen && x.s2 != null
    ? (x.s2 >= (x.s.threshold ?? 0) ? "passed stage 2" : "rejected by stage 2") : null;
  // a full row: a score of 20 or more at either stage, a pass, a filing, waiting for stage 2, or a flag on it; the rest share one line
  const full = st.filter(x => x.pending || (x.s1 ?? -1) >= E.floor || (x.s2 ?? -1) >= E.floor || x.s.passed || x.s.kept || wrong.has(x.s.stream));
  const low = st.filter(x => !full.includes(x) && x.s.score != null);
  const missing = st.filter(x => !full.includes(x) && x.s.score == null);
  const stageModel = E.scores.find(s => s.screen)?.model || E.scores.find(s => s.model && s.model !== E.model)?.model || null;
  return (
    <section className="card scores">
      <h2>Stream scores {E.scoring ? <span className="sub">{dtEn.dateTime(E.scoring.at)}, stage 1 {E.model}{stageModel ? `, stage 2 ${stageModel}` : ""}</span> : null}</h2>
      {!E.scoring && !full.length && !low.length && <p className="sub">Not scored: {why[E.not_scored || ""] || E.not_scored || "—"}</p>}
      {full.length > 0 && (
        <div className="sgrid" role="table" aria-label="Stream scores">
          <div className="sh" role="row"><span>#</span><span>Stream</span><span>Stage 1</span><span>Stage 2</span><span>Threshold</span><span /></div>
          {full.map(x => { const { s, k, s1, s2, r1, pending } = x; const out = outcome(x); return (
            <div key={s.stream} role="row" className={"sc" + (s.passed && !pending ? " pass" : "") + (wrong.has(s.stream) ? " wrong" : "")} data-stream={s.stream}
              data-outcome={s.kept ? "filed" : out || undefined}>
              <span className="k"><kbd>{k}</kbd></span>
              <b className="nm">{s.name}</b>
              <span className="num s1">{s1 ?? "—"}</span>
              <span className={"num s2" + (s2 != null ? " final" : "")}>{pending ? <span className="pend-s2">pending</span> : s2 ?? "—"}</span>
              <span className="num th">{s.threshold}</span>
              <span className="pills">{out === "pending" ? <span className="pill wait">waiting for stage 2</span>
                : out === "rejected by stage 2" ? <span className="pill bad">rejected by stage 2</span>
                : <span className={"pill" + (s.passed ? " ok" : "")}>{s.passed ? (out ? "passed stage 2" : "passed") : "below"}</span>}
                {s.kept && <span className="pill ok">filed {dtEn.date(s.kept.day)}</span>}{wrong.has(s.stream) && <span className="pill bad">flagged</span>}</span>
              {pending ? <p className="reason" title={r1 || ""}><span className="muted">stage 1: </span>{r1 || <span className="muted">(no reason)</span>}</p>
              : <p className="reason" title={[s.reason, r1 && r1 !== s.reason ? `stage 1: ${r1}` : null, s.category].filter(Boolean).join("; ")}>
                {s2 != null && s1 != null ? <span className="muted">stage 2: </span> : null}{s.reason || <span className="muted">(no reason)</span>}{r1 && r1 !== s.reason ? <span className="muted">; stage 1: {r1}</span> : null}
                {s.category ? <span className="muted">; {s.category}</span> : null}</p>}
            </div>
          ); })}
        </div>
      )}
      {low.length > 0 && <p className="low" data-low>Under {E.floor}: {low.map(({ s, k, s1, s2 }, j) => (
        <span key={s.stream} data-stream={s.stream} title={s.reason || undefined}>{j ? ", " : ""}<kbd>{k}</kbd> {s.name} <b className="num">{s2 ?? s1}</b>{s2 != null && s1 != null ? <span className="muted num"> (stage 1 {s1})</span> : null}</span>))}</p>}
      {missing.length > 0 && E.scoring && <p className="low muted" data-missing>{missing.map(x => x.s.name).join(", ")}: {E.all_scores ? "no score in the model's answer" : `not stored (scored before ${since}, when scores under ${E.floor} were not kept)`}</p>}
      {E.streams.length > 0 && <p className="sub">Filed in: {E.streams.map(s => `${s.name} (${dtEn.date(s.day)})`).join(", ")}</p>}
    </section>
  );
}

function NoteCard({ E, rec, open, draft, setDraft, noteRef, onOpen, onSave, onCancel, onFlag, onOk }: {
  E: ProofEntry; rec: ProofRecord | null; open: boolean; draft: string; setDraft: (s: string) => void;
  noteRef: React.RefObject<HTMLTextAreaElement | null>; onOpen: () => void; onSave: () => void; onCancel: () => void; onFlag: () => void; onOk: () => void }) {
  const v = rec?.verdict || null;
  const names = new Map(E.scores.map(s => [s.stream, s.name]));
  return (
    <section className={"card flag" + (v ? " v-" + v : "")}>
      <h2>Your check <span className="sub">kept in this browser</span></h2>
      <div className="verdict">
        <span className="srcbtns">
        <button type="button" className={"btn small flag-open" + (v === "wrong" ? " on" : "")} aria-pressed={v === "wrong"} onClick={onFlag}>Wrong <kbd>X</kbd></button>
        <button type="button" className={"btn small ok-btn" + (v === "ok" ? " on" : "")} aria-pressed={v === "ok"} onClick={onOk}>Correct <kbd>O</kbd></button>
        {!open && <button type="button" className="btn small note-open" onClick={onOpen}>{rec?.note ? "Edit note" : "Note"} <kbd>N</kbd></button>}
        </span>
        <span className="flag-msg" role="status">{v === "wrong" ? "flagged as wrong" : v === "ok" ? "marked correct" : rec?.wrong_streams.length ? "" : "not checked yet"}
          {rec?.wrong_streams.length ? `; scores flagged: ${rec.wrong_streams.map(s => names.get(s) || s).join(", ")}` : ""}</span>
      </div>
      {open ? (
        <div className="flag-form">
          <textarea id="flag-note" ref={noteRef} rows={2} maxLength={2000} value={draft} onChange={e => setDraft(e.target.value)}
            placeholder="What is wrong? e.g. wrong publish time, only half the text" aria-label="Note" />
          <div className="acts">
            <span className="srcbtns">
              <button type="button" className="btn small primary flag-send" onClick={onSave}>Save <kbd>Enter</kbd></button>
              <button type="button" className="btn small" onClick={onCancel}>Cancel <kbd>Esc</kbd></button>
            </span>
            <span className="sub">Shift+Enter: new line</span>
          </div>
        </div>
      ) : rec?.note ? <p className="note-text">{rec.note}</p> : null}
    </section>
  );
}

const short = (m: string | null | undefined) => (m || "").replace(/^[a-z-]+\//, "");
const KNOWN = new Set(["sentiment", "labels", "streams", "title_ok", "title_kind", "title"]);
const val = (v: unknown) => typeof v === "string" ? v : JSON.stringify(v);

/** everything the models answered for the post (scored.answers: each stage's answer as given; Deyao, 2026-10-21: "i
 *  want to see the tags etc that the model returns"), and what the pipeline kept from it */
function AnswersCard({ E }: { E: ProofEntry }) {
  const A = E.answers || {};
  const stage = (key: "stage1" | "stage2", label: string) => {
    const a = A[key];
    if (!a) return null;
    const x = a.answer || {};
    const labels = Array.isArray(x.labels) ? (x.labels as unknown[]).map(String) : [];
    const other = Object.entries(x).filter(([k]) => !KNOWN.has(k));
    const cats = (Array.isArray(x.streams) ? x.streams as Record<string, unknown>[] : []).filter(s => s.category)
      .map(s => `${E.scores.find(z => z.stream === s.id)?.name || s.id}: ${s.category}`);
    return (
      <div className="ans" data-stage={key}>
        <b>{label}</b> <span className="muted">{short(a.model)}</span>
        <span className="f"><i>sentiment</i> {x.sentiment != null ? val(x.sentiment) : "—"}</span>
        <span className="f"><i>labels</i> {labels.length ? labels.join(", ") : "none"}</span>
        {cats.length > 0 && <span className="f"><i>categories</i> {cats.join(", ")}</span>}
        {"title_ok" in x && <span className="f"><i>title</i> {x.title_ok ? "ok" : `not ok${x.title_kind ? ` (${val(x.title_kind)})` : ""}${x.title ? `: ${val(x.title)}` : ""}`}</span>}
        {other.map(([k, v]) => <span className="f" key={k}><i>{k}</i> {val(v)}</span>)}
      </div>
    );
  };
  const k = E.kept_tags;
  const mk = E.scores.filter(s => s.market);
  return (
    <section className="card answers">
      <h2>Model answers</h2>
      {stage("stage1", "Stage 1")}
      {stage("stage2", "Stage 2")}
      {!E.answers && <p className="sub">{E.scoring ? `The answers themselves are not stored (scored before ${E.all_scores_from ? dtEn.dateTime(E.all_scores_from) : "21 Oct"}); kept from them:` : "Not scored."}</p>}
      {(k || E.ai_title || mk.length > 0) && (
        <div className="ans kept">
          <b>Kept</b>
          {k && <span className="f"><i>sentiment</i> {k.sentiment || "—"}</span>}
          {k && <span className="f"><i>labels</i> {k.labels.length ? k.labels.join(", ") : "none"}{k.tag_model ? <span className="muted"> ({short(k.tag_model)})</span> : null}</span>}
          {E.ai_title && <span className="f"><i>AI title</i> {E.ai_title.title} <span className="muted">({E.ai_title.kind || "—"}{E.ai_title.model ? `, ${short(E.ai_title.model)}` : ""})</span></span>}
          {mk.map(s => <span className="f" key={s.stream}><i>market</i> {s.name}: {s.market.market}{s.market.name ? ` → ${s.market.name}` : ""} <span className="muted">({s.market.how}{s.market.model ? `, ${short(s.market.model)}` : ""})</span></span>)}
        </div>
      )}
    </section>
  );
}
