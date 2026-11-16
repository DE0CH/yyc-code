#!/usr/bin/env python3
"""Readers for Google News results whose pages had no text (2026-10-20), and hosts that refuse the cluster's IP are read through YYC's IPRoyal ISP IP (cache_fetch.ISP_HOSTS), one client at a
time (sites_fetch.isp_lock, the 'yyc youtube' lock), by the 30-minute full-text job, never by fulltext_now.

2026-10-20: 自由時報 news.ltn.com.tw and 翻爆 turnnewsapp.com answer the cluster's WARP IP with nginx 403 and the
ISP IP with the page (200, 215 KB / 97 KB): their posts (found through Google News) kept no text.

風火時報 flamingwheels.online (a 2.3 KB jQuery shell: the story comes from its POST API nowall_ct_news_find.do with
the id hidden in _FgID, a base64 with its own alphabet) and TDM tdm.com.mo (a Vue shell titled "TDM": GET
/api/v1.0/news/<id>) are read from their APIs.

    python3 tests/test_fulltext_routes.py
"""
import contextlib, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_fetch, fulltext_now, sites_fetch  # noqa: E402

PAGE = "<html><body><article>" + "".join(f"<p>邱垂正表示中共介選已是赤裸裸地進行，這是第{i}段內文。</p>" for i in range(8)) + "</article></body></html>"


class IspRoute(unittest.TestCase):
    def test_ltn_through_the_isp_ip_under_the_lock(self):
        held = []

        @contextlib.contextmanager
        def lock():
            held.append(True)
            yield
            held.append(False)

        def fetch(url, proxy=None, isp=False, **kw):
            self.assertEqual((proxy, isp, held[-1:]), ("http://isp:1", True, [True]))
            return PAGE

        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp:1"}), mock.patch.object(sites_fetch, "isp_lock", lock), \
                mock.patch.object(cache_fetch.sources, "fetch", side_effect=fetch):
            for u in ("https://news.ltn.com.tw/news/politics/breakingnews/5611937",
                      "https://turnnewsapp.com/livenews/politicsv3/20261020003017-260407"):
                text, _, _ = cache_fetch.fetch_one(u)
                self.assertIn("赤裸裸", text)
        self.assertEqual(held, [True, False, True, False])

    def test_not_on_sight(self):
        p = {"url": "https://news.ltn.com.tw/news/politics/breakingnews/5611937", "source_type": "News", "text": "",
             "published": "2026-10-20T10:00+08:00", "cached_at": None, "thread": None}
        self.assertFalse(fulltext_now.wanted(p))  # the 30-minute job (which holds ISP_PROXY) reads it


class Apis(unittest.TestCase):
    def test_flamingwheels_id(self):
        self.assertEqual(cache_fetch.fg_id("aKE9AHI2BRMz"), "156463")
        self.assertEqual(cache_fetch.fg_id("aKE9AHI2BRSw"), "156410")

    def test_flamingwheels(self):
        calls = []

        def fetch(url, data=None, **kw):
            calls.append((url, data))
            return '{"title": "t", "content": "第一段內文。<br/>第二段內文，講述香港政府最新措施。<br/>", "createDate": "2026-10-19 06:30:58"}'
        with mock.patch.object(cache_fetch.sources, "fetch", side_effect=fetch):
            meta = {}
            text, _, _ = cache_fetch.fetch_one("https://www.flamingwheels.online/news-view.html?_FgID=aKE9AHI2BRMz", meta)
        self.assertEqual(calls, [("https://www.flamingwheels.online/nowall_ct_news_find.do", b"id=156463")])
        self.assertEqual(text, "第一段內文。\n第二段內文，講述香港政府最新措施。")
        self.assertEqual(meta.get("published"), "2026-10-19T06:30+08:00")

    def test_tdm(self):
        api = ('{"data": {"detail": {"title": "t", "date": "10/20/2026 11:21:00", '
               '"content": "<p>澳門電台消息：施政報告提出多項措施。</p><p>第二段。</p>"}}}')
        with mock.patch.object(cache_fetch, "get", return_value=api) as g:
            meta = {}
            text, _, _ = cache_fetch.fetch_one("https://www.tdm.com.mo/zh-hant/news-detail/1252047?SpecialId=Policy_Address", meta)
        g.assert_called_once_with("https://www.tdm.com.mo/api/v1.0/news/1252047")
        self.assertEqual(text, "澳門電台消息：施政報告提出多項措施。\n第二段。")
        self.assertEqual(meta.get("published"), "2026-10-20T11:21+08:00")


class WriteShell(unittest.TestCase):
    """凡科 (website.xin) sites (澳門法治報 macaulawnews.com, 2026-10-20: 124 posts, every page read empty): the page is a
    1.4 KB shell whose body is a script on img.website.xin that document.write()s the whole page"""
    SHELL = ("<!DOCTYPE html><html class='smart-design-mode'><head><title>澳门杭州联谊会就职典礼-澳門法治報</title></head>"
             "<body id='smart-body'><script src='https://img.website.xin/pubsf/1/2/cdn-static-pages/newsinfo/pc/"
             "3214004_zh-cn.nocookie.html.Body.js?version=1'></script></body></html>")
    BODY = ("document.write('\\u003cdiv class=\\\"a\\\"\\u003e" + "".join(
        f"\\u003cp\\u003e本報訊 澳門杭州聯誼會第三屆理監事就職典禮在澳門隆重舉行，這是第{i}段。\\u003c/p\\u003e" for i in range(6))
        + "\\u003c/div\\u003e\\r\\n');")

    def test_body_script(self):
        def get(url, **kw):
            return self.BODY if url.startswith("https://img.website.xin/") else self.SHELL
        with mock.patch.object(cache_fetch, "get", side_effect=get), mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp:1"}):
            text, _, _ = cache_fetch.fetch_one("https://macaulawnews.com/newsinfo/3214004.html")
        self.assertIn("第三屆理監事就職典禮", text)
        self.assertIn("第5段", text)


class RecaptchaWall(unittest.TestCase):
    """a small page that is only a reCAPTCHA wall (uwants.com on the ISP IP, 2026-10-20, which is why its pages go
    through yycstream since 2026-10-21: a 1.9 KB page titled with the
    site's name after 83 reads in a row) is a wall to retry later (HTTP 429), never an empty article"""
    WALL = ('<html lang="zh-HK"><head><title>Uwants.com - 香港動漫手遊討論平台</title><link rel="stylesheet" '
            'href="https://www.uwants.com/css/recaptcha.css" /><script src="https://www.google.com/recaptcha/api.js">'
            '</script></head><body><form><div class="g-recaptcha" data-sitekey="x"></div></form></body></html>')

    def test_wall_is_retried(self):
        import urllib.error
        with mock.patch.object(cache_fetch, "get", return_value=self.WALL), mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp:1"}), \
                mock.patch.object(cache_fetch.resi, "url", lambda c="hk": "http://resi:1"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                cache_fetch.fetch_one("https://www.uwants.com/viewthread.php?tid=20568167")
        self.assertEqual(e.exception.code, 429)


if __name__ == "__main__":
    unittest.main()
