#!/usr/bin/env python3
"""Whole post text for the social, video and forum collectors (Deyao, 2026-10-20: a post with no separate article counts
as full text when its WHOLE text is stored; a video's full description, a forum thread's first post). On a throwaway
database (TEST_DATABASE_URL), no network (real answers in fixtures/youtube/ and fixtures/fulltext/):

1. YouTube posts carried no description: the yt_* / rthk_youtube uploads playlist page has none (92 of 99 posts empty
   since 09:15), TikHub's search gives only `description_snippet`, read as "description" (68 of 71 empty), and the
   watched channels' uploads-page fallback (YouTube's feed outage) had none. Each video's whole description now comes
   from YouTube's player answer (videoDetails.shortDescription), once per video; `sources.py youtube-text` fills the
   stored ones.
2. Reddit (TikHub, need_format): content is {"markdown": …}, read only as a string, so 19 of 20 posts had no text; a
   link post's text is the address it links to.
3. Every TikHub adapter but X cut a post's text at 2000 characters with "…" (28 TikTok, 5 Facebook posts in 3 days).
5. 香港討論區's printable view: an image's onload="if(this.width >screen.width*0.8) …" attribute (its ">" ended the
   tag for the tag stripper), the commented-out copy of the image and the attachment notes (「圖片附件: … 該附件被下載
   次數 0」, the attachment link, the file menu) were kept as the post's text (2477 of 60931 posts since 09:15).
4. 香港討論區 threads found through Google News (gnews_discuss, 583 of 590 empty) are filed under the thread's archiver
   address, where the replies job puts the opening post; `sources.py discuss-text` fills the stored ones.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_fulltext_social.py
"""
import json, os, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.pop("TIKHUB_REPLAY_DIR", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, sources, tikhub_fetch, watch_fetch  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

YT = os.path.join(HERE, "tests", "fixtures", "youtube")
FT = os.path.join(HERE, "tests", "fixtures", "fulltext")
HKT = timezone(timedelta(hours=8))
RTHK = next(s for s in sources.SOURCES if s[0] == "rthk_youtube")


def player():
    with open(os.path.join(YT, "player_Dv2_7coivdI.json")) as f:
        return json.load(f)


def uploads():
    import gzip
    return gzip.open(os.path.join(YT, "rthk_uploads.html.gz")).read().decode()


def answer(name):
    with open(os.path.join(FT, name)) as f:
        return json.load(f)["data"]


DESC = player()["videoDetails"]["shortDescription"].strip()
WHEN = datetime(2026, 10, 12, 11, 40, 17, tzinfo=HKT)


def fake_player(vid):
    return {"published": WHEN, "text": DESC}


def clear():
    corpus.db().execute("DELETE FROM posts")
    corpus.db().execute("DELETE FROM kv")


class YouTubeDescription(unittest.TestCase):
    def setUp(self):
        clear()

    def test_player_answer_gives_the_whole_description(self):
        self.assertGreater(len(DESC), 300)
        self.assertEqual(sources.youtube_player_text(player()), DESC)
        self.assertIsNone(sources.youtube_player_text({"playabilityStatus": {"status": "ERROR"}}))

    def test_channel_playlist_posts_get_the_description(self):
        with mock.patch.object(sources, "fetch", return_value=uploads()), \
                mock.patch.object(sources, "youtube_player", side_effect=fake_player) as m:
            got = sources.fetch_source(RTHK)
        self.assertTrue(got)
        self.assertEqual(m.call_count, min(len(got), sources.YT_INFO_MAX))
        self.assertTrue(all(g.get("text") == DESC for g in got[:sources.YT_INFO_MAX]))
        # read once: the next poll asks the player only for the rest of the backlog (YT_INFO_MAX a call)
        with mock.patch.object(sources, "fetch", return_value=uploads()), \
                mock.patch.object(sources, "youtube_player", side_effect=fake_player) as m:
            again = sources.fetch_source(RTHK)
        self.assertEqual(m.call_count, min(sources.YT_INFO_MAX, max(0, len(got) - sources.YT_INFO_MAX)))
        self.assertEqual(again[0].get("text"), DESC)

    def test_stored_with_text_is_not_read(self):
        rows = sources.youtube(uploads())
        corpus.ingest([{"sid": "rthk_youtube", "source": "RTHK", "url": rows[0]["url"], "title": rows[0]["title"],
                        "text": "a description", "published": "2026-10-14T09:30+08:00"}])
        with mock.patch.object(sources, "youtube_player", side_effect=fake_player) as m:
            info = sources.youtube_info(rows[:3])
        self.assertEqual(m.call_count, 2)
        self.assertNotIn(rows[0]["url"], info)

    def test_watched_channel_outage_fallback_has_the_description(self):
        with mock.patch.object(sources, "fetch", return_value=uploads()), \
                mock.patch.object(sources, "youtube_player", side_effect=fake_player):
            got = watch_fetch.youtube_uploads({"id": "UC6of7UYhctnYmqABjUqzuxw", "name": "RTHK"})
        self.assertTrue(got)
        self.assertTrue(got[0]["text"].startswith(got[0]["title"]))
        self.assertIn(" ".join(DESC.split()), got[0]["text"])

    def test_tikhub_search_gets_the_whole_description(self):
        with mock.patch.object(tikhub_fetch, "call", return_value=answer("tikhub_youtube_search.json")), \
                mock.patch.object(sources, "youtube_player", side_effect=fake_player):
            got = tikhub_fetch.youtube("大埔火災")
        self.assertEqual(len(got), 3)
        for g in got:
            self.assertTrue(g["text"].startswith(g["title"]))
            self.assertIn(" ".join(DESC.split()), g["text"])

    def test_tikhub_search_keeps_the_snippet_without_a_player_answer(self):
        with mock.patch.object(tikhub_fetch, "call", return_value=answer("tikhub_youtube_search.json")), \
                mock.patch.object(sources, "youtube_player", return_value=None):
            got = tikhub_fetch.youtube("大埔火災")
        by = {g["url"]: g for g in got}
        self.assertIn("本集由簡賢文教授從臺北車站", by["https://www.youtube.com/watch?v=mNTQa0rnV3M"]["text"])

    def test_youtube_text_fills_stored_posts(self):
        corpus.ingest([
            {"sid": "yt_5JfM27CQ", "source": "TVB", "url": "https://www.youtube.com/watch?v=ADyW6n8ptF4", "title": "APEC",
             "published": datetime.now(HKT).isoformat(timespec="minutes")},
            {"sid": "tikhub_youtube", "source": "x (YouTube)", "url": "https://www.youtube.com/watch?v=wdL9RlH5MFw",
             "title": "郭Sir財經", "published": datetime.now(HKT).isoformat(timespec="minutes")}])
        with mock.patch.object(sources, "youtube_player", side_effect=fake_player):
            self.assertEqual(sources.youtube_fill_text(), 2)
        t = {r["sid"]: r["text"] for r in corpus.db().execute("SELECT sid, text FROM posts")}
        self.assertEqual(t["yt_5JfM27CQ"], corpus._clean(DESC))
        self.assertTrue(t["tikhub_youtube"].startswith("郭Sir財經\n"))

    def test_item_page_knows_the_description_is_due(self):
        import fulltext_fetch
        corpus.ingest([{"sid": "yt_LyP6K8lQ", "source": "ISD", "url": "https://www.youtube.com/watch?v=f-Zvashrg0M",
                        "title": "特首寄語學生", "published": datetime.now(HKT).isoformat(timespec="minutes")}])
        row = lambda: dict(corpus.db().execute("SELECT * FROM posts WHERE sid='yt_LyP6K8lQ'").fetchone())
        self.assertTrue(fulltext_fetch.will_read(row()))  # Video is not a page-read type: due all the same
        with mock.patch.object(sources, "youtube_player", return_value={"published": WHEN, "text": ""}):
            self.assertEqual(sources.youtube_fill_text(), 0)  # no description: tried, nothing to keep
        self.assertIsNotNone(row()["cached_at"])
        self.assertFalse(fulltext_fetch.will_read(row()))

    def test_unanswered_player_stays_due(self):
        import fulltext_fetch
        corpus.ingest([{"sid": "rthk_youtube", "source": "RTHK", "url": "https://www.youtube.com/watch?v=Dv2_7coivdI",
                        "title": "非凡人物", "published": datetime.now(HKT).isoformat(timespec="minutes")}])
        with mock.patch.object(sources, "youtube_player", return_value=None):
            sources.youtube_fill_text()
        r = dict(corpus.db().execute("SELECT * FROM posts").fetchone())
        self.assertIsNone(r["cached_at"])
        self.assertTrue(fulltext_fetch.will_read(r))


class Reddit(unittest.TestCase):
    def test_markdown_body_and_link_posts(self):
        with mock.patch.object(tikhub_fetch, "call", return_value=answer("tikhub_reddit_search.json")):
            got = {g["url"].split("/comments/")[1].split("/")[0]: g for g in tikhub_fetch.reddit("Tai Po fire")}
        self.assertIn("Most discussions about FIRE end with one number", got["1xan5f6"]["text"])
        self.assertTrue(got["1xan5f6"]["text"].endswith("And do you even consider tax on withdrawals?"))
        long = got["1xamnov"]["text"]
        self.assertGreater(len(long), 2000)  # whole, not cut at 2000 with "…"
        self.assertTrue(long.endswith("end of the post."))
        self.assertIn("https://www.scmp.com/opinion/hong-kong-opinion/article/3371144/", got["1xam1vb"]["text"])


class TextLimit(unittest.TestCase):
    def test_tiktok_caption_over_2000_characters_is_whole(self):
        desc = "大埔宏福苑火災調查 " * 300 + "完"
        d = {"search_item_list": [{"aweme_info": {
            "aweme_id": "7560000000000000001", "desc": desc, "create_time": 1760925600,
            "author": {"unique_id": "hk_news", "nickname": "HK News"}, "statistics": {"play_count": 5}}}]}
        with mock.patch.object(tikhub_fetch, "call", return_value=d):
            got = tikhub_fetch.tiktok("大埔")
        self.assertEqual(got[0]["text"], tikhub_fetch.clean(desc, 10 ** 6))
        self.assertTrue(got[0]["text"].endswith("完"))


class DiscussThreads(unittest.TestCase):
    URLS = {  # gnews_discuss posts as stored, 2026-10-20 → their thread's archiver address
        "https://www.discuss.com.hk/viewthread.php?tid=32329067&utm_source=discuss_latest_reply_threads&utm_medium=click": "32329067",
        "https://m.discuss.com.hk/index.php?action=thread&tid=32328647&utm_source=discuss_forumdisplay&utm_medium=click": "32328647",
        "https://www.discuss.com.hk/redirect.php?tid=32329058&goto=lastpost": "32329058",
        "https://www.discuss.com.hk/viewthread.php?tid=32328664&extra=page%3D1&page=2": "32328664",
        "https://www.discuss.com.hk/index.php?action=thread&tid=32328082&extra=page%3D1": "32328082",
        "https://m.discuss.com.hk/index.php?action=thread&tid=32328953&extra=page=1105&utm_source=discuss_forumdisplay": "32328953",
        "https://discuss.com.hk/viewthread.php?tid=32300001": "32300001",
    }

    def test_thread_links_become_the_archiver_address(self):
        for u, tid in self.URLS.items():
            self.assertEqual(sources.discuss_url(u), f"https://www.discuss.com.hk/archiver/?tid-{tid}.html", u)
        for u in ("https://www.discuss.com.hk/forumdisplay.php?fid=1", "https://www.discuss.com.hk/archiver/?tid-1.html#c3",
                  "https://www.discuss.com.hk/index.php?action=forumdisplay&fid=2&tid=5", "https://lihkg.com/thread/1/page/1"):
            self.assertIsNone(sources.discuss_url(u), u)

    def test_discuss_text_fills_stored_google_news_posts(self):
        clear()
        corpus.ingest([
            {"sid": "discuss", "source": "香港討論區", "url": "https://www.discuss.com.hk/archiver/?tid-32328953.html",
             "title": "假日之晚餐@有魚有蟹", "text": "晚去剛返、食飽就瞓。", "author": "abc"},
            {"sid": "gnews_discuss", "source": "香港討論區 (via Google News)", "title": "假日之晚餐@有魚有蟹",
             "url": "https://www.discuss.com.hk/viewthread.php?tid=32328953&extra=page%3D1105"},
            {"sid": "gnews_discuss", "source": "香港討論區 (via Google News)", "title": "流量密碼",
             "url": "https://m.discuss.com.hk/index.php?action=thread&tid=32328647"}])
        self.assertEqual(sources.discuss_fill_text(), 1)
        r = corpus.db().execute("SELECT text, author FROM posts WHERE url LIKE '%%viewthread%%'").fetchone()
        self.assertEqual((r["text"], r["author"]), ("晚去剛返、食飽就瞓。", "abc"))


class DiscussPrintable(unittest.TestCase):
    STORED = ("⭕⭕水母 (1).png (494.57 KB)\n2026-10-20 11:49\n 圖片附件: ⭕⭕水母 (1).png (2026-10-20 11:49, 494.57 KB) / "
              "該附件被下載次數 0\nhttps://www.discuss.com.hk/attachment.php?aid=17224651 screen.width*0.8) "
              "this.width=screen.width*0.8\" alt=\"\" />-->\nscreen.width*0.8) this.width=screen.width*0.8\" alt=\"\" />")

    def test_stored_texts_are_cleaned(self):
        import replies_fetch
        clear()
        corpus.ingest([{"sid": "discuss", "source": "香港討論區", "url": "https://www.discuss.com.hk/archiver/?tid-32329299.html",
                        "title": "⭕⭕水母", "text": self.STORED},
                       {"sid": "discuss", "source": "香港討論區", "url": "https://www.discuss.com.hk/archiver/?tid-32327648.html",
                        "title": "t", "text": "今日好熱\n圖片附件: a.jpg (2026-10-20 10:00, 1.2 MB) / 該附件被下載次數 3\n"
                                              "https://www.discuss.com.hk/attachment.php?aid=1 screen.width*0.8) this.width=screen.width*0.8\" alt=\"\" />"}])
        self.assertEqual(replies_fetch.discuss_clean_stored(), 2)
        t = [r["text"] for r in corpus.db().execute("SELECT text FROM posts ORDER BY url")]
        self.assertEqual(t, ["今日好熱", ""])

    def test_image_attributes_and_attachment_notes_are_not_text(self):
        import replies_fetch
        with open(os.path.join(FT, "discuss_printable_32329299.html"), encoding="utf-8") as f:
            title, posts = replies_fetch.discuss_printable(f.read())
        self.assertEqual(title, "⭕⭕水母")
        text = posts[0]["text"]
        for junk in ("screen.width", "-->", "該附件被下載次數", "attachment.php", "494.57 KB"):
            self.assertNotIn(junk, text)
        self.assertEqual(text, "")  # a post of one picture: no words of its own

    def test_tags_with_a_quoted_greater_than_sign(self):
        import replies_fetch
        self.assertEqual(replies_fetch.plain('前<img src="a.png" onload="if(this.width >screen.width*0.8) x" alt="" />後'), "前後")
        self.assertEqual(replies_fetch.plain("好<!--<img src=x>-->嘢"), "好嘢")


if __name__ == "__main__":
    unittest.main()
