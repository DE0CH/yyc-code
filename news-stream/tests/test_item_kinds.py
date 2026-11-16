#!/usr/bin/env python3
"""One post's data (item.py: the agent's posts_get tool and the MCP server) answers for every kind of post (Deyao,
2026-10-15: "some details link when i click bring up 500"; the item page itself is the edge app's), on a throwaway
database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_item_kinds.py

1. Every kind of post: news, a social post, a comment, a LIHKG thread and one of its replies, an X post, a post with a
   video (transcribed, failed, queued), a post with pictures (read, queued, failed), a date-only post, an undated post,
   a post with an AI title, posts of one article, a post scored with a market answer. For each, item.view and
   item.text answer, and the agent's posts_get tool through the AI server (/app/api/v1/posts_get) answers 200.
2. The database restarted under the pods (a Postgres restart or failover, 15 Oct 2026 01:54 UTC): the pool's
   connections from before it are dead, and each one used once answered 500 (AdminShutdown) until it was dropped.
   Every request after the restart answers 200.
"""
import io, os, sys, unittest, urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, item, web  # noqa: E402

T = "2026-10-14T10:00+08:00"


def call(path, approved=False):
    p, _, q = path.partition("?")
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": urllib.parse.unquote(p), "QUERY_STRING": q, "RAW_URI": path,
           "wsgi.input": io.BytesIO(b"")}
    got = {}
    old = web.ADMIN_DEV
    web.ADMIN_DEV = approved
    try:
        data = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]), headers=dict(h))))
    finally:
        web.ADMIN_DEV = old
    return got["status"], got["headers"], data


def answers(p):
    """item.view and item.text of post p, and posts_get through the AI server as an approved account → [problems]"""
    bad = []
    for fn in (item.view, item.text):
        try:
            fn(p)
        except Exception as e:  # noqa: BLE001
            bad.append((fn.__name__, repr(e)[:200]))
    s, _, body = call(f"/app/api/v1/posts_get?id={p}", approved=True)
    if s != 200:
        bad.append(("posts_get", s, body[:200]))
    return bad


def pid(url):
    return corpus.ids_for_urls([url])[url]


class Kinds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        K = cls.kinds = {}
        corpus.ingest([{"url": "https://news.mingpao.com/n1", "title": "特首出席活動", "text": "全文" * 200,
                        "sid": "mingpao_x", "source": "明報", "section": "港聞", "published": T}])
        K["news"] = "https://news.mingpao.com/n1"
        corpus.ingest([{"url": "https://www.instagram.com/p/abc/", "title": "IG 帖子", "text": "帖子內容", "sid": "instagram",
                        "source": "johnlee (Instagram)", "published": T, "engagement": {"likes": 5}}])
        K["social"] = "https://www.instagram.com/p/abc/"
        corpus.add_comments({"url": K["social"], "title": "IG 帖子", "sid": "instagram", "source": "johnlee (Instagram)"},
                            [{"id": "c1", "author": "a", "text": "留言", "published": T, "likes": 1}], total=3)
        K["comment"] = K["social"] + "#cc1"
        lihkg = "https://lihkg.com/thread/3900000/page/1"
        corpus.add_comments({"url": lihkg, "title": "李家超 帖", "text": "開帖內容", "sid": "lihkg_current",
                             "source": "LIHKG討論區", "published": T},
                            [{"id": "2", "author": "會員", "text": "回覆", "published": T, "likes": 3, "dislikes": 1}])
        K["lihkg_thread"], K["lihkg_reply"] = lihkg, lihkg + "#c2"
        corpus.ingest([{"url": "https://x.com/johnlee/status/1", "title": "X post", "text": "推文", "sid": "watch_x",
                        "source": "johnlee (X)", "published": T}])
        K["x"] = "https://x.com/johnlee/status/1"
        corpus.ingest([{"url": "https://www.youtube.com/watch?v=v1", "title": "影片", "text": "影片描述", "sid": "yt_rthk",
                        "source": "RTHK (YouTube)", "published": T}])
        K["video"] = "https://www.youtube.com/watch?v=v1"
        corpus.ingest([{"url": "https://www.rthk.hk/pics", "title": "有圖新聞", "text": "內文", "sid": "rthk",
                        "source": "RTHK 香港電台", "published": T}])
        K["pictures"] = "https://www.rthk.hk/pics"
        corpus.ingest([{"url": "https://www.tkww.hk/epaper/d1", "title": "日報", "text": "內文", "sid": "tkp_epaper",
                        "source": "大公報", "published": corpus.DateOnly(2026, 10, 14, tzinfo=corpus.HKT)}])
        K["date_only"] = "https://www.tkww.hk/epaper/d1"
        corpus.ingest([{"url": "https://www.am730.com.hk/col/u1", "title": "無日期專欄", "text": "內文", "sid": "col_am730",
                        "source": "am730", "published": None}])
        K["undated"] = "https://www.am730.com.hk/col/u1"
        corpus.ingest([{"url": "https://t.me/s/chan/9", "title": "https://news.mingpao.com/n1", "text": "https://news.mingpao.com/n1", "sid": "tg_chan",
                        "source": "chan (Telegram)", "published": T}])
        K["link_only"] = "https://t.me/s/chan/9"
        corpus.ingest([{"url": "https://hk.on.cc/n1", "title": "特首出席活動", "text": "全文" * 200, "sid": "oncc",
                        "source": "東方日報", "published": T}])
        K["article"] = "https://hk.on.cc/n1"
        ids = {k: pid(u) for k, u in K.items()}
        cls.ids = ids
        db = corpus.db()
        db.execute("UPDATE posts SET ai_title='AI 標題', ai_title_kind='title', ai_title_model='anthropic/claude-haiku-5.5', "
                   "ai_title_at=%s WHERE id=%s", (T, ids["link_only"]))
        db.execute("INSERT INTO media (key, url, kind, host, status, duration, transcript, model, first_seen, done_at) VALUES "
                   "('yt:v1', %s, 'video', 'youtube', 'done', 61.4, '逐字稿', 'youtube-captions:zh-HK', %s, %s),"
                   "('yt:v2', 'https://y/2', 'video', 'youtube', 'failed', NULL, NULL, NULL, %s, NULL),"
                   "('yt:v3', 'https://y/3', 'audio', 'rthk', 'queued', NULL, NULL, NULL, %s, NULL)",
                   (K["video"], T, T, T, T))
        db.execute("UPDATE media SET error='no audio' WHERE key='yt:v2'")
        db.executemany("INSERT INTO post_media (post_id, key) VALUES (%s, %s)",
                       [(ids["video"], "yt:v1"), (ids["video"], "yt:v2"), (ids["video"], "yt:v3"), (ids["x"], "yt:v1")])
        db.execute("INSERT INTO images (hash, url, width, height, score, reason, text, model, at) VALUES "
                   "('h1', 'https://i/1.jpg', 800, 600, 80, '相關', '圖中文字', 'anthropic/claude-haiku-5.5', %s),"
                   "('h0', 'https://i/0.png', 16, 16, NULL, NULL, NULL, NULL, %s)", (T, T))
        db.execute("INSERT INTO post_images (post_id, url, pos, hash, tries) VALUES (%s, 'https://i/1.jpg', 0, 'h1', 1),"
                   "(%s, 'https://i/0.png', 1, 'h0', 1), (%s, 'https://i/q.jpg', 2, NULL, 0), (%s, 'https://i/f.jpg', 3, NULL, 9)",
                   (ids["pictures"],) * 4)
        db.execute("INSERT INTO scored (post_id, hash, sentiment, labels, at) VALUES (%s, 'x', 'neutral', '[\"monitor\"]', %s)",
                   (ids["news"], T))
        db.executemany("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,'r',NULL,%s)",
                       [("CEHK", ids["news"], 90, T), ("GALBOT", ids["news"], 70, T)])
        db.execute("INSERT INTO markets (stream, post_id, market, how, model, at) VALUES ('GALBOT', %s, 'hk', 'model', 'm', %s)",
                   (ids["news"], T))
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-14', 'CEHK', %s, %s)",
                   (K["news"], corpus.J({"url": K["news"], "stream": "CEHK", "post_id": ids["news"], "score": 90})))
        corpus.set_tags("CEHK", [(ids["news"], True, "neutral", ["monitor"])])

    def test_every_kind_answers(self):
        bad = []
        for kind in self.kinds:
            bad += [(kind, *b) for b in answers(self.ids[kind])]
        self.assertEqual(bad, [])

    def test_kinds_are_what_they_say(self):
        def j(k):
            return item.view(self.ids[k])
        self.assertEqual(j("social")["source_type"], "Social Media")
        self.assertTrue(j("comment")["comment"])
        self.assertEqual(j("lihkg_reply")["thread"]["comments"][0]["likes"], 3)
        self.assertTrue(j("date_only")["date_only"])
        self.assertIsNone(j("undated")["published"])
        self.assertEqual([m["status"] for m in j("video")["media"]], ["done", "failed", "queued"])
        self.assertEqual([p["status"] for p in j("pictures")["pictures"]["items"]], ["scored", "queued", "failed"])
        self.assertTrue(j("link_only")["title_by_ai"])
        self.assertTrue(j("article")["article"])


class DatabaseRestart(unittest.TestCase):
    def test_after_a_restart_every_request_answers(self):
        corpus.ingest([{"url": "https://www.rthk.hk/r1", "title": "重啟", "text": "內文", "sid": "rthk",
                        "source": "RTHK 香港電台", "published": T}])
        p = pid("https://www.rthk.hk/r1")
        with corpus.db():  # hold several pooled connections at once, so the pool keeps more than one
            pass
        for _ in range(3):
            self.assertEqual(answers(p), [])
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:  # what a Postgres restart does to them
            n = c.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname=current_database() "
                          "AND application_name='yyc-news-stream'").fetchone()[0]
        self.assertGreater(n, 0)
        for _ in range(4):
            self.assertEqual(answers(p), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
