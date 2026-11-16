#!/usr/bin/env python3
"""uptime_log.py: the probe's failures in Postgres (kv "uptime:failures"), not uptime.log on a volume (Deyao,
2026-11-08: state in the application).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_uptime_log.py
"""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import corpus, uptime_log  # noqa: E402


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class UptimeLog(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k = %s", (uptime_log.KEY,))

    def test_lines_in_time_order_old_ones_dropped_import_is_idempotent(self):
        old = tempfile.NamedTemporaryFile("w", delete=False)
        old.write(f"{iso(200)} / 000 30.001\n{iso(5)} /healthz 502 0.2\n\n")
        old.close()
        uptime_log.main(["--import", old.name])
        uptime_log.main(["--import", old.name])  # again: no duplicates
        uptime_log.main([iso(1), "/dashboard", "503", "1.5"])
        got = corpus.kv_get(uptime_log.KEY)
        self.assertEqual([ln.split()[1:] for ln in got], [["/healthz", "502", "0.2"], ["/dashboard", "503", "1.5"]],
                         "time order; the 200-day-old line is past the 90 days kept; an import twice adds nothing")


if __name__ == "__main__":
    unittest.main()
