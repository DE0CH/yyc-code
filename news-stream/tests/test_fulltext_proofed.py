#!/usr/bin/env python3
"""Full text of the sources Deyao proofed on the proof page (2026-10-21), fixed per source:

- 華富財經 Quamnet: the post page is a React shell; the stored text was its copyright footer. The post is its JSON API's
  item.body (cache_fetch.quamnet_text), and a 「1998-2024, Copyright …」 line is junk anywhere.
- uwants (香港討論區-uwants.com): its list reads on the ISP IP, where every thread page is a reCAPTCHA wall (1.9 KB), so
  no thread kept a text: its pages go through yycstream (sites.json "page_proxy"); its home page's links carry a new
  tr_h tracking hash on every read (one thread stored ~20 times): posts are stored without tracking parameters.
- 香港網絡 internet.com.hk (WordPress): a short story's entry-content block, not the truncated description.
- headline4hk.com: an aggregator (sites.json "aggregator"): its page's 「星島原文連結」 / og:url is followed into the
  corpus (follow.pointer) and the post carries the original's text (text_via "original", its URL the text's).
- Media OutReach (媒體拓展): the release is the innerband-content block (text between <br>s), not a "more news"
  card's English excerpt; a post link following stored as 〔連結〕host takes the page's headline when the page is read.

No network: pages are fixtures, reads are stubbed.
    TEST_DATABASE_URL=postgresql:///yyc_t_proofed python3 tests/test_fulltext_proofed.py
"""
import json, os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, corpus, follow, fulltext_fetch, fulltext_now, paywall, resi  # noqa: E402

FIX = os.path.join(HERE, "tests", "fixtures", "fulltext")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


QUAM = "https://www.quamnet.com/post/5h3KMtY9HrOXYKcMqTS7e"
UWANTS = ("https://www.uwants.com/viewthread.php?tid=20567904&utm_source=uwants_homepage&utm_medium=click"
          "&utm_campaign=hottopic_casuellife&tr_h=10267075866ad8398473c126_63131370")
INET = "https://internet.com.hk/hongkong/2026/10/11562/"
H4 = "https://www.headline4hk.com/article/20261020/stheadline/3628174/"
ST = "https://www.stheadline.com/politics/3628174"
MO = "https://www.media-outreach.com/news/hong-kong/2026/10/20/495435/x/"


class Quamnet(unittest.TestCase):
    def test_post_is_read_from_its_api(self):
        seen = []

        def get(url, proxy=None, headers=None, isp=False):
            seen.append(url)
            return fixture("proofed_quamnet_post.json")
        meta = {}
        with mock.patch.object(cache_fetch, "get", get):
            text, _, _ = cache_fetch.fetch_one(QUAM, meta)
        self.assertEqual(seen, ["https://www.quamnet.com/api/v1/post/5h3KMtY9HrOXYKcMqTS7e"])
        self.assertTrue(text.startswith("行政長官李家超在亞太經合組織APEC財長會議歡迎晚宴致辭時表示"))
        self.assertIn("享負盛名的維多利亞港。", text)
        self.assertGreater(text.count("\n"), 3)  # one paragraph a line
        self.assertNotIn("Copyright", text)
        self.assertEqual(meta["published"], "2026-10-21T08:17+08:00")

    def test_a_paid_post_drops_the_footer_an_old_read_stored(self):
        corpus.db().execute("TRUNCATE posts CASCADE")
        pid = corpus.ingest([{"url": "https://www.quamnet.com/post/paid1", "title": "付費文章", "sid": "hk_quamnet",
                              "source": "華富金錢"}])[0]
        junk = ("1998-2024, Copyright ©2024 Quam.net Financial Media Limited and Quam.net Financial Media (Holdings) "
                "Limited (Incorporated in Hong Kong with limited liability). All Rights Reserved.")
        corpus.set_text(pid, junk, via="excerpt")
        fulltext_fetch.save(pid, "", via="excerpt")  # the API's paid item: no body
        self.assertEqual(corpus.db().execute("SELECT text FROM posts WHERE id=%s", (pid,)).fetchone()[0], "")

    def test_the_page_alone_has_no_story_and_its_footer_is_junk(self):
        self.assertNotIn("Copyright", cache_fetch.article_text(fixture("proofed_quamnet_page.html")))
        self.assertTrue(cache_fetch.junk("1998-2024, Copyright ©2024 Quam.net Financial Media Limited and Quam.net "
                                         "Financial Media (Holdings) Limited (Incorporated in Hong Kong)"))


class Uwants(unittest.TestCase):
    def test_thread_pages_go_through_yycstream_not_the_isp_ip(self):
        self.assertIn("uwants.com", cache_fetch.PROXY_HOSTS)
        self.assertNotIn("uwants.com", cache_fetch.ISP_HOSTS)
        calls = []

        def get(url, proxy=None, headers=None, isp=False):
            calls.append((proxy, isp))
            return fixture("proofed_uwants_thread.html")
        with mock.patch.object(cache_fetch, "get", get), mock.patch.object(resi, "url", lambda c="hk": "socks5://resi-" + c):
            text, _, _ = cache_fetch.fetch_one("https://www.uwants.com/viewthread.php?tid=20567904")
        self.assertEqual(calls, [("socks5://resi-hk", False)])
        self.assertTrue(text.startswith("大埔宏福苑業主接受政府收購業權的限期"))
        self.assertIn("只要有得住，就已經滿意", text)

    def test_a_refused_exit_is_tried_again_from_another(self):
        import urllib.error
        answers = [urllib.error.HTTPError("u", 403, "Forbidden", {}, None), fixture("proofed_uwants_thread.html")]

        def get(url, proxy=None, headers=None, isp=False):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        with mock.patch.object(cache_fetch, "get", get), mock.patch.object(resi, "url", lambda c="hk": "socks5://resi"):
            text, _, _ = cache_fetch.fetch_one("https://www.uwants.com/viewthread.php?tid=20567904")
        self.assertTrue(text.startswith("大埔宏福苑業主"))

    def test_a_failed_read_keeps_the_text_a_post_has(self):
        corpus.db().execute("TRUNCATE posts CASCADE")
        pid = corpus.ingest([{"url": "https://www.uwants.com/viewthread.php?tid=1", "title": "業主積極響應",
                              "sid": "hk_uwants", "source": "uwants"}])[0]
        fulltext_fetch.save(pid, "大埔宏福苑業主接受政府收購業權的限期，已於8月31日下午5時屆滿。" * 5)
        fulltext_fetch.save(pid, "", via="page", failed=True)
        r = corpus.db().execute("SELECT text, cached_at FROM posts WHERE id=%s", (pid,)).fetchone()
        self.assertTrue(r["text"].startswith("大埔宏福苑業主"))
        self.assertIsNotNone(r["cached_at"])
        empty = corpus.ingest([{"url": "https://www.uwants.com/viewthread.php?tid=2", "title": "無內文",
                                "sid": "hk_uwants", "source": "uwants"}])[0]
        fulltext_fetch.save(empty, "", via="page", failed=True)
        self.assertIsNotNone(corpus.db().execute("SELECT cached_at FROM posts WHERE id=%s", (empty,)).fetchone()[0])

    def test_tracking_parameters_are_not_part_of_the_address(self):
        self.assertEqual(corpus.strip_tracking(UWANTS), "https://www.uwants.com/viewthread.php?tid=20567904")
        self.assertEqual(corpus.strip_tracking("https://a.hk/n?id=3&utm_source=rss_feed#x"), "https://a.hk/n?id=3#x")
        self.assertEqual(corpus.strip_tracking("https://a.hk/n?id=3"), "https://a.hk/n?id=3")
        other = UWANTS.replace("10267075866ad8398473c126_63131370", "99999")
        self.assertEqual(corpus.url_key(UWANTS), corpus.url_key(other))

    def test_the_poll_stores_one_post_per_thread(self):
        import app
        reads = [[{"url": UWANTS, "title": "業主積極響應 宏福苑收購安排獲廣泛認同", "sid": "hk_uwants",
                   "source": "香港討論區-uwants.com"}],
                 [{"url": UWANTS.replace("63131370", "1"), "title": "業主積極響應 宏福苑收購安排獲廣泛認同",
                   "sid": "hk_uwants", "source": "香港討論區-uwants.com"}]]
        got = []
        for items in reads:
            with mock.patch.object(app.sources, "fetch_all", lambda extra, items=items: ([dict(i) for i in items], {})), \
                    mock.patch.object(app, "after_poll", lambda got_, fresh: got.append([i["url"] for i in fresh])), \
                    mock.patch.object(app.sources, "scheduled", lambda extra: set()):
                app.poll_once()
                app.state["after"].join()
        self.assertEqual(got, [["https://www.uwants.com/viewthread.php?tid=20567904"], []])

    def test_clean_tracking_urls_merges_stored_copies(self):
        c = corpus.db()
        c.execute("TRUNCATE posts CASCADE")
        a = corpus.ingest([{"url": UWANTS, "title": "業主積極響應", "sid": "hk_uwants", "source": "uwants"}])[0]
        b = corpus.ingest([{"url": UWANTS.replace("63131370", "2"), "title": "業主積極響應", "sid": "hk_uwants",
                            "source": "uwants"}])[0]
        s = corpus.ingest([{"url": "https://www.scmp.com/news/a/article/1/x?utm_source=rss_feed", "title": "SCMP story",
                            "sid": "scmp_news", "source": "SCMP"}])[0]
        got = corpus.clean_tracking_urls()
        self.assertEqual(got["renamed"], 2)
        self.assertEqual(got["merged"], 1)
        rows = {r["id"]: r["url"] for r in c.execute("SELECT id, url FROM posts")}
        self.assertEqual(rows, {a: "https://www.uwants.com/viewthread.php?tid=20567904",
                                s: "https://www.scmp.com/news/a/article/1/x"})
        self.assertNotIn(b, rows)


class WordPress(unittest.TestCase):
    def test_short_story_is_its_entry_content(self):
        text = cache_fetch.article_text(fixture("proofed_internet_hk.html"))
        self.assertEqual(text.split("\n"), [
            "行政長官李家超出席一個以粵港澳大灣區為主題的論壇，提及今日在香港揭幕的APEC財政部長會。",
            "李家超表示，特區政府會把握財長會的機會，介紹香港作為大灣區中心城市，聯繫國家機遇和國際機遇的疊加優勢。",
            "編輯：梁家奇"])


class WordPressFamily(unittest.TestCase):
    """the family-wide check (2026-10-21, 544 WordPress posts against trafilatura's reading of the live page): stories
    split into several blocks inside the one entry-content block were cut to one block (technow, menstyle.be,
    flyformiles, u4get, CNET); a MemberPress paywall's page gave a sidebar story (InsuranceAsia News)"""

    def test_story_in_several_blocks_is_the_whole_entry_content(self):
        text = cache_fetch.article_text(fixture("proofed_wp_technow.html"))
        self.assertTrue(text.startswith("天氣終於轉涼，明明想專心工作"))
        self.assertIn("實際喝喝看！", text)
        self.assertTrue(text.endswith("詳細資訊請參閱 Red Bull 官方網站。"))

    def test_memberpress_wall_is_its_excerpt(self):
        page = fixture("proofed_wp_memberpress.html")
        meta = {}
        with mock.patch.object(cache_fetch, "get", lambda url, proxy=None, headers=None, isp=False: page):
            text, _, _ = cache_fetch.fetch_one("https://insuranceasianews.com/chubb-appoints-kathleen-warden-as-head-of-anz-pc/", meta)
        self.assertTrue(text.startswith("Kathleen Warden has returned to Chubb"))
        self.assertNotIn("BlackRock", text)
        self.assertEqual(meta["via"], "excerpt")


class MediaOutreach(unittest.TestCase):
    def test_the_release_not_a_more_news_card(self):
        page = fixture("proofed_media_outreach_zh.html")
        with mock.patch.object(cache_fetch, "get", lambda url, proxy=None, headers=None, isp=False: page), \
                mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp"}):
            text, _, _ = cache_fetch.fetch_one(MO)
        self.assertTrue(text.startswith("成立首年碩果累累"))
        self.assertIn("香港特區行政長官李家超撥冗出席並擔任主禮嘉賓", text)
        self.assertIn("2026年12月舉辦國際調解院論壇", text)
        self.assertNotIn("graced the occasion", text)
        self.assertEqual(fulltext_fetch.page_title(page, MO), "國際調解院舉辦一周年慶祝招待會")

    def test_a_link_title_takes_the_page_headline(self):
        corpus.db().execute("TRUNCATE posts CASCADE")
        pid = corpus.ingest([{"url": MO, "title": corpus.LINK_TITLE + "media-outreach.com", "sid": "hk_media_outreach_2",
                              "source": "媒體拓展"}])[0]
        cache_fetch._seen.page = fixture("proofed_media_outreach_zh.html")
        fulltext_fetch.save(pid, "國際調解院今日在香港總部舉辦一周年慶祝招待會。" * 10, url=MO)
        self.assertEqual(corpus.db().execute("SELECT title FROM posts WHERE id=%s", (pid,)).fetchone()[0],
                         "國際調解院舉辦一周年慶祝招待會")

    def test_a_wordless_title_takes_the_page_headline(self):
        """a sitemap row whose slug is a thread id (香港高登's 「8099523」) takes the page's headline when read"""
        corpus.db().execute("TRUNCATE posts CASCADE")
        url = "https://forum.hkgolden.com/thread/8099523"
        pid = corpus.ingest([{"url": url, "title": "8099523", "sid": "hk_forum_hkgolden", "source": "香港高登"}])[0]
        cache_fetch._seen.page = ('<html><head><meta property="og:title" content="何永賢離任聲明：願意承擔政治責任">'
                                  '</head><body></body></html>')
        fulltext_fetch.save(pid, "宏福苑｜何永賢離任聲明：願意承擔政治責任 希望逝者可以安息。" * 5, url=url)
        self.assertEqual(corpus.db().execute("SELECT title FROM posts WHERE id=%s", (pid,)).fetchone()[0],
                         "何永賢離任聲明：願意承擔政治責任")


class Aggregator(unittest.TestCase):
    def setUp(self):
        c = corpus.db()
        c.execute("TRUNCATE posts, follow_links, follow_scan CASCADE")
        follow._outlets.clear()
        follow._post_hosts.update(at=0, hosts={})
        self.addCleanup(mock.patch.stopall)

    def test_headline4hk_names_its_original(self):
        self.assertTrue(cache_fetch.aggregator(H4))
        page = fixture("proofed_headline4hk.html")
        meta = {}
        with mock.patch.object(cache_fetch, "get", lambda url, proxy=None, headers=None, isp=False: page):
            text, _, _ = cache_fetch.fetch_one(H4, meta)
        self.assertEqual(meta["original"], ST)
        self.assertTrue(text.startswith("立法會秘書處今日（20日）公布"))
        self.assertNotIn("最新最全面的熱門香港新聞", text)

    def test_its_own_blurb_is_no_text(self):
        page = ('<html><head><meta name="description" content="headline for hk、headline4hk、最新最全面的熱門香港新聞、'
                '即時新聞、要聞港聞、頭條新聞。"><title>某新聞 - headline4hk</title></head><body><div id="article-detail-content">'
                '</div></body></html>')
        self.assertEqual(cache_fetch.article_text(page), "")

    def test_original_is_collected_and_its_text_kept(self):
        story = ("立法會秘書處今日（20日）公布，截至提名限期，就香港大學校董會成員空缺值接獲一項有效提名。\n"
                 "該港大校董會成員空缺，源於早前因醉駕辭任立法會議員的黃錦輝。\n按規定，立法會議員互選產生5席港大校董代表。")
        # 星島 is a source of ours (its posts sit on stheadline.com)
        corpus.ingest([{"url": "https://www.stheadline.com/politics/1", "title": "星島舊聞", "sid": "stheadline",
                        "source": "星島頭條", "published": None}])
        reads = []

        def read(pid, url, sid):  # fulltext_now.fetch: the original's page read and saved
            reads.append(url)
            fulltext_fetch.save(pid, story, "何君堯無份做港大校董 簡慧敏成唯一人選 接替黃錦輝", url=url)
            return True
        mock.patch.object(fulltext_now, "fetch", read).start()
        mock.patch.object(follow, "_hop", side_effect=AssertionError("no shortener here")).start()
        h4 = corpus.ingest([{"url": H4, "title": "何君堯無份做港大校董 簡慧敏成唯一人選 接替黃錦輝",
                             "sid": "hk_headline4hk", "source": "headline4hk.com"}])[0]
        fulltext_fetch.save(h4, "立法會秘書處今日（20日）公布，截至提名限期", original=ST, url=H4)
        st = corpus.db().execute("SELECT id, sid, text FROM posts WHERE url=%s", (ST,)).fetchone()
        self.assertIsNotNone(st)
        self.assertEqual(st["sid"], "stheadline")
        self.assertEqual(reads, [ST])
        row = corpus.db().execute("SELECT text, text_via, text_via_url, article_id FROM posts WHERE id=%s", (h4,)).fetchone()
        self.assertEqual(row["text"], story)
        self.assertEqual((row["text_via"], row["text_via_url"]), ("original", ST))
        self.assertIn("original", paywall.VIA)
        link = corpus.db().execute("SELECT status, target_id FROM follow_links WHERE post_id=%s", (h4,)).fetchone()
        self.assertEqual((link["status"], link["target_id"]), ("new", st["id"]))
        # read again (a refill): the same link, now a duplicate of the collected post, no second read
        fulltext_fetch.save(h4, "", original=ST, url=H4)
        self.assertEqual(reads, [ST])
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM follow_links").fetchone()[0], 1)

    def test_original_under_another_address_is_the_post_we_have(self):
        """headline4hk names 星島 without its title slug, NOW's /mobile/ player, 有線's ?p=, HKET's title unencoded: one
        story each (corpus.STORY_KEYS), so the original is a duplicate, never a second post"""
        pairs = [("https://www.stheadline.com/breaking-news/3628156/櫻桃街公園鼠患",
                  "https://www.stheadline.com/breaking-news/3628156"),
                 ("https://news.now.com/home/local/player?newsId=666414", "https://news.now.com/mobile/local/player?newsId=666414"),
                 ("https://www.i-cable.com/%e6%96%b0%e8%81%9e/514826/%E6%B2%B9", "https://www.i-cable.com/?p=514826"),
                 ("https://news.hket.com/article/4210518/%E6%B8%AF?mtc=20023", "https://news.hket.com/article/4210518/港大")]
        for ours, theirs in pairs:
            self.assertEqual(corpus.url_key(ours), corpus.url_key(theirs), theirs)
            pid = corpus.ingest([{"url": ours, "title": "已有的報道 " + ours[-6:], "sid": "x", "source": "x"}])[0]
            corpus.assign_articles([pid])
            self.assertEqual(follow.have(theirs), pid)
        self.assertNotEqual(corpus.url_key("https://www.stheadline.com/breaking-news/3628157"),
                            corpus.url_key("https://www.stheadline.com/breaking-news/3628156"))
        self.assertEqual(cache_fetch.original_link(H4, '<meta property="og:url" content="https://news.now.com/mobile/local/'
                                                       'player?newsId=666414">'),
                         "https://news.now.com/home/local/player?newsId=666414")

    def test_fix_pointers_removes_copies_collected_before(self):
        c = corpus.db()
        old = corpus.ingest([{"url": "https://www.stheadline.com/breaking-news/1234567/某標題", "title": "某標題",
                              "sid": "stheadline", "source": "星島"}])[0]
        h4 = corpus.ingest([{"url": H4, "title": "某標題", "sid": "hk_headline4hk", "source": "headline4hk.com"}])[0]
        copy = corpus.ingest([{"url": "https://www.stheadline.com/breaking-news/1234567", "title": "某標題",
                               "sid": "stheadline", "source": "星島"}])[0]
        corpus.assign_articles([old, h4, copy])
        c.execute("""INSERT INTO follow_links (post_id, origin, url, final_url, status, target_id, at, tries)
                     VALUES (%s, %s, %s, %s, 'new', %s, '2026-10-21T14:00:00+08:00', 0)""",
                  (h4, H4, "https://www.stheadline.com/breaking-news/1234567", "https://www.stheadline.com/breaking-news/1234567", copy))
        self.assertEqual(follow.fix_pointers(), 1)
        self.assertIsNone(c.execute("SELECT id FROM posts WHERE id=%s", (copy,)).fetchone())
        r = c.execute("SELECT status, target_id FROM follow_links WHERE post_id=%s", (h4,)).fetchone()
        self.assertEqual((r["status"], r["target_id"]), ("duplicate", old))

    def test_an_unread_original_is_read_directly(self):
        mock.patch.object(follow, "_hop", side_effect=AssertionError("no shortener here")).start()
        mock.patch.object(cache_fetch, "fetch_one", return_value=("原文全文。" * 40, None, {})).start()
        mock.patch.object(follow, "_generic_page", return_value="<html><body>nothing</body></html>").start()
        h4 = corpus.ingest([{"url": H4.replace("stheadline", "rthk"), "title": "某新聞標題",
                             "sid": "hk_headline4hk", "source": "headline4hk.com"}])[0]
        fulltext_fetch.save(h4, "副本", original="https://news.example-outlet.hk/a/1", url=H4)
        row = corpus.db().execute("SELECT text, text_via FROM posts WHERE id=%s", (h4,)).fetchone()
        self.assertEqual((row["text"], row["text_via"]), ("原文全文。" * 40, "original"))
        self.assertEqual(corpus.db().execute("SELECT status FROM follow_links").fetchone()[0], "unknown")


if __name__ == "__main__":
    unittest.main()
