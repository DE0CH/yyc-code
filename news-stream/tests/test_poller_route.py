#!/usr/bin/env python3
"""The poller's direct sources fall back to yycstream (Deyao, 2026-10-21 "fix the failing sources": a WARP exit
got 403 from 熱血時報 while another exit read it), on a throwaway database (TEST_DATABASE_URL), no network:

1. a plain direct source whose read fails is read again over yycstream hk at once, and stays there for ROUTE_KEEP
   (its status line says route yycstream), then direct is tried again;
2. YouTube, proxied, LIHKG API, head-only and POST sources never escalate; a source failing on both routes fails;
3. yycstream out of traffic (402) during the fallback leaves the direct read's error (a failure, not "off").

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_poller_route.py
"""
import os, sys, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, resi, sources  # noqa: E402

PAGE = '<a href="https://pt.example/article/21-10-2026/1">熱血時報的一篇新聞標題</a>'
PARSER = sources.rx(r'<a href="(?P<url>[^"]+)">(?P<title>[^<]+)</a>')
SPEC = ("t_pt", "熱血時報", "https://pt.example/", PARSER, False)


class Route(unittest.TestCase):
    def setUp(self):
        sources._route.clear()
        sources._last.clear()
        self.calls = []

    def run_all(self, specs, fetch):
        def f(url, **kw):
            self.calls.append(kw.get("proxy"))
            return fetch(url, **kw)
        with mock.patch.object(sources, "SOURCES", []), mock.patch.object(sources, "browser_file", return_value=([], {})), \
                mock.patch.object(sources, "instagram_file", return_value=([], {})), \
                mock.patch.object(sources, "tikhub_file", return_value=([], {})), \
                mock.patch.object(sources, "watch_file", side_effect=LookupError()), \
                mock.patch.object(resi, "configured", return_value=True), \
                mock.patch.object(resi, "url", return_value="http://resi.example:1"), \
                mock.patch.object(sources, "fetch", side_effect=f):
            sources._last.clear()
            return sources.fetch_all(specs)

    @staticmethod
    def blocked(url, proxy=None, **kw):
        if not proxy:
            raise sources.urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return PAGE

    def test_falls_back_and_stays(self):
        items, st = self.run_all([SPEC], self.blocked)
        self.assertEqual((st["t_pt"]["ok"], st["t_pt"]["items"], st["t_pt"].get("route")), (True, 1, "yycstream"))
        self.assertEqual(self.calls, [None, "http://resi.example:1"])
        self.calls.clear()
        _, st = self.run_all([SPEC], self.blocked)
        self.assertEqual(self.calls, ["http://resi.example:1"])  # straight to the route that reads
        sources._route["t_pt"] = time.time() - sources.ROUTE_KEEP - 1
        self.calls.clear()
        _, st = self.run_all([SPEC], lambda url, **kw: PAGE)
        self.assertEqual((self.calls, st["t_pt"].get("route")), ([None], None))  # direct again, and it reads

    def test_never_escalates(self):
        for spec in (("t_yt", "YT", "https://www.youtube.com/playlist?list=UUx", PARSER, False),
                     ("t_px", "PX", "https://px.example/", PARSER, True)):
            self.assertFalse(sources.escalates(spec), spec[0])
        with mock.patch.object(resi, "configured", return_value=True):
            self.assertTrue(sources.escalates(SPEC))

    def test_both_fail_and_402(self):
        def down(url, **kw):
            raise sources.urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
        _, st = self.run_all([SPEC], down)
        self.assertEqual((st["t_pt"]["ok"], "t_pt" in sources._route), (False, False))

        def out(url, proxy=None, **kw):
            if proxy:
                raise resi.ProxyOut("HTTP 402")
            raise sources.urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        _, st = self.run_all([SPEC], out)
        self.assertEqual((st["t_pt"]["ok"], st["t_pt"].get("off")), (False, None))
        self.assertIn("403", st["t_pt"]["error"])


if __name__ == "__main__":
    unittest.main()
