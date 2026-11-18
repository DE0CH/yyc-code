// YYC system health (/app/health; Deyao, 2026-11-17): the systems half of the live dashboard on one iPhone screen, for
// the "YYC Systems" app (DE0CH/yyc-health-ios, a WebView of this page). Layout E of the mocks he picked: two columns
// ("otherwise it's too wide"), Jarvis 4's sessions and the cluster on the left, YYC AI, the providers, the sandbox hosts,
// Postgres, WiseSearch and the object storage on the right; no summary on top. The window's size decides how much
// detail shows (Deyao: "the screen space should inform the level of details"): lib/healthFit.ts.
// The look is the wall's (live.css): the pixel font at its design size, whole-px lines, the flight-deck state colours,
// dark only; read only (no link, button or input). Data: /app/health/data every POLL_MS (kv live:sys and Jarvis 4's
// last push); a read failing for STALE_MS greys the page out and says since when; a part older than a minute says so in
// its heading, in magenta.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { BUILD_ID } from "../lib/build";
import { monoEmoji } from "../lib/monoEmoji";
import { POLL_MS, STALE_MS, hhmm, hhmmss } from "../lib/liveLayout";
import { COL_RULE, JARVIS_ORDER, ROW, columns, fit, jarvisFold, frame, type FitLine, type FitSection } from "../lib/healthFit";
import * as S from "../lib/healthStates";
import type { St } from "../lib/healthStates";
import type { LiveSys } from "../server/live";
import type { JarvisLive, JarvisSession } from "../server/jarvis";

type J = Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
export interface HealthData { now: number; sys: LiveSys | null; jarvis: JarvisLive | null }


// ---- formatting (as the wall's -LivePage.tsx) ----
const s_ = (v: unknown) => (typeof v === "string" && v ? v : null);
const n_ = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const usd = (v: unknown, d = 2) => (typeof v === "number" ? `USD ${v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}` : "–");
const dur = (s: unknown) => {
  if (typeof s !== "number") return "–";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
};
const gib = (b: unknown) => (typeof b === "number" ? `${(b / 2 ** 30).toFixed(1)} GiB` : "–");
const plain = (v: string) => v.replace(/ · /g, ", ");
const ago = (iso: unknown, now: number) => { const t = s_(iso) ? Date.parse(s_(iso)!) : NaN; return Number.isNaN(t) ? null : now - t; };
const wakeAt = (ms: number, now: number) => {
  const d = (t: number) => new Date(t + 8 * 3600e3).toISOString();
  return (d(ms).slice(0, 10) === d(now).slice(0, 10) ? "" : d(ms).slice(8, 10) + "/" + d(ms).slice(5, 7) + " ") + hhmm(ms);
};

// ---- lines: each a fixed 14 px row with a level (lib/healthFit.ts) ----
type Line = FitLine & { key: string; node: ReactNode };
const K: Record<St, string> = { ok: "", warn: " warn", bad: " bad", blind: " blind", info: " info", ink: "" };
/** a state's marker (lib/healthStates.ts): lost a red block with a cross, caution an amber block, blind a crossed eye,
 * advisory a blue outline, working an ink block, normal none; 8 x 8 px pixel shapes */
const CROSS = "M1 1h1v1h1v1h2V2h1V1h1v1H6v1H5v2h1v1h1v1H6V6H5V5H3v1H2v1H1V6h1V5h1V3H2V2H1z";
const EYE = "M2 1h4v1H2zM1 2h1v1H1zM6 2h1v1H6zM0 3h1v2H0zM7 3h1v2H7zM3 3h2v2H3zM1 5h1v1H1zM6 5h1v1H6zM2 6h4v1H2z";
const SLASH = "M7 0h1v1H7zM6 1h1v1H6zM5 2h1v1H5zM4 3h1v1H4zM3 4h1v1H3zM2 5h1v1H2zM1 6h1v1H1zM0 7h1v1H0z";
const Mk = ({ st }: { st: St }) => st === "bad"
  ? <i className="hp-mk bad"><svg viewBox="0 0 8 8" aria-hidden="true"><path className="cut" d={CROSS} /></svg></i>
  : st === "blind" ? <i className="hp-mk blind"><svg viewBox="0 0 8 8" aria-hidden="true"><path d={EYE} /><path d={SLASH} /></svg></i>
  : <i className={"hp-mk " + st} />;
/** a check: its marker, its name, its figure on the right */
const kv = (key: string, level: Line["level"], st: St, k: ReactNode, v: ReactNode, group?: string): Line =>
  ({ key, level, group, node: <div className={"hp-l hp-kv" + K[st]}><Mk st={st} /><span className="k">{k}</span><span className="v">{v}</span></div> });
/** a line of words in its state's colour (a finding, the names behind a count) */
const ln = (key: string, level: Line["level"], st: St, t: ReactNode, group?: string): Line =>
  ({ key, level, group, node: <div className={"hp-l hp-ln" + K[st]}>{t}</div> });
/** a detail under the line above it, indented past the marker */
const sub = (key: string, level: Line["level"], t: ReactNode, group?: string, st: St = "ok"): Line =>
  ({ key, level, group, node: <div className={"hp-l hp-sub" + K[st]}>{t}</div> });

// ---- Jarvis 4 ----
const workers = (x: JarvisSession) => [x.jobs ? `${x.jobs} background ${x.jobs === 1 ? "job" : "jobs"}` : "",
  x.subagents ? `${x.subagents} ${x.subagents === 1 ? "subagent" : "subagents"}` : ""].filter(Boolean).join(", ");
const shortWorkers = (x: JarvisSession) => [x.jobs ? `${x.jobs} ${x.jobs === 1 ? "job" : "jobs"}` : "", x.subagents ? `${x.subagents} sub` : ""].filter(Boolean).join(", ");
function jRight(x: JarvisSession, now: number) {
  const age = x.since ? dur(Math.max(0, now - x.since) / 1000) : "";
  switch (x.state) {
    case "waiting": return "needs you";
    case "idle": return age ? `idle ${age}` : "idle";
    case "working": return x.jobs || x.subagents ? `working, ${shortWorkers(x)}` : "working";
    case "background": return shortWorkers(x) || "background";
    case "frozen": return x.next ? `frozen until ${wakeAt(x.next, now)}` : "frozen";
    case "scheduled": return x.next ? `next ${wakeAt(x.next, now)}` : "watching";
    default: return "booting";
  }
}
const jDetail = (x: JarvisSession, now: number) =>
  [x.since && x.state !== "background" ? `for ${dur(Math.max(0, now - x.since) / 1000)}` : "", workers(x)].filter(Boolean).join(", ");

/** the sessions' lines: each on one line, what a session needing you, working or in the background does under it
 * (level 1); the oldest idle ones folded into "+N idle" when there are more than `rows` */
function jarvisLines(j: JarvisLive, now: number, rows: number): Line[] {
  const rank = (x: JarvisSession) => JARVIS_ORDER.indexOf(x.state);
  const ss = [...j.sessions].sort((a, b) => rank(a) - rank(b) || (a.state === "idle" ? (b.since ?? 0) - (a.since ?? 0) : 0));
  const extra = Math.max(0, j.total - ss.length);  // pushed but not kept (JARVIS_KEEP): counted with the folded
  const idle = ss.filter(x => x.state === "idle");
  const fold = jarvisFold(ss.length + (extra ? 1 : 0), idle.length, rows);
  const folded = new Set(idle.slice(idle.length - fold));
  const out: Line[] = [];
  ss.forEach((x, i) => {
    if (folded.has(x)) {
      if (x === idle[idle.length - 1]) out.push(ln("jfold", 0, "ok", <span className="dim">+{fold} idle, longest {x.since ? dur((now - x.since) / 1000) : "–"}</span>));
      return;
    }
    out.push({ key: "j" + i, level: 0, node: <div className={"hp-l hp-j" + K[S.jarvis(x.state)]} data-state={x.state}><Mk st={S.jarvisMarker(x.state)} />
      <span className="n">{x.name}</span><span className="w">{jRight(x, now)}</span></div> });
    if (x.state === "waiting" || x.state === "working" || x.state === "background") {
      const d = jDetail(x, now);
      if (d) out.push(sub("jd" + i, 1, d));
    }
  });
  if (extra) out.push(ln("jextra", 0, "ok", <span className="dim">+{extra} more sessions</span>));
  return out;
}

// ---- the cluster (kv live:sys health: the news stream's Deployments, CronJobs, nodes; YYC AI's pods) ----
function clusterLines(h: J | null, ai: J): Line[] {
  if (!h) return [ln("noh", 0, "blind", "no systems data yet")];
  const out: Line[] = [];
  for (const [i, x] of ((h.stops || []) as J[]).entries())
    out.push(ln("stop" + i, 0, S.stop(), <>{x.name}: {x.tag || "requests failing"}{x.since ? `, since ${hhmm(Date.parse(x.since))}` : ""}</>));
  const cj = (h.cronjobs || {}) as J, late = (cj.late || []) as string[], failed = (cj.failed || []) as string[];
  const cjSt = S.cronjobs(late, failed);
  out.push(kv("cj", 0, cjSt, "CronJobs", late.length ? `${late.length} late` : failed.length ? `${failed.length} failed` : `${cj.n ?? "–"} on, ${cj.running ?? 0} running`));
  if (late.length || failed.length) out.push(ln("cjn", 0, cjSt, [...late.map(x => x + " late"), ...failed, `${cj.n ?? "–"} on, ${cj.running ?? 0} running`].join("; ")));
  const d = (h.deployments || {}) as J, short = (d.short || []) as string[];
  const dSt = S.deployments(short);
  out.push(kv("dep", 0, dSt, "Deployments", <><b>{d.ok ?? "–"}</b> / {d.n ?? "–"} ready</>));
  if (short.length) out.push(ln("deps", 0, dSt, "short: " + short.join(", ")));
  const nodes = Array.isArray(h.nodes) ? (h.nodes as J[]) : null;
  if (!nodes) out.push(kv("nodes", 0, S.nodes(null), "nodes", "can't read"));
  else {
    const bad = nodes.filter(n => !n.ready || n.pressure?.length || n.unschedulable);
    const nSt = S.nodes(nodes);
    out.push(kv("nodes", 0, nSt, "nodes", <><b>{nodes.length - bad.length}</b> / {nodes.length} ready</>));
    for (const n of bad) out.push(ln("nb" + n.name, 0, nSt, `${n.name}${n.ready ? "" : " not ready"}${n.pressure?.length ? " " + n.pressure.join(", ") : ""}${n.unschedulable ? " cordoned" : ""}`));
    for (const n of nodes.filter(x => !bad.includes(x))) out.push(sub("n" + n.name, 3, `${n.name} ready`, "nodes"));
  }
  const svc = (ai.service || []) as J[];
  if (ai.error && !svc.length) out.push(kv("pods", 0, "blind", "YYC AI pods", "can't read"));
  else {
    const pSt = S.pods(svc);
    out.push(kv("pods", 0, pSt, "YYC AI pods", <><b>{svc.reduce((t, x) => t + (x.ready || 0), 0)}</b> / {svc.reduce((t, x) => t + (x.desired || 0), 0)} ready</>));
    for (const x of svc) {
      const st = S.pod(x);
      const t = <>{x.name} <b>{x.ready}</b>/{x.desired}, {x.restarts ?? 0} restarts</>;
      out.push(st === "ok" ? sub("p" + x.name, 2, t, "pods") : sub("p" + x.name, 0, t, undefined, st));
    }
  }
  return out;
}

// ---- YYC AI (its operator snapshot) ----
const STATUS: Record<string, string> = { ok: "all fine", warming: "warming up", watch: "needs a look", blind: "can't see", problem: "problem" };
function aiLines(a: J | null, now: number): Line[] {
  if (!a) return [ln("noai", 0, "blind", "no YYC AI data yet")];
  if (a.error) return [kv("aierr", 0, "blind", "snapshot", "can't read"), sub("aierr2", 0, String(a.error), undefined, "blind")];
  const hl = (a.health || {}) as J, label = STATUS[hl.state] || String(hl.state || "?"), st = S.level(hl.state);
  const out: Line[] = [kv("status", 0, st, "status", label)];
  ((hl.findings || []) as J[]).slice(0, 4).forEach((f, i) => out.push(ln("f" + i, 0, S.level(f.level), f.text)));
  Object.entries((a.errors || {}) as Record<string, string>).forEach(([n, m]) => out.push(ln("e" + n, 0, "blind", `could not read ${n}: ${m}`)));
  const sbx = (a.sandboxes || []) as J[];
  out.push(kv("sbx", 0, "ok", "sandboxes", <><b>{sbx.length}</b>{a.max_active ? ` of ${a.max_active}` : ""}, {a.scaler?.available ?? "–"} free</>));
  sbx.slice(0, 4).forEach((x, i) => {
    const idle = (x.idle_s ?? 1e9) < 60 ? "active now" : `idle ${dur(x.idle_s)}`;
    const t = `${(s_(x.owner) ?? "?").split("@")[0]} ${x.host ?? "–"}, ${x.status === "starting" ? "starting" : idle}`;
    out.push(S.sandbox(x) !== "ok" ? sub("s" + i, 0, t, undefined, S.sandbox(x)) : sub("s" + i, i < 2 ? 2 : 3, t, i < 2 ? "sb1" : "sb2"));
  });
  if (sbx.length > 4) out.push(sub("smore", 3, `+${sbx.length - 4} more, ${a.scaler?.waiting ?? 0} waiting`, "sb2"));
  const b = (a.budget || {}) as J;
  out.push(kv("today", 0, "ok", "today", <><b>{usd(b.today_usd)}</b>, {b.calls_today ?? "–"} calls</>));
  const top = ((a.by_model || []) as J[])[0];
  out.push(sub("month", 1, <>month <b>{usd(b.month_usd)}</b>{top ? `; ${String(top.model).split("/").pop()} most` : ""}</>));
  out.push(kv("budget", 0, S.budget(b), b.since ? `budget since ${new Date(Date.parse(b.since) + 8 * 3600e3).toISOString().slice(5, 10)}` : "budget",
    <><b>{n_(b.spent_usd)?.toFixed(2) ?? "–"}</b> / {n_(b.limit_usd)?.toLocaleString("en-US") ?? "–"}</>));
  if (b.exhausted) out.push(ln("bex", 0, S.budget(b), "used up: model calls are refused"));
  const rec = (a.recent || {}) as J, errs = (rec.failedStarts || 0) + (rec.modelErrors || 0) + (rec.hostErrors || 0);
  const errT = [[rec.failedStarts, "start"], [rec.modelErrors, "call"], [rec.hostErrors, "host"]].filter(([n]) => n)
    .map(([n, w]) => `${n} ${w}${n === 1 ? "" : "s"}`).join(", ");
  out.push(errs ? kv("err", 0, S.errorsOver(errs), `failed, ${a.recent_minutes ?? 15} min`, errT) : kv("err", 1, "ok", `failed, ${a.recent_minutes ?? 15} min`, "none"));
  const s24 = (a.starts_24h || {}) as J;
  out.push(kv("s24", 0, "ok", "starts 24 h", <><b>{s24.n ?? "–"}</b>, {s24.failed ?? 0} failed</>));
  if (n_(s24.p50Ms) != null) out.push(sub("s24t", 1, <>median <b>{(s24.p50Ms / 1000).toFixed(1)} s</b>, p95 <b>{n_(s24.p95Ms) != null ? (s24.p95Ms / 1000).toFixed(1) + " s" : "–"}</b></>));
  void now;
  return out;
}

// ---- the providers' own figures (monitor.py provider_line) ----
function providerLines(h: J | null): Line[] {
  if (!h) return [];
  return ((h.providers || []) as J[]).flatMap((p, i) => {
    const name = (s_(p.name) || p.id || "?").split(" (")[0];
    if (p.status !== "ok") {
      const nc = p.status === "not_connected";
      return [kv("p" + i, 0, S.provider(p), name, nc ? "not connected" : "can't read"), ...(p.error ? [sub("pe" + i, 1, String(p.error), undefined, S.provider(p))] : [])];
    }
    const figs = (p.figs || []) as [string, string][];
    if (!figs.length) return [kv("p" + i, 0, "ok", name, "–")];
    const [first, ...rest] = plain(figs[0][1]).split(", ");
    const lines: Line[] = [kv("p" + i, 0, "ok", figs[0][0] === "balance" || figs[0][0] === "credit" ? name : `${name} ${figs[0][0]}`, <b>{first}</b>)];
    // the currency is named on the provider's line; its details give the figures alone
    const bare = (t: string) => (first.startsWith("USD ") ? t.replace(/USD /g, "") : t);
    const more = [...(rest.length ? [rest.join(", ")] : []), ...figs.slice(1).map(([l, v]) => `${l} ${plain(v)}`)].map(bare);
    more.forEach((t, k) => lines.push(sub(`p${i}-${k}`, 2, t, "prov" + i)));
    return lines;
  });
}

// ---- the sandbox hosts ----
function hostLines(ai: J): Line[] {
  const hosts = (ai.hosts || []) as J[];
  if (!hosts.length) return [ln("noh", 0, "ok", <span className="dim">{ai.error ? "can't read" : "no sandbox hosts"}</span>)];
  const healthy = (x: J) => !x.gateway && x.status === "active" && x.reachable;
  const out: Line[] = [];
  for (const x of hosts.filter(x => !healthy(x))) {
    const st = S.host(x);
    if (x.gateway) {
      out.push(kv("gw", 0, st, `gateway ${x.name}`, st === "ok" ? <>load <b>{x.load1 ?? "–"}</b></> : x.reachable ? x.status : "not answering"));
      out.push(sub("gwm", 2, `${x.mem_free_gib ?? "–"} of ${x.mem_total_gib ?? "–"} GiB free, ${x.cpus ?? "–"} CPU`));
    } else out.push(kv("h" + x.name, 0, st, x.name, x.status === "booting" ? "booting" : x.reachable ? x.status : "not answering"));
  }
  const ok = hosts.filter(healthy);
  if (ok.length) {
    out.push(kv("aws", 0, "ok", "AWS hosts", <><b>{ok.length}</b>, {ok.reduce((t, x) => t + (x.sandboxes || 0), 0)} / {ok.reduce((t, x) => t + (x.slots || 0), 0)} slots</>));
    ok.forEach(x => out.push(sub("hk" + x.name, 1, `${x.name} ${x.sandboxes}/${x.slots}, load ${x.load1 ?? "–"}`, "hosts")));
  }
  return out;
}

// ---- Postgres, WiseSearch, object storage (monitor.py database, wisers, storage panels) ----
function pgLines(pg: J | undefined): Line[] {
  if (!pg) return [];
  if (S.unread(pg)) return [kv("pg", 0, "blind", "Postgres", "can't read"), sub("pge", 1, String(pg.error), undefined, "blind")];
  const c = (pg.connections || {}) as J, sp = (pg.space || {}) as J, a = (pg.archive || {}) as J, lt = pg.longest_transaction as J | null;
  const probs = (a.problems || []) as string[];
  const mib = (v: unknown) => (n_(v) != null ? ((v as number) / 1024).toFixed(1) : "–");
  const dbs = (pg.databases || []) as J[];
  return [
    kv("con", 0, S.pgConnections(c), "connections", <><b>{c.total ?? "–"}</b> / {c.max ?? "–"}, <b>{pg.tps ?? "–"}</b>/s</>),
    sub("cpu", 1, `CPU ${pg.cpu_cores ?? "–"}/${pg.node_cores ?? "–"}, memory ${mib(pg.memory_mib)}/${mib(pg.memory_limit_mib)} GiB`),
    kv("free", 0, S.pgSpace(sp), "free", <><b>{gib(sp.free_bytes).replace(" GiB", "")}</b> / {gib(sp.capacity_bytes)}</>),
    sub("dbs", 2, `${dbs[0] ? `${dbs[0].name} ${gib(dbs[0].bytes)}, ` : ""}WAL ${gib(pg.wal_bytes)}`),
    kv("wal", 0, S.pgArchive(a), "WAL archive", n_(a.seconds_since_archived) != null ? <><b>{dur(a.seconds_since_archived)}</b> ago</> : "–"),
    ...probs.map((p, i) => ln("walp" + i, 0, S.pgArchive(a), p)),
    sub("walw", 1, `${a.waiting ?? "–"} waiting; base backup ${s_(a.last_backup) ? hhmm(Date.parse(a.last_backup)) : "–"}`),
    kv("ltx", 2, "ok", "longest tx", lt ? <><b>{dur(lt.seconds)}</b>, {c.waiting_on_locks ?? 0} locks</> : "none"),
  ];
}
const WISERS_STATE: Record<string, string> = { held: "logged in", logged_out: "logged out", unknown: "login unknown" };
function wsLines(w: J | undefined, st: J | undefined): Line[] {
  const out: Line[] = [];
  if (w?.error) out.push(kv("ws", 0, "blind", "WiseSearch", "can't read"));
  else if (w) {
    const probs = (w.problems || []) as string[], pr = w.probe as J | null;
    const k = S.wisers(w);
    out.push(kv("ws", 0, k, "WiseSearch", WISERS_STATE[w.state] || w.state || "–"));
    probs.forEach((p, i) => out.push(ln("wsp" + i, 0, k, p)));
    out.push(sub("wsd", 1, `check ${!pr ? "none yet" : pr.ok ? `ok ${pr.secs} s` : "failing"}, ${w.today ?? "–"} searches today`));
  }
  if (st?.error) out.push(kv("os", 0, "blind", "object storage", "can't read"));
  else if (st) {
    const bs = [...((st.buckets || []) as J[])].sort((a, b) => (b.bytes || 0) - (a.bytes || 0));
    out.push(kv("os", 0, st.refresh_error ? "blind" : "ok", "object storage", <b>{gib(st.bytes)}</b>));
    bs.filter(b => b.error).forEach(b => out.push(ln("ob" + b.name, 0, "blind", `${b.name}: can't read`)));
    const ok = bs.filter(b => !b.error);
    ok.slice(0, 2).forEach(b => out.push(sub("ob" + b.name, 2, `${b.name} ${gib(b.bytes)}`, "bk")));
    if (ok.length > 2) out.push(sub("obm", 3, `${ok.length - 2} more buckets, ${(st.objects ?? 0).toLocaleString("en-US")} objects`));
  }
  return out;
}

// ---- the page ----
type Sec = FitSection<Line> & { title: string; right?: ReactNode; blind?: boolean };

/** a column `w` px wide (the right one's includes the rule between them, its left border) */
function Column({ secs, w, h }: { secs: Sec[]; w: number; h: number }) {
  const { keep, more } = fit(secs, h);
  return (
    <div className="hp-col" style={{ width: w, height: h }}>
      {secs.map((s, i) => (
        <section key={s.key} className={"hp-sec" + (i === secs.length - 1 ? " last" : "")} data-sec={s.key}>
          <h2 className={i ? "" : "first"}><span>{s.title}</span><span className={"hr" + (s.blind ? " blind" : "")}>{s.right}</span></h2>
          <div className="hp-g">{s.lines.map((l, j) => (keep[i][j] ? <div key={l.key} data-level={l.level}>{l.node}</div> : null))}
            {more && i === secs.length - 1 ? <div className="hp-l hp-ln"><span className="dim">{more} more lines</span></div> : null}</div>
        </section>
      ))}
    </div>
  );
}

function useWindowSize() {
  const [s, setS] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    const f = () => setS([Math.floor(window.innerWidth), Math.floor(window.innerHeight)]);
    f();
    addEventListener("resize", f);
    return () => removeEventListener("resize", f);
  }, []);
  return s;
}

export function HealthPage() {
  const [d, setD] = useState<HealthData | null>(null);
  const [now, setNow] = useState(0);
  const st = useRef({ okAt: 0, skew: 0, failed: "", drawn: false });
  const [w, h] = useWindowSize();
  useEffect(() => {
    let gone = false, timer = 0;
    const read = async () => {
      const s = st.current;
      try {
        const r = await fetch("/app/health/data", { cache: "no-store", credentials: "same-origin" });
        if (!r.ok) throw new Error(r.status === 404 ? "not signed in as Deyao (404)" : "HTTP " + r.status);
        const x = JSON.parse(monoEmoji(await r.text())) as HealthData;
        if (gone) return;
        s.okAt = Date.now(); s.failed = ""; s.skew = x.now - Date.now();
        setD(x);
        if (!s.drawn) { s.drawn = true; if (window.parent !== window) window.parent.postMessage({ live: "drawn", build: BUILD_ID }, location.origin); }
      } catch (e) {
        s.failed = e instanceof Error ? e.message : String(e);
      } finally {
        if (!gone) timer = window.setTimeout(read, POLL_MS);
      }
    };
    read();
    return () => { gone = true; clearTimeout(timer); };
  }, []);
  useEffect(() => { setNow(Date.now()); const t = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { document.documentElement.classList.add("live-doc", "health-doc"); return () => document.documentElement.classList.remove("live-doc", "health-doc"); }, []);

  const s = st.current, srv = now + s.skew;  // the server's clock, for the ages of what it sent
  const stale = d && now && now - s.okAt > STALE_MS;
  if (!w || !h) return <div className="hp" data-ready="0" />;
  const [lw, rw] = columns(w);
  const sys = d?.sys ?? null, hh = (sys?.health || null) as J | null, ai = (sys?.ai || {}) as J;
  const sysOld = sys ? S.stale(ago(sys.at, srv)) === "blind" : false;
  const j = d?.jarvis ?? null, jOld = j ? S.stale(srv - j.got) === "blind" : false;

  // left: Jarvis 4 (the room the cluster's level-0 lines leave), the cluster
  const cl = clusterLines(hh, ai);
  const clRows = cl.filter(l => l.level === 0).length;
  const jRows = Math.max(1, Math.floor((h - frame(2) - ROW * clRows) / ROW));
  const jl = !d ? [ln("jl", 0, "ok", <span className="dim">Loading…</span>)] : !j ? [ln("jn", 0, "ok", <span className="dim">no report from Jarvis 4 yet</span>)]
    : !j.sessions.length ? [ln("jn", 0, "ok", <span className="dim">no session running</span>)] : jarvisLines(j, srv, jRows);
  const sysRight = (t: ReactNode) => (sysOld ? `no update since ${hhmmss(Date.parse(sys!.at))}` : t);
  const left: Sec[] = [
    { key: "jarvis", title: "Jarvis 4", right: j ? (jOld ? `no update since ${hhmmss(j.got)}` : `${j.total} sessions, ${hhmmss(j.got)}`) : "", blind: jOld, lines: jl },
    { key: "cluster", title: "Cluster", right: sysRight(""), blind: sysOld, lines: cl },
  ];
  const aiAge = ago(ai.at, srv);
  const right: Sec[] = [
    { key: "ai", title: "YYC AI", right: sysRight(aiAge != null ? `${dur(Math.max(0, aiAge) / 1000)} ago` : ""), blind: sysOld, lines: aiLines(sys ? ai : null, srv) },
    { key: "prov", title: "Providers", right: sysRight(hh?.balances_at ? hhmmss(Date.parse(hh.balances_at)) : ""), blind: sysOld, lines: providerLines(hh) },
    { key: "hosts", title: "Hosts", right: sysRight(""), blind: sysOld, lines: hostLines(ai) },
    { key: "pg", title: "Postgres", right: sysRight(""), blind: sysOld, lines: pgLines(hh?.postgres) },
    { key: "ws", title: "WiseSearch, storage", right: sysRight(""), blind: sysOld, lines: wsLines(hh?.wisers, hh?.storage) },
  ].filter(x => x.lines.length || x.key === "ai");
  return (
    <div className={"hp" + (stale ? " hp-stale" : "")} lang="en" data-ready={d ? "1" : "0"} style={{ width: w, height: h }}>
      <Column secs={left} w={lw} h={h} />
      <Column secs={right} w={rw + COL_RULE} h={h} />
      {stale && <div className="hp-note" role="status">No data since {hhmm(s.okAt)}{s.failed ? ` (${s.failed})` : ""}</div>}
    </div>
  );
}
