#!/usr/bin/env python3
"""IPRoyal out of traffic is a pause, never a failure (Deyao, 2026-10-15: yycstream ran out three times that day,
~14:40-15:02, 17:01-18:02 and 19:41-20:24 HKT; clips, a 信報 page and 香港討論區 thread reads were given up, second
scorings went out without their re-read). On a throwaway database (TEST_DATABASE_URL), against a fake proxy on
127.0.0.1 that answers every request as IPRoyal does when the sub-user's traffic is used up (402 Payment Required: a
CONNECT for https, the answer itself for a plain-http URL), with the real HTTP clients (urllib, curl_cffi, yt-dlp):

1. resi.is_out knows every wording the clients give (urllib 'Tunnel connection failed: 402', curl 'CONNECT tunnel
   failed, response 402', yt-dlp quoting either); a plain-http 402 answer is confirmed with a probe through the proxy;
   403s, 504s and timeouts are not.
2. every proxied client raises resi.ProxyOut at once (no retries into the 402): sources.fetch, resi.session (curl),
   replies_fetch.Proxied, media.run (yt-dlp), backfill.Client.
3. one record, one alert: kv proxy_out {since, at, message, jobs} names every job that stopped; one Discord message per
   outage (a flapping outage within 30 min of the last message sends none); the first proxied request that works
   after the outage began clears it; the monitor shows it as ONE stopped-provider line (IPRoyal, out of traffic, the
   paused jobs).
4. every proxied job pauses: no try counted, no cursor advanced, the item stays queued, the run stops proxied work,
   the job's status says proxy_out (sources: off 'proxy_out', never failing), and the job carries on by itself:
   fulltext, cache, links, the transcriber (yt-dlp and TVB's page), broadcast, the poller's proxied sources, 香港討論區
   search and Google News decodes, replies, comments (Facebook, Instagram), social_twopass's second read, Facebook
   pages, Instagram accounts, backfill, paywall (Reuters, archive.today).
5. every module that calls the proxy (resi.url / resi.session / resi.playwright) is one of the modules tested here.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_proxy_out.py
"""
import glob, json, os, re, socketserver, subprocess, sys, tempfile, threading, time, unittest, urllib.error
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  transcription on for the broadcast job's test (off since 2026-10-20)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
TMP = os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("DISCORD_WEBHOOK", None)
for _k in ("IPROYAL_API", "OPENROUTER_API", "TIKHUB_API", "ANYAPI_API_KEY", "CAPSOLVER_API", "TWOCAPTCHA_API"):
    os.environ.pop(_k, None)  # the monitor's stop probes (monitor.clear_answered) never ask a real provider here
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)


class Out402(socketserver.BaseRequestHandler):
    """IPRoyal with the sub-user's traffic used up: 402 to every CONNECT and every plain request"""
    hits = []

    def handle(self):
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = self.request.recv(4096)
            if not d:
                return
            buf += d
        Out402.hits.append(buf.split(b"\r\n", 1)[0].decode("latin-1"))
        self.request.sendall(b"HTTP/1.1 402 Payment Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


SRV = Server(("127.0.0.1", 0), Out402)
threading.Thread(target=SRV.serve_forever, daemon=True).start()
os.environ["RESI_PROXY"] = f"http://yycstream:pw123@127.0.0.1:{SRV.server_address[1]}"

import app, backfill, broadcast_fetch, cache_fetch, comments_fetch, corpus, fb_fetch, fulltext_fetch  # noqa: E402
import ig_fetch, links, media, monitor, notify, paywall, replies_fetch, resi, social_twopass, sources, twopass  # noqa: E402
import watchlist  # noqa: E402

HKT = corpus.HKT
replies_fetch.DISCUSS_GAP = 0
TESTED = {"app", "follow", "backfill", "cache_fetch", "lihkg_api", "comments_fetch", "fb_fetch", "ig_fetch", "links", "media", "paywall",
          "replies_fetch", "social_twopass", "sources", "sites_fetch", "forum_fetch", "canonical", "browser_fetch"}  # point 5: the modules that call the proxy themselves


def now():
    return datetime.now(HKT)


def post(url, sid="hkej_instant", source="信報", **kw):
    corpus.ingest([dict({"sid": sid, "source": source, "title": "標題 " + url[-12:], "url": url,
                         "published": (now() - timedelta(hours=3)).isoformat(timespec="seconds")}, **kw)],
                  seen=(now() - timedelta(hours=3)).isoformat(timespec="seconds"))
    return corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()["id"]


class Base(unittest.TestCase):
    def setUp(self):
        corpus.kv_set("proxy_out", None)
        corpus.kv_set("proxy_out_told", None)
        Out402.hits.clear()
        self.sent = []
        p = mock.patch.object(notify, "deyao", lambda text: self.sent.append(text) or True)
        p.start()
        self.addCleanup(p.stop)

    def assertPaused(self, job=None):
        rec = corpus.kv_get("proxy_out")
        self.assertTrue(rec, "no proxy_out record")
        if job:
            self.assertIn(job, rec["jobs"])


class T1Detect(Base):
    def test_wordings(self):
        out = [urllib.error.URLError(OSError("Tunnel connection failed: 402 Payment Required")),
               RuntimeError("Failed to perform, curl: (7) CONNECT tunnel failed, response 402. See https://curl.se/…"),
               RuntimeError("yt-dlp failed: ERROR: [youtube] x: Unable to download API page: <urlopen error Tunnel "
                            "connection failed: 402 Payment Required> (caused by ProxyError(…))"),
               "ERROR: [TikTok] 1: Unable to download webpage: Failed to perform, curl: (7) CONNECT tunnel failed, response 402"]
        for e in out:
            self.assertTrue(resi.is_out(e), e)
        chained = RuntimeError("wrapped")
        chained.__cause__ = out[0]
        self.assertTrue(resi.is_out(chained))
        for e in [urllib.error.URLError(OSError("Tunnel connection failed: 504 Gateway Timeout")),
                  RuntimeError("yt-dlp failed: ERROR: unable to download video data: HTTP Error 403: Forbidden"),
                  RuntimeError("curl: (28) Operation timed out after 40002 milliseconds"), TimeoutError()]:
            self.assertFalse(resi.is_out(e), e)

    def test_plain_http_402_answer_is_confirmed_by_a_probe(self):
        e = urllib.error.HTTPError("http://www2.hkej.com/x", 402, "Payment Required", {}, None)
        with mock.patch.object(resi, "probe", return_value=False):
            self.assertFalse(resi.is_out(e, proxied=True))  # the site itself said 402
        self.assertFalse(resi.is_out(e))  # not through the proxy: the site's own answer
        self.assertTrue(resi.is_out(e, proxied=True))  # the probe meets the fake IPRoyal's 402


class T2Clients(Base):
    def test_urllib_https_and_plain_http(self):
        for u in ("https://www1.hkej.com/dailynews/x", "http://www2.hkej.com/instantnews/x"):
            with self.assertRaises(resi.ProxyOut):
                sources.fetch(u, proxy=resi.url())
        self.assertPaused()

    def test_curl_session(self):
        t0 = time.time()
        with self.assertRaises(resi.ProxyOut):
            resi.session().get("https://www.instagram.com/x/", proxies={"https": resi.url()}, timeout=20)
        self.assertLess(time.time() - t0, 10)

    def test_replies_proxied_stops_at_once(self):
        self.assertTrue(issubclass(replies_fetch.ProxyOut, resi.ProxyOut))
        t0 = time.time()
        with self.assertRaises(resi.ProxyOut):
            replies_fetch.Proxied("discuss").get("https://www.discuss.com.hk/archiver/?fid-1.html")
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(len(Out402.hits), 1, "retried into the 402")

    @unittest.skipUnless(subprocess.run(["which", "yt-dlp"], capture_output=True).returncode == 0, "no yt-dlp")
    def test_ytdlp(self):
        with self.assertRaises(resi.ProxyOut) as cm:
            media.run(["yt-dlp", "-q", "--no-warnings", "--proxy", resi.url(), "-J",
                       "https://www.youtube.com/watch?v=aaaaaaaaaaa"], 120)
        self.assertIsInstance(cm.exception, media.HostBusy)  # the transcriber's not-a-try path

    def test_backfill_client_no_retries(self):
        c = backfill.Client(10, time.time() + 60, resi.url())
        with self.assertRaises(resi.ProxyOut):
            c.get("https://www1.hkej.com/dailynews/headline?date=2026-10-15")
        self.assertEqual(len(Out402.hits), 1)


class T3OneRecord(Base):
    def test_one_record_one_alert_cleared_by_a_working_request(self):
        t0 = time.time()
        with mock.patch.object(resi, "JOB", "replies_fetch"):
            resi.mark("Tunnel connection failed: 402 Payment Required")
        with mock.patch.object(resi, "JOB", "fulltext_fetch"):
            resi.mark("CONNECT tunnel failed, response 402")
        rec = corpus.kv_get("proxy_out")
        self.assertEqual(set(rec["jobs"]), {"replies_fetch", "fulltext_fetch"})
        self.assertEqual(len(self.sent), 1, "one Discord message per outage")
        self.assertIn("yycstream", self.sent[0])
        resi.ok(since=t0 - 60)  # a request that began before the outage proves nothing
        self.assertTrue(corpus.kv_get("proxy_out"))
        resi._ok_checked = 0
        resi.ok(since=time.time())
        self.assertIsNone(corpus.kv_get("proxy_out"))
        resi.mark("402 Payment Required")  # flapping: back out within 30 min of the message
        self.assertEqual(len(self.sent), 1)
        corpus.kv_set("proxy_out", None)
        corpus.kv_set("proxy_out_told", (now() - timedelta(minutes=31)).isoformat(timespec="seconds"))
        resi.mark("402 Payment Required")
        self.assertEqual(len(self.sent), 2)

    def test_monitor_one_line(self):
        with mock.patch.object(resi, "JOB", "media"):
            resi.mark("Tunnel connection failed: 402 Payment Required")
        with mock.patch.object(resi, "JOB", "app"):
            resi.mark("Tunnel connection failed: 402 Payment Required")
        st = [p for p in monitor.stops()["providers"] if p["id"] == "iproyal"]
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["tag"], "out of traffic")
        self.assertIn("transcriber", st[0]["message"])
        self.assertIn("poller", st[0]["message"])


class T4Jobs(Base):
    def test_fulltext_no_try_and_resumes(self):
        a = post("https://www1.hkej.com/dailynews/article/ft1", sid="hkej_daily_hknews")
        b = post("https://www1.hkej.com/dailynews/article/ft2", sid="hkej_daily_hknews")
        todo = [(a, "https://www1.hkej.com/dailynews/article/ft1", "hkej_daily_hknews", "2026-10-15"),
                (b, "https://www1.hkej.com/dailynews/article/ft2", "hkej_daily_hknews", "2026-10-15")]
        with mock.patch.object(fulltext_fetch, "candidates", return_value=todo), \
                mock.patch.object(cache_fetch, "pace", lambda: None), mock.patch.object(fulltext_fetch, "HOST_GAP", 0):
            fulltext_fetch.run()
        for pid in (a, b):
            self.assertIsNone(corpus.db().execute("SELECT cached_at FROM posts WHERE id=%s", (pid,)).fetchone()[0])
            self.assertIsNone(corpus.kv_get(f"fulltext:tries:{pid}"))
        self.assertEqual(len(Out402.hits), 1, "kept reading proxied pages after the 402")
        self.assertPaused()
        with mock.patch.object(fulltext_fetch, "candidates", return_value=todo[:1]), \
                mock.patch.object(cache_fetch, "fetch_one", lambda u, meta=None: ("全文" * 200, None, {})):
            fulltext_fetch.run()
        self.assertTrue(corpus.db().execute("SELECT text FROM posts WHERE id=%s", (a,)).fetchone()[0])

    def test_fulltext_now_no_try(self):
        """the poller's on-sight reader (fulltext_now) left Reuters and Bloomberg pages saved empty (tried) during
        the 16 Oct outage (02:38-11:03 HKT: 98 Reuters, 114 Bloomberg): a 402 is a pause there too, the page stays
        unread (cached_at NULL) for the 30-minute job"""
        import fulltext_now
        r = post("https://www.reuters.com/world/china/fn1-2026-10-16/", sid="reuters_world", source="Reuters")
        b = post("https://www.bloomberg.com/news/articles/2026-10-16/fn2", sid="bloomberg", source="Bloomberg")
        err = urllib.error.HTTPError("https://www.bloomberg.com/", 403, "Forbidden", {}, None)
        with mock.patch.object(cache_fetch, "get", side_effect=err), mock.patch.object(fulltext_now, "HOST_GAP", 0):
            fulltext_now.fetch(r, "https://www.reuters.com/world/china/fn1-2026-10-16/", "reuters_world")
            fulltext_now.fetch(b, "https://www.bloomberg.com/news/articles/2026-10-16/fn2", "bloomberg")
        for pid in (r, b):
            self.assertIsNone(corpus.db().execute("SELECT cached_at FROM posts WHERE id=%s", (pid,)).fetchone()[0])
        self.assertPaused()

    def test_cache_not_given_up(self):
        a = post("https://www1.hkej.com/dailynews/article/c1", sid="hkej_daily_hknews")
        with mock.patch.object(cache_fetch, "candidates", return_value=[(a, "https://www1.hkej.com/dailynews/article/c1")]), \
                mock.patch.object(cache_fetch, "pace", lambda: None):
            cache_fetch.run()
        self.assertIsNone(corpus.db().execute("SELECT cached_at FROM posts WHERE id=%s", (a,)).fetchone()[0])

    def test_links_no_try(self):
        a = post("https://www.facebook.com/page/posts/l1", sid="facebook", source="Page")
        with mock.patch.object(links, "resolve", side_effect=resi.ProxyOut("402")):
            links.one({"id": a, "url": "https://www.facebook.com/page/posts/l1", "text": "https://www1.hkej.com/x",
                       "title": "x"})
        r = corpus.db().execute("SELECT tries, status, next_try FROM link_scan WHERE post_id=%s", (a,)).fetchone()
        self.assertEqual((r["tries"], r["status"]), (0, "pending"))
        self.assertTrue(r["next_try"])

    def test_follow_no_try(self):
        """link following (follow.py): a link waiting on the proxy is pending again, no try counted"""
        import follow
        a = post("https://www.facebook.com/page/posts/f1", sid="facebook", source="Page")
        corpus.db().execute("INSERT INTO follow_links (post_id, origin, url, status, at) VALUES (%s,%s,%s,'pending',%s)",
                            (a, "https://www.facebook.com/page/posts/f1", "https://www1.hkej.com/x", now().isoformat()))
        row = corpus.db().execute("SELECT id, url FROM follow_links WHERE post_id=%s", (a,)).fetchone()
        with mock.patch.object(follow, "route", side_effect=resi.ProxyOut("402")):
            follow.one(dict(row))
        r = corpus.db().execute("SELECT tries, status, next_try FROM follow_links WHERE post_id=%s", (a,)).fetchone()
        self.assertEqual((r["tries"], r["status"]), (0, "pending"))
        self.assertTrue(r["next_try"])

    def _clip(self, key, url, kind):
        corpus.db().execute("INSERT INTO media (key, url, kind, status, first_seen, newest) VALUES (%s,%s,%s,'queued',%s,%s)",
                            (key, url, kind, now().isoformat(timespec="seconds"), now().isoformat(timespec="seconds")))
        return dict(corpus.db().execute("SELECT * FROM media WHERE key=%s", (key,)).fetchone())

    def test_transcriber_curl_wording_and_tvb_page(self):
        m = self._clip("tt:1", "https://www.tiktok.com/@a/video/1", "ytdlp")
        err = b"ERROR: [TikTok] 1: Unable to download webpage: Failed to perform, curl: (7) CONNECT tunnel failed, response 402."
        fake = mock.Mock(return_value=subprocess.CompletedProcess([], 1, b"", err))
        with mock.patch.object(subprocess, "run", fake):
            media._work(m)
        r = corpus.db().execute("SELECT status, tries, next_try FROM media WHERE key='tt:1'").fetchone()
        self.assertEqual((r["status"], r["tries"]), ("queued", 0))
        self.assertTrue(r["next_try"])
        self.assertPaused()
        # TVB's geo-restricted clips are never read through RESI_PROXY (Deyao, 2026-10-16): no_direct, nothing fetched
        t = self._clip("tvb:1", "https://news.tvb.com/tc/local/abc", "tvb_geo")
        media._work(t)
        self.assertEqual(corpus.db().execute("SELECT status FROM media WHERE key='tvb:1'").fetchone()["status"], "no_direct")

    def test_broadcast_stops_without_counting(self):
        eps = [({"sid": "x"}, {"url": "https://www.youtube.com/watch?v=b1", "title": "一"}),
               ({"sid": "x"}, {"url": "https://www.youtube.com/watch?v=b2", "title": "二"})]
        calls = []
        with mock.patch.object(broadcast_fetch, "todo", return_value=eps), \
                mock.patch.object(broadcast_fetch, "file_episode", side_effect=lambda p, ep: calls.append(ep) or (_ for _ in ()).throw(media.ProxyOut("402"))):
            broadcast_fetch.run()
        self.assertEqual(len(calls), 1)
        self.assertIsNone(corpus.kv_get("bc:fail:https://www.youtube.com/watch?v=b1"))

    def test_poller_sources_off_not_failing(self):
        spec = ("t_hkej", "信報", "https://www2.hkej.com/instantnews", sources.P["hkej_instant"], True)
        with mock.patch.object(sources, "SOURCES", []), mock.patch.object(sources, "browser_file", return_value=([], {})), \
                mock.patch.object(sources, "instagram_file", return_value=([], {})), \
                mock.patch.object(sources, "tikhub_file", return_value=([], {})), \
                mock.patch.object(sources, "watch_file", side_effect=LookupError()), \
                mock.patch.object(sources, "facebook_file", return_value=([], {}), create=True):
            _, st = sources.fetch_all([spec])
        v = st["t_hkej"]
        self.assertEqual(v.get("off"), "proxy_out")
        self.assertEqual(len(Out402.hits), 1, "retried into the 402")
        self.assertEqual(app.track(v, {"last_ok": "x", "fails": 0})["fails"], 0)
        Out402.hits.clear()
        with self.assertRaises(resi.ProxyOut):
            sources.discuss_search(["李家超", "特首", "政府"])
        self.assertEqual(len(Out402.hits), 1, "kept searching after the 402")
        app.state["gnews_direct_at"] = time.time() + 3600  # direct paused: through the proxy
        done, s = app.decode_gnews([{"url": "https://news.google.com/rss/articles/CBMiX?oc=5", "published": "2026-10-15T20:00+08:00"}])
        self.assertEqual((done, s.get("off")), ([], "proxy_out"))

    def test_lihkg_in_its_back_off_pauses_not_fails(self):
        """the poller's LIHKG source through the proxy (the server's IP in LIHKG's back-off): IPRoyal's 402 is a pause"""
        import lihkg_api
        lihkg_api.reset()
        corpus.kv_set(lihkg_api.K_BLOCKED, time.time() + 3600)
        spec = next(s for s in sources.SOURCES if s[0] == "lihkg_current")
        try:
            with mock.patch.object(sources, "SOURCES", [spec]), mock.patch.object(lihkg_api.time, "sleep", lambda s: None):
                _, st = sources.fetch_all()
        finally:
            corpus.kv_set(lihkg_api.K_BLOCKED, None)
            lihkg_api.reset()
        self.assertEqual(st["lihkg_current"].get("off"), "proxy_out")
        self.assertEqual(len(Out402.hits), 1, "retried into the 402")
        self.assertPaused()

    def test_replies_run_proxy_out(self):
        corpus.kv_set("replies:discuss:boards", {"at": time.time(), "boards": {"1": "時事"}})
        r = replies_fetch.run("discuss", 3600)
        self.assertTrue(r["proxy_out"])
        self.assertIsNone(corpus.kv_get("replies:discuss:listed:1"))
        self.assertPaused()

    def test_comments_facebook_and_instagram(self):
        fb = "https://www.facebook.com/page/posts/123456789012"
        ig = "https://www.instagram.com/p/AbCdEf123/"
        post(fb, sid="facebook", source="Page")
        post(ig, sid="instagram", source="Acct")
        corpus.file_put("fb-session.json", {"post": "variables=" + json.dumps({"count": 3}), "headers": {}, "cookies": {},
                                            "ua": "x", "captured": now().isoformat(timespec="seconds")})
        row = lambda u: dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (u,)).fetchone())  # noqa: E731
        with mock.patch.object(watchlist, "new_sources", return_value=set()):
            with self.assertRaises(resi.ProxyOut):  # the two-pass fetcher's readers (social_twopass.py)
                comments_fetch.facebook_post(comments_fetch.FB(), row(fb))
            with self.assertRaises(resi.ProxyOut):
                comments_fetch.instagram_post(resi.session(), row(ig))
        self.assertIsNone(corpus.kv_get("cm:" + fb))  # not marked checked: read again once the proxy has traffic
        self.assertIsNone(corpus.kv_get("cm:" + ig))

    def test_social_twopass_second_read_waits(self):
        u = "https://www.facebook.com/page/posts/223456789012"
        post(u, sid="facebook", source="Page")
        corpus.file_put("fb-session.json", {"post": "variables=" + json.dumps({"count": 3}), "headers": {}, "cookies": {},
                                            "ua": "x", "captured": now().isoformat(timespec="seconds")})
        corpus.db().execute("INSERT INTO two_pass (url, first_at, due_at) VALUES (%s, %s, %s)",
                            (u, now() - timedelta(hours=1), now() - timedelta(minutes=5)))
        social_twopass._local.__dict__.clear()
        social_twopass.again(u)
        self.assertIsNone(corpus.db().execute("SELECT fetched_at FROM two_pass WHERE url=%s", (u,)).fetchone()[0])

    def test_facebook_pages_keep_their_place(self):
        pid = "100083006243795"
        corpus.file_put("fb-session.json", {"post": "variables=" + json.dumps({"count": 3}), "headers": {}, "cookies": {},
                                            "ua": "x", "captured": now().isoformat(timespec="seconds")})
        corpus.file_put("fb-ids.json", {"https://www.facebook.com/johnleekachiu": pid})
        item = {"url": "https://www.facebook.com/johnleekachiu/posts/1", "at": int(time.time()) - 600, "title": "t",
                "source": "李家超", "published": now().isoformat()}
        corpus.file_put("facebook.json", {"state": {pid: {"ok": True, "newest_ts": 1792000000, "checked": "2026-10-15T20:00:00+08:00"}},
                                          "items": [item]})
        with open(os.path.join(TMP, "fb.json"), "w") as f:
            json.dump([{"group": "gov", "name": "李家超", "platform": "facebook", "url": "https://www.facebook.com/johnleekachiu",
                        "why": "test", "since": "2026-10-12"}], f)
        with mock.patch.object(watchlist, "FILE", os.path.join(TMP, "fb.json")), \
                mock.patch.object(fb_fetch, "capture_session", side_effect=AssertionError("no new session on a 402")):
            fb_fetch.poll()
        d = corpus.file_get("facebook.json")
        self.assertEqual(d.get("off"), "proxy_out")
        self.assertEqual(d["state"][pid]["newest_ts"], 1792000000)
        self.assertEqual(d["state"][pid]["checked"], "2026-10-15T20:00:00+08:00")
        self.assertEqual([i["url"] for i in d["items"]], [item["url"]])
        _, st = sources.facebook_file()
        self.assertEqual(st["facebook"].get("off"), "proxy_out")

    def test_instagram_accounts_keep_their_place(self):
        with open(os.path.join(TMP, "ig.json"), "w") as f:
            json.dump([{"group": "media", "name": "CNA", "platform": "instagram", "handle": "channelnewsasia", "why": "t",
                        "since": "2026-10-12"}], f)
        old = (now() - timedelta(hours=7)).isoformat(timespec="seconds")  # due (ig_fetch.QUIET_MIN)
        corpus.file_put("instagram.json", {"accounts": {"channelnewsasia": {"ok": True, "checked": old, "newest_ts": 1792000000}},
                                           "items": []})
        with mock.patch.object(watchlist, "FILE", os.path.join(TMP, "ig.json")):
            ig_fetch.main()
        d = corpus.file_get("instagram.json")
        self.assertEqual(d.get("off"), "proxy_out")
        self.assertEqual(d["accounts"]["channelnewsasia"]["checked"], old)
        self.assertEqual(len(Out402.hits), 1, "retried into the 402")

    def test_backfill_cursor_stays(self):
        corpus.kv_set("backfill:hkej", {"next": "2026-09-01"})
        backfill.run_outlet("hkej", since=datetime(2026, 8, 25).date(), until=datetime(2026, 9, 1).date(), log=lambda *a: None)
        self.assertEqual(corpus.kv_get("backfill:hkej")["next"], "2026-09-01")
        self.assertPaused()

    def test_paywall_reuters_and_archive(self):
        with self.assertRaises(resi.ProxyOut):
            paywall.reuters("https://www.reuters.com/world/china/x-2026-10-15/")
        self.assertFalse((corpus.kv_get("solver_limit") or {}).get("iproyal"))  # one record: kv proxy_out
        self.assertPaused()
        a = post("https://www.ft.com/content/abc", sid="ft_world", source="FT")
        rows = [{"id": a, "url": "https://www.ft.com/content/abc", "text": "", "looks": 0, "submitted_at": None},
                {"id": a, "url": "https://www.ft.com/content/abc", "text": "", "looks": 0, "submitted_at": None}]
        with mock.patch.object(paywall, "archive_candidates", return_value=rows), \
                mock.patch.object(paywall, "archive_lookup", side_effect=resi.ProxyOut("402")), \
                mock.patch.object(paywall, "ARCHIVE_GAP", 0):
            st = paywall.archive_run()
        self.assertTrue(st.get("proxy_out"))
        self.assertIsNone(corpus.db().execute("SELECT looks FROM archive_scan WHERE post_id=%s", (a,)).fetchone())


class T4bCatalogue(Base):
    """the catalogue's sites (sites_fetch), its forums' threads (forum_fetch) and the address fixer (canonical.fix)"""

    def test_catalogue_site_off_not_failing(self):
        import sites_fetch
        site = {"id": "t_proxied", "name": "測試", "url": "https://proxied.example.hk/feed", "kind": "rss",
                "region": "HK", "type": "Magazine", "proxy": "hk"}
        sites_fetch.run([site])
        st = corpus.file_get(sites_fetch.DOC)["sites"]["t_proxied"]
        self.assertEqual(st["off"], resi.STATUS)
        self.assertEqual(st.get("fails") or 0, 0, "an outage is no failure of the site")
        self.assertPaused()

    def test_browser_site_on_the_proxy_off_not_failing(self):
        """a catalogue "browser" site with a "proxy" country (Lifestyle Asia: Chrome on yycstream hk): Chrome names no
        status (ERR_TUNNEL_CONNECTION_FAILED), the probe through the proxy says 402 → the site is off, not failing; a
        page of it in cache_fetch's browser pass is not reached this run (a pause), never saved as read"""
        import browser_fetch

        class Ctx:
            pages = []

            def new_page(self):
                return object()

            def on(self, *a):
                pass

            def close(self):
                pass

        class PW:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            @property
            def chromium(self):
                return self

            def launch_persistent_context(self, prof, **k):
                return Ctx()

        def tunnel(pg):
            raise RuntimeError("Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://www.lifestyleasia.com/hk/")
        saved = {}
        with mock.patch.object(browser_fetch, "catalogue_sites", return_value=[("hk_lifestyleasia", "Lifestyle Asia", tunnel)]), \
                mock.patch.object(browser_fetch, "catalogue_proxies", return_value={"hk_lifestyleasia": "hk"}), \
                mock.patch.object(browser_fetch, "sync_playwright", return_value=PW()), \
                mock.patch.object(browser_fetch.nomedia, "block"), \
                mock.patch.object(browser_fetch.corpus, "file_put", side_effect=lambda k, v: saved.update({k: v})):
            browser_fetch.main(catalogue=True)
        st = saved[browser_fetch.CATALOGUE_DOC]["sites"]["hk_lifestyleasia"]
        self.assertEqual(st["off"], resi.STATUS)
        self.assertPaused()
        with mock.patch.object(cache_fetch, "PROXY_COUNTRY", {"lifestyleasia.com": "hk"}), \
                mock.patch("patchright.sync_api.sync_playwright", return_value=PW()), \
                mock.patch.object(cache_fetch.nomedia, "block"):
            class TabCtx(Ctx):
                def new_page(self):
                    class Pg:
                        def goto(self, *a, **k):
                            tunnel(None)

                        def close(self):
                            pass
                    return Pg()
            with mock.patch.object(PW, "launch_persistent_context", lambda self, prof, **k: TabCtx()):
                got = cache_fetch.browser_pass(["https://www.lifestyleasia.com/hk/whats-on/x/"])
        self.assertEqual(got, {}, "the page waits for the next run")

    def test_forum_thread_keeps_its_place(self):
        import forum_fetch as ff
        f = dict(ff.load()["jetsocome"], route="hk")
        f.pop("fallback", None)
        url = "https://www.jetsocome.com/forum.php?mod=viewthread&tid=777001&extra="
        corpus.ingest([{"sid": "hk_test_forum", "source": "測試論壇", "title": "一個帖子", "url": url}],
                      now().isoformat(timespec="seconds"))
        with mock.patch.object(ff, "GAP", 0):
            res = ff.run(["jetsocome"], {"jetsocome": f})
        st = ff.state("jetsocome", "777001")
        self.assertTrue(st is None or (st["tries"] == 0 and st["status"] != "error"), st)
        self.assertEqual(res["jetsocome"].get("off"), resi.STATUS, res)
        self.assertPaused()

    def test_canonical_fix_stops(self):
        import canonical
        for i in range(3):
            post(f"https://www1.hkej.com/dailynews/article/cn{i}", sid="hkej_daily_hknews")
        with mock.patch.object(canonical, "TIMEOUT", 5):
            with self.assertRaises(resi.ProxyOut):
                canonical.fix("hkej.com")
        self.assertPaused()


class T5Coverage(unittest.TestCase):
    def test_every_proxy_caller_is_tested(self):
        callers = set()
        for f in glob.glob(os.path.join(HERE, "*.py")):
            name = os.path.basename(f)[:-3]
            if name == "resi":
                continue
            if re.search(r"\bresi\.(url|session|playwright)\(", open(f, encoding="utf-8").read()):
                callers.add(name)
        self.assertEqual(callers - TESTED, set(), "a new proxy caller: give it the proxy_out pause and a test here")


if __name__ == "__main__":
    unittest.main()
