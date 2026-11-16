#!/usr/bin/env python3
"""Watching accounts directly (Deyao, 2026-10-14: every post of the important accounts on every platform they use, a
USD 20/day safety stop on the new watching spend), on a throwaway
database (TEST_DATABASE_URL), no network (saved real TikHub answers and a real YouTube feed in fixtures/watch/):

1. watchlist.json is one reviewable list: every row has a group, platform, a handle/id and why; the 147 Facebook
   pages and 70 Instagram/Threads accounts followed before are in it (fb_fetch.py and ig_fetch.py read it).
2. Each platform's reader turns its answer into posts with full text, time and engagement (X without pure retweets).
3. A run files every new post of a due account once, refreshes counts of known ones, meters each TikHub request.
4. The safety stop: at the cap, watching pauses (no TikHub request, new Facebook/Instagram rows not read), the
   monitor's kv says so, Deyao is told once; the next day it runs again. YouTube waits too (its videos are transcribed).
   The model share counts only what the watched posts add (per-post AI by share, transcription of their own clips), and a
   source name an old account also uses is not counted as new.
6. The poller sees the posts (sources.watch_file) as their platform, the account as the board.
8. YouTube's channel feed (feeds/videos.xml) answers 404/500 for every channel during YouTube's feed outages (all 192
   watched channels from 19 Oct 2026, 20:48 HKT): the channel is then read from its uploads playlist page, the page
   sources.py reads (exact times from YouTube's player answer), so it keeps answering.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_watch.py
"""
import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.pop("WATCH_CAP_USD", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, notify, sources, watchlist, watch_fetch  # noqa: E402

FIX = os.path.join(HERE, "tests", "fixtures", "watch")
HKT = timezone(timedelta(hours=8))
TMP = tempfile.mkdtemp()
SENT = []
notify.deyao = lambda text: SENT.append(text) or True


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read() if name.endswith(".xml") else json.loads(f.read())["data"]


def rows(*rs):
    path = os.path.join(TMP, "wl.json")
    with open(path, "w") as f:
        json.dump([dict({"group": "media", "why": "test", "since": "2026-10-14"}, **r) for r in rs], f)
    return mock.patch.object(watchlist, "FILE", path)


def reset():
    SENT.clear()
    corpus.db().execute("DELETE FROM kv WHERE k LIKE 'watch%%' OR k LIKE 'file:watch%%' OR k = 'social_limit'")


class List(unittest.TestCase):
    def test_list_is_valid(self):
        rs = watchlist.load()
        self.assertEqual(watchlist.problems(rs), [])
        self.assertEqual({r["group"] for r in rs}, set(watchlist.GROUPS))

    def test_old_follows_are_in_it(self):
        rs = watchlist.load()
        self.assertGreaterEqual(len([r for r in rs if r["platform"] == "facebook" and r["since"] < watchlist.WATCH_START]), 147)
        self.assertGreaterEqual(len([r for r in rs if r["platform"] in ("instagram", "threads") and r["since"] < watchlist.WATCH_START]), 70)
        self.assertFalse(os.path.exists(os.path.join(HERE, "facebook_pages.json")))
        self.assertFalse(os.path.exists(os.path.join(HERE, "instagram_accounts.json")))

    def test_youtube_rows_have_channel_ids(self):
        self.assertIn("YouTube needs its channel id", " ".join(watchlist.problems(
            [{"group": "kol", "name": "x", "platform": "youtube", "handle": "@x", "why": "t", "since": "2026-10-14"}])))


class Readers(unittest.TestCase):
    def test_youtube_feed(self):
        got = watch_fetch.youtube_feed(fixture("youtube_rss.xml"))
        self.assertGreater(len(got), 5)
        p = got[0]
        self.assertTrue(p["url"].startswith("https://www.youtube.com/"))
        self.assertTrue(p["published"].endswith("+08:00"))
        self.assertIn("views", p["engagement"])
        self.assertTrue(p["text"].startswith(p["title"]))

    def test_x_skips_pure_retweets_and_expands_links(self):
        d = fixture("x_user_posts.json")
        d["timeline"].append(dict(d["timeline"][0], tweet_id="1", text="RT @someone: not theirs"))
        got = watch_fetch.x_posts(d, {"handle": "SCMPNews"})
        self.assertEqual(len(got), 4)
        self.assertTrue(all("/status/1" not in p["url"] for p in got))
        self.assertIn("scmp.com", got[0]["text"])
        self.assertTrue({"views", "likes"} <= set(got[0]["engagement"]))

    def test_tiktok(self):
        got = watch_fetch.tiktok_posts(fixture("tiktok_user_posts.json"), {"handle": "8world.news"})
        self.assertEqual(len(got), 4)
        self.assertTrue(got[0]["pinned"])
        self.assertIn("views", got[0]["engagement"])


def x_answer(n_new, start=0):
    """an X user timeline answer with n posts, the newest now"""
    t = datetime.now(timezone.utc)
    return {"timeline": [{"tweet_id": str(1000 + start + i), "text": f"post {start + i}", "views": "10", "favorites": 1,
                          "created_at": (t - timedelta(minutes=5 * i)).strftime("%a %b %d %H:%M:%S +0000 %Y")}
                         for i in range(n_new)], "user": {"profile": "acct"}}


class Run(unittest.TestCase):
    def setUp(self):
        reset()

    def run_once(self, calls):
        with mock.patch.object(watch_fetch, "own_youtube", lambda: set()), \
                mock.patch.object(watch_fetch.th, "call", calls), \
                mock.patch.object(watch_fetch, "youtube", lambda row: watch_fetch.youtube_feed(fixture("youtube_rss.xml"))):
            watch_fetch.run()
        return corpus.file_get("watch.json")

    def test_every_new_post_once_and_metered(self):
        answers = [x_answer(3), x_answer(5)]  # the second check: the 3 known posts + 2 newer ones… (same ids 1000-1002)
        answers[1]["timeline"] = x_answer(2, start=10)["timeline"] + x_answer(3)["timeline"]
        sent = []

        def call(path, params=None, body=None, tag=None):
            sent.append(path)
            return answers[len(sent) - 1]
        with rows({"name": "Acct", "platform": "x", "handle": "acct"}):
            w = self.run_once(call)
            self.assertEqual(len([i for i in w["items"] if i["platform"] == "x"]), 3)
            st = w["accounts"]["x:acct"]
            st["checked"] = (datetime.now(HKT) - timedelta(hours=3)).isoformat(timespec="seconds")
            corpus.file_put("watch.json", w)
            w = self.run_once(call)
        xs = [i for i in w["items"] if i["platform"] == "x"]
        self.assertEqual(sorted(i["url"].rsplit("/", 1)[1] for i in xs), ["1000", "1001", "1002", "1010", "1011"])
        self.assertEqual(len(sent), 2)
        self.assertAlmostEqual(watchlist.spend()["tikhub"], 0.002)

    def test_x_and_tiktok_every_30_min(self):
        """X and TikTok accounts are checked every 30 min (Deyao, 2026-11-11: TikHub cost; every 10 min was USD 4.61 a
        day), whether the account is busy or quiet; the CronJob still runs every 10 min for the free YouTube feeds"""
        now_ts = int(datetime.now(HKT).timestamp())
        for platform in ("x", "tiktok"):
            for recent in ([], [now_ts - 3600], [now_ts - k * 600 for k in range(40)]):
                self.assertEqual(watch_fetch.every_min({"platform": platform}, {"recent": recent}, now_ts), 30)
        self.assertEqual(watch_fetch.every_min({"platform": "youtube"}, {}, now_ts), 10)
        sent = []

        def call(path, params=None, body=None, tag=None):
            sent.append(path)
            return x_answer(1)
        with rows({"name": "Quiet", "platform": "x", "handle": "quiet"}):
            w = self.run_once(call)
            for minutes, calls in ((10, 1), (30, 2)):  # checked 10 min ago: not due; 30 min ago: due
                w["accounts"]["x:quiet"]["checked"] = (datetime.now(HKT) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
                corpus.file_put("watch.json", w)
                w = self.run_once(call)
                self.assertEqual(len(sent), calls, f"checked {minutes} min ago")

    def test_safety_stop(self):
        sent = []
        with rows({"name": "Acct", "platform": "x", "handle": "acct"},
                  {"name": "Chan", "platform": "youtube", "id": "UCxrDuMmJI7r41l0PddgoeJg"}), \
                mock.patch.dict(os.environ, {"WATCH_CAP_USD": "0.01"}):
            watchlist.add_tikhub(0.02)
            w = self.run_once(lambda *a, **k: sent.append(a) or x_answer(1))
            self.assertEqual(sent, [], "no TikHub request once the stop is reached")
            self.assertEqual(w["accounts"]["x:acct"]["off"], "safety stop (watchlist.paused)")
            self.assertEqual(w["accounts"]["youtube:ucxrdummji7r41l0pddgoejg"].get("off"), "safety stop (watchlist.paused)",
                             "YouTube waits too (its videos are transcribed)")
            self.assertTrue(w["paused"])
            self.run_once(lambda *a, **k: sent.append(a) or x_answer(1))
            self.assertEqual(len(SENT), 1, "Deyao is told once")
            self.assertIn("safety stop", SENT[0])
            # the next HKT day: the meter starts at 0 again
            tomorrow = (datetime.now(HKT) + timedelta(days=1)).strftime("%Y-%m-%d")
            with mock.patch.object(watchlist, "today", lambda: tomorrow):
                self.assertFalse(watchlist.paused())
                self.assertIsNone(corpus.kv_get("watch_paused"))

    def test_stop_holds_new_instagram_rows(self):
        import ig_fetch
        fetched = []

        def fetch(http, url, hdr, cut):
            fetched.append(url)
            raise RuntimeError("no network in tests")
        with rows({"name": "Old", "platform": "instagram", "handle": "oldacct", "since": "2026-10-12"},
                  {"name": "New", "platform": "instagram", "handle": "newacct"}), \
                mock.patch.dict(os.environ, {"WATCH_CAP_USD": "0.01"}), mock.patch.object(ig_fetch, "fetch", fetch), \
                mock.patch("curl_cffi.requests.Session", lambda *a, **k: None):
            watchlist.add_tikhub(0.02)
            ig_fetch.main()
            first = list(fetched)
            # the next run while still stopped: the new account's state already says off (2026-10-15 the CronJob died
            # here with "dict() got multiple values for keyword argument 'off'")
            ig_fetch.main()
        self.assertEqual(first, ["https://www.instagram.com/oldacct/"] * 2)  # the old one (with its retry), not the new one
        self.assertNotIn("https://www.instagram.com/newacct/", fetched)
        self.assertEqual(corpus.file_get("instagram.json")["accounts"]["newacct"]["off"], "safety stop")

class ModelShare(unittest.TestCase):
    def test_counts_only_what_watched_posts_add(self):
        reset()
        c = corpus.db()
        day = watchlist.today()
        c.execute("DELETE FROM ai_spend; DELETE FROM post_media; DELETE FROM media; DELETE FROM posts")
        c.execute("INSERT INTO ai_spend (day, purpose, usd) VALUES (%s, 'scoring', 1.0), (%s, 'scoring_history', 50.0), (%s, 'transcribe', 9.0)",
                  (day, day, day))
        now = datetime.now(HKT).isoformat(timespec="seconds")
        ids = corpus.ingest([{"url": f"https://x.com/a/status/{i}", "title": "t", "sid": "watch_x", "source": "A (X)",
                              "published": now} for i in range(1)] +
                            [{"url": f"https://example.com/{i}", "title": "t", "sid": "rthk", "source": "RTHK", "published": now}
                             for i in range(3)])
        c.execute("INSERT INTO media (key, url, kind, status, cost, done_at) VALUES ('yt:a', 'u', 'proxy', 'done', 0.5, %s), "
                  "('yt:b', 'u', 'proxy', 'done', 7.0, %s)", (now, now))
        c.execute("INSERT INTO post_media (post_id, key) VALUES (%s, 'yt:a'), (%s, 'yt:b')", (ids[0], ids[1]))
        self.assertAlmostEqual(watchlist.model_usd(day), 1.0 / 4 + 0.5)

    def test_shared_names_are_not_new(self):
        rs = [{"group": "media", "name": "X", "platform": "instagram", "handle": "a", "why": "t", "since": "2026-10-12"},
              {"group": "media", "name": "X", "platform": "instagram", "handle": "b", "why": "t", "since": "2026-10-14"},
              {"group": "media", "name": "Y", "platform": "threads", "handle": "c", "why": "t", "since": "2026-10-14"}]
        self.assertEqual(watchlist.new_sources(rs), {"Y (Threads)"})


class Poller(unittest.TestCase):
    def test_watch_file(self):
        reset()
        now = datetime.now(HKT).isoformat(timespec="seconds")
        corpus.file_put("watch.json", {"fetched": now, "accounts": {
            "x:acct": {"platform": "x", "name": "Acct", "ok": True},
            "youtube:1": {"platform": "youtube", "name": "Gov", "ok": None, "off": "safety stop (watchlist.paused)",
                          "off_by": "safety stop"}},
            "items": [{"account": "x:acct", "platform": "x", "source": "李家超 John Lee (X)", "author": "李家超 John Lee",
                       "title": "hello", "url": "https://x.com/acct/status/1", "published": now, "text": "hello world",
                       "engagement": {"likes": 3}, "found": now}]})
        items, status = sources.watch_file()
        self.assertTrue(status["watch_x"]["ok"])
        self.assertEqual((status["watch_youtube"]["ok"], status["watch_youtube"]["off"]), (None, "safety stop"),
                         "a platform paused by the safety stop is off, not a failing source")
        self.assertEqual(items[0]["sid"], "watch_x")
        cl = corpus.classify(items[0])
        self.assertEqual((cl["platform"], cl["board"], cl["region"], cl["source_type"]), ("X", "李家超 John Lee", "HK", "Social Media"))
        self.assertEqual(corpus.classify({"sid": "watch_youtube", "source": "C (YouTube)"})["source_type"], "Video")


with open(os.path.join(HERE, "tests", "fixtures", "youtube", "rthk_uploads.html.gz"), "rb") as _f:
    import gzip
    UPLOADS = gzip.decompress(_f.read()).decode()


class YouTubeOutage(unittest.TestCase):
    ROW = {"platform": "youtube", "name": "RTHK", "id": "UC6of7UYhctnYmqABjUqzuxw"}

    def test_feed_outage_reads_the_uploads_page(self):
        import urllib.error
        asked = []

        def feed(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        def page(url, *a, **k):
            asked.append(url)
            return UPLOADS
        with mock.patch.object(watch_fetch.urllib.request, "urlopen", feed), \
                mock.patch.object(sources, "fetch", page), mock.patch.object(sources, "youtube_player", lambda v: None):
            got = watch_fetch.youtube(self.ROW)
        self.assertEqual(asked, ["https://www.youtube.com/playlist?list=UU6of7UYhctnYmqABjUqzuxw"])
        self.assertGreater(len(got), 5)
        p = got[0]
        self.assertRegex(p["url"], r"^https://www\.youtube\.com/watch\?v=[\w-]{11}$")
        self.assertEqual(p["id"], p["url"][-11:])
        self.assertTrue(p["title"] and p["text"].startswith(p["title"]))
        self.assertRegex(p["published"] or "", r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}.*\+08:00$")

    def test_feed_used_when_it_answers(self):
        class R:
            def __init__(self, b):
                self.b = b

            def read(self):
                return self.b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(watch_fetch.urllib.request, "urlopen", lambda req, timeout=None: R(fixture("youtube_rss.xml"))), \
                mock.patch.object(sources, "fetch", lambda *a, **k: self.fail("the page is read only in an outage")):
            got = watch_fetch.youtube(self.ROW)
        self.assertGreater(len(got), 5)

    def test_other_errors_still_fail(self):
        import urllib.error

        def feed(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        with mock.patch.object(watch_fetch.urllib.request, "urlopen", feed), \
                mock.patch.object(sources, "fetch", lambda *a, **k: self.fail("not an outage")):
            with self.assertRaises(urllib.error.HTTPError):
                watch_fetch.youtube(self.ROW)


if __name__ == "__main__":
    unittest.main(verbosity=1)
