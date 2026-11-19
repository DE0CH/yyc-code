#!/usr/bin/env python3
"""Posts that are only a link (Deyao, 2026-10-15): an X post by 伊凡投資心得 (HKIC, 14 Oct 17:45) showed as the title
"https://t.co/3DS8gH0wK7" with 「來源只提供標題」 and was scored and filed anyway. The tweet is an X Article: TikHub's
answer carries the article's title, preview and (tweet detail) full text, which the collectors dropped; t.co links were
not expanded; the title step skipped a body that short; the scorer scored the bare URL. On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again), no network: saved real TikHub answers and pages in
fixtures/x/, the model stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_link_posts.py

Covers:
  1. One X parser for search, user timeline and tweet detail: the tweet's full text with t.co links expanded (media
     t.co links dropped), the quoted tweet's text, an X Article's title and text, photos (→ images.py), videos (→ the
     transcription queue, best mp4), engagement, author name and handle. Long tweets are kept whole.
  2. The collectors (tikhub_fetch.twitter, watch_fetch.x) give that content; an X Article's full text comes from the
     tweet detail (one request, metered).
  3. Ingest links a post's photos and videos to images.py and media.py.
  4. links.py: a post whose own text is (mostly) a link gets the linked page's title and description as a
     〔連結內容：…〕 block (og:title / og:description; YouTube by oEmbed, its video queued for transcription; an X post's
     own link by the tweet detail), within seconds of arriving; a failed fetch is tried again later, a few times.
  5. Its title is the linked page's title, kind "linked" (no model call; an X Article: its own title, none from AI), shown with title_linked (「連結標題」), not
     as AI-written, and 〔連結標題〕 on Telegram; a bare URL is never the title shown.
  6. The scorer holds a post whose content is only URLs until it is resolved; then scores it.
  7. The backfill re-reads stored X posts through the tweet detail (text, links, article, quoted, media, counts).
"""
import json, os, re, shutil, sys, tempfile, unittest
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
import ai, app, corpus, item, links, sources, streams, telegram_alerts, tikhub_fetch as th, titles, watch_fetch, watchlist  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

HKT = app.HKT
NOW = datetime.now(HKT)  # written into link_scan as links.py writes times (whole seconds): the times compare as text
FIX = os.path.join(HERE, "tests", "fixtures", "x")
CALLS = []
ARTICLE_ID = "2110305899994558889"
ARTICLE_TITLE = "每人每年5万美元的外汇限额，有什么办法可以突破呢？"


def fx(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return json.loads(f.read())["data"]


def page(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    CALLS.append((purpose, prompt))
    if purpose == "titles":
        body = prompt.split("Posts:\n", 1)[1]
        return {"titles": [{"i": int(i), "kind": "written", "title": "模型寫的標題測試"}
                           for i in re.findall(r'(?m)^<post i="(\d+)"', body)]}
    if not purpose.startswith("scoring"):
        return {}
    body = prompt.split("Posts:\n", 1)[1]
    return {"posts": [{"i": int(i), "sentiment": "neutral", "labels": [],
                       "streams": [{"id": "HKIC", "score": 80, "reason": "測試", "category": "其他"}]}
                      for i in re.findall(r"(?m)^(\d+)\. \[", body)]}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)
streams.WAIT_TEXT = timedelta(0)
streams.FIRST_WAIT = timedelta(seconds=-60)  # a social post (a unit) is not waiting for its comments here
streams.settings()
titles.SETTLE = timedelta(seconds=-60)


def replay(**files):
    """a TIKHUB_REPLAY_DIR holding the saved answers under the names the code asks for"""
    d = tempfile.mkdtemp()
    for tag, name in files.items():
        shutil.copy(os.path.join(FIX, name), os.path.join(d, tag + ".json"))
    return mock.patch.dict(os.environ, {"TIKHUB_REPLAY_DIR": d})


def row(url):
    r = corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()
    return dict(r) if r else None


def due_ids():
    """every post the scorer would take now: single posts and units (social posts with their comments)"""
    return [p["id"] for p in streams.due(500)] + [p["id"] for p in streams.unit_due(500)]


def by_id(items, tid):
    return next(x for x in items if x["url"].endswith("/status/" + tid))


class Parser(unittest.TestCase):
    def test_article_tweet_from_timeline(self):
        t = next(t for t in fx("user_yifaninvest.json")["timeline"] if t["tweet_id"] == ARTICLE_ID)
        x = th.x_tweet(t)
        self.assertNotIn("t.co/", x["text"])
        self.assertNotIn("x.com/i/article", x["text"])  # the article replaces its link (test_url_titles.py)
        self.assertEqual(x["title"], ARTICLE_TITLE)
        self.assertIn("2004年央行发布的《个人财产对外转移售付汇管理暂行办法》", x["text"])  # the preview
        self.assertEqual(x["article"], "2096065673881604096")

    def test_article_full_text_from_detail(self):
        x = th.x_tweet(fx("detail_article.json"))
        self.assertEqual(x["title"], ARTICLE_TITLE)
        self.assertIn("移民转移：条件与重要限制", x["text"])  # the article body, past its preview
        self.assertEqual((x["author"], x["handle"]), ("伊凡投資心得", "yifaninvest"))
        self.assertEqual(x["engagement"]["likes"], 127)
        self.assertEqual(x["engagement"]["views"], 32430)

    def test_search_tweet(self):
        t = next(t for t in fx("search.json")["timeline"] if t["tweet_id"] == "1854819694173470812")
        x = th.x_tweet(t)
        self.assertTrue(x["text"].endswith("詳情：https://reurl.cc/4dE8XR"), x["text"][-60:])
        self.assertEqual(x["images"], ["https://pbs.twimg.com/media/Gb2kaH8WkAAp8Sk.jpg"])
        self.assertEqual((x["author"], x["handle"]), ("通傳媒", "TongMediaHK"))
        self.assertEqual(x["engagement"]["shares"], 2)

    def test_long_tweet_kept_whole(self):
        t = next(t for t in fx("search.json")["timeline"] if t["tweet_id"] == "2110190824839663689")
        self.assertGreater(len(th.x_tweet(t)["text"]), 6000)

    def test_quoted(self):
        x = th.x_tweet(fx("detail_quote.json"))
        self.assertTrue(x["text"].startswith("感谢很重要的指正！"))
        self.assertIn("〔引用 Wiseman X 🇸🇬 💙💛 @wisemanxai〕@yifaninvest 写的很详细准确", x["text"])

    def test_video_to_transcription_queue(self):
        x = th.x_tweet(fx("detail_video.json"))
        self.assertEqual(len(x["clips"]), 1)
        cl = x["clips"][0]
        self.assertEqual(cl["key"], "x:2108011090617798656")
        self.assertEqual(cl["kind"], "direct")
        self.assertRegex(cl["url"], r"^https://video\.twimg\.com/.+\.mp4")
        mp4s = [v for v in fx("detail_video.json")["media"]["video"][0]["variants"] if v.get("content_type") == "video/mp4"]
        self.assertEqual(cl["url"], max(mp4s, key=lambda v: v.get("bitrate") or 0)["url"])  # the best mp4
        self.assertEqual(x["images"], [])  # a video's still frame is not a picture to read

    def test_photo_tweet(self):
        x = th.x_tweet(fx("detail_photo.json"))
        self.assertEqual(x["images"], ["https://pbs.twimg.com/media/HUb0hmwaEAAacMx.jpg"])
        self.assertNotIn("t.co/", x["text"])

    def test_link_tweet(self):
        x = th.x_tweet(fx("detail_link.json"))
        self.assertEqual(x["text"], "https://global.hk01.com/a/60399375")

    def test_retweet_reads_the_original(self):
        q = fx("detail_quote.json")["quoted"]
        t = dict(fx("detail_quote.json"), text="RT @wisemanxai: @yifaninvest 写的很详细准确…", quoted=None,
                 retweeted_tweet=q)
        x = th.x_tweet(t)
        self.assertTrue(x["text"].startswith("〔轉推 Wiseman X 🇸🇬 💙💛 @wisemanxai〕@yifaninvest 写的很详细准确，作为办理过的人"))
        self.assertTrue(x["retweet"])


class Collectors(unittest.TestCase):
    def test_search_adapter(self):
        with replay(twitter="search.json"):
            got = th.twitter("香港投資管理有限公司")
        x = by_id(got, "1854819694173470812")
        self.assertIn("https://reurl.cc/4dE8XR", x["text"])
        self.assertNotIn("t.co/", x["text"])
        self.assertEqual(x["images"], ["https://pbs.twimg.com/media/Gb2kaH8WkAAp8Sk.jpg"])
        self.assertEqual(x["author"], "通傳媒")
        self.assertGreater(len(by_id(got, "2110190824839663689")["text"]), 6000)
        v = by_id(got, "2108011322927718666")
        self.assertEqual(v["clips"][0]["key"], "x:2108011090617798656")

    def test_watch_reads_article_text(self):
        spent = []
        with replay(api_v1_twitter_web_fetch_user_post_tweet="user_yifaninvest.json",
                    **{"twitter_detail_" + ARTICLE_ID: "detail_article.json"}), \
                mock.patch.object(watchlist, "add_tikhub", lambda usd: spent.append(usd)):
            got = watch_fetch.x_posts(fx("user_yifaninvest.json"), {"handle": "yifaninvest"})
        p = by_id(got, ARTICLE_ID)
        self.assertEqual(p["title"], ARTICLE_TITLE)
        self.assertIn("移民转移：条件与重要限制", p["text"])  # full text through the detail
        self.assertNotIn("t.co/", p["text"])
        q = by_id(got, "2110504381485568456")  # a quote tweet: the quoted text is kept
        self.assertIn("〔引用 ", q["text"])
        self.assertTrue(spent)  # the detail request is metered

    def test_search_item_reaches_corpus_with_media(self):
        with replay(twitter="search.json"):
            got = th.twitter("香港投資管理有限公司")
        x = by_id(got, "2108011322927718666")
        x2 = by_id(got, "1854819694173470812")
        items = [dict({"sid": "tikhub_twitter", "source": y["source"], "section": "X · 香港投資管理有限公司",
                       "title": y["title"], "url": y["url"], "published": NOW.isoformat(timespec="seconds")},
                      **sources.extras("tikhub", y)) for y in (x, x2)]
        corpus.ingest(items)
        pid = row(x2["url"])["id"]
        imgs = [r["url"] for r in corpus.db().execute("SELECT url FROM post_images WHERE post_id=%s", (pid,))]
        self.assertEqual(imgs, ["https://pbs.twimg.com/media/Gb2kaH8WkAAp8Sk.jpg"])
        vid = row(x["url"])["id"]
        m = corpus.db().execute("SELECT m.* FROM post_media pm JOIN media m ON m.key = pm.key WHERE pm.post_id=%s",
                                (vid,)).fetchone()
        self.assertEqual((m["key"], m["kind"], m["status"]), ("x:2108011090617798656", "direct", "queued"))
        self.assertIn("https://reurl.cc/4dE8XR", row(x2["url"])["text"])


class Links(unittest.TestCase):
    def test_link_only(self):
        self.assertTrue(links.bare("https://t.co/3DS8gH0wK7"))
        self.assertTrue(links.bare("  https://youtu.be/SBeTwR4nSeI?si=AQ 　"))
        self.assertTrue(links.mostly_link("詳情：https://bit.ly/4hLyGnl"))
        self.assertFalse(links.bare("詳情：https://bit.ly/4hLyGnl"))
        self.assertFalse(links.mostly_link("李家超今日出席立法會答問會 https://bit.ly/4hLyGnl"))
        self.assertFalse(links.mostly_link("沒有連結的短帖"))
        self.assertFalse(links.mostly_link(f"https://x.com/i/article/1 〔連結內容：{ARTICLE_TITLE}〕合法合规"))

    def test_page_info(self):
        info = links.parse_page("https://global.hk01.com/a/60399375", page("page_hk01_global.html"))
        self.assertEqual(info["title"], "华裔富商邓丕兵被揭柬国电骗大王　捐款建警局　陈志被捕前出逃")
        self.assertTrue(info["desc"].startswith("半岛电视台调查节目101 East"))
        info = links.parse_page("https://www.am730.com.hk/article/1058419", page("page_am730.html"))
        self.assertEqual(info["title"], "伍富橋宣布BB早產一個月 太太梁兆楹榮升媽咪母子平安 | am730")

    def test_youtube_by_oembed(self):
        with mock.patch.object(links, "fetch_json", lambda url: fx_raw("oembed_youtube.json")):
            info = links.page_info("https://youtu.be/SBeTwR4nSeI?si=AQNX8V0_bGvJHDTp")
        self.assertTrue(info["title"].startswith("溫總講債券 I 20261014"))
        self.assertIn("溫鋼城投藝頻道", info["desc"])
        self.assertEqual(info["clips"][0]["key"], "yt:SBeTwR4nSeI")


def fx_raw(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return json.loads(f.read())


def threads_post(url, text):
    return {"sid": "instagram", "source": "am730 (Threads)", "section": "am730hk", "title": text[:160], "url": url,
            "published": NOW.isoformat(timespec="seconds"), "text": text, "author": "am730hk"}


class Resolve(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_link_post_resolved_titled_held_then_scored(self):
        url = "https://www.threads.net/@am730hk/post/DeeBeULE9lF"
        link = "https://www.am730.com.hk/article/1058419"
        corpus.ingest([threads_post(url, link)])
        corpus.db().execute("UPDATE posts SET first_run = FALSE")
        pid = row(url)["id"]
        # held: the scorer does not take a post that is only a URL
        self.assertNotIn(pid, due_ids())
        self.assertNotIn(pid, [p["id"] for p in streams.backfill_due(500, app.STREAMS)])
        # a failed fetch: still held, tried again later
        with mock.patch.object(links, "fetch_page", side_effect=OSError("timed out")):
            links.run_once()
        self.assertNotIn(pid, due_ids())
        st = corpus.db().execute("SELECT * FROM link_scan WHERE post_id=%s", (pid,)).fetchone()
        self.assertEqual((st["tries"], st["status"]), (1, "pending"))
        self.assertGreater(st["next_try"], NOW.isoformat(timespec="seconds"))
        links.run_once()  # not due yet: nothing fetched
        corpus.db().execute("UPDATE link_scan SET next_try=%s WHERE post_id=%s", (NOW.isoformat(timespec="seconds"), pid))
        with mock.patch.object(links, "fetch_page", lambda u: page("page_am730.html")):
            self.assertEqual(links.run_once(), 1)
        r = row(url)
        self.assertIn("〔連結內容：伍富橋宣布BB早產一個月 太太梁兆楹榮升媽咪母子平安 | am730〕", r["text"])
        self.assertTrue(r["text"].startswith(link))
        self.assertIn(pid, due_ids())  # now scored
        # its title: the linked page's, no model call, marked as from the link
        titles.run_once()
        r = row(url)
        self.assertEqual((r["ai_title"], r["ai_title_kind"]), ("伍富橋宣布BB早產一個月 太太梁兆楹榮升媽咪母子平安 | am730", "linked"))
        self.assertFalse([p for k, p in CALLS if k == "titles" and "am730" in p])
        v = item.view(pid)
        self.assertEqual(v["title"], r["ai_title"])
        self.assertTrue(v["title_linked"])
        self.assertFalse(v["title_by_ai"])
        self.assertIn("伍富橋與太太梁兆楹", v["extract"] or v.get("text") or "")
        self.assertEqual(titles.plain({"title": r["ai_title"], "title_linked": True}),
                         "〔連結標題〕伍富橋宣布BB早產一個月 太太梁兆楹榮升媽咪母子平安 | am730")
        streams.run_once(app.STREAMS)
        self.assertTrue(corpus.db().execute("SELECT 1 FROM scored WHERE post_id=%s", (pid,)).fetchone())
        got = corpus.db().execute(f"SELECT si.item, {titles.ITEM_COLS} FROM stream_items si {titles.ITEM_JOIN} "
                                  f"WHERE si.url=%s", (url,)).fetchone()
        self.assertTrue(got)  # filed in 串流 (HKIC 80) under the linked page's title
        got = titles.item_row(dict(got))
        self.assertEqual(got["title"], r["ai_title"])
        self.assertTrue(got["title_linked"])
        self.assertFalse(got["title_by_ai"])

    def test_gives_up_after_tries_then_rejected(self):
        url = "https://www.threads.net/@x/post/gone"
        corpus.ingest([threads_post(url, "https://example.invalid/gone")])
        pid = row(url)["id"]
        with mock.patch.object(links, "fetch_page", side_effect=OSError("dns")):
            for _ in range(links.TRIES):
                corpus.db().execute("UPDATE link_scan SET next_try=%s WHERE post_id=%s", (NOW.isoformat(timespec="seconds"), pid))
                links.run_once()
        st = corpus.db().execute("SELECT * FROM link_scan WHERE post_id=%s", (pid,)).fetchone()
        self.assertEqual(st["status"], "failed")
        self.assertNotIn(pid, [p["id"] for p in links.due(50)])
        # given up: nothing more can come, so the scorer rejects it as near-empty (thin.py; it used to stay held for ever)
        streams.run_once(app.STREAMS)
        self.assertNotIn(pid, due_ids())
        self.assertFalse(corpus.db().execute("SELECT 1 FROM scored WHERE post_id=%s", (pid,)).fetchone())
        self.assertEqual(corpus.db().execute("SELECT status FROM thin_posts WHERE post_id=%s", (pid,)).fetchone()[0],
                         "rejected")

    def test_youtube_link_queues_its_video(self):
        url = "https://www.facebook.com/CISPRIDEPAGE/posts/pfbid0wx"
        corpus.ingest([dict(threads_post(url, "https://youtu.be/SBeTwR4nSeI?si=AQNX8V0_bGvJHDTp"),
                            sid="facebook", source="CISPRIDEPAGE (Facebook)")])
        with mock.patch.object(links, "fetch_json", lambda u: fx_raw("oembed_youtube.json")):
            links.run_once()
        r = row(url)
        self.assertIn("〔連結內容：溫總講債券 I 20261014", r["text"])
        self.assertTrue(corpus.db().execute("SELECT 1 FROM post_media WHERE post_id=%s AND key='yt:SBeTwR4nSeI'",
                                            (r["id"],)).fetchone())

    def test_bare_url_post_never_titled_by_url(self):
        """the reported post as stored: title the t.co URL, no text; resolved through the tweet detail"""
        url = f"https://x.com/yifaninvest/status/{ARTICLE_ID}"
        corpus.ingest([{"sid": "tikhub_twitter", "source": "伊凡投資心得 (X)", "section": "X · 香港投資管理有限公司",
                        "title": "https://t.co/3DS8gH0wK7", "url": url, "published": NOW.isoformat(timespec="seconds"),
                        "author": "伊凡投資心得"}])
        pid = row(url)["id"]
        self.assertNotIn(pid, due_ids())
        spent = []
        with replay(**{"twitter_detail_" + ARTICLE_ID: "detail_article.json"}), \
                mock.patch.object(watchlist, "add_tikhub", lambda usd: spent.append(usd)):
            links.run_once()
        r = row(url)
        self.assertEqual(r["title"], ARTICLE_TITLE)  # the X Article's own title and body
        self.assertIn("移民转移：条件与重要限制", r["text"])
        self.assertEqual(spent, [0.001])
        self.assertIn(pid, due_ids())
        titles.run_once()
        r = row(url)
        self.assertIsNone(r["ai_title"])  # it has its own title: none from AI
        v = item.view(pid)
        self.assertEqual(v["title"], ARTICLE_TITLE)
        self.assertFalse(v.get("title_by_ai"))
        self.assertNotRegex(v["title"], r"^https?://")


class Backfill(unittest.TestCase):
    def test_backfill_x_rereads_posts(self):
        url = "https://x.com/FosunWealthHK/status/2108011322927718666"
        corpus.ingest([{"sid": "watch_x", "source": "復星財富 (X)", "section": "FosunWealthHK",
                        "title": "復星財富國際控股有限公司…", "url": url, "published": NOW.isoformat(timespec="seconds"),
                        "text": "復星財富國際控股有限公司—— AI驅動 https://t.co/abc"}])
        pid = row(url)["id"]
        with replay(twitter_detail_2108011322927718666="detail_video.json"), \
                mock.patch.object(watchlist, "add_tikhub", lambda usd: None):
            n = links.backfill_x(days=30)
        self.assertGreaterEqual(n["updated"], 1)
        r = row(url)
        self.assertIn("星財富，打造亞洲第一的AI投資神器", r["text"])
        self.assertNotIn("t.co/", r["text"])
        self.assertEqual(json.loads(r["engagement"])["likes"], fx("detail_video.json")["likes"])
        self.assertTrue(corpus.db().execute("SELECT 1 FROM post_media WHERE post_id=%s AND key='x:2108011090617798656'",
                                            (pid,)).fetchone())


if __name__ == "__main__":
    unittest.main(verbosity=2)
