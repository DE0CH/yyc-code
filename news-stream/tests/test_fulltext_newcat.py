#!/usr/bin/env python3
"""Full text for catalogue sources (group newcat, 2026-10-20): 安聯投資 (a cookie-setting redirect urllib loops on; the
story split over several rich-text blocks), 易發車網 (a frameset over the article), 大館 (every press release one
#press-N section of one page), 公民教育委員會 (tables of the story's details), 香港紅十字會 (a newsletter that is a PDF in
pdf.js), Prestige (AWS WAF's challenge), Regulation Asia (a login wall), MSN (a JavaScript page over its Detail API),
鳳凰美洲網 (an Angular shell over /api/articles/<slug>), 澳門衛生局 (PDFs behind Cloudflare's challenge), Thomasnet (403s
when read fast), 澳門平台 (pages refused, its WordPress REST API open). Fixtures are real pages and API answers (scripts,
styles and images stripped) under fixtures/fulltext/newcat_*.

    python3 tests/test_fulltext_newcat.py
"""
import json, os, sys, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cache_fetch, paywall  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fulltext")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class AllianzGI(unittest.TestCase):
    URL = "https://hk.allianzgi.com/zh-hk/retail/insights/insights-and-strategies/202606-fx-hedging"

    def test_redirect_loop_is_read_with_cookies(self):
        # the site answers 302 to itself with Set-Cookie until the cookies come back; urllib keeps none and gives up
        # ("redirect error that would lead to an infinite loop"); curl_cffi keeps them through the redirects
        loop = urllib.error.HTTPError(self.URL, 302, "The HTTP server returned a redirect error that would lead to an "
                                      "infinite loop.", {}, None)
        r = mock.Mock(status_code=200, text="<html>ok</html>")
        with mock.patch.object(cache_fetch.sources, "fetch", side_effect=loop), \
                mock.patch("curl_cffi.requests.get", return_value=r) as g:
            self.assertEqual(cache_fetch.get(self.URL), "<html>ok</html>")
        g.assert_called_once()

    def test_story_is_every_rich_text_block(self):
        # the story is several <div class="u-padding-top-s c-copy"> blocks between charts; the largest paragraph
        # cluster was one of them (487 of 2,439 characters)
        t = cache_fetch.marked_body(self.URL, page("newcat_allianzgi.html"))
        self.assertTrue(t.startswith("近年，美元及日圓走弱"), t[:40])
        self.assertIn("是建立具韌性且均衡投資組合的關鍵", t)
        self.assertGreater(len(t), 2000)
        self.assertNotIn("本文內所載的資料", t)  # the disclaimer under the story
        self.assertNotIn("版權所有", t)


class Car28(unittest.TestCase):
    def test_frameset_reads_its_frame(self):
        # pub_art.php is a <frameset> whose one frame is index2.php?tourl=… (it redirects to the article's page)
        calls = []

        def get(url, **kw):
            calls.append(url)
            return page("newcat_28car_frameset.html") if len(calls) == 1 else page("newcat_28car_article.html")
        with mock.patch.object(cache_fetch, "get", side_effect=get):
            text, _, _ = cache_fetch.fetch_one("https://www.28car.com/pub_art.php?h_id=6463")
        self.assertEqual(calls[1], "https://www.28car.com/index2.php?tourl=%2Fpub_art.php%3Fh_id%3D6463")
        self.assertIn("Mercedes-AMG Motorsport 在闊別澳門賽場一年後", text)
        self.assertIn("成為東望洋賽道歷來最成功的汽車品牌", text)


class TaiKwun(unittest.TestCase):
    def test_fragment_is_its_own_press_release(self):
        # every press release is a #press-N section of one page; the post is that section, not the whole page
        with mock.patch.object(cache_fetch, "get", return_value=page("newcat_taikwun.html")):
            text, _, _ = cache_fetch.fetch_one("https://www.taikwun.hk/en/taikwun/press/press_release#press-204")
        self.assertTrue(text.startswith("Hong Kong, 7 Jan 2026, Wednesday"), text[:60])
        self.assertIn("Zhang Peili", text)
        self.assertFalse(text.endswith("Copy Url"), text[-40:])
        with mock.patch.object(cache_fetch, "get", return_value=page("newcat_taikwun.html")):
            other, _, _ = cache_fetch.fetch_one("https://www.taikwun.hk/en/taikwun/press/press_release#press-203")
        self.assertNotIn("DigiRadiance", other)
        self.assertGreater(len(other), 500)


class CPCE(unittest.TestCase):
    def test_story_keeps_its_details_table(self):
        t = cache_fetch.marked_body("https://www.cpce.gov.hk/main/tc/cvcc_interactive_drama26.html", page("newcat_cpce.html"))
        self.assertTrue(t.startswith("公民教育委員會（下稱「委員會」）"), t[:30])
        self.assertIn("故事大綱", t)
        self.assertIn("2026年10月26日至2027年7月", t)


class RedCross(unittest.TestCase):
    def test_pdf_in_a_pdfjs_viewer(self):
        # 紅十字脈搏's issue page is a pdf.js viewer over the issue's PDF: the page's only text is the PDF's path
        u = "https://www.redcross.org.hk/tc/publications/redcross_news_detail_107/rcp107_full.html"
        self.assertEqual(cache_fetch.pdf_link(u, page("newcat_redcross_ebook.html")),
                         "https://www.redcross.org.hk/sites/redcross/files/media/cco_files/ebook/"
                         "HKRC-newsletter%202023-JUN_spread_compressed.pdf")

    def test_redirect_pdf_still_found(self):
        p = "<script>window.open('/haho/ho/pad/260916PAE.pdf', '_self')</script>"
        self.assertEqual(cache_fetch.pdf_link("https://www.ha.org.hk/x.asp", p), "https://www.ha.org.hk/haho/ho/pad/260916PAE.pdf")


class Prestige(unittest.TestCase):
    def test_aws_waf_is_a_challenge(self):
        # CloudFront answers 202 with an empty body (urllib) or AWS WAF's challenge script (curl_cffi)
        self.assertTrue(cache_fetch.challenged(page("newcat_prestige_awswaf.html")))
        for p in ("", page("newcat_prestige_awswaf.html")):
            with mock.patch.object(cache_fetch, "get", return_value=p):
                with self.assertRaises(cache_fetch.Challenge):
                    cache_fetch.fetch_one("https://www.prestigeonline.com/hk/people/charmaine-sheh-living-life-unscripted/")


class RegulationAsia(unittest.TestCase):
    def test_login_wall_is_an_excerpt_with_no_text(self):
        # logged out, an article is its headline and "To get access to this article, please login …" with the
        # subscription pitch: no story text; the post is a paywalled excerpt (archive.today is tried)
        meta = {}
        with mock.patch.object(cache_fetch, "get", return_value=page("newcat_regulationasia.html")):
            text, _, _ = cache_fetch.fetch_one(
                "https://www.regulationasia.com/articles/fsb-finalises-sound-practices-for-responsible-ai-adoption", meta)
        self.assertEqual(text, "")
        self.assertEqual(meta.get("via"), "excerpt")


class MSN(unittest.TestCase):
    def test_article_from_detail_api(self):
        seen = []

        def get(url, **kw):
            seen.append(url)
            return page("newcat_msn_detail.json")
        with mock.patch.object(cache_fetch, "get", side_effect=get):
            text, author, _ = cache_fetch.fetch_one(
                "https://www.msn.com/zh-hk/money/科技/亞洲多個市場同步重挫/ar-AA2fE7T0?ocid=00000000")
        self.assertEqual(seen, ["https://assets.msn.com/content/view/v2/Detail/zh-hk/AA2fE7T0"])
        self.assertTrue(text.startswith("（法新社香港20日電）"), text[:30])
        self.assertIn("首爾和東京再度成為重災區", text)
        self.assertGreater(len(text), 500)


class IFengUS(unittest.TestCase):
    URL = "https://www.ifengus.com/article/给阿嬷的情书-9月4日北美上映-带家人一起-听见记忆里的乡音-1155"

    def test_article_from_api(self):
        seen = []

        def get(url, **kw):
            seen.append(url)
            return page("newcat_ifengus_text.json")
        meta = {}
        with mock.patch.object(cache_fetch, "get", side_effect=get):
            text, _, _ = cache_fetch.fetch_one(self.URL, meta)
        self.assertTrue(seen[0].startswith("https://www.ifengus.com/api/articles/%E7%BB%99"), seen[0])
        self.assertIn("一封跨越山海的家書", text)
        self.assertIn("影片由藍鴻春自編自導", text)
        self.assertNotIn("<p>", text)

    def test_video_only_article_has_no_text(self):
        with mock.patch.object(cache_fetch, "get", return_value=page("newcat_ifengus_video.json")):
            text, _, _ = cache_fetch.fetch_one("https://www.ifengus.com/article/紀實文學作家郭五一的行走人生-上-1067")
        self.assertEqual(text, "")


class SSM(unittest.TestCase):
    def test_pdf_behind_cloudflare_goes_to_the_browser(self):
        # 衛生局's press releases are PDFs; Cloudflare answers them 403 with its managed challenge
        u = "http://www.ssm.gov.mo/docs/37133/37133_e719bccebc764140b14e98abebd3e7e0_000_.pdf"
        self.assertTrue(cache_fetch.host(u).endswith(cache_fetch.BROWSER_HOSTS))
        err = urllib.error.HTTPError(u, 403, "Forbidden", {}, None)
        with mock.patch.object(cache_fetch, "get_bytes", side_effect=err):
            with self.assertRaises(cache_fetch.Challenge):
                cache_fetch.fetch_one(u)


    def test_browser_reads_the_pdf_from_the_sites_page(self):
        # the browser opens the site's home page (the challenge), then the page fetches the PDF; right after the
        # challenge the page may still navigate ("Execution context was destroyed"): tried again
        import pymupdf
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Health Bureau press release text")
        data = doc.tobytes()
        pg = mock.Mock(url="https://www.ssm.gov.mo/")
        pg.evaluate.side_effect = [Exception("Execution context was destroyed"), {"status": 200, "bytes": list(data)}]
        with mock.patch("browser_fetch.cleared", return_value=True), mock.patch.object(cache_fetch.time, "sleep"):
            t = cache_fetch.browser_pdf(pg, "http://www.ssm.gov.mo/docs/37133/37133_e7_000_.pdf")
        pg.goto.assert_called_once()
        self.assertEqual(pg.goto.call_args[0][0], "http://www.ssm.gov.mo/")
        self.assertEqual(pg.evaluate.call_args[0][1], "https://www.ssm.gov.mo/docs/37133/37133_e7_000_.pdf")
        self.assertIn("Health Bureau press release text", t)


class Thomasnet(unittest.TestCase):
    def test_slow_and_its_403_is_a_pause(self):
        # read 2 s apart it answered 403 to 15 of 57 pages in a row (2026-10-20 19:23), 200 to the same pages later
        self.assertIn("thomasnet.com", cache_fetch.SLOW_SITES)
        u = "https://www.thomasnet.com/insights/worlds-longest-tunnels/"
        e403 = urllib.error.HTTPError(u, 403, "Forbidden", {}, None)
        r = mock.Mock(status_code=403, text="")
        with mock.patch.object(cache_fetch.sources, "fetch", side_effect=e403), \
                mock.patch("curl_cffi.requests.get", return_value=r), mock.patch.object(cache_fetch, "slow"):
            with self.assertRaises(urllib.error.HTTPError) as c:
                cache_fetch.get(u)
        self.assertEqual(c.exception.code, 429)


class Plataforma(unittest.TestCase):
    def test_wordpress_api_for_a_walled_site(self):
        # a catalogue site listed by its REST API on the residential proxy (kind wp_json; cache_fetch.WP_API_HOSTS):
        # its pages answer 403 to the cluster and the ISP IP, /wp-json/wp/v2/posts?slug=… answers on every route.
        # Plataforma was such a site (its saved answer is the fixture); the catalogue reads it otherwise now
        # (sites.json), so the host is given here
        u = "https://www.plataformamedia.com/2026/10/20/macau-regista-nove-mortos-nas-estradas-em-apenas-nove-meses/"
        seen = []

        def get(url, **kw):
            seen.append(url)
            return page("newcat_plataforma_wp.json")
        meta = {}
        with mock.patch.object(cache_fetch, "get", side_effect=get), \
                mock.patch.object(cache_fetch, "WP_API_HOSTS", ("plataformamedia.com",)):
            text, _, _ = cache_fetch.fetch_one(u, meta)
        self.assertEqual(seen, ["https://www.plataformamedia.com/wp-json/wp/v2/posts?slug="
                                "macau-regista-nove-mortos-nas-estradas-em-apenas-nove-meses&_fields=content,date_gmt"])
        self.assertTrue(text.startswith("Macau registou mais de 10.300 acidentes"), text[:50])
        self.assertEqual(meta.get("published"), cache_fetch._when("2026-10-19T17:38:15+00:00"))


if __name__ == "__main__":
    unittest.main()
