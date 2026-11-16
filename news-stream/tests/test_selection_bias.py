#!/usr/bin/env python3
"""Every figure that depends on YYC's own selection carries the selection-bias note (Deyao, 2026-10-16: "restore the
data that is susceptible to selection bias, because that's still valuable, but the question mark on hover should say
this is susceptible to selection bias (in a short, layman way)"; README "Figures that depend on our selection").

Those figures count posts one per vote, so they depend on which sources YYC watches, the search terms it sends, feeds
per outlet, polling and which comments it fetches: Smart Hot Terms, the daily scores SPI/CSS/DVS/DPS/CCS, the 摘要
metrics and Exposure & Mentioning Trend, 趨勢, the 作者 and 來源 rankings, a topic's Top Author/Source, the most
active authors, profile-wide sentiment distributions (帖子's 情緒分佈, 串流's sentiment bars and 情緒/標籤/主要來源
column). On the pages the note appears ONCE per view (Deyao, 2026-10-20: "too many question marks"): one quiet line at
the top of each 數據分析 view that shows such figures (lib/BiasNote.tsx), and one at the foot of 串流's 摘要 column; no
figure carries its own 「?」, and 摘要's metric and score definitions sit in that line's single 「指標說明」 toggle. The
exports carry it as a header line, the daily reports as a footnote, the data API's and the MCP server's tools in
their descriptions and their answers.

Without a server this checks the code (no database or network needed):

    python3 tests/test_selection_bias.py

With a running dev server of the edge app on a copy of real data (the yyc-news-stream skill), it also opens every
page that shows such a figure in WebKit (iPhone, 390 px) and Chromium (1600 px) and checks the note is there once:

    WEB_APP_URL=http://127.0.0.1:5185 python3 tests/test_selection_bias.py
"""
import os, re, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("DATA_DIR", "/tmp/yyc-test-selection-bias")
import insights, reports, tools  # noqa: E402

NOTE = "這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。"
NOTE_EN = ("These figures depend on which sources and keywords we follow, so they may not reflect what is actually "
           "happening (selection bias).")

ENDPOINTS = ("summary", "scores", "authors", "sources", "trend", "terms")
EXPORTS = ("summary", "trend", "authors", "sources")
TOOLS = ("stats_overview", "authors_top", "sources_top", "trend")
MCP_TOOLS = ("analysis_overview", "analysis_authors", "analysis_sources", "analysis_trend", "analysis_terms")
FUNCS = ("hot_terms", "scores", "spi_series", "metrics", "trend", "top_authors", "top_sources", "summary_view",
         "day_counts", "METHOD")
VIEWS = ["summary", "posts", "hot", "authors", "sources", "trend", "themes", "persona"]
# every restored figure on 數據分析: its section title (as the page writes it); the view it is in carries one <BiasNote />
DASH_FIGURES = ("Exposure &amp; Mentioning Trend 曝光及提及趨勢", "Exposure &amp; Mentioning Summary 曝光及提及摘要",
                "每日分數", "Smart Hot Terms 熱門詞", "情緒分佈", "Top Author 作者排行", "Top Source 來源排行",
                "Trend 趨勢", "Posts, Topics &amp; Authors 帖子、話題及作者", "View Count 瀏覽數", "數據表",
                "Top Source 來源", "Top Author 作者", "期內最活躍作者", "Hot Terms 熱門詞")
STREAM_FIGURES = ("情緒", "標籤", "主要來源")
# the view components of 數據分析 that show such figures: each has exactly one <BiasNote />; 主題 has none
NOTE_VIEWS = ("SummaryView", "PostsView", "HotView", "TopicView", "AuthorsView", "SourcesView", "TrendView",
              "PersonaEmpty", "PersonaView")
NO_NOTE_VIEWS = ("ThemesView",)

DASH = ("web-app", "src", "server", "dashboard.ts")
EXPORT = ("web-app", "src", "server", "dashboard-export.ts")
PAGE = ("web-app", "src", "routes", "-DashboardPage.tsx")
STREAM = ("web-app", "src", "routes", "-StreamPage.tsx")
BIAS_TS = ("web-app", "src", "lib", "bias.ts")
BIAS_NOTE_TSX = ("web-app", "src", "lib", "BiasNote.tsx")


def src(*parts):
    return open(os.path.join(HERE, *parts), encoding="utf-8").read()


def fn_body(text, name):
    """a top-level `function name(` in a .tsx file, up to the next top-level declaration"""
    m = re.search(rf"^function {name}\(.*?(?=^(?:function |const |let |export |// -{{4}})|\Z)", text, re.S | re.M)
    return m.group(0) if m else None


def ts_const(text, name):
    m = re.search(rf'export const {name} =\s*"([^"]*)"', text)
    return m.group(1) if m else None


class Note(unittest.TestCase):
    def test_one_wording_everywhere(self):
        self.assertEqual(insights.BIAS_NOTE, NOTE)
        self.assertEqual(insights.BIAS_NOTE_EN, NOTE_EN)
        self.assertEqual(ts_const(src(*BIAS_TS), "BIAS_NOTE"), NOTE)
        self.assertEqual(ts_const(src(*BIAS_TS), "BIAS_NOTE_EN"), NOTE_EN)


class Python(unittest.TestCase):
    def test_analytics_functions(self):
        for f in FUNCS + ("select", "topics", "hot_topics", "persona"):
            self.assertTrue(hasattr(insights, f), f)
        self.assertIn('"hot_terms"', src("insights.py"))  # persona's hot terms

    def test_agent_tools_say_it(self):
        by = {t.name: t for t in tools.tools()}
        for name in TOOLS:
            with self.subTest(tool=name):
                self.assertIn(name, by)
                self.assertIn(NOTE_EN, by[name].description)
        code = src("tools", "data.py")
        for name in TOOLS:  # each answer carries the note, so an AI answer built on it can say so
            body = re.search(rf"def {name}\(a, ctx\):(.*?)(?=\n@tool|\Z)", code, re.S).group(1)
            self.assertIn('"note": insights.BIAS_NOTE_EN', body, name)

    def test_skills(self):
        self.assertTrue(os.path.exists(os.path.join(HERE, "skills", "authors-channels", "SKILL.md")))
        for d in ("authors-channels", "daily-brief", "exec-summary", "compare-subjects", "risk-complaints"):
            text = src("skills", d, "SKILL.md")
            self.assertTrue(any(t in text for t in TOOLS), d)
            self.assertIn(NOTE, text, d)


class Reports(unittest.TestCase):
    P = {"id": "CEHK", "name": "CEHK", "title": "特首及政府"}
    POSTS = [{"id": i, "title": f"特首出席活動 {i}", "url": f"https://news.test/{i}", "website": "政府新聞網",
              "source_type": "News", "published": "2026-10-13T10:00", "sentiment": "neutral", "labels": [],
              "author": "", "day": "2026-10-13", "board": None} for i in (1, 2)]
    SUMM = {"heading": "要點", "points": [{"topic": "施政", "text": "特首出席活動"}], "action": "留意"}
    SC = {k: {"value": 55.0, "level": "Medium", "change_pct": 10.0} for k in ("SPI", "CSS", "DVS", "DPS", "CCS")}

    def d(self):
        tops = insights.topics(self.POSTS)
        m = insights.metrics(self.POSTS, "2026-10-13", "2026-10-13", set(), total=(1, 2))
        return {"p": self.P, "day": "2026-10-13", "posts": self.POSTS, "topics": tops, "metrics": m, "wmetrics": m,
                "scores": {k: dict(v) for k, v in self.SC.items()}, "hot": [], "authors": [], "sites": [],
                "series": [{"day": "2026-10-13", "SPI": 55.0, "CSS": 50.0}],
                "window": ("2026-10-13T09:00", "2026-10-14T09:00"), "wposts": self.POSTS, "wtopics": tops}

    def test_reports_show_the_scores_with_the_footnote(self):
        d = self.d()
        for name, html in (("format1", reports.format1(d, self.SUMM)), ("format2", reports.format2(d, self.SUMM)),
                           ("pdf", reports.pdf_html(d, self.SUMM))):
            with self.subTest(report=name):
                self.assertIn("社交熱度指數", html)
                self.assertIn(NOTE, html)
                self.assertIn("特首出席活動", html)  # the AI summary is still there
        for back in ("def charts", "def interpretation", "spi.png", "top_authors", "top_sources", '"note": insights.BIAS_NOTE'):
            self.assertIn(back, src("reports.py"))


class Edge(unittest.TestCase):
    def test_endpoints_and_exports(self):
        table = re.search(r"export const ENDPOINTS = \{(.*?)\} as const", src(*DASH), re.S).group(1)
        names = re.findall(r'"?([\w/]+)"?:\s*\w+Api', table)
        for name in ENDPOINTS:
            self.assertIn(name, names)
        kinds = re.findall(r'"(\w+)"', re.search(r"export const EXPORTS = \[(.*?)\]", src(*EXPORT)).group(1))
        for kind in EXPORTS:
            self.assertIn(kind, kinds)
        self.assertIn("BIAS_NOTE", src(*EXPORT))  # the header line of those exports
        self.assertIn("note: BIAS_NOTE", src(*DASH))  # the endpoints' answers (the MCP server reads them too)

    def test_note_component(self):
        code = src(*BIAS_NOTE_TSX)
        self.assertIn("BIAS_NOTE", code)
        self.assertIn("data-bias-note", code)
        self.assertIn("指標說明", code)  # the one toggle that holds a view's definitions
        self.assertFalse(os.path.exists(os.path.join(HERE, "web-app", "src", "lib", "BiasQ.tsx")))  # no per-figure 「?」

    def test_dashboard_note_once_per_view(self):
        page = src(*PAGE)
        views = re.search(r"const VIEWS = (\[.*?\]) as const;", page, re.S).group(1)
        self.assertEqual(re.findall(r'\["(\w+)"', views), VIEWS)
        self.assertFalse("BiasQ" in page, "a figure still has its own 「?」")
        for title in DASH_FIGURES:  # the restored figures are all still there
            self.assertTrue(f">{title.replace('&amp;', '&')}<" in page or f">{title}<" in page or f'"{title}"' in page, title)
        for name in NOTE_VIEWS:
            with self.subTest(view=name):
                body = fn_body(page, name)
                self.assertTrue(body, name)
                self.assertEqual(body.count("<BiasNote"), 1, f"{name}: the note once")
        for name in NO_NOTE_VIEWS:
            self.assertEqual(fn_body(page, name).count("<BiasNote"), 0, name)
        for name in ("Mrow", "ScoreCard"):  # a metric row or score card has no 「?」 of its own
            body = fn_body(page, name)
            self.assertNotIn("note", body.split(")", 1)[0], name)
            self.assertNotIn("<Def", body, name)
        summ = fn_body(page, "SummaryView")
        self.assertIn("<BiasNote defs={", summ)  # 摘要's definitions go in the note's one toggle
        for k in ("posts", "new_posts", "topics", "new_topics", "websites", "boards", "authors", "view_count",
                  "website_coverage", "posts_spread", "per_day", "sentiment_balance"):
            self.assertIn(f"NOTE.{k}", summ, k)
        self.assertIn("M.sentiment_index", summ)
        self.assertIn('["SPI", "CSS", "DVS", "DPS", "CCS"]', summ)

    def test_stream_page_panels(self):
        page = src(*STREAM)
        self.assertIn("<SplitBar", page)
        self.assertFalse("BiasQ" in page, "串流 still has a 「?」")
        for title in STREAM_FIGURES:
            self.assertTrue([l for l in page.splitlines() if f"<h2>{title}" in l], title)
        self.assertEqual(fn_body(page, "Summary").count("<BiasNote"), 1)  # one note for the 摘要 column
        self.assertEqual(page.count("<BiasNote"), 1)
        self.assertIn("SENTS", src("web-app", "src", "server", "feed.ts"))  # each stream's sentiment counts

    def test_mcp_tools(self):
        code = src("mcp-edge", "src", "tools.ts")
        for name in MCP_TOOLS:
            with self.subTest(tool=name):
                m = re.search(rf'\{{ name: "{name}",(.*?)\n  \}}', code, re.S)
                self.assertTrue(m, name)
                self.assertIn("BIAS_NOTE_EN", m.group(1))


@unittest.skipUnless(os.environ.get("WEB_APP_URL"), "WEB_APP_URL: a running dev server of the edge app")
class Pages(unittest.TestCase):
    """each view shows the note once, as one line (no 「?」 on any figure); 摘要's 指標說明 opens the definitions"""
    BASE = os.environ.get("WEB_APP_URL", "").rstrip("/")
    URLS = ["/dashboard?v=summary&p=CEHK", "/dashboard?v=posts&p=CEHK", "/dashboard?v=hot&p=CEHK",
            "/dashboard?v=authors&p=CEHK", "/dashboard?v=sources&p=CEHK", "/dashboard?v=trend&p=CEHK",
            "/dashboard?v=persona&p=CEHK", "/?p=CEHK"]
    DESKTOP_ONLY = ("/?p=CEHK",)  # 串流's 摘要 column shows from 960 px (the phone has the picker)
    T = 90000  # ms; every wait has a timeout (a dev server on a local copy reads the period's posts cold)

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.webkit, cls.chromium = cls.pw.webkit.launch(), cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.webkit.close()
        cls.chromium.close()
        cls.pw.stop()

    def ctx(self, phone):
        if phone:
            dev = dict(self.pw.devices["iPhone 13"])
            dev.update(viewport={"width": 390, "height": 844})
            c = self.webkit.new_context(**dev)
        else:
            c = self.chromium.new_context(viewport={"width": 1600, "height": 1000})
        c.set_default_timeout(self.T)
        return c

    def test_each_view_says_the_note_once(self):
        for phone in (False, True):
            c = self.ctx(phone)
            try:
                for url in self.URLS:
                    if phone and url in self.DESKTOP_ONLY:
                        continue
                    p = c.new_page()
                    p.goto(self.BASE + url, timeout=self.T)
                    with self.subTest(url=url, phone=phone):
                        p.wait_for_selector("[data-figure]", state="attached", timeout=self.T)
                        p.wait_for_load_state("networkidle", timeout=self.T)
                        notes = [n for n in p.query_selector_all("[data-bias-note]") if n.is_visible()]
                        self.assertEqual(len(notes), 1, f"{url}: {len(notes)} notes shown")
                        self.assertIn(NOTE, notes[0].inner_text())
                        self.assertEqual(p.query_selector_all(".bias-q"), [], url)
                        if "v=summary" in url:  # the definitions: one toggle, closed until asked
                            dd = notes[0].query_selector("dl")
                            self.assertFalse(dd.is_visible())
                            notes[0].query_selector("summary").click()
                            dd.wait_for_element_state("visible", timeout=self.T)
                            self.assertIn("期內符合設定的帖子數。", dd.inner_text())
                            self.assertIn("SPI", dd.inner_text())
                        self.assertEqual(p.evaluate("document.documentElement.scrollWidth <= innerWidth"), True, url)
                    p.close()
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
