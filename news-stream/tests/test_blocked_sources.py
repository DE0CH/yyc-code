#!/usr/bin/env python3
"""Routes for sources that refuse plain HTTP clients (no network, no database): LIHKG through curl_cffi's Chrome
fingerprint (lihkg_api.Client for sources and cache_fetch, replies_fetch.Proxied), 獨立媒體's RSS read in the real browser (browser_fetch.rss_items), and a body a
server cuts off resumed with Range (sources.fetch; 橙新聞's sitemaps), against a local server.

    python3 tests/test_blocked_sources.py
"""
import http.server, os, sys, threading, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import browser_fetch  # noqa: E402
import sources  # noqa: E402


class Blocked(unittest.TestCase):
    def test_lihkg_uses_the_shared_client(self):
        """the poller's 時事台 source goes through lihkg_api (curl_cffi's Chrome, the web app's device headers)"""
        import lihkg_api
        spec = next(s for s in sources.SOURCES if s[0] == "lihkg_current")
        body = ('{"response":{"items":[{"thread_id":1,"title":"時事","create_time":1791949651,"category":{"name":"時事台"},'
                '"no_of_reply":5,"like_count":3,"dislike_count":7}]}}')
        session = mock.MagicMock()
        session.get.return_value = mock.MagicMock(status_code=200, text=body)
        lihkg_api.reset()
        with mock.patch.object(lihkg_api.corpus, "kv_get", return_value=None), mock.patch.object(lihkg_api, "pace"), \
             mock.patch("curl_cffi.requests.Session", return_value=session) as made, \
             mock.patch.object(sources, "fetch", side_effect=AssertionError("urllib used")):
            got = sources.fetch_source(spec)
        lihkg_api.reset()
        made.assert_called_once_with(impersonate="chrome")
        h = session.get.call_args.kwargs["headers"]
        self.assertEqual(h["Referer"], "https://lihkg.com/")
        self.assertTrue(h["X-LI-DEVICE"])
        self.assertEqual([(x["title"], x["url"]) for x in got], [("時事", "https://lihkg.com/thread/1/page/1")])
        self.assertEqual(got[0]["engagement"], {"replies": 5, "likes": 3, "dislikes": 7})

    def test_other_sources_keep_urllib(self):
        spec = next(s for s in sources.SOURCES if s[0] == "rthk_local")
        with mock.patch.object(sources, "fetch", return_value="<rss></rss>") as plain, \
             mock.patch.object(sources.lihkg_api, "client", side_effect=AssertionError("LIHKG client used")):
            sources.fetch_source(spec)
        plain.assert_called_once()

    def test_replies_lihkg_uses_chrome_fingerprint(self):
        import replies_fetch
        session = mock.MagicMock()
        session.get.return_value = mock.MagicMock(status_code=200, text='{"success":1,"response":{"x":1}}', content=b"x")
        with mock.patch.object(replies_fetch.corpus, "kv_get", return_value=None), \
             mock.patch("curl_cffi.requests.Session", return_value=session) as made, \
             mock.patch.object(replies_fetch.urllib.request, "build_opener", side_effect=AssertionError("urllib used")), \
             mock.patch.object(replies_fetch.lihkg_api, "pace"), mock.patch.object(replies_fetch.time, "sleep"):
            px = replies_fetch.Proxied("lihkg", direct=True)
            self.assertEqual(replies_fetch.lihkg_json(px, "thread/1/page/1"), {"x": 1})
            replies_fetch.lihkg_json(px, "thread/1/page/2")
        made.assert_called_once_with(impersonate="chrome")  # one session: its cookies carry over
        kw = session.get.call_args.kwargs
        self.assertIsNone(kw["proxies"])
        self.assertNotIn("User-Agent", kw["headers"])  # curl_cffi's own Chrome UA
        self.assertEqual(kw["headers"]["X-LI-DEVICE"], px.device)

    def test_replies_discuss_keeps_one_chrome_session(self):
        """香港討論區 through the sticky residential proxy on ONE kept-alive curl_cffi session (2026-10-15: 0.43 s a
        request against 1.25 s on a new connection each time), never urllib"""
        import replies_fetch
        session = mock.MagicMock()
        session.get.return_value = mock.MagicMock(status_code=200, text="ok", content=b"ok", download_size=2)
        with mock.patch.object(replies_fetch.corpus, "kv_get", return_value=None), \
             mock.patch.dict(os.environ, {"RESI_PROXY": "http://u:p@h:1"}), \
             mock.patch("curl_cffi.requests.Session", return_value=session) as made, \
             mock.patch.object(replies_fetch.urllib.request, "build_opener", side_effect=AssertionError("urllib used")):
            px = replies_fetch.Proxied("discuss")
            self.assertEqual(px.fetch("https://www.discuss.com.hk/", {}), (200, "ok"))
            px.fetch("https://www.discuss.com.hk/archiver/", {})
        made.assert_called_once_with(impersonate="chrome")  # one session: cookies and the connection carry over
        kw = session.get.call_args.kwargs
        self.assertTrue(kw["proxies"]["https"])  # through the proxy, not direct
        self.assertEqual(kw["proxies"]["https"], kw["proxies"]["http"])
        self.assertEqual(px.bytes, 2 * (2 + 800))  # bytes on the wire counted for the monitor

    def test_cache_lihkg_uses_the_shared_client(self):
        import cache_fetch, lihkg_api
        session = mock.MagicMock()
        session.get.return_value = mock.MagicMock(
            status_code=200, text='{"success":1,"response":{"item_data":[{"msg":"內文"}],"user_nickname":"甲","no_of_reply":2}}')
        lihkg_api.reset()
        with mock.patch.object(cache_fetch.sources, "fetch", side_effect=AssertionError("urllib used")), \
             mock.patch.object(lihkg_api.corpus, "kv_get", return_value=None), mock.patch.object(lihkg_api, "pace"), \
             mock.patch("curl_cffi.requests.Session", return_value=session):
            text, author, eng = cache_fetch.fetch_one("https://lihkg.com/thread/9/page/1")
        lihkg_api.reset()
        self.assertEqual((author, eng), ("甲", {"replies": 2}))
        self.assertTrue(session.get.call_args.kwargs["headers"]["X-LI-DEVICE"])

    def test_inmedia_is_a_browser_site(self):
        self.assertIn("inmediahk", [s[0] for s in browser_fetch.SITES])
        import sources_extra
        self.assertNotIn("inmediahk", [s[0] for s in sources_extra.EXTRA])

    def test_rss_items(self):
        xml = ('<?xml version="1.0"?><rss><channel><item><title>議會花絮</title><link>https://www.inmediahk.net/node/1</link>'
               '<pubDate>Tue, 14 Oct 2026 10:00:00 +0800</pubDate><category>政治</category><dc:creator>甲</dc:creator></item>'
               '<item><title></title><link>https://x</link></item></channel></rss>')
        got = browser_fetch.rss_items(xml)
        self.assertEqual(got, [{"title": "議會花絮", "url": "https://www.inmediahk.net/node/1", "section": "政治",
                                "published": "Tue, 14 Oct 2026 10:00:00 +0800", "author": "甲"}])
        self.assertEqual(sources.parse_time(got[0]["published"]).isoformat(), "2026-10-14T10:00:00+08:00")


BODY = ("<urlset>" + "<url><loc>https://x/新聞</loc></url>" * 400 + "</urlset>").encode()
CUT = 3000  # bytes the server sends before it drops the connection


class Truncating(http.server.BaseHTTPRequestHandler):
    """sends at most CUT bytes of BODY per response; honours Range like 橙新聞 does"""
    def do_GET(self):
        start = int(self.headers["Range"].split("=")[1].rstrip("-")) if self.headers.get("Range") else 0
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(BODY) - start))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        self.end_headers()
        self.wfile.write(BODY[start:start + CUT])
        self.close_connection = True

    def log_message(self, *a):
        pass


class Resume(unittest.TestCase):
    def test_truncated_body_is_resumed(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Truncating)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            got = sources.fetch(f"http://127.0.0.1:{srv.server_port}/s.xml", headers={"Accept-Encoding": "identity"})
        finally:
            srv.shutdown()
        self.assertEqual(got, BODY.decode())  # 15,217 bytes in 6 cut-off pieces (the first read + 5 resumes), multi-byte characters intact


if __name__ == "__main__":
    unittest.main()
