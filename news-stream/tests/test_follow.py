#!/usr/bin/env python3
"""Link following (Deyao, 2026-10-20): a post that reaches a stream (scored at or above a stream's threshold after
stage 2, seen since corpus.FLOOR, not a comment) has every hyperlink it carries (in its text, its link
target, a news article's body) handed to the same ingest every collected article goes through: an article we have
is a duplicate (the existing dedupe, by url_key), one we don't becomes a new post of its own outlet (its source's
sid, its page read by fulltext_now as any new article), a site no source reads is listed for the monitor, links that
aren't articles are skipped and counted. Shorteners and wrappers are expanded first. One hop only. Reddit's mirror
bots (sources.resolve_mirrors) go through the same routing. No network: hops, pages and page reads are stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_follow.py
"""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, corpus, follow, fulltext_fetch, fulltext_now, monitor, streams  # noqa: E402

HKT = corpus.HKT
OPED = "https://www.scmp.com/opinion/hong-kong-opinion/article/3371144/tai-po-fire-public-accountability-needed-honour-those-who-died"
OTHER = "https://www.scmp.com/news/hong-kong/politics/article/3371200/legco-debates-fire-inquiry"
REDDIT = "https://www.reddit.com/r/SCMPauto/comments/1xalzg8/oped_tai_po_fire_public_accountability_needed_to/"
ARTICLE_PAGE = """<html><head><title>LegCo debates fire inquiry | South China Morning Post</title>
<meta property="og:title" content="LegCo debates fire inquiry"></head><body>
<nav><a href="https://www.scmp.com/news/hong-kong">Hong Kong</a></nav>
<article><p>Lawmakers on Tuesday debated the inquiry into the Tai Po fire, which the government set up last week.</p>
<p>The chairman said the report <a href="https://www.thestandard.com.hk/news/article/999/fire-report">was due</a> in
spring, and <a href="https://bit.ly/3abc">a statement</a> followed; see also <a href="/news/hong-kong/article/3371300/x-y-z">the earlier story</a>.</p>
<p><a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a> <img src="https://img.scmp.com/a.jpg"></p></article>
<aside><a href="https://www.scmp.com/news/china/article/1/sidebar-story">sidebar</a></aside></body></html>"""


def now():
    return datetime.now(HKT).isoformat(timespec="seconds")


def post(url, title, text=None, sid="tikhub_reddit", source="r/SCMPauto (Reddit)", thread=None, seen=None):
    ids = corpus.ingest([{"sid": sid, "source": source, "url": url, "title": title, "text": text, "thread": thread,
                          "published": None, "seen": seen or now()}])
    corpus.assign_articles(ids)
    # a live post: a source's first read is backlog (first_run), which no stage takes (corpus.seen_cond)
    corpus.db().execute("UPDATE posts SET first_run = NULL WHERE url=%s", (url,))
    return ids[0] if ids else corpus.db().execute("SELECT id FROM posts WHERE url=%s", (url,)).fetchone()[0]


def score(pid, stream="WFC", sc=95):
    corpus.db().execute("INSERT INTO scores (stream, post_id, score, model, scored_at) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (stream, post_id) DO UPDATE SET score=excluded.score",
                        (stream, pid, sc, streams.CONFIRM_MODEL, now()))


def links_of(pid):
    return {r["url"]: dict(r) for r in corpus.db().execute("SELECT * FROM follow_links WHERE post_id=%s", (pid,))}


class Base(unittest.TestCase):
    def setUp(self):
        c = corpus.db()
        c.execute("TRUNCATE posts, follow_links, follow_scan CASCADE")
        c.execute("DELETE FROM kv WHERE k = 'stream_thresholds'")
        follow._outlets.clear()
        follow._post_hosts.update(at=0, hosts={})
        # no network anywhere: shorteners, pages and page reads are stubbed per test
        self.hop = mock.patch.object(follow, "_hop", side_effect=AssertionError("unexpected network hop")).start()
        self.page = mock.patch.object(follow, "_page", side_effect=AssertionError("unexpected page read")).start()
        self.read = mock.patch.object(fulltext_now, "fetch", side_effect=self.fake_read).start()
        # a site no source reads: its page read by the generic reader (default: a page with no article)
        self.gpage = mock.patch.object(follow, "_generic_page", return_value="<html><body>nothing</body></html>").start()
        self.addCleanup(mock.patch.stopall)
        self.reads = []

    def fake_read(self, pid, url, sid):
        """fulltext_now.fetch as it does it: the page read, saved, the page left in cache_fetch.last_page()"""
        self.reads.append(url)
        cache_fetch._seen.page = ARTICLE_PAGE
        fulltext_fetch.save(pid, "Lawmakers on Tuesday debated the inquiry into the Tai Po fire. " * 12, None,
                            "2026-10-20T10:00:00+08:00")
        return True


class Extract(Base):
    def test_text_links_with_wrappers(self):
        got = follow.text_links("見 https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.hk01.com%2Fa%2F1%3Ffbclid%3Dx&h=1 。",
                                "Tai Po https://www.scmp.com/x/article/1/y）和 https://www.scmp.com/x/article/1/y")
        self.assertEqual(got, ["https://www.scmp.com/x/article/1/y", "https://www.hk01.com/a/1?fbclid=x"])  # text, then title

    def test_body_links_are_the_articles_own(self):
        got = follow.body_links(ARTICLE_PAGE, OTHER)
        self.assertIn("https://www.thestandard.com.hk/news/article/999/fire-report", got)
        self.assertIn("https://bit.ly/3abc", got)
        self.assertIn("https://www.scmp.com/news/hong-kong/article/3371300/x-y-z", got)  # relative, made absolute
        self.assertIn("https://www.facebook.com/sharer/sharer.php?u=x", got)  # kept here, skipped (counted) later
        self.assertNotIn("https://www.scmp.com/news/hong-kong", got)  # navigation
        self.assertNotIn("https://www.scmp.com/news/china/article/1/sidebar-story", got)  # sidebar
        self.assertFalse(any("a.jpg" in u for u in got))  # pictures are not links

    def test_clean_drops_tracking_and_fragment(self):
        self.assertEqual(follow.clean("https://www.scmp.com/a/article/1/x?utm_source=rss_feed&id=2#comments"),
                         "https://www.scmp.com/a/article/1/x?id=2")


class Collectors(Base):
    """the stored post keeps its links (Deyao, 2026-10-20: Dimsum Daily's X post "has a link")"""
    def test_x_keeps_an_unexpanded_link(self):
        import tikhub_fetch
        t = {"tweet_id": "1", "text": "Hong Kong showcases strengths at APEC https://t.co/AbC123", "created_at": "x",
             "user_info": {"screen_name": "dimsumdaily_hk", "name": "Dimsum Daily"}}
        self.assertIn("https://t.co/AbC123", tikhub_fetch.x_tweet(t)["text"])
        t["entities"] = {"urls": [{"url": "https://t.co/AbC123", "expanded_url": "https://www.dimsumdaily.hk/a-b-c/"}]}
        self.assertIn("https://www.dimsumdaily.hk/a-b-c/", tikhub_fetch.x_tweet(t)["text"])

    def test_x_drops_only_its_own_photo_link(self):
        import tikhub_fetch
        t = {"tweet_id": "2", "text": "Read https://t.co/Art1 https://t.co/Pic9", "created_at": "x",
             "media": {"photo": [{"media_url_https": "https://pbs.twimg.com/media/a.jpg"}]}}
        text = tikhub_fetch.x_tweet(t)["text"]
        self.assertIn("https://t.co/Art1", text)
        self.assertNotIn("Pic9", text)

    def test_telegram_keeps_every_href(self):
        import sources
        page = ('<div class="tgme_widget_message_wrap"><div data-post="rthk_new_c/1"></div>'
                '<div class="tgme_widget_message_text js-message_text">政府公布<a href="https://news.rthk.hk/rthk/ch/component/k2/1.htm">詳情</a>'
                '及<a href="https://www.info.gov.hk/gia/general/202610/20/P1.htm">新聞公報</a></div>'
                '<time datetime="2026-10-20T08:00:00+00:00"></time></div>')
        it = sources.telegram(page)[0]
        self.assertIn("https://www.info.gov.hk/gia/general/202610/20/P1.htm", it["text"])

    def test_threads_link_post_keeps_its_target(self):
        import ig_fetch, json as j
        o = {"taken_at": 1760947200, "code": "C1", "caption": {"text": "看這篇"}, "like_count": 1,
             "text_post_app_info": {"link_preview_attachment": {
                 "url": "https://l.threads.com/?u=https%3A%2F%2Fwww.am730.com.hk%2F%E6%9C%AC%E5%9C%B0%2F123&e=x"}}}
        got = ig_fetch.threads_posts('<script type="application/json">' + j.dumps({"a": o}) + "</script>", "am730")
        self.assertIn("https://www.am730.com.hk/本地/123", got[0]["text"])


class Expand(Base):
    def test_shortener_chain_to_the_outlet(self):
        self.hop.side_effect = lambda u: {"https://t.co/abc": (301, "https://bit.ly/3abc", ""),
                                          "https://bit.ly/3abc": (301, OPED + "?utm_source=twitter", "")}[u]
        self.assertEqual(follow.expand("https://t.co/abc"), OPED + "?utm_source=twitter")
        self.assertEqual(self.hop.call_count, 2)  # the outlet's own page is never requested here

    def test_tco_meta_refresh_and_lnkd_interstitial(self):
        pages = {"https://t.co/x": (200, None, f'<meta http-equiv="refresh" content="0;URL={OPED}">'),
                 "https://lnkd.in/y": (200, None, f'<a data-tracking-control-name="external_url_click" href="{OTHER}">go</a>')}
        self.hop.side_effect = lambda u: pages[u]
        self.assertEqual(follow.expand("https://t.co/x"), OPED)
        self.assertEqual(follow.expand("https://lnkd.in/y"), OTHER)

    def test_not_a_shortener_is_not_requested(self):
        self.assertEqual(follow.expand(OPED), OPED)
        self.hop.assert_not_called()


class Kinds(Base):
    def test_not_articles(self):
        cases = {"https://www.scmp.com/": "home", "https://www.hk01.com": "home",
                 "https://img.scmp.com/a/b.jpg": "media", "https://x.com/intent/tweet?text=a": "share",
                 "https://www.facebook.com/sharer/sharer.php?u=x": "share", "https://wa.me/?text=x": "share",
                 "https://www.youtube.com/watch?v=abc": "social", "https://www.facebook.com/hk01/posts/1": "social",
                 "https://www.scmp.com/news/hong-kong": "section", "https://www.hk01.com/tag/123": "section",
                 "mailto:a@b.c": "not_web", "https://www.rthk.hk/?lang=zh-hant": "home",
                 "https://invest.hket.com/data/HKG02888/?mtc=20048": "section",
                 "https://hk.crntt.com/crn-webapp/about2014/aboutus_1.html": "section"}
        for u, k in cases.items():
            self.assertEqual(follow.skip_reason(u), k, u)
        for u in (OPED, "https://www.thestandard.com.hk/news/article/999/fire-report",
                  "https://hongkongfp.com/2026/10/20/tai-po-fire-inquiry/", "https://www.rthk.hk/x?id=123"):
            self.assertIsNone(follow.skip_reason(u), u)


class Follow(Base):
    def test_scmpauto_post_leads_to_scmp_duplicate(self):
        """the live case: r/SCMPauto's Tai Po op-ed post (WFC 95) → SCMP's own copy, already in the corpus"""
        orig = post(OPED + "?utm_source=rss_feed", "Tai Po fire: public accountability needed to honour those who died",
                    sid="scmp_hk", source="南華早報")
        bot = post(REDDIT, "[Op-Ed] - Tai Po fire: public accountability needed to honour those who died",
                   "[Op-Ed] - Tai Po fire: public accountability needed to honour those who died\n" + OPED)
        score(bot)
        self.assertEqual(follow.run_once(), 1)
        row = links_of(bot)[OPED]
        self.assertEqual((row["status"], row["target_id"], row["host"]), ("duplicate", orig, "scmp.com"))
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts").fetchone()[0], 2)  # nothing new
        self.assertEqual(follow.run_once(), 0)  # a post is followed once

    def test_new_article_from_its_outlet(self):
        bot = post(REDDIT, "Tai Po fire debate", "Tai Po fire debate " + OTHER + "?utm_source=reddit")
        score(bot)
        follow.run_once()
        row = links_of(bot)[OTHER + "?utm_source=reddit"]
        self.assertEqual(row["status"], "new")
        p = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (row["target_id"],)).fetchone()
        self.assertEqual(p["url"], OTHER)  # cleaned
        self.assertTrue(p["sid"].startswith("scmp"), p["sid"])
        self.assertEqual((p["website"], p["source_type"]), ("南華早報", "News"))
        self.assertEqual(p["title"], "LegCo debates fire inquiry")  # the page's own headline
        self.assertGreater(len(p["text"]), 500)  # read in full by fulltext_now's own reader
        self.assertEqual(self.reads, [OTHER])
        self.assertIsNotNone(p["cached_at"])
        # its own links are not followed (one hop) until it reaches a stream itself
        self.assertEqual(follow.run_once(), 0)
        self.assertEqual(links_of(p["id"]), {})

    def test_followed_article_reaching_a_stream_is_followed_too(self):
        self.page.side_effect, self.page.return_value = None, ARTICLE_PAGE
        self.hop.side_effect = lambda u: (301, "https://www.nytimes.com/2026/10/20/world/asia/hong-kong-fire.html", "")
        art = post(OTHER, "LegCo debates fire inquiry", "short feed text", sid="scmp_hk", source="南華早報")
        score(art, "WFC", 80)
        post("https://www.thestandard.com.hk/news/article/999/fire-report", "Fire report", sid="standard", source="The Standard")
        follow.run_once()
        got = links_of(art)
        self.assertEqual(got["https://www.thestandard.com.hk/news/article/999/fire-report"]["status"], "duplicate")
        self.assertEqual(got["https://www.facebook.com/sharer/sharer.php?u=x"]["status"], "skipped")
        self.assertEqual(got["https://www.facebook.com/sharer/sharer.php?u=x"]["reason"], "share")
        self.assertEqual(got["https://bit.ly/3abc"]["status"], "unknown")  # the NYT: no source reads it
        self.assertEqual(got["https://bit.ly/3abc"]["host"], "nytimes.com")
        self.assertEqual(got["https://www.scmp.com/news/hong-kong/article/3371300/x-y-z"]["status"], "new")
        scan = corpus.db().execute("SELECT * FROM follow_scan WHERE post_id=%s", (art,)).fetchone()
        self.assertEqual(scan["body"], "page")

    def test_only_posts_that_reach_a_stream(self):
        below = post(REDDIT, "Tai Po", "Tai Po " + OTHER)
        score(below, "WFC", 45)  # stage 1 passed, under the threshold
        old = post(REDDIT.replace("1xalzg8", "1old"), "Tai Po", "Tai Po " + OTHER,
                   seen=(datetime.fromisoformat(corpus.FLOOR) - timedelta(minutes=5)).isoformat())
        score(old)
        thread = post(REDDIT + "c1", "a comment " + OTHER, "a comment " + OTHER, thread=REDDIT)
        score(thread)
        self.assertEqual(follow.run_once(), 0)
        corpus.kv_set("stream_thresholds", {"WFC": 40})
        self.assertEqual(follow.run_once(), 1)  # the threshold Deyao sets decides

    def test_due_by_each_streams_threshold(self):
        """due() starts from scores: each stream's own threshold, a stream streams.json
        lacks has the default, a post over two streams comes once, followed ones and comments never, newest first"""
        corpus.kv_set("stream_thresholds", {"WFC": 80, "GALBOT": 30})
        ids = [post(REDDIT.replace("1xalzg8", f"1d{i}"), f"post {i}") for i in range(6)]
        score(ids[0], "WFC", 79)        # under WFC's 80
        score(ids[1], "WFC", 80)        # at it
        score(ids[2], "GALBOT", 31)     # at GALBOT's 30 or more
        score(ids[3], "GONE", 49)       # not in streams.json: the default 50
        score(ids[4], "GONE", 50)
        score(ids[5], "WFC", 95)
        score(ids[5], "POL", 95)        # two streams: one row
        thread = post(REDDIT + "c2", "a comment", thread=REDDIT)
        score(thread, "WFC", 99)
        self.assertEqual([r["id"] for r in follow.due()], [ids[5], ids[4], ids[2], ids[1]])
        corpus.db().execute("INSERT INTO follow_scan (post_id) VALUES (%s)", (ids[5],))
        self.assertEqual([r["id"] for r in follow.due(limit=2)], [ids[4], ids[2]])

    def test_unknown_platform_listed_with_example(self):
        self.hop.side_effect = AssertionError
        for i in range(3):
            p = post(f"https://www.reddit.com/r/HongKong/comments/u{i}/x/", f"NYT piece {i}",
                     f"NYT piece {i} https://www.nytimes.com/2026/10/2{i}/world/asia/story-{i}.html")
            score(p)
        follow.run_once()
        m = follow.monitor()
        site = next(s for s in m["unknown_sites"] if s["host"] == "nytimes.com")
        self.assertEqual(site["links"], 3)
        self.assertEqual(site["posts"], 3)
        self.assertIn("NYT piece", site["example"]["title"])
        self.assertEqual(m["last_24h"]["unknown"], 3)
        # once a source reads the site, its listed links go through again
        follow._outlets["nytimes.com"] = ("nyt", "New York Times")
        follow._recheck["at"] = 0
        follow.run_once()
        self.assertEqual(follow.monitor()["last_24h"]["new"], 3)

    def test_fix_skipped_removes_what_following_made(self):
        p = post(REDDIT, "Tai Po", "Tai Po https://www.scmp.com/news/hong-kong/article/3371300/x-y-z")
        score(p)
        follow.run_once()
        row = links_of(p)["https://www.scmp.com/news/hong-kong/article/3371300/x-y-z"]
        self.assertEqual(row["status"], "new")
        with mock.patch.object(follow, "skip_reason", return_value="section"):
            self.assertEqual(follow.fix_skipped(), (1, 1))
        self.assertEqual(links_of(p)["https://www.scmp.com/news/hong-kong/article/3371300/x-y-z"]["status"], "skipped")
        self.assertIsNone(corpus.db().execute("SELECT 1 FROM posts WHERE id=%s", (row["target_id"],)).fetchone())

    def test_failed_shortener_retried_then_given_up(self):
        self.hop.side_effect = TimeoutError("t.co timed out")
        p = post(REDDIT, "Tai Po", "Tai Po https://t.co/zzz")
        score(p)
        follow.run_once()
        row = links_of(p)["https://t.co/zzz"]
        self.assertEqual(row["status"], "pending")
        for _ in range(follow.TRIES):
            corpus.db().execute("UPDATE follow_links SET next_try = '2000-01-01' WHERE status = 'pending'")
            follow.run_once()
        row = links_of(p)["https://t.co/zzz"]
        self.assertEqual(row["status"], "failed")
        self.assertIn("timed out", row["error"])
        self.assertEqual(follow.monitor()["last_24h"]["failed"], 1)

    def test_monitor_counts(self):
        orig = post(OPED, "Tai Po fire", sid="scmp_hk", source="南華早報")
        p = post(REDDIT, "Tai Po", f"Tai Po {OPED} {OTHER} https://www.scmp.com/ https://img.scmp.com/a.png")
        score(p)
        follow.run_once()
        m = monitor.pipeline()["follow"]
        self.assertEqual(m["last_24h"]["posts"], 1)
        self.assertEqual(m["last_24h"]["links"], 4)
        self.assertEqual((m["last_24h"]["duplicate"], m["last_24h"]["new"], m["last_24h"]["skipped"]), (1, 1, 2))
        self.assertEqual({s["reason"] for s in m["skipped"]}, {"home", "media"})
        self.assertTrue(orig)


ISTSS = "https://istss.org/trauma-and-diversity-a-case-for-including-spirituality-in-healing-trauma-amanda-j-khan-phd/"
ARTICLE = ("<html><head><title>Trauma and Diversity – ISTSS</title><meta property=\"og:site_name\" content=\"ISTSS\">"
           "</head><body><article><h1>Trauma and Diversity</h1>" + "".join(
               f"<p>Paragraph {i}: the American Psychological Association adopted new multicultural guidelines for "
               f"psychologists, and spirituality is part of them.</p>" for i in range(6)) + "</article></body></html>")


class Generic(Base):
    """a site no source reads (Deyao, 2026-10-21: "add these sources"): its linked page is read by the generic reader
    and becomes a post when it is an article; forms, shops, file shares and held sites stay listed for Deyao"""
    def follow_one(self, link, page=None):
        if page is not None:
            self.gpage.return_value = page
        self.n = getattr(self, "n", 0) + 1
        p = post(REDDIT + str(self.n), "Tai Po", "Tai Po " + link)
        score(p)
        follow.run_once()
        return links_of(p)[link]

    def test_article_becomes_a_post_of_its_site(self):
        row = self.follow_one(ISTSS, ARTICLE)
        self.assertEqual(row["status"], "new")
        p = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (row["target_id"],)).fetchone()
        self.assertEqual((p["sid"], p["source"], p["source_type"], p["region"]), ("link_istss_org", "ISTSS", "Blog", "Global"))
        self.assertFalse(p["first_run"])  # a followed page is never a backlog, even as its sid's first post
        self.assertEqual(self.reads, [ISTSS])  # read in full by the normal reader
        self.assertEqual(follow.monitor()["unknown_sites"], [])

    def test_government_page_is_official_hk(self):
        url = "https://www.policyaddress.gov.hk/2026/en/chapter4.html"
        row = self.follow_one(url, ARTICLE.replace("ISTSS", "Policy Address"))
        p = corpus.db().execute("SELECT * FROM posts WHERE id=%s", (row["target_id"],)).fetchone()
        self.assertEqual((p["sid"], p["source_type"], p["region"]), ("link_policyaddress_gov_hk", "Official", "HK"))

    def test_not_articles_stay_listed_with_the_reason(self):
        cases = {
            "https://forms.office.com/r/abc123": (ARTICLE, "document or form service"),
            "https://www.dropbox.com/scl/fo/abc/xyz": (ARTICLE, "document or form service"),
            "https://www.example-form.hk/apply/1": ("<script>var FB_PUBLIC_LOAD_DATA_ = [];</script>" + ARTICLE, "form"),
            "https://www.somebroker.com/tc/page/open-account?ref=1":
                ('<form>' + '<input type="text" name="a">' * 6 + "</form>" + ARTICLE, "form"),
            "https://www.someshop.hk/product/detail/id/2700": (ARTICLE + "<button>加入購物車</button>", "shop page"),
            "https://www.somesummit.hk/2026/home": ("<html><h1>Summit 2026</h1><p>Women's strengths</p></html>",
                                                                   "no article text"),
        }
        for url, (page, why) in cases.items():
            row = self.follow_one(url, page)
            self.assertEqual((row["status"], row["reason"]), ("unknown", why), url)
        self.assertFalse(corpus.db().execute("SELECT 1 FROM posts WHERE left(sid, 5) = 'link_'").fetchone())
        hosts = {s["host"]: s["reason"] for s in follow.monitor()["unknown_sites"]}
        self.assertEqual(hosts["someshop.hk"], "shop page")

    def test_held_site_is_not_read(self):
        self.gpage.side_effect = AssertionError("a held site's page is not read")
        with mock.patch.dict(follow.HOLD, {"example-farm.ru": "content farm: waiting on Deyao"}):
            row = self.follow_one("https://geosirius.example-farm.ru/trumps-speech-tonight-4")
        self.assertEqual(row["status"], "unknown")
        self.assertIn("waiting on Deyao", row["reason"])

    def test_sites_deyao_ignored(self):
        """Deyao, 2026-10-21: "ignore all sites on the table": skipped as ignored, never read, off the list"""
        self.gpage.side_effect = AssertionError("an ignored link's page is not read")
        for u in ("https://drive.google.com/drive/folders/15qLYrh879zNfQtXp7YYNb2DTAP0PeHdD?usp=sharing",
                  "https://forms.cloud.microsoft/pages/responsepage.aspx?id=DQSIkWds",
                  "https://docs.google.com/forms/d/e/1FAIpQLSf0EEY/viewform", "https://www.gbb.shop/product/detail/id/2700",
                  "https://mingtakfn.com/tc/page/open-account?ref=11168", "https://geosirius.ifz.ru/trumps-speech-tonight-4",
                  "http://www.familyandwomensummit.hk/hyab2026/home",
                  "https://www.elections.gov.hk/ecss2026/chi/nomination2.html", "https://www.elections.gov.hk/ecss2026/eng/index.html",
                  "https://www.bpahk.org/%E5%8A%A0%E5%85%A5%E7%B6%93%E6%B0%91%E8%81%AF-%E5%BE%AE%E4%BF%A1%E5%85%AC%E7%9C%BE%E8%99%9F/"):
            row = self.follow_one(u)
            self.assertEqual((row["status"], row["reason"]), ("skipped", follow.IGNORED), u)
        for u in ("https://www.elections.gov.hk/ecss2026/chi/nomination.html",
                  "https://www.bpahk.org/%e9%81%b8%e8%b3%a2%e4%bb%bb%e8%83%bd-%e8%a9%95/"):  # kept: read as before
            self.assertIsNone(follow.skip_reason(u), u)
        self.assertEqual(follow.monitor()["unknown_sites"], [])

    def test_listed_links_deyao_ignored_leave_the_list(self):
        p = post(REDDIT, "Tai Po", "Tai Po")
        u = "https://www.gbb.shop/product/detail/id/2700/filter_category_id/2"
        corpus.db().execute("""INSERT INTO follow_links (post_id, origin, url, final_url, host, status, reason, at, tries)
                               VALUES (%s, %s, %s, %s, 'gbb.shop', 'unknown', 'shop page', %s, 0)""", (p, REDDIT, u, u, now()))
        follow._recheck["at"] = 0
        follow.run_once()
        self.assertEqual((links_of(p)[u]["status"], links_of(p)[u]["reason"]), ("skipped", follow.IGNORED))

    def test_page_that_cannot_be_read_is_tried_again(self):
        self.gpage.side_effect = TimeoutError("read timed out")
        row = self.follow_one(ISTSS)
        self.assertEqual(row["status"], "pending")

    def test_links_listed_before_the_generic_reader_go_through_again(self):
        p = post(REDDIT, "Tai Po", "Tai Po")
        corpus.db().execute("""INSERT INTO follow_links (post_id, origin, url, final_url, host, status, at, tries)
                               VALUES (%s, %s, %s, %s, 'istss.org', 'unknown', %s, 0)""", (p, REDDIT, ISTSS, ISTSS, now()))
        self.gpage.return_value = ARTICLE
        follow._recheck["at"] = 0
        follow.run_once()
        self.assertEqual(links_of(p)[ISTSS]["status"], "new")

    def test_qr_page_and_reurl_interstitials(self):
        pages = {"https://qr.page/g/4FWHtrF4xki": (302, "https://qrcodes.pro/4FWHtrF4xki", ""),
                 "https://qrcodes.pro/4FWHtrF4xki": (200, None, '<a href="https://www.hk01.com/a/1" id="redirect_link">Click here</a>'
                                                     '<script>window.location = "https://www.uniqode.com/qr-code-generator";</script>'),
                 "https://reurl.cc/v5NMVe": (200, None, '<input type="hidden" id="target" name="target" value="https://www.hk01.com/a/2">')}
        self.hop.side_effect = lambda u: pages[u]
        self.assertEqual(follow.expand("https://qr.page/g/4FWHtrF4xki"), "https://www.hk01.com/a/1")
        self.assertEqual(follow.expand("https://reurl.cc/v5NMVe"), "https://www.hk01.com/a/2")

    def test_title_from_the_one_h1_and_chinese_text_is_hk(self):
        page = ("<html><head><title>VA.gov | Veterans Affairs</title></head><body><h1>Addressing Spiritual Dimensions"
                " of Trauma</h1>" + ARTICLE.split("<body>")[1].replace("<h1>Trauma and Diversity</h1>", ""))
        self.gpage.return_value = page
        self.read.side_effect = lambda pid, url, sid: (setattr(cache_fetch._seen, "page", page), fulltext_fetch.save(
            pid, "Paragraph about trauma and spirituality, long enough to be an article. " * 10, None, None))
        row = self.follow_one("https://www.ptsd.va.gov/professional/treat/txessentials/spirituality_trauma.asp")
        p = corpus.db().execute("SELECT title, region FROM posts WHERE id=%s", (row["target_id"],)).fetchone()
        self.assertEqual((p["title"], p["region"]), ("Addressing Spiritual Dimensions of Trauma", "Global"))
        self.read.side_effect = lambda pid, url, sid: (setattr(cache_fetch._seen, "page", ARTICLE), fulltext_fetch.save(
            pid, "選賢任能優中選優，提高參政議政質素，是完善選舉制度後香港政治的要求。" * 10, None, None))
        row = self.follow_one("https://www.bpahk.org/opinion/2026/10/merit-based-selection/", ARTICLE)
        p = corpus.db().execute("SELECT region FROM posts WHERE id=%s", (row["target_id"],)).fetchone()
        self.assertEqual(p["region"], "HK")

    def test_a_listing_that_is_no_outlet(self):
        """the ECSS 2026 press-release listing sits on elections.gov.hk but lists info.gov.hk's pages ("outlet":
        false): a link to elections.gov.hk goes to the generic reader, not to that source"""
        import sites_fetch
        follow._outlets.clear()
        with mock.patch.object(sites_fetch, "load", return_value=[
                {"id": "hk_elections_ecss2026_press", "name": "ECSS press", "url": "https://www.elections.gov.hk/ecss2026/chi/press.html",
                 "kind": "listing", "type": "Official", "region": "HK", "href": "x", "outlet": False}]):
            self.assertIsNone(follow.outlet_for("https://www.elections.gov.hk/ecss2026/chi/nomination2.html"))
        row = self.follow_one("https://www.elections.gov.hk/ecss2026/chi/results.html", "<table><tr><td>候選人</td></tr></table>")
        self.assertEqual((row["status"], row["reason"]), ("unknown", "no article text"))

    def test_followed_article_of_a_new_source_stays_its_backlog(self):
        """a watched source's sid first seen through link following: that post is its first read (backlog), so the
        source's own first read that follows is backlog too"""
        follow._outlets.clear()
        follow._outlets["__loaded__"] = ("x", "x")
        follow._outlets["newsite.hk"] = ("hk_newsite", "New Site")
        row = self.follow_one("https://www.newsite.hk/news/2026/10/21/story-one")
        self.assertEqual(row["status"], "new")
        self.assertTrue(corpus.db().execute("SELECT first_run FROM posts WHERE id=%s", (row["target_id"],)).fetchone()[0])

    def test_bitly_preview_page_then_a_plain_client(self):
        calls = []

        def hop(u, browser=True):
            calls.append(browser)
            return (200, None, "<title>Bitly | bit.ly/3VXoXSr</title>") if browser else (301, ISTSS, "")
        self.hop.side_effect = hop
        self.assertEqual(follow.expand("https://bit.ly/IOMedForum"), ISTSS)
        self.assertEqual(calls, [True, False])

    def test_straits_times_addresses_are_one_article(self):
        a = "https://www.straitstimes.com/asia/east-asia/hong-kong-wraps-up-probe-into-preventable-wang-fuk-court-fire"
        self.assertEqual(corpus.url_key(a + "?ref=inline-article"), corpus.url_key(a + "?ai-allowed=1"))


class ItemPage(Base):
    """the post's links on its page (Deyao, 2026-10-20: Dimsum Daily's X post "has a link, but it's missing"):
    resolved with the outlet once followed, as they stand before; Python's item.view and the edge app's agree"""
    def test_links_shown(self):
        import edge_config, item
        sys.path.insert(0, os.path.join(HERE, "tests"))
        from edge_call import edge
        edge_config.publish()
        dimsum = "https://www.dimsumdaily.hk/hong-kong-showcases-strengths-at-apec/"
        art = post(dimsum, "Hong Kong showcases strengths at APEC", sid="gnews_en_0", source="Dimsum Daily")
        x = post("https://x.com/dimsumdaily_hk/status/2112465766004166882", "Hong Kong showcases strengths at APEC…",
                 "Hong Kong showcases strengths at APEC " + dimsum + " https://www.facebook.com/sharer/sharer.php?u=1",
                 sid="watch_x", source="Dimsum Daily (X)")
        before = item.view(x)["links"]
        self.assertEqual([l["url"] for l in before], [dimsum, "https://www.facebook.com/sharer/sharer.php?u=1"])
        self.assertEqual(edge("item.view", x)["links"], before)
        score(x, "CEHK", 75)
        follow.run_once()
        got = item.view(x)["links"]
        self.assertEqual(got, [{"url": dimsum, "host": "dimsumdaily.hk", "name": "Dimsum Daily", "item_id": art,
                                "status": "duplicate"}])  # the share button is not shown
        self.assertEqual(edge("item.view", x)["links"], got)


if __name__ == "__main__":
    unittest.main()
