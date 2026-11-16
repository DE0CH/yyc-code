#!/usr/bin/env python3
"""Full text of Macau catalogue sources, the part test_fulltext_macau.py leaves (2026-10-20). No network: real pages
saved under tests/fixtures/fulltext/macau_b_*, the fetches mocked.

  澳門城市大學           its certificate chain is incomplete (the list reads it unverified, its pages were not); the
                         story in many WordPress sections, the largest <p> cluster one of them
  保安部隊 / 保安司 /    the marked story block: 保安部隊's cross-heads, 保安司's first paragraphs (bare text), 聖保祿學校's
  聖保祿學校             SharePoint rich-text field (empty on a photo-only page: no text, not its footer address)
  澳門人才網 job853      bans an IP after ~28 pages 2 s apart: HTTP 403 「您的IP因異常請求已被臨時封禁」
  現代澳門日報           to the cluster's IP at times a 「请稍候…」 wall (wsidchk form): a challenge, never an article

    python3 tests/test_fulltext_macau_b.py
"""
import os, sys, unittest, urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_fetch, sources  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fulltext")


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()



class CataloguePages(unittest.TestCase):
    def fetch(self, url):
        seen = {}

        def fake(u, **kw):
            seen.update(kw, url=u)
            return "<html><body><p>" + "內文" * 200 + "</p></body></html>"
        with mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(cache_fetch, "pace"):
            cache_fetch.get(url)
        return seen

    def test_other_sites_verified(self):
        kw = self.fetch("https://www.gss.gov.mo/cht/newsdetail.aspx?masterid=404")
        self.assertNotEqual(kw.get("verify"), False)

    def test_unverified_tls_like_the_list(self):
        self.assertEqual(self.fetch("https://www.cityu.edu.mo/zh/07oct2026-01/").get("verify"), False)


class Bodies(unittest.TestCase):
    def read(self, url, name):
        with mock.patch.object(cache_fetch, "get", return_value=fx(name)):
            return cache_fetch.fetch_one(url)[0]

    def test_fsm_story_inside_the_page_form(self):
        t = self.read("https://www.fsm.gov.mo/psp/pspnews/showmsg_detail.aspx?newsid=3910", "macau_b_fsm_3910.html")
        self.assertTrue(t.startswith("國慶黃金周假期將至"), t[:60])
        self.assertIn("\n巡查熱點區域 維持市面秩序\n", t)  # the cross-heads too
        self.assertTrue(t.endswith("合理安排時間錯峰出行。"), t[-60:])

    def test_gss_first_paragraph_and_all(self):
        t = self.read("https://www.gss.gov.mo/cht/newsdetail.aspx?masterid=396", "macau_b_gss_396.html")
        self.assertTrue(t.startswith("今(16)日，為2026年全民國家安全教育展全面開放予公眾參觀的首日"), t[:60])
        self.assertIn("精耕細作，同心護澳", t)
        self.assertNotIn("版權所有", t)

    def test_esp_sharepoint_rich_text(self):
        t = self.read("https://www.esp.edu.mo/Secondary/Pages/2025-2026/Z31-x-2026-03.aspx", "macau_b_esp_z31.html")
        self.assertIn("在2月10日上午，聖保祿學校中學部洋溢著一片歡聲笑語", t)
        self.assertIn("\n剪紙藝術區\n", t)
        self.assertIn("讓古老智慧在新時代綻放更璀璨的光彩。", t)
        self.assertNotIn("Secondary Home", t)

    def test_esp_photo_only_page_has_no_text(self):  # never its footer address
        t = self.read("https://www.esp.edu.mo/Pages/2025-2026/Z05-x-2025-12.aspx", "macau_b_esp_photos_only.html")
        self.assertEqual(t, "")

    def test_cityu_every_section(self):
        t = self.read("https://www.cityu.edu.mo/zh/07oct2026-01/", "macau_b_cityu_07oct2026-01.html")
        self.assertTrue(t.startswith("這個學年，澳門城市大學的課堂上多了一群新面孔。"), t[:60])
        self.assertIn("Arman Eshraghi", t)
        self.assertIn("下一段同行的故事，或許就由你來書寫。", t)
        self.assertGreater(len(t), 4000)



class Job853(unittest.TestCase):
    def test_ip_ban_is_a_rate_limit(self):
        class R:
            status_code, text = 403, "您的IP因異常請求已被臨時封禁，請稍後訪問。"
        err = urllib.error.HTTPError("u", 403, "Access Denied", {}, None)
        with mock.patch.object(sources, "fetch", side_effect=err), mock.patch.object(cache_fetch, "pace"), \
                mock.patch.object(cache_fetch, "slow"), mock.patch("curl_cffi.requests.get", return_value=R()):
            with self.assertRaises(urllib.error.HTTPError) as c:
                cache_fetch.get("https://www.job853.com/personal/JobInfo.aspx?bid=347011")
        self.assertEqual(c.exception.code, 429)  # transient: the reader tries again later, never saves ""

    def test_slow_site_spacing(self):
        clock, sleeps = [100.0], []

        def sleep(s):
            sleeps.append(s)
            clock[0] += s
        cache_fetch._slow_next.clear()
        with mock.patch.object(cache_fetch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(cache_fetch.time, "sleep", side_effect=sleep):
            for _ in range(3):
                cache_fetch.slow("https://www.job853.com/personal/JobInfo.aspx?bid=1")
            cache_fetch.slow("https://www.gss.gov.mo/cht/default.aspx")  # not a slow site: no wait
        self.assertEqual(sum(sleeps), 2 * cache_fetch.SLOW_SITES["job853.com"])


class TodayMacaoWall(unittest.TestCase):
    def test_wall_is_a_challenge(self):
        self.assertTrue(cache_fetch.challenged(fx("macau_b_todaymacao_wall.html")))
        with mock.patch.object(cache_fetch, "get", return_value=fx("macau_b_todaymacao_wall.html")), \
                mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp:1"}):  # 現代澳門日報 is on the ISP IP (sites.json)
            with self.assertRaises(cache_fetch.Challenge):
                cache_fetch.fetch_one("https://todaymacao.com/%e9%a0%ad%e6%a2%9d%e6%96%b0%e8%81%9e-323/")

    def test_article_is_not(self):
        self.assertFalse(cache_fetch.challenged(fx("macau_b_gss_396.html")))


if __name__ == "__main__":
    unittest.main()
