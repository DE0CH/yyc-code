#!/usr/bin/env python3
"""Google News links are decoded to the publisher's URL without hammering Google (2026-10-15).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_gnews_decode.py

Live 2026-10-15: since 10-14 ~18:00 HKT Google answered every news.google.com article page from the cluster's WARP IP
with 429 → google.com/sorry, so no Google News result was decoded or stored (gnews_*: 5 posts in 21 h, against 5–30 an
hour before; poll_seen: 4 Google links on 10-15, 481 on 10-14). An undecoded link is left unseen so the next poll
tries it again (c7dff45), and every poll tried every pending link (448 listed per poll, two requests each, every
5 minutes): the retries kept the block going. Now:

1. Google's block (429 / a redirect to google.com/sorry) stops direct requests at once, for GNEWS_PAUSE seconds
   (kept in poll_state, so a restarted poller keeps it): one direct request per poll at most while blocked.
2. While direct is paused, links are decoded through RESI_PROXY (IPRoyal yycstream); with no proxy they wait, unseen.
3. At most GNEWS_PER_POLL links are decoded per poll, newest first; the rest wait for the next poll (a backlog never
   stalls the poll), and a decoded link is never requested again.
4. The decode has a status line of its own (gnews_decode) on the monitor, so a block shows as failing.
"""
import json, os, re, sys, tempfile, unittest
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

app.after_poll = lambda g, f: insights.after_poll(g, f)  # in this thread, so the test can check the corpus
app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
sources.discuss_search = lambda qs: []


class FakeGoogle:
    """news.google.com: the article page (sg/ts) and batchexecute; blocked answers GoogleBlocked for direct requests"""

    def __init__(self, blocked=False):
        self.blocked = blocked
        self.calls = []  # (method, aid or None, proxy)

    def __call__(self, method, url, proxy=None, data=None):
        if method == "GET":
            aid = re.search(r"articles/([^?/]+)", url).group(1)
        else:
            aid = json.loads(json.loads(data["f.req"])[0][0][1])[2]
        self.calls.append((method, aid, proxy))
        if self.blocked and not proxy:
            raise app.GoogleBlocked("429 google.com/sorry")
        if method == "GET":
            return f'<c-wiz data-n-a-sg="SIG{aid}" data-n-a-ts="1760000000"></c-wiz>'
        return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", json.dumps(["garturlres", f"https://pub.test/{aid}", 1])]])

    def direct(self):
        return [c for c in self.calls if not c[2]]


def gnews_items(prefix, n, now, sid="gnews_0"):
    """n Google News results, newest first is i=0"""
    return [{"sid": sid, "source": "Google News", "section": "某報", "title": f"{prefix} 新聞 {i}",
             "url": f"https://news.google.com/rss/articles/{prefix}{i}?oc=5", "published": now - timedelta(minutes=i)}
            for i in range(n)]


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"gnews_0": {"ok": True, "items": len(items), "error": None}})
    app.poll_once()


def stored(prefix):
    return {r[0] for r in corpus.db().execute("SELECT url FROM posts WHERE url LIKE %s", (f"https://pub.test/{prefix}%",))}


def seen_google(prefix):
    return {r[0] for r in corpus.db().execute("SELECT url FROM poll_seen WHERE url LIKE %s",
                                               (f"https://news.google.com/rss/articles/{prefix}%",))}


class Decode(unittest.TestCase):
    def setUp(self):
        app.state.clear()
        with corpus.db() as c:
            c.execute("DELETE FROM poll_state")
        app.load_state()
        self.env = os.environ.get("RESI_PROXY")

    def tearDown(self):
        if self.env is None:
            os.environ.pop("RESI_PROXY", None)
        else:
            os.environ["RESI_PROXY"] = self.env

    def test_block_pauses_direct_and_uses_proxy(self):
        os.environ["RESI_PROXY"] = "http://u:p@proxy.test:12321"
        g = app.google_get = FakeGoogle(blocked=True)
        now = app.datetime.now(app.HKT)
        poll(gnews_items("blk", 5, now))
        self.assertEqual(len(g.direct()), 1)  # the first refusal stops direct requests at once
        self.assertEqual(stored("blk"), {f"https://pub.test/blk{i}" for i in range(5)})  # decoded through the proxy
        self.assertEqual(len(seen_google("blk")), 5)
        st = app.state["source_status"]["gnews_decode"]
        self.assertTrue(st["ok"])
        self.assertEqual(st["items"], 5)
        # the pause is kept in poll_state: the next poll (even a restarted poller) goes straight to the proxy
        app.state.clear()
        app.load_state()
        poll(gnews_items("bkb", 2, now))
        self.assertEqual(len(g.direct()), 1)
        self.assertEqual(len(stored("bkb")), 2)
        # once the pause has passed, direct is tried again (and used when Google answers)
        g.blocked = False
        app.state["gnews_direct_at"] = 0
        poll(gnews_items("bkc", 1, now))
        self.assertGreater(len(g.direct()), 1)
        self.assertEqual(len(stored("bkc")), 1)

    def test_blocked_without_proxy_waits_unseen(self):
        os.environ.pop("RESI_PROXY", None)
        g = app.google_get = FakeGoogle(blocked=True)
        now = app.datetime.now(app.HKT)
        poll(gnews_items("np", 6, now))
        self.assertEqual(len(g.calls), 1)  # one refusal, not one per link
        self.assertEqual(stored("np"), set())
        self.assertEqual(seen_google("np"), set())  # not seen: decoded once Google answers again
        st = app.state["source_status"]["gnews_decode"]
        self.assertFalse(st["ok"])
        self.assertIn("sorry", st["error"])
        poll(gnews_items("np", 6, now))
        self.assertEqual(len(g.calls), 1)  # paused: no request at all
        g.blocked = False
        app.state["gnews_direct_at"] = 0
        poll(gnews_items("np", 6, now))
        self.assertEqual(len(stored("np")), 6)

    def test_cap_per_poll_newest_first(self):
        os.environ.pop("RESI_PROXY", None)
        g = app.google_get = FakeGoogle()
        cap, app.GNEWS_PER_POLL = app.GNEWS_PER_POLL, 3
        try:
            now = app.datetime.now(app.HKT)
            items = gnews_items("cap", 7, now)
            poll(list(reversed(items)))  # listed oldest first: still decoded newest first
            self.assertEqual(stored("cap"), {f"https://pub.test/cap{i}" for i in range(3)})
            poll(items)
            self.assertEqual(stored("cap"), {f"https://pub.test/cap{i}" for i in range(6)})
            poll(items)
            self.assertEqual(len(stored("cap")), 7)
            self.assertEqual(len([c for c in g.calls if c[0] == "GET"]), 7)  # each link requested once
            poll(items)
            self.assertEqual(len([c for c in g.calls if c[0] == "GET"]), 7)
        finally:
            app.GNEWS_PER_POLL = cap

    def test_monitor_row(self):
        name, kind = monitor.source_kinds()["gnews_decode"]
        self.assertEqual(kind, "google news")


if __name__ == "__main__":
    unittest.main()
