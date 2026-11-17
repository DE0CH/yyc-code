// Deyao's monitor: read-only, his Access identity only (server.ts answers 404 to anyone else
// before this renders), not linked from anywhere. English, its own bar (no site shell). The data is the snapshots
// monitor.py publishes into Postgres (kv monitor:data), read on the server for the first render and then every minute
// from /app/monitor/data. Each section's HTML is built by functions here (escaped strings),
// given the time "now" as a value: the server and the first browser render use the data's own fetch time, so they
// match (hydration); the browser then ticks its clock every 15 s.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { monitorQuery } from "../monitorData";
import { monitorFlagsQuery } from "../proofData";
import { dtEn } from "../lib/dates";
import { monoEmoji } from "../lib/monoEmoji";

// ---- formatting (dates and times: lib/dates.ts, HKT, the site's formats) ----
type D = Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
const esc = (s: unknown) => monoEmoji(String(s == null ? "" : s)).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
const usd = (v: unknown, d = 2) => v == null ? "–" : "USD " + Number(v).toFixed(d);
/** Number#toLocaleString("en"): thousands separators, at most 3 decimals */
export function num(v: unknown) {
  if (v == null) return "–";
  const n = Number(v);
  if (!isFinite(n)) return String(n === Infinity ? "∞" : n === -Infinity ? "-∞" : "NaN");
  const r = Math.round(Math.abs(n) * 1000) / 1000;
  let [i, f = ""] = r.toFixed(3).split(".");
  f = f.replace(/0+$/, "");
  i = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (n < 0 && r !== 0 ? "-" : "") + i + (f ? "." + f : "");
}
// bytes in IPRoyal's decimal units (1 GB = 10^9 bytes, 1 MB = 10^6 bytes): MB under 1 GB, else GB
export const bytes = (v: unknown) => v == null ? "–" : Number(v) >= 1e9 ? (Number(v) / 1e9).toFixed(2) + " GB" : (Number(v) / 1e6).toFixed(1) + " MB";
const gb = (v: unknown) => v == null ? "–" : Number(v).toFixed(3) + " GB";
const toDate = (ts: unknown) => new Date(ts as string);
/** "Wed 14 Oct 21:07" (HKT) */
function hkt(ts: unknown) {
  if (!ts) return "–";
  const d = toDate(ts); if (isNaN(+d)) return esc(ts);
  return dtEn.dateTime(d);
}
function dur(s: number) {
  if (s < 90) return Math.round(s) + " s"; if (s < 5400) return Math.round(s / 60) + " min";
  if (s < 172800) return (s / 3600).toFixed(1) + " h"; return Math.round(s / 86400) + " days";
}

interface Ctx { now: number; filter: string; query: string; open: Set<string>; W: number; wide: boolean }

function make(D: D, c: Ctx) {
  const ago = (ts: unknown) => {
    if (!ts) return "never";
    const s = (c.now - +toDate(ts)) / 1000; if (isNaN(s)) return esc(ts);
    if (s < 0) return "in " + dur(-s);
    return dur(s) + " ago";
  };
  const when = (ts: unknown) => ts ? `<span title="${esc(hkt(ts))} HKT">${ago(ts)}</span>` : "never";
  const pill = (cls: string, t: unknown) => `<span class="pill ${cls}">${esc(t)}</span>`;
  // a list of short values as wrapping chips (Deyao, 2026-10-21: "some columns are way too narrow")
  const chips = (xs: string[]) => xs.length ? `<span class="chips">${xs.map(x => `<span class="chip">${x}</span>`).join("")}</span>` : "–";
  /** a long status text as a one-line summary (up to its first ": ") with the detail on its own line under it */
  const brief = (t: unknown, cls = "") => {
    const s = String(t ?? ""); const i = s.indexOf(": ");
    if (s.length <= 70 || i < 1 || i > 60) return `<span class="${cls}">${esc(s)}</span>`;
    return `<span class="${cls}">${esc(s.slice(0, i))}</span><span class="det">${esc(s.slice(i + 2))}</span>`;
  };
  const hm = (ts: unknown) => {  // "21:07 HKT", the day too when it is not today
    const d = toDate(ts); if (!ts || isNaN(+d)) return esc(ts || "–");
    return dtEn.when(d, c.now) + " HKT";
  };
  const stopOf = (id: string) => ((D.stops || {}).providers || []).find((s: D) => s.id === id);
  const hmd = (ts: unknown) => {  // "Wed 14 Oct 21:07 HKT"
    const d = toDate(ts); if (!ts || isNaN(+d)) return esc(ts || "–");
    return dtEn.dateTime(d, c.now) + " HKT";
  };
  const stopLine = (s: D) => `${esc(s.tag)} — requests failing${s.error_text ? ` with '${esc(s.error_text)}'` : ""} since ${hmd(s.since)}`;

  function stops() {
    const st = (D.stops || {}).providers || [];
    return (D.stops || {}).error ? `<p class="blind">can't read: ${esc(D.stops.error)}</p>` : st.map((s: D) =>
      `<div class="stop" role="status"><b>${esc(s.name)}: ${stopLine(s)}</b><div class="small">${s.last_failed ? "last failed request " + ago(s.last_failed) + ", " : ""}${s.notified ? "Deyao told on Discord, " : ""}it said: ${esc(s.message || "")}</div></div>`).join("");
  }

  function tiles() {
    const s = D.sources || {}, cn = s.counts || {}, j = D.jobs || {}, p = D.pipeline || {}, sp = D.spending || {};
    // red: a service lost now (a CronJob with no success for three intervals, a Deployment with no pod ready); amber: one
    // failed run, a Deployment short of pods that still serves (Deyao, 2026-11-17)
    const cjs = (j.cronjobs || []).filter((x: D) => !x.suspended);
    const bad = cjs.filter((x: D) => x.last_finished === "failed" || x.late).length;
    const deps = (j.deployments || []).filter((d: D) => d.ready < (d.replicas || 0)).length;
    const down = cjs.some((x: D) => x.late) || (j.deployments || []).some((d: D) => (d.replicas || 0) > 0 && !d.ready);
    const or = ((D.balances || {}).providers || []).find((b: D) => b.id === "openrouter") || {};
    const sc = p.scoring || {};
    const win = (h: number) => (sp.windows || []).find((w: D) => w.hours === h);
    const w24 = win(24), w6 = win(6);
    const t: [string, string, unknown, string][] = [
      ["", "Sources failing", cn.failing == null ? "–" : `${cn.failing} / ${cn.total}`, `${cn.quiet || 0} quiet, last poll ${s.last_poll ? ago(s.last_poll) : "–"}`],
      [down ? "bad" : bad || deps ? "warn" : j.error ? "blind" : "", "Jobs failing or late", j.error ? "?" : bad, `${deps} deployment(s) not fully ready`],
      [(sc.awaiting || 0) > 100 ? "warn" : "", "Posts awaiting score", num(sc.awaiting), (sc.oldest_waiting ? "oldest " + ago(sc.oldest_waiting) : "live scoring queue") + (sc.first_run ? `, ${num(sc.first_run)} more from new sources' first reads (數據分析 only)` : "")],
      [stopOf("openrouter") ? "bad" : "", "OpenRouter this month", or.status === "ok" ? usd(or.usage_month_usd) : "–", stopOf("openrouter") ? stopLine(stopOf("openrouter")) : or.status === "ok" ? `${usd(or.usage_today_usd)} today (UTC), key yyc-stream` : esc(or.error || or.needs || "")],
      ["", "Spent, last 24 h", w24 ? usd(w24.total_usd) : "–", `${w6 ? usd(w6.total_usd / 6, 4) + "/h over the last 6 h, " : ""}${usd(sp.total_mtd_usd)} this month`],
      [(p.fulltext || {}).waiting_recent_2d > 500 ? "warn" : "", "Full text waiting", num((p.fulltext || {}).waiting_recent_2d), `from the last 2 days, ${num((p.fulltext || {}).waiting_all)} in all`],
      [p.ai_limit && !(p.transcribe || {}).off ? "bad" : "", "Clips to transcribe", num((p.transcribe || {}).queued), (p.transcribe || {}).off ? "off: " + esc(p.transcribe.off) + ", they wait in the queue" : p.ai_limit ? "stopped: OpenRouter " + esc((stopOf("openrouter") || {}).tag || "requests failing") : `${num((p.transcribe || {}).minutes_24h)} min transcribed in 24 h, ${usd((p.transcribe || {}).usd_today, 4)} today`],
    ];
    return t.map(([cls, k, v, s2]) => `<div class="tile ${cls}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${s2}</div></div>`).join("");
  }

  function balances() {
    const b = D.balances || {};
    if (b.error) return `<p class="blind">can't read: ${esc(b.error)}</p>`;
    return (b.providers || []).map((x: D) => {
      const stop = stopOf(x.id);
      const head = (main?: string) => `<h3>${esc(x.name)} ${stop ? pill("bad", stop.tag) : x.status === "ok" ? pill("ok", "connected") : x.status === "error" ? pill("blind", "can't read") : pill("off", "not connected")}</h3>${stop ? `<p class="stop">${stopLine(stop)}</p>` : ""}${main || ""}`;
      if (x.status === "not_connected") return `<div class="card">${head()}<div class="small muted">Needs: ${esc(x.needs)}</div></div>`;
      if (x.status === "error") return `<div class="card">${head()}<p class="blind">can't read: ${esc(x.error)}</p></div>`;
      let body = "";
      if (x.id === "openrouter") {
        const credit = x.credit_balance_usd != null
          ? `<div class="big">${usd(x.credit_balance_usd)} <span class="small muted">credit balance (whole account)</span></div>
        <dl class="kv"><dt>Bought</dt><dd>${usd(x.credits_bought_usd)}</dd><dt>Used</dt><dd>${usd(x.credits_used_usd)}</dd></dl>`
          : `<div class="small muted">Credit balance needs: ${esc(x.credit_needs || "")}</div>`;
        body = `${credit}<dl class="kv"><dt>Key yyc-stream</dt><dd>${usd(x.usage_month_usd, 4)} this month (UTC)</dd><dt>Today (UTC)</dt><dd>${usd(x.usage_today_usd, 4)}</dd>
        <dt>Last 24 h</dt><dd>USD ${x.rate_usd_per_hour}/h <span class="muted">(${esc(x.rate_basis)})</span></dd></dl>`;
      } else if (x.id === "tikhub") {
        body = `<div class="big">${usd(x.balance_usd, 3)}</div><dl class="kv"><dt>Account</dt><dd>${esc(x.account)}</dd>
        <dt>Free credit</dt><dd>${usd(x.free_credit_usd, 3)}</dd><dt>Today</dt><dd>${usd(x.usage_today_usd, 3)}, ${num(x.requests_today)} requests <span class="muted">(${esc(x.usage_today_date)}, ${esc(x.usage_today_tz)})</span></dd></dl>`;
      } else if (x.id === "anyapi" || x.id === "capsolver" || x.id === "2captcha") {
        body = `<div class="big">${usd(x.balance_usd)}</div><dl class="kv"><dt>Account</dt><dd>${esc(x.account)}</dd></dl>`;
      } else if (x.id === "iproyal") {
        body = `<div class="big">${gb(x.left_gb)} left</div><dl class="kv"><dt>Sub-user</dt><dd>${(x.subusers || []).map((s: D) => `${esc(s.username)}: ${gb(s.traffic_used)} used, ${gb(s.traffic_available)} left`).join("<br>")}</dd>
        <dt>By site</dt><dd>${x.by_site && !x.by_site.error ? (() => {
          const ss = Object.entries(x.by_site as Record<string, D>).sort((p, q) => q[1].today_mb - p[1].today_mb || q[1].month_mb - p[1].month_mb);
          const one = ([k, v]: [string, D]) => `<span>${esc(k)}: ${Number(v.today_mb).toFixed(1)} MB today, ${Number(v.month_mb).toFixed(1)} MB this month</span>`;
          return `<div class="cols">${ss.slice(0, 12).map(one).join("") || "–"}</div>${ss.length > 12 ? `<details class="inl"><summary class="small">${ss.length - 12} more sites</summary><div class="cols">${ss.slice(12).map(one).join("")}</div></details>` : ""}`;
        })() : esc((x.by_site || {}).error || "–")} <span class="det">${esc(x.by_site_source)}</span></dd>
        ${x.isp ? `<dt>ISP IP</dt><dd>${esc(x.isp.ip)} (order #${esc(x.isp.order)}, YouTube only): ${gb(x.isp.today_bytes / 1e9)} today, ${gb(x.isp.last30_bytes / 1e9)} in the last 30 days of its ${gb(x.isp.allowance_bytes / 1e9)} Fair Usage allowance (${Math.round(x.isp.last30_bytes / x.isp.allowance_bytes * 100)}%)${x.isp.expire_date ? `, paid to ${esc(String(x.isp.expire_date).slice(0, 10))}, auto-renew ${x.isp.auto_renew ? "on" : "off"}` : ""} <span class="muted">(${esc(x.isp.basis)})</span></dd>` : ""}
        <dt>Unallocated</dt><dd>${gb(x.unallocated_gb)} <span class="muted">(whole account)</span></dd>
        <dt>Price</dt><dd>${usd(x.price_per_gb_usd, 4)} per GB <span class="muted">(${esc(x.price_basis)})</span></dd></dl>`;
      } else if (x.id === "hetzner") {
        body = `<div class="big">${esc(x.currency)} ${Number(x.list_monthly_net).toFixed(2)} / month</div><dl class="kv">${(x.servers || []).map((s: D) => `<dt>${esc(s.name)}</dt><dd>${esc(s.type)} ${esc(s.location)} ${esc(s.status)}, ${esc(x.currency)} ${s.monthly_net}${s.backups_net ? " + backups " + s.backups_net : ""}</dd>`).join("")}<dt>IPv4</dt><dd>${x.ipv4}</dd></dl>`;
      } else if (x.id === "aws") {
        const svc = (l: D[]) => (l || []).map((v: D) => `${esc(v.name)} ${usd(v.usd, 4)}`).join("<br>") || "–";
        const est = (x.estimated_days || []).length;
        body = `<div class="big">${usd(x.month_usd)} <span class="small muted">billed this month (${esc(x.month)}, UTC)</span></div><dl class="kv"><dt>Account</dt><dd>${esc(x.account)}</dd>
        <dt>Yesterday</dt><dd>${usd(x.yesterday_usd ?? 0, 4)} <span class="muted">(${esc(x.yesterday)}, UTC)</span></dd>
        <dt>Today so far</dt><dd>${x.today_usd != null ? usd(x.today_usd, 4) : "–"} <span class="muted">(as far as AWS has billed it)</span></dd>
        <dt>This month by service</dt><dd>${svc(x.services_month)}</dd>
        <dt>Instances now</dt><dd>${(x.instances || []).map((i: D) => `${esc(i.name || i.id)}: ${esc(i.type)} ${esc(i.market)}, ${esc(i.state)} since ${hkt(i.launched)} <span class="muted">(${esc(i.region)})</span>`).join("<br>") || "none"}</dd>
        <dt>Cost figures read</dt><dd>${hkt(x.costs_read)}${est ? ` <span class="muted">(AWS's estimates until the month's invoice)</span>` : ""}${x.costs_error ? `<br><span class="blind">the last read failed: ${esc(x.costs_error)}</span>` : ""}</dd></dl>`;
      } else if (x.id === "cloudflare") {
        const pct = (u: D) => u.included ? ` (${Math.round(u.used / u.included * 100)}%)` : "";
        body = `<div class="big">${usd(x.subscriptions_monthly_usd)} / month <span class="small muted">${esc(x.plan || "")}</span></div><dl class="kv"><dt>Account</dt><dd>${esc(x.account)}</dd>
        <dt>Subscriptions</dt><dd>${(x.subscriptions || []).map((s: D) => `${esc(s.name)}: ${usd(s.price_usd)} ${esc(s.frequency)}, ${esc(s.state)}`).join("<br>")}</dd>
        <dt>Billing period</dt><dd>${hkt(x.period_start)} – ${hkt(x.period_end)} <span class="muted">(usage to ${hkt(x.usage_to)})</span></dd>
        ${(x.usage || []).map((u: D) => `<dt>${esc(u.name)}</dt><dd>${num(Math.round(u.used))} of ${num(u.included)} ${esc(u.unit)} included${pct(u)}${u.overage_usd > 0 ? `, beyond: ${usd(u.overage_usd, 4)}` : ""}</dd>`).join("")}
        <dt>Beyond included</dt><dd>${usd(x.overage_usd, 4)} this period <span class="muted">(list prices)</span></dd>
        <dt>By Worker</dt><dd>${Object.entries((x.by_script || {}) as Record<string, D>).map(([k, v]) => `${esc(k)}: ${num(v.requests)} requests, ${num(Math.round(v.cpu_ms))} CPU ms`).join("<br>") || "–"}</dd></dl><div class="src">${esc(x.price_basis)}</div>`;
      } else if (x.id === "exa") {
        body = `<div class="big">${usd(x.exa_charged_month_usd, 4)} <span class="small muted">charged by Exa this month (UTC)</span></div><dl class="kv"><dt>Account</dt><dd>${esc(x.account)}</dd>
        <dt>Calls</dt><dd>${num(x.calls_month)} this month, ${num(x.calls_today)} today (${usd(x.exa_charged_today_usd, 4)})</dd>
        <dt>Plan</dt><dd>${esc(String(x.plan || "").split(":")[0])}</dd></dl><div class="src">Plan: ${esc(x.plan)}. Balance: ${esc(x.usage_api)}</div>`;
      }
      return `<div class="card${x.id === "iproyal" || x.id === "cloudflare" || x.id === "aws" ? " wide" : ""}">${head(body)}<div class="src">Source: ${esc(x.source)}</div></div>`;
    }).join("");
  }

  // one hue per provider (house.css --p-*; the chart's legend names them). Never red, amber or green: those are status
  const COLORS: Record<string, string> = { openrouter: "var(--p-1)", social: "var(--p-2)", tikhub: "var(--p-2)", anyapi: "var(--p-3)", iproyal: "var(--p-4)", solvers: "var(--p-5)", hetzner: "var(--p-6)", aws: "var(--p-6)" /* servers, as Hetzner: the chart has no Hetzner series */, cloudflare: "var(--ink)", exa: "var(--muted)" };
  /** one bar row (Deyao, 2026-10-21: every spend as a bar, the bar = the last 24 h, rows ordered by it, one scale per
   *  section): the label (and a muted detail), the bar, the 24 h figure and the month's under it */
  const pct = (v: number, max: number) => max > 0 ? Math.max(0, Math.min(100, 100 * v / max)) : 0;
  const barRow = (o: { label: string; sub?: string; v: number; max: number; a: string; b?: string; color?: string; cls?: string }) =>
    `<div class="sbar${o.cls ? " " + o.cls : ""}"><div class="l"><span class="nm">${o.label}</span>${o.sub ? ` <span class="sub">${o.sub}</span>` : ""}</div><div class="track"><i style="width:${pct(o.v, o.max).toFixed(2)}%${o.color ? ";background:" + o.color : ""}"></i></div><div class="n"><span class="a">${o.a}</span>${o.b ? `<span class="b">${o.b}</span>` : ""}</div></div>`;
  /** a bar row that opens (its parts behind a tap; kept open across redraws: c.open) */
  const barDet = (id: string, row: string, body: string) => `<details class="sbx" data-id="${esc(id)}"${c.open.has(id) ? " open" : ""}><summary>${row}</summary><div class="sbin">${body}</div></details>`;
  const h3 = (t: string, sub = "") => `<h3 class="sh">${t}${sub ? ` <span class="small muted">${sub}</span>` : ""}</h3>`;
  const u4 = (v: unknown) => v == null ? "–" : usd(v, 4);
  // rows with no spend in the last 24 h: one collapsed line under the section, not a run of empty bars
  const split = <T,>(rows: T[], v: (r: T) => number) => [rows.filter(r => v(r) > 0), rows.filter(r => !(v(r) > 0))];
  const rest = (rows: string[], what: string) => rows.length ? `<details class="days"><summary>${rows.length} ${what} with no spend in the last 24 h</summary>${rows.join("")}</details>` : "";

  function spending() {
    const sp = D.spending || {};
    if (sp.error) return { html: `<p class="blind">can't read: ${esc(sp.error)}</p>`, month: "", days: [] as string[], series: [] as D[], totals: [] as number[] };
    const month = `${sp.month || ""}, ${sp.note || ""}`;
    const days: string[] = sp.days || [], series: D[] = (sp.series || []).filter((s: D) => s.by_day);
    const totals = days.map(d => series.reduce((a, s) => a + (s.by_day[d] || 0), 0));
    const max = Math.max(0.01, ...totals);
    const W = Math.max(280, Math.round(c.W || 600)), H = c.wide ? 240 : 200, L = 40, R = 14, B = 18, bw = (W - L - R) / days.length;
    const step = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20].find(s => max / s <= 4) || 50;
    let g = "";
    for (let v = 0; v <= max + 1e-9; v += step) {
      const y = H - B - (H - B - 8) * v / max;
      g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="var(--rule)" stroke-width="1"/><text x="${L - 4}" y="${y + 4}" text-anchor="end" font-size="12" fill="var(--muted)">${v.toFixed(step < 0.1 ? 2 : step < 1 ? 1 : 0)}</text>`;
    }
    days.forEach((d, i) => {
      let y = H - B; const x = L + i * bw + 1;
      series.forEach(s => {
        const v = s.by_day[d] || 0; if (!v) return;
        const h = (H - B - 8) * v / max;
        g += `<rect x="${x}" y="${y - h + 1}" width="${Math.max(bw - 2, 1)}" height="${Math.max(h - 1, 0.5)}" fill="${COLORS[s.id] || "var(--muted)"}"/>`;
        y -= h;
      });
      if (i % 5 === 4 || i === days.length - 1) g += `<text x="${x + bw / 2}" y="${H - 4}" text-anchor="middle" font-size="12" fill="var(--muted)">${dtEn.axis(d)}</text>`;
      g += `<rect class="hit" data-i="${i}" x="${L + i * bw}" y="0" width="${bw}" height="${H - B}" fill="transparent"/>`;
    });
    const legend = series.map(s => `<span><i style="background:${COLORS[s.id] || "var(--muted)"}"></i>${esc(s.name)}</span>`).join("");
    // now: the last 6 h and 24 h
    const wins: D[] = sp.windows || [];
    const win = (h: number) => wins.find(w => w.hours === h);
    const w6 = win(6), w24 = win(24);
    const nowTiles = `<div class="tiles sptiles">${[[w6, 6], [w24, 24]].map(([w, h]) => `<div class="tile"><div class="k">Last ${h} h</div><div class="v">${w ? usd((w as D).total_usd) : "–"}</div><div class="s">${w ? usd((w as D).total_usd / (h as number), 4) + "/h" : "not computed yet"}</div></div>`).join("")}<div class="tile"><div class="k">This month (HKT)</div><div class="v">${usd(sp.total_mtd_usd)}</div><div class="s">history below</div></div></div>`;
    const comp24: D[] = (w24 || {}).components || [];
    const partial = comp24.filter(x => x.cost_note).map(x => x.name.split(" (")[0]);
    const notes = partial.length ? `<p class="note">${esc(comp24.filter(x => x.cost_note).map(x => x.name.split(" (")[0] + ": " + x.cost_note).join("; "))}</p>` : "";
    const bars: D = sp.bars || {};
    const provs: D[] = bars.providers || [], allParts: D[] = bars.items || [];
    // turned off (Deyao, 2026-10-21): parts whose job or model no longer runs stay out of every ranking (bars.off_rule)
    const items = allParts.filter(r => r.live !== false), offParts = allParts.filter(r => r.live === false);
    const jobLive = (j: string) => ((bars.jobs || {})[j] || {}).live !== false;
    const jobWhy = (j: string) => ((bars.jobs || {})[j] || {}).reason || "";
    const pname = (id: string) => ((provs.find(p => p.id === id) || {}).name || id).split(" (")[0];
    // a provider's or part's month text: USD so far this month, or its monthly price (Hetzner, Cloudflare)
    const monthTxt = (r: D) => r.month_text ? esc(r.month_text) : r.month_usd == null ? "month –" : "month " + u4(r.month_usd);
    const h24Txt = (r: D) => r.h24_usd == null ? (r.h24_eur != null ? "24 h EUR " + Number(r.h24_eur).toFixed(4) : "24 h –") :
      "24 h " + u4(r.h24_usd) + (r.h24_eur != null ? ` <span class="muted">(EUR ${Number(r.h24_eur).toFixed(4)})</span>` : "");
    // 1. by provider, one scale
    const pmax = Math.max(0, ...provs.map(p => p.h24_usd || 0));
    const SRC: Record<string, string> = { provider: "the provider's own figure", "our log": "our log", "provider list prices": "list prices", "provider per call": "the provider's cost per call" };
    const provRows = provs.map(p => barRow({ label: esc(p.name), sub: esc(SRC[p.source] || p.source || "") + (p.note ? ", " + esc(p.note) : ""),
      v: p.h24_usd || 0, max: pmax, a: h24Txt(p), b: monthTxt(p) + ", 6 h " + u4(p.h6_usd), color: COLORS[p.id] })).join("");
    const provSrc = provs.filter(p => p.basis).map(p => `<li><b>${esc(pname(p.id))}</b>: ${esc(p.basis)}</li>`).join("");
    const offSum = (k: string) => provs.reduce((a, p) => a + (p[k] || 0), 0);
    const byProvider = `<div class="blk" id="spend-providers">${h3("All spending by provider, last 24 h", "running parts only; bar = the last 24 h, biggest first")}${provRows || '<p class="muted small">No spend in the last 24 h.</p>'}
      ${offParts.length ? `<p class="note">Not in this ranking: turned-off parts, 24 h ${u4(offSum("off_h24_usd"))}, month ${u4(offSum("off_month_usd"))} (the "Turned off" list).</p>` : ""}
      ${provSrc ? `<details class="days"><summary>Where each figure comes from</summary><ul class="small srcs">${provSrc}</ul><p class="small muted">${esc(bars.month_basis || "")}</p></details>` : ""}</div>`;
    // 2. every part of every provider, one scale: the biggest single items overall
    const [it24, it0] = split(items, r => r.h24_usd || 0);
    const imax = Math.max(0, ...it24.map(r => r.h24_usd || 0));
    const itemRow = (r: D) => barRow({ label: esc(r.name), sub: esc(pname(r.id)), v: r.h24_usd || 0, max: imax, a: h24Txt(r), b: monthTxt(r), color: COLORS[r.id] });
    const TOP = 25;
    const allItems = `<div class="blk" id="spend-items">${h3("All spending, every item, last 24 h", "every provider's parts on one scale")}
      ${it24.slice(0, TOP).map(itemRow).join("") || '<p class="muted small">No spend in the last 24 h.</p>'}
      ${it24.length > TOP ? `<details class="days"><summary>${it24.length - TOP} more items with spend in the last 24 h</summary>${it24.slice(TOP).map(itemRow).join("")}</details>` : ""}
      ${rest(it0.map(itemRow), "items")}
      <p class="note">${esc(bars.basis || "")}. Parts: AI purpose × model, IPRoyal site (job), TikHub / AnyAPI job × endpoint, captcha solver × site, Hetzner resource, AWS service.${bars.eur_usd ? ` EUR → USD ${esc(String(bars.eur_usd))} (${esc(bars.eur_usd_basis || "")}).` : bars.eur_usd_basis ? " " + esc(bars.eur_usd_basis) + "." : ""}</p></div>`;
    // 3. per job (Deyao, 2026-10-16): IPRoyal traffic in MB/GB only, TikHub / AnyAPI / OpenRouter USD; each job a bar
    // (the last 24 h), its sites / endpoints behind a tap on the same scale; 6 h, 24 h and this month as text
    const bd: D[] = sp.breakdown || [];
    const wk = ["6h", "24h", "month"], wl = ["6 h", "24 h", "Month"];
    const at = (k: string, part: string) => (bd.find(w => w.key === k) || {})[part] || {};
    const jobsOf = (part: string) => {
      const seen = new Map<string, D[]>();
      wk.forEach((k, i) => (at(k, part).by_job || []).forEach((j: D) => { if (!seen.has(j.job)) seen.set(j.job, [{}, {}, {}]); seen.get(j.job)![i] = j; }));
      const v = (r: D[], i: number) => +(r[i].bytes ?? r[i].usd ?? 0);
      return [...seen.entries()].sort((a, b) => v(b[1], 1) - v(a[1], 1) || v(b[1], 2) - v(a[1], 2) || a[0].localeCompare(b[0]));
    };
    const f4 = (v: unknown) => v == null ? "–" : Number(v).toFixed(4);  // USD, the panel's title names the currency
    const hkHour = (ts: string) => new Date(Date.parse(ts) + 8 * 3600e3).toISOString().slice(0, 13);  // the HKT hour key
    const grid = (label: string, vals: string[], cls = "") => `<span class="${cls}">${label}</span>${vals.map(v => `<span class="n">${v}</span>`).join("")}`;
    const head = grid("", wl, "h").replace(/<span class="n">/g, '<span class="n h">');
    const cov = (w: D) => (w.provider_usd != null || w.provider_bytes != null) && (w.provider_coverage ?? 0) < 0.95;
    const provVal = (w: D, f: (v: unknown) => string, key: string) => w[key] == null ? "–" : f(w[key]) + (cov(w) ? "*" : "");
    const anyPartial = (key: string) => wk.some(k => cov(at(k, key)));
    const three = (vals: string[]) => ({ a: "24 h " + vals[1], b: "6 h " + vals[0] + ", month " + vals[2] });
    // a per-job panel's turned-off jobs: their own list under the ranking, each with why (bars.jobs)
    const offJobs = (off: [string, D[]][], row: (job: string, r: D[]) => string) => off.length ? `<details class="days off" data-id="offjobs:${off.length}"><summary>Turned off: ${off.length} jobs (not in the ranking)</summary>${off.map(([j, r]) => row(j, r) + `<div class="sbmeta">${esc(j)}: ${esc(jobWhy(j))}</div>`).join("")}</details>` : "";
    const bdProxy = () => {
      const jobs = jobsOf("proxy");
      const jmax = Math.max(0, ...jobs.map(([, r]) => +(r[1].bytes || 0)));
      const row = (job: string, r: D[]) => {
        const sites = new Map<string, D[]>();
        r.forEach((x, i) => (x.sites || []).forEach((s: D) => { if (!sites.has(s.site)) sites.set(s.site, [{}, {}, {}]); sites.get(s.site)![i] = s; }));
        const body = [...sites.entries()].sort((a, b) => +(b[1][1].bytes || 0) - +(a[1][1].bytes || 0) || +(b[1][2].bytes || 0) - +(a[1][2].bytes || 0))
          .map(([site, v]) => barRow({ label: esc(site), v: +(v[1].bytes || 0), max: jmax, color: COLORS.iproyal, cls: "in", ...three(v.map(x => x.bytes == null ? "–" : bytes(x.bytes))) })).join("");
        return barDet("bd:proxy:" + job, barRow({ label: esc(job), v: +(r[1].bytes || 0), max: jmax, color: COLORS.iproyal, ...three(r.map(x => x.bytes == null ? "–" : bytes(x.bytes))) }), body);
      };
      const [busy, idle] = split(jobs.filter(([j]) => jobLive(j)), ([, r]) => +(r[1].bytes || 0));
      const off = jobs.filter(([j]) => !jobLive(j));
      const ours = wk.map(k => bytes(at(k, "proxy").bytes)), prov = wk.map(k => provVal(at(k, "proxy"), bytes, "provider_bytes"));
      const gap = wk.map(k => { const w = at(k, "proxy"); return w.provider_bytes == null || w.bytes == null ? "–" : (w.provider_bytes - w.bytes < 0 ? "−" : "+") + bytes(Math.abs(w.provider_bytes - w.bytes)); });
      return `<div class="bd blk" id="proxy-traffic">${h3("IPRoyal traffic per job", "(sub-user yycstream; data volume, not money)")}
      <p class="note" style="margin-top:0">Bytes (not bits), GB as IPRoyal counts it: 1 GB = 1,000,000,000 bytes, 1 MB = 1,000,000 bytes.</p>
      <div class="bdg tot">${head}${grid("IPRoyal's own figure", prov, "b")}${grid("Our count", ours)}${grid("IPRoyal minus ours", gap, "muted")}</div>
      <div class="bdl">${busy.map(([job, r]) => row(job, r)).join("") || '<p class="muted small">No proxied traffic counted in the last 24 h.</p>'}${idle.length ? `<details class="days"><summary>${idle.length} jobs with no traffic in the last 24 h</summary>${idle.map(([job, r]) => row(job, r)).join("")}</details>` : ""}${offJobs(off, row)}</div>
      <p class="note">Tap a job for its sites. Bar and order: the last 24 h. IPRoyal's own figure: ${esc(at("24h", "proxy").provider_basis || "")}; this month: ${esc(at("month", "proxy").provider_basis || "")}.${anyPartial("proxy") ? " * its snapshots cover under 95% of that window." : ""} Our count: ${esc(at("24h", "proxy").basis || "")}.${(() => { const w = at("month", "proxy"), st = (bd.find(x => x.key === "month") || {}).start; return w.counted_from && st && w.counted_from > hkHour(st) ? ` Our hourly count begins ${esc(dtEn.dateTime(w.counted_from + ":00", c.now))} HKT, so this month's gap includes the traffic before it.` : ""; })()}</p></div>`;
    };
    const failTxt = (st: D) => Object.entries(st || {}).map(([k, v]) => `${esc(k)} × ${num(v)}`).join(", ");
    const bdCalls = (part: string, id: string, title: string, provName: string) => {
      const jobs = jobsOf(part);
      const jmax = Math.max(0, ...jobs.map(([, r]) => +(r[1].usd || 0)));
      const color = COLORS[part];
      const row = (job: string, r: D[]) => {
        const d = r[1].job ? r[1] : r[2].job ? r[2] : r[0];
        const meta = `${r[1].job ? "24 h" : r[2].job ? "month" : "6 h"}: ${num(d.calls)} calls${d.failed_calls ? `, ${num(d.failed_calls)} failed` : ""}`;
        const eps = new Map<string, D[]>();
        r.forEach((x, i) => (x.endpoints || []).forEach((e: D) => { if (!eps.has(e.endpoint)) eps.set(e.endpoint, [{}, {}, {}]); eps.get(e.endpoint)![i] = e; }));
        const body = [...eps.entries()].sort((a, b) => +(b[1][1].usd || 0) - +(a[1][1].usd || 0) || +(b[1][2].usd || 0) - +(a[1][2].usd || 0)).map(([ep, v]) => {
          const e = v[1].endpoint ? v[1] : v[2].endpoint ? v[2] : v[0];
          return barRow({ label: esc(ep), sub: `${esc(e.platform)}, ${num(e.calls)} calls${e.unit_usd != null ? " × USD " + Number(e.unit_usd).toFixed(4) : ""}${e.failed_calls ? `, ${num(e.failed_calls)} failed (HTTP ${failTxt(e.failed_statuses)}, not charged)` : ""}`,
            v: +(v[1].usd || 0), max: jmax, color, cls: "in", ...three(v.map(x => x.endpoint ? f4(x.usd) : "–")) });
        }).join("");
        return barDet(`bd:${part}:${job}`, barRow({ label: esc(job), sub: meta, v: +(r[1].usd || 0), max: jmax, color, ...three(r.map(x => x.job ? f4(x.usd) : "–")) }), body);
      };
      const [busy, idle] = split(jobs.filter(([j]) => jobLive(j)), ([, r]) => +(r[1].usd || 0));
      const off = jobs.filter(([j]) => !jobLive(j));
      const ours = wk.map(k => f4(at(k, part).usd)), prov = wk.map(k => provVal(at(k, part), f4, "provider_usd"));
      const calls = wk.map(k => num(at(k, part).calls)), failed = wk.map(k => num(at(k, part).failed_calls));
      return `<div class="bd blk" id="${id}">${h3(title)}
      <div class="bdg tot">${head}${provName ? grid(provName, prov, "b") : ""}${grid("Our log", ours)}${grid("Calls", calls, "muted")}${grid("Failed calls", failed, "muted")}</div>
      <div class="bdl">${busy.map(([job, r]) => row(job, r)).join("") || '<p class="muted small">No calls logged in the last 24 h.</p>'}${idle.length ? `<details class="days"><summary>${idle.length} jobs with no spend in the last 24 h</summary>${idle.map(([job, r]) => row(job, r)).join("")}</details>` : ""}${offJobs(off, row)}</div>
      <p class="note">Amounts in USD. Tap a job for its endpoints. Bar and order: the last 24 h. Failed calls (an HTTP error, a 402 refusal) are not charged.${provName ? ` ${provName}: ${esc(at("24h", part).provider_basis || "")}.` : ""}${anyPartial(part) ? " * the provider's snapshots cover under 95% of that window (they began 16 Oct)." : ""} Our log: ${esc(at("24h", part).basis || "")}.</p></div>`;
    };
    const bdOr = () => {
      const jobs = jobsOf("openrouter");
      const jmax = Math.max(0, ...jobs.map(([, r]) => +(r[1].usd || 0)));
      const row = (job: string, r: D[]) => barRow({ label: esc(job), v: +(r[1].usd || 0), max: jmax, color: COLORS.openrouter, ...three(r.map(x => x.job ? f4(x.usd) : "–")) });
      const [busy, idle] = split(jobs.filter(([j]) => jobLive(j)), ([, r]) => +(r[1].usd || 0));
      const off = jobs.filter(([j]) => !jobLive(j));
      return `<div class="bd blk" id="openrouter-jobs">${h3("OpenRouter per job", "(USD; our log: OpenRouter's charged cost per call)")}
      <div class="bdg tot">${head}${grid("Our log", wk.map(k => f4(at(k, "openrouter").usd)))}</div><div class="bdl">${busy.map(([j, r]) => row(j, r)).join("")}${rest(idle.map(([j, r]) => row(j, r)), "jobs")}${offJobs(off, row)}</div></div>`;
    };
    const perJob = bd.length ? `${bdProxy()}${bdCalls("tikhub", "tikhub-calls", 'TikHub per job <span class="small muted">(USD)</span>', "TikHub's own figure")}${bdCalls("anyapi", "anyapi-calls", 'AnyAPI per job <span class="small muted">(USD)</span>', "AnyAPI's own figure")}${bdOr()}` : "";
    // 4. AI: the model in use per purpose and its rate now (bar = the rate, which covers at most the last 24 h)
    const models: D[] = sp.models || [];
    const livePurpose = (p: string) => !bars.items || items.some(r => r.id === "openrouter" && r.purpose === p);
    const offModels = models.filter(m => !livePurpose(m.purpose)), runModels = models.filter(m => livePurpose(m.purpose));
    const busy = runModels.filter(m => m.rate_usd_per_hour > 0), idle = runModels.filter(m => !(m.rate_usd_per_hour > 0));
    const rmax = Math.max(0, ...busy.map(m => m.rate_usd_per_hour));
    const modelRow = (m: D) => barRow({ label: esc(m.purpose), sub: esc(m.model), v: m.rate_usd_per_hour || 0, max: rmax, color: COLORS.openrouter,
      a: `USD ${Number(m.rate_usd_per_hour).toFixed(4)}/h`, b: `over the last ${Number(m.rate_hours).toFixed(1)} h` }) +
      `<div class="sbmeta">rate from ${hkt(m.rate_from)}, since ${hkt(m.since)}${m.previous_model ? `, replaced ${esc(m.previous_model)} (its last call ${hkt(m.replaced_at)})` : ""}, last call ${ago(m.last_call)}</div>`;
    const modelRows = busy.map(modelRow).join("") + (idle.length ? `<details class="days"><summary>${idle.length} purposes with no call in the last 24 h</summary>${idle.map(modelRow).join("")}</details>` : "") +
      (offModels.length ? `<details class="days off"><summary>Turned off: ${offModels.length} purposes (only one-off or stopped jobs call them)</summary>${offModels.map(modelRow).join("")}</details>` : "");
    // 5. AI this month by purpose × model: bar = the last 24 h, ordered by it; replaced models are history
    const am: D[] = bars.items ? items.filter(r => r.id === "openrouter").map(r => ({ purpose: r.purpose, model: r.model, current: true, last_24h_usd: r.h24_usd, usd: r.month_usd }))
      : (sp.ai_models || []);
    const amax = Math.max(0, ...am.map(a => a.last_24h_usd || 0));
    const modelOf = (purpose: string) => models.find(m => m.purpose === purpose);
    const aiRow = (a: D) => barRow({ label: esc(a.purpose), sub: esc(a.model) + (a.current ? "" : " " + pill("off", "replaced" + ((modelOf(a.purpose) || {}).previous_model === a.model ? " " + hkt((modelOf(a.purpose) || {}).replaced_at) : ""))),
      v: a.last_24h_usd || 0, max: amax, color: COLORS.openrouter, cls: a.current ? "" : "past", a: "24 h " + usd(a.last_24h_usd, 4), b: "month " + usd(a.usd, 4) });
    const amSorted = am.slice().sort((x, y) => (y.last_24h_usd || 0) - (x.last_24h_usd || 0) || (y.usd || 0) - (x.usd || 0));
    const [am24, am0] = split(amSorted, a => a.last_24h_usd || 0);
    const aiRows = am24.map(aiRow).join("") + rest(am0.map(aiRow), "purpose × model rows");
    // 6. the smaller providers' parts (captcha solvers, Hetzner, AWS, Cloudflare, Exa): same rows, one scale each
    const small = ([["solvers", "Captcha solvers", "(solver × site; Deyao's own accounts, our log)"], ["hetzner", "Hetzner Cloud (project yyc)", "(resources now, list prices)"],
      ["aws", "AWS (account yyc)", "(per service; the latest closed day's cost spread over its 24 h, Cost Explorer)"],
      ["cloudflare", "Cloudflare (account YYC)", "(subscription spread over the hours + usage beyond included)"], ["exa", "Exa", "(the data API's web search; within the free tier's monthly credit)"]] as const).map(([id, t, s]) => {
      const rows: D[] = items.filter(r => r.id === id);
      const p = provs.find(x => x.id === id);
      if (!p && !rows.length) return "";
      const m = Math.max(0, ...rows.map(r => r.h24_usd || 0));
      const [r24, r0] = split(rows, r => r.h24_usd || 0);
      const row = (r: D) => barRow({ label: esc(r.name), v: r.h24_usd || 0, max: m, a: h24Txt(r), b: monthTxt(r), color: COLORS[id] });
      return `<div class="blk" id="spend-${id}">${h3(esc(t), esc(s))}${r24.map(row).join("") || `<p class="muted small">No spend in the last 24 h${p && p.month_usd ? ", " + monthTxt(p) : ""}.</p>`}${rest(r0.map(row), "items")}</div>`;
    }).join("");
    // 7. turned off: every part that no longer runs, this month's spend (bar = the month: it has no 24 h to rank by)
    const offSorted = offParts.slice().sort((x, y) => (y.month_usd || 0) - (x.month_usd || 0) || (y.h24_usd || 0) - (x.h24_usd || 0));
    const omax = Math.max(0, ...offSorted.map(r => r.month_usd || 0));
    const offRow = (r: D) => barRow({ label: esc(r.name), sub: esc(pname(r.id)) + ", " + esc(r.reason || "") + (r.jobs && r.jobs.length ? ", job " + esc(r.jobs.join(", ")) : ""),
      v: r.month_usd || 0, max: omax, color: COLORS[r.id], cls: "past", a: "month " + u4(r.month_usd) + (r.month_bytes != null ? ` <span class="muted">(${bytes(r.month_bytes)})</span>` : ""), b: "24 h " + u4(r.h24_usd) });
    const OTOP = 15;
    const offBlock = offSorted.length ? `<div class="blk" id="spend-off">${h3("Turned off: spend this month", "not in any ranking above; bar = this month, biggest first")}
      ${offSorted.slice(0, OTOP).map(offRow).join("")}${offSorted.length > OTOP ? `<details class="days"><summary>${offSorted.length - OTOP} more turned-off parts</summary>${offSorted.slice(OTOP).map(offRow).join("")}</details>` : ""}
      <p class="note">${esc(bars.off_rule || "")}.</p></div>` : "";
    const table = days.slice().reverse().map((d, i) => `<span>${d}</span>${series.map(s => `<span>${s.by_day[d] != null ? Number(s.by_day[d]).toFixed(4) : "–"}</span>`).join("")}<span><b>${totals[days.length - 1 - i].toFixed(4)}</b></span>`).join("");
    const html = `<h3 class="sh" style="margin-top:0">Now</h3>${nowTiles}${notes}
    <div class="spgrid"><div class="stack">${byProvider}${allItems}${offBlock}</div><div class="stack">
    <div class="blk" id="ai-models">${h3("AI this month, by purpose and model", "(models in use; bar = the last 24 h; replaced ones are in the Turned off list)")}${aiRows || '<p class="muted small">No AI spend logged this month.</p>'}</div>
    <div class="blk" id="ai-rates">${h3("AI: the model in use per purpose and its rate now", "(our log: OpenRouter's charged cost per call)")}${modelRows || '<p class="muted small">No AI calls logged.</p>'}<p class="note">${esc(sp.now_basis || "")}</p></div>
    ${perJob}${small}</div></div>
    <h3 class="sh">Last 30 days, by day <span class="small muted">(history)</span></h3>
    <div class="legend">${legend}</div>
    <div class="chart"><svg viewBox="0 0 ${W} ${H}" style="height:${H}px" role="img" aria-label="USD per day, last 30 days, stacked by provider">${g}</svg><div class="tip" id="tip" hidden></div></div>
    <details class="days"><summary>Day by day (USD)</summary><div class="daygrid" style="grid-template-columns:auto repeat(${series.length + 1}, minmax(0,1fr))"><b>Day</b>${series.map(s => `<b>${esc(s.id)}</b>`).join("")}<b>Total</b>${table}</div></details>`;
    return { html, month, days, series, totals };
  }

  /** an error shown in place (Deyao, 2026-10-22: "expand the error by default, only fold the overflowing thing"):
   *  the label and the text, clamped to 2 lines by CSS (.errx); "more" appears only when the clamp hides text
   *  (measured in the browser: fitErrors), and stays expanded across redraws (c.open) */
  const errText = (id: string, label: string, text: string, past: boolean) =>
    `<div class="errx${c.open.has(id) ? " open" : ""}${past ? " past" : ""}" data-id="${esc(id)}"><pre><span class="errl">${esc(label)}:</span> ${esc(text)}</pre><button type="button" class="more" hidden>${c.open.has(id) ? "less" : "more"}</button></div>`;

  function sources() {
    const s = D.sources || {};
    if (s.error) return { html: `<p class="blind">can't read: ${esc(s.error)}</p>`, sum: "", rule: "" };
    const cn = s.counts || {};
    const sum = `${cn.total} sources, ${cn.failing} failing, ${cn.off || 0} off, ${cn.quiet} quiet, ${cn.proxy} via proxy, ${cn.browser} via browser, last poll ${s.last_poll ? ago(s.last_poll) : "–"}`;
    const q = c.query.toLowerCase(), filter = c.filter;
    const rows = (s.rows || []).filter((r: D) =>
      (filter === "all" || (filter === "problems" && (r.state === "failing" || r.state === "quiet")) || r.kind === filter) &&
      (!q || (r.name + " " + r.id).toLowerCase().includes(q)));
    const html = rows.length ? rows.map((r: D) => {
      // off on purpose (the safety stop): "off: safety stop", never failing (Deyao, 2026-10-15)
      // a failing source is quiet (ink, no colour): most likely their end, outside our control (Deyao, 2026-11-17)
      const st = r.state === "off" ? pill("off", "off: " + r.off) : r.state === "failing" ? pill("", `failing${r.fails ? ", " + r.fails + " polls" : ""}`) : r.quiet ? pill("warn", "quiet") : pill("ok", "ok");
      const err = r.state === "off" ? null : r.error || r.last_error;
      // ink while it is current (failing now), muted once the source answers again: never a state colour
      const live = r.error_current ?? r.state === "failing";
      return `<div class="item"><div class="top">${st}<span class="name">${esc(r.name)}</span><span class="id">${esc(r.id)}, ${esc(r.kind)}</span></div>
      <div class="meta">24 h: ${num(r.posts_24h)} posts, usual ${r.usual_per_day == null ? "–" : r.usual_per_day + "/day"}, last fetch ${num(r.items)} items, last OK ${when(r.last_ok)}${r.fail_since && r.state === "failing" ? ", failing since " + hkt(r.fail_since) : ""}</div>
      ${err ? errText("err:" + r.id, live ? (r.error ? "Error now" : "Last error " + ago(r.last_error_at)) : "Earlier error " + ago(r.last_error_at) + " (answering since: last OK " + ago(r.last_ok) + ")", err, !live) : ""}</div>`;
    }).join("") : `<p class="muted small">${filter === "problems" ? "Every source that is on is answering and none is quiet." : "No source matches."}</p>`;
    return { html, sum, rule: s.rule || "" };
  }

  /** the full-text rate per source, 24 h (monitor.fulltext_sources): worst first; under 80 % is a warning */
  function ftSources(rows: D[]) {
    if (!rows.length) return "";
    const line = (r: D) => `<li>${r.rate == null ? pill("off", "waiting") : r.rate < 80 ? pill("warn", r.rate + "%") : pill("ok", r.rate + "%")} ${esc(r.source || r.sid)} <span class="muted small">${esc(r.sid)}, ${num(r.full)} full, ${num(r.missing)} title only or none, ${num(r.pending)} waiting, ${num(r.posts)} posts</span></li>`;
    const bad = rows.filter(r => r.rate != null && r.rate < 80), rest = rows.filter(r => !(r.rate != null && r.rate < 80));
    return `<h4 class="small">Full text by source, 24 h</h4>${bad.length ? `<ul class="small ftl">${bad.map(line).join("")}</ul>` : `<p class="small muted">Every source read keeps full text for 80 % or more of its posts.</p>`}
      <details><summary class="small">All ${rows.length} sources</summary><ul class="small ftl">${rest.map(line).join("")}</ul></details>`;
  }

  /** link following (follow.py): the links of the posts that reached a stream, handed to ingest */
  function follow(f: D) {
    if (!f || !f.all) return "";
    const line = (x: D) => `${num(x.posts)} posts followed, ${num(x.links)} links: ${num(x.new)} new, ${num(x.duplicate)} duplicate, ${num(x.unknown)} unknown site, ${num(x.skipped)} skipped, ${num(x.failed)} failed${x.pending ? ", " + num(x.pending) + " waiting" : ""}${x.from_mirrors ? ` (${num(x.from_mirrors)} from Reddit mirror bots)` : ""}`;
    const site = (s: D) => `<li><b>${esc(s.host || "?")}</b> <span class="muted small">${num(s.links)} links from ${num(s.posts)} posts, last ${when(s.last)}${s.reason ? ", " + esc(s.reason) : ""}</span><br><span class="small">e.g. ${s.example && s.example.post_id ? `<a href="/item/${s.example.post_id}">${esc(s.example.title || s.example.post_url || "")}</a>` : esc((s.example || {}).post_url || "")} → <a href="${esc((s.example || {}).link || "")}" target="_blank" rel="noopener">${esc((s.example || {}).link || "")}</a></span></li>`;
    const sites: D[] = f.unknown_sites || [];
    const SKIP: Record<string, string> = { home: "front pages", section: "section / tag pages", media: "pictures, audio, video", file: "files (PDF …)", share: "share buttons", ad: "adverts", app: "app-store links", google: "Google pages", social: "social platform pages", not_web: "not web links" };
    return `<div class="card wide"><h3>Link following ${f.last_followed ? pill("ok", "running") : pill("off", "nothing yet")}</h3><dl class="kv">
      <dt>Last 24 h</dt><dd>${line(f.last_24h || {})}</dd><dt>All</dt><dd>${line(f.all)}</dd><dt>Last followed</dt><dd>${when(f.last_followed)}</dd>
      <dt>Skipped, 24 h</dt><dd>${(f.skipped || []).length ? chips(f.skipped.map((s: D) => `${esc(SKIP[s.reason] || s.reason)} <b>${num(s.links)}</b>`)) +
        `<details class="inl"><summary class="small">by host</summary>${f.skipped.map((s: D) => `<div class="small"><b>${esc(SKIP[s.reason] || s.reason)}</b>: ${(s.hosts || []).map((h: D) => esc(h.host) + " " + h.n).join(", ")}</div>`).join("")}</details>` : "none"}</dd>
      ${(f.failed || []).length ? `<dt>Given up</dt><dd>${f.failed.slice(0, 5).map((x: D) => `<div class="gu"><span class="url">${esc(x.url)}</span><span class="det">${esc(x.error || "")}</span></div>`).join("")}</dd>` : ""}</dl>
      <h4 class="small">Sites no source reads (${sites.length})</h4>${sites.length ? `<ul class="small">${sites.slice(0, 10).map(site).join("")}</ul>${sites.length > 10 ? `<details><summary class="small">${sites.length - 10} more</summary><ul class="small">${sites.slice(10).map(site).join("")}</ul></details>` : ""}` : `<p class="small muted">Every linked site has a source.</p>`}</div>`;
  }

  function pipeline() {
    const j = D.jobs || {}, p = D.pipeline || {};
    let h = "";
    if (p.error) h += `<p class="blind">can't read: ${esc(p.error)}</p>`;
    else {
      const sc = p.scoring || {}, hs = p.history_scoring || {}, ft = p.fulltext || {}, lim = p.ai_limit;
      const rep = p.replies || {}, soc = p.social || {}, db = p.database || {}, tr = p.transcribe || {}, pc = p.pictures || {};
      const arch = Object.entries(p.archive_backfill || {}) as [string, D][];
      for (const [prov, x] of Object.entries(p.social_limit || {}) as [string, D][]) h += `<p class="err">${prov === "anyapi" ? "AnyAPI" : "TikHub"}: ${esc((stopOf(prov) || {}).tag || "requests failing")}, requests failing since ${when(x.since)}: its social search platforms bring in nothing new until the account is topped up (the hourly run tries again). It said: ${esc(x.message || "")}${x.notified ? ", Deyao told on Discord" : ""}</p>`;
      const w = p.watch || {};
      if (w.paused) h += `<p class="cau">Account watching reached its daily safety stop on ${esc(w.paused.day || "")} (since ${when(w.paused.since)}): ${usd((w.paused.spend || {}).total, 2)} of ${usd((w.paused.spend || {}).cap, 2)}. The watched accounts wait until 00:00 HKT and carry on by themselves; search and the other sources keep running.${w.paused.notified ? ", Deyao told on Discord" : ""}</p>`;
      if (lim) h += `<p class="err">OpenRouter ${esc((stopOf("openrouter") || {}).tag || "requests failing")} since ${when(lim.since)}: live scoring, history scoring and AI features are paused until the key's limit is raised or the account recharged (the app tries again every 10 minutes). OpenRouter said: ${esc(lim.message || "")}${lim.notified ? ", Deyao told on Discord" : ""}</p>`;
      h += `<div class="cards">
      <div class="card"><h3>Poller &amp; live scoring ${(p.poller || {}).minutes_ago > 15 ? pill("bad", "late") : pill("ok", "running")}</h3><dl class="kv">
        <dt>Last poll</dt><dd>${when((p.poller || {}).last_poll)}</dd><dt>Awaiting score</dt><dd>${num(sc.awaiting)}${sc.oldest_waiting ? ", oldest " + ago(sc.oldest_waiting) : ""}</dd>${sc.first_run ? `<dt>New sources' first reads</dt><dd>${num(sc.first_run)} awaiting score (數據分析 only, never filed)</dd>` : ""}
        <dt>Last scored</dt><dd>${when(sc.last_scored)}</dd><dt>Comments scored</dt><dd>${sc.comments ? "on" : "off"}</dd></dl></div>
      ${hs.on === false ? "" : `<div class="card"><h3>History scoring ${hs.on === false ? pill("off", "off") : hs.state === "done" ? pill("ok", "done") : hs.state === "limit" ? (hs.state_current === false ? pill("off", "OpenRouter limit, " + (hs.at ? ago(hs.at) : "earlier")) : pill("bad", "OpenRouter limit")) : pill("ok", esc(hs.state || "–"))}</h3>${hs.state === "limit" && hs.state_current === false ? `<p class="small muted">Last recorded state: stopped for OpenRouter's limit ${hs.at ? ago(hs.at) : ""}; OpenRouter answers again since, so this is history until the scorer records its next state.</p>` : ""}<dl class="kv">
        <dt>Posts left</dt><dd>${num(hs.left)}</dd><dt>Scored so far</dt><dd>${num(hs.posts)}, ${usd(hs.spent_usd, 4)}</dd>
        <dt>Checked</dt><dd>${when(hs.at)}</dd></dl></div>`}
      <div class="card wide"><h3>Full text</h3><dl class="kv"><dt>Waiting</dt><dd>${num(ft.waiting_recent_2d)} from the last 2 days, ${num(ft.waiting_all)} in all</dd>
        <dt>With text</dt><dd>${num(ft.with_text)} of ${num(ft.posts)} news/official/blog posts</dd><dt>Last page read</dt><dd>${when(ft.last_read)}</dd>
        <dt>Proxy pages</dt><dd>${num(ft.proxy_pages_today)} today</dd></dl>${ftSources(ft.sources || [])}</div>
      ${follow(p.follow)}
      <div class="card wide"><h3>Video &amp; audio transcripts ${tr.off ? pill("off", "off: " + esc(tr.off)) : lim ? pill("bad", "stopped: OpenRouter " + ((stopOf("openrouter") || {}).tag || "requests failing")) : pill("ok", "running")}</h3><dl class="kv">
        ${tr.off ? `<dt>Off</dt><dd>${esc(tr.off)}<span class="det">no audio fetched, nothing sent to the model; queued clips wait and are transcribed once it is on again. YouTube clips still get their own subtitles (ISP IP, no model): ${num(tr.captions_only_24h)} in 24 h</span></dd>` : ""}
        ${lim && !tr.off ? `<dt>Stopped since</dt><dd>${when(lim.since)} (OpenRouter's requests failing: see above); clips wait in the queue and continue by themselves</dd>` : ""}
        <dt>Queue</dt><dd>${num(tr.queued)} to transcribe${tr.waiting_host ? ` (${num(tr.waiting_host)} waiting on their host)` : ""}${tr.oldest_queued ? ", oldest post " + ago(tr.oldest_queued) : ""}</dd>
        <dt>Last 24 h</dt><dd>${num(tr.done_24h)} clips, ${num(tr.minutes_24h)} min, ${num(tr.pages_scanned_24h)} pages looked at</dd>
        <dt>YouTube</dt><dd>through the ISP IP (one client at a time): ${num(tr.captions_24h)} clips with YouTube's own subtitles kept in 24 h</dd>
        <dt>Not read directly</dt><dd>${num(tr.no_direct_24h)} in 24 h (${num(tr.no_direct)} in all): no audio through yycstream</dd>
        <dt>Spent today</dt><dd>${usd(tr.usd_today, 4)} (ai_spend transcribe)</dd>
        <dt>All</dt><dd>${num(tr.done)} done, ${num(tr.failed)} given up, last ${when(tr.last_done)}</dd>
        <dt>By host, 24 h</dt><dd>${(() => {
          const hs: D[] = (tr.hosts_24h || []).slice().sort((a: D, b: D) => (b.done + b.queued + b.failed) - (a.done + a.queued + a.failed));
          const one = (x: D) => `${esc(x.host || "?")} <b>${x.done}/${x.done + x.queued + x.failed}</b>${x.failed ? " (" + x.failed + " failed)" : ""}`;
          return chips(hs.slice(0, 16).map(one)) + (hs.length > 16 ? `<details class="inl"><summary class="small">${hs.length - 16} more hosts</summary>${chips(hs.slice(16).map(one))}</details>` : "");
        })()}</dd></dl></div>
      <div class="card"><h3>Pictures scored for meaning ${pc.off ? pill("off", "off: " + esc(pc.off)) : lim ? pill("bad", "stopped: OpenRouter " + ((stopOf("openrouter") || {}).tag || "requests failing")) : pill("ok", "running")}</h3><dl class="kv">
        ${pc.off ? `<dt>Off</dt><dd>${esc(pc.off)}<span class="det">nothing scored, the stream scorer is text-only; queued pictures wait</span></dd>` : ""}
        <dt>Queue</dt><dd>${num(pc.due)} pictures to score${pc.failed ? `, ${num(pc.failed)} given up` : ""}</dd>
        <dt>Last 24 h</dt><dd>${num(pc.scored_24h)} scored, ${num(pc.meaningful_24h)} meaningful (${num(pc.threshold)}+), ${num(pc.posts_looked_at_24h)} posts looked at</dd>
        <dt>Spent today</dt><dd>${usd(pc.usd_today, 4)} (ai_spend images), ${esc(pc.model || "")}</dd>
        <dt>All</dt><dd>${num(pc.scored)} pictures, ${num(pc.meaningful)} meaningful, last ${when(pc.last)}</dd></dl></div>
      <div class="card"><h3>Watched accounts ${w.paused ? pill("warn", "paused: safety stop") : pill("ok", "running")}</h3><dl class="kv">
        <dt>Accounts</dt><dd>${num(w.accounts)}</dd>
        ${(Object.entries(w.platforms || {}) as [string, D][]).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${num(v.accounts)} (${Object.entries(v.groups || {}).map(([g, n]) => esc(g) + " " + n).join(", ")})${v.ok !== undefined ? ", " + num(v.ok) + " answering" : ""}${v.failing ? ", " + pill("warn", v.failing + " failing") : ""}${v.off ? ", " + esc(v.off) : ""}</dd>`).join("")}
        <dt>Last run</dt><dd>${when(w.last_run)}, ${num(w.posts_in_file)} posts in the file, ${num(w.posts_24h)} filed in 24 h</dd>
        <dt>Spent today</dt><dd>${usd((w.today || {}).total, 3)} (TikHub ${usd((w.today || {}).tikhub, 3)}, proxy ${usd((w.today || {}).proxy, 3)}, model share ${usd((w.today || {}).model, 3)})</dd>
        <dt>Last days</dt><dd>${chips(Object.entries(w.days || {}).map(([d, v]) => esc(dtEn.date(d, c.now)) + " <b>" + usd(v, 2) + "</b>"))}</dd></dl></div>
      <div class="card wide"><h3>Forum replies</h3><dl class="kv">
        ${(Object.entries(rep) as [string, D][]).map(([k, v]) => !v ? "" : `<dt>${esc(k)} replies</dt><dd>${when(v.at)}, ${v.skipped ? brief(v.skipped, "b") :
          `${v.ok === false ? pill("", "failing") + " " : ""}${v.threads != null ? num(v.threads) + " threads read, " : ""}${num(v.new_replies)} new, ${num(v.pending)} pending, ${v.proxy_mb_today ?? 0} MB proxy today${v.errors ? ", " + v.errors + " errors" : ""}${v.blocked ? " " + pill("warn", "blocked") : ""}`}${v.error ? `<div class="muted small">${esc(v.error)}</div>` : ""}</dd>`).join("")}</dl></div>
      <div class="card"><h3>Social search &amp; archive</h3><dl class="kv">${soc.last_run ? `<dt>Last social run</dt><dd>${when(soc.last_run.at)}, ${usd(soc.last_run.cost_usd, 3)}${(soc.last_run.failed || []).length ? ", failed: " + esc(soc.last_run.failed.join(", ")) : ""}</dd>` : ""}
        <dt>Failing checks</dt><dd>${(soc.failing_checks || []).length ? esc(soc.failing_checks.join(", ")) : "none"}</dd>
        <dt>Archive backfill</dt><dd>${arch.length ? arch.map(([k, v]) => `${esc(k)}: ${esc(v.done_to || v.next || JSON.stringify(v).slice(0, 60))}`).join("<br>") : "no outlet position stored"}</dd>
        <dt>Database</dt><dd>${db.bytes ? (db.bytes / 1e9).toFixed(2) + " GB" : "–"}, ${num(db.posts_estimate)} posts</dd></dl></div>
    </div>`;
    }
    if (j.error) h += `<p class="blind">Jobs: can't read: ${esc(j.error)}</p>`;
    else {
      h += `<h3 style="font-size:14px;margin:14px 0 4px">CronJobs</h3><div class="list">` + (j.cronjobs || []).map((cj: D) => {
        const st = cj.off ? pill("off", "off: " + esc(cj.off)) : cj.suspended ? pill("off", "off: suspended") : cj.late ? pill("bad", cj.late_after_minutes ? `late: no success in ${cj.late_after_minutes} min` : "late") : cj.last_finished === "failed" ? pill("warn", "last run failed") : pill("ok", "ok");
        return `<div class="item"><div class="top">${st}<span class="name">${esc(cj.name)}</span><span class="id">${esc(cj.schedule)} UTC</span>
        <span class="runs" title="newest first">${(cj.recent || []).map((r: D) => `<i class="${esc(r.state)}" title="${esc(r.state)}${r.reason ? " (" + esc(r.reason) + ")" : ""}, started ${esc(hkt(r.start))} HKT"></i>`).join("")}</span></div>
        <div class="meta">last success ${when(cj.last_success)}, last start ${when(cj.last_schedule)}${cj.active ? ", running now" : ""}${cj.memory_peak_mib != null ? `, memory peak ${cj.memory_peak_mib} of ${cj.memory_limit_mib ?? "?"} Mi (last 20 runs)` : ""}</div></div>`;
      }).join("") + `</div><h3 style="font-size:14px;margin:14px 0 4px">Deployments</h3><div class="list">` + (j.deployments || []).map((d: D) =>
        `<div class="item"><div class="top">${d.off && !d.replicas ? pill("off", "off: " + esc(d.off)) : (d.ready >= (d.replicas || 0) ? pill("ok", "ready") : d.ready > 0 ? pill("warn", `${d.ready} of ${d.replicas} ready`) : pill("bad", "not ready")) + (d.off ? pill("off", "transcription off: " + esc(d.off)) : "")}<span class="name">${esc(d.name)}</span><span class="id">${d.ready}/${d.replicas} ready, ${esc(d.image)}</span></div></div>`).join("") + `</div>`;
    }
    return h;
  }

  function telegram() {
    const t = D.telegram || {};
    if (t.pending) return `<p class="muted small">${esc(t.error || "first snapshot still being computed")}</p>`;
    if (t.error) return `<p class="blind">can't read: ${esc(t.error)}</p>`;
    return `<div class="list">${(t.streams || []).map((s: D) => `<div class="item"><div class="top">${!s.channel ? pill("off", "no channel") : s.backlog ? pill(s.backlog > 20 ? "bad" : "warn", s.backlog + " waiting") : pill("ok", "up to date")}<span class="name">${esc(s.name)}</span><span class="id">${esc(s.stream)}</span></div>
    <div class="meta">${num(s.items)} items in the stream, ${lastSent(s)}</div></div>`).join("")}</div>
    <p class="note">${sendError(t.last_error)} ${esc(t.note || "")}</p>`;
  }

  /** "last sent: item 2150, sent 8 min ago (filed 9 min ago)" */
  function lastSent(s: D) {
    return `last sent: item ${s.last_sent_id == null ? "–" : s.last_sent_id}${s.last_sent_at ? ", sent " + when(s.last_sent_at) : ""}${s.last_sent_item_seen ? " (filed " + ago(s.last_sent_item_seen) + ")" : ""}`;
  }

  /** a channel's last send error: red only while current (no send worked after it); else history, muted, with its time */
  function sendError(e: D | null | undefined) {
    if (!e) return "No send error recorded.";
    const where = e.stream ? " (" + esc(e.stream) + ")" : "";
    if (e.current === false) return `Earlier send error ${ago(e.at)}${where}, since then sends worked (${e.resolved_at ? "next one " + ago(e.resolved_at) : "later"}): ${esc(e.error)}.`;
    return `Last send error ${ago(e.at)}${where}: <span class="err">${esc(e.error)}</span>`;
  }

  function whatsapp() {
    const w = D.whatsapp || {};
    if (w.pending) return `<p class="muted small">${esc(w.error || "first snapshot still being computed")}</p>`;
    if (w.error) return `<p class="blind">can't read: ${esc(w.error)}</p>`;
    const st = w.status || {}, ph = w.phone || {};
    const kind = ({ linked: "ok", connecting: "warn", unlinked: "off" } as Record<string, string>)[st.state] || "bad";
    const close = st.last_close ? `, last disconnect ${ago(st.last_close.at)}: ${esc(String(st.last_close.code ?? ""))} ${esc(st.last_close.reason || "")}` : "";
    return `<div class="list"><div class="item"><div class="top">${pill(kind, st.state || "no gateway yet")}<span class="name">Linked device</span><span class="id">${esc(st.me ? "+" + st.me : "")}${st.name ? ", " + esc(st.name) : ""}</span></div>
    <div class="meta">${st.since ? (st.state || "state") + " since " + ago(st.since) : ""}${close}</div>
    <div class="meta">primary phone: cloud phone ${esc(ph.id || "–")}${ph.region ? " (" + esc(ph.region) + ")" : ""}</div></div>` +
      (w.streams || []).map((s: D) => `<div class="item"><div class="top">${s.backlog == null ? pill("off", "starts with the next item") : s.backlog ? pill(s.backlog > 20 ? "bad" : "warn", s.backlog + " waiting") : pill("ok", "up to date")}<span class="name">${esc(s.name)}</span><span class="id">${esc(s.stream)}</span></div>
    <div class="meta">${lastSent(s)}</div></div>`).join("") + `</div>
    <p class="note">${sendError(w.last_error)} ${esc(w.note || "")}</p>`;
  }

  /** Cloudflare: Deyao's five records (CNAME to YYC's SaaS zone, set by hand) and a link to the tofu workflow's runs */
  function cloudflare() {
    const c = D.cloudflare || {};
    if (c.pending) return `<p class="muted small">${esc(c.error || "first snapshot still being computed")}</p>`;
    if (c.error) return `<p class="blind">can't read: ${esc(c.error)}</p>`;
    const names = (c.names || []).map((n: D) => `<div class="item" data-name="${esc(n.name)}" data-ok="${n.ok ? "1" : "0"}"><div class="top">${n.ok ? pill("ok", "CNAME ok") : n.cname == null ? pill("warn", "lookup failed") : pill("bad", "wrong record")}<span class="name">${esc(n.name)}</span><span class="id">${esc((n.cname || []).join(", ") || (n.error ? "" : "no CNAME"))}</span></div>${n.error ? `<div class="meta err">${esc(n.error)}</div>` : ""}</div>`).join("");
    return `<div class="list">${names}</div><p class="note">Each name must be a DNS-only CNAME to ${esc(c.target || "")} (Deyao's zone, set by hand); public DNS, every 5 min. YYC's Cloudflare as code (news-stream/deploy/cloudflare/tofu): <a href="${esc(c.runs || "")}">the workflow's runs</a> (a failed run is posted to Discord).</p>`;
  }

  /** WiseSearch 轉接: searches sent to YYC's WisersOne account (the relay's search log) */
  function catalogue() {
    const k = D.catalogue || {}, rs: Record<string, D> = k.routes || {};
    const gb = (b: number | null | undefined) => b == null ? "" : `, ${(b / 1e6).toFixed(1)} MB yycstream today`;
    const names: Record<string, string> = { direct: "Direct (YYC server)", isp: "ISP IP", yycstream: "yycstream", browser: "Real browser" };
    const rows = Object.entries(rs).map(([r, v]) => `<div class="item"><div class="top">${pill(v.ok === v.sites ? "ok" : "warn", `${esc(String(v.ok))}/${esc(String(v.sites))} OK`)}<span class="name">${esc(names[r] || r)}</span><span class="id">${esc(String(v.new_posts))} new posts, ${esc(String(v.posts_scored))} scored (${esc(String(v.confirmed))} confirmed)</span></div>
      <div class="meta">${esc(String(v.backlog_posts))} first-read backlog, ${esc(String(v.pages_read))} article pages read${gb(v.yycstream_bytes_today)}</div></div>`).join("");
    return `<div class="list">${rows}<div class="item"><div class="meta">Last ${esc(String(k.hours ?? 24))} h. ISP IP, sites job, today: ${((k.isp_bytes_today_sites_job || 0) / 1e6).toFixed(1)} MB, scoring spend, all sources: USD ${esc(String(k.scoring_usd_all_sources ?? ""))}. ${esc(k.note || "")}</div></div></div>`;
  }

  function wisers() {
    const w = D.wisers || {};
    if (w.pending) return `<p class="muted small">${esc(w.error || "first snapshot still being computed")}</p>`;
    if (w.error) return `<p class="blind">can't read: ${esc(w.error)}</p>`;
    const last = w.last ? `last search ${ago(w.last)}${w.last_ok === false ? " (did not complete)" : ""}` : "no search yet";
    const ss = w.session || {}, ll = ss.last_login || {};
    const st = ss.state === "held" ? pill("ok", "session held") : ss.state === "logged_out" ? pill("bad", "logged out") : pill("off", "session not opened yet");
    const sess = `<div class="item" id="wisers-session" data-state="${esc(String(ss.state ?? ""))}"><div class="top">${st}<span class="name">WisersOne login</span><span class="id">${ss.since ? "since " + esc(ago(ss.since)) : ""}</span></div>
    <div class="meta">${ss.last_ok ? `last call Wisers answered ${esc(ago(ss.last_ok))}` : "no call answered yet"}${ll.at ? `, last automatic login ${esc(ago(ll.at))} ${ll.ok ? "ok" : "failed" + (ll.error ? ": " + esc(String(ll.error)) : "")}` : ""}${ss.state === "logged_out" ? ", take it back: <code>kubectl -n yyc exec deploy/wisers -- python3 /app/wisers/login.py /var/lib/wisers/login</code>" : ""}</div></div>`;
    const h = w.health || {}, pr = h.probe || {}, fl: Record<string, number> = h.failures || {};
    const mins = (x: unknown) => x == null ? "" : `${Math.round(Number(x) / 60)} min`;
    const hp = !w.health ? "" : `<div class="item" id="wisers-health" data-ok="${(h.problems || []).length ? "0" : "1"}"><div class="top">${!pr.at ? pill("off", "no check yet") : pr.ok ? pill("ok", `answering, ${esc(String(pr.secs))} s`) : pill(Number(pr.fails ?? 2) >= 2 ? "bad" : "warn", "not answering")}<span class="name">Health check</span><span class="id">${pr.at ? "last " + esc(ago(pr.at)) : ""}</span></div>
    <div class="meta">one free WisersOne call (${esc(String(pr.path || "getUserCustomConfig"))}, no quota) every ${esc(mins(h.probe_secs))}${pr.ok === false ? `: ${esc(String(pr.outcome || ""))} ${esc(String(pr.error || ""))}` : ""}. Last 15 min: ${esc(String(h.calls ?? 0))} calls, ${Object.keys(fl).length ? Object.entries(fl).map(([k, v]) => `${esc(String(v))} ${esc(k)}`).join(", ") : "none failed"}. Last hour: ${esc(String(h.logins_1h ?? 0))} logins${h.failed_logins_1h ? ` (${esc(String(h.failed_logins_1h))} failed)` : ""}, ${esc(String(h.expiries_1h ?? 0))} expiries.${h.last_expiry ? ` Last expiry ${esc(ago(h.last_expiry.at))}: ${esc(String(h.last_expiry.how || ""))}${h.last_expiry.idle_secs != null ? `, ${esc(mins(h.last_expiry.idle_secs))} after the last answered call` : ""}${h.last_expiry.since_login_secs != null ? `, ${esc(mins(h.last_expiry.since_login_secs))} after the last login` : ""}${h.last_expiry.exit_ip ? `, from ${esc(String(h.last_expiry.exit_ip))}` : ""}.` : ""}</div>
    ${(h.problems || []).length ? `<div class="meta ${h.down ? "err" : "cau"}">${(h.problems as string[]).map(esc).join("; ")}</div>` : ""}
    ${(h.recent_failures || []).map((r: D) => `<div class="meta">${esc(ago(r.at))} ${esc(String(r.path || ""))}, ${esc(String(r.who || ""))}: ${esc(String(r.outcome || ""))}${r.error ? " " + esc(String(r.error).slice(0, 120)) : ""}</div>`).join("")}</div>`;
    return `<div class="list">${sess}${hp}<div class="item" id="wisers-counts" data-today="${esc(String(w.today ?? ""))}" data-month="${esc(String(w.month ?? ""))}"><div class="top">${pill("", esc(String(w.today ?? 0)) + " today")}${pill("off", esc(String(w.month ?? 0)) + " this month")}<span class="name">WisersOne searches</span><span class="id">${esc(String(w.all ?? 0))} logged</span></div>
    <div class="meta">${last}${w.today_failed ? `, ${esc(String(w.today_failed))} today did not complete` : ""}, <a href="${esc(w.log || "/app/wisers/log")}">search log</a></div></div></div>
    <p class="note">${esc(w.note || "")}</p>`;
  }

  /** TikTok videos (tiktok_video.py): the posts passing stage 1 whose video is downloaded and kept in the bucket */
  function videos() {
    const v = D.videos || {};
    if (v.pending) return `<p class="muted small">${esc(v.error || "first snapshot still being computed")}</p>`;
    if (v.error) return `<p class="blind">can't read: ${esc(v.error)}</p>`;
    const mb = (b: unknown) => `${(Number(b || 0) / 1e6).toFixed(1)} MB`;
    const st: Record<string, D> = v.status || {}, bk = v.bucket || null;
    const q = st.queued?.n || 0;
    const head = `<div class="item" id="videos-totals"><div class="top">${pill("", `${esc(String(st.ok?.n || 0))} saved`)}${q ? pill("", `${q} queued${v.retrying ? ", " + v.retrying + " retrying" : ""}`) : pill("ok", "queue empty")}${st.failed?.n ? pill("warn", `${st.failed.n} failed`) : ""}${st.gone?.n ? pill("off", `${st.gone.n} gone`) : ""}<span class="name">TikTok videos</span><span class="id">${mb(st.ok?.bytes)} in our records</span></div>
      <div class="meta">${bk ? `bucket ${esc(bk.bucket)}: ${esc(String(bk.objects))} objects, ${mb(bk.bytes)} (ListObjectsV2 ${ago(bk.at)})` : "bucket not measured yet"}</div></div>`;
    const days = (v.by_day || []).map((d: D) => `<div class="item"><div class="top"><span class="name">${esc(d.day)}</span><span class="id">${esc(String(d.queued))} queued (${esc(String(d.queued_stage1))} at stage 1, the rest by the sweep or backlog), ${esc(String(d.ok))} downloaded, ${mb(d.bytes)}${d.failed ? `, <span class="cau">${esc(String(d.failed))} failed</span>` : ""}${d.gone ? `, ${esc(String(d.gone))} gone` : ""}</span></div></div>`).join("");
    const reasons = (v.reasons || []).map((r: D) => `<div class="item"><div class="top">${pill(r.status === "gone" ? "off" : "warn", `${esc(String(r.n))} ${esc(r.status)}`)}<span class="id">${esc(r.reason)}</span></div></div>`).join("");
    const recent = (v.recent_failures || []).map((r: D) => `<div class="item"><div class="top">${pill(r.status === "queued" || r.status !== "gone" ? "warn" : "off", r.status === "queued" ? `retry (${r.tries} tries)` : r.status)}<span class="name"><a href="/item/${esc(String(r.post_id))}">post ${esc(String(r.post_id))}</a></span><span class="id">${ago(r.at)}</span></div><div class="meta err-clamp">${esc(r.error)}</div></div>`).join("");
    return `<div class="list">${head}${days || `<div class="item"><div class="meta">no videos yet</div></div>`}</div>` +
      (reasons ? `<h3>Failures by reason (last ${esc(String(v.days))} days)</h3><div class="list">${reasons}</div>` : "") +
      (recent ? `<h3>Latest failures and retries</h3><div class="list">${recent}</div>` : "") +
      `<p class="note">${esc(v.note || "")}</p>`;
  }

  // each section's snapshot: when it was computed and how old it is (the monitor refreshes panels in the background)
  function age(panels: string, fetchedAt: number) {
    const since = (c.now - fetchedAt) / 1000;
    return panels.split(" ").map(k => {
      const x = D[k] || {};
      if (x.pending) return `${esc(k)}: first snapshot still being computed`;
      const a = x.age_seconds == null ? null : x.age_seconds + since;
      return `${esc(k)} as of ${x.computed_at ? hm(x.computed_at) : "–"}${a == null ? "" : ", " + dur(a) + " old"}` +
        (x.refresh_error ? `, <span class="blind">last refresh failed ${ago(x.refresh_failed_at)}: ${esc(x.refresh_error)}</span>` : "");
    }).join("; ");
  }

  const safe = <T,>(name: string, f: () => T, fallback: T): T => { try { return f(); } catch (e) { console.error(name, e); return fallback; } };
  return {
    stops: safe("stops", stops, ""), tiles: safe("tiles", tiles, ""), balances: safe("balances", balances, ""),
    spending: safe("spending", spending, { html: "", month: "", days: [] as string[], series: [] as D[], totals: [] as number[] }),
    sources: safe("sources", sources, { html: "", sum: "", rule: "" }), pipeline: safe("pipeline", pipeline, ""),
    telegram: safe("telegram", telegram, ""), whatsapp: safe("whatsapp", whatsapp, ""), wisers: safe("wisers", wisers, ""), cloudflare: safe("cloudflare", cloudflare, ""), catalogue: safe("catalogue", catalogue, ""), videos: safe("videos", videos, ""), age,
  };
}

const REFRESH = 60_000;

/** the open proof flags (/app/monitor/flags; proof.py): each links to its entry in the proof view */
function ProofFlags() {
  const q = useQuery({ ...monitorFlagsQuery, refetchInterval: REFRESH, refetchIntervalInBackground: false });
  if (q.isError) return <p className="err">Could not read the flags: {(q.error as Error).message}</p>;
  if (!q.data) return <p className="note">loading…</p>;
  const fl = q.data.flags;
  if (!fl.length) return <p className="note" id="flags-none">No open flags.</p>;
  return (
    <div className="list" id="flag-list">
      {fl.map(f => (
        <div className="item" key={f.id} data-flag={f.id}>
          <div className="top"><span className="name"><a href={`/app/proof?sid=${encodeURIComponent(f.sid || "")}&at=${f.post_id ?? ""}`}>{f.title || f.url}</a></span>
            <span className="id">#{f.id}, {f.sid || "–"}</span></div>
          <div className="meta">{f.source || ""}, {dtEn.dateTime(f.at)} HKT, {f.email}</div>
          {f.note ? <div className="small">{f.note}</div> : <div className="small muted">(no note)</div>}
        </div>
      ))}
    </div>
  );
}
/** show each error's "more" only when its clamped text overflows (measured clamped, also for an expanded one) */
function fitErrors(root: HTMLElement | null) {
  root?.querySelectorAll<HTMLElement>(".errx").forEach(box => {
    const pre = box.querySelector("pre"), b = box.querySelector<HTMLButtonElement>("button.more");
    if (!pre || !b) return;
    const open = box.classList.contains("open");
    if (open) box.classList.remove("open");
    const over = pre.scrollHeight > pre.clientHeight + 1;
    if (open) box.classList.add("open");
    b.hidden = !over;
  });
}
const FILTERS = [["problems", "Problems"], ["all", "All"], ["proxy", "Proxy"], ["browser", "Browser"], ["site", "Catalogue"]] as const;
const html = (__html: string) => ({ __html });

export function MonitorPage() {
  const q = useQuery({ ...monitorQuery, refetchInterval: REFRESH, refetchIntervalInBackground: false });
  const D = q.data as D | null | undefined;
  const lastOk = q.dataUpdatedAt;
  // the time the page reads ages against: the data's own fetch time until the browser has mounted (hydration)
  const [now, setNow] = useState<number | null>(null);
  const [filter, setFilter] = useState("problems");
  const [query, setQuery] = useState("");
  const [W, setW] = useState(600);
  const [wide, setWide] = useState(false);
  const openRef = useRef(new Set<string>());
  const srcRef = useRef<HTMLDivElement>(null);
  const spRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setNow(Date.now());
    try { setFilter(localStorage.getItem("yyc-monitor-filter") || "problems"); } catch { /* private mode */ }
    const t = setInterval(() => setNow(Date.now()), 15_000);
    const vis = () => { if (!document.hidden && Date.now() - q.dataUpdatedAt > REFRESH) q.refetch(); };
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (q.dataUpdatedAt) setNow(Date.now()); }, [q.dataUpdatedAt]);

  // the chart's width follows its box (resize, 200 ms after the last change): it redraws on resize
  useLayoutEffect(() => {
    const measure = () => { setW(spRef.current?.clientWidth || 600); setWide(innerWidth >= 1100); };
    measure();
    let rz = 0;
    const on = () => { clearTimeout(rz); rz = window.setTimeout(measure, 200); };
    addEventListener("resize", on);
    return () => { removeEventListener("resize", on); clearTimeout(rz); };
  }, [!!D]);

  // opened details (the sources' errors, the per-job rows) stay open when the list is redrawn
  useEffect(() => {
    const els = [srcRef.current, spRef.current].filter(Boolean) as HTMLElement[];
    const t = (e: Event) => {
      const d = e.target as HTMLDetailsElement;
      if (d.tagName !== "DETAILS" || !d.dataset.id) return;
      if (d.open) openRef.current.add(d.dataset.id); else openRef.current.delete(d.dataset.id);
    };
    els.forEach(el => el.addEventListener("toggle", t, true));
    return () => els.forEach(el => el.removeEventListener("toggle", t, true));
  }, []);

  // errors shown in place: the "more" toggle only where the clamp hides text (at this width), and its click
  useLayoutEffect(() => fitErrors(srcRef.current));
  useEffect(() => {
    const el = srcRef.current; if (!el) return;
    const click = (e: Event) => {
      const b = (e.target as Element).closest?.("button.more"), box = b?.closest<HTMLElement>(".errx");
      if (!b || !box?.dataset.id) return;
      const open = box.classList.toggle("open");
      if (open) openRef.current.add(box.dataset.id); else openRef.current.delete(box.dataset.id);
      b.textContent = open ? "less" : "more";
    };
    const fit = () => fitErrors(el);
    el.addEventListener("click", click);
    addEventListener("resize", fit);
    document.fonts?.ready.then(fit);
    return () => { el.removeEventListener("click", click); removeEventListener("resize", fit); };
  }, []);

  const clock = now ?? lastOk;
  const parts = useMemo(() => D ? make(D, { now: clock, filter, query, open: openRef.current, W, wide }) : null, [D, clock, filter, query, W, wide]);

  const pick = (f: string) => { setFilter(f); try { localStorage.setItem("yyc-monitor-filter", f); } catch { /* private mode */ } };

  // the spending chart's tooltip (the stacked values of the day under the pointer)
  const tipMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const sp = parts?.spending; const tip = spRef.current?.querySelector<HTMLElement>("#tip"); const svg = spRef.current?.querySelector("svg");
    if (!sp || !tip || !svg) return;
    const r = (e.target as Element).closest?.("rect.hit") as SVGRectElement | null;
    if (!r) { tip.hidden = true; return; }
    const i = +(r.dataset.i || 0), d = sp.days[i];
    tip.innerHTML = `<b>${d}</b><br>` + sp.series.map(s => `${esc(s.name)}: ${usd(s.by_day[d] || 0, 4)}`).join("<br>") + `<br><b>Total ${usd(sp.totals[i], 4)}</b>`;
    tip.hidden = false;
    const box = svg.getBoundingClientRect(), x = e.clientX - box.left;
    tip.style.left = Math.min(Math.max(x - 80, 0), box.width - 200) + "px"; tip.style.top = "4px";
  };
  const tipLeave = () => { const tip = spRef.current?.querySelector<HTMLElement>("#tip"); if (tip) tip.hidden = true; };

  const err = q.error ? "Could not refresh: " + (q.error as Error).message + (D ? " (showing the last data)" : "") : null;
  const stale = now != null && lastOk ? (now - lastOk) / 1000 > 150 : false;
  const updated = (() => {
    if (!lastOk || !D) return <><span className="dot down" />{q.isPending ? "loading…" : "no data yet"}</>;
    return <><span className={"dot" + (stale ? " stale" : "")} />updated {dtEn.timeSec(lastOk)} HKT</>;
  })();
  const age = (panels: string) => parts ? <p className="age" data-panels={panels} dangerouslySetInnerHTML={html(parts.age(panels, lastOk))} /> : <p className="age" data-panels={panels} />;

  return (
    <div className="page-monitor mono-emoji" lang="en">
      <header className="bar yyc-bar">
        <div className="row yyc-row"><h1>YYC Monitor</h1><div className="upd" id="upd">{updated}</div></div>
        <nav className="jump yyc-sub" aria-label="Sections">
          <a href="#overview">Overview</a><a href="#flags">Flags</a><a href="#balances">Balances</a><a href="#spending">Spending</a>
          <a href="#sources">Sources</a><a href="#jobs">Jobs &amp; pipeline</a><a href="#telegram">Telegram</a><a href="#whatsapp">WhatsApp</a><a href="#wisers">WiseSearch</a><a href="#cloudflare">Cloudflare</a><a href="#catalogue">Catalogue cost</a><a href="#videos">TikTok videos</a>
        </nav>
      </header>
      <p id="banner" role="status" style={{ display: err ? "block" : "none" }}>{err}</p>
      <main>
        <section id="overview"><h2>Overview <small>read-only, refreshes every minute</small></h2>{age("stops")}
          <div className="stops" id="stops" dangerouslySetInnerHTML={html(parts?.stops || "")} />
          <div className="tiles" id="tiles" dangerouslySetInnerHTML={html(parts?.tiles || "")} /></section>
        <section id="flags"><h2>Proof flags <small>open entries marked wrong on /app/proof; Claude reads them with proof.py</small></h2><ProofFlags /></section>
        <section id="balances"><h2>Account balances <small>from each provider's API, refreshed every 5 min</small></h2>{age("balances")}
          <div className="cards" id="bal" dangerouslySetInnerHTML={html(parts?.balances || "")} /></section>
        <section id="spending"><h2>Spending <small id="spmonth">{parts?.spending.month || ""}</small></h2>{age("spending")}
          <div id="sp" ref={spRef} onPointerMove={tipMove} onPointerLeave={tipLeave} dangerouslySetInnerHTML={html(parts?.spending.html || "")} /></section>
        <section id="sources"><h2>Sources <small id="srcsum">{parts?.sources.sum || ""}</small></h2>{age("sources")}
          <div className="filters" role="group" aria-label="Filter sources">
            <div className="seg">{FILTERS.map(([f, label]) => <button key={f} type="button" data-f={f} aria-pressed={filter === f} onClick={() => pick(f)}>{label}</button>)}</div>
            <input id="q" type="search" placeholder="Filter by name" aria-label="Filter by name" value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          <div className="list" id="src" ref={srcRef} dangerouslySetInnerHTML={html(parts?.sources.html || "")} /><p className="note" id="srcrule">{parts?.sources.rule || ""}</p></section>
        <section id="jobs"><h2>Jobs &amp; pipeline</h2>{age("jobs pipeline")}<div id="pipe" dangerouslySetInnerHTML={html(parts?.pipeline || "")} /></section>
        <section id="telegram"><h2>Telegram <small>one channel per stream</small></h2>{age("telegram")}<div id="tg" dangerouslySetInnerHTML={html(parts?.telegram || "")} /></section>
        <section id="whatsapp"><h2>WhatsApp <small>one group per stream, YYC's linked device</small></h2>{age("whatsapp")}<div id="wa" dangerouslySetInnerHTML={html(parts?.whatsapp || "")} /></section>
        <section id="wisers"><h2>WiseSearch <small>searches on YYC's WisersOne account, HKT</small></h2>{age("wisers")}<div id="ws" dangerouslySetInnerHTML={html(parts?.wisers || "")} /></section>
        <section id="cloudflare"><h2>Cloudflare <small>Deyao's records for YYC, and YYC's account as code</small></h2>{age("cloudflare")}<div id="cf" dangerouslySetInnerHTML={html(parts?.cloudflare || "")} /></section>
        <section id="catalogue"><h2>Catalogue sources <small>sites.json: what they add per route, last 24 h</small></h2>{age("catalogue")}<div dangerouslySetInnerHTML={html(parts?.catalogue || "")} /></section>
        <section id="videos"><h2>TikTok videos <small>posts passing stage 1, downloaded and kept, HKT days</small></h2>{age("videos")}<div id="vid" dangerouslySetInnerHTML={html(parts?.videos || "")} /></section>
      </main>
    </div>
  );
}
