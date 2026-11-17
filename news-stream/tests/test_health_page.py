#!/usr/bin/env python3
"""YYC system health, /app/health (Deyao, 2026-11-17: an app that only shows system health in small text, the wall's
colours, two columns, Jarvis 4 in, the screen's size deciding how much detail shows), rendered by `vite dev` against
HEALTH_DB (default yyc_dev_health: emptied and filled with the live dashboard test's fixtures), signed in with the
test's own Access key.

    python3 tests/test_health_page.py

1. Deyao only: /app/health, its frame, /app/health/data, /app/health/app-signin and an unknown path under it answer a
   plain 404 to no identity and to a colleague; Deyao gets the page (noindex) and the data (kv live:sys and Jarvis 4's
   push). The app's sign-in sends the sheet to dev.de0ch.yycsystems://signed-in with his Access token and the app's state
   as it came; a malformed state is refused.
2. On an iPhone 17's app area (402 x 778, WebKit, 3x): two columns of 200 and 201 px with the rule between; every font
   size, line height and line on a whole px (the wall's blur pitfall); nothing below a column's bottom, no sideways scroll;
   Jarvis 4 needing-you first; every lit state in its colour (AnyAPI can't be read: magenta; a Deployment with no pod ready:
   red; a failed CronJob: amber; a booting host: blue); no link, button or input.
3. The screen decides the detail (lib/healthFit.ts): every level-0 line shows at 778 and at 1000 px; the taller window
   shows more detail lines, never fewer; a short one (560 px) folds the oldest idle sessions into "+N idle"; a group of
   details (the AWS hosts) shows whole or not at all; the space a column leaves is less than its next detail would take.
4. healthFit in Node: fit's levels and groups, the fold.
5. A part gone old: kv live:sys 5 min old makes the systems' headings say "no update since" in magenta.
6. No console error. Screenshots in SHOTS.
"""
import json, os, re, subprocess, sys, time, unittest

os.environ.setdefault("LIVE_DB", os.environ.get("HEALTH_DB", "yyc_dev_health"))
os.environ.setdefault("LIVE_PORT", os.environ.get("HEALTH_PORT", "5193"))
os.environ.setdefault("SHOTS", os.path.expanduser("~/artifacts/health-page/test"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_live_dashboard_page as L  # noqa: E402  (fixtures, Server, push_jarvis; resets LIVE_DB)
ops = L.ops
from playwright.sync_api import sync_playwright  # noqa: E402

W, H = 402, 778  # iPhone 17 (402 x 874 pt) less the status bar (62) and the home indicator (34)
SCHEME = "dev.de0ch.yycsystems"


def page_at(br, w, h, errors=None, path="/app/health?frame=1", dpr=3):
    ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr)
    tok = ops.token(ops.DEYAO)
    ctx.route(ops.BASE + "/**", lambda r: r.continue_(headers={**r.request.headers, "cf-access-jwt-assertion": tok}))
    page = ctx.new_page()
    if errors is not None:
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(ops.BASE + path, wait_until="domcontentloaded", timeout=180000)
    if "frame=1" in path:
        page.wait_for_selector('.hp[data-ready="1"]', timeout=120000)
        page.wait_for_function("document.fonts.status === 'loaded'")
    return ctx, page


LAYOUT = r"""() => {
  const cols = [...document.querySelectorAll('.hp-col')].map(c => { const r = c.getBoundingClientRect();
    const lines = [...c.querySelectorAll('.hp-l')];
    const bottom = Math.max(...lines.map(l => l.getBoundingClientRect().bottom));
    return { x: r.left, w: r.width, h: r.height, bottom: bottom - r.top, n: lines.length,
             l0: c.querySelectorAll('[data-level="0"]').length, det: c.querySelectorAll('[data-level]:not([data-level="0"])').length }; });
  const off = [], sizes = new Set();
  for (const e of document.querySelectorAll('.hp *')) {
    const own = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const cs = getComputedStyle(e); sizes.add(cs.fontSize + '/' + cs.lineHeight);
    const rg = document.createRange(); rg.selectNodeContents(e);
    for (const q of rg.getClientRects()) if (q.top % 1 || q.left % 1) { off.push([e.className, e.textContent.slice(0, 30), q.top, q.left]); break; }
  }
  return { cols, off, sizes: [...sizes], scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
           controls: document.querySelectorAll('a, button, input, select, textarea').length,
           text: document.querySelector('.hp').innerText };
}"""


class HealthPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        L.fixtures()
        L.sys_fixture()
        cls.srv = L.Server(f"postgres://claude:dev@127.0.0.1:5432/{L.DB}")
        assert L.push_jarvis(L.JARVIS) == 200
        cls.pw = sync_playwright().start()
        cls.wk = cls.pw.webkit.launch()
        os.makedirs(L.SHOTS, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.wk.close()
        cls.pw.stop()
        cls.srv.stop()

    def test_1_deyao_only_and_sign_in(self):
        st = "aB3_-" * 5
        for who in (None, ops.token(L.COLLEAGUE)):
            for p in ("/app/health", "/app/health?frame=1", "/app/health/data", f"/app/health/app-signin?state={st}", "/app/health/x"):
                code, h, body = ops.get(p, {"Cf-Access-Jwt-Assertion": who} if who else {})
                self.assertEqual((code, body), (404, b"not found"), p)
        tok = ops.token(ops.DEYAO)
        code, h, body = ops.get("/app/health", {"Cf-Access-Jwt-Assertion": tok})
        self.assertEqual((code, h.get("X-Robots-Tag")), (200, "noindex"))
        code, h, body = ops.get("/app/health/data", {"Cf-Access-Jwt-Assertion": tok})
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(d["sys"]["ai"]["health"]["state"], "warming")
        self.assertEqual(d["jarvis"]["total"], 34)
        import urllib.request, urllib.error

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        for state, want in ((st, 302), ("short", 400), ("bad state with spaces 12345", 400)):
            req = urllib.request.Request(ops.BASE + "/app/health/app-signin?state=" + urllib.parse.quote(state), headers={"Cf-Access-Jwt-Assertion": tok})
            try:
                r = op.open(req, timeout=60); code, hd = r.status, r.headers
            except urllib.error.HTTPError as e:  # a redirect not followed comes back as one
                code, hd = e.code, e.headers
            loc = hd.get("Location")
            self.assertEqual(code, want, state)
            if want == 302:
                self.assertEqual(loc, f"{SCHEME}://signed-in?state={st}#token={tok}")
                self.assertEqual(hd.get("Cache-Control"), "no-store")

    def test_2_iphone_17(self):
        errors = []
        ctx, page = page_at(self.wk, W, H, errors)
        page.screenshot(path=os.path.join(L.SHOTS, "iphone17.png"))
        r = page.evaluate(LAYOUT)
        self.assertEqual([(c["x"], c["w"]) for c in r["cols"]], [(0, 200), (200, 202)])  # 201 + the 1 px rule as its border
        for c in r["cols"]:
            self.assertEqual(c["h"], H)
            self.assertLessEqual(c["bottom"], H, c)
        # the pixel face itself, not a fallback (the site's English font rule once replaced it): its digits are 5 px wide
        w10 = page.evaluate("""() => { const s = document.createElement('span'); s.textContent = '0123456789';
            document.querySelector('.hp-l').appendChild(s); const w = s.getBoundingClientRect().width; s.remove(); return w; }""")
        self.assertEqual(w10, 50)
        self.assertEqual(r["off"], [])
        self.assertTrue(all(re.fullmatch(r"\d+px/\d+px", s) for s in r["sizes"]), r["sizes"])
        self.assertEqual(r["scrollW"], r["innerW"])
        self.assertEqual(r["controls"], 0)
        t = r["text"]
        self.assertIn("Cathay seat for the HKG flight", t)
        first = page.locator(".hp-j").first
        self.assertEqual(first.get_attribute("data-state"), "waiting")
        self.assertIn("34 sessions", t)
        self.assertIn("+2 more sessions", t)  # 34 pushed, the Worker keeps 32
        colour = lambda sel: page.locator(sel).first.evaluate("e => getComputedStyle(e).color")  # noqa: E731
        self.assertEqual(colour(".hp-kv.blind:has-text('AnyAPI') .k"), "rgb(244, 114, 208)")
        self.assertEqual(colour(".hp-kv.bad:has-text('Deployments') .v"), "rgb(255, 77, 90)")
        self.assertEqual(colour(".hp-ln.bad:has-text('wisers 0/1')"), "rgb(255, 77, 90)")
        self.assertEqual(colour(".hp-kv.warn:has-text('CronJobs') .v"), "rgb(255, 184, 28)")
        self.assertEqual(colour(".hp-kv.info:has-text('aws-1b2c3d') .v"), "rgb(90, 169, 255)")
        self.assertIn("OpenRouter", t)
        self.assertIn("USD 61.52", t)
        self.assertIn("needs you", t)
        ctx.close()
        self.assertEqual(errors, [])

    def test_3_detail_follows_the_screen(self):
        got = {}
        for h in (560, 778, 1000):
            ctx, page = page_at(self.wk, W, h)
            page.screenshot(path=os.path.join(L.SHOTS, f"h{h}.png"))
            r = page.evaluate(LAYOUT)
            got[h] = r
            for c in r["cols"]:
                self.assertLessEqual(c["bottom"], h, (h, c))
            # the AWS hosts' lines: all four or none
            self.assertIn(page.locator(".hp-sub:has-text('aws-a0000')").count(), (0, 4), h)
            ctx.close()
        self.assertEqual(got[778]["cols"][1]["l0"], got[1000]["cols"][1]["l0"])
        for side in (0, 1):
            self.assertGreaterEqual(got[1000]["cols"][side]["det"], got[778]["cols"][side]["det"])
            self.assertGreaterEqual(got[778]["cols"][side]["det"], got[560]["cols"][side]["det"])
        self.assertGreater(got[1000]["cols"][1]["det"] + got[1000]["cols"][0]["det"], got[560]["cols"][1]["det"] + got[560]["cols"][0]["det"])
        self.assertRegex(got[560]["text"], r"\+\d+ idle, longest")
        self.assertNotRegex(got[1000]["text"], r"\+\d+ idle")

    def test_4_fit_in_node(self):
        out = L.node("""import { fit, jarvisFold, frame, ROW, columns } from './src/lib/healthFit.ts';
          const s = [{ key: 'a', lines: [{ level: 0 }, { level: 1, group: 'g' }, { level: 1, group: 'g' }, { level: 2 }] },
                     { key: 'b', lines: [{ level: 0 }, { level: 1 }, { level: 3 }] }];
          const at = n => fit(s, frame(2) + n * ROW).keep.map(x => x.map(Number).join('')).join('|');
          console.log(JSON.stringify({ two: at(2), three: at(3), four: at(4), five: at(5), all: at(7), short: fit(s, frame(2) + ROW),
            fold: [jarvisFold(10, 6, 12), jarvisFold(10, 6, 8), jarvisFold(30, 3, 10)], cols: [columns(402), columns(375)] }));""")
        self.assertEqual(out["two"], "1000|100")
        self.assertEqual(out["three"], "1000|110")    # the group of two doesn't fit; the single level-1 line does
        self.assertEqual(out["four"], "1110|100")     # the group first (its section comes first), then nothing fits
        self.assertEqual(out["five"], "1110|110")
        self.assertEqual(out["all"], "1111|111")
        self.assertEqual(out["short"]["more"], 2)  # room for one line: it says "2 more lines"
        self.assertEqual(out["fold"], [0, 3, 3])
        self.assertEqual(out["cols"], [[200, 201], [187, 187]])

    def test_5_old_systems_data(self):
        L.sys_fixture(age=300)
        try:
            ctx, page = page_at(self.wk, W, H)
            page.wait_for_selector(".hp-sec[data-sec='prov'] h2 .hr.blind", timeout=30000)
            self.assertIn("no update since", page.locator(".hp-sec[data-sec='prov'] h2").inner_text())
            self.assertEqual(page.locator(".hp-sec[data-sec='jarvis'] h2 .hr.blind").count(), 0)
            page.screenshot(path=os.path.join(L.SHOTS, "old.png"))
            ctx.close()
        finally:
            L.sys_fixture()

    def test_6_the_shell(self):
        ctx, page = page_at(self.wk, W, H, path="/app/health")
        page.wait_for_selector("iframe[data-frame='0']", timeout=60000)
        self.assertEqual(page.locator("iframe").get_attribute("src"), "/app/health?frame=1")
        page.frame_locator("iframe").locator('.hp[data-ready="1"]').wait_for(timeout=120000)
        ctx.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
