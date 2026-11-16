#!/usr/bin/env python3
"""Full text for the broadcasters' sources (full-text scan of 20 Oct 2026, posts seen since 09:15): TVB, RTHK, NOW,
新城, 商業電台, 有線, HKET video. On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again),
the pages from fixtures (tests/fixtures/fulltext/), no network.

  - TVB: a story of one line (港股收市報23,867點，跌319點。, the rest is a chart picture) was stored as the page
    footer "Copyright 2026 TVB Limited" (3 of 153 posts).
  - NOW: a video item whose written text is two lines was stored as its player's "update your browser" notice.
  - RTHK: the story is the page's div.itemFullText; 《報章摘要》 (a video replay whose written text is one line) was
    stored as its dateline "2026-10-20 HKT 09:35".
  - NOW's Telegram channel (now_telegram, Social Media) links each story's telegra.ph copy or its news.now.com page,
    and RTHK's YouTube uploads (rthk_youtube, Video) have their description: neither was ever read (only news-like
    types were), so 17 of 28 Telegram posts kept a one-line headline. telegra.ph's copy ends with its bot's trailer.
  - 商業電台 and RTHK publish a story as its headline or first lines and complete the same page later (881903
    2655518: 20 characters at 09:42, 731 at 17:00; 2655558: 99 → 709; RTHK 1874609: the title at 14:23, 583
    characters later): a page read short is read again (fulltext_fetch.reread).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_fulltext_broadcast.py
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
import cache_fetch, corpus, fulltext_fetch, fulltext_now  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

FIX = os.path.join(HERE, "tests", "fixtures", "fulltext")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def read(url, name):
    """fetch_one on a fixture page (cache_fetch.get answered from the file)"""
    with mock.patch.object(cache_fetch, "get", lambda u, proxy=None, headers=None: fixture(name)):
        return cache_fetch.fetch_one(url)[0]


def add(url, text, sid, source_type="News", source="來源"):
    at = (datetime.now(corpus.HKT) - timedelta(hours=4)).isoformat(timespec="seconds")
    corpus.ingest([{"sid": sid, "source": source, "title": "標題 " + url[-12:], "url": url,
                    "published": at, "seen": at, "text": text}])
    pid = corpus.ids_for_urls([url])[url]
    corpus.db().execute("UPDATE posts SET source_type=%s WHERE id=%s", (source_type, pid))
    return pid


def tried(pid, text, hours_ago):
    """the page was read hours_ago with this text"""
    corpus.set_text(pid, text)
    at = (datetime.now(corpus.HKT) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    corpus.db().execute("UPDATE posts SET cached_at=%s WHERE id=%s", (at, pid))


class Pages(unittest.TestCase):
    def test_tvb_one_line_story_is_not_the_copyright_footer(self):
        text = read("https://news.tvb.com/tc/1201762-港股收市報23867點跌319點", "tvb_1201762.html")
        self.assertEqual(text, "港股收市報23,867點，跌319點。")

    def test_now_video_story_is_its_written_lines_not_the_player_notice(self):
        """a NOW video item (英超精華) whose written text is two lines was stored as the old player's "update your
        browser" notice (2 of 133 NOW posts)"""
        text = read("https://news.now.com/home/local/player?newsId=666399", "now_666399.html")
        self.assertEqual(text, "【Now新聞台】10月20日英超賽事精華：\n熱刺 3:1 高雲地利")

    def test_rthk_story_is_its_item_text(self):
        text = read("https://news.rthk.hk/rthk/ch/component/k2/1874566-20261020.htm", "rthk_1874566.html")
        self.assertEqual(text, "每日搜羅本港主要報章要聞，緊貼社會動態。")  # a video replay: its one written line
        text = read("https://news.rthk.hk/rthk/ch/component/k2/1874583-20261020.htm", "rthk_1874583.html")
        self.assertTrue(text.startswith("人民銀行"), text[:40])
        self.assertIn("令政策利率與市場實際交易利率更加貼近。", text)
        self.assertNotIn("HKT", text)

    def test_telegraph_copy_without_the_bot_trailer(self):
        text = read("https://telegra.ph/曼城財務違規或被大量扣分提早確定降班-10-20", "telegraph_now.html")
        self.assertTrue(text.startswith("【Now新聞台】英超曼城違反財務規定罪成。"), text[:40])
        self.assertTrue(text.endswith("公布對曼城的判罰。"), text[-40:])
        self.assertNotIn("RSStT", text)


class Due(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM posts")

    def test_now_telegram_and_rthk_youtube_pages_are_read(self):
        tg = add("https://telegra.ph/APEC財長會一連兩日在香港舉行-10-20", "APEC財長會一連兩日在香港舉行", "now_telegram",
                 "Social Media", "NOW 新聞")
        yt = add("https://www.youtube.com/watch?v=oS4vZxpoDjM", None, "rthk_youtube", "Video", "RTHK 香港電台 (YouTube)")
        other = add("https://t.me/somechannel/12", "一則訊息", "tg_other", "Social Media")
        ids = [c[0] for c in fulltext_fetch.candidates(100)]
        self.assertIn(tg, ids)
        self.assertIn(yt, ids)
        self.assertNotIn(other, ids)  # other social posts: their text is the post itself
        rows = {r["id"]: dict(r) for r in corpus.db().execute(
            "SELECT id, url, sid, thread, source_type, text, cached_at, published, date_only, seen FROM posts").fetchall()}
        self.assertTrue(fulltext_now.wanted(rows[tg]))
        self.assertTrue(fulltext_fetch.will_read(rows[yt]))
        self.assertFalse(fulltext_now.wanted(rows[other]))

    def test_scoring_waits_for_the_linked_page(self):
        """complete data before AI (Deyao, 2026-10-15): a NOW Telegram headline is scored once its page was read"""
        import streams
        url = "https://telegra.ph/曼城財務違規或被大量扣分提早確定降班-10-20"
        at = datetime.now(corpus.HKT).isoformat(timespec="seconds")
        corpus.ingest([{"sid": "now_telegram", "source": "NOW 新聞", "title": "曼城財務違規或被大量扣分提早確定降班",
                        "url": url, "published": at, "seen": at, "text": "曼城財務違規或被大量扣分提早確定降班"}])
        pid = corpus.ids_for_urls([url])[url]
        corpus.db().execute("UPDATE posts SET source_type='Social Media' WHERE id=%s", (pid,))
        due = lambda: {p["id"] for p in streams.due(100, {"from_id": 0, "comments": False})}
        self.assertNotIn(pid, due())
        corpus.set_text(pid, "【Now新聞台】英超曼城違反財務規定罪成。" * 10)
        self.assertIn(pid, due())

    def test_growing_pages_are_read_again(self):
        grow = add("https://www.881903.com/news/finance/2655518", None, "cr881903")
        rthk = add("https://news.rthk.hk/rthk/ch/component/k2/1874609-20261020.htm", None, "rthk_local")
        full = add("https://www.881903.com/news/local/2655523", None, "cr881903")
        recent = add("https://www.881903.com/news/finance/2655558", None, "cr881903")
        other = add("https://news.tvb.com/tc/1201762-港股收市報", None, "tvb")
        tried(grow, "港股低開196點，恒生指數報23990點", 3)
        tried(rthk, "藍佛安：推動APEC中國年財經合作機制取得務實成果 - RTHK", 3)
        tried(full, "長" * 800, 3)
        tried(recent, "港股半日跌逾200點", 0.2)
        tried(other, "港股收市報23,867點，跌319點。", 3)
        self.assertEqual(fulltext_fetch.reread(), 2)
        ids = [c[0] for c in fulltext_fetch.candidates(100)]
        self.assertEqual(sorted(i for i in ids), sorted([grow, rthk]))
        for _ in range(fulltext_fetch.REREADS - 1):  # each read again leaves it short: read again, REREADS times in all
            tried(grow, "港股低開196點，恒生指數報23990點", 3)
            self.assertEqual(fulltext_fetch.reread(), 1)
        tried(grow, "港股低開196點，恒生指數報23990點", 3)
        self.assertEqual(fulltext_fetch.reread(), 0)  # then it is the story (881903's traffic notes stay one line)

    def test_a_reread_never_shortens_the_text(self):
        pid = add("https://www.881903.com/news/local/2655513", None, "cr881903")
        tried(pid, "亞太經合組織APEC財長會議一連兩日在本港舉行，" * 5, 3)
        fulltext_fetch.save(pid, "")  # the page answered empty this time
        self.assertTrue(corpus.db().execute("SELECT text FROM posts WHERE id=%s", (pid,)).fetchone()["text"])


if __name__ == "__main__":
    unittest.main()
