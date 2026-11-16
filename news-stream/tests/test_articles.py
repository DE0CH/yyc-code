#!/usr/bin/env python3
"""articles (Deyao, 2026-10-14): posts that are the same content share posts.article_id. On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_articles.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus  # noqa: E402

BODY = "政府今日宣布新一輪措施，" * 30  # well over TEXT_KEY_MIN letters
OTHER = "立法會今日三讀通過條例草案，" * 30


def post(url, title="標題", text=None, sid="mingpao", source="明報", **kw):
    ids = corpus.ingest([dict(url=url, title=title, text=text, sid=sid, source=source, **kw)])
    return ids[0] if ids else corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]


def art(i):
    return corpus.db().execute("SELECT article_id FROM posts WHERE id=%s", (i,)).fetchone()[0]


class UrlKey(unittest.TestCase):
    def test_cleanup(self):
        k = corpus.url_key
        self.assertEqual(k("https://www.example.com/a/b/?utm_source=x&id=2&fbclid=y#top"), "example.com/a/b?id=2")
        self.assertEqual(k("http://m.example.com/a/b?id=2"), "example.com/a/b?id=2")
        self.assertNotEqual(k("https://example.com/a?id=2"), k("https://example.com/a?id=3"))

    def test_scmp_variants(self):
        """SCMP coverage audit 2026-10-20: the same SCMP story came in as several articles: amp.scmp.com (Google News),
        SCMP's own ?module=…&pgtype=… placement parameters, and scmp.com/yp|spark|posties/… (Young Post's stories,
        which SCMP's sitemaps list on scmp.com and we read from youngpostclub.com)"""
        k = corpus.url_key
        base = "https://www.scmp.com/news/hong-kong/society/article/3371394/how-smart-will-hong-kongs-ai-city-brain-be"
        self.assertEqual(k(base.replace("www.", "amp.")), k(base))
        self.assertEqual(k(base + "?module=perpetual_scroll_0&pgtype=article"), k(base))
        self.assertEqual(k(base + "?utm_source=rss_feed"), k(base))
        yp = "/yp/news/world/article/3371492/why-protests-are-sweeping-across-europe"
        self.assertEqual(k("https://www.scmp.com" + yp), k("https://www.youngpostclub.com" + yp))
        self.assertEqual(k("https://www.scmp.com/posties/kids/big-read/article/3370535/x"),
                         k("https://www.youngpostclub.com/posties/kids/big-read/article/3370535/x"))
        self.assertEqual(k("https://www.scmp.com/spark/learning-zone/article/3370974/x"),
                         k("https://www.youngpostclub.com/spark/learning-zone/article/3370974/x"))
        self.assertNotEqual(k("https://www.scmp.com/news/yp-lookalike/article/1/x"), k("https://www.youngpostclub.com/news/yp-lookalike/article/1/x"))
        # other sites keep their query (an id can live there)
        self.assertNotEqual(k("https://example.com/a?module=1"), k("https://example.com/a?module=2"))

    def test_link_in_social_post(self):
        self.assertEqual(corpus.link_key("", "睇呢篇 https://l.facebook.com/l.php?u=https%3A%2F%2Fnews.example.com%2Fx%3Futm_medium%3Dfb"),
                         "news.example.com/x")
        self.assertIsNone(corpus.link_key("", "https://www.facebook.com/somepage/posts/1"))


class Articles(unittest.TestCase):
    def test_alone(self):
        i = post("https://a.example.com/alone")
        self.assertEqual(art(i), i)

    def test_same_url_after_cleanup(self):
        a = post("https://b.example.com/story?utm_source=rss")
        b = post("https://www.b.example.com/story/")
        self.assertEqual(art(b), a)

    def test_same_full_text_across_sites(self):
        a = post("https://c1.example.com/x", text=BODY, source="明報")
        b = post("https://c2.example.com/y", text=BODY, sid="hk01", source="香港01", title="另一標題")
        self.assertEqual(art(b), a)

    def test_full_text_arriving_later(self):
        a = post("https://d1.example.com/x", text=OTHER)
        b = post("https://d2.example.com/y", sid="hk01", source="香港01")
        self.assertEqual(art(b), b)
        corpus.set_text(b, OTHER)
        self.assertEqual(art(b), a)

    def test_same_site_boilerplate_is_not_an_article(self):
        wall = "請啟用瀏覽器的JavaScript功能以繼續閱讀本網站的內容" * 10
        a = post("https://e.example.com/1", title="新聞一", text=wall)
        b = post("https://e.example.com/2", title="新聞二", text=wall)
        self.assertEqual(art(b), b)
        self.assertNotEqual(art(a), art(b))

    def test_short_text_never_matches(self):
        a = post("https://f1.example.com/x", text="好")
        b = post("https://f2.example.com/y", text="好", sid="hk01", source="香港01")
        self.assertEqual(art(b), b)
        self.assertNotEqual(art(a), art(b))

    def test_social_share_after_and_before_the_article(self):
        a = post("https://g.example.com/story")
        s = post("https://www.facebook.com/page/posts/1", title="分享", text="https://g.example.com/story?utm_medium=social",
                 sid="facebook", source="某專頁 (Facebook)")
        self.assertEqual(art(s), a)
        s2 = post("https://www.facebook.com/page/posts/2", title="分享", text="https://h.example.com/story",
                  sid="facebook", source="某專頁 (Facebook)")
        h = post("https://h.example.com/story")
        self.assertEqual(art(h), s2)

    def test_two_articles_merge(self):
        a = post("https://i1.example.com/x", text=BODY.replace("政府", "九龍"))
        b = post("https://i2.example.com/y", sid="hk01", source="香港01")
        self.assertNotEqual(art(a), art(b))
        c = post("https://i2.example.com/y?utm_source=x", text=BODY.replace("政府", "九龍"), sid="rthk", source="香港電台")
        self.assertEqual({art(a), art(b), art(c)}, {a})

    def test_comments_are_never_grouped(self):
        t = dict(url="https://j.example.com/thread", title="帖", sid="lihkg_1", source="LIHKG")
        ids = corpus.add_comments(t, [{"id": "1", "text": BODY}, {"id": "2", "text": BODY}])
        self.assertEqual([art(i) for i in ids], ids)

    def test_z_backfill_matches(self):
        before = dict(corpus.db().execute("SELECT id, article_id FROM posts").fetchall())
        corpus.db().execute("UPDATE posts SET article_id=NULL, url_key=NULL, text_key=NULL, link_key=NULL")
        corpus.backfill_articles(batch=3)
        after = dict(corpus.db().execute("SELECT id, article_id FROM posts").fetchall())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
