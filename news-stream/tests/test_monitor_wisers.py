#!/usr/bin/env python3
"""The monitor's WiseSearch health (Deyao, 2026-11-05): monitor.wisers_problems() reads the relay's stats port
(wisers/relay.py health()) and names what is wrong; the monitor alerts on it once ("wisers:health") and once when it
is healthy again. No database: only the rule.

    python3 tests/test_monitor_wisers.py
"""
import copy, os, sys, tempfile, unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")  # never connected to here
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
import monitor  # noqa: E402

N = monitor.now()
iso = lambda t: t.isoformat(timespec="seconds")
BASE = {"session": {"state": "held"},
        "health": {"probe_secs": 300, "started": iso(N - timedelta(hours=1)), "failures": {},
                   "probe": {"at": iso(N - timedelta(minutes=2)), "ok": True, "secs": 0.8}}}


def p(**h):
    d = copy.deepcopy(BASE)
    d["health"].update(h)
    return monitor.wisers_problems(d)


class Problems(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(p(), [])
        self.assertIn("health check failed", p(probe={"at": iso(N), "ok": False, "outcome": "timeout", "error": "x"})[0])
        # 2026-11-11: one check in 10-25 timed out at 60 s and the next (60 s later) answered, about 10 Discord pings a
        # day while staff calls all worked: one failed check is not a problem, two in a row are
        self.assertEqual(p(probe={"at": iso(N), "ok": False, "outcome": "timeout", "error": "x", "fails": 1}), [],
                         "one failed check")
        self.assertIn("health check failed 2 times in a row",
                      p(probe={"at": iso(N), "ok": False, "outcome": "timeout", "error": "x", "fails": 2})[0])
        self.assertIn("no health check for 30 min", p(probe={"at": iso(N - timedelta(minutes=30)), "ok": True})[0])
        self.assertIn("no health check since the relay started", p(probe=None)[0])
        self.assertEqual(p(probe=None, started=iso(N - timedelta(minutes=3))), [], "a relay that just started")
        self.assertIn("refused as logged out", p(failures={"logged_out": 1})[0])
        self.assertEqual(p(failures={"error": 2}), [], "two errors: not yet")
        self.assertIn("3 call(s) failed", p(failures={"timeout": 2, "error": 1})[0])
        d = copy.deepcopy(BASE)  # 2026-11-06: "held", every check failing with HTTP 401 for hours
        d["session"]["last_ok"] = iso(N - timedelta(hours=9))
        d["health"]["probe"] = {"at": iso(N), "ok": False, "outcome": "error", "error": "HTTP 401"}
        self.assertIn("answered no call for 540 min", monitor.wisers_problems(d)[1])
        d = copy.deepcopy(BASE)
        d["session"]["state"] = "logged_out"
        d["health"]["failures"] = {"logged_out": 4}
        self.assertEqual(monitor.wisers_problems(d), [], "logged out is the wisers:session alert")
        self.assertEqual(monitor.wisers_problems({"session": {"state": "held"}}), [], "a relay without the health block")


if __name__ == "__main__":
    unittest.main()
