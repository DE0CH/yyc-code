#!/usr/bin/env python3
"""What the item page needs to say what is pending without a spinner (Deyao, 2026-10-16: "a spinner should be used
for actions that can be completed within seconds … not for data that doesn't exist yet"; NAVIGATION.md "Spinners"),
and the AI server's heartbeat that lets a page stop its spinner when a stream dies. On a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_pending_states.py

1. item.view's text_due: True only for a post the full-text job (fulltext_fetch.py) will read: a news post with no
   text or a feed summary, never tried. A Threads post whose whole text is its title (shinninglane's 「屈穎妍文章令我
   非常仇恨香港特區政府！」, 2026-10-16: the page showed 「處理中：系統正在收集全文」 with a spinner for a post no job
   reads) is never due, nor a comment, a tried page, a long feed text or a TikHub post.
2. comments_read: the posts whose comments a fetcher reads (twopass.UNIT_URLS); a news article's are not.
3. A queued clip's place in the transcription queue (media.due's order: newest post first), None while it waits to
   retry; scoring.second_due while a unit post's second scoring is still to come.
4. web.Stream: a {"t":"ping"} line at once and while the events are silent, the events in order, an error midway as
   {"t":"error"}, and the events' generator closed when the reader goes away.
"""
import json, os, sys, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import flags_on  # noqa: E402,F401  picture analysis and transcription on (off since 2026-10-20: tests/test_pictures_transcription_off.py)
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, insights, item, web  # noqa: E402

THREADS = "https://www.threads.com/@fixture_spin/post/DeiqJxFkcMP"


def add(url, title, text=None, sid="rthk_en", source_type="News", **extra):
    corpus.ingest([dict({"url": url, "title": title, "text": text, "sid": sid, "source": "fixture",
                         "published": insights.now().isoformat(timespec="minutes")}, **extra)])
    pid = corpus.ids_for_urls([url])[url]
    corpus.db().execute("UPDATE posts SET source_type=%s WHERE id=%s", (source_type, pid))
    return pid


class ItemPending(unittest.TestCase):
    def test_text_due(self):
        threads = add(THREADS, "屈穎妍文章令我非常仇恨香港特區政府！", sid="tikhub_threads", source_type="Social Media")
        news = add("https://news.fixture.example/a", "A news headline")
        summary = add("https://news.fixture.example/b", "Feed summary only", text="A short RSS summary.")
        full = add("https://news.fixture.example/c", "Full feed text", text="字" * 600)
        tried = add("https://news.fixture.example/d", "Tried, nothing came back")
        corpus.db().execute("UPDATE posts SET cached_at=%s WHERE id=%s", (insights.now().isoformat(), tried))
        tik = add("https://www.tiktok.com/@a/video/1", "A TikHub news-typed post", sid="tikhub_tiktok")
        fb = add("https://www.facebook.com/fixture/posts/1", "A Facebook page typed News")
        v = {p: item.view(p) for p in (threads, news, summary, full, tried, tik, fb)}
        self.assertFalse(v[threads]["text_due"], "a Threads post's text comes from its collector: never read")
        self.assertTrue(v[news]["text_due"])
        self.assertTrue(v[summary]["text_due"], "a feed summary under FULL_CHARS gets the page read")
        self.assertFalse(v[full]["text_due"])
        self.assertFalse(v[tried]["text_due"], "a page tried once is not read again")
        self.assertFalse(v[tik]["text_due"])
        self.assertFalse(v[fb]["text_due"], "social hosts are never read")
        self.assertEqual(v[threads]["text_chars"], len("屈穎妍文章令我非常仇恨香港特區政府！"))  # no title of its own: the text

    def test_comments_read(self):
        t = add(THREADS + "x", "A Threads post", sid="tikhub_threads", source_type="Social Media")
        n = add("https://news.fixture.example/e", "A news article")
        self.assertTrue(item.view(t)["comments_read"])
        self.assertFalse(item.view(n)["comments_read"])

    def test_queue_and_second_scoring(self):
        p = add("https://www.youtube.com/watch?v=AAAAAAAAAAA", "A video", source_type="Video")
        db = corpus.db()
        rows = (("fx:old", "2026-10-16T10:00:00+08:00", None), ("fx:new", "2026-10-16T12:00:00+08:00", None),
                ("fx:retry", "2026-10-16T13:00:00+08:00", "2099-01-01T00:00:00+08:00"))
        for key, newest, next_try in rows:
            db.execute("INSERT INTO media (key, url, kind, host, status, first_seen, newest, next_try) "
                       "VALUES (%s,%s,'video','www.youtube.com','queued',%s,%s,%s)", (key, "https://v/" + key, newest, newest, next_try))
            db.execute("INSERT INTO post_media (post_id, key) VALUES (%s,%s)", (p, key))
        q = {m["key"]: m["queue"] for m in item.view(p)["media"]}
        self.assertEqual(q, {"fx:old": 2, "fx:new": 1, "fx:retry": None})
        url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
        db.execute(f"INSERT INTO scored (post_id, hash, sentiment, labels, at) SELECT id, 'h', 'neutral', '[]', %s FROM posts WHERE id=%s",
                   (insights.now().isoformat(), p))
        db.execute("INSERT INTO two_pass (url, first_at, due_at) VALUES (%s, now(), %s)", (url, "2026-10-16T04:49:00+00"))
        self.assertEqual(item.view(p)["scoring"]["second_due"], "2026-10-16T12:49+08:00")
        db.execute("UPDATE two_pass SET second_at = now() WHERE url=%s", (url,))
        self.assertIsNone(item.view(p)["scoring"]["second_due"])


class Heartbeat(unittest.TestCase):
    def lines(self, events, n=None):
        out = []
        for b in web.Stream(events).lines():
            out.append(json.loads(b))
            if n and len(out) >= n:
                break
        return out

    def test_ping_first_and_while_silent(self):
        old = web.Stream.HEARTBEAT
        web.Stream.HEARTBEAT = 0.2
        try:
            def ev():
                yield {"t": "session", "session": "s"}
                time.sleep(0.7)
                yield {"t": "done", "answer": "ok"}
            got = self.lines(ev())
        finally:
            web.Stream.HEARTBEAT = old
        self.assertEqual(got[0], {"t": "ping"})
        self.assertEqual(got[1], {"t": "session", "session": "s"})
        self.assertGreaterEqual(sum(1 for x in got[2:-1] if x == {"t": "ping"}), 2, got)
        self.assertEqual(got[-1], {"t": "done", "answer": "ok"})

    def test_error_midway(self):
        def ev():
            yield {"t": "delta", "text": "a"}
            raise RuntimeError("model failed")
        got = [x for x in self.lines(ev()) if x["t"] != "ping"]
        self.assertEqual(got, [{"t": "delta", "text": "a"}, {"t": "error", "error": "model failed"}])

    def test_reader_gone_closes_events(self):
        closed = threading.Event()

        def ev():
            try:
                for i in range(1000):
                    yield {"t": "delta", "text": str(i)}
                    time.sleep(0.01)
            finally:
                closed.set()
        gen = web.Stream(ev()).lines()
        next(gen), next(gen)
        gen.close()
        self.assertTrue(closed.wait(5), "the events' generator keeps running after the reader went away")


if __name__ == "__main__":
    unittest.main(verbosity=2)
