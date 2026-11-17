#!/usr/bin/env python3
"""IPRoyal bandwidth (Deyao, 2026-10-29), on a throwaway database (TEST_DATABASE_URL), no network:

1. Instagram / Threads: an account that posted within 24 h is read hourly, the others every 6 h (ig_fetch.every_minutes).
2. Facebook: a page that posted within 24 h is checked every 30 min, the others every 3 h (fb_fetch.every_minutes); a
   page not due keeps its state and is not asked; a page that errors waits its interval too.
3. Instagram / Threads go through the ISP IP first (ig_fetch.read_isp); an account the ISP IP can't serve is read
   through yycstream, and after ISP_FAILS such accounts the rest of the run skip the ISP IP. Its lock is waited for
   in Postgres's queue (ig_fetch.isp_turn): a job taking it per request in a try loop never starves it.
4. 香港討論區: one direct request first (replies_fetch.discuss_route); Cloudflare's challenge or the reCAPTCHA wall =
   one request through the ISP IP (flat rate); walled there too (or no ISP_PROXY) = through yycstream.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_proxy_bandwidth.py
"""
import json, os, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.setdefault("RESI_PROXY", "http://u:p@127.0.0.1:9")
os.environ.setdefault("TIKHUB_API", "test")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, fb_fetch, ig_fetch, replies_fetch, watchlist  # noqa: E402
from test_social_coverage import ig_html  # noqa: E402

HKT = timezone(timedelta(hours=8))
TMP = tempfile.mkdtemp()
NOW = int(time.time())


class R:
    def __init__(self, html, status=200, url="https://www.instagram.com/x/"):
        self.text, self.status_code, self.url = html, status, url


class Cadence(unittest.TestCase):
    def test_instagram(self):
        self.assertEqual(ig_fetch.every_minutes([NOW - 3600], NOW), 60)
        self.assertEqual(ig_fetch.every_minutes([NOW - 30 * 3600], NOW), 360)
        self.assertEqual(ig_fetch.every_minutes([], NOW), 360)

    def test_facebook(self):
        self.assertEqual(fb_fetch.every_minutes([NOW - 3600], NOW), 30)
        self.assertEqual(fb_fetch.every_minutes([NOW - 30 * 3600], NOW), 180)
        ago = lambda m: (datetime.now(HKT) - timedelta(minutes=m)).isoformat(timespec="seconds")  # noqa: E731
        self.assertTrue(fb_fetch.due({}, NOW), "a page never checked is due")
        self.assertFalse(fb_fetch.due({"checked": ago(60), "recent": []}, NOW), "a quiet page waits 3 h")
        self.assertTrue(fb_fetch.due({"checked": ago(179), "recent": []}, NOW))
        self.assertTrue(fb_fetch.due({"checked": ago(29), "recent": [NOW - 600]}, NOW), "an active page every 30 min")
        self.assertFalse(fb_fetch.due({"checked": ago(10), "recent": [NOW - 600]}, NOW))


class InstagramRoute(unittest.TestCase):
    def run_main(self, handles, isp):
        """ig_fetch.main over these accounts; isp(url) → (R, html) or None stands in for read_isp → resi fetch calls"""
        with open(os.path.join(TMP, "ig.json"), "w") as f:
            json.dump([{"group": "media", "name": h, "platform": "instagram", "handle": h, "why": "t", "since": "2026-10-12"}
                       for h in handles], f)
        corpus.file_put("instagram.json", {"accounts": {}, "items": []})
        resi_urls = []

        def fetch(http, url, hdr, cut, isp=False):
            self.assertFalse(isp)
            resi_urls.append(url)
            h = ig_html([("RESIPOST01", NOW - 60)])
            return R(h), h
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://i:p@127.0.0.1:9"}), \
                mock.patch.object(watchlist, "FILE", os.path.join(TMP, "ig.json")), \
                mock.patch("curl_cffi.requests.Session", lambda *a, **k: None), \
                mock.patch.object(ig_fetch, "read_isp", lambda http, url, hdr, cut: isp(url)), \
                mock.patch.object(ig_fetch, "fetch", fetch):
            ig_fetch.main()
        return resi_urls, corpus.file_get("instagram.json")

    def test_isp_first(self):
        h = ig_html([("ISPPOST001", NOW - 60)])
        resi_urls, d = self.run_main(["a1", "a2"], lambda url: (R(h), h))
        self.assertEqual(resi_urls, [], "the ISP IP served every account: nothing through yycstream")
        self.assertTrue(all(v["ok"] for v in d["accounts"].values()))

    def test_fallback_then_skip(self):
        tried = []

        def isp(url):
            tried.append(url)
            return None if "bad" in url else (R(ig_html([("ISPPOST002", NOW - 60)])), ig_html([("ISPPOST002", NOW - 60)]))
        handles = ["bad1", "ok1", "bad2", "bad3", "ok2", "ok3"]
        resi_urls, d = self.run_main(handles, isp)
        self.assertEqual([u.split("/")[-2] for u in resi_urls], ["bad1", "bad2", "bad3", "ok2", "ok3"],
                         "the 3 accounts the ISP IP couldn't serve, then the rest straight to yycstream")
        self.assertEqual([u.split("/")[-2] for u in tried], ["bad1", "ok1", "bad2", "bad3"], "no ISP try after ISP_FAILS")
        self.assertTrue(all(v["ok"] for v in d["accounts"].values()))


class IspTurn(unittest.TestCase):
    def test_queued_waiter_beats_a_try_loop(self):
        """sites_fetch's way: pg_try_advisory_lock every few ms, hold a moment, release, try again at once"""
        import threading
        stop = threading.Event()

        def hog():
            with corpus.lock_connect("hog") as c:
                while not stop.is_set():
                    if c.execute("SELECT pg_try_advisory_lock(hashtext('yyc youtube'))").fetchone()[0]:
                        time.sleep(0.3)
                        c.execute("SELECT pg_advisory_unlock(hashtext('yyc youtube'))")
                    time.sleep(0.005)
        t = threading.Thread(target=hog, daemon=True)
        t.start()
        time.sleep(0.5)
        try:
            t0 = time.time()
            with ig_fetch.isp_turn(wait=5):
                got = time.time() - t0
            self.assertLess(got, 1.5, "the queued waiter gets the lock at the hog's next release")
        finally:
            stop.set()
            t.join(5)

    def test_long_holder_times_out(self):
        with corpus.lock("yyc youtube"):
            t0 = time.time()
            with self.assertRaises(corpus.LockTimeout):
                with ig_fetch.isp_turn(wait=1):
                    pass
            self.assertLess(time.time() - t0, 3)


class DiscussRoute(unittest.TestCase):
    def route(self, answers, isp_env=True):
        """answers: {route: (status, text)} for "direct" and "isp" → (discuss_route(), routes asked)"""
        asked = []

        class Px:
            def __init__(self, site, direct=False, isp=False):
                self.r = "direct" if direct else "isp" if isp else "hk"
                assert self.r != "hk", "the probe never goes through yycstream"

            def fetch(self, url, headers):
                asked.append(self.r)
                return answers[self.r]

            def account(self):
                pass
        env = {"ISP_PROXY": "http://u:p@isp.test:12323"} if isp_env else {}
        with mock.patch.object(replies_fetch, "Proxied", Px), mock.patch.dict(os.environ, env):
            if not isp_env:
                os.environ.pop("ISP_PROXY", None)
            return replies_fetch.discuss_route(), asked

    CHALLENGE = (403, "<!DOCTYPE html><html><head><title>Just a moment...</title>")
    WALL = (202, "請確認身份 g-recaptcha")
    OPEN = (200, '<a href="?fid-17.html">時事</a>')

    def test_open_site_goes_direct(self):
        self.assertEqual(self.route({"direct": self.OPEN}), ("direct", ["direct"]))

    def test_challenge_goes_through_the_isp_ip(self):
        """17 Nov 2026: the cluster's IP got Cloudflare's challenge, every residential IP the reCAPTCHA wall, the ISP
        IP the site"""
        self.assertEqual(self.route({"direct": self.CHALLENGE, "isp": self.OPEN}), ("isp", ["direct", "isp"]))
        self.assertEqual(self.route({"direct": (403, "<title>請稍候...</title>"), "isp": self.OPEN})[0], "isp")

    def test_walled_everywhere_goes_through_the_proxy(self):
        self.assertEqual(self.route({"direct": self.CHALLENGE, "isp": self.WALL}), ("hk", ["direct", "isp"]))
        self.assertEqual(self.route({"direct": self.WALL}, isp_env=False), ("hk", ["direct"]))

    def test_walled_names_the_challenge(self):
        self.assertTrue(replies_fetch.discuss_walled(403, "<html><head><title>Just a moment...</title>"))
        self.assertFalse(replies_fetch.discuss_walled(403, "<html><head><title>403 Forbidden</title>"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
