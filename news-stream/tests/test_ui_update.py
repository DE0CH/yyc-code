#!/usr/bin/env python3
"""A deploy of the site reaches every open page within seconds and never reloads it (Deyao, 2026-10-20: "UI updates
should be pushed to users quite quickly, and there should be a mechanism to invalidate stale browser cache so they see
updates when they are pushed"). web-app/README.md "Updates reach open pages"; NAVIGATION.md "A new deploy reaches open pages".

Two production builds of web-app (A, then B, different build ids, so different hashed files) run in the Workers runtime
(`wrangler dev` on each build's dist/server/wrangler.json, as `wrangler deploy` ships it) behind a small switch on one
port, which turns from A to B the way a Worker deploy does: B is up, new connections go to B, A's connections (the
LiveHub sockets too) close. B is built with `scripts/keep-assets.mjs` reading A's site, as the web-app workflow does.

Checked in WebKit (iPhone 13, 390 px, touch) and Chromium (1600 px), each page in a window of its own. A deploy never
reloads an open page (Deyao, 2026-10-22: "new ui shouldn't refresh the page, because refreshing the page makes my live
stream exit full screen, and shows the loading bar"): every document a tab loads is logged with its navigation type
(performance.getEntriesByType('navigation'), in sessionStorage), and a check fails on a document the reader did not ask
for, on any 'reload' the reader did not tap, and on a page whose JS context was replaced.
- cache headers: documents `no-cache` (never served stale), hashed /assets/* `immutable` for a year, build-assets.json
  `no-cache`, an unknown /assets/ file a plain 404;
- a file of A that fails to load (the test answers 404) during a page change makes that page change one full load, and
  files that stay gone never loop (exactly one more document);
- B still serves A's hashed files (a tab on A keeps working mid-session);
- a visible page learns of B within ANNOUNCE_S seconds, stays as it is (same document, scroll, no note) and its next
  link is a full load of B;
- the live dashboard /app/live?half=left (Chromium; Deyao's identity, signed with the test's own Access key), in full
  screen, stays on A and in full screen, and its data reads (A's code, B's server) keep answering 200;
- a hidden page stays on A through the deploy and when it comes back on screen, its data reads keep answering 200; a
  data request that fails with B live shows 已有更新, and only a tap reloads (into B, same URL and scroll);
- a page with unsaved input keeps it and shows nothing;
- Back to a page of A: in Chromium with the back/forward cache on (a Chromium started by the test: Playwright's own
  launch turns the cache off) the restored page stays on A and its next link loads B; in WebKit (no back/forward cache
  under Playwright) Back is a full load of B, and the restore event (pageshow, persisted) of a page that slept through
  the deploy changes nothing;
- a page that was frozen (hidden and offline through the deploy, as an iPhone tab is) stays on A when it wakes; its
  next link loads B.

    python3 tests/test_ui_update.py     # needs web-app/node_modules and the local Postgres; ENGINES=webkit to run one

Its own database (UI_UPDATE_DB, default ui_update_test: made, seeded and dropped here), reached by the Workers runtime
with the local login claude:dev over TCP (README "Local development"). Screenshots: ~/artifacts/spa/ui-update/.
"""
import asyncio, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

import psycopg

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBNAME = os.environ.get("UI_UPDATE_DB", "ui_update_test")
assert not DBNAME.startswith("yyc_dev"), "the test writes stream items and drops its database: its own one"
os.environ["DATABASE_URL"] = f"postgresql:///{DBNAME}"
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["EDGE_DB"] = DBNAME
import test_edge_ops as ops  # noqa: E402  (token, DEYAO: the test's own Access key)
os.environ["DATABASE_URL"] = f"postgresql:///{DBNAME}"
from playwright.sync_api import sync_playwright, expect  # noqa: E402

APP = os.path.join(HERE, "web-app")
PORT = int(os.environ.get("UI_UPDATE_PORT", "5190"))       # what the browsers open (the switch)
PORT_A, PORT_B = PORT + 1, PORT + 2
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = os.path.expanduser(os.environ.get("UI_UPDATE_SHOTS", "~/artifacts/spa/ui-update"))
ANNOUNCE_S = 10      # a visible page knows of the new build within this many seconds of the deploy
T = 20_000           # ms: every wait has a timeout
BUILD_A, BUILD_B = "a0a0a0a0a0a0", "b1b1b1b1b1b1"
HYPERDRIVE = f"postgres://claude:dev@127.0.0.1:5432/{DBNAME}"
WRANGLER = os.path.join(APP, "node_modules", ".bin", "wrangler")
FX = "https://yyc-uiupdate.example/"   # the test's own stream items (a long page to scroll)
HKT = timezone(timedelta(hours=8))


def seed(on=True):
    """the test's own database (made here, dropped at the end): the schema, the site's config, 60 items of today in CEHK"""
    if not on:
        with psycopg.connect("postgresql:///postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{DBNAME}" WITH (FORCE)')
        return
    with psycopg.connect("postgresql:///postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DBNAME,)).fetchone():
            c.execute(f'CREATE DATABASE "{DBNAME}"')
    import corpus, edge_config  # noqa: E402 (DATABASE_URL first)
    edge_config.publish(3)
    db = corpus.db()
    db.execute("DELETE FROM stream_items WHERE url LIKE %s", (FX + "%",))
    now = datetime.now(HKT)
    for n in range(60):
        t = (now - timedelta(minutes=n)).isoformat(timespec="seconds")
        it = {"url": f"{FX}{n}", "title": f"測試更新 第{n}則：立法會今日討論施政報告", "source": "政府新聞網", "stream": "CEHK",
              "sentiment": "neutral", "labels": [], "published": t, "seen": t}
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                   (now.date().isoformat(), it["url"], corpus.J(it)))
    db.execute("INSERT INTO poll_state (k, v) VALUES ('last_poll', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
               (corpus.J(now.isoformat(timespec="seconds")),))


# document.visibilityState as the test sets it (a page cannot be hidden on demand otherwise); null = the browser's own
VISIBILITY = """(() => {
  let forced = null;
  const own = Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState").get;
  Object.defineProperty(document, "visibilityState", { configurable: true, get() { return forced ?? own.call(document); } });
  Object.defineProperty(document, "hidden", { configurable: true, get() { return (forced ?? own.call(document)) === "hidden"; } });
  window.__setVisibility = v => { forced = v; document.dispatchEvent(new Event("visibilitychange")); };
  window.__setVisibilityQuiet = v => { forced = v; };
  // the first restore of this tab from the back/forward cache (the reload into B that follows is not one)
  addEventListener("pageshow", e => { try { if (e.persisted) sessionStorage.setItem("t-persisted-first", "true"); } catch {} });
  // when the page itself first knew of the new build (the test's own clock is the same machine's)
  new MutationObserver(() => {
    if (document.documentElement.dataset.update === "ready" && !window.__readyAt) window.__readyAt = Date.now();
  }).observe(document, { attributes: true, subtree: true, attributeFilter: ["data-update"] });
})();"""

# every document this tab loads, with its navigation type (navigate / reload / back_forward)
DOCS = """(() => { try {
  const a = JSON.parse(sessionStorage.getItem("t-docs") || "[]"), n = performance.getEntriesByType("navigation")[0];
  // (WebKit has no navigation entry yet when this runs: the older performance.navigation says the same)
  a.push([location.pathname + location.search, n ? n.type : ["navigate", "reload", "back_forward"][performance.navigation.type] || "?"]);
  sessionStorage.setItem("t-docs", JSON.stringify(a));
} catch {} })();"""

# who moved the scroll position, and the reloads before this document (printed when a check fails)
TRACE = """(() => {
  window.__trace = [];
  const t0 = Date.now(), log = (...a) => { if (window.__trace.length < 200) window.__trace.push([Date.now() - t0, ...a]); };
  for (const [o, n] of [[window, "scrollTo"], [window, "scroll"], [Element.prototype, "scrollIntoView"], [Element.prototype, "scrollTo"]]) {
    const f = o[n];
    o[n] = function (...a) { log(n, JSON.stringify(a).slice(0, 80), (new Error().stack || "").split("\\\\n").slice(2, 4).join(" | ").slice(0, 200)); return f.apply(this, a); };
  }
  try { log("start", sessionStorage.getItem("yyc-update-scroll"), sessionStorage.getItem("yyc-update-guard")); } catch {}
  let last = -1;
  addEventListener("scroll", () => { if (Math.abs(scrollY - last) > 50) { last = scrollY; log("y", scrollY, document.documentElement.scrollHeight); } }, true);
})();"""


class Switch:
    """one port in front of the build that is live: switch(port) is a deploy (new connections go there, the old ones close)"""

    def __init__(self, port, backend):
        self.port, self.backend, self.conns = port, backend, set()
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(10)

    def _run(self, ready):
        asyncio.set_event_loop(self.loop)
        self.server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", self.port))
        ready.set()
        self.loop.run_forever()

    async def _handle(self, r, w):
        # a connection that comes while the next build is still starting waits for it (as one atomic deploy looks)
        for _ in range(600):
            try:
                br, bw = await asyncio.open_connection("127.0.0.1", self.backend)
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            w.close()
            return
        pair = (w, bw)
        self.conns.add(pair)

        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                for x in pair:
                    x.close()
        await asyncio.gather(pipe(r, bw), pipe(br, w))
        self.conns.discard(pair)

    def switch(self, backend):
        def go():
            self.backend = backend
            for pair in list(self.conns):
                for x in pair:
                    x.close()
        self.loop.call_soon_threadsafe(go)

    def stop(self):
        self.loop.call_soon_threadsafe(self.server.close)
        self.loop.call_soon_threadsafe(self.loop.stop)


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def status(url):
    try:
        return get(url)[0:2]
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


class Loud(AssertionError):
    """a failed check, printed at once (the run goes on with the other pages, and a hung page would hide it)"""
    def __init__(self, *a):
        super().__init__(*a)
        print("CHECK FAILED:", *a, file=sys.stderr, flush=True)


class UiUpdate(unittest.TestCase):
    failureException = Loud
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        cls.tmp = tempfile.mkdtemp(prefix="ui-update-")
        cls.log = open(os.path.join(SHOTS, "servers.log"), "w")  # noqa: SIM115 (closed in tearDownClass)
        cls.servers, cls.own = {}, {}
        cls.switch = None
        cls.pw = None
        seed()
        try:
            # A: the first deploy (no site to keep files from); B: the next one, keeping A's files from A's site
            cls.build("A", BUILD_A)
            cls.keep("A", "http://127.0.0.1:9")
            cls.build("B", BUILD_B)
            cls.serve("A", PORT_A)
            cls.switch = Switch(PORT, PORT_A)
            cls.keep("B", BASE)
        except Exception:
            cls.tearDownClass()
            raise
        cls.pw = sync_playwright().start()

    @classmethod
    def build(cls, name, build_id):
        env = dict(os.environ, GITHUB_SHA=build_id + "0" * 28)
        subprocess.run(["npx", "vite", "build"], cwd=APP, env=env, stdout=cls.log, stderr=subprocess.STDOUT, check=True, timeout=900)
        shutil.copytree(os.path.join(APP, "dist"), os.path.join(cls.tmp, name, "dist"))
        cls.own[name] = set(os.listdir(os.path.join(cls.tmp, name, "dist", "client", "assets")))  # before keep-assets

    @classmethod
    def keep(cls, name, site):
        env = dict(os.environ, GITHUB_SHA={"A": BUILD_A, "B": BUILD_B}[name] + "0" * 28)
        subprocess.run(["node", os.path.join(APP, "scripts", "keep-assets.mjs"), "--site", site,
                        "--client", os.path.join(cls.tmp, name, "dist", "client")],
                       env=env, stdout=cls.log, stderr=subprocess.STDOUT, check=True, timeout=300)

    @classmethod
    def serve(cls, name, port):
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=HYPERDRIVE)
        with open(os.path.join(cls.tmp, name, "dist", "server", ".dev.vars"), "w") as f:
            f.write("ACCESS_TEST_JWKS=" + ops.jwks() + "\n")  # access.ts: a request to 127.0.0.1 only
        p = subprocess.Popen([WRANGLER, "dev", "-c", "wrangler.json", "--port", str(port), "--ip", "127.0.0.1",
                              "--inspector-port", str(port + 1000), "--persist-to", os.path.join(cls.tmp, name, "state")],
                             cwd=os.path.join(cls.tmp, name, "dist", "server"), env=env, stdout=cls.log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        cls.servers[name] = p
        deadline = time.time() + 120
        while True:
            try:
                if get(f"http://127.0.0.1:{port}/version", 5)[2].decode().strip() == {"A": BUILD_A, "B": BUILD_B}[name]:
                    return
            except Exception:
                pass
            if p.poll() is not None or time.time() > deadline:
                raise RuntimeError(f"build {name} did not start: {SHOTS}/servers.log")
            time.sleep(0.5)

    @classmethod
    def kill(cls, name):
        p = cls.servers.pop(name, None)
        if p:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=30)
            except Exception:
                pass

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "pw", None):
            cls.pw.stop()
        for name in list(cls.servers):
            cls.kill(name)
        if cls.switch:
            cls.switch.stop()
        cls.log.close()
        seed(False)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- helpers ----
    def assets(self, name):
        return set(os.listdir(os.path.join(self.tmp, name, "dist", "client", "assets")))

    def setup_ctx(self, ctx):
        ctx.set_default_timeout(T)
        ctx.add_init_script(VISIBILITY)
        ctx.add_init_script(TRACE)
        ctx.add_init_script(DOCS)
        ctx.route(re.compile(r"^https://fonts\.(googleapis|gstatic)\.com/"), lambda r: r.abort())  # no outside dependency
        return ctx

    def context(self, engine):
        """a context of its own for each page: its own window, so it is the visible one (WebKit hides background tabs)"""
        if engine not in self.browsers:
            self.browsers[engine] = getattr(self.pw, engine).launch()
            self.addCleanup(self.browsers[engine].close)
        b = self.browsers[engine]
        if engine == "webkit":
            return self.setup_ctx(b.new_context(**self.pw.devices["iPhone 13"]))
        return self.setup_ctx(b.new_context(viewport={"width": 1600, "height": 1000}))

    def cdp_chromium(self):
        """Chromium as a reader runs it: Playwright's own launch turns the back/forward cache off, this one keeps it"""
        port = PORT + 50
        # --no-sandbox as Playwright's own launch passes it: Ubuntu 24.04 (the CI runner) refuses Chrome's sandbox
        # its user namespaces, and that Chrome exits at once; the flag leaves the back/forward cache as it is
        proc = subprocess.Popen([self.pw.chromium.executable_path, "--headless=new", "--no-sandbox",
                                 f"--remote-debugging-port={port}",
                                 f"--user-data-dir={tempfile.mkdtemp(dir=self.tmp)}", "--no-first-run", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: os.killpg(proc.pid, signal.SIGTERM))
        deadline = time.time() + 30
        while True:
            try:
                b = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        return self.setup_ctx(b.new_context(viewport={"width": 1600, "height": 1000}))

    def page(self, ctx, path, mark, scroll=0):
        page = ctx.new_page()
        page.on("response", lambda r: r.status >= 400 and print(f"  {mark}: {r.status} {r.url[:160]}", file=sys.stderr))
        page.goto(BASE + path, wait_until="domcontentloaded")
        expect(page.locator(f"html[data-build='{BUILD_A}']")).to_have_count(1)
        page.wait_for_load_state("networkidle")
        if path == "/":  # 串流 writes ?p= once its data is in (a navigation that scrolls to the top)
            page.wait_for_function("new URLSearchParams(location.search).has('p')")
            page.wait_for_load_state("networkidle")
        if scroll:
            page.wait_for_function("y => document.documentElement.scrollHeight > y + innerHeight", arg=scroll, timeout=T)
            for _ in range(20):
                page.evaluate("y => window.scrollTo(0, y)", scroll)
                time.sleep(0.8)  # and it stays there
                if abs(page.evaluate("scrollY") - scroll) < 2:
                    break
            else:
                self.fail(f"{mark}: cannot scroll to {scroll}: " + str(page.evaluate("[scrollY, document.documentElement.scrollHeight, location.href]")))
        page.evaluate("m => { window.__doc = m; }", mark)
        self.urls[mark] = page.url  # as the page has written it (串流 adds ?p=)
        return page

    def same_doc(self, page, mark):
        return page.evaluate("window.__doc") == mark

    def wait_new_build(self, page, label, timeout=T):
        try:
            page.wait_for_function(f"document.documentElement.dataset.build === '{BUILD_B}' && document.readyState === 'complete'",
                                   timeout=timeout)
        except Exception:
            page.screenshot(path=os.path.join(SHOTS, label + "-FAILED.png"))
            self.fail(f"{label}: not on build B: " + json.dumps(page.evaluate(
                "[location.href, JSON.stringify(document.documentElement.dataset), document.visibilityState, navigator.onLine, window.__doc, window.__trace]"),
                ensure_ascii=False)[:3000])
        page.screenshot(path=os.path.join(SHOTS, label + ".png"))

    def wait_scroll(self, page, y):
        try:
            page.wait_for_function("y => Math.abs(scrollY - y) <= 60", arg=y, timeout=5000)
        except Exception:
            self.fail(f"scroll not kept: {page.evaluate('scrollY')} instead of {y}; " + json.dumps(page.evaluate("window.__trace"), ensure_ascii=False))

    # ---- tests ----
    def test_1_cache_headers(self):
        st, h = status(BASE + "/")
        self.assertEqual(st, 200)
        cc = h.get("Cache-Control", "")
        self.assertIn("no-cache", cc, cc)
        self.assertNotRegex(cc, r"max-age=[1-9]", cc)
        asset = sorted(f for f in self.assets("A") if f.endswith(".js"))[0]
        st, h = status(BASE + "/assets/" + asset)
        self.assertEqual(st, 200)
        self.assertIn("immutable", h.get("Cache-Control", ""))
        self.assertIn("max-age=31536000", h.get("Cache-Control", ""))
        st, h, body = get(BASE + "/build-assets.json")
        self.assertIn("no-cache", h.get("Cache-Control", ""))
        listed = json.loads(body)
        self.assertEqual(listed["build"], BUILD_A)
        self.assertTrue({"assets/" + f for f in self.assets("A")} <= set(listed["files"]))
        st, h = status(BASE + "/assets/no-such-file-XyZ.js")
        self.assertEqual(st, 404)
        self.assertNotIn("text/html", h.get("Content-Type", ""))
        st, h = status(BASE + "/version")
        self.assertEqual(st, 200)
        self.assertIn("no-store", h.get("Cache-Control", ""))

    def docs(self, page):
        return page.evaluate("JSON.parse(sessionStorage.getItem('t-docs') || '[]')")

    def kept(self, page, mark, n, build=BUILD_A):
        """still the document `mark` (its JS context never replaced), the tab has loaded exactly n documents, none of
        them a reload, on `build`"""
        d = self.docs(page)
        self.assertTrue(self.same_doc(page, mark), f"{mark}: the page was replaced: {d}")
        self.assertEqual(len(d), n, f"{mark}: documents {d}")
        self.assertNotIn("reload", [t for _, t in d], f"{mark}: {d}")
        self.assertEqual(page.evaluate("document.documentElement.dataset.build"), build, mark)

    def watch(self, page, pattern):
        """the statuses of the page's requests matching `pattern` from now on"""
        got = []
        page.on("response", lambda r: re.search(pattern, r.url) and got.append(r.status))
        return got

    def wait_for(self, page, cond, what, timeout=T / 1000):
        deadline = time.time() + timeout
        while not cond():
            if time.time() > deadline:
                self.fail(what)
            page.wait_for_timeout(250)  # (Playwright delivers the page's events only while it is called)

    def no_note(self, page, mark):
        self.assertEqual(page.locator(".yyc-update").count(), 0, f"{mark}: 已有更新 shown with nothing broken")

    def link(self, page, mark, n):
        """the next link: a full load of B (document n + 1, a navigation, not a reload)"""
        page.locator("a[href^='/dashboard']:visible").first.click()
        self.wait_new_build(page, f"{mark}-next-{page.context.browser.browser_type.name}")
        self.assertFalse(self.same_doc(page, mark), f"{mark}: the next link was not a full load")
        self.assertTrue(page.url.startswith(BASE + "/dashboard"), page.url)
        d = self.docs(page)
        self.assertEqual(len(d), n + 1, d)
        self.assertEqual(d[-1][1], "navigate", d)

    def missing_file(self, engine, once):
        """a page of A whose not-yet-loaded files are gone (404); its link to 數據分析 is one full load, never a loop"""
        mark = "file-once" if once else "file-gone"
        ctx = self.context(engine)
        page = self.page(ctx, "/", mark)
        have = set(page.evaluate("performance.getEntriesByType('resource').map(e => new URL(e.name).pathname)"))
        blocked = []

        def gone(route):
            path = urllib.parse.urlparse(route.request.url).path
            if path in have or (once and blocked):
                return route.continue_()
            blocked.append(path)
            return route.fulfill(status=404, content_type="text/plain", body="gone")
        page.route(re.compile(r"/assets/.+\.(js|css)(\?|$)"), gone)
        page.locator("a[href^='/dashboard']:visible").first.click()
        page.wait_for_url(re.compile(r"/dashboard"), timeout=T)
        time.sleep(15)  # long enough for any loop to show
        d = self.docs(page)
        self.assertTrue(blocked, f"{mark}: no file of A was needed: the check did not run")
        self.assertNotIn("reload", [t for _, t in d], d)
        self.assertEqual([p.split("?")[0] for p, _ in d], ["/", "/dashboard"], f"{mark}: documents {d} (blocked {blocked[:5]})")
        if once:  # the full load works
            expect(page.get_by_role("heading", name=re.compile("Exposure & Mentioning Trend"))).to_be_visible()
        page.screenshot(path=os.path.join(SHOTS, f"{mark}-{engine}.png"))
        ctx.close()

    def test_2_deploy_never_reloads_open_pages(self):
        only_a = sorted(self.own["A"] - self.own["B"])
        self.assertTrue(only_a, "builds A and B have the same files: the build id is not in the bundle")
        self.assertTrue(set(only_a) <= self.assets("B"), "B's deploy does not carry A's files")
        engines = os.environ.get("ENGINES", "webkit,chromium").split(",")
        pages, self.urls, self.browsers, seen = {}, {}, {}, {}

        # before the deploy: A's files gone mid-session (older than the 14 days the deploy keeps)
        for engine in engines:
            for once in (True, False):
                with self.subTest(engine=engine, page="missing file", once=once):
                    self.missing_file(engine, once)

        for engine in engines:
            p = pages[engine] = {}
            p["visible"] = self.page(self.context(engine), "/", "visible", 800)
            vlog = p["vlog"] = []
            p["visible"].on("request", lambda r: "/version" in r.url and vlog.append(("req", round(time.time(), 1))))
            p["visible"].on("requestfailed", lambda r: "/version" in r.url and vlog.append(("failed", round(time.time(), 1))))
            p["visible"].on("websocket", lambda w: vlog.append(("ws", round(time.time(), 1))))
            p["hidden"] = self.page(self.context(engine), "/", "hidden", 1500)
            seen["hidden-" + engine] = self.watch(p["hidden"], r"/_serverFn/")
            p["input"] = self.page(self.context(engine), "/dashboard?v=posts", "input")
            p["input"].locator("#qin").click()
            p["input"].locator("#qin").press_sequentially("立法會")
            p["input"].locator("#qin").evaluate("e => e.blur()")
            # Back to a page of A: in Chromium with the back/forward cache on (the page must stay eligible: no open socket)
            p["back"] = self.page(self.cdp_chromium() if engine == "chromium" else self.context(engine), "/", "back", 600)
            p["back"].goto(BASE + "/dashboard")
            expect(p["back"].locator(f"html[data-build='{BUILD_A}']")).to_have_count(1)
            if engine == "webkit":
                # Playwright's WebKit has no back/forward cache: an iPhone's restore is the pageshow event (persisted)
                # of a page that slept through the deploy (hidden, offline)
                bctx = self.context(engine)
                p["bfsim"] = self.page(bctx, "/", "bfsim", 900)
                p["bfsim"].evaluate("window.__setVisibility('hidden')")
                bctx.set_offline(True)
                p["bfsim_ctx"] = bctx
            p["hidden"].evaluate("window.__setVisibility('hidden')")
            # an iPhone tab in the background: frozen (no timers, no socket) through the deploy; offline
            fctx = self.context(engine)
            p["frozen"] = self.page(fctx, "/", "frozen", 1200)
            p["frozen"].evaluate("window.__setVisibility('hidden')")
            fctx.set_offline(True)
            p["frozen_ctx"] = fctx
            if engine == "chromium":
                # the live dashboard, signed in as Deyao, full screen (F): in Chromium (Deyao's displays; Playwright's WebKit
                # has no full screen). Opened last: its reads every 3 s slow the local runtime down
                lctx = self.browsers[engine].new_context(viewport={"width": 1920, "height": 1200},
                                                         extra_http_headers={"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})
                p["live"] = self.page(self.setup_ctx(lctx), "/app/live?half=left", "live")
                seen["live-" + engine] = self.watch(p["live"], r"/app/live/data")
                p["live"].keyboard.press("f")
                p["live"].wait_for_function("!!document.fullscreenElement", timeout=5000)

        # the deploy: the switch turns to B (A's connections close; new ones wait in the switch until B is up, so to the
        # pages it is one atomic swap, as in production), A stops, B starts (one Workers runtime at a time on this
        # machine: two at once hang)
        time.sleep(3)
        self.switch.switch(PORT_B)
        self.kill("A")
        self.serve("B", PORT_B)
        t0 = time.time()
        for got in seen.values():
            got.clear()  # what A answered does not count

        # B still serves A's files
        for f in only_a:
            st, h = status(BASE + "/assets/" + f)
            self.assertEqual(st, 200, f)
            self.assertNotIn("text/html", h.get("Content-Type", ""), f)

        took = {}
        for engine, p in pages.items():
            v = p["visible"]
            try:
                v.wait_for_function("document.documentElement.dataset.update === 'ready'", timeout=ANNOUNCE_S * 1000)
            except Exception:
                self.fail(f"{engine} visible: no new build after {ANNOUNCE_S} s (deploy at {round(t0, 1)}): {p['vlog']} "
                          + json.dumps(v.evaluate("[JSON.stringify(document.documentElement.dataset), window.__doc]")))
            took[engine] = round(v.evaluate("window.__readyAt") / 1000 - t0, 2)
        time.sleep(15)  # long enough for any reload to show (the old code reloaded a just-opened page within 8 s)

        for engine, p in pages.items():
            with self.subTest(engine=engine, page="visible"):
                v = p["visible"]
                self.kept(v, "visible", 1)
                self.assertLess(abs(v.evaluate("scrollY") - 800), 2, json.dumps(v.evaluate("window.__trace"), ensure_ascii=False))
                self.no_note(v, "visible")
                v.screenshot(path=os.path.join(SHOTS, f"visible-ready-{engine}.png"))
                self.link(v, "visible", 1)

            if "live" in p:  # Chromium
                with self.subTest(engine=engine, page="live dashboard"):
                    lv = p["live"]
                    self.kept(lv, "live", 1)
                    self.assertEqual(lv.evaluate("document.documentElement.dataset.update"), "ready")
                    self.assertTrue(lv.evaluate("!!document.fullscreenElement"), "the live dashboard left full screen")
                    got = seen["live-" + engine]
                    self.wait_for(lv, lambda: len(got) >= 2, f"live: no data read after the deploy: {got}")
                    self.assertEqual(set(got), {200}, f"live: A's reads against B: {got}")
                    lv.screenshot(path=os.path.join(SHOTS, f"live-after-deploy-{engine}.png"))

            with self.subTest(engine=engine, page="hidden"):
                h = p["hidden"]
                self.kept(h, "hidden", 1)
                got = seen["hidden-" + engine]
                got.clear()
                h.evaluate("window.__setVisibility('visible')")  # back on screen: its live queries are read again
                self.wait_for(h, lambda: len(got) >= 1, f"hidden: no data read when back on screen: {got}")
                time.sleep(2)
                self.assertEqual(set(got), {200}, f"hidden: A's reads against B: {got}")
                self.kept(h, "hidden", 1)
                self.no_note(h, "hidden")
                self.wait_scroll(h, 1500)
                # a data request that fails with B live: the note, and only a tap reloads
                h.route(re.compile(r"/_serverFn/"), lambda r: r.fulfill(status=500, content_type="application/json", body="{}"))
                h.evaluate("window.__setVisibility('hidden'); window.__setVisibility('visible')")
                note = h.locator(".yyc-update")
                expect(note).to_be_visible(timeout=T)
                expect(note).to_contain_text("已有更新")
                self.kept(h, "hidden", 1)
                h.screenshot(path=os.path.join(SHOTS, f"hidden-note-{engine}.png"))
                w = h.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
                self.assertLessEqual(w[0], w[1], f"sideways scroll: {w}")
                h.unroute(re.compile(r"/_serverFn/"))
                note.click()
                self.wait_new_build(h, f"hidden-after-tap-{engine}")
                self.assertEqual(h.url, self.urls["hidden"])
                self.wait_scroll(h, 1500)

            with self.subTest(engine=engine, page="input"):
                i = p["input"]
                self.kept(i, "input", 1)
                self.assertEqual(i.locator("#qin").input_value(), "立法會")
                self.no_note(i, "input")

            with self.subTest(engine=engine, page="back"):
                b = p["back"]
                b.go_back(wait_until="commit")
                if engine == "chromium":  # restored from the back/forward cache: still A, its next link loads B
                    b.wait_for_function("document.readyState === 'complete'")
                    time.sleep(3)
                    self.assertEqual(b.evaluate("sessionStorage.getItem('t-persisted-first')"), "true",
                                     "Back did not use the back/forward cache")
                    self.kept(b, "back", 2)
                    self.assertEqual(b.url, self.urls["back"])
                    b.wait_for_function("document.documentElement.dataset.update === 'ready'", timeout=ANNOUNCE_S * 1000)
                    self.link(b, "back", 2)
                else:  # no back/forward cache: Back is a full load, of B
                    self.wait_new_build(b, f"back-{engine}")
                    self.assertEqual(b.url, self.urls["back"])

            if engine == "webkit":
                with self.subTest(engine=engine, page="bfcache restore (pageshow persisted)"):
                    f = p["bfsim"]
                    p["bfsim_ctx"].set_offline(False)
                    time.sleep(1)  # the network is back before the page hears of it
                    f.evaluate("""() => { window.__setVisibilityQuiet('visible');
                                          dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })); }""")
                    f.wait_for_function("document.documentElement.dataset.update === 'ready'", timeout=ANNOUNCE_S * 1000)
                    time.sleep(3)
                    self.kept(f, "bfsim", 1)
                    self.wait_scroll(f, 900)

            with self.subTest(engine=engine, page="frozen"):
                f = p["frozen"]
                p["frozen_ctx"].set_offline(False)
                time.sleep(1)  # the network is back before the page hears of it (the online event)
                f.evaluate("window.__setVisibility('visible'); dispatchEvent(new Event('online'))")
                f.wait_for_function("document.documentElement.dataset.update === 'ready'", timeout=ANNOUNCE_S * 1000)
                time.sleep(3)
                self.kept(f, "frozen", 1)
                self.wait_scroll(f, 1200)
                self.link(f, "frozen", 1)
        print("announced after (s):", took, file=sys.stderr)
        for engine, s in took.items():
            self.assertLess(s, ANNOUNCE_S, engine)


if __name__ == "__main__":
    unittest.main(verbosity=2)
