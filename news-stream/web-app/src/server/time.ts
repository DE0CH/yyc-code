// Python datetime semantics the pages rely on: ISO strings, naive ones read as HKT (+08:00), offsets kept.
export const HKT_MIN = 8 * 60;

export interface Moment { ms: number; offset: number }  // offset in minutes east of UTC, as written (naive → HKT)

/** datetime.fromisoformat (3.11+) for the forms the corpus stores; naive → HKT; null when it can't be read */
export function parseIso(s: string | null | undefined): Moment | null {
  if (!s) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,6}))?)?)?(Z|[+-]\d{2}:?\d{2}(?::?\d{2})?)?$/.exec(s.trim());
  if (!m) return null;
  let offset = HKT_MIN;
  if (m[8]) {
    if (m[8] === "Z") offset = 0;
    else {
      const sign = m[8][0] === "-" ? -1 : 1, d = m[8].slice(1).replace(/:/g, "");
      offset = sign * (Number(d.slice(0, 2)) * 60 + Number(d.slice(2, 4)));
    }
  }
  const frac = m[7] ? Number((m[7] + "000").slice(0, 3)) : 0;
  const utc = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0), frac) - offset * 60_000;
  return { ms: utc, offset };
}

const pad = (n: number, w = 2) => String(Math.abs(n)).padStart(w, "0");

/** isoformat(timespec="minutes") in the moment's own offset */
export function isoMinutes(t: Moment): string {
  const d = new Date(t.ms + t.offset * 60_000);
  const off = t.offset === 0 ? "+00:00" : (t.offset < 0 ? "-" : "+") + pad(Math.floor(Math.abs(t.offset) / 60)) + ":" + pad(Math.abs(t.offset) % 60);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${off}`;
}

export function dayAdd(day: string, n: number): string {
  return new Date(Date.parse(day + "T00:00:00Z") + n * 864e5).toISOString().slice(0, 10);
}

export const hktNow = () => new Date(Date.now() + HKT_MIN * 60_000);
export const hktToday = () => hktNow().toISOString().slice(0, 10);

/** Python's sort key `x.get("published") or x.get("seen") or ""` compares strings */
export const pubKey = (x: { published?: string | null; seen?: string | null }) => x.published || x.seen || "";
