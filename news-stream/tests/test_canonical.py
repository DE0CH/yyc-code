#!/usr/bin/env python3
"""開啟原文 opens the article's own public address (canonical.py; Deyao, 2026-10-20), on a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again).

2026-10-20: the item page of 信報網站's 「午間新聞精選｜李家超:把握好APEC平台展示香港全面發展」 linked the address Google
News gave, www.hkej.com/instantnews/current/article/4542647/…. The article sits in section hongkong: a desktop browser
gets it whatever the section, but a phone is redirected to m.hkej.com/instantnews/current/…, which is 信報's 404 page.
The page states its own address (<link rel=canonical>, og:url), which opens everywhere.

1. canonical.from_page: <link rel=canonical>, else og:url; a //host or relative address resolved; a twice-encoded
   path (信報's mobile pages) decoded once; raw Chinese percent-encoded; another site's address, the front page and
   the post's own url are not taken.
2. The full-text readers (fulltext_fetch.run, fulltext_now.fetch, cache_fetch.run) keep the page's address in posts.canonical_url; the
   post's url (its identity) stays as it was.
3. The site links to it: the item page's data (item.view) and 搜尋's rows carry `link` = canonical_url, else url, and
   the pages' 開啟原文 / 原文 / Original link use `link`.
4. A host that no reader can open (canonical.REHOST: hkcd.com.hk serves video.hkcd.com's certificate, so every browser
   refuses it; Google News links it) gets the site's working host at ingest, without reading the page.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_canonical.py
"""
import os, re, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, canonical, corpus, fulltext_fetch, resi, sources  # noqa: E402
from edge_call import edge  # noqa: E402

SLUG = ("%E5%8D%88%E9%96%93%E6%96%B0%E8%81%9E%E7%B2%BE%E9%81%B8%E4%B8%A8%E6%9D%8E%E5%AE%B6%E8%B6%85%3A%E6%8A%8A%E6%8F"
        "%A1%E5%A5%BDAPEC%E5%B9%B3%E5%8F%B0%E5%B1%95%E7%A4%BA%E9%A6%99%E6%B8%AF%E5%85%A8%E9%9D%A2%E7%99%BC%E5%B1%95")
GNEWS = "https://www.hkej.com/instantnews/current/article/4542647/" + SLUG    # what Google News linked: 404 on a phone
CANON = "https://www.hkej.com/instantnews/hongkong/article/4542647/" + SLUG   # what the page states
BODY = "".join(f"<p>行政長官李家超出席《信報財經新聞》主辦的「2026粵港澳大灣區高峰論壇」致詞稱，第{i}段。</p>" for i in range(8))
# the head of the desktop page (hkej.com, 2026-10-20), trimmed
DESKTOP = (f'<html><head><title>信報即時新聞 - 香港財經 - 午間新聞精選丨李家超:把握好APEC平台展示香港全面發展 - 信報網站 hkej.com</title>'
           f'<meta property="og:url" content="{CANON}">'
           f'<link rel="canonical" hreflang="zh_hk" href="{CANON}" /></head><body><article>{BODY}</article></body></html>')
# the head of the mobile page (m.hkej.com/landing/mobarticle2/id/4542647/…): a twice-encoded canonical, raw Chinese og:url
TWICE = CANON.split("//", 1)[1].replace("%", "%25")
MOBILE = (f'<html><head><link rel="canonical" href="//{TWICE}">'
          '<meta property="og:url" content="https://m.hkej.com/landing/mobarticle2/id/4542647/午間新聞精選丨李家超:把握好APEC平台展示香港全面發展">'
          f'</head><body>{BODY}</body></html>')


class FromPage(unittest.TestCase):
    def test_hkej_desktop_page(self):
        self.assertEqual(canonical.from_page(DESKTOP, GNEWS), CANON)

    def test_twice_encoded_protocol_relative(self):
        self.assertEqual(canonical.from_page(MOBILE, "https://m.hkej.com/landing/mobarticle2/id/4542647/x"), CANON)

    def test_og_url_raw_chinese_is_encoded(self):
        page = '<meta property="og:url" content="https://m.hkej.com/landing/mobarticle2/id/4542647/李家超">'
        self.assertEqual(canonical.from_page(page, "https://www2.hkej.com/instantnews/current/article/4542647/x"),
                         "https://m.hkej.com/landing/mobarticle2/id/4542647/%E6%9D%8E%E5%AE%B6%E8%B6%85")

    def test_relative(self):
        page = '<link rel="canonical" href="/news/article/a1">'
        self.assertEqual(canonical.from_page(page, "https://amp.example.com.hk/amp/a1?x=1"),
                         "https://amp.example.com.hk/news/article/a1")

    def test_not_taken(self):
        u = "https://news.example.com/2026/10/20/a1"
        for page in ('<link rel="canonical" href="https://news.example.com/">',          # the front page
                     '<meta property="og:url" content="https://www.example.com">',
                     '<link rel="canonical" href="https://other.org/2026/10/20/a1">',    # another site
                     f'<link rel="canonical" href="{u}/">',                               # the post's own url
                     '<link rel="canonical" href="javascript:void(0)">', "", "not html"):
            self.assertIsNone(canonical.from_page(page, u), page)


def hkej_post(url=GNEWS):
    corpus.ingest([{"url": url, "title": "午間新聞精選丨李家超:把握好APEC平台展示香港全面發展", "text": "",
                    "sid": "gnews_0", "source": "信報網站", "published": "2026-10-20T13:15+08:00"}])
    return corpus.ids_for_urls([url])[url]


def stored(pid):
    return corpus.db().execute("SELECT url, canonical_url FROM posts WHERE id=%s", (pid,)).fetchone()


class Readers(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM posts")

    def readers(self):
        return mock.patch.object(sources, "fetch", return_value=DESKTOP), \
            mock.patch.object(resi, "configured", return_value=True), \
            mock.patch.object(resi, "url", return_value="http://proxy.invalid:1"), \
            mock.patch.object(cache_fetch, "pace"), mock.patch("time.sleep"), mock.patch("builtins.print")

    def test_fulltext_keeps_the_page_address(self):
        pid = hkej_post()
        a, b, c, d, e, f = self.readers()
        with a, b, c, d, e, f, mock.patch.object(fulltext_fetch, "candidates", return_value=[(pid, GNEWS, "gnews_0", "2026-10-20")]), \
                mock.patch.object(fulltext_fetch, "BROWSER_PAGES", 0):
            fulltext_fetch.run()
        r = stored(pid)
        self.assertEqual((r["url"], r["canonical_url"]), (GNEWS, CANON))

    def test_cache_fetch_keeps_the_page_address(self):
        pid = hkej_post()
        a, b, c, d, e, f = self.readers()
        with a, b, c, d, e, f, mock.patch.object(cache_fetch, "candidates", return_value=[(pid, GNEWS)]):
            cache_fetch.run()
        r = stored(pid)
        self.assertEqual((r["url"], r["canonical_url"]), (GNEWS, CANON))

    def test_fulltext_now_keeps_the_page_address(self):
        import fulltext_now
        pid = hkej_post()
        a, b, c, d, e, f = self.readers()
        with a, b, c, d, e, f, mock.patch.object(fulltext_now, "_slot"):
            fulltext_now.fetch(pid, GNEWS, "gnews_0")
        self.assertEqual(stored(pid)["canonical_url"], CANON)

    def test_same_address_keeps_none(self):
        pid = hkej_post(CANON)
        a, b, c, d, e, f = self.readers()
        with a, b, c, d, e, f, mock.patch.object(cache_fetch, "candidates", return_value=[(pid, CANON)]):
            cache_fetch.run()
        self.assertIsNone(stored(pid)["canonical_url"])


class Rehost(unittest.TestCase):
    def test_hkcd_bare_host(self):
        corpus.db().execute("DELETE FROM posts")
        u = "https://hkcd.com.hk/hkcdweb/content/2026/10/16/content_8780377.html"
        corpus.ingest([{"url": u, "title": "選舉委員會界別分組一般選舉今日再接獲五份提名表格", "sid": "gnews_13",
                        "source": "香港商報", "published": "2026-10-16T12:00+08:00"}])
        r = stored(corpus.ids_for_urls([u])[u])
        self.assertEqual((r["url"], r["canonical_url"]),
                         (u, "https://www.hkcd.com/hkcdweb/content/2026/10/16/content_8780377.html"))
        self.assertIsNone(canonical.rehost("https://www.hkcd.com/hkcdweb/content/2026/10/16/content_8780377.html"))


class Site(unittest.TestCase):
    def test_item_view_links_the_canonical(self):
        corpus.db().execute("DELETE FROM posts")
        import edge_config
        pid, plain = hkej_post(), hkej_post("https://news.mingpao.com/a1")
        corpus.db().execute("UPDATE posts SET canonical_url=%s WHERE id=%s", (CANON, pid))
        edge_config.publish()
        d, p = edge("item.view", pid), edge("item.view", plain)
        self.assertEqual((d["url"], d["link"]), (GNEWS, CANON))
        self.assertEqual(p["link"], "https://news.mingpao.com/a1")

    def test_search_rows_link_the_canonical(self):
        src = open(os.path.join(HERE, "web-app/src/server/search/search.ts"), encoding="utf-8").read()
        self.assertTrue("p.canonical_url" in src, "search.ts COLS has no p.canonical_url")
        self.assertTrue(re.search(r"link: r\.canonical_url \|\| r\.url", src), "search.ts view() has no link")

    def test_pages_open_link(self):
        src = lambda n: open(os.path.join(HERE, "web-app/src/routes", n), encoding="utf-8").read()
        item = src("-ItemPage.tsx")
        btn = re.search(r'<a className="btn primary" href=\{(D\.\w+)\}[^>]*><Icon name="out" />開啟原文<span className="src">', item)
        self.assertEqual(btn and btn.group(1), "D.link")
        self.assertIn("href={x.link}", src("-SearchPage.tsx"))
        dash = src("-DashboardPage.tsx")
        self.assertNotRegex(dash, r'href=\{\w+\.url\}[^>]*>.*Original link')


if __name__ == "__main__":
    unittest.main()
