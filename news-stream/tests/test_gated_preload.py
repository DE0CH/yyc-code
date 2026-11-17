#!/usr/bin/env python3
"""What only approved accounts see (the full text, the scores and the model's reasons) comes with the page, not after
it (Deyao, 2026-11-17): it is in the HTML the server renders for an approved account, and a hover or touch that
preloads a link reads it together with the public data. On a throwaway database (TEST_DATABASE_URL; its tables are
dropped and made again), against the Worker as it is deployed (the production build under wrangler's local runtime,
GATED_PORT, default 5187; GATED_DEV=1: `vite dev` instead) with the test's own stand-in for Cloudflare Access in front
of it (GATED_PORT + 1).

    TEST_DATABASE_URL=postgresql:///yyc_test_gated python3 tests/test_gated_preload.py

The stand-in does what Access does on the main site: a signed-in browser's cookie (CF_Authorization, for the whole
host) goes to the site untouched on every path, and only under /app does Access look at it: a live session's request
goes on with the signed token (Cf-Access-Jwt-Assertion), anything else is sent to the sign-in page. So a public page
(/, /item/<id>) reaches the site with the cookie and no token. The test signs tokens with its own key (test_edge_ops:
ACCESS_TEST_JWKS in web-app/.dev.vars).

1. The item page's HTML (no browser, nothing run): for an approved account it has the full text and the scorer's
   reason, is never stored (Cache-Control private, no-store), and the server asked Access once (/app/me, with the
   cookie); the public gets the excerpt and the note, no full text or reason, and Access is not asked; a session
   Access no longer accepts (revoked), a cookie signed by another key and a token for another Access app get the
   public page.
2. 串流's HTML: an approved account's has each item's score and reason; the public's has not.
3. Where Access sends the token on every path (the beta site; here: the server asked directly with the token):
   the same HTML, with nobody asked.
4. In a browser, the approved account's item page shows the full text from the server's HTML: no hydration error, and
   the browser itself never asks /app/me, /app/item/text or /app/item/ai.
5. Hovering a 串流 card requests the item's full text and scores before the click; the click then shows the full text
   with no second request. The public's hover requests none of it.
6. Hovering the link to 串流 on the item page requests the day's scores before the click.
7. A phone (WebKit, 390 px, touch): tapping a card shows the item with its full text, each read requested once.
"""
import http.client, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
DSN = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
DBNAME = DSN.rsplit("/", 1)[-1].split("?")[0]
PORT = int(os.environ.get("GATED_PORT", "5187"))
DEVSERVER = os.environ.get("GATED_DEV") == "1"  # `vite dev` instead of the production build (quicker to start)
APORT = PORT + 1
DEV, SITE = f"http://127.0.0.1:{PORT}", f"http://127.0.0.1:{APORT}"
OUT = os.path.expanduser("~/artifacts/gated-preload")
os.environ["DATABASE_URL"] = DSN
os.environ["EDGE_DB"] = DBNAME
os.environ["EDGE_PORT"] = str(PORT)
os.environ.setdefault("FLAGS_FILE", os.path.join(HERE, "deploy/apps/flags/flags.json"))
import psycopg  # noqa: E402

with psycopg.connect(DSN, autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, edge_config, insights  # noqa: E402
import test_edge_ops as ops  # noqa: E402  (token, jwks, the test's keys)
from playwright.sync_api import sync_playwright  # noqa: E402

STAFF = "colleague@yyc.hk"
URL = "https://yyc-gated.example/news/1"
OPENING = "立法會今日舉行行政長官答問會，多名議員就房屋及土地供應提問。"
SECRET = "這一句只在全文的最後，摘錄不會到這裏：答問會歷時九十分鐘結束。"
REASON = "特首主講答問會並回應房屋提問"
WISERS_AUD = "af0134321afca2f355d7782cb0ad2a2c06e8f901208d8e8d3bd121941d3e5cab"
GATED = re.compile(r"/app/(me|item/text|item/ai|item/video|stream/scores)\b")

ASKED = []        # (path, had the cookie) of every request the stand-in saw under /app
REVOKED = set()   # sessions Access no longer accepts


class Access(BaseHTTPRequestHandler):
    """Cloudflare Access on the main site, as far as the site can tell (the module's docstring)"""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        under = path == "/app" or path.startswith("/app/")
        cookie = SimpleCookie(self.headers.get("Cookie", "")).get("CF_Authorization")
        session = cookie.value if cookie else None
        skip = ("host", "connection", "accept-encoding", "cf-access-jwt-assertion", "x-forwarded-host")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in skip}
        headers["X-Forwarded-Host"] = f"127.0.0.1:{APORT}"  # the host the reader asked (yyc-edge sends it on)
        if under:
            ASKED.append((path, bool(session)))
            if not session or session in REVOKED:
                self.send_response(302)
                self.send_header("Location", "https://yyc-hk.cloudflareaccess.com/cdn-cgi/access/login/test")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            headers["Cf-Access-Jwt-Assertion"] = session
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=180)
        try:
            conn.request("GET", self.path, headers=headers)
            r = conn.getresponse()
            body = r.read()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in ("transfer-encoding", "connection", "content-length", "keep-alive"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, http.client.HTTPException) as err:
            try:
                self.send_error(502, str(err))
            except OSError:
                pass
        finally:
            conn.close()


def get(base, path, cookie=None, token=None):
    """(status, headers, text) of a plain GET: what the server sends before anything runs in a browser"""
    h = {"Accept": "text/html"}
    if cookie:
        h["Cookie"] = "yyc-profile=CEHK; CF_Authorization=" + cookie
    if token:
        h["Cf-Access-Jwt-Assertion"] = token
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=h), timeout=180) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read().decode()


def seed():
    corpus.kv_set("scoring", {"from_id": 0, "comments": False})
    now = insights.now().isoformat(timespec="minutes")
    day = now[:10]
    post = {"url": URL, "title": "行政長官出席立法會答問會", "text": OPENING + "議員關注公營房屋輪候時間。" * 150 + SECRET,
            "sid": "mingpao_x", "source": "明報", "published": now}
    corpus.ingest([post])
    pid = corpus.ids_for_urls([URL])[URL]
    db = corpus.db()
    db.execute("""INSERT INTO scored (post_id, hash, sentiment, labels, at)
                  SELECT id, md5(coalesce(title, '') || chr(10) || coalesce(text, '')), 'neutral', '["monitor"]', %s
                  FROM posts WHERE id=%s""", (now, pid))
    db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES ('CEHK', %s, 92, %s, '立法會', %s)",
               (pid, REASON, now))
    db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
               (day, URL, corpus.J(dict(post, stream="CEHK", score=92, reason=REASON, category="立法會", sentiment="neutral",
                                        seen=now + ":00+08:00" if len(now) == 16 else now))))
    corpus.kv_set("edge:config", edge_config.build())
    return pid


class GatedPreload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(OUT, exist_ok=True)
        cls.pid = seed()
        cls.item = f"/item/{cls.pid}?p=CEHK"
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=f"postgres://claude:dev@127.0.0.1:5432/{DBNAME}")
        cls.log = open(os.path.join(OUT, "server.log"), "w")  # noqa: SIM115 (closed in tearDownClass)
        cls.tmp = tempfile.mkdtemp(prefix="gated-")
        if DEVSERVER:
            cls.vars = os.path.join(APP, ".dev.vars")
            if not os.path.exists(cls.vars):
                with open(cls.vars, "w") as f, open(cls.vars + ".example") as ex:
                    f.write(ex.read())
            with open(cls.vars) as f:
                cls.vars_before = f.read()
            with open(cls.vars, "w") as f:
                f.write("\n".join(l for l in cls.vars_before.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                        "\nACCESS_TEST_JWKS=" + ops.jwks() + "\n")
            cmd, cwd = ["npx", "vite", "dev", "--port", str(PORT), "--strictPort"], APP
        else:  # the Worker as it is deployed: the production build, run by wrangler's local runtime
            # one build id for the browser's files and the Worker, as a deploy has (vite.config.ts: the commit); without
            # it each half gets its own time, the page takes the site for a newer build and every link is a full load
            subprocess.run(["npx", "vite", "build"], cwd=APP, env=dict(env, GITHUB_SHA="9a7ed0" + "0" * 34), stdout=cls.log,
                           stderr=subprocess.STDOUT, check=True, timeout=900)
            shutil.copytree(os.path.join(APP, "dist"), os.path.join(cls.tmp, "dist"))
            cwd = os.path.join(cls.tmp, "dist", "server")
            with open(os.path.join(cwd, ".dev.vars"), "w") as f:
                f.write("ACCESS_TEST_JWKS=" + ops.jwks() + "\n")  # access.ts: a request to 127.0.0.1 only
            cmd = [os.path.join(APP, "node_modules", ".bin", "wrangler"), "dev", "-c", "wrangler.json", "--port", str(PORT),
                   "--ip", "127.0.0.1", "--inspector-port", str(PORT + 1000), "--persist-to", os.path.join(cls.tmp, "state")]
        cls.dev = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=cls.log, stderr=subprocess.STDOUT, start_new_session=True)
        cls.access = ThreadingHTTPServer(("127.0.0.1", APORT), Access)
        cls.access.handle_error = lambda *a: None  # a browser closing a connection it no longer needs
        threading.Thread(target=cls.access.serve_forever, daemon=True).start()
        deadline = time.time() + 600
        while True:
            try:
                if get(DEV, "/app/me")[0] == 200:
                    break
            except Exception:  # noqa: BLE001 (not listening yet)
                pass
            if cls.dev.poll() is not None or time.time() > deadline:
                cls.tearDownClass()
                raise RuntimeError("the server did not start: " + os.path.join(OUT, "server.log"))
            time.sleep(2)
        cls.staff = ops.token(STAFF)
        for path in (cls.item, "/?p=CEHK"):  # (the dev server compiles a route on its first use)
            get(SITE, path, cookie=cls.staff)
        cls.pw = sync_playwright().start()
        cls.chromium = cls.pw.chromium.launch()
        cls.webkit = cls.pw.webkit.launch()

    @classmethod
    def tearDownClass(cls):
        for stop in (lambda: cls.chromium.close(), lambda: cls.webkit.close(), lambda: cls.pw.stop(), lambda: cls.access.shutdown()):
            try:
                stop()
            except Exception:  # noqa: BLE001 (never started)
                pass
        try:
            os.killpg(cls.dev.pid, signal.SIGTERM)
            cls.dev.wait(timeout=30)
        except Exception:  # noqa: BLE001
            pass
        cls.log.close()
        if DEVSERVER:
            with open(cls.vars, "w") as f:
                f.write(cls.vars_before)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def asks(self, fn):
        """fn()'s result and the /app/me requests the stand-in saw meanwhile"""
        n = len(ASKED)
        out = fn()
        return out, [a for a in ASKED[n:] if a[0] == "/app/me"]

    # 1
    def test_1_item_html(self):
        (code, h, html), asked = self.asks(lambda: get(SITE, self.item, cookie=self.staff))
        self.assertEqual(code, 200)
        self.assertIn(SECRET, html, "an approved account's HTML has the full text")
        self.assertIn(REASON, html, "and the scorer's reason")
        self.assertEqual(h.get("Cache-Control"), "private, no-store")
        self.assertEqual(asked, [("/app/me", True)], "the server asked Access once, with the cookie")

        (code, h, html), asked = self.asks(lambda: get(SITE, self.item))
        self.assertEqual(code, 200)
        self.assertIn(OPENING[:20], html)
        self.assertIn("只供 Deyao 批准的帳戶閱讀", html)
        self.assertNotIn(SECRET, html, "the public's HTML has no full text")
        self.assertNotIn(REASON, html)
        self.assertEqual(h.get("Cache-Control"), "private, no-cache")
        self.assertEqual(asked, [], "nobody is asked about a reader with no cookie")

        gone = ops.token(STAFF)
        REVOKED.add(gone)
        other_app = ops.jwt.encode({"email": STAFF, "aud": [WISERS_AUD], "iss": ops.ISS, "iat": int(time.time()),
                                    "exp": int(time.time()) + 3600}, ops.KEY, algorithm="RS256", headers={"kid": "test"})
        for what, cookie in (("a session Access no longer accepts", gone), ("a cookie signed by another key", ops.token(STAFF, ops.ROGUE)),
                             ("a token for another Access app", other_app), ("not a token", "x.y.z")):
            (code, h, html), asked = self.asks(lambda: get(SITE, self.item, cookie=cookie))  # noqa: B023
            self.assertEqual(code, 200, what)
            self.assertNotIn(SECRET, html, what)
            self.assertNotIn(REASON, html, what)
            self.assertEqual(h.get("Cache-Control"), "private, no-cache", what)
            self.assertEqual(asked, [("/app/me", True)], what)

    # 2
    def test_2_stream_html(self):
        code, h, html = get(SITE, "/?p=CEHK", cookie=self.staff)
        self.assertEqual(code, 200)
        self.assertIn(REASON, html, "an approved account's 串流 has the score and reason")
        self.assertEqual(h.get("Cache-Control"), "private, no-store")
        code, h, html = get(SITE, "/?p=CEHK")
        self.assertIn("行政長官出席立法會答問會", html)
        self.assertNotIn(REASON, html)

    # 3
    def test_3_token_on_every_path(self):
        (code, h, html), asked = self.asks(lambda: get(DEV, self.item, token=self.staff))
        self.assertEqual(code, 200)
        self.assertIn(SECRET, html)
        self.assertIn(REASON, html)
        self.assertEqual(h.get("Cache-Control"), "private, no-store")
        self.assertEqual(asked, [])
        code, h, html = get(DEV, self.item, token=ops.token(STAFF, ops.ROGUE))
        self.assertNotIn(SECRET, html)

    def open(self, path, cookie=None, phone=False):
        if phone:
            ctx = self.webkit.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                          device_scale_factor=2)
        else:
            ctx = self.chromium.new_context(viewport={"width": 1600, "height": 1000})
        ctx.set_default_timeout(120000)
        if cookie:
            ctx.add_cookies([{"name": "CF_Authorization", "value": cookie, "url": SITE, "httpOnly": True}])
        page = ctx.new_page()
        page.asked = []   # the browser's own requests for what only approved accounts get
        page.errors = []
        page.all = []     # every request: (resource type, path)
        page.on("request", lambda r: page.all.append((r.resource_type, urlsplit(r.url).path)))
        page.on("request", lambda r: GATED.search(r.url) and page.asked.append(urlsplit(r.url).path))
        page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.goto(SITE + path, wait_until="load", timeout=180000)
        page.wait_for_function("() => document.documentElement.dataset.hydrated === '1'", timeout=180000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        return ctx, page

    def hydration_errors(self, page):
        return [e for e in page.errors if re.search(r"hydrat|did not match|Minified React error", e, re.I)]

    # 4
    def test_4_item_in_a_browser(self):
        ctx, page = self.open(self.item, self.staff)
        try:
            self.assertIn(SECRET, page.inner_text("#text"))
            self.assertIn(REASON, page.inner_text("body"))
            page.screenshot(path=os.path.join(OUT, "item-approved.png"), full_page=False)
            self.assertEqual(self.hydration_errors(page), [])
            self.assertEqual(page.asked, [], "everything came in the server's HTML: the browser asks for none of it")
        finally:
            ctx.close()
        # a session Access did not take: the server does not say who is reading, the browser asks as it always did
        gone = ops.token(STAFF)
        REVOKED.add(gone)
        ctx, page = self.open(self.item, gone)
        try:
            self.assertEqual(page.asked, ["/app/me"])
            self.assertNotIn(SECRET, page.inner_text("body"))
            self.assertIn("已獲批准請登入", page.inner_text("#text"))
            self.assertEqual(self.hydration_errors(page), [])
        finally:
            ctx.close()

    # 5
    def test_5_hover_reads_the_full_text(self):
        ctx, page = self.open("/?p=CEHK", self.staff)
        try:
            self.assertEqual(page.asked, [], "串流's scores came in the server's HTML")
            card = page.locator("a.item").first
            card.hover()
            for _ in range(100):
                if {"/app/item/text", "/app/item/ai"} <= set(page.asked):
                    break
                page.wait_for_timeout(100)
            self.assertEqual(sorted(page.asked), ["/app/item/ai", "/app/item/text"],
                             "hovering a card requests its full text and scores, once each")
            page.wait_for_load_state("networkidle")
            page.all.clear()
            card.click()
            page.wait_for_selector("#h-title")
            page.wait_for_function("s => (document.querySelector('#text')?.innerText || '').includes(s)", arg=SECRET, timeout=10000)
            self.assertIn(REASON, page.inner_text("body"))
            page.wait_for_timeout(500)
            self.assertEqual(sorted(page.asked), ["/app/item/ai", "/app/item/text"], "the click reuses what the hover read")
            self.assertEqual([x for x in page.all if x[0] == "document"], [], "the click is a page change in the app, not a new document")
            self.assertEqual(self.hydration_errors(page), [])
        finally:
            ctx.close()
        ctx, page = self.open("/?p=CEHK")
        try:
            page.asked.clear()  # (the public's page asks /app/me once: Access answers its sign-in page)
            page.locator("a.item").first.hover()
            page.wait_for_timeout(2500)
            self.assertEqual(page.asked, [], "the public's hover requests nothing meant for approved accounts")
        finally:
            ctx.close()

    # 6
    def test_6_hover_reads_the_scores(self):
        ctx, page = self.open(self.item, self.staff)
        try:
            page.locator("a.yyc-brand").first.hover()
            for _ in range(100):
                if page.asked:
                    break
                page.wait_for_timeout(100)
            self.assertEqual(page.asked, ["/app/stream/scores"], "hovering the link to 串流 requests the day's scores")
        finally:
            ctx.close()

    # 7
    def test_7_tap_on_a_phone(self):
        ctx, page = self.open("/?p=CEHK", self.staff, phone=True)
        try:
            self.assertEqual(page.asked, [])
            card = page.locator("a.item").first
            card.scroll_into_view_if_needed()
            page.all.clear()
            card.tap()
            page.wait_for_selector("#h-title")
            page.wait_for_function("s => (document.querySelector('#text')?.innerText || '').includes(s)", arg=SECRET, timeout=10000)
            page.wait_for_timeout(500)
            self.assertEqual(sorted(page.asked), ["/app/item/ai", "/app/item/text"], f"the tap reads each once {page.all}")
            self.assertEqual([x for x in page.all if x[0] == "document"], [], "the tap is a page change in the app, not a new document")
            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "no sideways scroll")
            page.screenshot(path=os.path.join(OUT, "item-approved-phone.png"), full_page=False)
            self.assertEqual(self.hydration_errors(page), [])
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
