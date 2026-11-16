#!/usr/bin/env python3
"""A post whose own title does not say what it is about (Deyao, 2026-10-20: RTHK's YouTube 「1100 新聞簡報」, true
but meaningless; the rule: its title differs too much from its content) gets a title from AI as its headline, with
the 「AI 標題」 badge, and its own title is kept and shown as the source's title (來源標題). The judgement and the title
come from the model that reads the post (stage-2 scoring); titles.store(p, title, "retitled", model) files it. On a
throwaway database (TEST_DATABASE_URL), no model called.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_retitled.py

Covers:
  - titles.store(..., "retitled", model): ai_title + kind + the given model; the collected title and text untouched.
  - display (titles.display, item.view, the edge app's item.view and 串流 feed): title = the AI title, title_by_ai,
    title_retitled, original_title = the source's own title, text = the post's text.
  - Telegram / exports (titles.plain): 〔AI標題〕 before it, as for any AI-written title.
  - the page: the badge's hover names the source's title; the item page shows 來源標題：<the source's title>.
"""
import json, os, subprocess, sys, tempfile, unittest
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, edge_config, item, titles  # noqa: E402
from edge_call import edge, APP  # noqa: E402

HKT = app.HKT
NOW = datetime.now(HKT)
URL = "https://www.youtube.com/watch?v=rthk1100test"
SOURCE_TITLE = "1100 新聞簡報"
AI = "亞太經合組織財長會議在港舉行；李家超感謝國家肯定香港金融中心地位"
OPUS = "anthropic/claude-opus-5.5"


def row(url):
    return dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone())


def render(module, expr):
    """server-render a React element from web-app/src/<module> (expr uses `m` and `h` = React.createElement) → HTML"""
    with tempfile.NamedTemporaryFile("w", suffix=".ts", dir=os.path.join(APP, "scripts"), delete=False) as f:
        f.write(f"""import * as React from "react";
import {{ renderToStaticMarkup }} from "react-dom/server";
import * as m from "../src/{module}";
const h = React.createElement;
process.stdout.write(renderToStaticMarkup({expr}));
""")
    try:
        r = subprocess.run(["npx", "tsx", f.name], cwd=APP, capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(f.name)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return r.stdout


class Retitled(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.ingest([{"sid": "rthk_youtube", "source": "RTHK 香港電台 (YouTube)", "section": "YouTube", "title": SOURCE_TITLE,
                        "url": URL, "published": NOW.isoformat(timespec="seconds"), "text": "#香港電台 #RTHK #新聞簡報"}])
        cls.p = row(URL)
        titles.store(cls.p, AI, "retitled", model=OPUS)
        edge_config.publish()  # the edge app's item.view reads the site's config

    def test_stored(self):
        r = row(URL)
        self.assertEqual((r["ai_title"], r["ai_title_kind"], r["ai_title_model"]), (AI, "retitled", OPUS))
        self.assertTrue(r["ai_title_at"])
        self.assertEqual((r["title"], r["text"]), (SOURCE_TITLE, "#香港電台 #RTHK #新聞簡報"))  # as collected
        self.assertIn("retitled", titles.KINDS)

    def test_display(self):
        d = titles.display(row(URL))
        self.assertEqual((d["title"], d["original_title"]), (AI, SOURCE_TITLE))
        self.assertTrue(d["title_by_ai"])
        self.assertTrue(d["title_retitled"])
        self.assertFalse(d["title_extracted"] or d["title_linked"])
        self.assertEqual(d["text"], "#香港電台 #RTHK #新聞簡報")
        self.assertEqual(titles.plain(d), titles.MARK + AI)
        # a post with no title of its own is not "retitled": nothing to show as the source's title
        self.assertFalse(titles.shown("某標題", "written", "某帖子的開頭…", "某帖子的開頭全文")["title_retitled"])

    def test_item_view(self):
        pid = self.p["id"]
        for v in (item.view(pid), edge("item.view", pid)):
            self.assertEqual((v["title"], v["original_title"]), (AI, SOURCE_TITLE))
            self.assertTrue(v["title_by_ai"])
            self.assertTrue(v["title_retitled"])
            self.assertEqual(v["ai_title_model"], OPUS)

    def test_stream_item(self):
        it = titles.item_display({"title": SOURCE_TITLE, "text": "#香港電台", "url": URL}, AI, "retitled", OPUS, NOW.isoformat(),
                                 SOURCE_TITLE, "#香港電台 #RTHK #新聞簡報")
        self.assertTrue(it["title_retitled"])
        self.assertEqual(it["original_title"], SOURCE_TITLE)

    def test_page(self):
        x = json.dumps({"title": AI, "title_by_ai": True, "title_retitled": True, "original_title": SOURCE_TITLE,
                        "ai_title_model_name": "Claude Opus 5.5", "ai_title_at": None}, ensure_ascii=False)
        badge = render("lib/AiBadge.tsx", f"h(m.AiBadge, {{ x: {x} }})")
        self.assertIn("AI 標題", badge)
        self.assertIn(SOURCE_TITLE, badge)  # the hover names the source's own title
        self.assertNotIn("沒有自己的標題", badge)
        src = render("lib/AiBadge.tsx", f"h(m.SourceTitle, {{ x: {x} }})")
        self.assertIn("來源標題", src)
        self.assertIn(SOURCE_TITLE, src)
        # a written title (no title of its own): no source title line
        w = json.dumps({"title": AI, "title_by_ai": True, "original_title": "某帖子的開頭…"}, ensure_ascii=False)
        self.assertEqual(render("lib/AiBadge.tsx", f"h(m.SourceTitle, {{ x: {w} }})"), "")
        with open(os.path.join(APP, "src/routes/-ItemPage.tsx")) as f:
            self.assertIn("<SourceTitle x={D} />", f.read())  # under the item page's headline


if __name__ == "__main__":
    unittest.main(verbosity=2)
