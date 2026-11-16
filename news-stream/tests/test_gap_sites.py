#!/usr/bin/env python3
"""The sites link following found no source for (Deyao, 2026-10-20: "let's add 'Sites no source reads (7)'"): each a
watched source of the catalogue (sites.json, sites_fetch.py), and once a source reads a site its listed links go
through link following again (follow.recheck) and leave the list. get.surfshark.net is an advert's tracking link,
not a site: skipped as "ad". On a throwaway database (TEST_DATABASE_URL), no network: saved page snippets.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_gap_sites.py
"""
import json, os, sys, tempfile, unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, follow, fulltext_now, sites_fetch, streams  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

HKT = corpus.HKT
SITES = {s["id"]: s for s in sites_fetch.load()}
# the seven sites of the monitor's list on 20 Oct 2026 → the source that reads each (None: not a site)
GAPS = {
    "https://hk.crntt.com/crn-webapp/desc.jsp?jsid=5": None,  # a column's description page: not an article
    "http://www.crntt.com/doc/7_0_160280074_1_1020173446.html": "hk_crntt",
    "https://hongkong-invest.com/%e9%99%b3%e8%8c%82%e6%b3%a2/": "hk_hongkong_invest",
    "https://robotbelt.com/galbots-humanoid-plays-real-tennis-marking-its-astratennis-m-e09/": "gl_robotbelt",
    "https://www.silentmajority.hk/articles/1041640": "hk_silentmajority",
    "https://www.taiwannews.com.tw/zh/news/6444783": "gl_taiwannews_zh",
    "https://www.kinliu.hk/news/n/408934.html": "hk_kinliu",
}

KINLIU = ('"timeWeight":132},408934,"選委會界別分組選舉提名期今日結束　31個界別分組超額報名","2026年選委會界別分組一般選舉提名期'
          '今日（20日）結束","https:\\u002F\\u002Fimg.kinliu.hk\\u002Fa.jpg",3,"2026-10-20T12:03:01","2026-10-20T11:51:02",'
          '{"id":1},374415,"【堅有片】富泰邨女童","短","x",1,"2026-09-30T08:00:00"')
TAIWAN = ('<a class="mx-2" target="_blank" href="/zh/news/6461489"><h3 class="text-subtle-semibold">華盛証券迎十週年里程碑</h3>'
          '<p class="text-tiny-regular text-red-1">Oct. 20, 2026 13:56</p></a><a class="absolute" href="/zh/news/6461567"></a>')
SILENT = ('<a href="https://www.silentmajority.hk/articles/1041642" class="t">學習五年規劃 懂發展安己身</a>'
          '<a href="https://www.silentmajority.hk/articles/1041624" class="h351" delayed_img="x.jpg"></a>')


class Sources(unittest.TestCase):
    def test_every_site_has_a_source(self):
        follow._outlets.clear()
        for url, sid in GAPS.items():
            if sid is None:
                self.assertIsNotNone(follow.skip_reason(url), url)
                continue
            self.assertIn(sid, SITES)
            if sites_fetch.off(SITES[sid]):  # a region not read (Deyao, 2026-10-28): its links are ignored
                self.assertEqual(follow.skip_reason(url), follow.IGNORED, url)
                continue
            self.assertEqual(follow.outlet_for(url)[0], sid, url)

    def test_surfshark_is_an_advert(self):
        self.assertEqual(follow.skip_reason("https://get.surfshark.net/SH1Yr"), "ad")

    def test_kinliu_payload(self):
        rows = sites_fetch.regex_list(SITES["hk_kinliu"])(KINLIU)
        self.assertEqual(rows[0]["url"], "https://www.kinliu.hk/news/n/408934.html")
        self.assertEqual(rows[0]["title"], "選委會界別分組選舉提名期今日結束 31個界別分組超額報名")  # clean(): one space
        self.assertEqual(rows[0]["time"], "2026-10-20T12:03:01")
        self.assertEqual(len(rows), 2)

    def test_kinliu_reads_like_a_browser(self):
        """Cloudflare challenges urllib on kinliu.hk; Chrome's TLS fingerprint (curl_cffi) passes"""
        class R:
            status_code, text, headers = 200, KINLIU, {}
        with mock.patch("curl_cffi.requests.get", return_value=R()) as g, \
                mock.patch.object(sites_fetch.sources, "fetch", side_effect=AssertionError("urllib")):
            rows = sites_fetch.read(SITES["hk_kinliu"])
        self.assertEqual(g.call_args.kwargs.get("impersonate"), "chrome")
        self.assertEqual(rows[0]["url"], "https://www.kinliu.hk/news/n/408934.html")

    def test_taiwannews_and_silentmajority(self):
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=TAIWAN):
            rows = sites_fetch.read(SITES["gl_taiwannews_zh"])
        self.assertEqual([(r["url"], r["title"]) for r in rows],
                         [("https://www.taiwannews.com.tw/zh/news/6461489", "華盛証券迎十週年里程碑")])
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=SILENT):
            rows = sites_fetch.read(SITES["hk_silentmajority"])
        self.assertEqual([r["url"] for r in rows], ["https://www.silentmajority.hk/articles/1041642"])


class Recheck(unittest.TestCase):
    def test_listed_links_go_through_once_a_source_reads_the_site(self):
        c = corpus.db()
        c.execute("TRUNCATE posts, follow_links, follow_scan CASCADE")
        ids = corpus.ingest([{"sid": "tikhub_reddit", "source": "r/HongKong (Reddit)", "title": "堅料",
                              "url": "https://www.reddit.com/r/HongKong/comments/k1/x/",
                              "text": "堅料 https://www.kinliu.hk/news/n/408934.html", "published": None}])
        c.execute("INSERT INTO scores (stream, post_id, score, model, scored_at) VALUES ('CEHK', %s, 80, 'm', 'x')", (ids[0],))
        follow._outlets.clear()
        follow._outlets["__loaded__"] = ("x", "x")  # as before the source: kinliu.hk read by no source
        with mock.patch.object(fulltext_now, "fetch", return_value=True), \
                mock.patch.object(follow, "_generic_page", return_value="<html></html>"):  # the generic reader: no article
            follow.run_once()
            self.assertEqual(c.execute("SELECT status FROM follow_links").fetchone()[0], "unknown")
            follow._outlets.clear()  # the deploy that added the source: a fresh process
            follow._recheck["at"] = 0
            follow.run_once()
        r = c.execute("SELECT status, target_id FROM follow_links").fetchone()
        self.assertEqual(r["status"], "new")
        self.assertEqual(c.execute("SELECT sid FROM posts WHERE id=%s", (r["target_id"],)).fetchone()[0], "hk_kinliu")
        self.assertEqual(follow.monitor()["unknown_sites"], [])


class SecondList(unittest.TestCase):
    """the list's next six (20 Oct 2026): chinadailyasia.com is China Daily Hong Kong's old address (its pages state
    www.chinadailyhk.com), www.hkcd.com.hk 香港商報's host no browser opens (canonical.REHOST: www.hkcd.com), hkgpao.com
    (HKG報) and Thomasnet's Insights are sources now; google.com's buttons and app-store links are not articles"""
    def test_routes(self):
        follow._outlets.clear()
        self.assertEqual(follow.alias("https://www.chinadailyasia.com/hk/article/641304"),
                         "https://www.chinadailyhk.com/hk/article/641304")
        self.assertEqual(follow.alias("https://www.hkcd.com.hk/content_app/2026-10/15/content_8780146.html"),
                         "https://www.hkcd.com/content_app/2026-10/15/content_8780146.html")
        self.assertEqual(follow.outlet_for("https://www.hkgpao.com/articles/1040099")[0], "hk_hkgpao")
        # a Global site, not read since 2026-10-28 (sites_fetch.OFF_REGIONS): its links are ignored
        self.assertEqual(follow.skip_reason("https://www.thomasnet.com/insights/galbot-latent-system-robot-playing-tennis/"),
                         follow.IGNORED)
        self.assertEqual(follow.skip_reason("https://www.google.com/preferences/source?q=https://www.am730.com.hk"), "share")
        self.assertEqual(follow.skip_reason("https://www.google.com/search?q=apec"), "google")
        self.assertEqual(follow.unwrap("https://www.google.com/amp/s/www.scmp.com/news/article/1/x"),
                         "https://www.scmp.com/news/article/1/x")
        self.assertEqual(follow.skip_reason("https://onelink.to/f92q4m"), "app")
        self.assertEqual(follow.skip_reason("https://apps.apple.com/hk/app/x/id1"), "app")
        self.assertIn("gl_thomasnet", SITES)
        self.assertTrue(SITES["gl_thomasnet"].get("impersonate"))

    def test_listed_alias_goes_through(self):
        c = corpus.db()
        c.execute("TRUNCATE posts, follow_links, follow_scan CASCADE")
        art = corpus.ingest([{"sid": "chinadailyhk", "source": "China Daily HK", "title": "HK aims to drive capital flow",
                              "url": "https://www.chinadailyhk.com/hk/article/641304", "published": None}])[0]
        c.execute("""INSERT INTO follow_links (post_id, origin, url, final_url, host, status, at)
                     VALUES (NULL, 'https://x.com/a/status/2', 'https://www.chinadailyasia.com/hk/article/641304#x',
                             'https://www.chinadailyasia.com/hk/article/641304', 'chinadailyasia.com', 'unknown',
                             '2026-10-20T17:00:00+08:00')""")
        ids = corpus.ingest([{"sid": "tikhub_reddit", "source": "r/x (Reddit)", "title": "cd", "published": None,
                              "url": "https://www.reddit.com/r/x/comments/c1/x/",
                              "text": "cd https://www.chinadailyasia.com/hk/article/641304#x"}])
        c.execute("INSERT INTO follow_links (post_id, origin, url, final_url, host, status, at) VALUES "
                  "(%s, 'https://www.reddit.com/r/x/comments/c1/x/', 'https://www.chinadailyasia.com/hk/article/641304#x',"
                  " 'https://www.chinadailyasia.com/hk/article/641304', 'chinadailyasia.com', 'unknown', '2026-10-20T17:00:00+08:00')",
                  (ids[0],))
        follow._recheck["at"] = 0
        with mock.patch.object(fulltext_now, "fetch", return_value=True):
            follow.run_once()
        got = {r["post_id"]: (r["status"], r["target_id"]) for r in c.execute("SELECT * FROM follow_links")}
        self.assertEqual(got[ids[0]], ("duplicate", art))
        self.assertEqual(follow.monitor()["unknown_sites"], [])  # the mirror bot's row too (routed again, no post)


class ListedAdvert(unittest.TestCase):
    def test_listed_advert_leaves_the_list(self):
        c = corpus.db()
        c.execute("TRUNCATE posts, follow_links, follow_scan CASCADE")
        c.execute("""INSERT INTO follow_links (post_id, origin, url, final_url, host, status, at)
                     VALUES (NULL, 'https://x.com/a/status/1', 'https://get.surfshark.net/SH1Yr',
                             'https://get.surfshark.net/SH1Yr', 'get.surfshark.net', 'unknown', '2026-10-20T17:00:00+08:00')""")
        follow._recheck["at"] = 0
        follow.recheck()
        self.assertEqual(follow.monitor()["unknown_sites"], [])
        self.assertEqual(c.execute("SELECT status, reason FROM follow_links").fetchone()[:], ("skipped", "ad"))


class Headline(unittest.TestCase):
    def test_listing_gives_a_collected_post_its_headline(self):
        """hongkong-invest.com: link following stored the post as 〔連結〕hongkong-invest.com (no reader in the poller opens
        the site: its route is the ISP IP); the catalogue's listing of the same URL gives its headline"""
        url = "https://hongkong-invest.com/%e9%99%b3%e8%8c%82%e6%b3%a2/"
        corpus.db().execute("TRUNCATE posts CASCADE")
        pid = corpus.ingest([{"sid": "hk_hongkong_invest", "source": "香港投資", "url": url,
                              "title": corpus.LINK_TITLE + "hongkong-invest.com", "published": None}])[0]
        corpus.ingest([{"sid": "hk_hongkong_invest", "source": "香港投資", "url": url, "title": "陳茂波倡營造有利投資環境",
                        "published": None}])
        self.assertEqual(corpus.db().execute("SELECT title FROM posts WHERE id=%s", (pid,)).fetchone()[0],
                         "陳茂波倡營造有利投資環境")
        corpus.ingest([{"sid": "x", "source": "x", "url": url, "title": "another title", "published": None}])
        self.assertEqual(corpus.db().execute("SELECT title FROM posts WHERE id=%s", (pid,)).fetchone()[0],
                         "陳茂波倡營造有利投資環境")  # a real title is never replaced


if __name__ == "__main__":
    unittest.main()
