#!/usr/bin/env python3
"""Apostrophes and quotation marks of English posts (Deyao, 2026-10-21: "Apostrophe and quotation mark for English
sources are wrong"). On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_quotes.py

Covers each cause found in the stored rows and pages:
  - the site's font: Noto Sans HK draws ‘ ’ “ ” full-width, so "Netflix’s" showed as "Netflix’ s" (Dimsum Daily post
    6796016). Latin-script text is marked lang="en" (web-app lib/lang.tsx langOf/Txt, run here in Node) and under
    :lang(en) the marks come from the Noto Sans subset __root.tsx loads: measured in Chromium with the page's own font
    links and CSS rule, the apostrophe is narrow in an English title and stays full-width in a Chinese one; the pages
    render post titles and texts through it.
  - the reports and alert mails (reports.el): English titles in a Latin font, Chinese ones as before; Word's Latin
    font is Arial.
  - HTML entities from the collectors (X's S&amp;P, Bloomberg's M&amp;amp;A, SCMP photos' China&rsquo;s, the Macau
    CAB's &ldquo;…&rdquo;, The Beat's &amp;#8217;) and HTML stored as text (Blogspot): corpus.plain on every stored
    title and text (ingest, set_content, set_text, set_transcript, titles.store), so the site shows ’ and Telegram /
    WhatsApp get S&P (Telegram escaped once). "&notes", "AT&T", "P<0.001" and Chinese text stay as written.
  - the AI-title prompts asked for 「」 in every title: CGTN's "face wash" came back as 「face wash」 (post 5957696). The
    prompts say nothing about quotation marks (one general instruction; Deyao, 2026-10-23): titles.clean turns 「」
    into “” in a non-Chinese written title and writes straight double quotes as “” in English, 「」 in Chinese; a
    Chinese title keeps 「」.
  - the repair (tools/fix_entities.py): wrong rows fixed, right rows untouched, scored.hash / ai_title_hash follow
    (no rescoring), stream items' copies fixed, resumable from kv, every change logged.
"""
import json, os, re, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, reports, streams, telegram_alerts, titles, whatsapp_alerts  # noqa: E402
import fix_entities  # noqa: E402

WEB = os.path.join(HERE, "web-app")


def post(url, title, text=None, sid="dimsumdaily", source="Dimsum Daily"):
    corpus.ingest([{"url": url, "title": title, "text": text, "sid": sid, "source": source,
                    "published": "2026-10-21T11:15:00+08:00"}])
    return corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()


class Plain(unittest.TestCase):
    def test_entities(self):
        for raw, want in [
            ("Markets mixed as Nasdaq and S&amp;P 500 slip", "Markets mixed as Nasdaq and S&P 500 slip"),
            ("Nordic Firms More Than Double M&amp;amp;A", "Nordic Firms More Than Double M&A"),
            ("What&&#35;8217;s On HKG: LINK Hong Kong Open, &amp; More", "What’s On HKG: LINK Hong Kong Open, & More"),
            ("Kwun Tong&rsquo;s 200-year-old Cha Kwo Ling", "Kwun Tong’s 200-year-old Cha Kwo Ling"),
            ("more than 40 &ldquo;10-Minute Reading Corners&rdquo;", "more than 40 “10-Minute Reading Corners”"),
            ("It&#39;s &quot;fine&quot; &#x2014; really", "It's \"fine\" — really"),
            ("文化局於社區設置逾40個&ldquo;10分鐘閱讀角&rdquo;", "文化局於社區設置逾40個“10分鐘閱讀角”"),
        ]:
            self.assertEqual(corpus.plain(raw), want)

    def test_left_as_written(self):
        for s in ["AT&T and Q&A", "Ben&notes", "P<0.001 and x > 3", "a <b>bold</b> word", "李家超「超級聯繫人」",
                  "Netflix’s “AI Gone Rogue”", None, ""]:
            self.assertEqual(corpus.plain(s), s)

    def test_html_text(self):
        raw = ('<p>&nbsp;</p><div class="separator" style="clear: both;"><a href="https://x/y.jpg">'
               '<img src="https://x/y.jpg&amp;w=1000" /></a></div><p>Malaysia&#39;s &quot;new&quot; plan<br />'
               'second line</p><p>&nbsp;</p>')
        self.assertEqual(corpus.plain(raw), 'Malaysia\'s "new" plan\nsecond line')


class Stored(unittest.TestCase):
    def test_ingest_and_alerts(self):
        r = post("https://x.com/dimsumdaily/status/1", "Markets mixed as Nasdaq and S&amp;P 500 slip",
                 "Markets mixed as Nasdaq and S&amp;P 500 slip while Dow&#8217;s up", sid="watch_x", source="Dimsum Daily (X)")
        self.assertTrue(r["title"].startswith("Markets mixed as Nasdaq and S&P 500 slip"), r["title"])  # X: its excerpt
        self.assertEqual(r["text"], "Markets mixed as Nasdaq and S&P 500 slip while Dow’s up")
        it = {"title": r["title"], "url": r["url"], "website": "X", "post_id": r["id"]}
        tg = telegram_alerts.text(it)
        self.assertIn("S&amp;P 500", tg)  # HTML parse mode: one escape, Telegram shows S&P
        self.assertNotIn("&amp;amp;", tg)
        self.assertIn("S&P 500", whatsapp_alerts.text(it))

    def test_set_text_and_content(self):
        r = post("https://www.scmp.com/photos/1", "Hong Kong&rsquo;s week in pictures", sid="scmp_photos", source="南華早報 SCMP")
        self.assertEqual(r["title"], "Hong Kong’s week in pictures")
        corpus.set_text(r["id"], "China&rsquo;s World Robot Conference opened")
        corpus.set_content(r["id"], "China&rsquo;s robots", "A &ldquo;robot&rdquo; show")
        x = corpus.db().execute("SELECT title, text FROM posts WHERE id=%s", (r["id"],)).fetchone()
        self.assertEqual((x["title"], x["text"]), ("China’s robots", "A “robot” show"))

    def test_ai_title_stored_plain(self):
        r = post("https://www.bloomberg.com/news/1", "AT&amp;T's Exit", sid="bloomberg", source="Bloomberg")
        self.assertEqual(r["title"], "AT&T's Exit")
        titles.store(dict(r), "AT&amp;T&#8217;s exit is a dropped signal", "written", "m")
        self.assertEqual(corpus.db().execute("SELECT ai_title FROM posts WHERE id=%s", (r["id"],)).fetchone()[0],
                         "AT&T’s exit is a dropped signal")


class AiTitles(unittest.TestCase):
    def test_prompts(self):
        for p in (titles.PROMPT, streams.TITLE):  # typography is code's (clean)
            self.assertNotIn("quote", p)
            for mark in ("「", "“", "‘"):
                self.assertNotIn(mark, p)

    def test_clean(self):
        self.assertEqual(titles.clean("Sunflowers line up for a 「face wash」", ""), "Sunflowers line up for a “face wash”")
        self.assertEqual(titles.clean("Director hopes his film 「Another World」 and 『it』 help", ""),
                         "Director hopes his film “Another World” and ‘it’ help")
        self.assertEqual(titles.clean("李家超：彰顯「超級聯繫人」角色", ""), "李家超：彰顯「超級聯繫人」角色")
        self.assertEqual(titles.clean('Lee says the city\'s "super-connector" role grows', ""),
                         "Lee says the city's “super-connector” role grows")
        self.assertEqual(titles.clean('李家超：彰顯"超級聯繫人"角色', ""), "李家超：彰顯「超級聯繫人」角色")
        self.assertEqual(titles.latin_quotes("「非常階段をペンライトで」避難"), "「非常階段をペンライトで」避難")


class Reports(unittest.TestCase):
    def test_latin_font(self):
        self.assertIn('lang="en"', reports.el("Netflix’s documentary"))
        self.assertIn("Arial", reports.el("Netflix’s documentary"))
        self.assertEqual(reports.el("李家超：彰顯「超級聯繫人」角色"), "李家超：彰顯「超級聯繫人」角色")
        self.assertIn('lang="en"', reports.el("(負面) [X][S&P slips] https://x", "S&P slips"))
        with open("reports.py") as f:
            src = f.read()
        self.assertIn('st.font.name = "Arial"', src)


class Repair(unittest.TestCase):
    def test_fix_entities(self):
        c = corpus.db()
        bad = post("https://www.bloomberg.com/news/2", "seed", sid="bloomberg", source="Bloomberg")
        good = post("https://www.thestandard.com.hk/1", "FAA certifies Boeing's 737 MAX 10", "It’s “done”.",
                    sid="thestandard", source="英文虎報 The Standard")
        cg = post("https://www.instagram.com/p/1", "seed2", "Sunflowers line up for a \"face wash\"!", sid="instagram",
                  source="CGTN (Instagram)")
        zh = post("https://www.hkwisdom.hk/1", "apec b1cbe23c", "李家超：陳茂波：彰顯「超級聯繫人」角色", sid="hkwisdom",
                  source="hkwisdom天機")
        # rows as the collectors stored them before the fix (raw SQL: ingest() now decodes)
        c.execute("UPDATE posts SET title=%s, text=%s WHERE id=%s",
                  ("Nordic Firms More Than Double M&amp;A", "<p>Firms&#39; deals</p><p>more</p><div>end</div>", bad["id"]))
        c.execute("UPDATE posts SET ai_title=%s, ai_title_kind='written' WHERE id=%s", ("Sunflowers line up for a 「face wash」", cg["id"]))
        c.execute("UPDATE posts SET ai_title=%s, ai_title_kind='written' WHERE id=%s", ("李家超：彰顯「超級聯繫人」角色", zh["id"]))
        h = c.execute(f"SELECT {streams.HASH} AS h FROM posts p WHERE id=%s", (bad["id"],)).fetchone()["h"]
        c.execute("INSERT INTO scored (post_id, hash) VALUES (%s, %s)", (bad["id"], h))
        c.execute("UPDATE posts SET ai_title_hash=%s WHERE id=%s",
                  (fix_entities.md5("Nordic Firms More Than Double M&amp;A",
                                    "<p>Firms&#39; deals</p><p>more</p><div>end</div>"), bad["id"]))
        c.execute("INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-21', 'CEHK', %s, %s)",
                  (bad["url"], json.dumps({"title": "Nordic Firms More Than Double M&amp;A", "url": bad["url"]})))
        before_good = dict(c.execute("SELECT * FROM posts WHERE id=%s", (good["id"],)).fetchone())
        log = tempfile.mktemp()
        n, m = fix_entities.run(dry=True, log_path=log)
        self.assertEqual((n, m), (2, 1))
        self.assertEqual(c.execute("SELECT title FROM posts WHERE id=%s", (bad["id"],)).fetchone()[0],
                         "Nordic Firms More Than Double M&amp;A")  # a dry run writes nothing
        n, m = fix_entities.run(log_path=log, chunk=2)
        self.assertEqual((n, m), (2, 1))
        r = c.execute(f"SELECT *, {streams.HASH} AS h FROM posts p WHERE id=%s", (bad["id"],)).fetchone()
        self.assertEqual((r["title"], r["text"]), ("Nordic Firms More Than Double M&A", "Firms' deals\nmore\nend"))
        self.assertEqual(c.execute("SELECT hash FROM scored WHERE post_id=%s", (bad["id"],)).fetchone()[0], r["h"])
        self.assertEqual(r["ai_title_hash"], fix_entities.md5(r["title"], r["text"]))
        self.assertIn("m&a", r["stitle"])  # the search columns are redone from the repaired title
        self.assertEqual(c.execute("SELECT ai_title FROM posts WHERE id=%s", (cg["id"],)).fetchone()[0],
                         "Sunflowers line up for a “face wash”")
        self.assertEqual(c.execute("SELECT ai_title FROM posts WHERE id=%s", (zh["id"],)).fetchone()[0],
                         "李家超：彰顯「超級聯繫人」角色")
        self.assertEqual(dict(c.execute("SELECT * FROM posts WHERE id=%s", (good["id"],)).fetchone()), before_good)
        self.assertEqual(c.execute("SELECT item->>'title' FROM stream_items WHERE url=%s", (bad["url"],)).fetchone()[0],
                         "Nordic Firms More Than Double M&A")
        lines = [json.loads(x) for x in open(log)]
        self.assertTrue(any(x.get("id") == bad["id"] and x["title"][1] == "Nordic Firms More Than Double M&A" for x in lines))
        # resumable: the next run starts after the last finished chunk and finds nothing to do
        self.assertGreaterEqual(int(corpus.kv_get(fix_entities.KV)), bad["id"])
        self.assertEqual(fix_entities.run(log_path=log), (0, 0))


LANG_CASES = {
    "Hugging Face chief says Netflix’s OpenAI hack documentary is inaccurate": "en",
    "Cultural Affairs Bureau sets up more than 40 “10-Minute Reading Corners”": "en",
    "Instituto Cultural cria mais de 40 “Cantinhos da Leitura”": "en",
    "Lee (李家超) says the city’s “super-connector” role grows": "en",
    "李家超：向世界展示香港無限機遇 陳茂波：彰顯「超級聯繫人」角色": None,
    "文化局於社區設置逾40個“10分鐘閱讀角”持續招募各界共建全城閱讀網絡": None,
    "準備排隊！G-DRAGON 品牌 PEACEMINUSONE 聯乘 ZO&FRIENDS 登陸香港": None,
    "HOSHIMACHI SUISEI ARENA TOUR 2026 「Once Upon a Stellar」 ホロライブ": None,
    "": None,
}


class Site(unittest.TestCase):
    def test_lang_of(self):
        js = ("import { langOf } from './src/lib/lang.tsx';"
              f"console.log(JSON.stringify({json.dumps(list(LANG_CASES))}.map(s => langOf(s) ?? null)));")
        out = subprocess.run(["npx", "tsx", "-e", js], cwd=WEB, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), list(LANG_CASES.values()))

    def test_pages_mark_post_text(self):
        need = {"src/routes/-ItemPage.tsx": ["<Txt s={D.title} />", "lang={langOf(TEXT.text)}", "lang={langOf(D.extract)}",
                                             "<Txt s={x.title} />", "lang={langOf(full[c.id] ?? c.text)}"],
                "src/routes/-StreamPage.tsx": ["<Txt s={it.title} />", "lang={langOf(it.text)}"],
                "src/routes/-DashboardPage.tsx": ["<Txt s={x.title} />", "<Txt s={r.title} />", "<Txt s={d.title} />",
                                                  "lang={langOf(x.text)}"],
                "src/routes/-SearchPage.tsx": ["<Txt s={x.title}>", "lang={langOf(x.extract)}"],
                "src/routes/-ProofPage.tsx": ["lang={langOf(E.title)}", "lang={langOf(E.text)}"]}
        for f, bits in need.items():
            src = open(os.path.join(WEB, f)).read()
            for b in bits:
                self.assertIn(b, src, f)

    def test_apostrophe_width(self):
        """the site's own font lists (house.css --cjk, --latin) and its :lang(en) rule, in Chromium: ’ narrow in English,
        full-width in Chinese. The site uses the reader's system fonts (PingFang HK on Apple devices, Noto Sans HK
        elsewhere); here the site's "Noto Sans HK" is the runner's installed Noto Sans CJK HK"""
        from playwright.sync_api import sync_playwright
        house = open(os.path.join(WEB, "src/styles/house.css")).read()
        cjk = re.search(r"--cjk:([^;]+);", house).group(1).strip()
        latin = re.search(r"--latin:([^;]+);", house).group(1).strip().replace("var(--cjk)", cjk)
        self.assertRegex(house, r":lang\(en\)[^{]*\{ *font-family:var\(--latin\)")  # English text takes the Latin list
        page = ("<!doctype html><html lang=zh-HK><head><style>"
                "@font-face{font-family:'Noto Sans HK';src:local('Noto Sans CJK HK Regular'),local('NotoSansCJKhk-Regular')}"
                f"body{{font:16px/1.5 {cjk}}} :lang(en){{font-family:{latin}}} span{{white-space:pre}}</style></head><body>"
                "<p><span lang=en id=en>’</span></p><p><span id=zh>’</span></p><p><span id=x>x</span></p></body></html>")
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.set_content(page, wait_until="networkidle")
            pg.evaluate("document.fonts.ready")
            pg.evaluate("document.fonts.load('16px \"Noto Sans HK\"', '’')")
            pg.wait_for_timeout(500)
            w = {k: pg.evaluate(f"document.getElementById('{k}').getBoundingClientRect().width") for k in ("en", "zh")}
            b.close()
        self.assertLess(w["en"], 8, w)    # Latin apostrophe: about a quarter em
        self.assertGreater(w["zh"], 12, w)  # the CJK face's: a full em (Chinese text keeps it)


if __name__ == "__main__":
    unittest.main(verbosity=2)
