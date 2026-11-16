#!/usr/bin/env python3
"""A source switched off on purpose is "off", never "failing" (Deyao, 2026-10-15: "If its turned off shouldnt show it
as a failing job"), and the poller's per-source status follows the latest run.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_source_off.py

1. Off by the watching safety stop: watch_file gives the platform a status {"ok": None, "off": "<reason>"};
   app.track keeps no failure count for it; monitor.sources shows it as off with the reason, outside the failing
   count; the page shows "off: safety stop", not under Problems, not "failing since"; the public status line doesn't
   count it as failed. Once its accounts are read again it is judged normally.
2. The poller carries an earlier status forward only for a scheduled source that wasn't due this poll. Live
   2026-10-15: "browser" got an error status once when browser.json couldn't be read during the Postgres OOM
   (08:13 HKT); later polls read the file fine but never wrote "browser" again, so the 08:13 error was carried for
   ever ("failing since 08:13, last OK never"); rows whose readers stopped emitting them were carried the same way.
"""
import os, sys, tempfile, unittest, unittest.mock
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, monitor, sources, watch_fetch  # noqa: E402

HKT = corpus.HKT


def now():
    return datetime.now(HKT).isoformat(timespec="seconds")


def watch_doc(accounts):
    return {"fetched": now(), "accounts": {f"{p}:{i}": dict(st, name=f"{p}{i}", platform=p, group="g")
                                           for i, (p, st) in enumerate(accounts)}, "items": []}


class WatchFile(unittest.TestCase):
    def test_safety_stop_is_off(self):
        corpus.file_put("watch.json", watch_doc([("youtube", {"ok": False, "error": "x", "off": "safety stop (watchlist.paused)",
                                                              "off_by": "safety stop"})]))
        _, st = sources.watch_file()
        self.assertEqual(st["watch_youtube"]["off"], "safety stop")

    def test_read_again_judged_normally(self):
        corpus.file_put("watch.json", watch_doc([("x", {"ok": False, "error": "HTTP 500"})]))
        _, st = sources.watch_file()
        self.assertFalse(st["watch_x"].get("off"))
        self.assertIs(st["watch_x"]["ok"], False)
        self.assertIn("HTTP 500", st["watch_x"]["error"])

    def test_watch_fetch_marks_why(self):
        """watch_fetch writes off_by for the safety stop"""
        rows = [{"platform": "x", "id": "1", "name": "w", "group": "g"}]
        with unittest.mock.patch.object(watch_fetch.watchlist, "load", return_value=rows), \
                unittest.mock.patch.object(watch_fetch.watchlist, "paused", return_value=True), \
                unittest.mock.patch.object(watch_fetch, "own_youtube", return_value=set()):
            corpus.file_put("watch.json", {"fetched": now(), "accounts": {}, "items": []})
            watch_fetch._run()
        st = next(iter(corpus.file_get("watch.json")["accounts"].values()))
        self.assertEqual(st.get("off_by"), "safety stop")


class Track(unittest.TestCase):
    def test_off_is_not_a_failure(self):
        failing = app.track({"ok": False, "error": "boom"}, {"last_ok": "T0", "fails": 3, "fail_since": "T1"})
        v = app.track({"ok": None, "off": "safety stop", "error": None}, failing)
        self.assertEqual((v["fails"], v["fail_since"], v["last_ok"]), (0, None, "T0"))
        back = app.track({"ok": False, "error": "boom2"}, v)  # read again and failing: a fresh count
        self.assertEqual(back["fails"], 1)
        self.assertNotEqual(back["fail_since"], "T1")


class Carry(unittest.TestCase):
    """the poller's merge: a stale status is never carried for a source this poll's readers answered for"""
    def poll(self, status):
        with unittest.mock.patch.object(app.sources, "fetch_all", return_value=([], status)), \
                unittest.mock.patch.object(app, "after_poll"):
            app.state["discuss_at"] = 9e12  # 香港討論區 not due
            app.poll_once()
        return app.state["source_status"]

    def test_browser_error_cleared_by_a_good_read(self):
        app.state["source_status"] = {}
        first = self.poll({"browser": {"ok": False, "items": 0, "error": "OperationalError('ssl eof')"},
                           "watch_x": {"ok": False, "items": 0, "error": "safety stop"},
                           "tikhub_lemon8": {"ok": True, "items": 3, "error": None}})
        self.assertFalse(first["browser"]["ok"])
        corpus.file_put("browser.json", {"fetched": now(), "sites": {"singpao": {"name": "成報", "ok": True, "error": None,
                                         "items": [{"title": "t", "url": "https://sp.test/1"}]}}})
        items, st = sources.browser_file()
        self.assertTrue(st["browser"]["ok"])  # the job's own line: answered
        later = self.poll(st)
        self.assertTrue(later["browser"]["ok"])
        self.assertTrue(later["browser"]["last_ok"])
        self.assertEqual(later["browser"]["fails"], 0)
        self.assertNotIn("watch_x", later)  # no reader answered for it: not carried
        self.assertNotIn("tikhub_lemon8", later)

    def test_scheduled_source_not_due_is_carried(self):
        sid = sources.SOURCES[0][0]
        app.state["source_status"] = {}
        self.poll({sid: {"ok": True, "items": 5, "error": None}})
        later = self.poll({})
        self.assertTrue(later[sid]["ok"])


class MonitorRows(unittest.TestCase):
    def test_off_rows(self):
        status = {"watch_x": {"ok": None, "off": "safety stop", "items": 0, "error": None, "fails": 0},
                  "browser": {"ok": False, "items": 0, "error": "boom", "fails": 2, "fail_since": now()},
                  "singpao": {"ok": True, "items": 3, "error": None}}
        with unittest.mock.patch.object(monitor, "poll_state", return_value={"source_status": status, "last_poll": now()}):
            s = monitor.sources()
        rows = {r["id"]: r for r in s["rows"]}
        self.assertEqual(rows["watch_x"]["off"], "safety stop")
        self.assertEqual(rows["watch_x"]["state"], "off")
        self.assertEqual(rows["browser"]["state"], "failing")
        self.assertEqual(rows["singpao"]["state"], "ok")
        self.assertEqual(s["counts"]["failing"], 1)
        self.assertEqual(s["counts"]["off"], 1)

    def test_browser_job_counts_its_sites_posts(self):
        corpus.file_put("browser.json", {"fetched": now(), "sites": {"singpao": {"name": "成報", "ok": True, "error": None,
                                         "items": []}}})
        corpus.ingest([{"url": "https://sp.test/job-1", "title": "成報新聞", "sid": "singpao", "source": "成報",
                        "published": None}])
        status = {"browser": {"ok": True, "items": 1, "error": None}, "singpao": {"ok": True, "items": 1, "error": None}}
        with unittest.mock.patch.object(monitor, "poll_state", return_value={"source_status": status, "last_poll": now()}):
            rows = {r["id"]: r for r in monitor.sources()["rows"]}
        self.assertEqual(rows["browser"]["posts_24h"], rows["singpao"]["posts_24h"])
        self.assertGreaterEqual(rows["browser"]["posts_24h"], 1)
        self.assertIsNone(rows["browser"]["usual_per_day"])  # no summed backlog days: never "quiet" by itself
        self.assertFalse(rows["browser"]["quiet"])

    def test_watching_off_not_failing(self):
        corpus.file_put("watch.json", watch_doc([("youtube", {"ok": False, "off": "safety stop (watchlist.paused)",
                                                              "off_by": "safety stop"})]))
        w = monitor.watching()
        self.assertFalse(w["platforms"].get("youtube", {}).get("failing"))


class Page(unittest.TestCase):
    def test_off_shown_as_off(self):  # the monitor (the edge app's -MonitorPage.tsx) says "off: <why>", never failing
        src = open(os.path.join(HERE, "web-app", "src", "routes", "-MonitorPage.tsx"), encoding="utf-8").read()
        self.assertIn('r.state === "off" ? pill("off", "off: " + r.off)', src)
        self.assertIn("${cn.off || 0} off", src)

    def test_public_status_line(self):  # 串流's source status line doesn't count an off source as 失敗
        src = open(os.path.join(HERE, "web-app", "src", "routes", "-StreamPage.tsx"), encoding="utf-8").read()
        line = next(l for l in src.splitlines() if "bad = Object.entries(st)" in l)
        self.assertIn("!v.off", line)


if __name__ == "__main__":
    unittest.main()
