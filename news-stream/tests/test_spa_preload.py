#!/usr/bin/env python3
"""Preload on intent in the edge app (web-app/): TanStack Router's `defaultPreload: "intent"` runs the target route's
loader when the reader shows intent (hover for defaultPreloadDelay on desktop, touchstart on a phone), and the loaders
put the page's data into TanStack Query's cache (ensureQueryData), so the tap renders from data already loaded or on
its way. Runs against the app's dev server, which server-renders from Postgres (the same database as the cluster's
pages): no mocked data.

    # the dev server (news-stream/web-app):
    CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=postgres://…/yyc_dev_x npx vite dev --port 5185
    WEB_APP_URL=http://127.0.0.1:5185 python3 tests/test_spa_preload.py

The item's data is the server function fetchItem (/_serverFn/<id>, its id names the export in dev). A 串流 card links
/item?p=<stream>&url=<publisher url>: its loader finds the post (fetchItem by url) and redirects to /item/<id>, whose
loader then finds the same post already in the cache.

1. Desktop (Chromium, 1600 px): hovering a card requests its item before the click; the click then shows the item
   without a second request. A mouse passing over a card faster than the preload delay requests nothing.
2. Phone (WebKit, 390 px, touch): touchstart on a card starts the request; the tap shows the item from it (one
   request in all).
3. Tap-to-content on WebKit iPhone (and click-to-content on Chromium), printed as the median of REPEAT runs: no intent
   (a click with no touch/pointer events before it), a real tap (touchstart → click at once), and touchstart held
   150 ms before the click (Chromium: hover 0.3 s, then the click); as the server answers, and with DELAY seconds added
   to the item's server functions (held back in the test's route). Measured on the page's own clock (the first
   touchstart/pointerdown/click → the item's headline inserted, a MutationObserver), since Playwright's polling of a
   busy page adds seconds. Asserted: the held tap's item request went out before the click.
   WEB_APP_URL can also be a production build (`npx vite preview`): the counts in 1–2 need the dev server's named
   server functions, the timings work on both.
"""
import base64, functools, json, os, sys, time, unittest
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

print = functools.partial(print, flush=True)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
OWN = not os.environ.get("WEB_APP_URL")  # no server given (CI): a dev server of its own on a database seeded below
import devserver  # noqa: E402
devserver.ensure(5185)
BASE = os.environ.get("WEB_APP_URL", "http://127.0.0.1:5185")
DELAY = 1.0
STREAM = os.environ.get("WEB_APP_STREAM", "POL")  # its first card opens a typical post (CEHK's may be the 205-comment fixture)
REPEAT = int(os.environ.get("WEB_APP_REPEAT", "3"))  # runs per case (the median is printed)
HOLD = 0.15  # a finger's touchstart → click on a real tap


def is_item(url):
    """a request for the item's data: fetchItem in dev; in a production build (`vite preview`) the ids are hashes, so
    every server function (on the item page: fetchItem and the coverage that follows it)"""
    n = fn_name(url)
    return bool(n) and (n.startswith("fetchItem_") or "{" not in n and len(n) == 64)


def fn_name(url):
    """/_serverFn/<base64 json {file, export}> (dev) → the export's name, e.g. fetchItem_createServerFn_handler"""
    if "/_serverFn/" not in url:
        return None
    seg = url.split("/_serverFn/", 1)[1].split("?", 1)[0]
    try:
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))).get("export", seg)
    except (ValueError, UnicodeDecodeError):
        return seg


FX = "https://yyc-preload.example/news/"


def seed():
    """the test's own feed when it runs on its own database: STREAM's 串流 today with a few cards, each a post the item
    page finds by its URL (on a developer's copy of real data the feed is the copy's)"""
    os.environ["DATABASE_URL"] = os.environ["WEB_APP_DATABASE_URL"]
    import corpus, edge_config
    hkt = timezone(timedelta(hours=8))
    now = datetime.now(hkt)
    urls = [FX + str(i) for i in range(1, 4)]
    corpus.ingest([{"url": u, "title": f"預載測試新聞第{i}則", "sid": "rthk_en", "source": "RTHK",
                    "published": (now - timedelta(minutes=10 * i)).isoformat(timespec="minutes")}
                   for i, u in enumerate(urls, 1)])
    ids = corpus.ids_for_urls(urls)
    db = corpus.db()
    for i, u in enumerate(urls, 1):
        db.execute("UPDATE posts SET source_type='News', website='RTHK', text=%s WHERE id=%s",
                   (f"預載測試新聞第{i}則的全文。" * 20, ids[u]))
        it = {"url": u, "title": f"預載測試新聞第{i}則", "source": "RTHK", "stream": STREAM, "sentiment": "neutral",
              "labels": [], "published": (now - timedelta(minutes=10 * i)).isoformat(timespec="seconds"),
              "seen": now.isoformat(timespec="seconds")}
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                   (now.date().isoformat(), STREAM, u, corpus.J(it)))
    edge_config.publish(3)


class Preload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if OWN:
            seed()
        cls.pw = sync_playwright().start()
        cls.chromium = cls.pw.chromium.launch()
        cls.webkit = cls.pw.webkit.launch()

    @classmethod
    def tearDownClass(cls):
        cls.chromium.close(); cls.webkit.close(); cls.pw.stop()

    def open(self, engine, delay=0.0):
        if engine == "webkit":
            ctx = self.webkit.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                          device_scale_factor=3)
        else:
            ctx = self.chromium.new_context(viewport={"width": 1600, "height": 1000})
        ctx.set_default_timeout(120000)
        page = ctx.new_page()
        page.log = []  # (time, export name) of every item request
        page.held = []  # (due, route): item requests held back `delay` seconds (released by pump(), never a sleep in
        # the handler: the sync API's handlers run on this thread)
        if delay:
            ctx.route(is_item, lambda r: page.held.append((time.time() + delay, r)))
        page.on("request", lambda r: (fn_name(r.url) or "").startswith("fetchItem_") and page.log.append((time.time(), fn_name(r.url))))
        page.goto(BASE + "/?p=" + STREAM, wait_until="load", timeout=180000)
        page.wait_for_function("() => document.documentElement.dataset.hydrated === '1'", timeout=180000)
        card = page.locator("a.item").first
        card.wait_for(timeout=60000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)  # (never time.sleep: the sync API delivers request events only inside its calls)
        page.log.clear()
        return ctx, page, card

    @staticmethod
    def pump(page, seconds=0.0, until=None):
        """let the page run for `seconds` (or until the selector shows), releasing held requests when due"""
        end = time.time() + (seconds or 120)
        while time.time() < end:
            for x in [x for x in page.held if x[0] <= time.time()]:
                page.held.remove(x); x[1].continue_()
            if until and page.query_selector(until):
                return True
            page.wait_for_timeout(10)
        return False

    ARM = """() => { window.__t0 = null; window.__seen = null;
      for (const ev of ["touchstart", "pointerdown", "click"])  // the finger (or button) going down, or a bare click
        addEventListener(ev, () => { window.__t0 ??= performance.now(); }, { capture: true, once: true });
      new MutationObserver(() => { if (window.__seen == null && document.getElementById("h-title")) window.__seen = performance.now(); })
        .observe(document.body, { childList: true, subtree: true }); }"""

    def shown(self, page, t0=None):
        """seconds from the tap (the page's own clock: touchstart, pointerdown or click, whichever came first) to the
        item's headline in the DOM; the test's own polling, slow on a busy machine, is not in it"""
        self.assertTrue(self.pump(page, until="#h-title"), "the item page shows")
        return page.evaluate("() => (window.__seen - window.__t0) / 1000")

    def test_hover_preloads_and_click_reuses(self):
        ctx, page, card = self.open("chromium")
        try:
            card.hover()
            for _ in range(50):
                if page.log:
                    break
                page.wait_for_timeout(100)
            self.assertEqual(len(page.log), 1, "hovering a card requests its item once")
            page.wait_for_timeout(2000)
            card.click()
            page.wait_for_selector("#h-title")
            page.wait_for_timeout(500)
            self.assertEqual(len(page.log), 1, f"the click reuses the preloaded item: no second request {page.log}")
        finally:
            ctx.close()

    def test_sweep_requests_nothing(self):
        ctx, page, card = self.open("chromium")
        try:
            # the mouse enters and leaves the card within the same task (a sweep; Playwright's own moves can be further
            # apart than the 75 ms delay on a busy machine): React's onMouseEnter/Leave come from mouseover/mouseout
            card.evaluate("""a => { const o = { bubbles: true, relatedTarget: document.body };
              a.dispatchEvent(new MouseEvent("mouseover", o)); a.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body })); }""")
            page.wait_for_timeout(600)
            self.assertEqual(page.log, [], "a mouse sweeping over a card must not preload it")
        finally:
            ctx.close()

    def test_touch_preloads_and_tap_reuses(self):
        ctx, page, card = self.open("webkit")
        try:
            card.scroll_into_view_if_needed()
            page.evaluate(self.ARM)
            t0 = time.time()
            card.tap(position={"x": 20, "y": 10})  # touchstart, touchend, then the click
            took = self.shown(page, t0)
            page.wait_for_timeout(500)
            self.assertEqual(len(page.log), 1, f"touchstart preload and the tap's page share one request: {page.log}")
            print(f"\n  phone tap: tap-to-content {took:.2f}s")
        finally:
            ctx.close()

    def measure(self, engine, mode, delay):
        """→ (seconds from the tap/click to the item page, the item request was sent before the click)"""
        ctx, page, card = self.open(engine, delay)
        try:
            card.scroll_into_view_if_needed()
            page.evaluate(self.ARM)
            t0 = time.time()
            if mode == "no-intent":
                card.evaluate("a => a.click()")  # a click with no pointer/touch events before it
            elif mode == "tap":
                card.tap(position={"x": 20, "y": 10}) if engine == "webkit" else card.click()
            if mode != "held":
                return self.shown(page, t0), self.server_ms(page)
            else:  # the finger (or mouse) rests on the card HOLD seconds before the click
                card.dispatch_event("touchstart") if engine == "webkit" else card.hover()
                self.pump(page, HOLD if engine == "webkit" else 0.3)
                t1 = card.evaluate("a => { const t = performance.now(); a.click(); return t; }")
                took = self.shown(page, t0)
                # when the browser sent the item's request (its own clock; the sync API delivers events late)
                sent = page.evaluate("() => performance.getEntriesByType('resource').filter(e => e.name.includes('/_serverFn/'))"
                                     ".map(e => [e.name, e.startTime])")
                return took, self.server_ms(page), any(is_item(u) and t < t1 for u, t in sent)
        finally:
            ctx.close()

    @staticmethod
    def server_ms(page):
        """the item request's time in the browser (request sent → response read), ms"""
        return page.evaluate("() => performance.getEntriesByType('resource').filter(e => e.name.includes('/_serverFn/'))"
                             ".map(e => Math.round(e.responseEnd - e.startTime))")

    def test_tap_to_content_times(self):
        out = {}
        for delay in (0.0, DELAY):
            for engine in ("webkit", "chromium"):
                for mode in ("no-intent", "tap", "held"):
                    runs = sorted((self.measure(engine, mode, delay) for _ in range(REPEAT)), key=lambda r: r[0])
                    out[(delay, engine, mode)] = runs[len(runs) // 2] + ((min(r[0] for r in runs), max(r[0] for r in runs)),)
        print("\n  tap-to-content (seconds, the page's clock: finger/button down or bare click → the item's headline in the"
              " DOM; webkit = iPhone 390 touch, held = touchstart %.2fs before the click; chromium = 1600 mouse,"
              " held = hover 0.3s then click):" % HOLD)
        for (delay, engine, mode), (v, ms, *rest) in out.items():
            lo, hi = rest[-1]
            print(f"    server +{delay:.1f}s  {engine:8s} {mode:10s} median {v:.2f} (of {REPEAT}: {lo:.2f}–{hi:.2f})"
                  f"   server functions {ms} ms" + ("  (item request sent before the click)" if len(rest) == 2 and rest[0] else ""))
        for e in ("webkit", "chromium"):  # intent starts the item's request before the click (as the server answers:
            # a request held back in the test's route shows in WebKit's resource timing only once released)
            self.assertTrue(out[(0.0, e, "held")][2] is True, f"{e}: the request starts on intent, before the click")


if __name__ == "__main__":
    unittest.main(verbosity=2)
