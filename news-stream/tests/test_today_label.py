#!/usr/bin/env python3
"""串流's day row says 今日 on exactly one day: the HKT day now (Deyao, 15 Oct 2026: "the date picker is a bit buggy,
it shows today for two days").

The cause: the edge Worker (edge/worker.js) kept `feed.json?date=<day>` copies stored while that day was still today.
Their `today` field is that day, so the next morning ‹ to yesterday showed "10月14日 週三 · 今日", hid 回到今日 and
disabled ›. (Live on 15 Oct 2026 08:37 HKT: feed.json?date=2026-10-14 → X-Edge: HIT, age 75782 s, today 2026-10-14.)
The pages never take a response's `today` as older than the HKT day of the browser's clock, whatever the browser's
time zone. The edge app (web-app/) is server-rendered with uncached server functions; this checks the day helpers it
shares (lib/time.ts, server/time.ts) in Node under Europe/London and Asia/Hong_Kong, the clock fixed at several
instants, above all 00:00–08:00 HKT when London's calendar day is still the day before.

    python3 tests/test_today_label.py
"""
import json, os, subprocess, unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "web-app")
HKT = timezone(timedelta(hours=8))

# instants (HKT): just after midnight, the last minute before London's midnight passes (07:59 HKT = 00:59 BST,
# 23:59 GMT in winter), after it, the afternoon, the last minutes of the day
INSTANTS = ["2026-10-15T00:30:00+08:00", "2026-10-15T07:59:00+08:00", "2026-10-15T08:30:00+08:00",
            "2026-10-15T15:00:00+08:00", "2026-10-15T23:50:00+08:00", "2026-12-01T06:00:00+08:00"]
ZONES = ["Europe/London", "Asia/Hong_Kong"]


class TodayLabel(unittest.TestCase):
    def test_react_app(self):
        """the edge app (web-app/, TanStack Start: server-rendered on a Worker, data from server functions that are never
        cached) takes its day helpers from lib/time.ts (browser) and server/time.ts (Worker): in Node, under each time
        zone and instant, today() never keeps a stale day, and the HKT day is right"""
        for zone in ZONES:
            r = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "-e", NODE_CHECK, json.dumps(INSTANTS)],
                               cwd=APP, capture_output=True, text=True, timeout=60, env=dict(os.environ, TZ=zone))
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            for at, got in json.loads(r.stdout).items():
                t = datetime.fromisoformat(at).astimezone(HKT).date()
                today, yday = t.isoformat(), (t - timedelta(days=1)).isoformat()
                with self.subTest(zone=zone, at=at):
                    self.assertEqual(got, {"hk": today, "server": today, "stale": today, "fresh": today, "none": today,
                                           "prev": yday, "local_differs": got["local_differs"]}, got)


NODE_CHECK = """
const lib = await import("./src/lib/time.ts"), srv = await import("./src/server/time.ts");
const out = {}, real = Date.now;
for (const at of JSON.parse(process.argv[1])) {
  const t = Date.parse(at); Date.now = () => t;
  const hk = lib.hkToday();
  out[at] = {hk, server: srv.hktToday(), stale: lib.today(lib.dayAdd(hk, -1)), fresh: lib.today(hk), none: lib.today(),
             prev: lib.dayAdd(hk, -1), local_differs: new Date(t).getDate() !== +hk.slice(8)};
}
Date.now = real;
console.log(JSON.stringify(out));
"""

if __name__ == "__main__":
    unittest.main()
