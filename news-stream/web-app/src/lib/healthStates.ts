// The colour mapping of the system health page (/app/health, -HealthPage.tsx): every line's state comes from here, so
// one thing has one colour wherever it shows. The rule is the wall's (Deyao, 2026-11-17; news-stream README "State
// colours", the colour audit of that day and his answers in it) and the house style's (claude-env deyao-web-design
// "Status colours": the flight deck's colours, each state with its own shape too). Pure, so tests/test_health_page.py
// checks every rule in Node. Ask "who is stopped right now?": someone → lost (red); nobody, but worse than normal →
// caution (amber); the page cannot tell → blind (magenta); it ends by itself → advisory (blue); else normal, quiet.
//
// | state    | hue     | marker (health.css .hp-mk)        | when                                                        |
// |----------|---------|-----------------------------------|-------------------------------------------------------------|
// | lost     | red     | red block with a cross            | a service lost right now, for everyone or one person        |
// | caution  | amber   | amber block                       | still serving but worse: degraded, late, near a limit, an error that is over |
// | blind    | magenta | crossed eye                       | the page cannot read it, or its figures are over a minute old |
// | advisory | blue    | blue outline square               | something that finishes by itself (booting, warming up, a rollout, background, scheduled) |
// | working  | ink     | ink block                         | a Jarvis 4 session's turn running (normal, so no hue)       |
// | normal   | none    | none (dark cockpit)               | everything else, and what is recovered                      |
// Green is not a state colour here: normal stays quiet. An idle Jarvis 4 session is amber, but its word only, no
// block (Deyao, 2026-11-17: idle amber on the wall; the block is for what needs him or is wrong).

export type St = "ok" | "warn" | "bad" | "blind" | "info" | "ink";
export const STATE_NAME: Record<St, string> = { bad: "lost", warn: "caution", blind: "blind", info: "advisory", ink: "working", ok: "normal" };

type J = Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any

/** a figure older than this is blind: kv live:sys is written every 10 s, Jarvis 4 pushes every 5 s */
export const PART_STALE_MS = 60_000;
export const stale = (ageMs: number | null) => (ageMs != null && ageMs > PART_STALE_MS ? "blind" : "ok") as St;

// ---- the cluster ----
/** Deployments nobody works through: none ready stops nobody → caution at worst. The monitor (Deyao, 2026-11-17: its
 * 0/1 is amber, not red) is his own view, and its process keeps writing live:sys while /readyz waits on a slow panel */
export const SERVES_NOBODY = new Set(["monitor"]);
/** a news-stream Deployment with none ready ("name 0/2") that someone works through */
export const deploymentLost = (x: string) => / 0\/\d+$/.test(x) && !SERVES_NOBODY.has(x.split(" ")[0]);
/** news-stream Deployments short of pods ("name 1/2"): one lost → lost; else any short → caution */
export const deployments = (short: string[]): St => (short.some(deploymentLost) ? "bad" : short.length ? "warn" : "ok");
/** CronJobs: no success for three of its intervals ("late") → lost; one failed run (it runs again) → caution */
export const cronjobs = (late: string[], failed: string[]): St => (late.length ? "bad" : failed.length ? "warn" : "ok");
/** nodes: none ready → lost; one not ready, under pressure or cordoned (its pods move) → caution; unreadable → blind */
export function nodes(list: J[] | null): St {
  if (!list) return "blind";
  if (list.length && list.every(n => !n.ready)) return "bad";
  return list.some(n => !n.ready || n.pressure?.length || n.unschedulable) ? "warn" : "ok";
}
/** one YYC AI Deployment (hermit's state): none ready → lost; fewer than asked, others serving → caution; rolling out →
 * advisory */
export function pod(x: J): St {
  if (x.state === "problem" && !(x.ready > 0)) return "bad";
  if (x.state === "watch" || (x.state === "problem" && x.ready > 0)) return "warn";
  return x.state === "warming" ? "info" : "ok";
}
/** all of YYC AI's Deployments: the worst of them */
export const pods = (svc: J[]): St => worst(svc.map(pod));

// ---- YYC AI ----
/** hermit's levels (health_verdict.rs ok < warming < watch < blind < problem) */
export const LEVEL: Record<string, St> = { ok: "ok", warming: "info", watch: "warn", blind: "blind", problem: "bad" };
export const level = (l: unknown): St => LEVEL[String(l)] ?? "warn";
/** a sandbox: starting → advisory (a stuck start is hermit's red finding, shown as its own line) */
export const sandbox = (x: J): St => (x.status === "starting" ? "info" : "ok");
/** the budget used up: model calls are refused → lost */
export const budget = (b: J): St => (b.exhausted ? "bad" : "ok");
/** the counts of the last minutes (failed starts, failed calls, host errors): errors that are over → caution */
export const errorsOver = (n: number): St => (n > 0 ? "warn" : "ok");

// ---- providers ----
/** a provider's figures: read → normal; the read failed → blind; never connected (no key, on purpose) → normal */
export const provider = (p: J): St => (p.status === "ok" || p.status === "not_connected" ? "ok" : "blind");
/** a provider stopped for credit or a limit (requests failing now) → lost */
export const stop = (): St => "bad";

// ---- hosts ----
/** a sandbox host: booting → advisory; not answering with someone's sandbox on it, or the gateway (every sandbox's
 * network) → lost; an empty host not answering → caution */
export function host(x: J): St {
  if (x.status === "booting") return "info";
  if (!x.reachable) return x.gateway || x.sandboxes ? "bad" : "warn";
  return "ok";
}

// ---- Postgres, WiseSearch, object storage ----
/** connections at 80% of the limit or more → caution */
export const pgConnections = (c: J): St => (c.max && c.total >= 0.8 * c.max ? "warn" : "ok");
/** under the free-space floor → caution */
export const pgSpace = (sp: J): St => (typeof sp.free_bytes === "number" && sp.free_bytes < sp.floor_bytes ? "warn" : "ok");
/** WAL archiving or the base backup failing: the data less protected, nobody stopped → caution */
export const pgArchive = (a: J): St => (((a.problems || []) as string[]).length ? "warn" : "ok");
/** WiseSearch: logged out or its health check failed twice in a row (monitor.wisers_down) → lost; a few failed calls →
 * caution */
export const wisers = (w: J): St => (w.state === "logged_out" || w.down ? "bad" : ((w.problems || []) as string[]).length ? "warn" : "ok");
/** a part that could not be read (`{error}`) → blind */
export const unread = (x: J | null | undefined): St | null => (x && x.error ? "blind" : null);

// ---- Jarvis 4 (the wall's colours: needs you and idle amber; working ink; background, frozen, scheduled, booting blue) ----
export const JARVIS: Record<string, St> = { waiting: "warn", idle: "warn", working: "ink", background: "info", frozen: "info", scheduled: "info", booting: "info" };
export const jarvis = (state: string): St => JARVIS[state] ?? "ok";
/** the marker a session gets: idle shows its amber word without a block */
export const jarvisMarker = (state: string): St => (state === "idle" ? "ok" : jarvis(state));

/** the worst of several states (lost > blind > caution > advisory > normal) */
const RANK: St[] = ["ok", "ink", "info", "warn", "blind", "bad"];
export const worst = (xs: St[]): St => xs.reduce<St>((a, b) => (RANK.indexOf(b) > RANK.indexOf(a) ? b : a), "ok");
