// Day arithmetic: days are HKT calendar days "YYYY-MM-DD". Showing a date or time = lib/dates.ts, nothing else.
import { clock } from "./clock.ts";  // with its extension: tests load this file in Node

export const dayStart = (d: string) => new Date(d + "T00:00:00+08:00");
export const dayAdd = (d: string, n: number) => new Date(Date.parse(d + "T12:00:00Z") + n * 864e5).toISOString().slice(0, 10);
/** the HKT day now, whatever the browser's time zone */
export const hkToday = () => new Date(clock() + 8 * 3600e3).toISOString().slice(0, 10);
/** a response's "today", never older than the HKT day now (a cache's copy from an earlier day says that day) */
export const today = (t?: string) => { const c = hkToday(); return t && t > c ? t : c; };
export const isDay = (s: unknown): s is string => typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s);

export const fmtN = (n: number | null | undefined) => String(Math.round(Number(n || 0))).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
