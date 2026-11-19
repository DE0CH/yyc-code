#!/usr/bin/env python3
"""數據分析 in the React app (web-app/src/routes/dashboard.tsx), against a running dev server on a copy of real data:

    DASH_BASE=http://localhost:5181 DASH_DB=postgresql:///yyc_dev_dash python3 tests/test_dashboard_spa.py

WebKit (iPhone, 390 px, touch) and Chromium (1600 px): every view (帖子, 熱門話題 and a topic, 主題, 人物 with and
without an author) renders from the server, hydrates with no console error, has no sideways scroll, no text running out
of its box (熱門話題's phone cards: each label and value wraps inside its own cell) and shows its content; the URL is
dashboard.html's (defaults written in, 摘要 first; 摘要/作者/來源/趨勢 each with its 「?」); drill-downs are
history entries (a topic, an author via 作者分析), the topic's ‹ 熱門話題 is the browser's Back, the period row and the
filters only change their params, ?post= opens the page holding the post expanded and marked; a stream switch drops
tid/page/post. The persona's AI button (flag `ai` on the beta host; /app/me, /api/ai/estimate and /app/ai/persona
mocked in the browser): the projected cost before the press, the streamed answer, the cost charged, the 「AI 讀了…」 line
opening the posts it read; the post and hot-topic listings have no AI button (Deyao, 2026-10-28). The exports answer an .xlsx with an RFC 6266 Content-Disposition.
Screenshots: ~/artifacts/spa/dash/.
"""
import json, os, re, subprocess, sys, time, unittest, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
OWN = not os.environ.get("DASH_BASE")  # no server given (CI): a dev server of its own on a database seeded below
import devserver  # noqa: E402
devserver.ensure(5181, "DASH_BASE", "DASH_DB")
BASE = os.environ.get("DASH_BASE", "http://localhost:5181")
DB = os.environ.get("DASH_DB", "postgresql:///yyc_dev_dash")
SHOTS = os.path.expanduser("~/artifacts/spa/dash")
BETA = "yyc-beta.deyaochen.com"
from playwright.sync_api import sync_playwright  # noqa: E402

T = 20000  # ms: every wait has a timeout


def psql(sql):
    return subprocess.run(["psql", DB, "-Atc", sql], capture_output=True, text=True, check=True).stdout.strip()


# boxes in the view whose own text runs out of them sideways (a label or value wider than its card cell runs into the
# next one, e.g. 熱門話題's "Correlation Index (%)Initial Date" at 390 px); scroll boxes, charts and form controls
# are left out
OVERFLOWING = """() => [...document.querySelectorAll('#view *')].filter(e => {
  if (e.closest('svg, select, input, button')) return false;
  const cs = getComputedStyle(e);
  if (cs.display === 'inline' || cs.display === 'none' || !e.clientWidth || !e.offsetParent) return false;
  if (/(auto|scroll|hidden|clip)/.test(cs.overflowX)) return false;
  return e.scrollWidth > e.clientWidth + 1;
}).slice(0, 8).map(e => `${e.tagName.toLowerCase()}.${e.className} ${e.scrollWidth}>${e.clientWidth}: ${(e.getAttribute('data-h') || '')} ${e.innerText.slice(0, 40)}`)"""


def seed():
    """on a database of its own, what the views read on a copy of real data: CEHK posts of the last six days (news,
    forum and social, more than a page, some by 測試作者 and some on 李家超 and 施政報告) with scores, the profile's
    tags (sentiment, labels, theme), today's stream items, a persona context for the AI button, the site's config and
    search dictionary"""
    os.environ["DATABASE_URL"] = DB
    import corpus, edge_config, insights, searchtext
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=8)))
    kinds = (("News", "RTHK", "rthk_en"), ("Forum", "香港討論區", "rthk_en"), ("Social", "Facebook", "rthk_en"))
    items = []
    for i in range(1, 61):
        st, src, sid = kinds[i % 3]
        topic = ("李家超發表施政報告第{}項措施" if i % 2 else "特區政府公布房屋政策第{}輪安排").format(i)
        items.append({"url": f"https://dash-fixture.example/{st.lower()}/{i}", "title": topic,
                      "text": f"{topic}，市民反應不一。" * 3, "sid": sid, "source": src, "source_type": st,
                      "region": "HK", "author": "測試作者" if i % 4 == 0 else f"作者{i % 7}",
                      "published": (now - timedelta(hours=2 * i)).isoformat(timespec="minutes")})
    corpus.ingest(items)
    ids = corpus.ids_for_urls([x["url"] for x in items])
    db = corpus.db()
    at = now.isoformat(timespec="seconds")
    sents, themes = ("positive", "neutral", "negative"), ("施政", "房屋", "民生")
    tags, pairs = [], []
    for k, x in enumerate(items):
        pid = ids[x["url"]]
        db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES ('CEHK', %s, %s, "
                   "'about the Chief Executive', 'news', %s) ON CONFLICT DO NOTHING", (pid, 95 - k % 40, at))
        tags.append((pid, True, sents[k % 3], ["政策"] if k % 2 else ["房屋", "民生"]))
        pairs.append((pid, themes[k % 3]))
        if k < 10:
            it = {"url": x["url"], "title": x["title"], "source": x["source"], "stream": "CEHK",
                  "sentiment": sents[k % 3], "labels": [], "published": x["published"], "seen": at, "id": pid}
            db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                       (now.date().isoformat(), x["url"], corpus.J(it)))
    corpus.set_tags("CEHK", tags)
    corpus.set_themes("CEHK", pairs)
    read = [{"id": ids[x["url"]]} for x in items if x["author"] == "測試作者"]
    insights.make_context("persona", "CEHK", "測試作者", "測試作者", "測試作者的近期帖子", read, len(read))
    edge_config.publish()
    searchtext.publish_edge(force=True)


def today():
    return psql("SELECT to_char(now() AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD')")


def ctx_id():
    return psql("SELECT substr(k, 5) FROM kv WHERE k LIKE 'ctx:%' ORDER BY k LIMIT 1")


def post_in_list():
    """a CEHK post of the last 7 days, deep in the list (page 2 or later)"""
    rows = psql(f"""SELECT p.id FROM scores s JOIN posts p ON p.id = s.post_id WHERE s.stream = 'CEHK' AND s.score >= 50
                    AND p.day >= to_char(now() AT TIME ZONE 'Asia/Hong_Kong' - interval '6 days', 'YYYY-MM-DD')
                    ORDER BY coalesce(p.published, p.seen) DESC, p.id LIMIT 40""").split()
    return rows[-1]


class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        if OWN:
            seed()
        urllib.request.urlopen(BASE + "/dashboard?p=CEHK", timeout=60).read()  # warm the dev server
        # the keyword box needs the search dictionary the poller publishes (searchtext.publish_edge); a copy made
        # without it answers 伺服器沒有回應 to every query
        assert psql("SELECT count(*) FROM kv WHERE k = 'search:edge'") == "1", \
            f"no search dictionary in {DB}: DATABASE_URL={DB} python3 -c 'import searchtext; searchtext.publish_edge()'"
        # the `ai` flag on the beta host, in this test's own database copy (edge:config; the Worker reads it every 30 s)
        cls.flags_before = psql("SELECT v->'flags' FROM kv WHERE k = 'edge:config'")
        psql(f"""UPDATE kv SET v = jsonb_set(v, '{{flags,{BETA},ai}}', 'true') WHERE k = 'edge:config'""")
        cls.pw = sync_playwright().start()
        cls.browsers = {"webkit": cls.pw.webkit.launch(), "chromium": cls.pw.chromium.launch()}

    @classmethod
    def tearDownClass(cls):
        psql(f"UPDATE kv SET v = jsonb_set(v, '{{flags}}', '{cls.flags_before}'::jsonb) WHERE k = 'edge:config'")
        for b in cls.browsers.values():
            b.close()
        cls.pw.stop()

    def ctx(self, engine, extra_headers=None):
        if engine == "webkit":
            dev = dict(self.pw.devices["iPhone 13"])
            dev.update(viewport={"width": 390, "height": 844})
            c = self.browsers["webkit"].new_context(**dev, extra_http_headers=extra_headers or {})
        else:
            c = self.browsers["chromium"].new_context(viewport={"width": 1600, "height": 1000}, extra_http_headers=extra_headers or {})
        c.set_default_timeout(T)
        return c

    def open(self, c, path):
        page = c.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.errors = errors
        self.pages.append(page)
        page.goto(BASE + path, wait_until="networkidle", timeout=60000)
        # hydrated: the defaults written in (read location itself: WebKit's page.url misses a replaceState)
        page.wait_for_function("/[?&]v=\\w+&p=\\w+&from=/.test(location.search)", timeout=30000, polling=250)
        return page

    def setUp(self):
        self.pages = []

    def tearDown(self):  # on failure: what the pages showed
        if any(e for _, e in (self._outcome.result.errors + self._outcome.result.failures) if e):
            for i, pg in enumerate(self.pages):
                try:
                    pg.screenshot(path=os.path.join(SHOTS, f"fail-{self._testMethodName}-{i}.png"))
                    print(pg.url, pg.inner_text("body")[:400].replace("\n", " "), file=sys.stderr)
                except Exception:
                    pass

    def check(self, page, name, engine):
        page.wait_for_timeout(300)
        errs = [e for e in page.errors if "favicon" not in e and "/api/ai/estimate" not in e and "404" not in e]
        self.assertEqual(errs, [], f"{name} {engine}: console errors")
        sw = page.evaluate("document.scrollingElement.scrollWidth - innerWidth")
        self.assertLessEqual(sw, 0, f"{name} {engine}: sideways scroll {sw}px")
        self.assertEqual(page.evaluate(OVERFLOWING), [], f"{name} {engine}: text running out of its box")
        page.screenshot(path=os.path.join(SHOTS, f"{name}-{engine}.png"), full_page=False)

    def url(self, page):
        return page.evaluate("location.href")

    def q(self, page):
        from urllib.parse import parse_qsl, urlsplit
        return dict(parse_qsl(urlsplit(self.url(page)).query))

    def wait_q(self, page, js):
        """wait until the URL's search params satisfy js (p = URLSearchParams(location.search))"""
        page.wait_for_function(f"(p => {js})(new URLSearchParams(location.search))", timeout=T, polling=250)

    # ---- every view, both engines ----
    def test_views(self):
        day = today()
        for engine in ("webkit", "chromium"):
            c = self.ctx(engine)
            # 摘要 first: the defaults written into the URL in dashboard.html's order
            p = self.open(c, "/dashboard?p=CEHK")
            p.wait_for_selector("[data-figure='summary']")
            self.assertIn("Exposure & Mentioning Summary 曝光及提及摘要", p.inner_text("#view"))
            self.assertRegex(self.url(p), rf"/dashboard\?v=summary&p=CEHK&from=\d{{4}}-\d{{2}}-\d{{2}}&to={day}$")
            self.assertEqual(p.get_attribute(".tab[aria-current=true]", "title"), "Summary")
            self.check(p, "summary", engine)
            p.close()
            # 帖子
            p = self.open(c, "/dashboard?v=posts&p=CEHK")
            p.wait_for_selector(".post a.pl")
            self.assertIn("Post Listing 帖子列表", p.inner_text("#view"))
            self.assertRegex(self.url(p), rf"/dashboard\?v=posts&p=CEHK&from=\d{{4}}-\d{{2}}-\d{{2}}&to={day}$")
            self.assertEqual(p.get_attribute(".tab[aria-current=true]", "title"), "Post Listing")
            self.assertEqual(p.get_attribute("#quick [data-days='7']", "aria-pressed"), "true")
            self.assertIn(f"7 日，資料至 {day}", p.inner_text("#pnote"))
            href = p.get_attribute("a[href^='/api/export/posts.xlsx']", "href")
            self.assertIn("p=CEHK", href)
            self.check(p, "posts", engine)
            # a post's meta line with one unbreakable word longer than the screen (an outlet, board or author name;
            # tests/test_web_app_pages.py seeds such a post) wraps inside the card: no sideways scroll
            p.evaluate("""() => { const m = document.querySelector('.post .mt'); const s = document.createElement('span');
                                  s.textContent = 'Donaudampfschifffahrtsgesellschaftskapitänswitwe'.repeat(3); m.prepend(s); }""")
            self.assertLessEqual(p.evaluate("document.scrollingElement.scrollWidth - innerWidth"), 0, f"long meta word {engine}")
            p.evaluate("document.querySelector('.post .mt span').remove()")
            # expand a post in place (the chevron), the row itself links to the item page
            first = p.locator(".post").first
            first.locator("button.exp").click()
            self.assertEqual(first.get_attribute("aria-expanded"), "true")
            self.assertTrue(first.locator(".pbody .txt").is_visible())
            self.assertRegex(first.locator("a.pl").get_attribute("href"), r"^/item/\d+")
            # a sentiment chip: only its param changes, no new stream
            chip = p.locator("button.chip[data-f=sent]:not(.zero)").first
            val = chip.get_attribute("data-val")
            chip.click()
            self.wait_q(p, f"p.get('sent') === '{val}'")
            self.assertEqual(self.q(p)["p"], "CEHK")
            p.locator("button[data-clear]").click()
            p.wait_for_function("!location.search.includes('sent=')")
            # the text box (query.py's language): ?q=, the list narrowed
            p.fill("#qin", "李家超 AND 施政報告")
            p.click("#qform button[type=submit]")
            self.wait_q(p, "p.get('q') === '李家超 AND 施政報告'")
            p.wait_for_selector("#view:not(.loading) .post")
            self.assertRegex(p.inner_text("#view .sec header .sub"), r"共 \d+ 篇")
            p.locator("button[data-clear]").click()
            self.wait_q(p, "!p.get('q')")
            # 30 日: from/to only
            p.click("#quick [data-days='30']")
            p.wait_for_function("document.querySelector(\"#quick [data-days='30']\").getAttribute('aria-pressed') === 'true'")
            self.assertEqual(self.q(p)["to"], day)
            self.check(p, "posts-30d", engine)

            # 熱門話題 → a topic → ‹ 熱門話題 (the browser's Back) → the list
            p.click(".tab[title='Hot Topic']")
            p.wait_for_selector("tr.go")
            self.check(p, "hot", engine)
            list_url = self.url(p)
            p.locator("tr.go").first.click()
            self.wait_q(p, "/^\\d+$/.test(p.get('tid') || '')")
            p.wait_for_selector("#c-topic svg")
            self.assertIn("Trend 趨勢", p.inner_text("#view"))
            self.check(p, "topic", engine)
            p.click("button[data-back]")
            p.wait_for_function(f"location.href === {json.dumps(list_url)}", timeout=T, polling=250)
            p.wait_for_selector("tr.go")
            # 主題
            p.click(".tab[title='Themes']")
            p.wait_for_selector("#view .sec h2:has-text('Themes AI 主題')")
            self.check(p, "themes", engine)
            # 人物 without an author: the name box only
            p.click(".tab[title='Persona']")
            p.wait_for_selector("#pform")
            self.assertIn("輸入作者名稱", p.inner_text("#view"))
            self.check(p, "persona-empty", engine)
            c.close()

    def test_persona_and_back(self):
        for engine in ("webkit", "chromium"):
            c = self.ctx(engine)
            p = self.open(c, "/dashboard?v=posts&p=CEHK")
            p.wait_for_selector(".post")
            posts_url = self.url(p)
            # 作者分析 from an expanded post → 人物 (a new history entry); Back returns to the list
            btn = None
            for i in range(min(12, p.locator(".post").count())):  # the first post with an author
                post = p.locator(".post").nth(i)
                post.locator("button.exp").click()
                post.locator(".pbody").wait_for()
                if post.locator("button[data-persona]").count():
                    btn = post.locator("button[data-persona]")
                    break
            if btn is None:
                c.close()
                self.skipTest("no post with an author")
            author = btn.get_attribute("data-persona")
            btn.click()
            self.wait_q(p, "p.get('v') === 'persona' && !!p.get('a')")
            p.wait_for_selector("#view h2:has-text('Persona 人物分析')")
            p.wait_for_selector(f"#view h2:text-is('{author}')") if "'" not in author else None
            self.assertIn("近期帖子", p.inner_text("#view"))
            self.check(p, "persona", engine)
            # an author name that is one word longer than the screen (tests/test_web_app_pages.py seeds one) wraps
            p.evaluate("""() => { const h = [...document.querySelectorAll('#view h2')].find(h => h.style.fontSize); h.dataset.was = h.textContent;
                                  h.textContent = 'Donaudampfschifffahrtsgesellschaftskapitänswitwe'.repeat(3); }""")
            self.assertLessEqual(p.evaluate("document.scrollingElement.scrollWidth - innerWidth"), 0, f"long author {engine}")
            p.evaluate("() => { const h = [...document.querySelectorAll('#view h2')].find(h => h.style.fontSize); h.textContent = h.dataset.was; }")
            p.go_back()
            p.wait_for_function(f"location.href === {json.dumps(posts_url)}", timeout=T, polling=250)
            p.wait_for_selector(".post")
            c.close()

    def test_old_views_and_post(self):
        for engine in ("webkit", "chromium"):
            c = self.ctx(engine)
            for v, title in (("summary", "Exposure & Mentioning Trend 曝光及提及趨勢"), ("authors", "Top Author 作者排行"),
                             ("sources", "Top Source 來源排行"), ("trend", "Trend 趨勢")):  # back, each with its 「?」
                p = self.open(c, f"/dashboard?v={v}&p=CEHK")
                p.wait_for_selector("#view [data-bias-note]")
                self.assertIn(title, p.inner_text("#view"))
                self.assertEqual(self.q(p)["v"], v)
                self.check(p, v, engine)
                p.close()
            # ?post= (the item page's 在數據分析開啟): the page holding it, expanded, marked, ?page= written in
            pid = post_in_list()
            day = today()
            p = self.open(c, f"/dashboard?v=posts&p=CEHK&post={pid}")
            p.wait_for_selector(f".post.here[data-id='{pid}'][aria-expanded=true]")
            p.wait_for_function("new URLSearchParams(location.search).get('page')")
            self.assertEqual(self.q(p)["post"], pid)
            self.check(p, "post-here", engine)
            # a stream switch drops tid/page/post (data-pick-drop) and keeps the view and period
            other = p.locator(".yyc-streams a[data-p]:not([aria-current])").first
            if other.is_visible():
                other.click()
                p.wait_for_function("!location.search.includes('post=')")
                q = self.q(p)
                self.assertNotIn("page", q)
                self.assertEqual(q["v"], "posts")
            c.close()

    def test_ai(self):
        """the persona's AI button with the flag on (beta host) for an approved account (mocked /app/me, estimate, run)"""
        cid = ctx_id()
        for engine in ("webkit", "chromium"):
            c = self.ctx(engine, {"X-Forwarded-Host": BETA})
            c.route("**/app/me", lambda r: r.fulfill(status=200, content_type="application/json", body='{"email":"t@example.com"}'))
            c.route("**/api/ai/estimate?*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                               body='{"model_name":"Test Model","usd":0.0123,"input_tokens":100,"output_tokens":50}'))
            lines = [{"t": "delta", "text": "## 重點\n"}, {"t": "delta", "text": "- 第一點 **粗體**\n"},
                     {"t": "done", "markdown": "x", "html": "<p>完成的摘要</p>", "at": "2026-10-15T10:00:00+08:00",
                      "context": cid, "note": "AI 讀了測試帖子", "usage": {"model_name": "Test Model", "usd": 0.0042}}]
            c.route("**/app/ai/persona", lambda r: r.fulfill(status=200, content_type="application/x-ndjson",
                                                            body="".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines)))
            deadline = time.time() + 45  # the Worker reads edge:config every 30 s
            while True:
                p = self.open(c, "/dashboard?v=persona&p=CEHK&a=測試作者")
                if p.locator("button[data-ai=persona]").count() or time.time() > deadline:
                    break
                p.close()
                time.sleep(3)
            p.wait_for_selector("button[data-ai=persona]")
            p.wait_for_selector("[data-est=persona]:has-text('預計 USD 0.0123')")
            p.click("button[data-ai=persona]")
            p.wait_for_selector("#ai-out:has-text('完成的摘要')")
            self.assertIn("實際 USD 0.0042", p.inner_text("#ai-out"))
            p.click("#ai-out button.read")
            p.wait_for_selector("#ai-out .ctx-list .post")
            self.assertEqual(p.locator("#ai-out .ctx a.btn").count(), 0)  # no 在對話中追問: the AI 助理 is gone
            self.check(p, "ai", engine)
            p.goto(f"{BASE}/dashboard?v=posts&p=CEHK")  # the listings have no AI button
            p.wait_for_selector(".post")
            self.assertEqual(p.locator("button[data-ai]").count(), 0)
            c.close()
        # not approved: the button is off and says why, with the login link back to this page
        c = self.ctx("chromium", {"X-Forwarded-Host": BETA})
        p = self.open(c, "/dashboard?v=persona&p=CEHK&a=測試作者")
        p.wait_for_selector("button.btn.ai[disabled]")
        self.assertIn("/app/login?next=%2Fdashboard%3F", p.get_attribute(".ai-note a", "href"))
        c.close()

    def test_exports(self):
        import io
        from openpyxl import load_workbook
        for kind in ("summary", "posts", "hot_topics", "authors", "sources", "trend", "themes"):
            r = urllib.request.urlopen(f"{BASE}/api/export/{kind}.xlsx?p=CEHK&from=2026-10-09&to=2026-10-15", timeout=120)
            self.assertEqual(r.headers["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            name = f"CEHK_{kind}_2026-10-09_2026-10-15.xlsx"
            self.assertEqual(r.headers["Content-Disposition"], f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}")
            data = r.read()
            self.assertEqual(data[:2], b"PK")
            if kind in ("summary", "authors", "sources", "trend"):  # the selection-bias note in the header rows
                ws = load_workbook(io.BytesIO(data)).worksheets[0]
                notes = [row[1] for row in ws.iter_rows(values_only=True) if row and row[0] == "Note"]
                self.assertTrue(notes and "這些數字取決於我們追蹤哪些來源和關鍵字" in notes[0], kind)


if __name__ == "__main__":
    unittest.main(verbosity=2)
