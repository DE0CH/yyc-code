#!/usr/bin/env python3
"""The edge app's 串流 (web-app/, -StreamPage.tsx) says 即時更新 only while what it shows is current
(NAVIGATION.md "串流: is it current?"; Deyao, 15 Oct 2026: "stuck at an old time …
and it still tells me it's live").

What was wrong: the label was fixed text; today's feed was read again only when the LiveHub socket announced a change
(or every minute while the socket was down), so the shown `last_poll` aged without the page knowing whether it was
current, a socket that died silently while the phone slept kept the page waiting, a back/forward-cache restore was not
handled, and a read had no timeout.

Against the app's dev server on a database seeded here (never one anyone else uses):

    # news-stream/web-app:
    CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=postgres://…/yyc_test_live npx vite dev --port 5186
    WEB_APP_DATABASE_URL=postgresql:///yyc_test_live WEB_APP_URL=http://127.0.0.1:5186 python3 tests/test_stream_fresh_app.py

Chromium (1600 px) and WebKit (iPhone): live; woken after hours with the timers frozen (visibilitychange, pageshow),
the read on its way; a failed read; a read that never answers; offline and back; the poller behind; a device clock
hours wrong. The feed's server function is held back or failed in the browser's route; the data comes from the database.
"""
import base64, json, os, sys, threading, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
import devserver  # noqa: E402
devserver.ensure(5186)  # a dev server of the test's own when none is given (CI)
DB = os.environ.get("WEB_APP_DATABASE_URL") or sys.exit("WEB_APP_DATABASE_URL is not set (the dev server's database)")
BASE = os.environ.get("WEB_APP_URL", "http://127.0.0.1:5186")
os.environ["DATABASE_URL"] = DB
os.chdir(HERE)
import corpus, edge_config  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HKT = timezone(timedelta(hours=8))
FX = "https://yyc-fresh.example/"
BROWSERS = os.environ.get("BROWSERS", "chromium,webkit").split(",")


def now():
    return datetime.now(HKT)


def hm(t):
    return t.astimezone(HKT).strftime("%H:%M")


def until_midnight():
    """up to 3 h ahead without passing HKT midnight (a device already in tomorrow shows today as a past day)"""
    left = now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - now() - timedelta(minutes=10)
    return max(min(timedelta(hours=3), left), timedelta(minutes=1))


def set_poll(t):
    corpus.db().execute("INSERT INTO poll_state (k, v) VALUES ('last_poll', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                        (corpus.J(t.isoformat(timespec="seconds")),))


def add_item(v):
    t = now()
    day = t.date().isoformat()
    it = {"url": FX + str(v), "title": f"CEHK 第{v}版", "source": "政府新聞網", "stream": "CEHK", "sentiment": "neutral",
          "labels": [], "published": t.isoformat(timespec="seconds"), "seen": t.isoformat(timespec="seconds")}
    corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                        (day, it["url"], corpus.J(it)))


def seed():
    db = corpus.db()
    db.execute("DELETE FROM stream_items WHERE url LIKE %s", (FX + "%",))
    edge_config.publish(3)
    add_item(1)
    set_poll(now() - timedelta(minutes=1))


def fn_name(url):
    seg = url.split("/_serverFn/", 1)[1].split("?", 1)[0]
    try:
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))).get("export", seg)
    except (ValueError, UnicodeDecodeError):
        return seg


class Feed:
    """the feed's server function as the browser sees it: mode ok | fail | hang"""

    def __init__(self):
        self.mode, self.hung, self.calls, self.lock = "ok", [], 0, threading.Lock()

    def route(self, route):
        if not fn_name(route.request.url).startswith("fetchFeed"):
            return route.continue_()
        self.calls += 1
        if self.mode == "hang":
            with self.lock:
                self.hung.append(route)
            return
        if self.mode == "fail":
            return route.fulfill(status=502, content_type="text/plain", body="bad gateway")
        return route.continue_()

    def release(self):
        with self.lock:
            hung, self.hung = self.hung, []
        for r in hung:
            try:
                r.abort()
            except Exception:
                pass


class StreamFreshApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browsers = {b: getattr(cls.pw, b).launch() for b in BROWSERS}

    @classmethod
    def tearDownClass(cls):
        for b in cls.browsers.values():
            b.close()
        cls.pw.stop()
        corpus.db().execute("DELETE FROM stream_items WHERE url LIKE %s", (FX + "%",))

    def setUp(self):
        seed()

    def open(self, name, device_off=timedelta(0)):
        feed = Feed()
        if name == "webkit":
            opts = {k: v for k, v in self.pw.devices["iPhone 13"].items() if k != "default_browser_type"}
        else:
            opts = {"viewport": {"width": 1600, "height": 1000}}
        ctx = self.browsers[name].new_context(**opts, timezone_id="Europe/London", locale="zh-HK")
        ctx.set_default_timeout(60000)
        self.addCleanup(ctx.close)
        self.addCleanup(feed.release)
        ctx.clock.install(time=datetime.now(timezone.utc) + device_off)
        ctx.route("**/_serverFn/**", feed.route)
        ctx.route("**/live?**", lambda r: r.abort())  # no LiveHub in dev: the page's own reads keep it current
        p = ctx.new_page()
        p.goto(BASE + "/?p=CEHK", wait_until="domcontentloaded")
        p.wait_for_selector(".days .fresh[data-state]", timeout=90000)  # hydrated (the dev server compiles on first use)
        return ctx, p, feed

    def label(self, p):
        el = p.locator(".days .fresh")
        if not el.count():
            return None, ""
        return el.get_attribute("data-state"), (el.text_content() or "").strip()

    def wait_state(self, p, state, msg=""):
        try:
            p.wait_for_function("s => document.querySelector('.days .fresh')?.dataset.state === s", arg=state, timeout=15000)
        except Exception:
            self.fail(f"{msg}: the label never became {state!r}; it is {self.label(p)!r}")
        return self.label(p)[1]

    def wait_version(self, p, v, msg=""):
        try:
            p.wait_for_function("v => document.body.textContent.includes('第' + v + '版')", arg=v, timeout=15000)
        except Exception:
            self.fail(f"{msg}: the list never showed version {v}")

    def each(self, fn):
        for b in BROWSERS:
            with self.subTest(browser=b):
                seed()
                fn(b)

    def test_live(self):
        def run(b):
            ctx, p, feed = self.open(b)
            self.wait_version(p, 1, "first load")
            self.assertEqual(self.wait_state(p, "live", "fresh data"), "即時更新")
        self.each(run)

    def _wake(self, b, js):
        ctx, p, feed = self.open(b)
        self.wait_state(p, "live", "before sleeping")
        ctx.clock.pause_at(datetime.now(timezone.utc) + timedelta(seconds=2))
        ctx.clock.set_system_time(datetime.now(timezone.utc) + until_midnight())  # hours later, no timer has run
        feed.mode = "hang"
        p.evaluate(js)
        text = self.wait_state(p, "updating", "woken, the read on its way")
        self.assertIn("更新中", text)
        self.assertIn("資料截至", text)
        feed.release()
        feed.mode = "ok"
        add_item(2)
        set_poll(now())
        p.evaluate(js)
        ctx.clock.resume()  # awake: timers run again (TanStack Query hands results to React on a 0 ms timer); the
        # minute's timer is still up to 60 s away, so the new version within 15 s is the wake's own read
        self.wait_version(p, 2, "woken page")
        self.wait_state(p, "live", "after waking")

    def test_wake_visibility(self):
        self.each(lambda b: self._wake(b, "document.dispatchEvent(new Event('visibilitychange'))"))

    def test_wake_pageshow(self):
        self.each(lambda b: self._wake(b, "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"))

    def test_failed_read(self):
        def run(b):
            ctx, p, feed = self.open(b)
            self.wait_state(p, "live", "before")
            feed.mode = "fail"
            ctx.clock.run_for(61_000)
            ctx.clock.run_for(5_000)  # TanStack Query's one retry
            text = self.wait_state(p, "failed", "a 502")
            self.assertIn("未能更新", text)
            feed.mode = "ok"
            add_item(2)
            ctx.clock.run_for(20_000)
            self.wait_version(p, 2, "after the failure")
            self.wait_state(p, "live", "recovered")
        self.each(run)

    def test_hanging_read(self):
        def run(b):
            ctx, p, feed = self.open(b)
            self.wait_state(p, "live", "before")
            feed.mode = "hang"
            ctx.clock.run_for(61_000)
            ctx.clock.run_for(20_000)
            ctx.clock.run_for(20_000)  # the timeout, the retry, its timeout
            text = self.wait_state(p, "failed", "a read that never answers")
            self.assertIn("未能更新", text)
            feed.mode = "ok"
            add_item(2)
            ctx.clock.run_for(20_000)
            self.wait_version(p, 2, "after the hang")
            self.wait_state(p, "live", "recovered")
        self.each(run)

    def test_offline(self):
        def run(b):
            ctx, p, feed = self.open(b)
            self.wait_state(p, "live", "before")
            ctx.set_offline(True)
            p.evaluate("window.dispatchEvent(new Event('offline'))")
            self.assertIn("離線", self.wait_state(p, "offline", "offline"))
            ctx.set_offline(False)
            add_item(2)
            p.wait_for_timeout(500)
            p.evaluate("window.dispatchEvent(new Event('online'))")
            self.wait_version(p, 2, "back online")
            self.wait_state(p, "live", "back online")
        self.each(run)

    def test_server_behind(self):
        def run(b):
            t = now() - timedelta(minutes=40)
            set_poll(t)
            ctx, p, feed = self.open(b)
            text = self.wait_state(p, "behind", "poller behind")
            self.assertIn("資料截至 " + hm(t), text)
        self.each(run)

    LAYOUT_JS = """() => {
      const el = document.querySelector('.days .fresh'), row = el.closest('.days'), bar = el.closest('.yyc-bar');
      const r = el.getBoundingClientRect(), rr = row.getBoundingClientRect(), dot = getComputedStyle(el, '::before');
      const hit = [...bar.querySelectorAll('a, button, h1, .yyc-brand, [class*=stream] > *')]
        .filter(o => !row.contains(o) && !o.contains(row) && o.offsetParent !== null)
        .map(o => [o.textContent.trim().slice(0, 20) || o.className, o.getBoundingClientRect()])
        .filter(([, o]) => o.width && o.height && o.left < r.right && r.left < o.right && o.top < r.bottom && r.top < o.bottom)
        .map(([n]) => n);
      return {position: getComputedStyle(el).position, top: r.top, bottom: r.bottom, left: r.left, right: r.right,
              rowTop: rr.top, rowBottom: rr.bottom, rowLeft: rr.left, rowRight: rr.right, vw: innerWidth,
              dot: dot.width, hit};
    }"""

    def test_layout(self):
        """the label sits in the date row, whole, on screen, over nothing (Deyao, 22 Oct 2026: on an iPhone it was
        pinned to the bar's top-left corner over 「YYC」, half off the top: the live dashboard's `.live` rule)"""
        def run(b):
            for state, poll in (("live", timedelta(minutes=1)), ("behind", timedelta(minutes=40))):
                seed()
                set_poll(now() - poll)
                ctx, p, feed = self.open(b)
                self.wait_state(p, state, state)
                g = p.evaluate(self.LAYOUT_JS)
                msg = f"{b} {state}: {g}"
                self.assertEqual(g["position"], "static", msg)
                self.assertGreaterEqual(g["top"], g["rowTop"] - 0.5, msg)
                self.assertLessEqual(g["bottom"], g["rowBottom"] + 0.5, msg)
                self.assertGreaterEqual(g["left"], g["rowLeft"], msg)
                self.assertLessEqual(g["right"], min(g["rowRight"], g["vw"]) + 0.5, msg)
                self.assertGreaterEqual(g["top"], 0, msg)
                self.assertEqual(g["dot"], "7px", msg)
                self.assertEqual(g["hit"], [], msg)
                ctx.close()
        self.each(run)

    def test_device_clock_wrong(self):
        def run(b):
            # ahead by up to 3 h, but not past HKT midnight: a device already in tomorrow shows today as a past day
            ahead = until_midnight()
            for off in (timedelta(hours=-3),) + ((ahead,) if ahead > timedelta(minutes=5) else ()):
                ctx, p, feed = self.open(b, device_off=off)
                ctx.clock.run_for(16_000)  # at most one retry
                self.wait_state(p, "live", f"device clock {off}")
        self.each(run)


if __name__ == "__main__":
    unittest.main()
