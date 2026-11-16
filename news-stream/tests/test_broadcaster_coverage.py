#!/usr/bin/env python3
"""Every item the broadcasters and their own channels publish (coverage audit, 2026-10-15), offline:

- RTHK Chinese: all five express-news feeds (international and sport were not polled: 40 stories missed on 13-14 Oct);
- Now 新聞: every category its API has (entertainment 500, life 501, technology 502) and Now Sports' own API
  (24 stories missed on 13-14 Oct, plus every sports story);
- 新城: every section page (finance and English were not polled: 35 of their 40 newest missing), one URL per story
  whatever page lists it;
- 經濟通: its whole latest-news list (~450 stories a day; only the ~40 editor's picks came in);
- YouTube: each channel's uploads playlist (videos, Shorts and past live streams; the /videos tab has no Shorts or
  streams: 124 uploads missed on 13-14 Oct), no separate /streams sources.

    python3 tests/test_broadcaster_coverage.py
"""
import json, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources, sources_extra  # noqa: E402,F401  (sources_extra adds EVERY entries)


def spec(sid):
    return next(s for s in sources.SOURCES + sources_extra.EXTRA if s[0] == sid)


def sids():
    return {s[0] for s in sources.SOURCES + sources_extra.EXTRA}


class RTHK(unittest.TestCase):
    def test_every_chinese_feed(self):
        urls = {spec(s)[2] for s in sids() if s.startswith("rthk_") and not s.startswith("rthk_en_") and s != "rthk_youtube"}
        for c in ("clocal", "cfinance", "greaterchina", "cinternational", "csport"):
            self.assertIn(f"https://rthk.hk/rthk/news/rss/c_expressnews_{c}.xml", urls)


NOW_LIST = json.dumps([{"newsId": "665901", "title": "Hexed｜迪士尼新片預告", "publishDate": 1791966600000,
                        "categoryName": "娛樂"}])
NOW_SPORTS = json.dumps([{"newsId": "39812933442", "publishDate": 1791990474000, "headlineChi": "莎芭列卡膝傷困擾 武漢網賽一輪遊",
                          "summaryChi": "【Now Sports】失去世界一姐之位的白羅斯球手莎芭列卡……", "sportType": "網球"}])


class Now(unittest.TestCase):
    def test_every_category(self):
        cats = {spec(s)[2].split("category=")[1].split("&")[0] for s in sids() if s.startswith("now_") and "category=" in spec(s)[2]}
        self.assertEqual(cats, {"119", "120", "121", "500", "501", "502"})

    def test_category_story_url(self):
        with mock.patch.object(sources, "fetch", return_value=NOW_LIST):
            got = sources.fetch_source(spec("now_ent"))
        self.assertEqual(got[0]["url"], "https://news.now.com/home/local/player?newsId=665901")  # one URL form per id

    def test_sports(self):
        self.assertIn("sportsapi.now.com", spec("now_sports")[2])
        with mock.patch.object(sources, "fetch", return_value=NOW_SPORTS):
            got = sources.fetch_source(spec("now_sports"))
        self.assertEqual(got[0]["title"], "莎芭列卡膝傷困擾 武漢網賽一輪遊")
        self.assertEqual(got[0]["url"], "https://news.now.com/home/sports/player?newsId=39812933442")
        self.assertEqual(got[0]["section"], "網球")
        self.assertTrue(got[0]["text"].startswith("【Now Sports】"))
        self.assertEqual(got[0]["published"].timestamp(), 1791990474)


METRO_HOME = ('<a id="ContentPlaceHolder1_gvNews_lnkTitle_0" href="/news/details.aspx?NewsId=20261014232102&amp;page=0">'
              '本港新增病例</a>')
METRO_SECTION = ('<a id="ContentPlaceHolder1_gvNews_lnkTitle_0" href="/news/details.aspx?CategoryName=english&amp;'
                 'NewsId=20261014132458&amp;page=0">New Zealand extends strategic diesel reserve to August 2027</a>'
                 '<a id="ContentPlaceHolder1_gvNews_lnkTitle_1" href="/news/details.aspx?CategoryName=finance&amp;'
                 'NewsId=20261014232102&amp;page=0">本港新增病例</a>')


class Metro(unittest.TestCase):
    def test_every_section(self):
        urls = {spec(s)[2] for s in sids() if s.startswith("metroradio")}
        for sec in ("local", "china", "international", "finance", "eng"):
            self.assertIn(f"https://www.metroradio.com.hk/news/{sec}.aspx", urls)

    def test_one_url_per_story(self):
        with mock.patch.object(sources, "fetch", return_value=METRO_HOME):
            home = sources.fetch_source(spec("metroradio"))
        with mock.patch.object(sources, "fetch", return_value=METRO_SECTION):
            sec = sources.fetch_source(spec("metroradio_eng"))
        self.assertEqual(len(sec), 2)
        self.assertEqual(sec[1]["url"], home[0]["url"])  # the same story from the home page and a section page
        self.assertEqual(sec[0]["title"], "New Zealand extends strategic diesel reserve to August 2027")
        self.assertEqual(sec[0]["published"].isoformat(), "2026-10-14T13:24:58+08:00")


ETNET = ('<ul class="et-list"><li>\t<div class="content">\t\t<div class="h4">\t\t\t<a href="/www/tc/news/news-article.php?'
         'section=categorized&category=latest&newsid=20261014429" target="_blank"><h2 class="h4">馬建榮增持申洲國際(02313)85萬股'
         '，持股升至44.03%</h2>\t\t\t</a>\t\t</div>\t\t<div class="content-group">\t\t\t<p class="time">14/10/2026 19:06</p>'
         '\t\t</div>\t</div></li></ul>')


class Etnet(unittest.TestCase):
    def test_latest_list(self):
        self.assertIn("categorized_news.php", spec("etnet_latest")[2])
        self.assertIn("page=2", spec("etnet_latest_p2")[2])
        with mock.patch.object(sources, "fetch", return_value=ETNET):
            got = sources.fetch_source(spec("etnet_latest"))
        self.assertEqual(len(got), 1)
        # the editor's-picks RSS links the same story as home_categorized_news_detail.php?newsid=ETN361014429
        self.assertEqual(got[0]["url"], "http://www.etnet.com.hk/www/tc/news/home_categorized_news_detail.php?newsid=ETN361014429")
        self.assertEqual(got[0]["title"], "馬建榮增持申洲國際(02313)85萬股，持股升至44.03%")
        self.assertEqual(got[0]["published"].isoformat(), "2026-10-14T19:06:00+08:00")


class YouTube(unittest.TestCase):
    def test_uploads_playlists(self):
        yt = [s for s in sources.SOURCES if s[3] is sources.youtube]
        self.assertTrue(yt)
        for s in yt:
            self.assertRegex(s[2], r"^https://www\.youtube\.com/playlist\?list=UU[\w-]{22}$", s[0])
        self.assertFalse([s for s in sids() if s.endswith("_live")])
        self.assertIn("https://www.youtube.com/playlist?list=UU6of7UYhctnYmqABjUqzuxw", {s[2] for s in yt})  # RTHK

    def test_watch_leaves_our_channels_alone(self):
        import watch_fetch
        own = watch_fetch.own_youtube()
        self.assertIn("UC6of7UYhctnYmqABjUqzuxw", own)  # RTHK, read here from its uploads playlist
        self.assertIn("UCw6-y9jGJbfw6jLmtGqDPzQ", own)  # 香港商報


if __name__ == "__main__":
    unittest.main()
