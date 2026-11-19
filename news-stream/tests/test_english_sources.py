#!/usr/bin/env python3
"""The Hong Kong English sources (2026-10-14), offline: Young Post's page data, Bloomberg's sitemap + RSS summaries,
Reuters' paged news sitemap, Mingtiandi's cache-busted feed, FinanceAsia's twice-escaped titles and +00:00 dates,
the paywalled outlets' summaries as text, regions, and the English Google News edition.

    python3 tests/test_english_sources.py
"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus, paywall, sources, sources_extra as se  # noqa: E402


def spec(sid):
    return next(s for s in sources.SOURCES + se.EXTRA if s[0] == sid)


SITEMAP = ('<urlset><url><loc>https://www.bloomberg.com/news/articles/2026-10-14/a-story</loc><news:news>'
           '<news:publication_date>2026-10-14T05:30:00.001Z</news:publication_date><news:title>A Story</news:title>'
           '</news:news></url><url><loc>https://www.bloomberg.com/news/articles/2026-10-14/b-story</loc><news:news>'
           '<news:publication_date>2026-10-14T05:31:00Z</news:publication_date><news:title>B Story</news:title>'
           '</news:news></url></urlset>')
BRSS = ('<rss><channel><item><title>A Story</title><link>https://www.bloomberg.com/news/articles/2026-10-14/a-story</link>'
        '<description>The one-line summary of story A.</description></item></channel></rss>')


class English(unittest.TestCase):
    def test_youngpost(self):
        page = ('<a href="/yp/news/hong-kong/article/3370661/bread-maker-apologises">x</a><script>self.__next_f.push([1,"'
                '{\\"articleId\\":\\"a1\\",\\"headLine\\":\\"Bread maker apologises\\",\\"publishDate\\":\\"2026-10-13T02:33:10.000+00:00\\",'
                '\\"entityId\\":\\"3370661\\",\\"currentSectionName\\":\\"Hong Kong\\"},'
                '{\\"articleId\\":\\"a2\\",\\"headLine\\":\\"Rats and AI\\",\\"publishDate\\":\\"2026-10-11T23:30:10.000+00:00\\",'
                '\\"entityId\\":\\"3370121\\",\\"urlAlias\\":\\"/yp/news/sports/article/3370121/rats-and-ai\\"}"])</script>')
        with mock.patch.object(sources, "fetch", return_value=page):
            got = sources.fetch_source(spec("youngpost_hk"))
        self.assertEqual([(x["title"], x["url"], x["section"]) for x in got], [
            ("Bread maker apologises", "https://www.youngpostclub.com/yp/news/hong-kong/article/3370661/bread-maker-apologises", "Hong Kong"),
            ("Rats and AI", "https://www.youngpostclub.com/yp/news/sports/article/3370121/rats-and-ai", None)])
        self.assertEqual(got[0]["published"].isoformat(), "2026-10-13T02:33:10+00:00")

    def test_bloomberg_summaries(self):
        def fake(url, **kw):
            return SITEMAP if "sitemaps" in url else BRSS if "markets" in url else "<rss></rss>"
        with mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(se, "fetch", side_effect=fake):
            got = sources.fetch_source(spec("bloomberg"))
        self.assertEqual([x["title"] for x in got], ["A Story", "B Story"])  # every headline, none dropped
        self.assertEqual(got[0]["text"], "The one-line summary of story A.")
        self.assertNotIn("text", got[1])
        self.assertEqual(corpus.classify(got[0])["region"], "Global")

    def test_reuters_two_pages(self):
        page = ('<urlset><url><loc>https://www.reuters.com/world/china/x-2026-10-14/</loc><news:news><news:publication_date>'
                '2026-10-14T06:21:52.604Z</news:publication_date><news:title>{t}</news:title></news:news></url></urlset>')
        # every Reuters sitemap is read with the solved DataDome session since 30 Oct 2026 (paywall.reuters_page)
        def fake(url):
            if "news-sitemap" not in url:
                return "<urlset></urlset>"  # the full sitemap: nothing more here
            return page.format(t="Second" if "from=50" in url else "First")
        with mock.patch.object(paywall, "reuters_page", side_effect=fake) as read:
            got = sources.fetch_source(spec("reuters"))
        self.assertEqual([(x["title"], x["section"]) for x in got], [("First", "world"), ("Second", "world")])
        self.assertTrue(any("news-sitemap" in c.args[0] and "from=50" in c.args[0] for c in read.call_args_list))

    def test_mingtiandi_cache_bust_and_summary(self):
        feed = ('<rss><item><title>Deal</title><link>https://www.mingtiandi.com/real-estate/x/</link>'
                '<pubDate>Wed, 14 Oct 2026 04:35:27 +0000</pubDate><description><![CDATA[<p>Brookfield bought parks. '
                'The deal gives the fund... <a>Read More&gt;&gt;</a></p><p>The post Deal appeared first on Mingtiandi.</p>]]>'
                '</description></item></rss>')
        with mock.patch.object(sources, "fetch", return_value=feed) as f:
            got = sources.fetch_source(spec("mingtiandi"))
        self.assertRegex(f.call_args.args[0], r"/feed/\?t=\d{10}$")
        self.assertEqual(got[0]["text"], "Brookfield bought parks. The deal gives the fund…")

    def test_financeasia(self):
        feed = ('<rss><item><title>Malaysia&amp;#39;s AI &amp;amp; more</title><link>https://www.financeasia.com/article/x/1</link>'
                '<pubDate>Tue, 13 Oct 2026 09:03:00 +00:00</pubDate></item></rss>')
        with mock.patch.object(sources, "fetch", return_value=feed):
            got = sources.fetch_source(spec("financeasia"))
        self.assertEqual(got[0]["title"], "Malaysia's AI & more")
        self.assertEqual(got[0]["published"].isoformat(), "2026-10-13T09:03:00+00:00")  # aware, not naive

    def test_full_text_feeds_and_regions(self):
        rss = ('<rss><item><title>Buses seek fare rises</title><link>https://news.rthk.hk/rthk/en/component/k2/1-20261014.htm</link>'
               '<pubDate>Wed, 14 Oct 2026 14:36:42 +0800</pubDate><description>Four franchised bus operators…</description></item></rss>')
        with mock.patch.object(sources, "fetch", return_value=rss):
            rthk = sources.fetch_source(spec("rthk_en_local"))
            press = sources.fetch_source(spec("infogov_press_en"))
            ft = sources.fetch_source(spec("ft_hk"))
            fa = sources.fetch_source(spec("financeasia"))
        self.assertEqual(rthk[0]["text"], "Four franchised bus operators…")
        self.assertEqual(ft[0]["text"], "Four franchised bus operators…")  # paywall: the summary is the text
        self.assertNotIn("text", fa[0])  # free lead paragraph: fulltext_fetch reads the page
        self.assertEqual((corpus.classify(rthk[0])["source_type"], corpus.classify(rthk[0])["region"]), ("News", "HK"))
        self.assertEqual(corpus.classify(press[0])["source_type"], "Official")
        self.assertEqual(corpus.classify(ft[0])["region"], "Global")
        self.assertEqual(se.GLOBAL_SIDS, corpus.GLOBAL_SIDS)

    def test_gnews_english_edition(self):
        got = sources.gnews_queries(['"John Lee" Hong Kong'], "en")
        self.assertEqual(got[0][0], "gnews_en_0")
        self.assertTrue(got[0][2].endswith("+when:1d&hl=en-HK&gl=HK&ceid=HK:en"))
        self.assertTrue(sources.gnews_queries(["李家超"])[0][2].endswith("hl=zh-HK&gl=HK&ceid=HK:zh-Hant"))

    def test_paywalled_hosts_are_read(self):
        """Deyao, 2026-10-15: Reuters, Bloomberg, FT and Mingtiandi are read (paywall.py; tests/test_paywall.py)"""
        import cache_fetch, fulltext_fetch
        for h in ("bloomberg.com", "ft.com", "reuters.com", "mingtiandi.com"):
            self.assertFalse(h in cache_fetch.SKIP_HOSTS)
        self.assertEqual(set(fulltext_fetch.SLUG_SIDS), se.SLUG_TITLES)
        self.assertEqual(fulltext_fetch.page_title('<meta property="og:title" content="AcousPrint develops tool"/>'),
                         "AcousPrint develops tool")


if __name__ == "__main__":
    unittest.main()
