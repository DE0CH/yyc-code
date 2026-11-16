// Streamed AI answers (the AI server's NDJSON: {"t":"ping"} at once and every 5 s, then delta/step/… and done; web.py
// Stream). A spinner shows only while the stream is alive (Deyao, 2026-10-16, lib/Wait.tsx): no answer headers, or no
// line at all, for STALL_MS ends the request with an error the page shows, instead of a spinner turning forever.
export const STALL_MS = 15_000;
export const STALL_MSG = "15 秒沒有收到伺服器的回應，連線可能已中斷，請再試一次。";

/** the request ended because nothing arrived for STALL_MS (`shown`: the page shows its message as is) */
export class Stalled extends Error {
  shown = true;
  constructor() { super(STALL_MSG); this.name = "Stalled"; }
}

/** fetch, aborted with Stalled when the response headers take longer than STALL_MS; `init.signal` still aborts it */
export async function fetchLive(input: string, init: RequestInit = {}): Promise<Response> {
  const ctl = new AbortController(), outer = init.signal;
  let stalled = false;
  const stop = () => ctl.abort();
  if (outer) { if (outer.aborted) ctl.abort(); else outer.addEventListener("abort", stop); }
  const t = setTimeout(() => { stalled = true; ctl.abort(); }, STALL_MS);
  try {
    return await fetch(input, { ...init, signal: ctl.signal });
  } catch (e) {
    if (stalled) throw new Stalled();
    throw e;
  } finally {
    clearTimeout(t);
    outer?.removeEventListener("abort", stop);
  }
}

/** read NDJSON events: `on` gets each one but done (returned) and error (thrown); pings keep it alive */
export async function readNdjson<D>(r: Response, on?: (ev: { t: string; [k: string]: unknown }) => void): Promise<D> {
  if (!(r.headers.get("Content-Type") || "").includes("ndjson")) return r.json() as Promise<D>;
  const rd = r.body!.getReader(), dec = new TextDecoder();
  let buf = "", done: D | null = null;
  for (;;) {
    let t = 0;
    const stall = new Promise<"stall">(res => { t = setTimeout(() => res("stall"), STALL_MS) as unknown as number; });
    const got = await Promise.race([rd.read(), stall]).finally(() => clearTimeout(t));
    if (got === "stall") { rd.cancel().catch(() => {}); throw new Stalled(); }
    const { value, done: end } = got;
    if (value) buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.t === "error") throw new Error(ev.error || "AI 回應中斷");
      if (ev.t === "done") done = ev as D;
      else if (ev.t !== "ping" && on) on(ev);
    }
    if (end) break;
  }
  if (!done) throw new Error("AI 回應中斷，請再試一次。");
  return done;
}
