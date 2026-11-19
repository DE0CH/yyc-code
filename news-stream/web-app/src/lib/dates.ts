// Every date and time the site shows is formatted here, and only here (Deyao, 2026-10-20: "why do some pages say
// (週二) but some don't? … They should [use the same template]"). Always Hong Kong time (HKT, +08:00), always
// Traditional Chinese with Hong Kong usage; the weekday is 「星期二」 (YYC/CLAUDE.md lists 星期五 as HK usage).
// Deyao's own pages (the monitor, the proof view, the data API's approvals) are in English (Deyao, 2026-10-21) and use
// `dtEn`: the same named formats and rules in English, "Tue 20 Oct 14:05" (the year when it is not this year:
// "Mon 20 Oct 2025"). Only the edge app's pages use it, so datefmt.py has no English side.
// The Python side (messages, reports, server-made sentences) is news-stream/datefmt.py, the same rules;
// tests/test_dates.py checks both give identical output for the same instants, and that no other file formats a date.
//
// The named formats (examples for Tuesday 20 Oct 2026, 14:05:09 HKT, seen in 2026):
//   date          10月20日（星期二）            the year only when it is not this year: 2025年10月20日（星期一）
//   dateYear      2026年10月20日（星期二）
//   time          14:05
//   timeSec       14:05:09
//   dateTime      10月20日（星期二）14:05       a bare day (the source gave no time) shows as `date`
//   dateTimeYear  2026年10月20日（星期二）14:05
//   when          14:05 today, else dateTime    (as 「最遲 14:05」, a status line, a list of runs)
//   axis          20/10                          chart axes only, where a weekday doesn't fit
//   sortable      2026-10-20 14:05               spreadsheet and CSV cells (sortable text), still HKT
//   onDay(t, day) 14:05 on that day, else 10月19日 23:40   rows under a day picker (串流; 數據分析 showing one day): the
//                 picker already says the day (Deyao, 2026-10-20); a bare day: "" on that day, else 10月19日
//
// Input: an ISO time with a zone (Z, +08:00, +0800), a naive ISO time (taken as HKT: the corpus stores HKT), a bare
// day "YYYY-MM-DD" (a date without a time), a Date or epoch milliseconds. Empty → ""; unreadable text comes back as is.
// Formatted by hand, never with Intl or toLocale*: the server (V8) and an iPhone (WebKit) carry different ICU data,
// and a page rendered on the server must read the same once the browser takes over (hydration).
import { clock } from "./clock.ts";  // with its extension: tests load this file in Node

export type When = string | number | Date | null | undefined;
export type Kind = "date" | "dateYear" | "time" | "timeSec" | "dateTime" | "dateTimeYear" | "when" | "axis" | "sortable" | "monthDay";

const HK = 8 * 3600e3;
const WEEK = "日一二三四五六";
const two = (n: number) => String(n).padStart(2, "0");
const RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?)?$/i;

interface Parts { y: number; m: number; d: number; wd: number; hh: number; mi: number; ss: number; dayOnly: boolean }

function fromMs(ms: number, dayOnly = false): Parts {
  const h = new Date(ms + HK);
  return { y: h.getUTCFullYear(), m: h.getUTCMonth() + 1, d: h.getUTCDate(), wd: h.getUTCDay(), hh: h.getUTCHours(),
    mi: h.getUTCMinutes(), ss: h.getUTCSeconds(), dayOnly };
}

/** the HKT parts of a time, or null when it can't be read */
function parts(t: When): Parts | null {
  if (t instanceof Date) return isNaN(+t) ? null : fromMs(+t);
  if (typeof t === "number") return isFinite(t) ? fromMs(t) : null;
  const m = RE.exec(String(t).trim());
  if (!m) return null;
  const [y, mo, d] = [+m[1], +m[2], +m[3]];
  if (new Date(Date.UTC(y, mo - 1, d)).getUTCDate() !== d || mo < 1 || mo > 12) return null;  // 2026-02-30: not a day
  if (m[4] == null) return fromMs(Date.UTC(y, mo - 1, d) - HK, true);
  if (+m[4] > 23 || +m[5] > 59 || +(m[6] || 0) > 59) return null;
  let off = HK;
  if (m[7]) {
    const z = m[7].toUpperCase().replace(":", "");
    off = z === "Z" ? 0 : (z[0] === "-" ? -1 : 1) * (+z.slice(1, 3) * 3600e3 + +z.slice(3, 5) * 60e3);
  }
  const ms = Date.UTC(y, mo - 1, d, +m[4], +m[5], +(m[6] || 0)) - off;
  return isNaN(ms) ? null : fromMs(ms);
}

export type Lang = "zh" | "en";
const WEEK_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayZh = (p: Parts, year: boolean) => `${year ? p.y + "年" : ""}${p.m}月${p.d}日（星期${WEEK[p.wd]}）`;
const dayEn = (p: Parts, year: boolean) => `${WEEK_EN[p.wd]} ${p.d} ${MONTH_EN[p.m - 1]}${year ? " " + p.y : ""}`;
const hm = (p: Parts) => `${two(p.hh)}:${two(p.mi)}`;

/** format a time in one of the named formats; `now` (epoch ms, default the clock) decides "this year" and "today" */
export function format(kind: Kind, t: When, now: number = clock(), lang: Lang = "zh"): string {
  if (t == null || t === "") return "";
  const p = parts(t);
  if (!p) return String(t);
  const n = fromMs(now);
  const en = lang === "en";
  const dayText = en ? dayEn : dayZh;
  const sep = en ? " " : "";  // 10月20日（星期二）14:05 · Tue 20 Oct 14:05
  const ymd = (x: Parts) => `${x.y}-${two(x.m)}-${two(x.d)}`;
  switch (kind) {
    case "date": return dayText(p, p.y !== n.y);
    case "dateYear": return dayText(p, true);
    case "time": return p.dayOnly ? "" : hm(p);
    case "timeSec": return p.dayOnly ? "" : `${hm(p)}:${two(p.ss)}`;
    case "dateTime": return dayText(p, p.y !== n.y) + (p.dayOnly ? "" : sep + hm(p));
    case "dateTimeYear": return dayText(p, true) + (p.dayOnly ? "" : sep + hm(p));
    case "when": return !p.dayOnly && ymd(p) === ymd(n) ? hm(p) : dayText(p, p.y !== n.y) + (p.dayOnly ? "" : sep + hm(p));
    case "axis": return `${p.d}/${p.m}`;
    case "sortable": return ymd(p) + (p.dayOnly ? "" : " " + hm(p));
    case "monthDay": return `${two(p.m)}-${two(p.d)}`;  // 11-19: the live dashboard's and health page's cells
  }
}

/** the named formats as functions: dt.date(x), dt.dateTime(x), … */
export const dt = {
  date: (t: When, now?: number) => format("date", t, now),
  dateYear: (t: When, now?: number) => format("dateYear", t, now),
  time: (t: When, now?: number) => format("time", t, now),
  timeSec: (t: When, now?: number) => format("timeSec", t, now),
  dateTime: (t: When, now?: number) => format("dateTime", t, now),
  dateTimeYear: (t: When, now?: number) => format("dateTimeYear", t, now),
  when: (t: When, now?: number) => format("when", t, now),
  axis: (t: When, now?: number) => format("axis", t, now),
  sortable: (t: When, now?: number) => format("sortable", t, now),
  monthDay: (t: When, now?: number) => format("monthDay", t, now),
  onDay,
};

/** the same named formats in English, for Deyao's own pages: dtEn.dateTime(x) = "Tue 20 Oct 14:05" */
export const dtEn = {
  date: (t: When, now?: number) => format("date", t, now, "en"),
  dateYear: (t: When, now?: number) => format("dateYear", t, now, "en"),
  time: (t: When, now?: number) => format("time", t, now, "en"),
  timeSec: (t: When, now?: number) => format("timeSec", t, now, "en"),
  dateTime: (t: When, now?: number) => format("dateTime", t, now, "en"),
  dateTimeYear: (t: When, now?: number) => format("dateTimeYear", t, now, "en"),
  when: (t: When, now?: number) => format("when", t, now, "en"),
  axis: (t: When, now?: number) => format("axis", t, now, "en"),
  sortable: (t: When, now?: number) => format("sortable", t, now, "en"),
  monthDay: (t: When, now?: number) => format("monthDay", t, now, "en"),
};

/** a row under a day picker that shows `day` ("YYYY-MM-DD", HKT): the time only when `t` falls on that day (a bare day
 *  on it: ""), else the shortest form that can't be misread, 「10月19日 23:40」 (bare: 「10月19日」; the year when it is
 *  not this year). Deyao, 2026-10-20: "no need to show the date when there's already a date picker at the top" */
export function onDay(t: When, day: string, now: number = clock()): string {
  if (t == null || t === "") return "";
  const p = parts(t);
  if (!p) return String(t);
  if (`${p.y}-${two(p.m)}-${two(p.d)}` === day) return p.dayOnly ? "" : hm(p);
  return `${p.y !== fromMs(now).y ? p.y + "年" : ""}${p.m}月${p.d}日` + (p.dayOnly ? "" : " " + hm(p));
}

/** the HKT day "YYYY-MM-DD" a time falls on ("" when it can't be read): grouping, not display */
export function hkDayOf(t: When): string {
  const p = t == null || t === "" ? null : parts(t);
  return p ? `${p.y}-${two(p.m)}-${two(p.d)}` : "";
}

/** the start of the half-hour an instant falls in (HKT's offset is whole hours, so UTC half-hours are HKT's) */
export const halfHour = (d: Date) => new Date(Math.floor(+d / 1800e3) * 1800e3).toISOString();

/** 0 = Sunday … 6 = Saturday, for a day "YYYY-MM-DD" (week arithmetic) */
export const weekday = (day: string) => new Date(day + "T00:00:00Z").getUTCDay();
