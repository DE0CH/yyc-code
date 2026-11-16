#!/usr/bin/env python3
"""財經事務及庫務局 LinkedIn (fstb_linkedin) is read by sources_extra.linkedin_read: the logged-out company page directly
(curl_cffi Chrome; every cluster node read it 22 Oct 2026), then YYC's ISP IP, then the keyless r.jina.ai reader over a
fresh yycstream exit per try (Jina answered the cluster's IP 401 and blocked anonymous linkedin.com reads with 403
AbuseAlleviationError on 16 Oct). A route counts only with posts parsed (an authwall is a 200 with none). No network.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_linkedin_read.py
"""
import contextlib, os, sys, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
from curl_cffi import requests as cr  # noqa: E402
import corpus, resi, sources, sources_extra  # noqa: E402

PAGE = ('<script type="application/ld+json">{"@type":"SocialMediaPosting","headline":"Gold Master Plan",'
        '"url":"https://www.linkedin.com/posts/fstbhk_x","datePublished":"2026-10-20T07:14:43Z"}</script>')


class R:
    def __init__(self, code, text):
        self.status_code, self.text, self.content = code, text, text.encode()


class LinkedInRead(unittest.TestCase):
    def spec(self):
        return next(s for s in sources_extra.EXTRA if s[0] == "fstb_linkedin")

    def setUp(self):
        self.calls = []
        p = [mock.patch.object(corpus, "lock", lambda *a, **k: contextlib.nullcontext(True)),
             mock.patch.object(resi, "count_isp", lambda n: None),
             mock.patch.object(resi, "configured", lambda: True),
             mock.patch.object(resi, "url", lambda c="hk", s=None, *a, **k: f"http://u-{s}:p@proxy:1"),
             mock.patch.object(corpus, "kv_set", lambda k, v: self.calls.append(("kv", k, v))),
             mock.patch.object(corpus, "kv_get", lambda k, d=None: d)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def get(self, answers):
        def g(url, impersonate=None, timeout=None, proxies=None):
            self.calls.append(("get", (proxies or {}).get("https")))
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        return mock.patch.object(cr, "get", g)

    def test_spec_is_the_page_itself(self):
        sp = self.spec()
        self.assertEqual(sp[2], "https://www.linkedin.com/company/fstbhk")
        self.assertFalse(sp[4])
        self.assertFalse(sources.escalates(sp))  # its own routes, never the generic yycstream retry

    def test_direct_first(self):
        with self.get([R(200, PAGE)]):
            rows = sources.fetch_source(self.spec())
        self.assertEqual(rows[0]["title"], "Gold Master Plan")
        self.assertEqual(sources.ROUTE_USED["fstb_linkedin"], "direct")
        self.assertEqual(self.calls, [("get", None)])

    def test_999_then_isp(self):
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp:1"}), self.get([R(999, ""), R(200, PAGE)]):
            text, route = sources_extra.linkedin_read("https://www.linkedin.com/company/fstbhk")
        self.assertEqual(route, "isp")
        self.assertEqual(self.calls[1], ("get", "http://isp:1"))

    def test_authwall_200_is_no_read_then_jina_on_fresh_exits(self):
        seen = []

        def fetch(url, headers=None, proxy=None, timeout=30, **k):
            seen.append(proxy)
            if len(seen) < 3:
                raise urllib.error.HTTPError(url, 403, "AbuseAlleviationError: Anonymous access blocked", {}, None)
            return PAGE
        with mock.patch.dict(os.environ, {"ISP_PROXY": ""}), self.get([R(200, "<html>authwall</html>")]), \
                mock.patch.object(sources_extra, "fetch", fetch):
            text, route = sources_extra.linkedin_read("https://www.linkedin.com/company/fstbhk")
        self.assertEqual(route, "jina")
        self.assertEqual(len(set(seen)), 3)  # a new sticky session (exit) every try
        logs = [c for c in self.calls if c[0] == "kv"]
        self.assertEqual([r["status"] for r in logs[-1][2]], [200])
        self.assertIn("AbuseAlleviationError", logs[0][2][0]["error"])

    def test_all_fail_names_each_route(self):
        def fetch(url, **k):
            raise urllib.error.HTTPError(url, 401, "AuthenticationRequiredError", {}, None)
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp:1"}), self.get([R(999, ""), R(999, "")]), \
                mock.patch.object(sources_extra, "fetch", fetch):
            with self.assertRaises(RuntimeError) as c:
                sources_extra.linkedin_read("https://www.linkedin.com/company/fstbhk")
        self.assertIn("direct 999; isp 999; jina 401; jina 401; jina 401", str(c.exception))


if __name__ == "__main__":
    unittest.main()
