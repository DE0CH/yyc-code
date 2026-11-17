// The live dashboard (/app/live; Deyao, 2026-10-21): the pipeline watched live, with full attention, on two displays
// side by side (a 1920 x 1200 and a 1920 x 1080). One logical canvas of two 1920-wide halves, each rendered by its own
// window: /app/live?half=left and ?half=right, each fullscreen on its display, both reading the same data. A panel
// never spans the two displays (Deyao: "a panel cannot span two displays"). Without ?half, one window shows every
// panel (a 1440 x 830 laptop window, testing), or on a phone (a window under PHONE_MAX wide) one scrolling column.
// Besides the news pipeline (Deyao, 2026-10-28): YYC AI (its operator snapshot's state, and its activity as cards:
// model calls per person, sandbox starts and releases, host events, egress errors, changes of status) and Systems (the
// providers' balances with their source, stops, the news stream's and YYC AI's pods, the sandbox hosts, the nodes,
// Postgres's load, space and backups, the WiseSearch relay, the object storage buckets), both from kv live:sys
// (news-stream/monitor.py live_once, every 10 s).
// Fixed, not fitted (Deyao: "text size and layout should be fixed"): every panel at a fixed px box (lib/liveLayout.ts
// LAYOUTS), every card a fixed height per panel with its text clamped inside, fixed px type. Each panel shows only its
// newest few events as rich cards (Deyao: "a list doesn't need to be that long"), and its counts in the last 10 minutes
// and hour in its header. New cards go on top ONE AT A TIME, (Deyao: "one new
// item at a time … otherwise it's hard to follow"), at the rate they arrive (lib/liveLayout.ts enqueue/step: each
// panel's arrival rate measured like a download's speed, a rate decaying over 5 min, seeded with the server's 10-minute
// count; one card every 1 / rate, faster only when the queue would wait over 2 min), each insert instant: no highlight
// or animation (Deyao: "no highlight, too busy"), "+N behind" when a panel can't keep up; nothing is dropped.
// Data: /app/live/data every POLL_MS with the cursors of what the page has (server/live.ts); when nothing new has come
// for STALE_MS (the read failing, or the poller silent for POLL_STALE_MS) the whole screen greys out and says since
// when. A screen wake lock while visible; F = full screen. Deyao's own tool, so English; titles, outlets and the posts'
// text as written. Read-only.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearch } from "@tanstack/react-router";
import { BUILD_ID } from "../lib/build";
import { deploymentLost } from "../lib/healthStates";
import { monoEmoji } from "../lib/monoEmoji";
import { subscribeUpdate } from "../lib/update";
import type { Card, LiveData, LiveSys, PanelId } from "../server/live";
import type { JarvisLive, JarvisSession } from "../server/jarvis";
import { CARD_H, HEADER, JARVIS_ROW_H, LAYOUTS, PANEL_NAMES, PHONE_MAX, POLL_MS, RATES_EVERY, aiSplit, behind, jarvisGrid, cardsIn, enqueue, gapAt, hhmm, hhmmss, newPace,
  phoneLayout, stale, step, type Box, type Mode, type Pace } from "../lib/liveLayout";

type FsDoc = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> | void };
type FsEl = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void };
/** the top page's document: full screen belongs to the shell (LiveShell), also when F is pressed inside the frame */
const topDoc = () => { try { return (window.top || window).document; } catch { return document; } };
const fsElement = () => (topDoc() as FsDoc).fullscreenElement ?? (topDoc() as FsDoc).webkitFullscreenElement ?? null;
async function toggleFullscreen() {
  const d = topDoc() as FsDoc, el = d.documentElement as FsEl;
  if (fsElement()) await (d.exitFullscreen ? d.exitFullscreen() : d.webkitExitFullscreen?.());
  else await (el.requestFullscreen ? el.requestFullscreen() : el.webkitRequestFullscreen?.());
}

interface Stream { id: string; name: string; threshold: number | null }
type Models = { stage1: string; stage2: string };  // the two stages' models by display name (edge_config.py)
interface PanelState { shown: Card[]; pace: Pace; inserted: number }
type Panels = Partial<Record<PanelId, PanelState>>;
const KEEP = 12;  // cards kept per panel (more than any panel shows)

/** the screen kept on while the page is visible (the Screen Wake Lock API; re-taken when the tab comes back) */
function useWakeLock() {
  useEffect(() => {
    type WL = { release(): Promise<void> };
    const nav = navigator as Navigator & { wakeLock?: { request(t: "screen"): Promise<WL> } };
    if (!nav.wakeLock || window.top !== window) return;  // in the frame: the shell holds it
    let lock: WL | null = null, gone = false;
    const take = () => { if (document.visibilityState === "visible" && !gone) nav.wakeLock!.request("screen").then(l => { lock = l; }).catch(() => {}); };
    take();
    document.addEventListener("visibilitychange", take);
    return () => { gone = true; document.removeEventListener("visibilitychange", take); lock?.release().catch(() => {}); };
  }, []);
}

/** the window's width when it is a phone's (under PHONE_MAX; no ?half), else 0. 0 on the server's render and the first
 * client render (no hydration mismatch), then the window's */
function usePhoneWidth(on: boolean) {
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!on) return;
    const f = () => setW(window.innerWidth < PHONE_MAX ? window.innerWidth : 0);
    f();
    addEventListener("resize", f);
    return () => removeEventListener("resize", f);
  }, [on]);
  return w;
}

/** the shell at /app/live: the dashboard (/app/live?frame=1) in a frame filling the window; it holds full screen (F) and
 * the wake lock. When the site runs a newer build than the frame (/version, asked every minute and whenever this page's
 * update module hears of one: lib/update.ts), a second frame loads the new build out of sight and replaces the first
 * once it has drawn its data, so nothing blinks and the browser never reloads the page: full screen stays (Deyao,
 * 2026-11-17). The shell itself is never replaced: what it does is only this. */
export function LiveShell() {
  const { half } = useSearch({ from: "/app/live" });
  return <FrameShell src={"/app/live?" + new URLSearchParams({ ...(half ? { half } : {}), frame: "1" })} title="YYC live" />;
}

/** LiveShell's frame swap for any page that draws itself in a frame (/app/live, /app/health): the frame posts
 * {live: "drawn"} to this window once it has its first data */
export function FrameShell({ src, title }: { src: string; title: string }) {
  const [frames, setFrames] = useState<{ id: number; shown: boolean }[]>([{ id: 0, shown: true }]);
  const els = useRef(new Map<number, HTMLIFrameElement>());
  const fr = useRef(frames);  // the same, for the listeners
  fr.current = frames;
  useWakeLock();
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
      if (e.key === "f" || e.key === "F") { e.preventDefault(); toggleFullscreen().catch(() => {}); }
    };
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, []);
  useEffect(() => { document.documentElement.classList.add("live-doc"); return () => document.documentElement.classList.remove("live-doc"); }, []);
  useEffect(() => {
    let gone = false, pending: number | null = null, timer = 0, next = 1;
    const tries = new Map<string, number>();
    const shownId = () => fr.current.find(x => x.shown)?.id ?? 0;
    const frameBuild = (id: number) => { try { return els.current.get(id)?.contentDocument?.documentElement.dataset.build || ""; } catch { return ""; } };
    const drop = (id: number) => setFrames(f => f.filter(x => x.id !== id));
    const check = async () => {
      if (gone || pending != null) return;
      let v = "";
      try { const r = await fetch("/version", { cache: "no-store" }); if (r.ok) v = (await r.text()).trim(); } catch { return; }
      const have = frameBuild(shownId());
      if (gone || pending != null || !v || !have || v === have) return;
      const n = (tries.get(v) || 0) + 1;  // a build the edge doesn't serve yet: at most 5 tries, a minute apart
      if (n > 5) return;
      tries.set(v, n);
      const id = next++;
      pending = id;
      setFrames(f => [...f, { id, shown: false }]);
      timer = window.setTimeout(() => { if (pending === id) { pending = null; drop(id); } }, 90_000);  // never drew: keep the old one
    };
    const msg = (e: MessageEvent) => {
      if (e.origin !== location.origin || (e.data as { live?: string } | null)?.live !== "drawn" || pending == null) return;
      const id = pending;
      if (els.current.get(id)?.contentWindow !== e.source) return;
      pending = null;
      clearTimeout(timer);
      setFrames([{ id, shown: true }]);
    };
    addEventListener("message", msg);
    const off = subscribeUpdate(() => { if (document.documentElement.dataset.update !== "current") void check(); });
    const poll = window.setInterval(check, 60_000);
    const vis = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", vis);
    return () => { gone = true; removeEventListener("message", msg); off(); clearInterval(poll); clearTimeout(timer); document.removeEventListener("visibilitychange", vis); };
  }, []);
  return (
    <div className="live-shell" data-frames={frames.length}>
      {frames.map(f => <iframe key={f.id} ref={el => { if (el) els.current.set(f.id, el); else els.current.delete(f.id); }} src={src}
        title={title} data-frame={f.id} className={f.shown ? "on" : "off"} allow="fullscreen" />)}
    </div>
  );
}

export function LivePage() {
  const { half } = useSearch({ from: "/app/live" });
  const phoneW = usePhoneWidth(!half);
  const mode: Mode = half || (phoneW ? "phone" : "single");
  const layout = mode === "phone" ? phoneLayout(phoneW) : LAYOUTS[mode];
  const [panels, setPanels] = useState<Panels | null>(null);
  const pr = useRef<Panels | null>(null);  // the same, for the timers
  const [streams, setStreams] = useState<Stream[]>([]);
  const [models, setModels] = useState<Models>({ stage1: "", stage2: "" });
  const [rates, setRates] = useState<LiveData["rates"]>();
  const [sys, setSys] = useState<LiveSys | null>(null);
  const [jarvis, setJarvis] = useState<JarvisLive | null>(null);
  const [now, setNow] = useState(0);  // 0 until the page is live: the server's render has no clock (no hydration mismatch)
  const st = useRef({ cursors: "", okAt: 0, lastPoll: null as number | null, skew: 0, failed: "", ratesAt: 0 });
  useWakeLock();
  const put = (p: Panels) => { pr.current = p; setPanels(p); };

  // the reads: one at a time, POLL_MS apart
  useEffect(() => {
    let gone = false, timer = 0;
    const read = async () => {
      const s = st.current;
      try {
        const q = new URLSearchParams();
        if (s.cursors) q.set("c", s.cursors); else q.set("cfg", "1");
        if (Date.now() - s.ratesAt >= RATES_EVERY * POLL_MS) q.set("rates", "1");
        const r = await fetch("/app/live/data?" + q, { cache: "no-store", credentials: "same-origin" });
        if (!r.ok) throw new Error(r.status === 404 ? "not signed in as Deyao (404)" : "HTTP " + r.status);
        const d = JSON.parse(monoEmoji(await r.text())) as LiveData;
        if (gone) return;
        s.okAt = Date.now(); s.failed = ""; s.skew = d.now - Date.now(); s.lastPoll = d.last_poll;
        const first = !s.cursors;
        s.cursors = Object.entries(d.cursors).map(([k, v]) => `${k}:${v}`).join(",");
        if (d.streams) setStreams(d.streams);
        if (d.models) setModels(d.models);
        setSys(d.sys ?? null);
        setJarvis(d.jarvis ?? null);
        if (d.rates) { setRates(d.rates); s.ratesAt = Date.now(); }
        const t = Date.now(), old = pr.current || {}, next: Panels = {};
        for (const [k, got] of Object.entries(d.panels) as [PanelId, Card[]][]) {
          if (first || !old[k]) { const shown = got.slice(0, KEEP); next[k] = { shown, pace: newPace(shown, d.rates?.[k]?.[0] ?? 0), inserted: 0 }; }  // the first read: as it is
          else next[k] = { ...old[k]!, pace: enqueue(old[k]!.pace, got, t) };
        }
        put(next);
        // the shell shows a new build's frame once it has drawn its first data (LiveShell)
        if (first && window.parent !== window) window.parent.postMessage({ live: "drawn", build: BUILD_ID }, location.origin);
      } catch (e) {
        s.failed = e instanceof Error ? e.message : String(e);
      } finally {
        if (!gone) timer = window.setTimeout(read, POLL_MS);
      }
    };
    read();
    return () => { gone = true; clearTimeout(timer); };
  }, []);
  // one card at a time: each panel's queue steps on its own pace
  useEffect(() => {
    const h = window.setInterval(() => {
      const cur = pr.current;
      if (!cur) return;
      const t = Date.now();
      let changed = false;
      const next: Panels = { ...cur };
      for (const [k, v] of Object.entries(cur) as [PanelId, PanelState][]) {
        const [c, pace] = step(v.pace, t);
        if (!c) continue;
        next[k] = { shown: [c, ...v.shown].slice(0, KEEP), pace, inserted: v.inserted + 1 };
        changed = true;
      }
      if (changed) put(next);
    }, 50);
    return () => clearInterval(h);
  }, []);
  // the clock
  useEffect(() => { setNow(Date.now()); const h = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(h); }, []);
  // F: full screen
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
      if (e.key === "f" || e.key === "F") { e.preventDefault(); toggleFullscreen().catch(() => {}); }
    };
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, []);
  useEffect(() => { document.documentElement.classList.add("live-doc"); return () => document.documentElement.classList.remove("live-doc"); }, []);
  useEffect(() => { document.documentElement.classList.toggle("live-phone", mode === "phone"); }, [mode]);

  const s = st.current;
  const old = panels ? stale(now, s.okAt, s.lastPoll == null ? null : s.lastPoll - s.skew) : null;
  return (
    <div className={"live live-" + mode + (old ? " live-stale" : "")} lang="en" data-mode={mode} style={{ width: layout.w, height: layout.h }}>
      <header className="live-bar">
        <b>YYC live{half ? `, ${half}` : ""}</b>
        <span className="num">{now ? `${hhmmss(now)} HKT` : ""}</span>
        <span className="num">{s.lastPoll ? `last poll ${hhmm(s.lastPoll - s.skew)}` : ""}</span>
        {s.failed && <span className="blind">read failed: {s.failed}</span>}
        <span className="dim">F full screen</span>
      </header>
      {layout.panels.map(b => b.id === "sys" ? <SysPanel key={b.id} box={b} sys={sys} now={now} />
        : b.id === "jarvis" ? <JarvisPanel key={b.id} box={b} j={jarvis} now={now} />
        : <Panel key={b.id} box={b} st={panels?.[b.id] || null} streams={streams} models={models} rate={rates?.[b.id]} sys={sys} now={now} />)}
      {old && <div className="live-stale-note" role="status">No data since {hhmm(old.since)}{old.why ? ` (${old.why})` : ""}</div>}
    </div>
  );
}

function Panel({ box, st, streams, models, rate, sys, now }: { box: Box; st: PanelState | null; streams: Stream[]; models: Models; rate?: [number, number]; sys: LiveSys | null; now: number }) {
  const id = box.id as PanelId, n = cardsIn(box), h = CARD_H[id];
  const ai = id === "ai" ? aiSplit(box) : null;
  const name = PANEL_NAMES[id] + (id === "stage1" || id === "stage2" ? (models[id] ? ", " + models[id] : "") : "");
  const late = st ? behind(st.pace) : 0;
  return (
    <section className="lp" data-panel={id} data-inserted={st?.inserted ?? 0} data-queued={st?.pace.queue.length ?? 0} data-gap={st ? Math.round(gapAt(st.pace, Date.now())) : 0} aria-label={name}
      style={{ left: box.x, top: box.y, width: box.w, height: box.h }}>
      <h2 style={{ height: HEADER }}><span>{name}</span>
        <span className="hr">{late ? <b className="warn">+{late} behind</b> : null}
          {rate ? <span className="num dim" title="in the last 10 minutes / hour">{rate[0]} /10 min, {rate[1]} /h</span> : null}</span></h2>
      {ai && <AiState sys={sys} now={now} w={ai.stateW} h={ai.stateH} side={ai.side} />}
      <ol style={ai?.side ? { position: "absolute", left: ai.stateW + 1, right: 0, top: HEADER + 1, bottom: 0, borderLeft: "1px solid var(--rule)" } : undefined}>
        {st === null ? <li className="dim" style={{ height: h }}>Loading…</li> : !st.shown.length ? <li className="dim" style={{ height: h }}>Nothing yet</li>
          : st.shown.slice(0, n).map(c => <li key={c.id} className={cls(id, c)} style={{ height: h }}><CardBody id={id} c={c} streams={streams} models={models} /></li>)}
      </ol>
    </section>
  );
}

const s_ = (v: unknown) => (typeof v === "string" && v ? v : null);
const n_ = (v: unknown) => (typeof v === "number" ? v : null);

/** a row's left line. Red only for a service lost now (Deyao, 2026-11-17: red = "immediate loss of service"): an alert
 *  refused for good, YYC AI's status turning to problem. Amber for an error of ours that is over (a failed sandbox
 *  start, a refused call, a host error). Quiet (ink): a news source's failure, such as one article's full text not
 *  fetched: most likely their end, outside our control (Deyao, 2026-11-17); and, dark cockpit, normal, recovered, the
 *  problem tile and Stage 2's one-off "no answer" (Deyao, 2026-11-02) */
function cls(id: PanelId, c: Card) {
  if (id === "alert" && c.ok === false) return "bad";
  if (id === "ai") return aiBad(c) ? "bad" : aiPast(c) ? "cau" : undefined;  // dark cockpit: back to normal stays quiet
  return undefined;
}

/** time, source, platform: the card's first line */
function Meta({ c, extra, children }: { c: Card; extra?: ReactNode; children?: ReactNode }) {
  const src = s_(c.source), plat = s_(c.platform);
  return <div className="m"><span className="num">{hhmmss(c.t)}</span>{children}{src && <span>{src}</span>}{plat && plat !== src && <span className="dim">{plat}</span>}{extra}</div>;
}
function Title({ v, ai, lines = 2 }: { v: unknown; ai?: unknown; lines?: 1 | 2 }) {
  const t = s_(v);
  return <div className={"t l" + lines}>{t ?? <span className="dim">(no title)</span>}{ai ? <span className="tag">AI title</span> : null}</div>;
}
/** a model's display name without its version and size words: "Qwen3.7 Flash" → "Qwen3.7", for the score lines */
const short = (m: string) => m.split(" ")[0];
const X = ({ lines = 1, k, children }: { lines?: number; k?: string; children: ReactNode }) => <div className={`x l${lines}${k ? " " + k : ""}`}>{children}</div>;

interface Comment { text?: string; author?: string | null; likes?: number | null }

function CardBody({ id, c, streams, models }: { id: PanelId; c: Card; streams: Stream[]; models: Models }) {
  const m1 = short(models.stage1) || "Stage 1", m2 = short(models.stage2) || "Stage 2";
  const th = (sid: string) => streams.find(x => x.id === sid)?.threshold ?? 50;
  switch (id) {
    case "ingest":
      return <><Meta c={c} extra={<>{s_(c.type) && <span className="dim">{s_(c.type)}</span>}{c.backlog ? <span className="tag">first read</span> : null}</>} />
        <Title v={c.title} ai={c.ait} />{s_(c.snippet) && s_(c.snippet) !== s_(c.title) ? <X k="dim">{s_(c.snippet)}</X> : null}</>;
    case "fulltext": {
      const ok = c.ok !== false;
      return <><Meta c={c} extra={<><span className="tag">{s_(c.route)}</span>{!ok && <span className="tag">not fetched</span>}{ok && <span className="num">{n_(c.chars)?.toLocaleString("en")} characters</span>}{s_(c.via) && s_(c.via) !== "page" && <span className="dim">via {s_(c.via)}</span>}</>} />
        <Title v={c.title} ai={c.ait} lines={1} />
        {ok ? s_(c.head) && <X lines={5}>{s_(c.head)}</X>
          : <X lines={2}>{s_(c.reason)}{c.kept ? ", the text it had is kept" : ""}</X>}</>;
    }
    case "comments": {
      const top: Comment[] = Array.isArray(c.top) ? (c.top as Comment[]) : s_(c.top) ? [{ text: s_(c.top)!, author: s_(c.top_author), likes: n_(c.top_likes) }] : [];
      return <><Meta c={c} extra={<span className="num">+{n_(c.new)} new{n_(c.total) != null ? `, ${n_(c.total)} on the platform` : ""}</span>} />
        <Title v={c.title} ai={c.ait} lines={1} />
        {top.map((x, i) => <X key={i} lines={i < 2 ? 2 : 1}>“{x.text}”<span className="dim"> {x.author || ""}{x.likes ? `, ${x.likes} likes` : ""}</span></X>)}</>;
    }
    case "stage1": {
      const sc = (c.scores || {}) as Record<string, number | null>;
      const rs = (c.reasons || {}) as Record<string, string>;
      const ids = (streams.length ? streams.map(x => x.id) : Object.keys(sc)).sort((a, b) => (sc[b] ?? -1) - (sc[a] ?? -1));
      return <><Meta c={c} extra={s_(c.purpose) !== "scoring" ? <span className="dim">{s_(c.purpose)}</span> : undefined} /><Title v={c.title} ai={c.ait} lines={1} />
        <div className="topics">{ids.map(k => {
          const v = sc[k]; const pass = v != null && v >= th(k), near = v != null && v >= 40;
          return <div key={k} className={"topic" + (pass ? " pass" : near ? " near" : "")}>
            <b>{k}</b><em className="num">{v ?? "–"}</em><i className="meter"><s style={{ width: `${Math.max(0, Math.min(100, v ?? 0))}%` }} /></i>
            <span>{rs[k] || (k === s_(c.best) ? s_(c.reason) : "") || ""}</span></div>;
        })}</div></>;
    }
    case "stage2": {
      if (c.failed) return <><Meta c={c} /><Title v={c.title} ai={c.ait} /><X lines={2} k="dim">{s_(c.failed)}</X></>;  // retried next round: quiet
      const ch = Object.entries((c.checks || {}) as Record<string, { s1: number | null; s2: number | null; reason: string | null; s1reason?: string | null }>);
      const labels = Array.isArray(c.labels) ? (c.labels as string[]) : [];
      return <><Meta c={c} extra={<>{s_(c.sentiment) && <span className="tag">{s_(c.sentiment)}</span>}{labels.map(l => <span key={l} className="dim">{l}</span>)}{ch.length > 2 && <span className="dim">+{ch.length - 2} more topics</span>}</>} />
        <Title v={c.title} ai={c.ait} lines={1} />
        {ch.slice(0, 2).map(([k, v]) => {
          const t = th(k), was = v.s1 != null && v.s1 >= t, now = v.s2 != null && v.s2 >= t;
          return <div key={k} className="chk">
            <X><b>{k}</b> <span className="num">{m1} {v.s1 ?? "–"} → {m2} {v.s2 ?? "–"}</span>{" "}
              <span className={"tag" + (was === now ? "" : " info")}>{now ? (was ? "passes" : "now passes") : (was ? "rejected" : "below")} (threshold {t})</span></X>
            <X k="dim">{m1}: {v.s1reason || "–"}</X>
            <X>{m2}: {v.reason || "–"}</X></div>;
        })}</>;
    }
    case "filed": {
      const labels = Array.isArray(c.labels) ? (c.labels as string[]) : [];
      const name = streams.find(x => x.id === c.stream)?.name;
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span><b className="tag ink">{s_(c.stream)}</b>{name && name !== c.stream && <span>{name}</span>}
          <span className="num">score {n_(c.score)}{n_(c.screen) != null ? ` (stage 1: ${n_(c.screen)})` : ""}</span>
          {s_(c.category) && <span className="dim">{s_(c.category)}</span>}{c.backpost ? <span className="tag">back-post</span> : null}</div>
        <Title v={c.title} />
        <X><span>{s_(c.source)}</span>{s_(c.sentiment) && <span className="tag">{s_(c.sentiment)}</span>}{labels.map(l => <span key={l} className="dim"> {l}</span>)}</X>
        {s_(c.reason) && <X lines={2} k="dim">{s_(c.reason)}</X>}</>;
    }
    case "alert": {
      const ok = c.ok !== false;
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span><b className={"tag " + (c.channel === "whatsapp" ? "wa" : "tg")}>{c.channel === "whatsapp" ? "WhatsApp" : "Telegram"}</b><b>{s_(c.stream)}</b>{s_(c.source) && <span>{s_(c.source)}</span>}{!ok && <span className="tag badt">not sent</span>}</div>
        <Title v={c.title} ai={c.ait} lines={ok ? 2 : 1} />{!ok && <X k="why">{s_(c.error)}</X>}</>;
    }
    case "ai":
      return <AiCard c={c} />;
    case "problem":
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span><span className={"tag" + (c.state === "failing" ? " ink" : "")}>{c.state === "failing" ? "failing" : "recovered"}</span><span className="dim">{s_(c.what)}</span></div>
        <div className="t l1">{s_(c.key)}</div>
        {s_(c.since) && <X k="dim">{c.state === "failing" ? "since" : "was failing since"} {hhmm(Date.parse(s_(c.since)!))}</X>}
        {s_(c.reason) && <X lines={2}>{s_(c.reason)}</X>}</>;
  }
  return null;
}

// ---- YYC AI (panel 9) and Systems (panel 10): kv live:sys (news-stream/monitor.py live_once) ----

type J = Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
const usd = (v: unknown, d = 2) => (typeof v === "number" ? `USD ${v.toFixed(d)}` : "–");
const dur = (s: unknown) => {
  if (typeof s !== "number") return "–";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
};
/** an e-mail address without a common mail domain, for the narrow lines */
const who = (v: unknown) => (s_(v) ?? "?").replace(/@(gmail\.com|yyc\.hk)$/, m => (m === "@yyc.hk" ? "@yyc" : ""));
const STATUS: Record<string, [string, string]> = {
  ok: ["all fine", ""], warming: ["warming up", "info"], watch: ["needs a look", "warn"], blind: ["can't see", "blindt"], problem: ["problem", "badt"] };
/** a finding's row by its level (hermit health_verdict Level) */
const FINDING: Record<string, string> = { problem: "why", blind: "blindc", warming: "infoc" };
/** an ISO time → HH:MM:SS HKT */
const clock = (v: unknown) => (s_(v) ? hhmmss(Date.parse(s_(v)!)) : "–");
/** live:sys older than this: its areas say since when */
const SYS_STALE_MS = 60_000;

/** YYC AI's status turned to problem: the one activity card that says a service is lost now */
function aiBad(c: Card) {
  return c.what === "status" && c.state === "problem";
}
/** an error that is over: a failed sandbox start, a refused or failed call, a host error (amber) */
function aiPast(c: Card) {
  return (c.what === "start" && c.ok === false && !c.quiet) || c.what === "egress" || (c.what === "host" && !!c.error);
}

function AiCard({ c }: { c: Card }) {
  const w = s_(c.what) || "";
  const tag = (t: string, k = "") => <span className={"tag " + k}>{t}</span>;
  switch (w) {
    case "calls": {
      const m = c.models && typeof c.models === "object" ? Object.entries(c.models as Record<string, number>) : [];
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{tag("model calls")}<span>{who(c.owner)}</span></div>
        <X><b>+{n_(c.calls)}</b> calls, {usd(n_(c.usd), 4)}<span className="dim">; today {n_(c.calls_today)} calls, {usd(n_(c.usd_today))}</span></X>
        {m.length ? <X k="dim">{m.map(([k, v]) => `${k} +${v}`).join(", ")}</X> : null}</>;
    }
    case "start": {
      // quiet: refused at the account's own sandbox limit, which hermit's notifyPersonLimit says needs nobody: grey
      const quiet = c.ok === false && !!c.quiet;
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{quiet ? tag("sandbox start refused") : c.ok === false ? tag("sandbox start failed", "warn") : tag("sandbox started")}<span>{who(c.owner)}</span></div>
        <X>{[s_(c.host) ? `host ${s_(c.host)}` : "no host", n_(c.ms) != null ? `${(n_(c.ms)! / 1000).toFixed(1)} s` : null].filter(Boolean).join(", ")}</X>
        {quiet ? <X k="dim">at its own sandbox limit</X> : s_(c.reason) && <X k={c.ok === false ? "warn" : "dim"}>{s_(c.reason)}</X>}</>;
    }
    case "release":
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{tag("sandbox released")}<span>{who(c.owner)}</span></div>
        <X>{s_(c.host) ? `host ${s_(c.host)}, ` : ""}lived {dur((n_(c.ms) ?? 0) / 1000)}</X>
        {s_(c.reason) && <X k="dim">{s_(c.reason)}</X>}</>;
    case "host":
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{tag(s_(c.kind) || "host", c.error ? "warn" : "")}<span>{s_(c.host)}</span>{s_(c.owner) && <span className="dim">{who(c.owner)}</span>}</div>
        <X lines={2} k={c.error ? "warn" : "dim"}>{s_(c.error) || s_(c.detail) || ""}</X></>;
    case "egress":
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{tag(`${s_(c.kind) || "egress"} ${n_(c.status) ?? s_(c.decision) ?? ""}`, "warn")}<span>{who(c.owner)}</span></div>
        <X lines={2} k="warn">{s_(c.host)}{s_(c.path) ? s_(c.path) : ""}: {s_(c.decision)}</X></>;
    case "status": {
      const [label, k] = STATUS[s_(c.state) || ""] || [s_(c.state) || "?", ""];
      const f = Array.isArray(c.findings) ? (c.findings as string[]) : [];
      return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{tag(label, k)}<span className="dim">was {STATUS[s_(c.was) || ""]?.[0] ?? s_(c.was)}</span></div>
        <X lines={2} k={k === "badt" ? "why" : k === "blindt" ? "blind" : undefined}>{f.join(", ") || "no findings"}</X></>;
    }
  }
  return <><div className="m"><span className="num">{hhmmss(c.t)}</span>{tag(w || "?")}</div></>;
}

/** live:sys's age note: "as of HH:MM:SS"; past SYS_STALE_MS "no update since" in the blind colour (Deyao, 2026-11-17:
 * old figures mean the board no longer sees the present) */
function Age({ at, now }: { at?: string; now: number }) {
  if (!at) return null;
  const t = Date.parse(at), old = now && now - t > SYS_STALE_MS;
  return <span className={"num " + (old ? "blind" : "dim")}>{old ? "no update since " : "as of "}{hhmmss(t)}</span>;
}

const R = ({ k, children, title }: { k?: string; children: ReactNode; title?: string }) => <div className={"row" + (k ? " " + k : "")} title={title}>{children}</div>;
const Sec = ({ children }: { children: ReactNode }) => <div className="sec">{children}</div>;

/** YYC AI's state: its status, sandboxes now, today's use and the budget, errors, starts */
function AiState({ sys, now, w, h, side }: { sys: LiveSys | null; now: number; w: number; h: number; side: boolean }) {
  const a = (sys?.ai || null) as J | null;
  const style = side ? { position: "absolute" as const, left: 0, top: HEADER + 1, width: w, height: h } : { height: h, flex: "none" as const };
  if (!a) return <div className="st" style={style}><R k="dim">{sys ? "no YYC AI data yet" : "Loading…"}</R></div>;
  if (a.error) return <div className="st" style={style}><R k="blindc">YYC AI snapshot: can't read: {a.error}</R><Age at={sys?.at} now={now} /></div>;
  const hl = (a.health || {}) as J, [label, k] = STATUS[hl.state] || [hl.state || "?", ""];
  const b = (a.budget || {}) as J, rec = (a.recent || {}) as J, st24 = (a.starts_24h || {}) as J;
  const sbx = (a.sandboxes || []) as J[];
  const errs = Object.entries((a.errors || {}) as Record<string, string>);
  return (
    <div className="st" style={style} data-ai-state="">
      <R><span className={"tag " + k}>{label}</span><span className="dim">ai.yyc-internal.com</span><Age at={a.at} now={now} /></R>
      {((hl.findings || []) as J[]).slice(0, 3).map((f, i) => <R key={i} k={FINDING[f.level] || "warnc"}>{f.text}</R>)}
      {errs.map(([n, m]) => <R key={n} k="blindc">could not read {n}: {m}</R>)}
      <Sec>Sandboxes now: {sbx.length}{a.max_active ? ` of ${a.max_active} max` : ""}, scaler {a.scaler?.available ?? "–"} free slots{a.scaler?.waiting ? `, ${a.scaler.waiting} waiting` : ""}</Sec>
      {sbx.length ? sbx.slice(0, 6).map((x, i) => (
        <R key={i} k={x.status === "starting" ? "infoc" : undefined}>
          <span className="c1">{who(x.owner)}</span><span className="c2">{x.status}</span><span className="c2">{x.host ?? "–"}</span>
          <span className="num">up {dur(x.age_s)}</span><span className={"num" + ((x.idle_s ?? 1e9) < 60 ? " now" : " dim")}>{(x.idle_s ?? 1e9) < 60 ? "active now" : `idle ${dur(x.idle_s)}`}</span>
          {x.migrations ? <span className="dim">{x.migrations} moves</span> : null}</R>))
        : <R k="dim">none running</R>}
      {sbx.length > 6 && <R k="dim">+{sbx.length - 6} more</R>}
      <Sec>Today (HKT): {usd(b.today_usd)}, {b.calls_today ?? "–"} model calls; month {usd(b.month_usd)}</Sec>
      <R k={b.exhausted ? "why" : undefined}>budget: {usd(b.spent_usd)} of USD {b.limit_usd ?? "–"} used since {s_(b.since) ? new Date(Date.parse(b.since) + 8 * 3600e3).toISOString().slice(5, 10) : "–"}{b.exhausted ? "; used up: model calls are refused" : ""}</R>
      {((a.by_user || []) as J[]).slice(0, 5).map((u, i) => {
        const p = ((a.people_today || []) as J[]).find(x => x.owner === u.owner);
        return <R key={i}><span className="c1">{who(u.owner)}</span><span className="num">{u.calls} calls</span><span className="num">{usd(u.today_usd)}</span>
          {p ? <span className="dim">{p.conversations} conversations today</span> : null}</R>;
      })}
      {((a.by_model || []) as J[]).length ? <R k="dim">{((a.by_model || []) as J[]).slice(0, 3).map(m => `${m.model} ${m.calls} (${usd(m.today_usd)})`).join(", ")}</R> : null}
      <Sec>Errors in the last {a.recent_minutes ?? 15} min; sandbox starts in 24 h</Sec>
      <R k={rec.failedStarts || rec.modelErrors || rec.hostErrors ? "warnc" : undefined}>
        {rec.failedStarts ?? "–"} failed starts, {rec.modelErrors ?? "–"} model/tool call errors, {rec.hostErrors ?? "–"} host errors, {rec.blocked ?? "–"} blocked hosts</R>
      <R>{st24.n ?? "–"} starts, {st24.failed ?? 0} failed, median {n_(st24.p50Ms) != null ? (st24.p50Ms / 1000).toFixed(1) + " s" : "–"}, p95 {n_(st24.p95Ms) != null ? (st24.p95Ms / 1000).toFixed(1) + " s" : "–"}</R>
      <R k="dim" title={s_(a.source) || undefined}>source: YYC AI operator snapshot (its Postgres, Kubernetes, the scaler)</R>
    </div>
  );
}

/** the first words of a source, up to its first "(": "provider API", "Exa's own charged cost per call" */
const srcShort = (v: unknown) => (s_(v) || "").split(" (")[0].split(";")[0];

const gib = (b: unknown) => (typeof b === "number" ? `${(b / 2 ** 30).toFixed(1)} GiB` : "–");
const count = (v: unknown) => (typeof v === "number" ? v.toLocaleString("en-US") : "–");
/** an ISO time → "HH:MM" today, else "DD/MM HH:MM" (HKT) */
const when = (v: unknown, now: number) => {
  const t = s_(v) ? Date.parse(s_(v)!) : NaN;
  if (Number.isNaN(t)) return "–";
  const d = (x: number) => new Date(x + 8 * 3600e3).toISOString().slice(0, 10);
  return d(t) === d(now || Date.now()) ? hhmm(t) : `${d(t).slice(8, 10)}/${d(t).slice(5, 7)} ${hhmm(t)}`;
};
const WISERS_STATE: Record<string, string> = { held: "logged in", logged_out: "logged out", unknown: "login unknown" };

/** a box narrower than this (a phone) shows each part of a Systems line on its own row; wider, they share one */
const SYS_NARROW = 600;
type Part = { k?: string; n: ReactNode };
/** one line of the Systems panel and its fixed height. The panel's box is known in px and every line is shown: the
 * lines are laid out to fit it (Deyao, 2026-11-12: Postgres, WiseSearch and object storage fell off the bottom of the
 * screen; 2026-11-17: "no overflow" means every line fits, never a link to the rest) */
type Item = { key: string; node: ReactNode; h: number };
const ROW_PX = 14, SEC_PX = 18;  // live.css .st .row (14px) and .st .sec (16px + 2px margin)
const COL_MIN = 440, COL_GAP = 13;  // a column of the Systems panel at least this wide; between two: live.css .col + .col (6 + 1 + 6)
const PC_W = 270;  // a provider's cell in the compact form (its name and first figure)
const row = (key: string, k: string | undefined, children: ReactNode, title?: string): Item =>
  ({ key, node: <R key={key} k={k} title={title}>{children}</R>, h: ROW_PX });
const sec = (key: string, children: ReactNode): Item => ({ key, node: <Sec key={key}>{children}</Sec>, h: SEC_PX });
const height = (xs: Item[]) => xs.reduce((t, x) => t + x.h, 0);

/** the lines in at most `n` columns of `px` each, in order, a column ending before a heading rather than under it;
 * null when they don't fit */
export function flow(items: Item[], n: number, px: number): Item[][] | null {
  const cols: Item[][] = [[]];
  let h = 0;
  for (const x of items) {
    let col = cols[cols.length - 1];
    if (h + x.h > px) {
      if (cols.length >= n) return null;
      const carry = col.length > 1 && col[col.length - 1].h === SEC_PX ? [col.pop()!] : [];
      col = [...carry];
      cols.push(col);
      h = height(col);
      if (h + x.h > px) return null;
    }
    col.push(x);
    h += x.h;
  }
  return cols;
}

/** parts → lines: wide, each group's parts on one line (joined by "; "), the line in its worst part's colour; narrow,
 * one line per part */
function lines(key: string, groups: Part[][], narrow: boolean): Item[] {
  const rows = narrow ? groups.flat().map(p => [p]) : groups;
  return rows.map((g, i) => {
    const k = g.find(p => p.k === "why")?.k || g.find(p => p.k === "blindc")?.k || g.find(p => p.k)?.k;
    return row(`${key}${i}`, k, <span>{g.map((p, j) => <span key={j}>{j ? "; " : ""}{p.n}</span>)}</span>);
  });
}

/** Postgres (monitor.py database panel): load, the longest transaction, WAL archiving and backups, space. Near a limit
 * (connections, space) and a failing backup are amber: nobody is stopped (Deyao, 2026-11-17) */
function pgItems(pg: J, now: number, narrow: boolean): Item[] {
  if (pg.error) return [row("pg", "blindc", <>Postgres: can't read: {pg.error}</>)];
  const c = (pg.connections || {}) as J, by = (c.by_state || {}) as Record<string, number>;
  const busy = Object.entries(by).filter(([k]) => k !== "idle").sort((a, b) => b[1] - a[1]);
  const lt = pg.longest_transaction as J | null, a = (pg.archive || {}) as J, sp = (pg.space || {}) as J;
  const dbs = (pg.databases || []) as J[], dbTotal = dbs.reduce((t, d) => t + (d.bytes || 0), 0);
  const low = typeof sp.free_bytes === "number" && sp.free_bytes < sp.floor_bytes;
  const full = c.max && c.total >= 0.8 * c.max;
  const probs = (a.problems || []) as string[];
  const mib = (v: unknown) => gib(n_(v) != null ? (v as number) * 2 ** 20 : null);
  return lines("pg", [
    [{ k: full ? "warnc" : undefined, n: <>Postgres load: <b>{c.total ?? "–"}</b> of {c.max ?? "–"} connections{busy.length ? ` (${busy.map(([k, v]) => `${v} ${k}`).join(", ")})` : ""}, <b>{pg.tps ?? "–"}</b> transactions/s</> },
     { n: <>CPU <b>{pg.cpu_cores ?? "–"}</b> of {pg.node_cores ?? "–"} cores, memory <b>{mib(pg.memory_mib)}</b> of {mib(pg.memory_limit_mib)}</> }],
    [{ n: <>longest open transaction {lt ? <><b>{dur(lt.seconds)}</b> ({[lt.database, lt.application || lt.user, lt.state].filter(Boolean).join(", ")})</> : "none"}</> },
     { n: <>{c.waiting_on_locks ?? 0} waiting on locks</> }],
    [{ k: probs.length ? "warnc" : undefined, n: <>WAL archive: last {n_(a.seconds_since_archived) != null ? `${dur(a.seconds_since_archived)} ago` : "–"}, {a.waiting ?? "–"} waiting{probs.length ? ": " + probs.join("; ") : a.last_failed ? `, last failure ${when(a.last_failed, now)}` : ""}</> },
     { n: <>base backup {when(a.last_backup, now)}</> }],
    [{ k: low ? "warnc" : undefined, n: <>Postgres space: <b>{gib(sp.free_bytes)}</b> free of {gib(sp.capacity_bytes)} (alert under {gib(sp.floor_bytes)})</> },
     { n: <>databases {gib(dbTotal)}{dbs[0] ? ` (${dbs[0].name} ${gib(dbs[0].bytes)})` : ""}, WAL {gib(pg.wal_bytes)}, volume {sp.pvc ?? "–"}</> }],
  ], narrow);
}

/** the WiseSearch relay (monitor.py wisers panel; its detail on /app/monitor#wisers) */
function wisersItems(w: J, now: number, narrow: boolean): Item[] {
  if (w.error) return [row("ws", "blindc", <>WiseSearch: can't read: {w.error}</>)];
  const pr = w.probe as J | null, f = Object.entries((w.failures || {}) as Record<string, number>);
  // red: staff cannot search (logged out, or the relay's health check failed twice in a row: monitor.py wisers_down);
  // amber: a few failed calls, a health check that stopped
  const probs = (w.problems || []) as string[], bad = w.state === "logged_out" || w.down ? "why" : probs.length ? "warnc" : undefined;
  return lines("ws", [[
    { k: bad, n: <>WiseSearch: <b>{WISERS_STATE[w.state] || w.state || "–"}</b>{w.since ? ` since ${when(w.since, now)}` : ""}, health check {!pr ? "none yet" : pr.ok ? <>ok ({pr.secs} s, {when(pr.at, now)})</> : <>failing ({pr.outcome})</>}{probs.length ? ": " + probs.join("; ") : ""}</> },
    { n: <>{f.length ? f.map(([k, v]) => `${v} ${k}`).join(", ") : "no"} failed calls in 15 min, {w.logins_1h ?? 0} logins in 1 h{w.failed_logins_1h ? ` (${w.failed_logins_1h} failed)` : ""}, {w.today ?? "–"} searches today</> },
  ]], narrow);
}

/** YYC's Hetzner Object Storage buckets (monitor.py storage panel): Hetzner's own figures, as many buckets a line as
 * fit (one on a phone); a bucket that can't be read first, then the biggest */
function storageItems(st: J, now: number, w: number): Item[] {
  if (st.error) return [row("os", "blindc", <>Object storage: can't read: {st.error}</>)];
  const bs = [...((st.buckets || []) as J[])].sort((a, b) => (b.error ? 1 : 0) - (a.error ? 1 : 0) || (b.bytes || 0) - (a.bytes || 0));
  const per = Math.max(1, Math.floor((w - 8) / 306)), out: Item[] = [
    row("os", st.refresh_error ? "blindc" : undefined, <><span>Object storage: <b>{gib(st.bytes)}</b> in {count(st.objects)} objects, {bs.length} buckets</span><span className="src dim">Hetzner's bucket stats, {when(st.computed_at, now)}</span></>, st.refresh_error || undefined)];
  for (let i = 0; i < bs.length; i += per) {
    const p = bs.slice(i, i + per);
    out.push(row(`bk${i}`, undefined, p.map(b => <span key={b.name} className={"bk" + (b.error ? " blindc" : "")}>{b.name} {b.error ? "can't read" : <><b>{gib(b.bytes)}</b>, {count(b.objects)} objects</>}</span>)));
  }
  return out;
}

/** the sandbox hosts: one line each; when they don't all fit, the healthy AWS hosts share one line (a booting, failing or
 * unreachable host and the gateway keep their own) */
function hostItems(hosts: J[], fold: boolean, narrow = false): Item[] {
  // narrow (a phone, a column): the load and memory on a line of their own under the host
  const one = (x: J, i: number) => {
    // not answering: red when someone's sandbox is on it (or it is the gateway: every sandbox's network), else amber
    const k = x.status === "booting" ? "infoc" : !x.reachable ? (x.gateway || x.sandboxes ? "why" : "warnc") : undefined;
    const load = <span className="num dim">load {x.load1 ?? "–"} / {x.cpus} CPU, {x.mem_free_gib ?? "–"} of {x.mem_total_gib ?? "–"} GiB free</span>;
    const head = <>
      <span className="pn">{x.gateway ? "gateway" : x.kind === "aws" ? "AWS host" : x.kind}</span><span className="c2">{x.name}</span>
      <span>{x.status}{x.reachable || x.status === "booting" ? "" : ", not answering"}</span>
      {!x.gateway && <span className="num">{x.sandboxes}{x.slots != null ? ` of ${x.slots}` : ""} sandboxes</span>}</>;
    return narrow ? [row(`h${i}`, k, head), row(`h${i}l`, k, <><span className="ind" />{load}</>)] : [row(`h${i}`, k, <>{head}{load}</>)];
  };
  const healthy = (x: J) => !x.gateway && x.status === "active" && x.reachable;
  if (!fold || hosts.filter(healthy).length < 2) return hosts.flatMap(one);
  const ok = hosts.filter(healthy), sb = ok.reduce((t, x) => t + (x.sandboxes || 0), 0), sl = ok.reduce((t, x) => t + (x.slots || 0), 0);
  return [...hosts.map((x, i) => [x, i] as const).filter(([x]) => !healthy(x)).flatMap(([x, i]) => one(x, i)),
    ...(narrow ? [row("hok", undefined, <><span className="pn">AWS hosts</span><span className="c2">{ok.length} active</span><span className="num">{sb} of {sl} sandboxes</span></>),
      row("hokn", undefined, <><span className="ind" /><span className="num dim">{ok.map(x => x.name).join(", ")}</span></>)]
      : [row("hok", undefined, <><span className="pn">AWS hosts</span><span className="c2">{ok.length} active</span><span className="num">{sb} of {sl} sandboxes</span>
      <span className="num dim">{ok.map(x => x.name).join(", ")}</span></>)])];
}

/** panel 10: the providers' balances (their own figures, with the source), stops, pods, hosts, nodes, Postgres, the
 * WiseSearch relay, the object storage buckets. Every line is shown (Deyao, 2026-11-17: "it should just fit"): the
 * first layout that fits the box wins: one column, two columns (a wide box), then the same with the healthy hosts on
 * one line, then the compact form (several providers a line, the healthy cluster checks on one line, Postgres, WiseSearch and
 * object storage a line each; then without the headings) */
function SysPanel({ box, sys, now }: { box: Box; sys: LiveSys | null; now: number }) {
  const hh = (sys?.health || null) as J | null, ai = (sys?.ai || {}) as J;
  const stops = (hh?.stops || []) as J[];
  const deps = (hh?.deployments || {}) as J, cj = (hh?.cronjobs || {}) as J, pg = (hh?.postgres || {}) as J;
  const nodes = Array.isArray(hh?.nodes) ? (hh!.nodes as J[]) : [];
  const nodeErr = hh && !Array.isArray(hh.nodes) ? (hh.nodes as J | undefined)?.error : null;
  const notReady = nodes.filter(n => !n.ready || n.pressure?.length || n.unschedulable);
  const hosts = (ai.hosts || []) as J[], svc = (ai.service || []) as J[];
  const providers = (hh?.providers || []) as J[], wisers = (hh?.wisers || {}) as J, storage = (hh?.storage || {}) as J;
  const px = box.h - HEADER - 2 - 4;  // the box less its header, rules and padding
  const inner = box.w - 2 - 8;        // less its rules and padding
  const ncol = Math.max(1, Math.floor((inner + COL_GAP) / (COL_MIN + COL_GAP)));
  const colW = Math.floor((inner - (ncol - 1) * COL_GAP) / ncol);
  const stopRows = () => stops.map((x, i) => row(`stop${i}`, "why", <>{x.name}: {x.tag || "requests failing"}{x.error ? `, ${x.error}` : ""}{x.since ? `, since ${clock(x.since)}` : ""}</>));
  // a node down or under pressure is amber (its pods move; what is really down shows on its own line); red when none is ready
  const nodesK = nodeErr ? "blindc" : nodes.length && nodes.every(n => !n.ready) ? "why" : notReady.length ? "warnc" : undefined;
  const nodesRow = () => row("nodes", nodesK, <>nodes: {nodeErr ? `can't read: ${nodeErr}` : `${nodes.length - notReady.length} of ${nodes.length} ready`}
    {notReady.map(n => `; ${n.name}${n.ready ? "" : " not ready"}${n.pressure?.length ? " " + n.pressure.join(", ") : ""}${n.unschedulable ? " cordoned" : ""}`).join("")}</>);
  // a Deployment with some pods ready still serves: amber; red when one people work through has none ("name 0/2"),
  // never the monitor (healthStates.ts deploymentLost)
  const depsK = (deps.short || []).some(deploymentLost) ? "why" : (deps.short || []).length ? "warnc" : undefined;
  const depsRow = () => row("deps", depsK, <>news stream Deployments: {deps.ok ?? "–"} of {deps.n ?? "–"} with every pod ready{(deps.short || []).length ? "; short: " + deps.short.join(", ") : ""}</>);
  // late (no success for three of its intervals) is red: the job's work is not being done; one failed run is amber, it
  // runs again (Deyao, 2026-11-17)
  const cjK = (cj.late || []).length ? "why" : (cj.failed || []).length ? "warnc" : undefined;
  const cjRow = () => row("cj", cjK, <>CronJobs: {cj.n ?? "–"} on, {cj.running ?? 0} running{(cj.late || []).length ? "; late: " + cj.late.join(", ") : ""}{(cj.failed || []).length ? "; last run failed: " + cj.failed.join(", ") : ""}{cj.off ? `, ${cj.off} off` : ""}</>);
  // red: a Deployment with no pod ready (or missing); amber: fewer ready than asked, the others serve (hermit's "watch";
  // a hermit from before that state says "problem" with ready pods); blue: rolling out
  const podDown = (x: J) => x.state === "problem" && !(x.ready > 0);
  const podShort = (x: J) => x.state === "watch" || (x.state === "problem" && x.ready > 0);
  const podsK = svc.some(podDown) ? "why" : svc.some(podShort) ? "warnc" : svc.some(x => x.state === "warming") ? "infoc" : !svc.length && ai.error ? "blindc" : undefined;
  const podsRow = () => row("pods", podsK, <>YYC AI pods: {svc.length ? svc.map(x => `${x.name} ${x.ready}/${x.desired}${x.restarts ? ` (${x.restarts} restarts)` : ""}`).join(", ") : ai.error ? "snapshot unreadable" : "–"}</>);
  const noHosts = () => (!hosts.length && !ai.error ? [row("nohosts", "dim", "no sandbox hosts")] : []);
  /** every line, for a column `w` px wide (the box's width less nothing: as SYS_NARROW and storageItems count it) */
  const full = (w: number, fold: boolean): Item[] => {
    const narrow = w < SYS_NARROW;
    return [
      ...stopRows(),
      sec("s1", <>Providers' own figures, balances as of {clock(hh!.balances_at)}</>),
      ...providers.flatMap(p => {
        const figs = (p.figs || []) as [string, string][];
        const bad = p.status !== "ok", rest = figs.length > 1 && figs[1][0] === "today" ? 2 : 1;
        return [row(`p${p.id}`, bad ? (p.status === "error" ? "blindc" : "dim") : undefined, <><span className="pn" data-provider={p.id}>{p.name}</span>
            {bad ? <span>{p.status === "not_connected" ? "not connected" : "can't read"}: {s_(p.error)}</span>
              : figs.length ? <span>{figs[0][0]} <b>{figs[0][1]}</b>{rest === 2 ? <>, today <b>{figs[1][1]}</b></> : null}</span> : null}
            <span className="src dim">{srcShort(p.source)}</span></>, s_(p.source) || undefined),
          ...(bad ? [] : figs.slice(rest).map(([l, v], i) => row(`p${p.id}-${i}`, undefined, <><span className={narrow ? "ind" : "pn"} /><span>{l} <b>{v}</b></span></>)))];
      }),
      sec("s2", "Cluster, news stream, YYC AI"),
      nodesRow(), depsRow(), cjRow(), podsRow(),
      ...hostItems(hosts, fold, narrow),
      ...noHosts(),
      sec("s3", "Postgres, WiseSearch, object storage"),
      ...pgItems(pg, now, narrow),
      ...wisersItems(wisers, now, narrow),
      ...storageItems(storage, now, w),
    ];
  };
  /** the compact form: each provider's first figure in a cell, several a line; the healthy cluster checks on one line
   * (a check with a problem keeps its own); Postgres, WiseSearch and object storage a line each (in its state's colour) */
  const compact = (w: number, heads: boolean): Item[] => {
    const per = Math.max(1, Math.floor((w + 6) / (PC_W + 6))), cw = Math.floor((w - (per - 1) * 6) / per);
    const cells = providers.map(p => {
      const figs = (p.figs || []) as [string, string][], bad = p.status !== "ok";
      return <span key={p.id} className={"pc" + (bad ? (p.status === "error" ? " blindc" : " dim") : "")} style={{ width: cw }} title={s_(p.source) || undefined}>
        <span data-provider={p.id}>{s_(p.name)?.split(" (")[0]}</span> {bad ? (p.status === "not_connected" ? "not connected" : `can't read: ${s_(p.error)}`)
          : figs.length ? <>{figs[0][0]} <b>{figs[0][1]}</b></> : null}</span>;
    });
    const provRows: Item[] = [];
    for (let i = 0; i < cells.length; i += per) provRows.push(row(`pc${i}`, undefined, cells.slice(i, i + per)));
    const ok: string[] = [], probs: Item[] = [];
    if (nodesK) probs.push(nodesRow()); else ok.push(`nodes ${nodes.length} of ${nodes.length} ready`);
    if ((deps.short || []).length) probs.push(depsRow()); else ok.push(`Deployments ${deps.ok ?? "–"} of ${deps.n ?? "–"}`);
    if (cjK) probs.push(cjRow()); else ok.push(`CronJobs ${cj.n ?? "–"} on, ${cj.running ?? 0} running`);
    if (podsK || !svc.length) probs.push(podsRow());
    else ok.push(`YYC AI pods ${svc.reduce((t, x) => t + (x.ready || 0), 0)} of ${svc.reduce((t, x) => t + (x.desired || 0), 0)} ready`);
    const c = (pg.connections || {}) as J, sp = (pg.space || {}) as J, pp = ((pg.archive || {}) as J).problems as string[] || [];
    const low = typeof sp.free_bytes === "number" && sp.free_bytes < sp.floor_bytes, busy = c.max && c.total >= 0.8 * c.max;
    const pgRow = pg.error ? row("pg", "blindc", <>Postgres: can't read: {pg.error}</>)
      : row("pg", low || busy || pp.length ? "warnc" : undefined, <span>Postgres: <b>{c.total ?? "–"}</b> of {c.max ?? "–"} connections, <b>{pg.tps ?? "–"}</b> transactions/s, <b>{gib(sp.free_bytes)}</b> free{low ? ` (alert under ${gib(sp.floor_bytes)})` : ""}{pp.length ? "; WAL archive: " + pp.join("; ") : ""}</span>);
    const bad = ((storage.buckets || []) as J[]).filter(b => b.error).map(b => b.name);
    return [
      ...stopRows(),
      ...(heads ? [sec("s1", <>Providers, balances as of {clock(hh!.balances_at)}</>)] : []),
      ...provRows,
      ...(heads ? [sec("s2", "Cluster, news stream, YYC AI")] : []),
      ...(ok.length ? [row("ok", undefined, ok.join(" · "))] : []), ...probs,
      ...hostItems(hosts, true, true),
      ...noHosts(),
      ...(heads ? [sec("s3", "Postgres, WiseSearch, object storage")] : []),
      pgRow,
      ...wisersItems(wisers, now, true).slice(0, 1),
      ...storageItems(storage, now, w).slice(0, 1),
      ...(bad.length ? [row("bkbad", "blindc", <>buckets that can't be read: {bad.join(", ")}</>)] : []),
    ];
  };
  let cols: Item[][] | null = null, cw = inner;
  if (!hh || hh.error) cols = [[!hh ? row("x", "dim", sys ? "no health data yet" : "Loading…") : row("x", "blindc", <>can't read: {s_(hh.error)}</>)]];
  else {
    const tries: [number, () => Item[]][] = [[1, () => full(box.w, false)]];
    if (ncol > 1) tries.push([ncol, () => full(colW + 8, false)]);
    tries.push([1, () => full(box.w, true)]);
    if (ncol > 1) tries.push([ncol, () => full(colW + 8, true)]);
    tries.push([ncol, () => compact(colW, true)], [ncol, () => compact(colW, false)]);
    for (const [n, f] of tries) {
      cols = flow(f(), n, px);
      if (cols) { cw = n > 1 ? colW : inner; break; }
    }
    cols ||= [compact(colW, false)];  // nothing fits: the compact form, its last lines under the box (tests keep this from happening)
  }
  return (
    <section className="lp" data-panel="sys" aria-label={PANEL_NAMES.sys} style={{ left: box.x, top: box.y, width: box.w, height: box.h }}>
      <h2 style={{ height: HEADER }}><span>{PANEL_NAMES.sys}</span><span className="hr"><Age at={sys?.at} now={now} /></span></h2>
      {cols.length === 1 ? <div className="st">{cols[0].map(x => x.node)}</div>
        : <div className="st cols">{cols.map((c, i) => <div key={i} className="col" style={{ width: cw }}>{c.map(x => x.node)}</div>)}</div>}
    </section>
  );
}

/** Jarvis 4's state words and their colour (the flight deck's: working is normal and stays ink; waiting and idle are
 * amber (Deyao, 2026-11-17); background jobs, a schedule (Deyao, 2026-11-17) and booting go on by themselves, blue) */
const JARVIS_TAG: Record<JarvisSession["state"], [string, string]> = {
  working: ["working", "inkf"], waiting: ["needs you", "warn"], background: ["background", "info"], scheduled: ["scheduled", "info"], idle: ["idle", "warn"], booting: ["booting", "info"],
};
/** "2 background jobs, 1 subagent" */
const workersText = (x: JarvisSession) => [x.jobs ? `${x.jobs} background ${x.jobs === 1 ? "job" : "jobs"}` : "",
  x.subagents ? `${x.subagents} ${x.subagents === 1 ? "subagent" : "subagents"}` : ""].filter(Boolean).join(", ");

/** a scheduled session's next wake in HKT: "14:30" today, "11-19 14:30" on a later day */
const wakeAt = (ms: number, now: number) => {
  const d = (t: number) => new Date(t + 8 * 3600e3).toISOString();
  return (d(ms).slice(0, 10) === d(now).slice(0, 10) ? "" : d(ms).slice(5, 10) + " ") + hhmm(ms);
};
/** panel 11: Deyao's running Jarvis 4 sessions in the router's order (amber first), one fixed cell each (liveLayout jarvisGrid) */
function JarvisPanel({ box, j, now }: { box: Box; j: JarvisLive | null; now: number }) {
  const g = jarvisGrid(box), list = (j?.sessions || []).slice(0, g.n);
  const more = j ? j.total - list.length : 0;
  const old = !!j && !!now && now - j.got > JARVIS_STALE_MS;
  return (
    <section className="lp" data-panel="jarvis" aria-label={PANEL_NAMES.jarvis} style={{ left: box.x, top: box.y, width: box.w, height: box.h }}>
      <h2 style={{ height: HEADER }}><span>{PANEL_NAMES.jarvis}</span>
        <span className="hr">{more > 0 ? <span className="dim">{more} more, </span> : null}
          {j ? <span className={"num " + (old ? "blind" : "dim")}>{old ? "no update since " : "as of "}{hhmmss(j.got)}</span> : null}</span></h2>
      <div className="js" style={{ gridTemplateColumns: `repeat(${g.cols}, ${Math.floor((box.w - 2) / g.cols)}px)`, gridTemplateRows: `repeat(${g.rows}, ${JARVIS_ROW_H}px)` }}>
        {!j ? <div className="jr dim">{sys0(now)}</div> : !list.length ? <div className="jr dim">no session running</div>
          : list.map((x, i) => {
            const [word, k] = JARVIS_TAG[x.state];
            return <div key={i} className={"jr" + (i >= g.rows ? " c2nd" : "")} data-state={x.state}>
              <div className="j1"><span className={"tag " + k}>{word}</span><span className="jn">{x.name}</span></div>
              <div className="j2 num">{x.state === "scheduled" ? (x.next ? `next ${wakeAt(x.next, now || j.got)}` : "watching")
                : <>{x.since ? `for ${dur(Math.max(0, (now || j.got) - x.since) / 1000)}` : ""}{workersText(x) ? `${x.since ? ", " : ""}${workersText(x)}` : ""}</>}</div>
            </div>;
          })}
      </div>
    </section>
  );
}
const sys0 = (now: number) => now ? "no report from Jarvis 4 yet" : "Loading…";
const JARVIS_STALE_MS = 60_000;  // the router pushes every 5 s: a minute without one is blind (magenta)
