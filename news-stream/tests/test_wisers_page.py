#!/usr/bin/env python3
"""WiseSearch's pages (web-app/src/routes/-wisers/; Deyao, 2026-10-29: the full read-only WiseSearch, WisersOne's layout
in his house style) against the relay (wisers/relay.py) and the fake WisersOne (fake_wisers.py).

    CHROME=/usr/bin/google-chrome python3 tests/test_wisers_page.py     # starts `vite dev` on WISERS_PAGE_PORT (5187)
    (a local Postgres with the corpus schema in WISERS_PAGE_PG, default postgres://claude:dev@127.0.0.1:5432/yyc_dev_mcp)

The browser's /app/wisers/api/* requests are routed to the relay (in production yyc-edge sends that path to the
cluster). Signing in: the test signs Access tokens for the WiseSearch Access app's audience with its own key
(ACCESS_TEST_JWKS in web-app/.dev.vars, as test_edge_ops.py does). WebKit iPhone 390 px and Chromium 1280 px:
1. no token, or a token for another Access app: a plain 404; a token for the WiseSearch app: the page, server-rendered;
2. the form: 快速 with WisersOne's keyword suggestions, 邏輯 (four fields), the media panel (a parent ticks its items, a
   specific medium included), a label tree (a level loads when opened; a suggested label becomes a chip), 負面新聞,
   其他選項; 搜尋 runs the checks first (a stop word shows the dialog, "仍然搜尋" goes on);
3. the results at their own URL: the criteria plates, the tabs with WisersOne's counts (a tab is a new search with the
   tab's media type), each row's content icons (the print row has 文字 + 印刷版; the web rows 網頁), page and section, the
   hit marked; the URL's options reached Wisers (pubType from the form, never createSearchHistoryFlag true); paging
   (startIdx), the sidebar's facets and hot keywords (loaded when opened), 相似 n expanding into the similar articles;
4. the reader: the heading plates (outlet, 版面 A07 財經, words, date), the text with the hit marked and Wisers' scripts
   gone, the images panel (thumbnails), the full-screen viewer, 同版其他新聞 as a line, 上一篇 / 下一篇 over the job's
   items; the 印刷版 icon opens the page image; the footer names the source;
5. 下載: the sheet's options, a job, the file (explicit document ids reached Wisers);
6. 紀錄: the searches and the document opens (who, outcome);
7. house style: no rounded corner and no transition anywhere, no sideways scroll, controls >= 44px; screenshots in
   ~/artifacts/wisers-page/ (light and dark, phone and desktop).
"""
import json, os, subprocess, sys, time, unittest, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_wisers import FakeWisers, Store, new_ns  # noqa: E402

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

APP = os.path.join(HERE, "web-app")
PORT = int(os.environ.get("WISERS_PAGE_PORT", "5187"))
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = os.path.expanduser("~/artifacts/wisers-page")
WISERS_AUD = "af0134321afca2f355d7782cb0ad2a2c06e8f901208d8e8d3bd121941d3e5cab"
APP_AUD = "8b644dde1fadd76a241cace4428d57ebe8fccb6cdfe0af987e5f35bc7e31dd9b"
ISS = "https://yyc-hk.cloudflareaccess.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
NS = new_ns("page")
ST = Store(NS)
W = FakeWisers(captcha=False, latency=0.2)


def token(email="tony.leung@example.com", aud=WISERS_AUD):
    pem = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [aud], "iss": ISS, "iat": now, "exp": now + 3600}, pem, algorithm="RS256",
                      headers={"kid": "test"})


def jwks():
    pub = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
    pub.update(kid="test", alg="RS256", use="sig")
    return json.dumps({"keys": [pub]})


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def hydrated(page, sel):
    page.wait_for_function(f"Object.keys(document.querySelector({json.dumps(sel)}) || {{}}).some(k => k.startsWith('__react'))",
                           timeout=120000)


STYLE_CHECK = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('.page-wisers, .page-wisers *')) {
    const s = getComputedStyle(el);
    if (['borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius'].some(k => parseFloat(s[k]) > 0))
      bad.push('radius ' + el.tagName + '.' + el.className);
    if (parseFloat(s.transitionDuration) > 0 || (s.animationName && s.animationName !== 'none')) bad.push('motion ' + el.tagName + '.' + el.className);
  }
  const small = [...document.querySelectorAll('.page-wisers button, .page-wisers .tb, .page-wisers .go, .page-wisers .ico, .page-wisers .seg button')]
    .filter(el => el.offsetParent && el.getBoundingClientRect().height < 43.5 && !el.closest('.chips, .slist, .thumbs'))
    .map(el => 'small ' + el.className + ' ' + (el.textContent || '').trim().slice(0, 20));
  return {bad: bad.slice(0, 10), small: small.slice(0, 10), wide: document.documentElement.scrollWidth > window.innerWidth + 1};
}"""


class WisersPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        W.stale_state(NS)
        cls.rport = 18900 + os.getpid() % 500
        env = dict(os.environ, **W.env(NS), PYTHONUNBUFFERED="1", WISERS_PROBE_SECS="0", WISERS_LOGIN_GAP="0")
        cls.rlog = open(os.path.join(SHOTS, "relay.log"), "w")  # noqa: SIM115
        cls.relay = subprocess.Popen([sys.executable, os.path.join(HERE, "wisers", "relay.py"), "--port", str(cls.rport),
                                      "--prefix", "/app/wisers/"], env=env, stdout=cls.rlog, stderr=subprocess.STDOUT)
        cls.vars = os.path.join(APP, ".dev.vars")
        if not os.path.exists(cls.vars):
            with open(cls.vars, "w") as f, open(cls.vars + ".example") as ex:
                f.write(ex.read())
        with open(cls.vars) as f:
            cls.vars_before = f.read()
        with open(cls.vars, "w") as f:
            f.write("\n".join(l for l in cls.vars_before.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                    "\nACCESS_TEST_JWKS=" + jwks() + "\n")
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=os.environ.get(
            "WISERS_PAGE_PG", "postgres://claude:dev@127.0.0.1:5432/yyc_dev_mcp"))
        cls.log = open(os.path.join(SHOTS, "dev.log"), "w")  # noqa: SIM115
        cls.dev = subprocess.Popen(["npx", "vite", "dev", "--port", str(PORT), "--strictPort"], cwd=APP, env=env,
                                   stdout=cls.log, stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < 180:
            try:
                urllib.request.urlopen(BASE + "/version", timeout=5)
                break
            except Exception:
                time.sleep(1)
        get("/app/wisers/", {"Cf-Access-Jwt-Assertion": token()})  # the first render compiles the route

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()
        cls.relay.terminate()
        with open(cls.vars, "w") as f:
            f.write(cls.vars_before)

    def test_1_gate(self):
        self.assertEqual(get("/app/wisers/")[0], 404)
        self.assertEqual(get("/app/wisers/", {"Cf-Access-Jwt-Assertion": token(aud=APP_AUD)})[0], 404)
        self.assertEqual(get("/app/wisers/doc/doc1", {"Cf-Access-Jwt-Assertion": "x.y.z"})[0], 404)
        code, body = get("/app/wisers/", {"Cf-Access-Jwt-Assertion": token()})
        self.assertEqual(code, 200)
        self.assertIn("WiseSearch", body)
        self.assertIn('name="q"', body, "the form is server-rendered")

    def style(self, page, where):
        r = page.evaluate(STYLE_CHECK)
        self.assertEqual(r["bad"], [], where)
        self.assertFalse(r["wide"], f"{where}: sideways scroll")
        self.assertEqual(r["small"], [], f"{where}: controls under 44px")

    def browse(self, name, browser, ctx_args):
        b = browser.launch(**({"executable_path": os.environ["CHROME"]} if browser.name == "chromium" and os.environ.get("CHROME") else {}))
        shots = []
        for scheme in ("light", "dark"):
            ctx = b.new_context(**ctx_args, color_scheme=scheme, extra_http_headers={"Cf-Access-Jwt-Assertion": token()})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: None if "access control checks" in str(e) else errors.append(str(e)))

            def to_relay(route):
                u = urllib.parse.urlsplit(route.request.url)
                req = urllib.request.Request(f"http://127.0.0.1:{self.rport}{u.path}?{u.query}", method=route.request.method,
                                             data=route.request.post_data_buffer, headers={
                                                 "Cf-Access-Jwt-Assertion": route.request.headers.get("cf-access-jwt-assertion", ""),
                                                 "Content-Type": route.request.headers.get("content-type", "application/json")})
                try:
                    with urllib.request.urlopen(req, timeout=70) as r:
                        route.fulfill(status=r.status, body=r.read(), headers={"Content-Type": r.headers.get("Content-Type")})
                except urllib.error.HTTPError as e:
                    route.fulfill(status=e.code, body=e.read(), headers={"Content-Type": e.headers.get("Content-Type") or "text/plain"})
            page.route("**/app/wisers/api/**", to_relay)
            self.flow(page, f"{name}-{scheme}", full=(scheme == "light"))
            self.assertEqual(errors, [], f"{name} {scheme}: page errors")
            shots.append(scheme)
            ctx.close()
        b.close()

    def flow(self, page, tag, full):
        kw = f"施政{tag.replace('-', '')}"  # its own search per run: the relay answers a repeat from memory
        page.goto(BASE + "/app/wisers/")
        hydrated(page, "form#wf")
        self.style(page, tag + " form")
        if full:
            page.locator(".kw input[name=q]").fill("施政")
            page.wait_for_selector(".kw .slist button", timeout=30000)
            self.assertIn("施政報告", page.locator(".kw .slist").inner_text(), "WisersOne's keyword suggestions")
            page.locator(".modes .seg button", has_text="邏輯搜尋").click()
            page.locator("input[name=and]").fill("房屋")
            page.locator("input[name=nand]").fill("公屋")
            page.locator(".modes .seg button", has_text="快速搜尋").click()
            page.locator("details.media > summary").click()
            page.wait_for_selector("label:has-text('香港報刊') input[name=mt]", timeout=30000)
            page.locator("label.par:has-text('報刊') input").check()
            self.assertTrue(page.locator("label:has-text('香港報刊') input[name=mt]").is_checked(), "a parent ticks its items")
            page.locator(".picker input").fill("明")
            page.wait_for_selector(".mlist li", timeout=30000)
            page.locator(".mlist li button[aria-label^='包括']").first.click()
            self.assertEqual(page.locator(".picker .chip").count(), 1)
            page.locator("details.tree[data-key='tree:subject'] > summary").click()
            page.locator("details.tree[data-key='tree:subject'] .sugg input").fill("施")
            page.wait_for_selector("details.tree[data-key='tree:subject'] .sugg .slist button", timeout=30000)
            page.locator("details.tree[data-key='tree:subject'] .sugg .slist button").first.click()
            self.assertEqual(page.locator("details.tree[data-key='tree:subject'] .chips .chip").count(), 1)
            page.locator("input[name=author]").fill("記者甲")
            page.screenshot(path=f"{SHOTS}/{tag}-form.png", full_page=True)
            # a stop word: the check dialog, then 仍然搜尋
            page.locator(".kw input[name=q]").fill(f"{kw} 的")
            n0 = W.S["searches"]
            page.locator("form#wf button[type=submit]").click()
            page.wait_for_selector("dialog.wdlg[open]", timeout=30000)
            self.assertIn("的", page.locator("dialog.wdlg").inner_text())
            self.assertEqual(W.S["searches"], n0, "the check is no search")
            page.locator("dialog.wdlg button", has_text="仍然搜尋").click()
        else:
            page.locator(".kw input[name=q]").fill(kw)
            page.locator("form#wf button[type=submit]").click()
        page.wait_for_url("**/app/wisers/search?**")
        page.wait_for_selector(".wrow[data-doc]", timeout=60000)
        page.wait_for_selector(".tabs button .num:text-matches('\\\\d')", timeout=30000)
        self.assertIn("報刊", page.locator(".tabs").inner_text())
        self.assertIn("10", page.locator(".tabs button", has_text="報刊").inner_text())
        rows = page.locator(".wrow[data-doc]")
        self.assertEqual(rows.count(), 3)
        first = rows.nth(0)
        self.assertEqual(first.locator(".ico-text").count() + first.locator(".ico-img").count(), 2, "print row: 文字 + 印刷版")
        self.assertIn("A07 版", first.inner_text())
        self.assertEqual(first.locator(".ex mark").count(), 1)
        self.assertEqual(rows.nth(1).locator(".ico-website").count(), 1)
        if full:
            raw = sorted(ST.listdir("raw"))[-1]
            crit = ST.read_json(f"raw/{raw}")["request"]["criteria"]
            self.assertEqual((crit["pubType"], crit["author"], crit["createSearchHistoryFlag"]), ("print-hk", "記者甲", False))
            self.assertTrue(crit["includePubids"])
            self.assertEqual(len(crit["subjects"]), 1)
            # a tab is a new search with the tab's media type
            page.locator(".tabs button", has_text="網站").click()
            page.wait_for_url("**tab=web**")
            page.wait_for_selector(".wrow[data-doc]", timeout=60000)
            raw = sorted(ST.listdir("raw"))[-1]
            self.assertEqual(ST.read_json(f"raw/{raw}")["request"]["criteria"]["mediaType"], "web")
            # the sidebar
            page.locator(".side-btn").click()
            page.locator(".side .fh", has_text="媒體及地區").click()
            page.wait_for_selector(".side ul.facet li", timeout=30000)
            page.locator(".side .seg button", has_text="熱門關鍵字").click()
            page.locator(".side .fh", has_text="熱門關鍵字").click()
            page.wait_for_selector(".side ol.hotw li", timeout=30000)
            self.style(page, tag + " results with sidebar")
            page.screenshot(path=f"{SHOTS}/{tag}-results.png", full_page=True)
            # download
            rows = page.locator(".wrow[data-doc]")
            rows.nth(0).locator(".lead input").check()
            rows.nth(1).locator(".lead input").check()
            page.locator(".tools .tb", has_text="下載").click()
            page.locator(".sheet .seg button", has_text="只有標題").click()
            page.locator(".sheet .seg button", has_text="TXT").click()
            page.locator(".sheet button.go").click()
            try:
                page.wait_for_selector(".sheet a.go", timeout=30000)
            except Exception:
                self.fail("no download file; the sheet says: " + page.locator(".sheet").inner_text())
            self.assertEqual(W.S["download"]["docIds"], [rows.nth(0).get_attribute("data-doc"), rows.nth(1).get_attribute("data-doc")])
        else:
            self.style(page, tag + " results")
            page.screenshot(path=f"{SHOTS}/{tag}-results.png", full_page=True)
        # the reader
        page.locator(".wrow[data-doc]").nth(0).locator("h2 a").click()
        page.wait_for_url("**/app/wisers/doc/**")
        page.wait_for_selector("article.doc .text", timeout=60000)
        doc = page.locator("article.doc")
        self.assertIn("A07", doc.locator(".plates").inner_text())
        self.assertEqual(doc.locator(".text mark").count(), 1)
        self.assertNotIn("bad()", doc.inner_text())
        page.wait_for_selector(".imgs .thumbs img", timeout=30000)
        self.assertEqual(page.locator(".imgs .thumbs li").count(), 2)
        self.assertEqual(page.locator(".samepage ol.samelist li").count(), 2)
        self.assertIn("全文來源：Wisers", doc.locator(".dfoot").inner_text())
        self.style(page, tag + " reader")
        page.screenshot(path=f"{SHOTS}/{tag}-reader.png", full_page=True)
        page.locator(".imgs .thumbs button").first.click()
        page.wait_for_selector(".viewer img", timeout=10000)
        page.screenshot(path=f"{SHOTS}/{tag}-viewer.png")
        page.locator(".viewer .vbar button:has(svg[aria-label='關閉'])").click()
        page.locator(".dtools .tb", has_text="下一篇").click()
        page.wait_for_url("**/doc/doc2**")
        page.wait_for_selector("article.doc .text", timeout=60000)
        page.go_back()
        page.go_back()
        page.wait_for_selector(".wrow[data-doc]", timeout=30000)
        # the 印刷版 icon: the page image
        page.locator(".wrow[data-doc]").nth(0).locator(".ico-img").click()
        page.wait_for_selector(".imgs.big .page-img img", timeout=60000)
        page.screenshot(path=f"{SHOTS}/{tag}-page-image.png", full_page=True)
        if full:
            page.goto(BASE + "/app/wisers/log")
            page.wait_for_selector(".logrow", timeout=30000)
            self.assertIn("tony.leung@example.com", page.locator(".logrow").first.inner_text())
            page.locator(".seg button", has_text="文章、圖片及其他").click()
            page.wait_for_selector(".logrow[data-kind=doc]", timeout=30000)
            self.style(page, tag + " log")
            page.screenshot(path=f"{SHOTS}/{tag}-log.png", full_page=True)

    def test_2_iphone(self):
        with sync_playwright() as pw:
            self.browse("iphone", pw.webkit, dict(pw.devices["iPhone 15 Pro"]))

    def test_3_desktop(self):
        with sync_playwright() as pw:
            self.browse("desktop", pw.chromium, dict(viewport={"width": 1280, "height": 900}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
