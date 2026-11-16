#!/usr/bin/env python3
"""The monitor's "quiet" rule counts what a source delivers, not which source's id a post got (2026-10-15).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_monitor_quiet.py

Live 2026-10-15 (Deyao: "lots of quiet sources"): the corpus had its biggest day (18,918 posts by 15:40 HKT; SCMP 96
vs 89 the day before), yet SCMP's Hong Kong feed (scmp_hk), 明報 港聞 (mingpao_pns_ganwen), the 信報 columns and 明報·Emily
were "quiet":
1. A URL is stored under the first source that lists it. SCMP's news sitemap (scmp_news, scmp_sitemap) and 明報's
   sitemaps (mingpao_*_sitemap, added 13-14 Oct) list the same stories earlier, so scmp_hk's 50 listed items were
   37 scmp_news + 12 scmp_hk + 1 new. The poller now records per source how many of its listed URLs were first read in
   the last 24 h (status new_24h, whichever source stored them); a source whose listing brings that many is not quiet.
2. The usual day was counted by seen time, so a source's first read (a whole listing at once: 20 columns of 信報 on
   12 Oct 19:45) made a day of 20 for a column that publishes one a day ("usual 10.5"). Days are now counted by
   publish time (seen when there is none): a backlog falls on the days it was published.
"""
import os, sys, tempfile, unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, insights, monitor, sources  # noqa: E402

app.after_poll = lambda g, f: insights.after_poll(g, f)
app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
sources.discuss_search = lambda qs: []
HKT = corpus.HKT


def iso(t):
    return t.isoformat(timespec="seconds")


def add(sid, url, seen, published):
    with corpus.db() as c:
        c.execute("INSERT INTO posts (url, sid, source, title, seen, published, day) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (url, sid, sid, "t " + url, iso(seen), iso(published) if published else None, seen.strftime("%Y-%m-%d")))


def row(sid):
    return next(r for r in monitor.sources()["rows"] if r["id"] == sid)


class Quiet(unittest.TestCase):
    def test_listing_stored_under_another_source_is_not_quiet(self):
        now = app.datetime.now(HKT)
        for d in range(1, 8):  # a usual week: 30 posts a day under its own id
            for i in range(30):
                t = now - timedelta(days=d, minutes=i * 10)
                add("feed_a", f"https://a.test/{d}/{i}", t, t - timedelta(minutes=5))
        # today a sitemap lists the same stories first: they are stored under it
        items = []
        for i in range(30):
            t = now - timedelta(hours=1, minutes=i)
            items.append({"sid": "sitemap_a", "source": "A", "title": f"今日 {i}", "url": f"https://a.test/today/{i}",
                          "published": t})
        sources.fetch_all = lambda extra: (list(items), {"sitemap_a": {"ok": True, "items": 30, "error": None}})
        app.state.clear()
        app.load_state()
        app.poll_once()
        listing = [dict(it, sid="feed_a") for it in items]
        sources.fetch_all = lambda extra: (list(listing), {"feed_a": {"ok": True, "items": 30, "error": None}})
        app.poll_once()
        st = app.state["source_status"]["feed_a"]
        self.assertEqual(st["new_24h"], 30)  # every listed URL first read today (by the sitemap)
        r = row("feed_a")
        self.assertEqual(r["posts_24h"], 0)
        self.assertEqual(r["listed_new_24h"], 30)
        self.assertFalse(r["quiet"])
        self.assertEqual(r["state"], "ok")

    def test_still_quiet_when_the_listing_brings_nothing_new(self):
        now = app.datetime.now(HKT)
        for d in range(1, 8):
            for i in range(20):
                t = now - timedelta(days=d, minutes=i * 10)
                add("feed_b", f"https://b.test/{d}/{i}", t, t - timedelta(minutes=5))
        old = [{"sid": "feed_b", "source": "B", "title": f"舊 {i}", "url": f"https://b.test/1/{i}",
                "published": now - timedelta(days=1, minutes=i * 10)} for i in range(20)]
        with corpus.db() as c:  # read yesterday: in poll_seen 25 h ago
            c.executemany("INSERT INTO poll_seen (url, at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                          [(it["url"], iso(now - timedelta(hours=25))) for it in old])
        sources.fetch_all = lambda extra: (list(old), {"feed_b": {"ok": True, "items": 20, "error": None}})
        app.state.clear()
        app.load_state()
        app.poll_once()
        self.assertEqual(app.state["source_status"]["feed_b"]["new_24h"], 0)
        r = row("feed_b")
        self.assertTrue(r["quiet"])
        self.assertEqual(r["state"], "quiet")

    def test_first_read_backlog_falls_on_its_publish_days(self):
        """a daily column: its first read brought its whole listing (20) at one moment; one a day since"""
        now = app.datetime.now(HKT)
        first = now - timedelta(days=2, hours=20)
        for i in range(20):
            add("col_c", f"https://c.test/back/{i}", first, first - timedelta(days=i, hours=3))
        add("col_c", "https://c.test/old", now - timedelta(days=3, hours=3), now - timedelta(days=3, hours=4))
        for d in (0, 1):
            t = now - timedelta(days=d, hours=13)
            add("col_c", f"https://c.test/{d}", t, t - timedelta(hours=1))
        with corpus.db() as c:
            c.execute("""INSERT INTO poll_state (k, v) VALUES ('source_status', %s)
                         ON CONFLICT (k) DO UPDATE SET v = poll_state.v || excluded.v""",
                      (corpus.J({"col_c": {"ok": True, "items": 20, "error": None, "new_24h": 1}}),))
        r = row("col_c")
        self.assertLessEqual(r["usual_per_day"], 1.5)  # not the backlog's 20 in one day
        self.assertFalse(r["quiet"])


if __name__ == "__main__":
    unittest.main()
