#!/usr/bin/env python3
"""SiteGround's robot challenge (sgcaptcha) and eQualit.ie Deflect's (追光者 pulsehknews.com) are passed, so its sites' articles keep their full text.

2026-10-20: 集誌社's article 「宏福苑聽證｜獨委會將交報告…」 (thecollectivehk.com, a SiteGround site behind Cloudflare)
showed 「來源只提供標題，系統沒有保存內文。」. From the cluster's IP the site answers every page with HTTP 202 and a
370-byte meta refresh to /.well-known/sgcaptcha/ (header sg-captcha: challenge); the reader took that page as the
article, found no text and saved "" as read. The challenge is a proof of work (SHA-1 of the challenge string plus a
counter with N leading zero bits); its answer sets the cookie _I_, after which the site serves the page.

Deflect (pulsehknews.com, 2026-10-20): HTTP 429 「Verifying if your connection is secure..」 on every page from the
cluster and from the session; its answer is a SHA-256 proof of work written back into the cookie deflect_challenge3.

A local server plays SiteGround (and Deflect under /deflect/): the same 202 stub, the challenge page, the check of the answer and the cookie.

    python3 tests/test_sgcaptcha.py
"""
import base64, hashlib, http.server, os, re, sys, threading, unittest, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_fetch, sources, walls  # noqa: E402

BITS = 12  # the live site asks 21 (about a second in Python); fewer keeps the test quick
CHALLENGE = f"{BITS}:1792486370:1d520568:83546cfb47ea9618f5d634407a65d4ee2c86213674386171c5cd997b06125b8c:"
ARTICLE = ("<html><head><title>宏福苑聽證｜獨委會將交報告 - 集誌社</title></head><body><article>"
           + "".join(f"<p>調查宏福苑火災的獨立委員會預計本周內向特首提交報告，這是第{i}段內文。</p>" for i in range(10))
           + "</article></body></html>")
STUB = ('<html><head><link rel="icon" href="data:;"><meta http-equiv="refresh" '
        'content="0;/.well-known/sgcaptcha/?r=%2Farticle%2F&y=ipc:104.28.207.215:1792486343.247"></meta></head></html>')
PAGE = ('<!doctype html><html><head><title>Robot Challenge Screen</title><script>const sgchallenge="' + CHALLENGE + '";\n'
        'const sgsubmit_url="/.well-known/sgcaptcha/?r=%2Farticle%2F";const sgfallback_url="/.well-known/captcha/";'
        '</script></head><body></body></html>')
COOKIE = "_I_=5f1e0c0ffee"


DEFLECT_BITS = 6
DEFLECT_COOKIE = bytes(range(20)) + bytes(32) + (1792572743).to_bytes(8, "big")
DEFLECT_PAGE = ('<html><head><title>Verifying if your connection is secure..</title></head><body>deflect.ca '
                '<script>new_solver(%d)</script></body></html>' % DEFLECT_BITS)


def deflect_ok(cookie_header):
    m = re.search(r"deflect_challenge3=([^;]+)", cookie_header or "")
    if not m:
        return False
    raw = base64.b64decode(urllib.parse.unquote(m.group(1)))
    d = hashlib.sha256(raw[:52]).digest()
    zeros = next((i * 8 + 8 - b.bit_length() for i, b in enumerate(d) if b), 256)
    return raw[:20] == DEFLECT_COOKIE[:20] and raw[52:] == DEFLECT_COOKIE[52:] and zeros >= DEFLECT_BITS


JSC_TOKEN = "dd56b4c062cdd62e2dfca0655d9fb645"
JSC_PAGE = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><script>(function(){var t="' + JSC_TOKEN +
            '";document.cookie="js_clearance="+t+"; path=/; SameSite=Lax";window.location.replace(window.location.href);})();'
            '</script></body></html>')  # the live page, 2026-10-21 (256 bytes, HTTP 200)


BP_SECRET, BP_N = b"\x98\xf2\x98\x88openrice-seed", 7
BP_JSON = {"v": {"a": base64.b64encode(BP_SECRET).decode(), "b": 1792548606,
                 "c": base64.b64encode(hashlib.sha256(BP_SECRET + str(BP_N).encode()).digest()).decode()}, "s": "c2ln"}
BP_PAGE = ("<!DOCTYPE html><html><head><script>" + "var _0x1=0;" * 2500  # its JSON sits past 20 KB, as on the cluster
           + "JSON['parse'](atob('" + base64.b64encode(__import__("json").dumps(BP_JSON, separators=(",", ":")).encode()).decode().rstrip("=")
           + "'));document['cookie']='_wafchallengeid='+x;</script></head><body>Security Check in Progress...</body></html>")
BP_SLIDER = "<html><script>ttVerifyCenter.render({verify_data: '{\"verify_scene\":\"tob_waf\"}'})</script></html>"


def bp_ok(cookie):
    m = re.search(r"_wafchallengeid=([^;]+)", cookie or "")
    if not m:
        return False
    j = __import__("json").loads(base64.b64decode(m.group(1)))
    return j["v"] == BP_JSON["v"] and j["s"] == BP_JSON["s"] and base64.b64decode(j["d"]) == str(BP_N).encode()


class SiteGround(http.server.BaseHTTPRequestHandler):
    solved = 0

    def log_message(self, *a):
        pass

    def send(self, code, body, headers=()):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=UTF-8")
        self.send_header("Content-Length", str(len(b)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/jsc/"):  # 中國攝影新聞通訊社 (zgsyxwtx.com): a script sets js_clearance and reloads
            if "js_clearance=" + JSC_TOKEN in (self.headers.get("Cookie") or ""):
                SiteGround.solved += 1
                return self.send(200, ARTICLE)
            return self.send(200, JSC_PAGE)
        if u.path.startswith("/byteplus/"):  # 開飯 OpenRice: BytePlus WAF's SHA-256 proof of work, then the page
            if u.path.startswith("/byteplus/slider"):
                return self.send(200, BP_SLIDER)
            if bp_ok(self.headers.get("Cookie")):
                SiteGround.solved += 1
                return self.send(200, ARTICLE, [("Set-Cookie", "_waftokenid=t; Path=/; Max-Age=60")])
            return self.send(200, BP_PAGE)
        if u.path.startswith("/deflect/"):
            if deflect_ok(self.headers.get("Cookie")):
                SiteGround.solved += 1
                return self.send(200, ARTICLE)
            return self.send(429, DEFLECT_PAGE, [("Set-Cookie", "deflect_challenge3="
                                                  + base64.b64encode(DEFLECT_COOKIE).decode() + "; path=/")])
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/.well-known/sgcaptcha/":
            if "sol" not in q:
                return self.send(200, PAGE, [("sg-captcha", "challenge")])
            d = base64.b64decode(q["sol"][0])
            ok = d.startswith(CHALLENGE.encode()) and int.from_bytes(hashlib.sha1(d).digest()[:4], "big") >> (32 - BITS) == 0
            if not ok:
                return self.send(403, "bad answer")
            SiteGround.solved += 1
            return self.send(202, "", [("Set-Cookie", COOKIE + "; path=/; HttpOnly"), ("sg-captcha", "challenge")])
        if COOKIE in (self.headers.get("Cookie") or ""):
            return self.send(200, ARTICLE)
        self.send(202, STUB, [("sg-captcha", "challenge")])


class SgCaptcha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SiteGround)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/article/"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        walls.forget()
        SiteGround.solved = 0

    def test_solution(self):
        d = walls.sg_solve(CHALLENGE)
        self.assertTrue(d.startswith(CHALLENGE.encode()))
        self.assertEqual(int.from_bytes(hashlib.sha1(d).digest()[:4], "big") >> (32 - BITS), 0)

    def test_article_text_behind_the_challenge(self):
        """the original bug: the reader got the 202 stub and kept "" as the article's text"""
        text, _, _ = cache_fetch.fetch_one(self.url)
        self.assertIn("調查宏福苑火災的獨立委員會", text)
        self.assertGreater(len(text), 200)
        self.assertEqual(SiteGround.solved, 1)

    def test_cookie_reused(self):
        """one solve serves the site's later pages (the cookie is kept per host)"""
        for _ in range(3):
            self.assertIn("獨立委員會", sources.fetch(self.url))
        self.assertEqual(SiteGround.solved, 1)

    def test_stub_is_never_an_article(self):
        """a challenge that can't be passed is a Challenge (tried again later), never an empty article"""
        with unittest.mock.patch.object(walls, "sg_solve", side_effect=walls.Unsolved("give up")):
            with self.assertRaises(cache_fetch.Challenge):
                cache_fetch.fetch_one(self.url)

    def test_js_clearance(self):
        """中國攝影新聞通訊社: HTTP 200 with a script that sets the cookie js_clearance and reloads; the cookie is set
        in Python and the page asked again (Chrome with the media block never reached domcontentloaded there)"""
        url = self.url.replace("/article/", "/jsc/")
        self.assertEqual(walls.which(JSC_PAGE), "js_clearance")
        self.assertIn("獨立委員會", sources.fetch(url))
        self.assertIn("獨立委員會", sources.fetch(url))
        self.assertEqual(SiteGround.solved, 2)

    def test_byteplus(self):
        """開飯 OpenRice (2026-10-21): BytePlus WAF's page; the answer (n with SHA-256(a + n) = c) goes back as the
        cookie _wafchallengeid and the page is served; its slider captcha is Unsolved, never a page"""
        self.assertEqual(walls.which(BP_PAGE), "byteplus")
        self.assertIn("獨立委員會", sources.fetch(self.url.replace("/article/", "/byteplus/review/")))
        self.assertEqual(SiteGround.solved, 1)
        with self.assertRaises(walls.Unsolved):
            sources.fetch(self.url.replace("/article/", "/byteplus/slider/"))

    def test_deflect(self):
        """追光者: urllib gets the 429 wall page; the answer written into the cookie gets the article"""
        url = self.url.replace("/article/", "/deflect/20261020editorial/")
        text, _, _ = cache_fetch.fetch_one(url)
        self.assertIn("調查宏福苑火災的獨立委員會", text)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
