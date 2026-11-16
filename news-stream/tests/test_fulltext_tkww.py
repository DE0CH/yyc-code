#!/usr/bin/env python3
"""Full text for the 大公文匯 group (紫荊, 大公報 / 文匯報 and their e-papers, 點新聞, 香港商報, 大公文匯網 via Google News):
saved real pages (tests/fixtures/fulltext/tkww_*.html, 2026-10-20) give the article's own text, never a hidden field,
the site's default description, the footer or the headline again.

    python3 tests/test_fulltext_tkww.py
"""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cache_fetch

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fulltext")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class ArticleText(unittest.TestCase):
    def test_bauhinia_hidden_fields_are_not_the_text(self):
        # 紫荊: a flash whose story is one <section>; hidden <p>s hold the post id and its JSON URL
        t = cache_fetch.article_text(page("tkww_bauhinia_openweb.html"))
        self.assertTrue(t.startswith("中國基金報10月20日電，據港交所披露"), t)
        self.assertIn("240.5041港元", t)
        self.assertNotIn("json", t)
        self.assertNotIn("1562149651470286848", t)
        self.assertNotIn("信德中心", t)  # its footer's address <p>s

    def test_hkcd_short_story_not_the_site_slogan(self):
        # 香港商報: a two-paragraph story; its meta description is the paper's own blurb on every page
        t = cache_fetch.article_text(page("tkww_hkcd_short.html"))
        self.assertTrue(t.startswith("截至發稿，韓國KOSPI指數跌幅擴大至4%"), t)
        self.assertIn("開盤下跌0.6%", t)
        self.assertNotIn("歷史悠久", t)

    def test_hkcd_flash_not_the_footer(self):
        # 香港商報: a one-line flash; the page's <div id="footer2"> holds its phone numbers and address
        t = cache_fetch.article_text(page("tkww_hkcd_flash.html"))
        self.assertTrue(t.startswith("10月13日早盤，日韓股市「崩」了"), t)
        self.assertNotIn("觀塘道", t)

    def test_wenweipo_video_blurb_not_the_headline(self):
        # 香港文匯報: a video post whose text is one short paragraph under the clip
        t = cache_fetch.article_text(page("tkww_wenweipo_video.html"))
        self.assertEqual(t, "香港文匯報訊 新皇崗口岸來了，如何轉化為實質經濟動力？今集經湋論，主持提出六個建議提振香港零售餐飲及旅遊業。")

    def test_chart_only_pages_give_nothing(self):
        # pages whose story is only a picture (a chart, a table): no text rather than a slogan, a footer, the
        # headline or a stray "””" description
        for name in ("tkww_wenweipo_chart.html", "tkww_tkp_epaper_chart.html", "tkww_wwp_epaper_chart.html"):
            self.assertEqual(cache_fetch.article_text(page(name)), "", name)
        t = cache_fetch.article_text(page("tkww_wenweipo_infographic.html"))
        self.assertNotIn("一圖速覽", t)  # the headline (its description repeats it)

    def test_story_inside_an_unclosed_header(self):
        # 上報 (via Google News): its whole page sits inside a <header> the markup never closes
        t = cache_fetch.article_text(page("tkww_upmedia.html"))
        self.assertGreater(len(t), 1000)
        self.assertIn("張啓楷表示，早在二年多前的2024年2月", t)
        self.assertNotIn("...", t[:200])


class Routes(unittest.TestCase):
    def test_hkcd_com_hk_read_on_www(self):
        # hkcd.com.hk serves a certificate for another name; www.hkcd.com.hk has the same pages
        seen = []
        with mock.patch.object(cache_fetch, "get", side_effect=lambda u, **k: seen.append(u) or "<html></html>"):
            cache_fetch.fetch_one("https://hkcd.com.hk/hkcdweb/content/2026/10/20/content_8780795.html")
        self.assertEqual(seen, ["https://www.hkcd.com.hk/hkcdweb/content/2026/10/20/content_8780795.html"])

    def test_isp_hosts_use_the_isp_ip(self):
        # 自由時報 and 菱傳媒 answer 403 to the cluster's IP: they are read on YYC's IPRoyal ISP IP
        seen = []
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://u:p@isp.example:1"}), \
                mock.patch.object(cache_fetch, "get", side_effect=lambda u, **k: seen.append(k) or "<html></html>"):
            cache_fetch.fetch_one("https://news.ltn.com.tw/news/politics/breakingnews/5611937")
            cache_fetch.fetch_one("https://turnnewsapp.com/livenews/politicsv3/20261020003017-260407")
        self.assertEqual([(k.get("proxy"), k.get("isp")) for k in seen], [("http://u:p@isp.example:1", True)] * 2)

    def test_isp_host_without_the_isp_proxy_fails(self):
        with mock.patch.dict(os.environ, {"ISP_PROXY": ""}), self.assertRaises(RuntimeError):
            cache_fetch.fetch_one("https://news.ltn.com.tw/news/politics/breakingnews/5611937")

    def test_isp_tunnel_refusal_is_tried_again(self):
        # the ISP IP serves one client at a time; a second gets "Tunnel connection failed: 403" (seen 2026-10-20)
        import urllib.error
        calls = []

        def fetch(url, **k):
            calls.append(url)
            if len(calls) < 3:
                raise urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))
            return "<html>ok</html>"
        with mock.patch.object(cache_fetch.sources, "fetch", side_effect=fetch), \
                mock.patch.object(cache_fetch, "_isp_lock", mock.MagicMock()), \
                mock.patch.object(cache_fetch.time, "sleep"):
            self.assertEqual(cache_fetch.get("https://news.ltn.com.tw/x", proxy="http://isp", isp=True), "<html>ok</html>")
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
