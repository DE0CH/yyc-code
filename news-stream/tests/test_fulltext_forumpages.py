#!/usr/bin/env python3
"""Full text for the Forum-type posts that are no thread (fulltext_fetch.forum_page, 2026-10-20): the page reader gives
the opening post, not the forum's disclaimer or footer. Fixtures are real pages (scripts, styles and images stripped)
under fixtures/fulltext/forumpages_*; no network.

    python3 tests/test_fulltext_forumpages.py
"""
import json, os, re, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cache_fetch, sites_fetch, sources  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "fulltext")


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def read(url, body):
    """fetch_one(url) with get() answering body → (text, author, engagement, the URLs asked for)"""
    asked = []

    def get(u, *a, **k):
        asked.append(u)
        return body

    # cyclub is on the ISP IP (sites.json "proxy": "isp"), uwants' pages on yycstream ("page_proxy": "hk")
    with mock.patch.object(cache_fetch, "get", get), mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp.invalid:1"}), \
            mock.patch.object(cache_fetch.resi, "url", lambda c="hk": "http://resi.invalid:1"):
        return cache_fetch.fetch_one(url) + (asked,)


class Discuz(unittest.TestCase):
    """a Discuz! thread page: the opening post is the first postmessage_<pid>; the generic extractor took the forum's
    「重要聲明」 disclaimer (uwants, xocat) or the page's footer (hkitalk)"""

    def test_uwants_first_post_without_attachment_menus(self):
        t, _, _, _ = read("https://game.uwants.com/viewthread.php?tid=20561672", page("forumpages_uwants_game.html"))
        self.assertTrue(t.startswith("從零開始的傳奇正式降臨！《幪面超人古迦 25 週年紀念：超 Kuuga 展》"), t[:80])
        self.assertIn("近百款日本直送及香港限定周邊引爆收藏熱潮", t)
        self.assertNotIn("重要聲明", t)
        self.assertNotIn("ALR11071.JPG", t)  # an attachment's hidden menu (file name, size, upload time)
        self.assertNotIn("我去左, 現場放好多劇集片睇返", t)  # the first reply

    def test_uwants_edit_note_is_not_text(self):
        # a picture-only post: its 「[ 本帖最後由 … 編輯 ]」 note is all the post holds, never its text
        t, _, _, _ = read("https://www.uwants.com/viewthread.php?tid=20568207", page("forumpages_uwants_edited.html"))
        self.assertEqual(t, "")

    def test_xocat_first_post_without_the_ad_slot(self):
        t, _, _, _ = read("https://www.xocat.com/f/thread-228987-1-1.html", page("forumpages_xocat.html"))
        self.assertTrue(t.startswith("【内容简介】"), t[:40])
        self.assertIn("沈媚三十八岁。保养到令人发指的程度。", t)
        self.assertNotIn("Powered by Discuz!", t)

    def test_hkitalk_short_post_is_the_post(self):
        t, _, _, _ = read("https://www.hkitalk.net/HKiTalk2/thread-2335346-1-1.html", page("forumpages_hkitalk.html"))
        self.assertEqual(t, "松本清\n第22間分店進駐青衣城\n10月29日開幕")

    def test_cyclub_video_post_has_no_text_but_the_title(self):
        # 香港公營房屋討論區 shows the subject inside the post (#threadtitle); this post is a YouTube video only
        t, _, _, _ = read("http://cyclub.happyhongkong.com/viewthread.php?tid=398835", page("forumpages_cyclub.html"))
        self.assertEqual(t, "")


    def test_racingworld_old_discuz_post_cell(self):
        # 賽馬世界 (Discuz! 4): the post is the td.line that holds its <a name="pid…">; the footer was taken
        t, _, _, _ = read("http://racingworld.no-ip.org/viewthread.php?tid=195038", page("forumpages_racingworld.html"))
        self.assertEqual(t, "https://db.netkeiba.com/horse/2024103443/\n出價: 100m")


class MyBB(unittest.TestCase):
    def test_forum4hk_first_post_body(self):
        # 香港論壇: the generic extractor took "Users browsing this thread: 1 Guest(s)"
        t, _, _, _ = read("https://forum4hk.com/showthread.php?tid=14634607", page("forumpages_forum4hk.html"))
        self.assertTrue(t.startswith("(38629) 美食新聞報道｜西營盤米芝蓮推介潮州滷水鵝"), t[:60])
        self.assertIn("轉載｜www.right4hk.blogspot.com", t)
        self.assertNotIn("Users browsing", t)


class ULifestyleCommunity(unittest.TestCase):
    def test_post_text_from_the_flight_data(self):
        # www.ulifestyle.com.hk/community/detailpost: a Next.js page; the post is the JSON-LD Article "text" inside the
        # flight data (the HTML has only the ~100-character description)
        t, _, _, _ = read("https://www.ulifestyle.com.hk/community/detailpost/b46fcd49-720f-41f8-b9de-e2159bb2384e/"
                          "%E7%94%9F%E6%B4%BB/rebeccalin1108/660476", page("forumpages_ulifestyle_community.html"))
        self.assertTrue(t.startswith("熱愛路跑、球類或重訓的族群，難免遇上肌腱拉傷、網球肘"), t[:60])
        self.assertIn("恢復期間最常見的失誤，是在疼痛稍減時就急著恢復原本強度。", t)
        self.assertGreater(len(t), 600)

    def test_post_quoting_html_hashtags(self):
        # a post whose text holds its hashtags as <a href=\"…\"> links: the flight data read as sent, the tags taken out
        t, _, _, _ = read("https://www.ulifestyle.com.hk/community/detailpost/d01e21cc-d958-47f7-81e8-3cf868c2b21f/"
                          "%E7%BE%8E%E5%A6%9D%E6%99%82%E5%B0%9A/Soniatung/1002821",
                          page("forumpages_ulifestyle_community_tags.html"))
        self.assertTrue(t.startswith("最近頭髮乾旱雜草一樣"), t[:40])
        self.assertIn("Step2: Loretta 保濕護髮素", t)
        self.assertIn("#lorettahk", t)
        self.assertNotIn("<a href", t)


class HongKongCard(unittest.TestCase):
    def test_thread_from_its_json(self):
        # /forum/show/<id> is a Vue shell; its data is /vue/forum/threads/<id>
        t, author, eng, asked = read("https://www.hongkongcard.com/forum/show/51898",
                                     page("forumpages_hongkongcard_51898.json"))
        self.assertEqual(asked, ["https://www.hongkongcard.com/vue/forum/threads/51898"])
        self.assertTrue(t.startswith("are Apple gift cards ways out of stock??"), t)
        self.assertTrue(t.endswith("they are always out of stock… so bad"), t)
        self.assertEqual(author, "cF")
        self.assertEqual(eng.get("replies"), 1)


class HKHorseDB(unittest.TestCase):
    def test_rss_links_read_on_the_cseh_path(self):
        # the RSS (backend.php) links /article.php?sid=N, a 404; the article is /cseh/article.php?sid=N
        _, _, _, asked = read("http://www.hkhorsedb.com/article.php?sid=616", "<html><body><p>x</p></body></html>")
        self.assertEqual(asked, ["http://www.hkhorsedb.com/cseh/article.php?sid=616"])

    def test_big5_page_decoded_by_its_charset(self):
        with open(os.path.join(FIX, "forumpages_hkhorsedb_616_big5.html"), "rb") as f:
            raw = f.read()

        class R:
            status = 200
            headers = {"Content-Type": "text/html; charset=BIG5"}

            def read(self):
                return raw

        opener = mock.Mock()
        opener.open.return_value = R()
        with mock.patch("urllib.request.build_opener", return_value=opener), mock.patch.object(cache_fetch, "pace"):
            p = cache_fetch.get("http://www.hkhorsedb.com/cseh/article.php?sid=616")
        t = cache_fetch.article_text(p)
        self.assertIn("香港五歲佳駟「喜蓮戰略」繼一月尾奪得港澳盃之後", t)


class Discuss(unittest.TestCase):
    def test_thread_links_read_on_the_archiver(self):
        # 香港討論區 links as Google News filed them: the archiver page holds the opening post (discuss_text)
        body = ('<html><body><div class="archiver_post"><cite>作者甲</cite><div class="archiver_postbody">'
                '開帖的內容第一段<br>第二段</div></div></body></html>')
        with mock.patch.object(cache_fetch.resi, "url", return_value="http://proxy.invalid:1"):
            t, author, _, asked = read("https://www.discuss.com.hk/viewthread.php?tid=32329208&utm_source=x", body)
        self.assertEqual(asked, ["https://www.discuss.com.hk/archiver/?tid-32329208.html"])
        self.assertIn("開帖的內容第一段", t)
        self.assertEqual(author, "作者甲")


class Listings(unittest.TestCase):
    """listing pages filed as posts: a board index, a site's section pages"""

    def site(self, sid):
        return next(s for s in sites_fetch.load() if s["id"] == sid)

    def test_dcfever_board_index_lists_threads_not_boards(self):
        # the forum index (index.php) links only the boards (list.php?f=N): those were filed as posts
        rx = self.site("hk_dcfever")["href"]
        self.assertFalse(re.search(rx, "list.php?f=10"))
        self.assertTrue(re.search(rx, "read.php?f=10&i=65998&t=65998"))

    def test_hkhorsedb_board_index_lists_topics_not_boards(self):
        rx = self.site("hk_hkhorsedb")["href"]
        self.assertFalse(re.search(rx, "modules.php?op=modload&name=Forum&file=viewforum&forum=6"))
        self.assertTrue(re.search(rx, "modules.php?op=modload&name=Forum&file=viewtopic&topic=13578&forum=6&2"))

    def test_asiaxpat_sitemap_keeps_posts_only(self):
        keep = self.site("hk_hongkong_asiaxpat_2")["keep"]
        b = "https://hongkong.asiaxpat.com/"
        for u in ("classifieds/3490", "jobs/1201", "properties/3947", "domestic-helper-profiles/2672", "cars/12",
                  "forums/work-visa", "articles/why-every-expat-in-hong-kong-needs-a-trusted-law-firm-on-their-side"):
            self.assertTrue(re.search(keep, b + u), u)
        for u in ("directories/education", "about", "team", "privacy", "contact", "jobs", "events/today",
                  "domestic-helpers/jobs", "domestic-helpers/pricing", "articles", "forums", ""):
            self.assertFalse(re.search(keep, b + u), u)


if __name__ == "__main__":
    unittest.main()
