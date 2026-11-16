#!/usr/bin/env python3
"""No post is titled with its site's name (Deyao, 2026-10-20: "title is wrong", an HKGolden post in 串流 titled
「香港高登討論區」 and tagged 新聞). Google News lists HKGolden threads with the bare URL as their title; links.py read the
thread page, a JavaScript shell whose <title> is only the site's name (og:site_name 香港高登討論區), and filed that as
the title. The same in other forms: Google News titles 「澳門日報電子版」 (macaodaily.com's <title>) and 「華僑報」, 巴士的報's
video pages whose og:title is 「巴士的報」, 香港討論區 listing pages titled 「香討．香港 No.1」. On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again), no network: saved pages and HKGolden's API answer in
fixtures/sitetitle/.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_site_titles.py

Covers:
  1. corpus.site_named: a title equal to the source's name, the website's, og:site_name, or a known name of the URL's
     site (corpus.SITE_NAMES; generic suffixes such as 電子版 ignored) is a site name; a headline is not.
  2. HKGolden is a forum: a Google News item from forum.hkgolden.com (publisher "HKGolden") is source type Forum,
     website 高登討論區, never News.
  3. Ingest: a news/forum item whose title is a site name is filed with no title yet (〔連結〕host, its link in the
     text), held from scoring until links.py reads the page.
  4. links.py: an HKGolden thread is read through HKGolden's own API (the web app's v1/view call): the thread's
     title and its first post as the text; any other page's title skips a site name (og:title, <title> without
     "- <site>", the page's founder-title, <h1>, a .title block), and a page with only its site name states no title.
  5. fulltext_fetch.page_title never returns a site name (巴士的報's slug title stays).
  6. cache_fetch reads an HKGolden thread through the API (text, author, counts).
  7. links.py fix-site-titles corrects the stored posts (HKGolden by the API, others from the page, slug sources from
     the slug) and their stream items' stored titles and website; membership is kept (no recall).
"""
import json, os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, cache_fetch, corpus, fulltext_fetch, links, streams  # noqa: E402

HKT = app.HKT
NOW = datetime.now(HKT)
FIX = os.path.join(HERE, "tests", "fixtures", "sitetitle")
THREAD = "https://forum.hkgolden.com/thread/8096937/page/1"
AMP = "https://forum.hkgolden.com/amp/8096937/page/1"
REAL = "[宏福苑報告]據悉設重點章節「招呼」房屋局獨立審查組"
MACAO = "https://www.macaodaily.com/html/2026-10/15/content_1940018.htm"
VAKIO = "http://www.vakiodaily.com/news/view/id/718600"
BASTILLE = ("https://www.bastillepost.com/hongkong/article/17003983-%e5%ad%ab%e7%8e%89%e8%8f%a1%e8%aa%aa%e7%a8%b1ai"
            "%e5%8f%aa%e6%98%af%e5%b7%a5%e5%85%b7")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8-sig") as f:
        return f.read()


def api_answer(path, *a, **kw):
    assert "v1/view/8096937/" in path, path
    return page("hkgolden_view_8096937.json")


PAGES = {THREAD: "hkgolden_thread.html", AMP: "hkgolden_amp.html", MACAO: "macaodaily_1940018.html",
         VAKIO: "vakiodaily_718600.html", BASTILLE: "bastille_video.html"}


def fake_page(url):
    return page(PAGES[url])


ai.jsonc = lambda *a, **kw: {}
streams.settings()


def row(url):
    r = corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()
    return dict(r) if r else None


def gnews(url, title, source, sid="gnews_17"):
    corpus.ingest([{"sid": sid, "source": source, "title": title, "url": url,
                    "published": NOW.isoformat(timespec="seconds")}])
    corpus.db().execute("UPDATE posts SET first_run = FALSE")
    return row(url)


def held(pid):
    return bool(corpus.db().execute(f"SELECT {links.HELD_SQL} FROM posts p WHERE p.id=%s", (pid,)).fetchone()[0])


def fresh():
    corpus.db().execute("TRUNCATE posts, link_scan, stream_items CASCADE")


class SiteNamed(unittest.TestCase):
    def test_names(self):
        sn = corpus.site_named
        self.assertTrue(sn("香港高登討論區", url=THREAD))
        self.assertTrue(sn("HKGolden", url=AMP))
        self.assertTrue(sn("澳門日報電子版", "澳門日報"))
        self.assertTrue(sn("華僑報", "華僑報"))
        self.assertTrue(sn(" 巴士的報 ", url=BASTILLE))
        self.assertTrue(sn("香討．香港 No.1", url="https://www.discuss.com.hk/index.php?action=forum&fid=167"))
        self.assertFalse(sn(REAL, "HKGolden", url=THREAD))
        self.assertFalse(sn("僑總賀三慶續發揮優勢", "澳門日報", url=MACAO))
        self.assertFalse(sn("", "澳門日報"))


class Forum(unittest.TestCase):
    def test_hkgolden_is_a_forum(self):
        for source in ("HKGolden", "吹水台- 香港高登討論區", "香港高登討論區"):
            cl = corpus.classify({"sid": "gnews_17", "source": source, "url": THREAD})
            self.assertEqual(cl["source_type"], "Forum", source)
            self.assertEqual(cl["website"], "高登討論區", source)


class Ingest(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_hkgolden_google_news_item(self):
        """the reported post as Google News gives it: the URL as title, publisher HKGolden"""
        r = gnews(AMP, THREAD, "HKGolden")
        self.assertEqual(r["source_type"], "Forum")
        self.assertEqual(r["title"], "〔連結〕forum.hkgolden.com")
        self.assertTrue(held(r["id"]))
        with mock.patch.object(links, "fetch_page", fake_page), \
                mock.patch("hkgolden.get", api_answer):
            links.run_once()
        r = row(AMP)
        self.assertEqual(r["title"], REAL)
        self.assertIn("大埔宏福苑大火獨立委員會", r["text"])
        self.assertNotIn("〔連結內容：香港高登討論區", r["text"] or "")
        self.assertFalse(held(r["id"]))

    def test_site_name_title_is_pending(self):
        r = gnews(MACAO, "澳門日報電子版", "澳門日報", sid="gnews_1")
        self.assertEqual(r["title"], "〔連結〕www.macaodaily.com")
        self.assertEqual(r["text"], MACAO)
        self.assertTrue(held(r["id"]))
        with mock.patch.object(links, "fetch_page", fake_page):
            links.run_once()
        self.assertEqual(row(MACAO)["title"], "僑總賀三慶續發揮優勢")

    def test_headline_kept(self):
        r = gnews("https://www.hk01.com/a/1", "李家超出席立法會答問會", "香港01")
        self.assertEqual(r["title"], "李家超出席立法會答問會")


class Pages(unittest.TestCase):
    def test_thread_shell_states_no_title(self):
        self.assertEqual(links.parse_page(THREAD, page("hkgolden_thread.html"))["title"], "")

    def test_amp_title_without_site(self):
        t = links.parse_page(AMP, page("hkgolden_amp.html"))["title"]
        self.assertIn(REAL, t)
        self.assertNotIn("香港高登討論區", t)

    def test_macao_pages(self):
        self.assertEqual(links.parse_page(MACAO, page("macaodaily_1940018.html"))["title"], "僑總賀三慶續發揮優勢")
        self.assertEqual(links.parse_page(VAKIO, page("vakiodaily_718600.html"))["title"], "港首個具身智能訓練場開幕")

    def test_bastille_site_title(self):
        self.assertEqual(links.parse_page(BASTILLE, page("bastille_video.html"))["title"], "")
        self.assertEqual(fulltext_fetch.page_title(page("bastille_video.html"), BASTILLE), "")

    def test_cache_fetch_reads_the_api(self):
        with mock.patch("hkgolden.get", api_answer):
            text, author, eng = cache_fetch.fetch_one(THREAD, meta := {})
        self.assertIn("大埔宏福苑大火獨立委員會", text)
        self.assertEqual(author, "黃秋岳再世")
        self.assertEqual(eng.get("replies"), 4)
        self.assertEqual(meta.get("title"), REAL)


class Fix(unittest.TestCase):
    def setUp(self):
        fresh()

    def store(self, url, title, sid, source, stype="News", text=None, website=None):
        corpus.ingest([{"sid": sid, "source": source, "title": "暫時標題" + url[-6:], "url": url, "text": text,
                        "published": (NOW - timedelta(days=1)).isoformat(timespec="seconds")}])
        corpus.db().execute("UPDATE posts SET title=%s, source_type=%s, website=%s WHERE url=%s",
                            (title, stype, website or source, url))
        pid = row(url)["id"]
        for s in ("CEHK", "WFC"):
            corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,%s,%s,%s)",
                                (NOW.strftime("%Y-%m-%d"), s, url, json.dumps(
                                    {"post_id": pid, "url": url, "title": title, "source": source,
                                     "website": website or source, "score": 70})))
        return pid

    def test_fix_site_titles(self):
        self.store(AMP, "香港高登討論區", "gnews_17", "HKGolden",
                   text=THREAD + "\n\n〔連結內容：香港高登討論區〕集合分台最新資訊")
        self.store(MACAO, "澳門日報電子版", "gnews_1", "澳門日報", text="僑總賀三慶續發揮優勢\n【本報消息】澳門歸僑總會昨舉行")
        self.store(VAKIO, "華僑報", "gnews_1", "華僑報", text="【新華社香港十月十五日電】")
        self.store(BASTILLE, "巴士的報", "bastillepost", "巴士的報", text="(附字幕) 盧永雄「巴士的點評」")
        self.store("https://www.hk01.com/a/2", "李家超出席立法會答問會", "hk01", "香港01")
        self.assertEqual(links.site_titled(), 4)
        with mock.patch.object(links, "fetch_page", fake_page), mock.patch("hkgolden.get", api_answer):
            out = links.fix_site_titles()
        self.assertEqual(out["before"], 4)
        self.assertEqual(out["after"], 0)
        r = row(AMP)
        self.assertEqual(r["title"], REAL)
        self.assertEqual((r["source_type"], r["website"]), ("Forum", "高登討論區"))
        self.assertIn("大埔宏福苑大火獨立委員會", r["text"])
        self.assertEqual(row(MACAO)["title"], "僑總賀三慶續發揮優勢")
        self.assertEqual(row(VAKIO)["title"], "港首個具身智能訓練場開幕")
        self.assertTrue(row(BASTILLE)["title"].startswith("孫玉菡"), row(BASTILLE)["title"])
        self.assertEqual(row("https://www.hk01.com/a/2")["title"], "李家超出席立法會答問會")
        items = corpus.db().execute("SELECT stream, item FROM stream_items WHERE url=%s ORDER BY stream", (AMP,)).fetchall()
        self.assertEqual([i["stream"] for i in items], ["CEHK", "WFC"])  # kept: no recall
        self.assertEqual({i["item"]["title"] for i in items}, {REAL})
        self.assertEqual({i["item"]["website"] for i in items}, {"高登討論區"})
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM stream_items").fetchone()[0], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
