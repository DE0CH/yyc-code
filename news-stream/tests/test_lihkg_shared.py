#!/usr/bin/env python3
"""Every LIHKG reader shares one client (lihkg_api.py), on a throwaway database (TEST_DATABASE_URL), no network
(Deyao, 2026-10-20: "fix the failing source"; the poller's 時事台 source lihkg_current got HTTP 429 from the cluster
IP while the whole-site job, beside it on the same IP, read LIHKG fine):

1. the poller's source (sources.lihkg_current) and the full-text reader (cache_fetch.fetch_one) send the web app's
   X-LI-DEVICE headers like the whole-site job: live 2026-10-20 11:00 HKT a request without them got 429 from the
   cluster IP while one with them, at the same moment from the same pod, got 60 threads;
2. one pacing for all of them: every LIHKG request from any process takes the next slot of one shared clock
   (kv lihkg:next_at), GAP_DIRECT seconds apart direct from the server, so two readers never burst together;
3. one back-off: a 429 / 403 / Cloudflare 1015 on a direct request by ANY reader records kv lihkg:blocked_until
   (now + 26 h); from then on every reader goes through RESI_PROXY (the poller retries the same request through it at
   once), a direct replies_fetch run in progress stops before its next request, and its next run starts proxied;
4. the source's status clears on the next successful poll.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_lihkg_shared.py
"""
import json, os, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["RESI_PROXY"] = "http://yycstream:pw123@geo.iproyal.com:12321"
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, corpus, lihkg_api, replies_fetch, sources  # noqa: E402

LISTING = {"success": 1, "response": {"items": [{"thread_id": 7, "title": "時事", "create_time": int(time.time()) - 60,
                                                  "category": {"name": "時事台"}, "no_of_reply": 5, "like_count": 3,
                                                  "dislike_count": 1}]}}
THREAD = {"success": 1, "response": {"user_nickname": "樓主", "no_of_reply": 5, "like_count": 3, "dislike_count": 1,
                                     "item_data": [{"msg": "開帖內容"}]}}


class Resp:
    def __init__(self, status, body):
        self.status_code, self.text = status, body
        self.content = body.encode()
        self.download_size = len(self.content)


class LIHKG:
    """a fake lihkg.com behind Cloudflare: 429 to a direct request without the web app's device headers, or to every
    direct request once `limited`; the residential proxy always passes"""

    def __init__(self):
        self.limited, self.calls = False, []

    def session(self, *a, **k):
        site = self

        class S:
            def get(self, url, headers=None, proxies=None, **kw):
                proxies = proxies or k.get("proxies")
                site.calls.append((url, dict(headers or {}), bool(proxies)))
                if not proxies and (site.limited or "X-LI-DEVICE" not in (headers or {})):
                    return Resp(429, "<!doctype html>error code: 1015")
                return Resp(200, json.dumps(THREAD if "/thread/7/" in url else LISTING))
            post = get
        return S()


def spec():
    return next(s for s in sources.SOURCES if s[0] == "lihkg_current")


class Shared(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv")
        self.site = LIHKG()
        self.patches = [mock.patch("curl_cffi.requests.Session", self.site.session),
                        mock.patch.object(lihkg_api.resi, "session", self.site.session),
                        mock.patch.object(lihkg_api.time, "sleep", lambda s: None),
                        mock.patch.object(replies_fetch.time, "sleep", lambda s: None)]
        for p in self.patches:
            p.start()
        lihkg_api.reset()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_1_poller_source_sends_device_headers(self):
        got = sources.fetch_source(spec())
        self.assertEqual([x["url"] for x in got], ["https://lihkg.com/thread/7/page/1"])
        url, h, proxied = self.site.calls[-1]
        self.assertFalse(proxied)  # direct from the server while LIHKG isn't limiting its IP
        self.assertTrue(h.get("X-LI-DEVICE"))
        self.assertEqual(h.get("X-LI-DEVICE-TYPE"), "browser")
        self.assertIsNone(corpus.kv_get(lihkg_api.K_BLOCKED))

    def test_1_fulltext_reader_sends_device_headers(self):
        text, author, eng = cache_fetch.fetch_one("https://lihkg.com/thread/7/page/1")
        self.assertEqual((text, author), ("開帖內容", "樓主"))
        self.assertTrue(self.site.calls[-1][1].get("X-LI-DEVICE"))

    def test_2_one_pacing_for_every_reader(self):
        slots = []
        with mock.patch.object(lihkg_api.time, "sleep", lambda s: slots.append(s)):
            sources.fetch_source(spec())  # the poller
            px = replies_fetch.Proxied("lihkg", direct=True)  # the whole-site job, another process
            replies_fetch.lihkg_json(px, "thread/7/page/1")
            cache_fetch.fetch_one("https://lihkg.com/thread/7/page/1")
        nxt = float(corpus.kv_get(lihkg_api.K_NEXT))
        # three requests, each in its own slot of the one clock: the third waited about two gaps
        self.assertGreaterEqual(nxt - time.time(), 2 * lihkg_api.GAP_DIRECT - 0.5)
        self.assertGreaterEqual(max(slots), lihkg_api.GAP_DIRECT - 0.5)

    def test_3_a_429_on_any_reader_is_one_back_off(self):
        self.site.limited = True
        got = sources.fetch_source(spec())  # direct 429 → recorded, retried through the proxy at once
        self.assertEqual(len(got), 1)
        self.assertEqual([c[2] for c in self.site.calls], [False, True])
        until = float(corpus.kv_get(lihkg_api.K_BLOCKED))
        self.assertAlmostEqual(until - time.time(), 26 * 3600, delta=60)
        self.assertTrue(lihkg_api.blocked())
        # the whole-site job's run already going direct: stops before its next request, never hits the IP again
        n = len(self.site.calls)
        px = replies_fetch.Proxied("lihkg", direct=True)
        with self.assertRaises(replies_fetch.Blocked):
            replies_fetch.lihkg_json(px, "thread/7/page/1")
        self.assertEqual(len(self.site.calls), n)
        # its next run starts through the proxy
        made = []
        with mock.patch.object(replies_fetch, "Proxied", lambda site, direct=False: made.append(direct) or 1 / 0):
            try:
                replies_fetch.run("lihkg", 3600)
            except ZeroDivisionError:
                pass
        self.assertEqual(made, [False])
        # and the full-text reader goes through the proxy too
        cache_fetch.fetch_one("https://lihkg.com/thread/7/page/1")
        self.assertTrue(self.site.calls[-1][2])

    def test_3_the_whole_site_job_records_the_shared_back_off(self):
        self.site.limited = True
        px = replies_fetch.Proxied("lihkg", direct=True)
        with self.assertRaises(replies_fetch.Blocked):
            replies_fetch.lihkg_json(px, "thread/7/page/1")
        self.assertTrue(lihkg_api.blocked())
        sources.fetch_source(spec())  # the poller now goes straight through the proxy
        self.assertTrue(self.site.calls[-1][2])

    def test_4_status_clears_on_the_next_good_poll(self):
        with mock.patch.object(sources, "SOURCES", [spec()]):
            with mock.patch.object(lihkg_api.Client, "get", side_effect=RuntimeError("LIHKG 500")):
                _, st = sources.fetch_all()
            self.assertFalse(st["lihkg_current"]["ok"])
            sources._last.clear()
            _, st = sources.fetch_all()
        self.assertTrue(st["lihkg_current"]["ok"])
        self.assertEqual(st["lihkg_current"]["items"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
