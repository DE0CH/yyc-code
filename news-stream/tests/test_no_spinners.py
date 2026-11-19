#!/usr/bin/env python3
"""No spinner for data that doesn't exist yet (Deyao, 2026-10-16, on the item page of a Threads post by shinninglane:
the 內容 card spun 「處理中：系統正在收集全文」 for a post whose whole text is its headline and that no job reads).
NAVIGATION.md "Spinners": a spinner means the page is fetching something that answers within seconds; data the
pipeline has not produced yet is a static line in words; a fetch that takes over 15 s stops spinning and says so.

The edge app's item page against its dev server, on a database seeded here (posts first seen just now, so tags, scores
and comments are still to come):

    # the dev server (news-stream/web-app), with a .dev.vars whose ACCESS_TEST_JWKS is ACCESS_TEST_KEY's public key:
    CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=postgres://…/yyc_dev_x npx vite dev --port 5191
    WEB_APP_DATABASE_URL=postgresql:///yyc_dev_x WEB_APP_URL=http://127.0.0.1:5191 ACCESS_TEST_KEY=key.pem \\
        python3 tests/test_no_spinners.py

WebKit (iPhone, 390 px) as the public and Chromium (1600 px) as an approved account (a token signed with
ACCESS_TEST_KEY):
1. The Threads post with only a headline: 內容 says its whole text is the headline; no spinner, no running animation,
   no 處理中 anywhere once the page's own reads are done.
2. A news post the full-text job will read, with a clip in the transcription queue and a picture not scored yet:
   「正在收集內文」, 「AI 尚未評分（排隊中）」, static; no animation. Transcription is off in the shipped flags (Deyao,
   2026-10-20: cost), so the clip waiting on it is absent, never announced (NAVIGATION.md "Turned-off features"): no
   逐字稿 line (the queue line with transcription on: tests/test_media.py, test_web_app_pages.py).
3. Approved: a read that hangs (/app/item/ai held by the test) spins, and after 15 s turns into a static note with
   重試, which reads it again and shows the scores.
Screenshots: ~/artifacts/no-spinners/test-*.png.
"""
import os, sys, time, unittest, urllib.request
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
import devserver  # noqa: E402
devserver.ensure(5185)  # a dev server of the test's own when none is given (CI)
DB = os.environ.get("WEB_APP_DATABASE_URL") or sys.exit("WEB_APP_DATABASE_URL is not set (the dev server's database)")
BASE = os.environ.get("WEB_APP_URL", "http://127.0.0.1:5185")
KEY = os.environ.get("ACCESS_TEST_KEY") or sys.exit("ACCESS_TEST_KEY is not set (the private key of the dev server's ACCESS_TEST_JWKS)")
os.environ["DATABASE_URL"] = DB
os.environ.setdefault("FLAGS_FILE", os.path.join(HERE, "deploy/apps/flags/flags.json"))
os.chdir(HERE)
import jwt  # noqa: E402
import corpus, insights  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS = os.path.expanduser("~/artifacts/no-spinners")
THREADS = "https://www.threads.com/@fixture_spin/post/SPINFX1"
NEWS = "https://yyc-spin.example/news/1"
AUD, ISS = "8b644dde1fadd76a241cace4428d57ebe8fccb6cdfe0af987e5f35bc7e31dd9b", "https://yyc-hk.cloudflareaccess.com"
FONTS = "".join(f'@font-face{{font-family:"Noto Sans HK";font-weight:{w};src:local("Noto Sans CJK HK{n}")}}'
                for w, n in ((400, ""), (500, ""), (700, " Bold")))
# no element animating, no spinner element, no page read in flight
QUIET_JS = """() => document.getAnimations().filter(a => a.playState === 'running').length === 0
  && !document.querySelector('.spin, [data-wait=busy]')"""


def token():
    now = int(time.time())
    return jwt.encode({"email": "chendeyao000@gmail.com", "aud": [AUD], "iss": ISS, "iat": now, "exp": now + 3 * 3600},
                      open(KEY, "rb").read(), algorithm="RS256", headers={"kid": "test"})


def seed():
    db = corpus.db()
    for t in ("posts",):
        db.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT coalesce(max(id), 0) + 1 FROM {t}), false)")
    db.execute("DELETE FROM posts WHERE url IN (%s, %s)", (THREADS, NEWS))
    db.execute("DELETE FROM media WHERE key LIKE %s", ("spinfx:%",))
    if not corpus.kv_get("scoring"):  # stream scoring on (a copied database may predate it): new posts are due
        corpus.kv_set("scoring", {"from_id": 0, "comments": False})
    now = insights.now().isoformat(timespec="minutes")
    corpus.ingest([{"url": THREADS, "title": "屈穎妍文章令我非常仇恨香港特區政府！", "sid": "tikhub_threads",
                    "source": "fixture_spin (Threads)", "author": "fixture_spin", "published": now},
                   {"url": NEWS, "title": "一則等待讀取全文的新聞", "sid": "rthk_en", "source": "RTHK", "published": now}])
    ids = corpus.ids_for_urls([THREADS, NEWS])
    db.execute("UPDATE posts SET source_type='Social Media', website='Threads', platform='Threads', text=NULL, seen=%s "
               "WHERE id=%s", (now, ids[THREADS]))
    db.execute("UPDATE posts SET source_type='News', website='RTHK', text=NULL, cached_at=NULL, seen=%s WHERE id=%s",
               (now, ids[NEWS]))
    db.execute("INSERT INTO media (key, url, kind, host, status, first_seen, newest) VALUES "
               "('spinfx:1', %s, 'video', 'www.youtube.com', 'queued', %s, %s)", (NEWS + "#clip", now, "9999-12-31T00:00:00+08:00"))
    db.execute("INSERT INTO post_media (post_id, key) VALUES (%s, 'spinfx:1')", (ids[NEWS],))
    import edge_config
    corpus.kv_set("edge:config", edge_config.build())  # the shipped flags (FLAGS_FILE): transcription off
    return ids[THREADS], ids[NEWS]


class NoSpinners(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        cls.threads, cls.news = seed()
        for p in (cls.threads, cls.news):  # the dev server compiles on first use
            urllib.request.urlopen(f"{BASE}/item/{p}", timeout=180).read()
        cls.pw = sync_playwright().start()

    @classmethod
    def tearDownClass(cls):
        cls.pw.stop()

    def open(self, engine, approved=False):
        if engine == "webkit":
            self.browser = self.pw.webkit.launch()
            ctx = self.browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                           device_scale_factor=2)
        else:
            self.browser = self.pw.chromium.launch()
            ctx = self.browser.new_context(viewport={"width": 1600, "height": 1000})
        if approved:
            ctx.set_extra_http_headers({"Cf-Access-Jwt-Assertion": token()})
        ctx.set_default_timeout(60000)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)[:300]))
        return ctx, page

    def go(self, page, path):
        page.goto(BASE + path, wait_until="load", timeout=120000)
        page.add_style_tag(content=FONTS)
        page.wait_for_function("() => document.documentElement.dataset.hydrated === '1'", timeout=120000)
        page.wait_for_selector("#text")

    def quiet(self, page, what):
        """the page's reads done: no spinner, nothing animating, no 處理中"""
        try:
            page.wait_for_function(QUIET_JS, timeout=20000)
        except Exception:
            self.fail(f"{what}: still a spinner or a running animation 20 s after load: "
                      + page.evaluate("() => [...document.querySelectorAll('.spin, [data-wait=busy]')].map(e => e.closest('section')?.id + ':' + e.parentElement.textContent.slice(0, 60)).join(' | ')"))
        self.assertNotIn("處理中", page.locator("main").inner_text(), what)

    def states(self, engine, approved):
        ctx, page = self.open(engine, approved)
        tag = f"{engine}{'-approved' if approved else ''}"
        try:
            self.go(page, f"/item/{self.threads}")
            self.quiet(page, f"Threads post {tag}")
            text = page.locator("#text").inner_text()
            self.assertIn("這則帖子的全部文字就是上面的標題", text)
            self.assertNotIn("收集全文", text)
            page.screenshot(path=f"{SHOTS}/test-threads-{tag}.png", full_page=True)

            self.go(page, f"/item/{self.news}")
            self.quiet(page, f"news post {tag}")
            self.assertIn("正在收集內文", page.locator("#text").inner_text())
            self.assertNotIn("逐字稿", page.locator("main").inner_text(), "transcription off: the queued clip is absent")
            self.assertIn("AI 尚未評分（排隊中）", page.locator("#ai").inner_text())
            self.assertIn("AI 尚未標記（排隊中）", page.locator("#tags").inner_text())
            page.screenshot(path=f"{SHOTS}/test-news-{tag}.png", full_page=True)
            self.assertEqual(page.errors, [], f"{tag}: page errors")
        finally:
            ctx.close(); self.browser.close()

    def test_webkit_390_public(self):
        self.states("webkit", False)

    def test_chromium_1600_approved(self):
        self.states("chromium", True)

    def test_slow_read_stops_spinning(self):
        ctx, page = self.open("chromium", True)
        held = []
        hold = lambda route: held.append(route)  # never answered while held
        try:
            page.route("**/app/item/ai**", hold)
            self.go(page, f"/item/{self.news}")
            page.wait_for_selector("#ai [data-wait=busy] .spin", timeout=10000)
            t0 = time.time()
            page.wait_for_selector("#ai [data-wait=slow]", timeout=25000)
            self.assertGreaterEqual(time.time() - t0, 10, "the note came too early")
            note = page.locator("#ai [data-wait=slow]").inner_text()
            self.assertIn("超過 15 秒", note)
            self.assertEqual(page.locator("#ai .spin").count(), 0)
            page.screenshot(path=f"{SHOTS}/test-slow-read.png", full_page=True)
            page.unroute("**/app/item/ai**", hold)
            for r in held:
                try:
                    r.abort()
                except Exception:
                    pass
            page.locator("#ai [data-wait=slow] button", has_text="重試").click()
            page.wait_for_function("() => !document.querySelector('#ai [data-wait]')", timeout=20000)
            self.assertIn("AI 尚未評分（排隊中）", page.locator("#ai").inner_text())
        finally:
            ctx.close(); self.browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
