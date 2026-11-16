#!/usr/bin/env python3
"""Text only: no job downloads pictures, video, audio or fonts (Deyao, 2026-10-20: "images and audio shouldn't be
downloaded either, because we want to optimise IPRoyal bandwidth as well"). Only media URLs (strings) are stored,
never fetched, on every route (IPRoyal yycstream, the ISP IP, the server's own IP).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_nomedia.py

1. The rule (nomedia.blocked): pictures, video, audio, fonts and video players are blocked by resource type, by
   file extension and by media CDN host (googlevideo, ytimg, Facebook's scontent / video hosts, Instagram's,
   WhatsApp's); page documents, scripts, stylesheets, XHR and Facebook's static JS are not; Cloudflare's challenge
   is never blocked.
2. A real Chromium with nomedia.block on its context: a page with <img>, <video>, <audio>, an @font-face, a CSS
   background picture, a fetch() of an .m3u8 and a YouTube player frame loads its HTML, CSS and JS, and the local
   server receives NOT ONE request for a picture, a video, audio or a font.
3. Every browser a job starts blocks media: each launch_persistent_context / new_context in the news stream's code
   (fb_fetch, comments_fetch, browser_fetch, cache_fetch, paywall, wisers relay and login) is followed
   by nomedia.block on that context. Wisers' login page keeps its pictures (allow: the captcha), the only picture any job needs.
4. The page readers never read a media body: sources.fetch (urllib) refuses a response whose Content-Type is a
   picture / video / audio / font with HTTP 415 before reading its body (only the headers crossed), and
   cache_fetch.get refuses a URL whose path is a media file (.jpg, .mp4, .mp3 …) without any request.
5. The one exception (Deyao, 2026-10-22): TikTok videos (tiktok_video.py). Only the modules nomedia.EXCEPTIONS names
   run a downloader; tiktok_video takes only TikTok video URLs and runs yt-dlp with no proxy.
"""
import http.server, os, re, sys, tempfile, threading, unittest, urllib.error

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")

import nomedia  # noqa: E402

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/s.css">
<script src="/a.js"></script></head><body><h1 id="t">text</h1>
<img src="/pic.jpg"><img src="/photo?id=1"><picture><source srcset="/p.webp"><img src="/p2.png"></picture>
<video src="/clip.mp4" autoplay muted preload="auto"></video><audio src="/sound.mp3" preload="auto"></audio>
<iframe src="https://www.youtube.com/embed/abc"></iframe>
<div style="background:url(/bg.gif);width:10px;height:10px"></div><span style="font-family:F">&#23383;</span>
<script>fetch('/live.m3u8').catch(()=>{}); fetch('/data.json').then(r=>r.text()).then(t=>document.title=t)</script>
</body></html>"""
CSS = b"@font-face{font-family:F;src:url(/f.woff2)} body{color:#000}"
MEDIA_PATHS = {"/pic.jpg", "/photo", "/p.webp", "/p2.png", "/clip.mp4", "/sound.mp3", "/bg.gif", "/f.woff2",
               "/live.m3u8", "/big"}


class Server:
    def __init__(self):
        self.hits, self.sent = [], {}
        srv = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                srv.hits.append(path)
                body, ct = {"/": (PAGE, "text/html"), "/s.css": (CSS, "text/css"),
                            "/a.js": (b"window.ok=1", "application/javascript"),
                            "/data.json": (b"json-ok", "application/json"),
                            "/photo": (b"\xff\xd8" + b"x" * 5000, "image/jpeg"),
                            "/big": (b"\xff\xd8" + b"x" * 2_000_000, "image/jpeg"),
                            "/article": (b"<html><body>article</body></html>", "text/html; charset=utf-8")
                            }.get(path, (b"x" * 5000, "application/octet-stream"))
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                    srv.sent[path] = srv.sent.get(path, 0) + len(body)
                except OSError:
                    pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class Rule(unittest.TestCase):
    def test_blocked(self):
        b = nomedia.blocked
        for t, u in [("image", "https://example.com/x"), ("media", "https://example.com/v"), ("font", "https://e.com/f"),
                     ("xhr", "https://e.com/seg/live.m3u8"), ("fetch", "https://e.com/a/b.mp4?x=1"),
                     ("other", "https://e.com/c.webp"), ("xhr", "https://rr3---sn-a.googlevideo.com/videoplayback?x"),
                     ("xhr", "https://video.fhkg4-1.fna.fbcdn.net/o1/v/t2/f2/m69/abc?efg=1"),
                     ("fetch", "https://scontent.fhkg4-2.fna.fbcdn.net/v/t39.30808-6/1.jpg"),
                     ("xhr", "https://instagram.fhkg4-1.fna.fbcdn.net/v/t51/1"),
                     ("xhr", "https://scontent-hkg4-1.cdninstagram.com/v/t51.2885-15/1"),
                     ("document", "https://www.youtube.com/embed/abc"), ("document", "https://player.vimeo.com/video/1"),
                     ("xhr", "https://mmg.whatsapp.net/v/t62.7118-24/1.enc"), ("other", "https://i.ytimg.com/vi/a/hq.jpg")]:
            self.assertTrue(b(t, u), (t, u))
        for t, u in [("document", "https://www.facebook.com/x"), ("script", "https://static.xx.fbcdn.net/rsrc.php/a.js"),
                     ("stylesheet", "https://static.xx.fbcdn.net/rsrc.php/a.css"), ("xhr", "https://www.facebook.com/api/graphql/"),
                     ("script", "https://static.cdninstagram.com/rsrc.php/a.js"), ("fetch", "https://e.com/data.json"),
                     ("document", "https://www.singpao.com.hk/index.php?fi=news1"), ("script", "https://e.com/app.js?v=.mp4x"),
                     ("image", "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/x.png"),
                     ("script", "https://challenges.cloudflare.com/turnstile/v0/api.js")]:
            self.assertFalse(b(t, u), (t, u))

    def test_url_and_type(self):
        self.assertTrue(nomedia.media_url("https://arch.metroradio.hk/104/20261015/104_202610150600.mp3"))
        self.assertTrue(nomedia.media_url("https://e.com/a/b.JPG?w=1"))
        self.assertFalse(nomedia.media_url("https://e.com/news/123.html"))
        self.assertFalse(nomedia.media_url("https://e.com/jpg/123"))
        for ct in ("image/jpeg", "video/mp4", "audio/mpeg; x=1", "font/woff2", "application/vnd.apple.mpegurl"):
            self.assertTrue(nomedia.media_type(ct), ct)
        for ct in ("text/html; charset=utf-8", "application/json", "application/xml", "", None):
            self.assertFalse(nomedia.media_type(ct), ct)


class RealBrowser(unittest.TestCase):
    def test_chromium_fetches_no_media(self):
        from playwright.sync_api import sync_playwright
        s = Server()
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                ctx = b.new_context()
                n = nomedia.block(ctx)
                pg = ctx.new_page()
                pg.goto(s.url + "/", wait_until="load")
                pg.wait_for_function("document.title == 'json-ok'", timeout=10000)
                pg.wait_for_timeout(1500)
                self.assertEqual(pg.inner_text("#t"), "text")
                self.assertEqual(pg.evaluate("window.ok"), 1)  # scripts run
                b.close()
        finally:
            s.close()
        got = set(s.hits)
        self.assertTrue({"/", "/s.css", "/a.js", "/data.json"} <= got, got)
        self.assertFalse(got & MEDIA_PATHS, f"media requested: {sorted(got & MEDIA_PATHS)}")
        self.assertGreaterEqual(n["blocked"], 8, n)


SRC = {"chrome_state.py": 1, "paywall.py": 1, "wisers/relay.py": 1, "wisers/login.py": 1}
# browser, sites-browser, cache, fulltext, facebook and comments make theirs through chrome_state.launch (one place blocks)
VIA_CHROME_STATE = ("fb_fetch.py", "comments_fetch.py", "browser_fetch.py", "cache_fetch.py")


class EveryBrowser(unittest.TestCase):
    def test_every_context_blocks_media(self):
        """each browser context a job makes gets nomedia.block (a context made and not blocked fails here)"""
        made = re.compile(r"(\w+(?:\.\w+)*)\s*=\s*[\w.]+\.(?:launch_persistent_context|new_context)\(")  # ctx, cls._ctx
        found = {}
        for root, _, files in os.walk(HERE):
            if "/tests" in root or "node_modules" in root or "/web-app" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                rel = os.path.relpath(path, HERE)
                src = open(path, encoding="utf-8").read()
                for m in made.finditer(src):
                    var, after = m.group(1), src[m.end():m.end() + 1500]
                    self.assertRegex(after, r"nomedia\.block\(\s*%s\b" % re.escape(var),
                                     f"{rel}: the browser context {var!r} does not block media (nomedia.block)")
                    found[rel] = found.get(rel, 0) + 1
        for rel, k in SRC.items():
            self.assertGreaterEqual(found.get(rel, 0), k, f"{rel}: no browser context found")
        for rel in VIA_CHROME_STATE:
            self.assertIn("chrome_state.launch(", open(os.path.join(HERE, rel), encoding="utf-8").read(), rel)

    def test_wisers_login_keeps_its_captcha(self):
        import importlib.util
        sys.path.insert(0, os.path.join(HERE, "wisers"))
        spec = importlib.util.spec_from_file_location("wlogin", os.path.join(HERE, "wisers", "login.py"))
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        self.assertTrue(w.keep(w.LOGIN_URL))  # a request made by the login page (its captcha picture)
        self.assertFalse(w.keep(w.HOME))


class TikTokException(unittest.TestCase):
    """5. The one exception (Deyao, 2026-10-22): tiktok_video.py downloads TikTok videos, nothing else. Only the
    modules nomedia.EXCEPTIONS names run a downloader (yt-dlp); tiktok_video queues only TikTok video URLs, and runs
    yt-dlp with no proxy (--proxy "" and no *_proxy variable in its environment)."""

    def test_only_the_exceptions_run_a_downloader(self):
        found = set()
        for root, _, files in os.walk(HERE):
            if "/tests" in root or "node_modules" in root or "/web-app" in root:
                continue
            for f in files:
                if f.endswith(".py") and re.search(r"""["']yt-dlp["']|yt_dlp""", open(os.path.join(root, f), encoding="utf-8").read()):
                    found.add(os.path.relpath(os.path.join(root, f), HERE))
        self.assertEqual(found, set(nomedia.EXCEPTIONS), "a module runs yt-dlp without being a nomedia.EXCEPTIONS entry")

    def test_only_tiktok_video_urls(self):
        import tiktok_video as tv
        self.assertEqual(tv.video_id("https://www.tiktok.com/@rthk_news/video/7563000000000000001"), "7563000000000000001")
        for u in ("https://www.tiktok.com/@a/photo/7563000000000000001", "https://www.youtube.com/watch?v=x",
                  "https://www.instagram.com/reel/abc/", "https://www.tiktok.com/@a", "https://e.com/tiktok.com/@a/video/1",
                  "https://www.tiktok.com/@a/video/7563000000000000001?comment=1", None, ""):
            self.assertIsNone(tv.video_id(u), u)
        self.assertFalse(tv.queue(1, "https://www.youtube.com/watch?v=x", 90, "CEHK", "stage1"))

    def test_no_proxy(self):
        import subprocess
        import tiktok_video as tv
        seen = {}

        def fake(cmd, capture_output, text, timeout, env):
            seen.setdefault("cmds", []).append(cmd)
            seen["env"] = env
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: [TikTok] 1: Video not available")
        os.environ["HTTPS_PROXY"] = os.environ["http_proxy"] = "http://user:pw@geo.iproyal.com:12321"
        old = subprocess.run
        subprocess.run = fake
        try:
            with self.assertRaises(tv.Gone):
                tv.download("https://www.tiktok.com/@a/video/1", tempfile.mkdtemp())
        finally:
            subprocess.run = old
            os.environ.pop("HTTPS_PROXY"), os.environ.pop("http_proxy")
        cmd = seen["cmds"][0]
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertEqual(cmd[cmd.index("--proxy") + 1], "")
        self.assertFalse([k for k in seen["env"] if k.lower().endswith("_proxy")])


class Readers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = Server()

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def test_fetch_refuses_media_body(self):
        import sources
        with self.assertRaises(urllib.error.HTTPError) as e:
            sources.fetch(self.s.url + "/big")
        self.assertEqual(e.exception.code, 415)
        self.assertEqual(sources.fetch(self.s.url + "/article"), "<html><body>article</body></html>")

    def test_cache_get_never_requests_a_media_url(self):
        import cache_fetch
        cache_fetch.pace = lambda *a, **k: None
        before = len(self.s.hits)
        for u in ("/clip.mp4", "/pic.jpg?w=1", "/sound.mp3"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                cache_fetch.get(self.s.url + u)
            self.assertEqual(e.exception.code, 415)
        self.assertEqual(len(self.s.hits), before, "a media URL was requested")
        self.assertIn("article", cache_fetch.get(self.s.url + "/article"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
