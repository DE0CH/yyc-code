#!/usr/bin/env python3
"""搜尋 in the edge app (web-app/src/routes/search.tsx, -SearchPage.tsx), against a running dev server of the app on a
copy of real data (its search dictionary published: searchtext.publish_edge).

    SEARCH_SPA_URL=http://127.0.0.1:5182 PARITY_DATABASE_URL=postgresql:///yyc_dev_search python3 tests/test_search_spa.py

The beta host (X-Forwarded-Host yyc-beta.deyaochen.com) has the `search` flag on; the main site has it off.
1. The flag: off → /search, /api/search, /api/search/meta, /api/export/search.xlsx answer 404 {"error": "not found"}.
2. Phone (WebKit, iPhone, 390 px) and desktop (Chromium, 1600 px): a search URL renders on the server with its results
   (count, cards with marked terms, the extract, 原文), no console errors (hydration), no sideways scroll; the 篩選 panel
   is collapsed on the phone and a sticky column on desktop. Screenshots in ~/artifacts/spa/search/.
3. Every search is a history entry: a new query, a facet, the next page; Back returns to the search before.
4. The page's count and first results equal the server function's (search.run, in Node by tests/edge_call.py) on the
   same query; the export link downloads the workbook with an RFC 6266 file name (server/xlsx.ts attachment).
"""
import json, os, re, sys, unittest, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
BASE = os.environ.get("SEARCH_SPA_URL", "http://127.0.0.1:5182")
os.environ["DATABASE_URL"] = os.environ.get("PARITY_DATABASE_URL", "postgresql:///yyc_dev_search")
os.environ.setdefault("FLAGS_FILE", os.path.join(HERE, "deploy", "apps", "flags", "flags.json"))
BETA, MAIN = "yyc-beta.deyaochen.com", "newswatch.yyc-internal.com"
SHOTS = os.path.expanduser("~/artifacts/spa/search")
os.makedirs(SHOTS, exist_ok=True)
from playwright.sync_api import sync_playwright  # noqa: E402
from edge_call import edge  # noqa: E402

FIRST = None


def get(path, host=BETA):
    req = urllib.request.Request(BASE + path, headers={"X-Forwarded-Host": host})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def first_day():
    import corpus
    return corpus.db().execute("SELECT min(day) FROM posts WHERE thread IS NULL").fetchone()[0]


class SearchSpa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.first = first_day()
        cls.pw = sync_playwright().start()
        cls.chromium = cls.pw.chromium.launch()
        cls.webkit = cls.pw.webkit.launch()

    @classmethod
    def tearDownClass(cls):
        cls.chromium.close(); cls.webkit.close(); cls.pw.stop()

    def ctx(self, phone):
        if phone:
            c = self.webkit.new_context(**self.pw.devices["iPhone 13"])
        else:
            c = self.chromium.new_context(viewport={"width": 1600, "height": 1000})
        c.set_default_timeout(30000)
        # the beta site (the flag on): its Host as yyc-edge passes it, on this app's requests only
        c.route(BASE + "/**", lambda r: r.continue_(headers={**r.request.headers, "x-forwarded-host": BETA}))
        page = c.new_page()
        errors = []
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        return c, page, errors

    def no_sideways(self, page):
        w = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(w, 0, "no sideways scroll")

    def click_until(self, page, sel, cond, tries=20):
        """click until the page reacts (the server-rendered page answers taps once React has hydrated it)"""
        for _ in range(tries):
            page.locator(sel).first.click()
            try:
                page.wait_for_function(cond, timeout=1500)
                return
            except Exception:  # noqa: BLE001: not hydrated yet
                continue
        self.fail(f"{sel}: no reaction")

    def test_flag_off_is_404(self):
        for path in ("/search", "/search?q=香港", "/api/search?q=x", "/api/search/meta", "/api/export/search.xlsx?q=x"):
            st, _, body = get(urllib.parse.quote(path, safe="/?=&"), MAIN)
            self.assertEqual((st, json.loads(body)), (404, {"error": "not found"}), path)
        st, _, _ = get("/search", BETA)
        self.assertEqual(st, 200)

    def run_page(self, phone):
        q = {"q": ["香港 AND 政府"], "from": [self.first]}
        py = edge("search/search.run", q)
        c, page, errors = self.ctx(phone)
        try:
            url = f"{BASE}/search?q={urllib.parse.quote('香港 AND 政府')}&from={self.first}"
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector("article.hit", timeout=30000)
            self.assertIn(f"共 {py['total']:,} 篇", page.locator(".rhead .count").inner_text())
            titles = page.locator("article.hit h2").all_inner_texts()
            self.assertEqual(len(titles), len(py["posts"]))
            for t, p in zip(titles, py["posts"]):
                self.assertIn(p["title"], t.replace("AI 標題", "").replace("連結標題", ""))
            self.assertGreater(page.locator("article.hit mark").count(), 0, "terms are marked")
            hrefs = page.locator("article.hit h2 a").evaluate_all("as => as.map(a => a.getAttribute('href'))")
            self.assertEqual(hrefs[0], f"/item/{py['posts'][0]['id']}")
            self.no_sideways(page)
            fbody = page.locator(".filters .fbody")
            if phone:
                self.assertFalse(fbody.is_visible(), "phone: 篩選 collapsed")
                self.click_until(page, ".fsummary", "document.querySelector('.filters').classList.contains('open')")
                page.wait_for_selector(".filters .fbody .fgroup", timeout=30000)
                self.assertTrue(fbody.is_visible())
                self.no_sideways(page)
                page.screenshot(path=f"{SHOTS}/phone-filters.png", full_page=False)
                page.locator(".fsummary").click()
            else:
                self.assertTrue(fbody.is_visible(), "desktop: the filters column")
                self.assertEqual(page.locator(".filters").evaluate("e => getComputedStyle(e).position"), "sticky")
                box = page.locator(".filters").bounding_box()
                self.assertLess(box["width"], 320)
            page.screenshot(path=f"{SHOTS}/{'phone' if phone else 'desktop'}.png", full_page=not phone)
            # a new query is a new history entry; Back returns to the search before
            box = page.locator("form[role=search] input[type=search]")
            box.fill("選舉")
            self.click_until(page, "button.go", "location.search.includes('q=%E9%81%B8%E8%88%89')")
            page.wait_for_function("() => document.querySelector('.rhead .count') && !document.querySelector('.rhead .spin')", timeout=30000)
            self.assertIn("選舉", page.locator("article.hit").first.inner_text() + page.locator("article.hit").nth(1).inner_text())
            # a facet narrows to one outlet (its own entry)
            n_before = page.locator(".rhead .count").inner_text()
            page.locator(".facets button").first.click()
            page.wait_for_url(re.compile(r"type="), timeout=30000)
            page.wait_for_function("() => !document.querySelector('.rhead .spin')", timeout=30000)
            if page.locator(".pager button").count():
                page.locator(".pager button").nth(1).click()
                page.wait_for_url(re.compile(r"page=2"), timeout=30000)
                page.wait_for_function("() => !document.querySelector('.rhead .spin')", timeout=30000)
                self.assertIn("第 2 /", page.locator(".pager").inner_text())
                page.go_back()
                page.wait_for_url(lambda u: "page=2" not in u, timeout=30000)
            page.go_back()
            page.wait_for_url(lambda u: "type=" not in u, timeout=30000)
            page.wait_for_function("() => !document.querySelector('.rhead .spin')", timeout=30000)
            self.assertEqual(page.locator(".rhead .count").inner_text(), n_before)
            page.go_back()
            page.wait_for_url(re.compile(r"q=%E9%A6%99%E6%B8%AF"), timeout=30000)
            self.assertEqual(page.locator("form[role=search] input[type=search]").input_value(), "香港 AND 政府")
            # the export link: an RFC 6266 file name, an xlsx
            href = page.locator(".rhead a.btn").get_attribute("href")
            st, h, body = get(href)
            self.assertEqual(st, 200)
            self.assertTrue(body.startswith(b"PK"))
            self.assertRegex(h.get("Content-Disposition") or h.get("content-disposition"),
                             r'attachment; filename="search_\d{4}-\d\d-\d\d_\d{4}-\d\d-\d\d_\d{8}-\d{4}\.xlsx"')
            self.no_sideways(page)
            self.assertEqual([e for e in errors if "favicon" not in e], [], "no console errors (hydration)")
        finally:
            c.close()

    def test_desktop(self):
        self.run_page(False)

    def test_phone(self):
        self.run_page(True)

    def test_empty_page_and_old_links(self):
        c, page, errors = self.ctx(False)
        try:
            page.goto(BASE + "/search", wait_until="networkidle")
            self.assertIn("輸入關鍵字", page.locator(".list").inner_text())
            page.wait_for_selector(".sites label", timeout=30000)
            self.click_until(page, "button.go", "location.search.includes('from=')")  # 按「搜尋」 on an empty box: the last 30 days
            page.wait_for_selector(".rhead .count", timeout=30000)
            self.assertIn("共", page.locator(".rhead .count").inner_text())
            # an old link with repeated keys keeps working (the same search, written with commas)
            page.goto(BASE + "/search?q=%E9%A6%99%E6%B8%AF&type=News&type=Forum&from=" + self.first, wait_until="networkidle")
            self.assertIn("type=News%2CForum", page.url)
            page.wait_for_selector(".rhead .count", timeout=30000)
            py = edge("search/search.run", {"q": ["香港"], "type": ["News", "Forum"], "from": [self.first]})
            self.assertIn(f"共 {py['total']:,} 篇", page.locator(".rhead .count").inner_text())
            # a bad query says why, inline
            page.goto(BASE + "/search?q=%22abc", wait_until="networkidle")
            page.wait_for_selector(".note.err", timeout=30000)
            self.assertIn("查詢有誤", page.locator(".note.err").inner_text())
            self.assertEqual([e for e in errors if "favicon" not in e and "400" not in e], [])
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
