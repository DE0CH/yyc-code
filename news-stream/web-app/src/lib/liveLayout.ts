// The live dashboard's fixed layout and pacing (-LivePage.tsx). Pure, so tests/test_live_dashboard_page.py checks it in
// Node too.
//
// Fixed, not fitted (Deyao, 2026-10-21: "text size and layout should be fixed, and not dynamically adjusted since we
// know the screen layout"): each mode is drawn for one known viewport in px, every panel at a fixed box, every card a
// fixed height per panel (its text clamped inside), so a new card shifts its panel by exactly one card. Short panels
// of a few rich cards rather than long lists (Deyao: "a list doesn't need to be that long … it's just wasting
// space"): a grid of 3 columns x 2 rows per display. No panel crosses the seam between the two displays (Deyao: "a
// panel cannot span two displays"): the left window (1920 x 1200) and the right window (1920 x 1080) each lay out only
// their own panels inside x 0..1920.
// The news pipeline reads left to right, then YYC AI and the systems' health (Deyao, 2026-10-28: "watch live stuff for
// the AI system as well, and also health of systems … make some components smaller or delete a few … still the two
// screens"; Duplicates and Links followed were removed for them, Full text went from two columns to one):
//   left  1920 x 1200   Ingest | Full text | Comments
//                       Stage 1 (two columns wide: every topic's score and reason) | Stage 2
//   right 1920 x 1080   Filed | Alerts | Problems | Jarvis 4 (Deyao's sessions: working, idle, background; 2026-11-12)
//                       YYC AI (two columns wide: its state, then its activity as cards) | Systems (two columns wide:
//                       pods, hosts, balances, Postgres; at 640 px its lines did not fit and its bottom fell off)
//   one window 1440 x 830: every panel in a 5 x 3 grid (testing, a laptop)
//   phone (a window under 900 px wide, no ?half): the panels in one column, the page scrolls
import type { Card, PanelId } from "../server/live";

export type Mode = "left" | "right" | "single" | "phone";
/** a panel's box: the card panels, "sys" (the systems' health) and "jarvis" (Jarvis 4's sessions): no cards */
export type BoxId = PanelId | "sys" | "jarvis";
export interface Box { id: BoxId; x: number; y: number; w: number; h: number }
export interface Layout { w: number; h: number; panels: Box[] }

export const BAR = 16;      // the top line (clock, last poll)
export const HEADER = 20;   // a panel's header (name, counts)
// panels share their edges (Swiss clean lines, deyao-web-design): each box overlaps its neighbour by its 1px rule, so
// two panels side by side show one rule, never two parallel ones with ground between; no edge margin
const G = -1, E = 0;        // step between panels (−1: one shared rule), edge

export const PANEL_NAMES: Record<BoxId, string> = {
  ingest: "1 Ingest", fulltext: "2 Full text", comments: "3 Comments",
  stage1: "4 Stage 1", stage2: "5 Stage 2",  // + the stage's model from the live data (Models)
  filed: "6 Filed", alert: "7 Alerts", problem: "8 Problems", ai: "9 YYC AI", sys: "10 Systems", jarvis: "11 Jarvis 4",
};

/** each panel's card height (px): the card's lines at their fixed sizes, clamped inside */
export const CARD_H: Record<PanelId, number> = {
  ingest: 70, fulltext: 112, comments: 112,
  stage1: 100, stage2: 126, filed: 100, alert: 63, problem: 84, ai: 52,
};

/** YYC AI's panel: its state beside its cards (a wide box) or above them (a phone) */
export const AI_STATE_W = 620;   // the state column in a box at least AI_SIDE wide
export const AI_SIDE = 1100;
export const AI_STATE_H = 330;   // the state block above the cards (narrower boxes)
export function aiSplit(b: Box): { side: boolean; stateW: number; stateH: number } {
  return b.w >= AI_SIDE ? { side: true, stateW: AI_STATE_W, stateH: b.h - HEADER - 2 }
    : b.w >= 700 ? { side: true, stateW: Math.floor(b.w / 2), stateH: b.h - HEADER - 2 }
    : { side: false, stateW: b.w - 2, stateH: AI_STATE_H };
}

function grid(w: number, h: number, cols: number, rows: number, cells: (BoxId | null)[][], spans: Partial<Record<BoxId, number>> = {}): Layout {
  const cw = Math.floor((w - 2 * E - (cols - 1) * G) / cols);
  const rh = Math.floor((h - BAR - E - (rows - 1) * G) / rows);
  const panels: Box[] = [];
  cells.forEach((row, r) => {
    let c = 0;
    for (const id of row) {
      const span = (id && spans[id]) || 1;
      if (id) panels.push({ id, x: E + c * (cw + G), y: BAR + r * (rh + G), w: span * cw + (span - 1) * G, h: rh });
      c += span;
    }
  });
  return { w, h, panels };
}

/** a phone: one column as wide as the window, each panel a few cards high (fixed px like the rest), the page scrolls */
export const PHONE_MAX = 900;
const PHONE_ORDER: [BoxId, number][] = [  // panel, cards shown
  ["ai", 5], ["sys", 0], ["jarvis", 0], ["problem", 3], ["alert", 4], ["filed", 3], ["stage2", 2], ["stage1", 3], ["ingest", 4], ["fulltext", 2], ["comments", 2]];
export const SYS_PHONE_H = 800;

/** Jarvis 4's panel: one fixed-height row per session (two lines: its state and name, then how long and what runs
 * besides it), in columns of JARVIS_COL_W px or more, at most JARVIS_MAX sessions (Deyao, 2026-11-12: "allocate at
 * most 12-16 depending on the layout"); the router sends them most active first */
export const JARVIS_ROW_H = 31;
export const JARVIS_COL_W = 280;
export const JARVIS_MAX = 16;
export const JARVIS_PHONE_H = HEADER + 2 + JARVIS_MAX * JARVIS_ROW_H;
export function jarvisGrid(b: Box): { cols: number; rows: number; n: number } {
  const cols = Math.max(1, Math.floor((b.w - 2) / JARVIS_COL_W));
  const rows = Math.max(1, Math.floor((b.h - HEADER - 2) / JARVIS_ROW_H));
  const n = Math.min(JARVIS_MAX, cols * rows);
  return { cols: Math.min(cols, Math.ceil(n / rows)), rows: Math.min(rows, n), n };
}
export function phoneLayout(width: number): Layout {
  const w = Math.max(320, Math.min(PHONE_MAX, Math.floor(width)));
  let y = BAR;
  const panels: Box[] = PHONE_ORDER.map(([id, n]) => {
    const h = id === "sys" ? SYS_PHONE_H : id === "jarvis" ? JARVIS_PHONE_H : HEADER + 2 + n * CARD_H[id] + (id === "ai" ? AI_STATE_H : 0);
    const b = { id, x: E, y, w: w - 2 * E, h };
    y += h + G;
    return b;
  });
  return { w, h: y - G + E, panels };  // y is one step (G) past the last panel's bottom
}

export const LAYOUTS: Record<Exclude<Mode, "phone">, Layout> = {
  left: grid(1920, 1200, 3, 2, [["ingest", "fulltext", "comments"], ["stage1", "stage2"]], { stage1: 2 }),
  right: grid(1920, 1080, 4, 2, [["filed", "alert", "problem", "jarvis"], ["ai", "sys"]], { ai: 2, sys: 2 }),
  single: grid(1440, 830, 5, 3, [["ingest", "fulltext", "comments", "stage1", "stage2"], ["filed", "alert", "problem", "jarvis"], ["ai", "sys"]],
    { jarvis: 2, ai: 3, sys: 2 }),
};
export const HALF_OF: Record<BoxId, "left" | "right"> = Object.fromEntries([
  ...LAYOUTS.left.panels.map(b => [b.id, "left"]), ...LAYOUTS.right.panels.map(b => [b.id, "right"])]) as Record<BoxId, "left" | "right">;

/** how many cards a panel box shows (YYC AI: in the space its state leaves; Systems: none) */
export function cardsIn(b: Box): number {
  if (b.id === "sys" || b.id === "jarvis") return 0;
  const a = b.id === "ai" && !aiSplit(b).side ? AI_STATE_H : 0;
  return Math.max(1, Math.floor((b.h - HEADER - 2 - a) / CARD_H[b.id]));
}

export const POLL_MS = 3000;            // one read every 3 s
export const RATES_EVERY = 10;          // the header counts with every 10th read (30 s)
export const MIN_GAP = 300;             // one card at a time, at least this far apart (Deyao: "one new item at a time")
export const STALE_MS = 30_000;         // no successful read for 30 s: the screen greys out
export const POLL_STALE_MS = 12 * 60e3; // the poller's last poll older than 12 min (it polls every 5): greyed out too

const HK = 8 * 3600e3;
const pad = (n: number) => String(n).padStart(2, "0");
/** HH:MM:SS / HH:MM in Hong Kong time, whatever the display's time zone */
export const hhmmss = (t: number) => { const d = new Date(t + HK); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; };
export const hhmm = (t: number) => hhmmss(t).slice(0, 5);

// ---- paced: cards come on screen at the rate they arrive (Deyao, 2026-10-21: "the new items displaying in should be
// roughly similar to the rate it's coming in so it's not scrolling really fast and then pausing"; 2026-10-27: "the
// speed of new item popping in equal to the rate of which the content is going in, … the download eta algorithm") ----
// The pipeline works in batches (10 posts a stage-1 call, rounds a few minutes apart), so events reach the page in
// clumps. Each panel measures its arrival rate the way a download's speed (and ETA) is measured: an exponentially
// decaying rate (the estimator of Linux's load average and of metrics "meters"). Between arrivals it decays with
// time constant TAU, and n new cards add n / TAU, so it is the count of cards in a sliding, exponentially weighted
// window of about TAU, per ms. It is seeded with the server's own count of the last 10 minutes (rates[0], the panel's
// header), so it is right from the first read, then follows the data.
// The panel shows its queue at that rate: the next card goes on top 1 / rate after the previous one. Every queued card
// is due on screen MAX_WAIT after it arrived: when the rate would miss a card's deadline (the rate measured too low, a
// burst), the queue goes just fast enough to meet them all, never faster than one card per MIN_GAP (followable). An
// idle panel shows a new card at once.
// Over any stretch longer than TAU the cards go on screen as fast as they come in. "+N behind" counts the cards that
// even MIN_GAP can't show within MAX_WAIT; nothing is dropped.
export interface Pace {
  queue: Card[];
  arrived: number[];  // when each queued card arrived (ms), in queue order
  last: number;       // the newest id queued or shown
  rate: number;       // the decaying arrival rate (cards per ms) at `at`
  at: number;         // when `rate` was last brought up to date (ms; 0: not yet)
  shownAt: number;    // when the last card went on screen (ms; 0: none yet)
}
export const TAU = 600_000;             // the rate's time constant: 10 min (several pipeline rounds)
export const MAX_WAIT = 120_000;        // a queued card is on screen within about 2 min
export const SEED_WINDOW = 600_000;     // rates[0] counts the last 10 minutes

/** a panel's pace; `per10min` = the server's count of the last 10 minutes (seeds the rate) */
export const newPace = (shown: Card[], per10min = 0): Pace =>
  ({ queue: [], arrived: [], last: Math.max(0, ...shown.map(c => c.id)), rate: per10min / SEED_WINDOW, at: 0, shownAt: 0 });

/** the decaying rate brought up to `now` */
export const rateAt = (p: Pace, now: number) => p.at ? p.rate * Math.exp(-Math.max(0, now - p.at) / TAU) : p.rate;

/** the time between two cards going on screen now (ms) */
export function gapAt(p: Pace, now: number): number {
  let r = rateAt(p, now);
  // card i (0 = next) needs i + 1 cards shown before its deadline
  p.arrived.forEach((a, i) => { r = Math.max(r, (i + 1) / Math.max(1, a + MAX_WAIT - now)); });
  return r > 0 ? Math.max(MIN_GAP, 1 / r) : MAX_WAIT;
}

/** a read's cards for this panel (any order) → the pace with the new ones queued, oldest first, and their arrival
 * counted into the rate */
export function enqueue(p: Pace, got: Card[], now: number): Pace {
  const fresh = got.filter(c => c.id > p.last).sort((a, b) => a.id - b.id);
  if (!fresh.length) return p;
  const queue = [...p.queue, ...fresh];
  return { ...p, queue, arrived: [...p.arrived, ...fresh.map(() => now)], last: queue[queue.length - 1].id, rate: rateAt(p, now) + fresh.length / TAU, at: now };
}

/** at `now`: the card due to go on top (or null) and the pace after it */
export function step(p: Pace, now: number): [Card | null, Pace] {
  if (!p.queue.length || (p.shownAt && now - p.shownAt < gapAt(p, now))) return [null, p];
  const [c, ...rest] = p.queue;
  return [c, { ...p, queue: rest, arrived: p.arrived.slice(1), shownAt: now }];
}

/** cards still waiting beyond what MIN_GAP can show within MAX_WAIT */
export const behind = (p: Pace) => Math.max(0, p.queue.length - Math.floor(MAX_WAIT / MIN_GAP));

/** → null while the data is current, else since when it is not and why (the page greys out) */
export function stale(now: number, okAt: number, lastPoll: number | null): { since: number; why: string } | null {
  if (okAt && now - okAt > STALE_MS) return { since: okAt, why: "the dashboard can't read the database" };
  if (lastPoll && now - lastPoll > POLL_STALE_MS) return { since: lastPoll, why: "the poller has not polled" };
  return null;
}
