#!/usr/bin/env python3
"""Coverage of the big Chinese dailies (the coverage audit of 14 Oct 2026, ~/artifacts/coverage-audit): everything
東方, 明報 and 都市日報 publish comes in, with its full text. Real pages and feeds saved on 14 Oct 2026 in
tests/fixtures/coverage/. No network, no database.

Found by comparing each outlet's complete list for 13 and 14 Oct with the corpus:
- on.cc instant news: the sitemap's racing (/bknrac/…) and weather (/hk/bkn/cnt/weather/…) stories were left out by
  oncc_instant's section filter (9 on 13 Oct, 1 on 14 Oct).
- on.cc (instant and the Oriental Daily paper): the story is in <div class="paragraph"> blocks, not <p>, so the reader
  kept only the longest paragraph (~150 characters): 458 of 644 instant and 350 of 494 paper stories had no full text.
- 明報 instant news RSS (rss/ins/all.xml): its finance items' <link> ends in `" target="blank`, so 325 posts were
  stored under a broken URL whose page can't be read.
- 都市日報: its RSS holds the newest 30 posts and the print edition lands ~90 posts within minutes around 03:30, so 87
  posts of 13 Oct and 60 of 14 Oct never came in; its monthly article sitemap lists every post.

    python3 tests/test_coverage_dailies.py
"""
import json, os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_fetch, corpus, sources, sources_extra  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "coverage")


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def spec(sid):
    return next(s for s in list(sources_extra.EXTRA) + list(sources.SOURCES) if s[0] == sid)


def run(sid, pages):
    """fetch_source(sid) with the network replaced by {url substring: text}"""
    def fake(url, headers=None, proxy=None, timeout=None, **k):
        for key, text in pages.items():
            if key in url:
                return text
        raise AssertionError("unexpected fetch " + url)
    with mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(sources_extra, "fetch", side_effect=fake):
        return sources.fetch_source(spec(sid))


class OnccSitemap(unittest.TestCase):
    def test_every_section_of_the_sitemap(self):
        got = {it["url"]: it for it in run("oncc_instant", {"hk.on.cc/sitemap.xml": fx("oncc_sitemap.xml")})}
        racing = "https://hk.on.cc/bknrac/bkn/cnt/news/20261014/bknrac-20261014224619314-1014_01002_001.html"
        weather = "https://hk.on.cc/hk/bkn/cnt/weather/20261014/bkn-20261014085522297-1014_00942_001.html"
        news = "https://hk.on.cc/hk/bkn/cnt/news/20261014/bkn-20261014161437413-1014_00822_001.html"
        self.assertEqual(len(got), 5)
        self.assertIn(racing, got)
        self.assertIn(weather, got)
        self.assertEqual(got[news]["section"], "港澳")
        self.assertEqual(got[weather]["section"], "天氣")
        self.assertEqual(got[racing]["section"], "馬經")


class OnccText(unittest.TestCase):
    def test_instant_story_whole(self):
        text = cache_fetch.article_text(fx("oncc_instant.html"))
        self.assertTrue(text.startswith("政府加速發展北部都會區"), text[:80])
        self.assertIn("戰略新興產業落戶", text)  # the last paragraph
        self.assertGreater(len(text), 600)
        self.assertNotIn("政府加速發展北部都會區。\n", text)  # not the photo caption

    def test_paper_story_whole(self):
        text = cache_fetch.article_text(fx("oncc_paper.html"))
        self.assertIn("譚方駿出庭作供", text)
        self.assertIn("案件編號：HCCC 198/2025", text)  # the last paragraph
        self.assertIn("譚父對蔡大方", text)  # a cross-head
        self.assertGreater(len(text), 900)


class MingpaoRss(unittest.TestCase):
    def test_link_without_the_target_attribute(self):
        items = run("mingpao_ins_all", {"rss/ins/all.xml": fx("mingpao_ins_all.xml")})
        self.assertEqual(len(items), 3)
        for it in items:
            self.assertNotIn('"', it["url"])
            self.assertNotIn(" ", it["url"])
            self.assertNotIn("target", it["url"])
        self.assertTrue(any(it["url"].startswith("https://finance.mingpao.com/fin/instant") for it in items))


class Metrohk(unittest.TestCase):
    def test_every_post_of_the_month_sitemaps(self):
        items = run("metrohk_sitemap", {"metrohk.com.hk/sitemap.xml": fx("metrohk_index.xml"),
                                        "sitemap-articles-2026-10.xml": fx("metrohk_articles.xml"),
                                        "sitemap-articles-2026-09.xml": "<urlset></urlset>"})
        self.assertEqual(len(items), 3)
        first = next(it for it in items if it["url"].endswith("-2e0590"))
        self.assertTrue(first["url"].startswith("https://metrohk.com.hk/"))
        self.assertEqual(first["section"], "港聞")
        self.assertEqual(first["title"], "K11藝術基金公佈2026年度藝術家獎公開招募")
        self.assertNotRegex(first["title"], r"[0-9a-f]{6}$")  # the slug's id suffix is not part of the title
        self.assertTrue(first["published"])
        self.assertIn("metrohk_sitemap", sources_extra.SLUG_TITLES)
        self.assertIn("metrohk_sitemap", __import__("fulltext_fetch").SLUG_SIDS)

    def test_same_post_as_the_rss(self):
        import corpus
        rss = ("https://metrohk.com.hk/%E6%B8%AF%E8%81%9E/K11%E8%97%9D%E8%A1%93%E5%9F%BA%E9%87%91%E5%85%AC%E4%BD%88"
               "2026%E5%B9%B4%E5%BA%A6%E8%97%9D%E8%A1%93%E5%AE%B6%E7%8D%8E%E5%85%AC%E9%96%8B%E6%8B%9B%E5%8B%9F-2e0590")  # as the RSS gives it
        items = run("metrohk_sitemap", {"metrohk.com.hk/sitemap.xml": fx("metrohk_index.xml"),
                                        "sitemap-articles-2026-10.xml": fx("metrohk_articles.xml"),
                                        "sitemap-articles-2026-09.xml": "<urlset></urlset>"})
        self.assertIn(corpus.url_key(rss), {corpus.url_key(it["url"]) for it in items})


def hk01_article(i, published=True, t=1791990632):
    return json.dumps({"articleId": i, "publishUrl": f"https://www.hk01.com/社會新聞/{i}/標題-{i}", "title": f"標題 {i}",
                       "mainCategory": "社會新聞", "authors": [{"publishName": "記者"}], "publishTime": t if published else None,
                       "blocks": [{"blockType": "text", "htmlTokens": [[{"type": "text", "content": f"第{i}篇的內文。" * 20}]]}]})


class Hk01Walk(unittest.TestCase):
    """香港01: every article id in turn through its article API (the sitemap and the category feed leave out some
    stories: 9 of 730 on 13–14 Oct, mostly 好生活 / 熱爆話題 / 食玩買). An id with no published article yet (404, or
    not yet published) is looked at again later, for 3 days."""

    def setUp(self):
        self.kv, self.calls, self.now = {}, [], [1791990000.0]
        self.missing, self.unpublished = set(), set()
        feed = json.dumps({"items": [{"type": 1, "data": {"articleId": 1000, "title": "x",
                                                          "publishUrl": "https://www.hk01.com/a/1000/x", "publishTime": 1}},
                                     {"type": 1, "data": {"articleId": 998, "title": "y",
                                                          "publishUrl": "https://www.hk01.com/a/998/y", "publishTime": 1}}]})

        def fake(url, headers=None, proxy=None, timeout=None, **k):
            if "feed/category/0" in url:
                return feed
            i = int(url.rsplit("/", 1)[1])
            self.calls.append(i)
            if i in self.missing:
                raise sources.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return hk01_article(i, published=i not in self.unpublished)
        self.p = [mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(sources_extra, "fetch", side_effect=fake),
                  mock.patch.object(corpus, "kv_get", side_effect=lambda k, d=None: self.kv.get(k, d)),
                  mock.patch.object(corpus, "kv_set", side_effect=lambda k, v: self.kv.__setitem__(k, json.loads(json.dumps(v)))),
                  mock.patch.object(sources_extra.time, "time", side_effect=lambda: self.now[0]),
                  mock.patch.object(sources_extra, "HK01_WALK_BACK", 20)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_first_run_reads_back_and_carries_text(self):
        self.missing = {985}
        items = sources.fetch_source(spec("hk01_walk"))
        self.assertEqual(sorted(self.calls), list(range(980, 1001)))
        self.assertEqual(len(items), 20)
        it = next(x for x in items if x["url"].endswith("/990/標題-990"))
        self.assertIn("第990篇的內文", it["text"])
        self.assertEqual(it["section"], "社會新聞")
        self.assertEqual(it["author"], "記者")
        self.assertTrue(it["published"])

    def test_next_run_goes_on_from_the_newest_and_rechecks_the_missing_later(self):
        self.missing, self.unpublished = {985}, {990}
        sources.fetch_source(spec("hk01_walk"))
        self.calls.clear()
        self.missing, self.unpublished = set(), set()
        self.now[0] += 60  # a minute later: nothing new, nothing due again yet (the found ones are offered again)
        self.assertEqual(len(sources.fetch_source(spec("hk01_walk"))), 19)
        self.assertEqual(self.calls, [])
        self.now[0] += 15 * 60  # due again: both come in
        got = sources.fetch_source(spec("hk01_walk"))
        self.assertEqual(sorted(self.calls), [985, 990])
        self.assertEqual(len(got), 21)
        self.calls.clear()
        self.now[0] += 4 * 86400
        self.assertEqual(sources.fetch_source(spec("hk01_walk")), [])
        self.assertEqual(self.calls, [])  # found: never asked again

    def test_an_id_gone_for_three_days_is_dropped(self):
        self.missing = {985}
        sources.fetch_source(spec("hk01_walk"))
        for _ in range(40):
            self.now[0] += 3 * 3600
            sources.fetch_source(spec("hk01_walk"))
        self.assertNotIn("985", self.kv["hk01_walk"]["pending"])
        self.assertLess(self.calls.count(985), 15)


class StheadlineWalk(unittest.TestCase):
    """星島頭條: every article id in turn (the sitemap leaves out the 星島日報 paper's daily-* channels and opinion:
    66 of 363 stories on 13 Oct, 67 of 371 on 14 Oct). /realtime-news/<id> redirects to the article; an id with no
    article answers a "404" page with status 200."""

    def setUp(self):
        self.kv, self.calls, self.now = {}, [], [1791990000.0]
        page = fx("stheadline_daily.html")
        rss = ("<rss><channel>" + "".join(f"<item><title>t{i}</title><link>https://www.stheadline.com/society/{i}/t</link>"
                                          f"<pubDate>Wed, 14 Oct 2026 10:00:00 +0800</pubDate></item>" for i in (3625754, 3625750))
               + "</channel></rss>")

        def fake(url, headers=None, proxy=None, timeout=None, **k):
            if url.endswith("/rss"):
                return rss
            i = int(url.rsplit("/", 1)[1])
            self.calls.append(i)
            return page if i == 3625754 else "<html><head><title>404</title></head><body>404</body></html>"
        self.p = [mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(sources_extra, "fetch", side_effect=fake),
                  mock.patch.object(corpus, "kv_get", side_effect=lambda k, d=None: self.kv.get(k, d)),
                  mock.patch.object(corpus, "kv_set", side_effect=lambda k, v: self.kv.__setitem__(k, json.loads(json.dumps(v)))),
                  mock.patch.object(sources_extra.time, "time", side_effect=lambda: self.now[0]),
                  mock.patch.object(sources_extra, "STHEADLINE_WALK_BACK", 10)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_an_article_page_and_the_ids_without_one(self):
        items = sources.fetch_source(spec("stheadline_walk"))
        self.assertEqual(sorted(self.calls), list(range(3625744, 3625755)))
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["url"], "https://www.stheadline.com/daily-sport/3625754/艾納斯-打開亞冠勝利之門")
        self.assertEqual(it["title"], "艾納斯 打開亞冠勝利之門")
        self.assertEqual(it["section"], "daily-sport")
        self.assertTrue(it["published"].isoformat().startswith("2026-10-14T03:00"))
        self.assertIn("C朗拿度", it["text"])
        self.assertEqual(len(self.kv["stheadline_walk"]["pending"]), 10)


class WalkPerRun(unittest.TestCase):
    def test_a_long_backlog_is_read_over_several_runs(self):
        kv, calls = {}, []

        def fake(url, **k):
            if "feed/category/0" in url:
                return json.dumps({"items": [{"type": 1, "data": {"articleId": 1000}}]})
            calls.append(int(url.rsplit("/", 1)[1]))
            return hk01_article(int(url.rsplit("/", 1)[1]))
        with mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(sources_extra, "fetch", side_effect=fake), \
                mock.patch.object(corpus, "kv_get", side_effect=lambda k, d=None: kv.get(k, d)), \
                mock.patch.object(corpus, "kv_set", side_effect=lambda k, v: kv.__setitem__(k, json.loads(json.dumps(v)))), \
                mock.patch.object(sources_extra, "HK01_WALK_BACK", 100), mock.patch.object(sources_extra, "WALK_PER_RUN", 30):
            n = [len(sources.fetch_source(spec("hk01_walk"))) for _ in range(5)]
        self.assertEqual(n, [30, 60, 90, 101, 101])  # what was found is offered again (WALK_KEEP)
        self.assertEqual(sorted(calls), list(range(900, 1001)))


class Bastille(unittest.TestCase):
    """巴士的報: the month's post sitemap (every post; the "today" sitemap is paged and starts over each UTC day, so
    posts fell off it: 16968436 of 13 Oct 23:39 never came in). Its <lastmod> is UTC written as +08:00 (14 Oct 17:28
    "+08:00" = the page's 15 Oct 01:28 HKT), so every 巴士的報 post was stored 8 hours early."""

    def test_every_post_of_the_month_at_the_right_time(self):
        items = run("bastillepost", {"sitemap/generate/index": fx("bastille_index.xml"),
                                     "gen-sitemap/post/2026-10": fx("bastille_month.xml"),
                                     "gen-sitemap/post/2026-09": "<urlset></urlset>"})
        self.assertEqual(len({it["url"] for it in items}), 2)
        it = next(x for x in items if "/16975593-" in x["url"])
        self.assertEqual(it["published"].astimezone(sources.HKT).isoformat()[:16], "2026-10-15T01:28")
        self.assertTrue(it["title"])


class MonthSitemapsRecentOnly(unittest.TestCase):
    """the month sitemaps (都市日報 ~2,200 rows, 巴士的報 ~4,300 by mid-month) went whole to the corpus on every read,
    which held the poller's ingest back (live, 15 Oct 2026): a read keeps the rows modified in the 3 days before the
    sitemap's newest row (the first read after deploy took the whole month)"""

    def test_old_rows_left_out(self):
        old = ('<url><loc>https://metrohk.com.hk/%E6%B8%AF%E8%81%9E/old-abcdef</loc>'
               '<lastmod>2026-10-01T10:00:00+08:00</lastmod></url>')
        month = fx("metrohk_articles.xml").replace("</urlset>", old + "</urlset>")
        items = run("metrohk_sitemap", {"metrohk.com.hk/sitemap.xml": fx("metrohk_index.xml"),
                                        "sitemap-articles-2026-10.xml": month, "sitemap-articles-2026-09.xml": "<urlset></urlset>"})
        self.assertEqual(len(items), 3)
        old = ('<url><loc>https://www.bastillepost.com/hongkong/article/16000000-old</loc>'
               '<lastmod>2026-10-01T10:00:00+08:00</lastmod></url>')
        items = run("bastillepost", {"sitemap/generate/index": fx("bastille_index.xml"),
                                     "gen-sitemap/post/2026-10": fx("bastille_month.xml").replace("</urlset>", old + "</urlset>"),
                                     "gen-sitemap/post/2026-09": "<urlset></urlset>"})
        self.assertEqual(len({it["url"] for it in items}), 2)


class WalkOffersAgain(unittest.TestCase):
    """a walk returns an article once; when the poll that carried it is lost before its corpus write (a deploy
    restarts the poller while its after_poll waits), the article was gone for good (live, 15 Oct: 星島日報 stories
    seen by the poller at 06:08 never reached the corpus). Articles found in the last WALK_KEEP seconds are offered
    again on every run."""

    def test_found_articles_come_again_for_a_while(self):
        kv, now = {}, [1791990000.0]

        def fake(url, **k):
            if "feed/category/0" in url:
                return json.dumps({"items": [{"type": 1, "data": {"articleId": 1000}}]})
            return hk01_article(int(url.rsplit("/", 1)[1]))
        with mock.patch.object(sources, "fetch", side_effect=fake), mock.patch.object(sources_extra, "fetch", side_effect=fake), \
                mock.patch.object(corpus, "kv_get", side_effect=lambda k, d=None: kv.get(k, d)), \
                mock.patch.object(corpus, "kv_set", side_effect=lambda k, v: kv.__setitem__(k, json.loads(json.dumps(v)))), \
                mock.patch.object(sources_extra.time, "time", side_effect=lambda: now[0]), \
                mock.patch.object(sources_extra, "HK01_WALK_BACK", 5):
            first = {it["url"] for it in sources.fetch_source(spec("hk01_walk"))}
            now[0] += 600
            again = {it["url"] for it in sources.fetch_source(spec("hk01_walk"))}
            now[0] += sources_extra.WALK_KEEP
            later = {it["url"] for it in sources.fetch_source(spec("hk01_walk"))}
        self.assertEqual(len(first), 6)
        self.assertEqual(again, first)
        self.assertEqual(later, set())


if __name__ == "__main__":
    unittest.main()
