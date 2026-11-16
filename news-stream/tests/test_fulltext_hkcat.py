#!/usr/bin/env python3
"""Full text for catalogue sources (group hkcat, 2026-10-20): 香港城市大學 (Incapsula interstitial; Drupal body split into several blocks), Sino Group (a React
page over Kentico's delivery API), Vintage Grail (a Nuxt page over its own API), 生活易 (links without the /en/ the
site needs), 新華財經's disclaimer line, and the listings in sites.json that listed pages that are not articles
(businesstouch's forms, 低聲道's sitemap, Vintage Grail's shop items). Fixtures are real pages and
API answers (scripts, styles and images stripped) under fixtures/fulltext/hkcat_*.

    python3 tests/test_fulltext_hkcat.py
"""
import json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cache_fetch, canonical, sites_fetch  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fulltext")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def site(sid):
    return next(s for s in sites_fetch.load() if s["id"] == sid)


class CityU(unittest.TestCase):
    def test_incapsula_interstitial_is_a_challenge(self):
        # Imperva's interstitial (212-1,200 bytes, its script /_Incapsula_Resource) answers the cluster's IP and the ISP
        # IP alike: a real browser passes it, so the page goes to the browser pass, never stored as an empty article
        self.assertTrue(cache_fetch.challenged(page("hkcat_incapsula.html")))
        with mock.patch.object(cache_fetch, "get", return_value=page("hkcat_incapsula.html")):
            with self.assertRaises(cache_fetch.Challenge):
                cache_fetch.fetch_one("https://www.example.org/news/1")

    def test_story_is_every_body_block_not_the_contact_box(self):
        # the story's Drupal body is split over several field--name-body blocks; a "paragraph paragraph--type--cityu-poi"
        # contact box (Drupal Paragraphs, not on.cc's <div class="paragraph">) was taken for the story (149 characters)
        p = page("hkcat_cityu_news.html")
        t = cache_fetch.marked_body("https://www.cityu.edu.hk/zh-cn/media/news/2026/10/20/cityuhk-researchers-shatter-physical-limitations", p)
        self.assertTrue(t.startswith("對於科學家而言，最令人振奮的驚喜"), t[:60])
        self.assertIn("來自自然界能源的能量往往伴隨着「熵」", t)
        self.assertIn("本研究獲研究資助局的優配研究金資助。", t)
        self.assertNotIn("ciro@cityu.edu.hk", t)
        a = cache_fetch.article_text(p)
        self.assertNotIn("ciro@cityu.edu.hk", a)

    def test_language_cookie_follows_the_url(self):
        # Sitecore keys www.cityu.edu.hk/media/… on the cookie home#lang the last page set: an English URL read after a
        # /zh-hk/ one came back in Chinese
        self.assertEqual(cache_fetch.lang_cookie("https://www.cityu.edu.hk/media/news/2026/10/16/x")["value"], "en")
        self.assertEqual(cache_fetch.lang_cookie("https://www.cityu.edu.hk/zh-hk/media/news/2026/10/16/x")["value"], "zh-HK")
        self.assertEqual(cache_fetch.lang_cookie("https://www.cityu.edu.hk/zh-cn/media/news/x")["value"], "zh-CN")
        self.assertIsNone(cache_fetch.lang_cookie("https://www.hk01.com/x"))

    def test_on_cc_paragraph_blocks_still_read(self):
        p = ('<html><body><div class="paragraph"><div class="content">' + "第一段" * 30 + '</div></div>'
             '<div class="paragraph"><div class="content">' + "第二段" * 30 + '</div></div></body></html>')
        t = cache_fetch.article_text(p)
        self.assertIn("第一段", t)
        self.assertIn("第二段", t)


class Sino(unittest.TestCase):
    """www.sino.com: a React page whose text comes from Kentico's delivery API (web-cdn.sino.com)"""

    def test_api_url(self):
        self.assertEqual(cache_fetch.sino_api("https://www.sino.com/tc/media-centre/press-release/2026/hkust-sino-one-million-dollar-entrepreneurship-competition-2026-concludes/"),
                         "https://web-cdn.sino.com/20a53f0a-15c8-0029-b8df-e495023b403f/items?elements.slug="
                         "hkust-sino-one-million-dollar-entrepreneurship-competition-2026-concludes&language=tc&depth=0&limit=1")
        self.assertIn("language=en", cache_fetch.sino_api("https://www.sino.com/en/our-business/residential/one-marina/"))
        self.assertIsNone(cache_fetch.sino_api("https://www.sino.com/en/"))

    def test_press_release_body(self):
        t = cache_fetch.sino_text(page("hkcat_sino_press_release.json"))
        self.assertIn("HKUST InnoBay 1M PLUS計劃啟動", t)
        self.assertGreater(len(t), 800)
        self.assertNotIn("<", t)

    def test_property_page(self):
        t = cache_fetch.sino_text(page("hkcat_sino_property.json"))
        self.assertIn("Name of the Development: One Marina", t)

    def test_fetch_one(self):
        with mock.patch.object(cache_fetch, "get", return_value=page("hkcat_sino_press_release.json")) as g:
            t, _, _ = cache_fetch.fetch_one("https://www.sino.com/tc/media-centre/press-release/2026/hkust-sino-one-million-dollar-entrepreneurship-competition-2026-concludes/")
        self.assertIn("web-cdn.sino.com", g.call_args[0][0])
        self.assertIn("HKUST InnoBay 1M PLUS計劃啟動", t)


class VintageGrail(unittest.TestCase):
    """vintagegrail.com: a Nuxt page over api.vintagegrail.com (articles: details[] in order; watches: detail.description)"""

    def test_api_urls(self):
        self.assertEqual(cache_fetch.vintagegrail_api("https://vintagegrail.com/explore/opinion-pieces/the-best-watches-of-2022-so-far"),
                         "https://api.vintagegrail.com/api/v1/web/article/item/the-best-watches-of-2022-so-far")
        self.assertEqual(cache_fetch.vintagegrail_api("https://vintagegrail.com/media-room/press/x-y"),
                         "https://api.vintagegrail.com/api/v1/web/article/item/x-y")
        self.assertEqual(cache_fetch.vintagegrail_api("https://vintagegrail.com/timepieces/watches/aquanaut-5168"),
                         "https://api.vintagegrail.com/api/v1/web/watch/item/aquanaut-5168")
        self.assertEqual(cache_fetch.vintagegrail_api("https://vintagegrail.com/accessories/chrono-case-6"),
                         "https://api.vintagegrail.com/api/v1/web/accessories/item/chrono-case-6")

    def test_article_every_section(self):
        t = cache_fetch.vintagegrail_text(page("hkcat_vintagegrail_article.json"))
        self.assertTrue(t.startswith("2022 has been a record-breaking year"), t[:60])
        self.assertIn("Patek Philippe Annual Calendar Travel-Time Ref. 5326G-001", t)
        self.assertIn("As demands for mainstream brands have been on the rise", t)
        self.assertGreater(len(t), 7000)
        self.assertNotIn("&nbsp;", t)

    def test_watch_without_description(self):
        self.assertEqual(cache_fetch.vintagegrail_text(page("hkcat_vintagegrail_watch.json")), "")


class EsdLife(unittest.TestCase):
    def test_news_links_get_the_language_the_site_needs(self):
        # the /en/news list links /news/<slug>, which answers 404; the page is /en/news/<slug> (2026-10-20)
        self.assertEqual(canonical.rehost("https://www.esdlife.com/news/sharing-blessings"),
                         "https://www.esdlife.com/en/news/sharing-blessings")
        self.assertIsNone(canonical.rehost("https://www.esdlife.com/en/news/sharing-blessings"))
        with mock.patch.object(cache_fetch, "get", return_value="<html><body><p>x</p></body></html>") as g:
            cache_fetch.fetch_one("https://www.esdlife.com/news/sharing-blessings")
        self.assertEqual(g.call_args[0][0], "https://www.esdlife.com/en/news/sharing-blessings")


class CnFin(unittest.TestCase):
    def test_disclaimer_is_junk(self):
        self.assertTrue(cache_fetch.junk("声明：新华财经（中国金融信息网）为新华社承建的国家金融信息平台。任何情况下，本平台所发布的信息均不构成投资建议。如有问题，请联系客服：400-6123115"))
        self.assertFalse(cache_fetch.junk("截至10月16日，宁波舟山港江内中品位铁矿石价格指数报810.05点.，较10月10日下跌14.57点，跌幅1.77%。"))


class Listings(unittest.TestCase):
    """sites.json: lists that listed pages which are not articles"""

    def rows(self, sid, fixture):
        return sites_fetch.read(site(sid), fetch=lambda u: page(fixture))

    def test_kwaifunghin_exhibitions(self):
        # /feed/ was Artlogic's artwork inventory (each item a JPG); the gallery publishes its exhibitions
        rows = self.rows("hk_kwaifunghin", "hkcat_kwaifunghin_exhibitions.html")
        self.assertEqual(len(rows), 3)
        self.assertIn(("Group Exhibition: In Search of Lost Time",
                       "https://www.kwaifunghin.com/exhibitions/119-group-exhibition-in-search-of-lost-time-kwai-fung-hin-hong-kong/overview/"),
                      [(r["title"], r["url"]) for r in rows])

    def test_businesstouch_blog_posts_only(self):
        urls = sorted(r["url"] for r in self.rows("hk_businesstouch_com_tr", "hkcat_businesstouch_sitemap.xml"))
        self.assertEqual(urls, ["http://businesstouch.com.tr/blog/dijital-kartvizit-1/neden-business-touch-dijital-kartvizit-37",
                                "http://businesstouch.com.tr/blog/oteller-icin-tripadvisor-da-yorum-ve-puanlama-almann-onemi-iste-neden-6/35"])

    def test_vintagegrail_articles(self):
        urls = [r["url"] for r in self.rows("hk_vintagegrail_articles", "hkcat_vintagegrail_sitemap.xml")]
        self.assertEqual(len(urls), 3)
        self.assertTrue(all("/explore/" in u or "/media-room/" in u for u in urls), urls)

    def test_thevoicehk_feed_carries_the_story(self):
        rows = self.rows("hk_thevoicehk", "hkcat_thevoicehk.rss")
        self.assertEqual(rows[0]["title"], "教宗確診肺腺癌 手術成功 切除右下肺葉 醒來神智清晰")
        self.assertTrue(rows[0]["url"].startswith("https://www.thevoicehk.com/news/"))
        self.assertGreater(len(rows[0].get("text") or ""), 500)


if __name__ == "__main__":
    unittest.main()
