#!/usr/bin/env python3
"""One IPRoyal sub-user for every job (Deyao, 2026-10-15: "have all things share the same IPRoyal sub-user").

Before, YYC's traffic was split over yycstream (RESI_PROXY), yycfacebook (FB_PROXY), yycinstagram (IG_PROXY) and
yycpaywall (PAYWALL_PROXY), each with its own traffic allocation: one ran out while the others had plenty. Now:

1. One env name, RESI_PROXY = the bare login of sub-user yycstream (http://yycstream:<password>@geo.iproyal.com:12321,
   no suffix); FB_PROXY, IG_PROXY, PAYWALL_PROXY and BROWSER_PROXY are gone from the code, the manifests and the
   cluster's app-env Secret.
2. Each request builds its own password suffix on that login (resi.url): the country (HK by default; GB for Reuters,
   US/HK for Bloomberg), and a sticky session with its lifetime where the job needs one IP (replies 24 h, YouTube
   30 min, Reuters/archive.today 24 h).
3. Every proxied byte is counted per site in kv proxy:bytes:<site>:<HKT day | month | total> (resi.count), so the
   monitor still shows the split IPRoyal's per-sub-user report used to give; the monitor's IPRoyal card shows the one
   sub-user (yycstream) and that split.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_one_proxy.py
"""
import glob, os, re, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
for k in ("RESI_PROXY", "IPROYAL_API"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, monitor, resi  # noqa: E402

HKT = timezone(timedelta(hours=8))
BASE = "http://yycstream:pw123@geo.iproyal.com:12321"
GONE = ("FB_PROXY", "IG_PROXY", "PAYWALL_PROXY", "BROWSER_PROXY")


def env(**kw):
    return mock.patch.dict(os.environ, {"RESI_PROXY": BASE, **kw})


class OneLogin(unittest.TestCase):
    def test_suffix_per_request(self):
        with env():
            self.assertEqual(resi.url(), "http://yycstream:pw123_country-hk@geo.iproyal.com:12321")
            self.assertEqual(resi.url("gb", session="abc12345", lifetime="24h"),
                             "http://yycstream:pw123_country-gb_session-abc12345_lifetime-24h@geo.iproyal.com:12321")
            self.assertEqual(resi.parts("us", session="s1", lifetime="30m"),
                             ("http", "geo.iproyal.com", 12321, "yycstream", "pw123_country-us_session-s1_lifetime-30m"))
        with mock.patch.dict(os.environ, {"RESI_PROXY": "http://u:p%40ss@h:1"}):
            self.assertEqual(resi.url(), "http://u:p%40ss_country-hk@h:1")  # the password stays quoted in the URL
            self.assertEqual(resi.parts()[4], "p@ss_country-hk")
        with mock.patch.dict(os.environ, {"RESI_PROXY": BASE.replace("pw123", "pw123_country-us_session-x")}):
            self.assertEqual(resi.url("gb"), "http://yycstream:pw123_country-gb@geo.iproyal.com:12321")  # resi sets the suffix
        self.assertIsNone(resi.url())
        self.assertFalse(resi.configured())

    def test_browser_sends_the_login_unasked(self):
        """Chrome sends proxy credentials only after a 407; since 16 Oct 2026 (after the top-up) IPRoyal answers an
        unauthenticated CONNECT with 402 instead, so every browser job on the proxy failed with
        ERR_TUNNEL_CONNECTION_FAILED (Facebook's session refresh: no Facebook post read) while curl/urllib, which send
        the login up front, worked. resi.playwright gives the browser a local forwarder that adds the login to
        every request it passes on"""
        import base64, socket, socketserver, threading
        seen = []

        class Up(socketserver.BaseRequestHandler):
            def handle(self):
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                seen.append(head.decode())
                want = "Proxy-Authorization: Basic " + base64.b64encode(b"yycstream:pw123_country-gb_session-s9_lifetime-30m").decode()
                if want in head.decode():
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    self.request.sendall(self.request.recv(100))  # echo the first bytes through the tunnel
                else:
                    self.request.sendall(b"HTTP/1.1 402 Payment Required\r\n\r\n")

        class Srv(socketserver.ThreadingTCPServer):
            daemon_threads = allow_reuse_address = True
        up = Srv(("127.0.0.1", 0), Up)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        self.addCleanup(up.shutdown)
        with mock.patch.dict(os.environ, {"RESI_PROXY": f"http://yycstream:pw123@127.0.0.1:{up.server_address[1]}"}):
            px = resi.playwright("gb", session="s9", lifetime="30m")
        self.assertNotIn("username", px)
        self.assertNotIn("password", px)
        host, port = px["server"].split("://")[1].split(":")
        self.assertEqual(host, "127.0.0.1")
        c = socket.create_connection((host, int(port)), timeout=5)
        c.sendall(b"CONNECT ipv4.icanhazip.com:443 HTTP/1.1\r\nHost: ipv4.icanhazip.com:443\r\n\r\n")
        self.assertIn(b"200 Connection Established", c.recv(200))
        c.sendall(b"hello")
        self.assertEqual(c.recv(5), b"hello")
        c.close()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith("CONNECT ipv4.icanhazip.com:443 HTTP/1.1\r\n"))

    def test_no_browser_gets_the_login(self):
        """every browser on the proxy goes through resi's forwarder (above): no module hands Chrome a proxy login"""
        for f in glob.glob("*.py"):
            with open(f, encoding="utf-8") as fh:
                self.assertNotRegex(fh.read(), r'proxy["\']?\]?\s*[=:]\s*\{[^\n]*"username"', f)

    def test_old_names_gone(self):
        files = [f for f in glob.glob("*.py") + glob.glob("infra/*") + glob.glob("deploy/**/*.y*ml", recursive=True)
                 + glob.glob("deploy/**/*.sh", recursive=True) if os.path.isfile(f)]
        for f in files:
            with open(f, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            for name in GONE:
                self.assertNotIn(name, src, f)
        with open("deploy/apps/sealed/app-env.yaml") as fh:  # a SealedSecret keeps the key names in clear
            secret = fh.read()
        self.assertRegex(secret, r"(?m)^\s+RESI_PROXY: Ag")
        self.assertEqual(re.findall(r"(?m)^\s+(\w*PROXY\w*):", secret), ["RESI_PROXY"])

    def test_every_caller_builds_on_the_one_login(self):
        import media, paywall, replies_fetch
        with env():
            self.assertEqual(resi.parts("hk", session="abcdefgh", lifetime="30m")[3:],
                             ("yycstream", "pw123_country-hk_session-abcdefgh_lifetime-30m"))  # media.youtube_captions
            with resi.metered("hk", session="abcdefgh", lifetime="30m") as (local, n):  # its counting forwarder
                self.assertRegex(local, r"^http://127\.0\.0\.1:\d+$")
                self.assertEqual(n, {"bytes": 0})
            px = replies_fetch.Proxied("discuss")
            self.assertRegex(px.proxy(), r"^http://yycstream:pw123_country-hk_session-[a-z0-9]{10}_lifetime-24h@geo\.iproyal\.com:12321$")
            self.assertEqual(paywall.proxy_url("abc", "gb"),
                             "http://yycstream:pw123_country-gb_session-abc_lifetime-24h@geo.iproyal.com:12321")
        with mock.patch.dict(os.environ, {"RESI_PROXY": ""}):
            self.assertRaises(paywall.Blocked, paywall.proxy_url, "abc")


class Bytes(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'proxy:bytes:%%'")

    def test_site_labels(self):
        for url, label in (("https://www1.hkej.com/dailynews/x", "hkej"), ("https://m.discuss.com.hk/index.php", "discuss"),
                           ("https://www.facebook.com/api/graphql/", "facebook"), ("https://www.instagram.com/x/", "instagram"),
                           ("https://www.threads.com/@x", "threads"), ("https://news.google.com/rss/articles/x", "google"),
                           ("https://www.reuters.com/world/x", "reuters"), ("https://archive.ph/submit/", "archive"),
                           ("https://www.youtube.com/watch?v=x", "youtube"), ("https://news.tvb.com/tc/x", "tvb"),
                           ("lihkg", "lihkg")):
            self.assertEqual(resi.site(url), label, url)

    def test_count_per_site_day_month_total(self):
        day = datetime.now(HKT)
        resi.count(1000, "https://www1.hkej.com/x")
        resi.count(500, "hkej")
        resi.count(0, "facebook")
        self.assertEqual(corpus.kv_get(f"proxy:bytes:hkej:{day:%Y-%m-%d}"), 1500)
        self.assertEqual(corpus.kv_get(f"proxy:bytes:hkej:{day:%Y-%m}"), 1500)
        self.assertEqual(corpus.kv_get("proxy:bytes:hkej:total"), 1500)
        self.assertEqual(corpus.kv_get(f"proxy:bytes:hkej:{day:%Y-%m-%dT%H}"), 1500)
        self.assertIsNone(corpus.kv_get(f"proxy:bytes:facebook:{day:%Y-%m-%d}"))
        self.assertEqual(resi.by_site(day), {"hkej": {"today_mb": 0.0015, "month_mb": 0.0015}})

    def test_sources_fetch_counts_when_proxied(self):
        import io, sources

        class R(io.BytesIO):
            headers, status = {}, 200

        class Op:
            def open(self, req, timeout=None):
                return R(b"x" * 2000)
        with env(), mock.patch.object(sources.urllib.request, "build_opener", lambda *a: Op()):
            sources.fetch("https://www1.hkej.com/dailynews/x", proxy=resi.url())
            sources.fetch("https://www.rthk.hk/x")  # direct: not counted
        self.assertEqual(corpus.kv_get(f"proxy:bytes:hkej:{datetime.now(HKT):%Y-%m-%d}"), 2000 + resi.OVERHEAD)
        self.assertIsNone(corpus.kv_get(f"proxy:bytes:rthk:{datetime.now(HKT):%Y-%m-%d}"))

    def test_counting_session(self):
        class Resp:
            request_size, download_size, header_size, content = 300, 4000, 200, b""

        with env(), mock.patch("curl_cffi.requests.Session.request", lambda self, method, url, **k: Resp()):
            s = resi.session()
            s.get("https://www.instagram.com/x/", proxies={"https": resi.url()})
            s.get("https://www.example.com/")  # direct: not counted
            p = resi.session(proxy=resi.url())
            p.post("https://www.facebook.com/api/graphql/", data="q")
        today = f"{datetime.now(HKT):%Y-%m-%d}"
        self.assertEqual(corpus.kv_get(f"proxy:bytes:instagram:{today}"), 4500)
        self.assertEqual(corpus.kv_get(f"proxy:bytes:facebook:{today}"), 4500)
        self.assertIsNone(corpus.kv_get(f"proxy:bytes:example:{today}"))

    def test_replies_count_under_proxy_bytes(self):
        import replies_fetch
        with env():
            px = replies_fetch.Proxied("discuss")
            px.bytes = 1234
            px.account()
        self.assertEqual(corpus.kv_get(f"proxy:bytes:discuss:{datetime.now(HKT):%Y-%m-%d}"), 1234)
        self.assertEqual(replies_fetch.mb_today("discuss"), 0.0)  # rounded to 0.01 MB
        self.assertFalse(list(corpus.db().execute("SELECT k FROM kv WHERE k LIKE 'replies:bytes:%%'")))


class Monitor(unittest.TestCase):
    def test_card_is_the_one_subuser_with_the_split(self):
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'proxy:bytes:%%'")
        resi.count(2_000_000, "facebook")
        subs = [{"hash": "A", "username": "yycstream", "traffic_available": 3.5, "traffic_used": 1.25},
                {"hash": "B", "username": "yycother", "traffic_available": 9.0, "traffic_used": 0.0},
                {"hash": "C", "username": "someone", "traffic_available": 1.0, "traffic_used": 0.0}]

        def get(url, h=None, raw=False):
            if "data-usage-report" in url:
                self.assertIn("hash=A", url)
                return "date,host,data\n2026-10-15,x,0.5\n"
            if "/residential-subusers" in url:
                return {"data": subs}
            if "/orders" in url:
                return {"data": [{"id": 1, "status": "confirmed", "quantity": 10, "amount": "35.00", "created_at": "2026-10-01"}]}
            return {"available_traffic": 0}
        with mock.patch.dict(os.environ, {"IPROYAL_API": "k"}), mock.patch.object(monitor, "get", get):
            b = monitor.bal_iproyal()
        self.assertEqual(monitor.IPROYAL_SUBUSER, "yycstream")
        self.assertEqual([s["username"] for s in b["subusers"]], ["yycstream"])
        self.assertEqual(b["account"], "sub-user yycstream")
        self.assertEqual((b["left_gb"], b["used_gb"]), (3.5, 1.25))
        self.assertEqual(b["by_site"]["facebook"]["today_mb"], 2.0)
        for page in ("web-app/src/routes/-MonitorPage.tsx",):
            with open(page, encoding="utf-8") as fh:
                self.assertIn("by_site", fh.read(), page)


if __name__ == "__main__":
    unittest.main()
