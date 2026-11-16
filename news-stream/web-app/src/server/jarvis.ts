// Jarvis 4's sessions on the live dashboard (Deyao, 2026-11-12: "a panel for my jarvis 4 status, i just need to know
// limited information like if session is working, idle, or with background"). Jarvis 4's router pushes them to
// POST /live/jarvis (server.ts) every few seconds; the LiveHub Durable Object keeps the last push and /app/live/data
// returns it as `jarvis`. Only what the panel shows is kept: a name, a state, two counts and a time per session.

/** working: a turn is running; waiting: it asks Deyao (a permission prompt); background: no turn, background jobs or
 * subagents running; idle: nothing running; booting: started, not reporting yet */
export const JARVIS_STATES = ["working", "waiting", "background", "idle", "booting"] as const;
export type JarvisState = typeof JARVIS_STATES[number];
export interface JarvisSession { name: string; state: JarvisState; jobs: number; subagents: number; since: number | null }
export interface JarvisLive { at: number; got: number; total: number; sessions: JarvisSession[] }

export const JARVIS_KEEP = 16;  // liveLayout JARVIS_MAX: the most any layout shows

const int = (v: unknown, max: number) => typeof v === "number" && Number.isFinite(v) ? Math.min(max, Math.max(0, Math.floor(v))) : 0;

/** the router's body → what the hub keeps, or null when it is not one */
export function cleanJarvis(body: unknown, now: number): JarvisLive | null {
  const b = body as { at?: unknown; total?: unknown; sessions?: unknown } | null;
  if (!b || typeof b !== "object" || !Array.isArray(b.sessions)) return null;
  const sessions: JarvisSession[] = [];
  for (const x of b.sessions.slice(0, JARVIS_KEEP) as Record<string, unknown>[]) {
    if (!x || typeof x !== "object" || !JARVIS_STATES.includes(x.state as JarvisState)) continue;
    sessions.push({
      name: (typeof x.name === "string" ? x.name : "").replace(/\p{Cc}/gu, " ").trim().slice(0, 120) || "untitled",
      state: x.state as JarvisState, jobs: int(x.jobs, 999), subagents: int(x.subagents, 999),
      since: typeof x.since === "number" && Number.isFinite(x.since) && x.since > 0 ? Math.floor(x.since) : null,
    });
  }
  return { at: int(b.at, 8.64e15) || now, got: now, total: Math.max(int(b.total, 9999), sessions.length), sessions };
}

/** the hub's last push (null: none yet, or the hub can't be reached) */
export async function jarvisLive(): Promise<JarvisLive | null> {
  try {
    const { env } = await import("cloudflare:workers") as unknown as { env: { LIVE?: DurableObjectNamespace } };
    if (!env.LIVE) return null;
    const r = await env.LIVE.get(env.LIVE.idFromName("hub")).fetch("https://hub/jarvis");
    return r.ok ? ((await r.json()) as JarvisLive | null) : null;
  } catch {
    return null;
  }
}
