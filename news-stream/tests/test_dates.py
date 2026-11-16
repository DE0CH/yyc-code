"""One date format everywhere (Deyao, 2026-10-20: "why do some pages say (週二) but some don't? They don't use the same
template? They should."). The edge app formats every date and time with web-app/src/lib/dates.ts and the Python side
with datefmt.py: HKT, Traditional Chinese with Hong Kong usage, weekday 「星期二」 (not 周/週).

A: the named formats give the agreed text. B: the TypeScript and Python formatters give identical output for the
same instants. C: no other file of the site, the MCP server or the Python code that writes readers' text formats a
date or time by itself (toLocale*, Intl, slicing an ISO string, building 月/日 by hand, a weekday table)."""
import json, os, re, shutil, subprocess, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import datefmt  # noqa: E402

NOW = "2026-10-20T15:00:00+08:00"   # a Tuesday
NOW_MS = 1792479600000
assert datefmt._parts(NOW_MS)[0].isoformat() == "2026-10-20T15:00:00+08:00"

EXPECT = [
    ("date", "2026-10-20T14:05:09+08:00", "10月20日（星期二）"),
    ("dateYear", "2026-10-20T14:05:09+08:00", "2026年10月20日（星期二）"),
    ("time", "2026-10-20T14:05:09+08:00", "14:05"),
    ("timeSec", "2026-10-20T14:05:09+08:00", "14:05:09"),
    ("dateTime", "2026-10-20T14:05:09+08:00", "10月20日（星期二）14:05"),
    ("dateTimeYear", "2026-10-20T14:05:09+08:00", "2026年10月20日（星期二）14:05"),
    ("when", "2026-10-20T14:05:09+08:00", "14:05"),
    ("when", "2026-10-19T23:59+08:00", "10月19日（星期一）23:59"),
    ("axis", "2026-10-20T14:05:09+08:00", "20/10"),
    ("sortable", "2026-10-20T14:05:09+08:00", "2026-10-20 14:05"),
    ("dateTime", "2026-10-20T06:05:09Z", "10月20日（星期二）14:05"),          # UTC → HKT
    ("dateTime", "2026-10-19T16:30:00Z", "10月20日（星期二）00:30"),          # crosses midnight into HKT's day
    ("dateTime", "2026-10-20T14:05", "10月20日（星期二）14:05"),              # naive = HKT
    ("dateTime", "2026-10-20 14:05:00", "10月20日（星期二）14:05"),
    ("dateTime", "2026-10-20T14:05:09.123456+0800", "10月20日（星期二）14:05"),
    ("dateTime", "2026-10-20T01:05:00-05:00", "10月20日（星期二）14:05"),
    ("dateTime", "2026-10-20", "10月20日（星期二）"),                         # a bare day: no clock time
    ("time", "2026-10-20", ""),
    ("when", "2026-10-20", "10月20日（星期二）"),
    ("sortable", "2026-10-20", "2026-10-20"),
    ("date", "2025-12-31T23:00+08:00", "2025年12月31日（星期三）"),           # another year shows its year
    ("dateTime", "2027-01-01T08:00+08:00", "2027年1月1日（星期五）08:00"),
    ("date", "2026-03-22", "3月22日（星期日）"),
    ("date", None, ""), ("date", "", ""),
    ("date", "not a time", "not a time"), ("date", "2026-02-30", "2026-02-30"), ("time", "2026-10-20T25:00", "2026-10-20T25:00"),
    ("dateTime", 1792479600000, "10月20日（星期二）15:00"),                   # epoch ms
]


def ts_formats(cases, now_ms):
    prog = ('const m = await import("./src/lib/dates.ts"); let s = ""; for await (const b of process.stdin) s += b;'
            'const c = JSON.parse(s);'
            'console.log(JSON.stringify(c.map(([k, t]) => m.format(k, t, ' + str(now_ms) + '))));')
    r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", prog,
                        ], input=json.dumps(cases), cwd=os.path.join(HERE, "web-app"), capture_output=True, text=True,
                       timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class A_Formats(unittest.TestCase):
    def test_python(self):
        for kind, t, want in EXPECT:
            self.assertEqual(datefmt.format(kind, t, NOW), want, (kind, t))

    def test_typescript(self):
        got = ts_formats([[k, t] for k, t, _ in EXPECT], NOW_MS)
        for (kind, t, want), g in zip(EXPECT, got):
            self.assertEqual(g, want, (kind, t))

    def test_python_names(self):
        self.assertEqual(datefmt.date_time("2026-10-20T14:05+08:00", NOW), "10月20日（星期二）14:05")
        self.assertEqual(datefmt.date_time_year("2026-10-20", NOW), "2026年10月20日（星期二）")


# Deyao's own pages (the monitor, the proof view, the data API's approvals) are in English: dtEn, the same formats
EXPECT_EN = [
    ("date", "2026-10-20T14:05:09+08:00", "Tue 20 Oct"),
    ("dateYear", "2026-10-20T14:05:09+08:00", "Tue 20 Oct 2026"),
    ("time", "2026-10-20T14:05:09+08:00", "14:05"),
    ("timeSec", "2026-10-20T14:05:09+08:00", "14:05:09"),
    ("dateTime", "2026-10-20T14:05:09+08:00", "Tue 20 Oct 14:05"),
    ("dateTimeYear", "2026-10-20T14:05:09+08:00", "Tue 20 Oct 2026 14:05"),
    ("when", "2026-10-20T14:05:09+08:00", "14:05"),
    ("when", "2026-10-19T23:59+08:00", "Mon 19 Oct 23:59"),
    ("dateTime", "2026-10-19T16:30:00Z", "Tue 20 Oct 00:30"),
    ("dateTime", "2026-10-20", "Tue 20 Oct"),
    ("date", "2025-12-31T23:00+08:00", "Wed 31 Dec 2025"),
    ("dateTime", "2027-01-01T08:00+08:00", "Fri 1 Jan 2027 08:00"),
    ("axis", "2026-10-20T14:05:09+08:00", "20/10"),
    ("sortable", "2026-10-20T14:05:09+08:00", "2026-10-20 14:05"),
    ("date", None, ""), ("date", "not a time", "not a time"),
]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class A_English(unittest.TestCase):
    def test_typescript_en(self):
        prog = ('const m = await import("./src/lib/dates.ts"); let s = ""; for await (const b of process.stdin) s += b;'
                'console.log(JSON.stringify(JSON.parse(s).map(([k, t]) => [m.format(k, t, ' + str(NOW_MS) + ', "en"),'
                ' m.dtEn[k](t, ' + str(NOW_MS) + ')])));')
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", prog],
                           input=json.dumps([[k, t] for k, t, _ in EXPECT_EN]), cwd=os.path.join(HERE, "web-app"),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        for (kind, t, want), (got, named) in zip(EXPECT_EN, json.loads(r.stdout)):
            self.assertEqual((got, named), (want, want), (kind, t))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class B_Parity(unittest.TestCase):
    def test_same_output_for_the_same_instants(self):
        # every half hour across two years (DST-free HKT, leap day, year ends) in each zone notation, and bare days
        base = 1767225600000 - 8 * 3600e3  # 2026-01-01T00:00 HKT
        ts = []
        for i in range(0, 2 * 365 * 48, 37):
            ms = int(base + i * 1800e3 - 400 * 864e5)
            t = datefmt._parts(ms)[0]
            ts += [ms, t.isoformat(), t.astimezone(datefmt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   t.strftime("%Y-%m-%dT%H:%M"), t.strftime("%Y-%m-%d")]
        cases = [[k, t] for t in ts for k in datefmt.KINDS]
        for now in (NOW_MS, 1798732800000):  # and from 2027-01-01 08:00 HKT
            py = [datefmt.format(k, t, now) for k, t in cases]
            self.assertEqual(ts_formats(cases, now), py)

    def test_english_same_output(self):
        prog = ('const m = await import("./src/lib/dates.ts"); let s = ""; for await (const b of process.stdin) s += b;'
                'console.log(JSON.stringify(JSON.parse(s).map(([k, t]) => m.format(k, t, ' + str(NOW_MS) + ', "en"))));')
        cases = [[k, t] for _, t, _ in EXPECT_EN for k in datefmt.KINDS if t is not None]
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", prog],
                           input=json.dumps(cases), cwd=os.path.join(HERE, "web-app"), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(json.loads(r.stdout), [datefmt.format(k, t, NOW, lang="en") for k, t in cases])


# pages with a day picker (串流; 數據分析 showing one day): rows show only the time when it falls on the chosen day,
# else the shortest unambiguous form 「10月19日 23:40」 (Deyao, 2026-10-20: "no need to show the date when there's
# already a date picker at the top of the page")
ON_DAY = [
    ("2026-10-20T08:58:00+08:00", "2026-10-20", "08:58"),
    ("2026-10-20T00:30:00Z", "2026-10-20", "08:30"),
    ("2026-10-19T23:40:00+08:00", "2026-10-20", "10月19日 23:40"),        # filed today, posted yesterday
    ("2026-10-19T16:30:00Z", "2026-10-20", "00:30"),                        # UTC evening = HKT's chosen day
    ("2026-10-20", "2026-10-20", ""),                                       # a date-only post on that day: the picker says it
    ("2026-10-18", "2026-10-20", "10月18日"),                               # a date-only post of another day
    ("2025-12-31T23:00:00+08:00", "2026-10-20", "2025年12月31日 23:00"),    # another year
    (None, "2026-10-20", ""),
]


@unittest.skipUnless(shutil.which("node"), "node not installed")
class D_OnDay(unittest.TestCase):
    def test_on_day(self):
        prog = ('const m = await import("./src/lib/dates.ts"); let s = ""; for await (const b of process.stdin) s += b;'
                'console.log(JSON.stringify(JSON.parse(s).map(([t, d]) => m.dt.onDay(t, d, ' + str(NOW_MS) + '))));')
        r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", prog],
                           input=json.dumps([[t, d] for t, d, _ in ON_DAY]), cwd=os.path.join(HERE, "web-app"),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        for (t, d, want), got in zip(ON_DAY, json.loads(r.stdout)):
            self.assertEqual(got, want, (t, d))


# what counts as formatting a date by hand (outside the two formatters)
TS_BANNED = [
    (r"toLocale(Date|Time)String|Intl\.DateTimeFormat|toLocaleString\([^)]*(timeZone|hour|month|weekday)", "toLocale/Intl"),
    (r"\.replace\(\s*[\"']T[\"']\s*,\s*[\"'] [\"']\s*\)", "ISO string with T → space"),
    (r"\.slice\(\s*(0\s*,\s*16|11\s*,\s*16|5\s*,\s*16|11\s*,\s*19|5\s*,\s*10|5)\s*\)", "a slice of an ISO time"),
    (r"get(UTC)?(Hours|Minutes|Seconds|Day)\(\)", "clock fields of a Date"),
    (r"[年月]\$\{|\}[年月日]|\+\s*[\"'][年月日]", "年/月/日 built by hand"),
    (r"星期[一二三四五六日]|[週周][一二三四五六日]|[週周]\$\{|星期\$\{|日一二三四五六", "a weekday"),
]
PY_BANNED = [
    (r"(?<!T)%H:%M|%-d|%-m|%F %T|strftime\([^)]*[年月]", "strftime for a reader"),
    (r"\[\s*(:16|11:16|5:16|:19|11:19|5:10)\s*\]", "a slice of an ISO time"),
    (r"\.replace\(\s*[\"']T[\"']\s*,\s*[\"'] [\"']\s*\)", "ISO string with T → space"),
    (r"星期[一二三四五六日]|[週周][一二三四五六日]", "a weekday"),
]
# lines that only look like formatting (reading, keys, logs to a terminal), each with why
ALLOW = [
    ("web-app/src/server/search/search.ts", "getUTCDate()", "validates a typed day"),
    ("web-app/src/server/dashboard.ts", "getUTCDate()", "days in a month"),
    ("web-app/src/server/time.ts", "", "ISO strings for the API (parseIso / isoMinutes), not display"),
    ("web-app/src/server/monitor.ts", "+08:00", "the API's ISO time"),
    ("mcp-edge/src/tools.ts", "", "tools return ISO times (data for an AI), not display"),
    ("sources.py", "", "reads the sources' own dates"), ("sources_extra.py", "", "reads the sources' own dates"),
    ("tikhub_fetch.py", "", "reads the platforms' dates"), ("cache_fetch.py", "", "reads the pages' dates"),
    ("replies_fetch.py", "strptime", "reads the forum's dates"), ("broadcast_fetch.py", "strptime", "reads the schedule"),
    ("broadcast_fetch.py", "星期六問責", "a programme's name"),
    ("reports.py", "%F %T", "terminal log"),
    ("reports.py", "fresh = ", "an ISO bound for a comparison"), ("reports.py", "start <= (x.get", "an ISO comparison"),
    ("reports.py", ">= cut[:16]", "an ISO comparison"), ("reports.py", ">= fresh]", "an ISO comparison"),
    ("insights.py", "first[:10]", "ISO days for the API"),
    ("thin.py", "isoformat(timespec", "an ISO time for the API"),
]
# Deyao's own terminal tools and the readers of the sources' dates are not readers' text
PY_SKIP_DIRS = {"tests", "infra", "deploy", "docs", "skills", "tools", "web-app", "mcp-edge", "whatsapp",
                "wisers", "edge", "node_modules", "__pycache__"}
TS_SKIP = {"web-app/src/lib/dates.ts", "web-app/src/routeTree.gen.ts"}


def _allowed(rel, line):
    if rel.endswith(".py") and ("print(" in line or "hexdigest()" in line):  # terminal output; a hash
        return True
    return any(rel == f and (s in line) for f, s, _ in ALLOW)


class C_NoOtherFormatting(unittest.TestCase):
    def scan(self, files, banned):
        bad = []
        for rel in files:
            with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
                lines = fh.readlines()
            for n, line in enumerate(lines, 1):
                if rel.endswith((".ts", ".tsx")) and line.lstrip().startswith(("//", "*", "/*")):
                    continue  # a comment
                code = line.split("//")[0] if rel.endswith((".ts", ".tsx")) and "://" not in line else line
                if rel.endswith(".py"):
                    code = re.sub(r"#.*$", "", line) if not re.search(r"[\"'][^\"']*#", line) else line
                    if code.lstrip().startswith(('"""', "'''")):
                        continue
                for pat, what in banned:
                    if re.search(pat, code) and not _allowed(rel, line):
                        bad.append(f"{rel}:{n}: {what}: {line.strip()[:140]}")
        return bad

    def test_typescript(self):
        files = []
        for top in ("web-app/src", "mcp-edge/src"):
            for d, dirs, fs in os.walk(os.path.join(HERE, top)):
                dirs[:] = [x for x in dirs if x != "node_modules"]
                files += [os.path.relpath(os.path.join(d, f), HERE) for f in fs if f.endswith((".ts", ".tsx"))]
        bad = self.scan([f for f in sorted(files) if f not in TS_SKIP], TS_BANNED)
        self.assertEqual(bad, [], "format dates with web-app/src/lib/dates.ts:\n" + "\n".join(bad))

    def test_python(self):
        files = []
        for d, dirs, fs in os.walk(HERE):
            dirs[:] = [x for x in dirs if x not in PY_SKIP_DIRS]
            files += [os.path.relpath(os.path.join(d, f), HERE) for f in fs if f.endswith(".py")]
        bad = self.scan([f for f in sorted(files) if f != "datefmt.py"], PY_BANNED)
        self.assertEqual(bad, [], "format dates with datefmt.py:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
