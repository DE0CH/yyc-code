"""Every date and time YYC's Python code shows to a reader (reports, e-mails, alerts, sentences the pages show) is
formatted here, and only here: the Python twin of the edge app's web-app/src/lib/dates.ts, with the same named formats
and the same rules (Deyao, 2026-10-20: every page and message reads the same way). Always Hong Kong time, Traditional
Chinese with Hong Kong usage, weekday 「星期二」. tests/test_dates.py checks both give identical output for the same
instants, and that no other file formats a date.

Named formats (Tuesday 20 Oct 2026, 14:05:09 HKT, seen in 2026):
  date          10月20日（星期二）       (the year only when it is not this year)
  date_year     2026年10月20日（星期二）
  time          14:05
  time_sec      14:05:09
  date_time     10月20日（星期二）14:05  (a bare day shows as `date`)
  date_time_year 2026年10月20日（星期二）14:05
  when          14:05 today, else date_time
  axis          20/10                     (chart axes only)
  sortable      2026-10-20 14:05          (spreadsheet / CSV cells)

Input: an ISO string with a zone, a naive ISO string (taken as HKT), a bare day "YYYY-MM-DD", a datetime (naive =
HKT), a date (a bare day) or epoch milliseconds. Empty → ""; unreadable text comes back as is.

lang="en": the same formats in English for what only Deyao reads (the monitor's Discord alerts; dates.ts `dtEn` on his
English pages): "Tue 20 Oct 14:05", another year "Wed 31 Dec 2025"."""
import re
from datetime import date as _date, datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
WEEK = "一二三四五六日"  # Python's weekday(): Monday = 0
WEEK_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?)?$", re.I)
KINDS = ("date", "dateYear", "time", "timeSec", "dateTime", "dateTimeYear", "when", "axis", "sortable")


def _parts(t):
    """(HKT datetime, day_only) or None"""
    if isinstance(t, datetime):
        return (t.replace(tzinfo=HKT) if t.tzinfo is None else t).astimezone(HKT), False
    if isinstance(t, _date):
        return datetime(t.year, t.month, t.day, tzinfo=HKT), True
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return datetime.fromtimestamp(t / 1000, HKT), False
    m = _RE.match(str(t).strip())
    if not m:
        return None
    try:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if m[4] is None:
            return datetime(y, mo, d, tzinfo=HKT), True
        tz = HKT
        if m[7]:
            z = m[7].upper().replace(":", "")
            tz = timezone.utc if z == "Z" else timezone((-1 if z[0] == "-" else 1) * timedelta(hours=int(z[1:3]), minutes=int(z[3:5])))
        return datetime(y, mo, d, int(m[4]), int(m[5]), int(m[6] or 0), tzinfo=tz).astimezone(HKT), False
    except ValueError:
        return None


def _day_zh(p, year):
    return f"{str(p.year) + '年' if year else ''}{p.month}月{p.day}日（星期{WEEK[p.weekday()]}）"


def _day_en(p, year):
    return f"{WEEK_EN[p.weekday()]} {p.day} {MONTH_EN[p.month - 1]}" + (f" {p.year}" if year else "")


def format(kind, t, now=None, lang="zh"):
    """t in one of the named formats (the edge app's names: date, dateYear, time, timeSec, dateTime, dateTimeYear,
    when, axis, sortable); `now` (a datetime or epoch ms, default the clock) decides "this year" and "today" """
    if t is None or t == "":
        return ""
    got = _parts(t)
    if not got:
        return str(t)
    p, day_only = got
    n = _parts(now)[0] if now is not None else datetime.now(HKT)
    hm = f"{p.hour:02d}:{p.minute:02d}"
    other_year = p.year != n.year
    _day = _day_en if lang == "en" else _day_zh
    hm_ = " " + hm if lang == "en" else hm  # Tue 20 Oct 14:05 · 10月20日（星期二）14:05
    if kind == "date":
        return _day(p, other_year)
    if kind == "dateYear":
        return _day(p, True)
    if kind == "time":
        return "" if day_only else hm
    if kind == "timeSec":
        return "" if day_only else f"{hm}:{p.second:02d}"
    if kind == "dateTime":
        return _day(p, other_year) + ("" if day_only else hm_)
    if kind == "dateTimeYear":
        return _day(p, True) + ("" if day_only else hm_)
    if kind == "when":
        return hm if not day_only and p.date() == n.date() else _day(p, other_year) + ("" if day_only else hm_)
    if kind == "axis":
        return f"{p.day}/{p.month}"
    if kind == "sortable":
        return f"{p:%Y-%m-%d}" + ("" if day_only else " " + hm)
    raise ValueError(f"unknown date format {kind!r}")


def date(t, now=None): return format("date", t, now)
def date_year(t, now=None): return format("dateYear", t, now)
def time(t, now=None): return format("time", t, now)
def time_sec(t, now=None): return format("timeSec", t, now)
def date_time(t, now=None): return format("dateTime", t, now)
def date_time_year(t, now=None): return format("dateTimeYear", t, now)
def when(t, now=None): return format("when", t, now)
def axis(t, now=None): return format("axis", t, now)
def sortable(t, now=None): return format("sortable", t, now)
