// Deyao's monitor: the snapshots monitor.py (the cluster's monitor Deployment) publishes into kv monitor:data, one
// panel per key {stored_at: unix seconds, value: {…}}; read here in the shape of monitor.data(), each panel with its
// age now (monitor.published, exact port). Never calls the cluster.
import type { Sql } from "./db";
import { kvGet } from "./kv";

/** monitor.PANELS, in order */
export const PANELS = ["stops", "sources", "telegram", "whatsapp", "jobs", "pipeline", "balances", "spending", "wisers", "catalogue", "videos", "cloudflare"] as const;

type Panel = Record<string, unknown>;
export type MonitorData = Record<(typeof PANELS)[number], Panel> & { at: string };

const two = (n: number) => String(n).padStart(2, "0");
/** monitor.iso(monitor.now()): HKT, seconds, e.g. 2026-10-15T16:00:38+08:00 */
export function hktIsoNow(ms = Date.now()) {
  const h = new Date(ms + 8 * 3600e3);
  return `${h.getUTCFullYear()}-${two(h.getUTCMonth() + 1)}-${two(h.getUTCDate())}T${two(h.getUTCHours())}:${two(h.getUTCMinutes())}:${two(h.getUTCSeconds())}+08:00`;
}

/** Python's round(x, 1) for the ages (half to even is invisible at this precision; ties are not reachable in practice) */
const round1 = (x: number) => Math.round(x * 10) / 10;

export async function published(sql: Sql, nowMs = Date.now()): Promise<MonitorData> {
  const raw = await kvGet<unknown>(sql, "monitor:data", {});
  const d = raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Record<string, unknown> : {};
  const out: Record<string, unknown> = {};
  for (const k of PANELS) {
    const x = d[k] as { stored_at?: number; value?: Panel } | undefined;
    out[k] = x && typeof x === "object" && "value" in x
      ? { ...x.value, age_seconds: round1(nowMs / 1000 - Number(x.stored_at)) }
      : { pending: true, error: "first snapshot still being computed" };
  }
  out.at = hktIsoNow(nowMs);
  return out as MonitorData;
}
