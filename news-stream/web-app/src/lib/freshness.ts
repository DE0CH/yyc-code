// Is 串流 current? (Deyao, 2026-10-15: "stuck at an old time … and it still tells me it's live"; NAVIGATION.md
// "串流: is it current?": keep the two in step.) 即時更新 only while the last read of today's
// feed succeeded within 2 refresh intervals AND the server's last check of its sources (last_poll) is within 2 poll
// intervals, measured on the server's clock (the feed's served_at; a phone's clock can be off). Otherwise the label
// says when the data is from and why it isn't current.
import { dt } from "./dates";

export const EVERY = 60_000;    // today's feed is read every minute…
export const RETRY = 15_000;    // …and every 15 s while it isn't current
export const TIMEOUT = 15_000;  // a read with no answer by then has failed
const POLL_MS = 5 * 60_000;     // the poller's interval (app.py POLL_SECONDS)

export type FreshState = "live" | "updating" | "offline" | "failed" | "signedout" | "behind";

export interface FreshInput {
  okAt: number;           // device time of the last successful read (0: none)
  failed: "" | "failed" | "signedout";  // the last read's failure, when it failed after okAt
  online: boolean;
  lastPoll: string | null;
  skew: number;           // server clock − device clock
  now: number;            // device clock
}

export function freshState(i: FreshInput): FreshState {
  const lp = i.lastPoll ? Date.parse(i.lastPoll) : NaN;
  const recent = !i.failed && i.okAt > 0 && i.now - i.okAt < 2 * EVERY;
  const current = lp > 0 && i.now + i.skew - lp < 2 * POLL_MS + 2 * EVERY;
  if (!i.online) return "offline";
  if (recent && current) return "live";
  if (i.failed) return i.failed;
  return recent ? "behind" : "updating";
}

const TEXT: Record<Exclude<FreshState, "live">, [string, string]> = {
  updating: ["更新中", "正在讀取最新資料"],
  offline: ["離線", "沒有網絡連線：恢復連線後會自動更新"],
  failed: ["未能更新", "伺服器沒有回應：會自動重試"],
  signedout: ["登入已過期", "登入已過期：重新開啟頁面並登入後會再更新"],
  behind: ["伺服器未有新資料", "伺服器最近沒有檢查來源：有新資料時會自動顯示"],
};

/** the label's parts: `at` = when the data is from (HKT; the day too when not today), `why` = why it isn't current */
export function freshLabel(st: FreshState, lastPoll: string | null) {
  const at = lastPoll && !isNaN(Date.parse(lastPoll)) ? dt.when(lastPoll) : "—";
  if (st === "live") return { at, why: "", title: `每分鐘自動更新；伺服器 ${at} 檢查了來源` };
  return { at, why: TEXT[st][0], title: `顯示的是 ${at} 的資料。${TEXT[st][1]}` };
}

/** a promise that fails after ms (a read that never answers must not keep the page waiting) */
export function withTimeout<T>(p: Promise<T>, ms = TIMEOUT): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    p.then(v => { clearTimeout(t); resolve(v); }, e => { clearTimeout(t); reject(e); });
  });
}
