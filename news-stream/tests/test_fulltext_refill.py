#!/usr/bin/env python3
"""Refilling the posts whose page read kept no article text (Deyao, 2026-10-20: "so many sources don't capture the
full text"), and the save rules that keep it from happening again.

1. save(): a page text that is only the post's title again (titles.title_only) is no text: "" is kept, never the
   title; an earlier text the feed gave stays when longer, but an earlier PAGE read's text (via page / browser: junk a
   broken extractor kept, e.g. 「Copyright 2026 TVB Limited」, 紫荊's JSON URL) is replaced by the new read.
2. refill(since): every news post seen since then whose text is missing, only the title again, or shorter than
   FULL_CHARS (plus every post of the named sources, for junk longer than that) is read again by the job's own run
   (run(todo)); posts on hosts never read are left out. Its scored hash changes with the text, so streams.due scores
   it again the normal way.

    TEST_DATABASE_URL=postgresql:///yyc_t_refill python3 tests/test_fulltext_refill.py
"""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, corpus, fulltext_fetch  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

T = "2026-10-20T16:20+08:00"
BODY = "調查宏福苑火災的獨立委員會，預計本周內向特首李家超提交報告，據了解，報告會稍後才公開。" * 8
HEAD = "宏福苑聽證｜獨委會將交報告"


def text(url):
    return corpus.db().execute("SELECT text, cached_at, text_via FROM posts WHERE url=%s", (url,)).fetchone()


class Refill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        items = [
            {"url": "https://thecollectivehk.com/a/", "title": HEAD, "text": "", "sid": "gnews_15", "source": "集誌社", "published": T},
            {"url": "https://news.tvb.com/tc/1/", "title": "港股中午收市", "text": "", "sid": "tvb", "source": "TVB", "published": T},
            {"url": "https://skypost.hk/article/1/", "title": "早餐", "text": "", "sid": "skypost", "source": "晴報", "published": T},
            {"url": "https://news.rthk.hk/ok/", "title": "新聞", "text": BODY * 3, "sid": "rthk_local", "source": "RTHK", "published": T},
            {"url": "https://www.facebook.com/p/1", "title": "帖子", "text": "", "sid": "gnews_15", "source": "x", "published": T},
            {"url": "https://old.example.com/a/", "title": "舊", "text": "", "sid": "gnews_15", "source": "x",
             "published": "2026-10-19T08:00+08:00"},
        ]
        corpus.ingest(items)
        c = corpus.db()
        c.execute("UPDATE posts SET seen=%s", ("2026-10-20T16:27:20+08:00",))
        c.execute("UPDATE posts SET seen=%s WHERE url LIKE 'https://old.%%'", ("2026-10-19T08:00:00+08:00",))
        pid = lambda u: corpus.ids_for_urls([u])[u]
        corpus.set_text(pid("https://thecollectivehk.com/a/"), "", via=None)  # the 202 stub: read, nothing kept
        corpus.set_text(pid("https://news.tvb.com/tc/1/"), "Copyright 2026 TVB Limited", via="page")
        corpus.set_text(pid("https://skypost.hk/article/1/"), "早餐飲品" + "{{hk.x}}" * 120, via="page")  # junk over 500
        corpus.set_text(pid("https://old.example.com/a/"), "", via=None)
        corpus.set_text(pid("https://news.rthk.hk/ok/"), BODY * 3, via="page")  # full: not read again
        cls.pid = staticmethod(pid)

    def test_1_save_title_only_is_no_text(self):
        u = "https://thecollectivehk.com/a/"
        fulltext_fetch.save(self.pid(u), HEAD + " - 集誌社")
        self.assertEqual(text(u)["text"], "")

    def test_2_refill(self):
        got = []

        def fetch_one(url, meta=None):
            got.append(url)
            return BODY, None, {}
        with mock.patch.object(cache_fetch, "fetch_one", side_effect=fetch_one), \
                mock.patch.object(fulltext_fetch.media, "seen_page"), mock.patch.object(fulltext_fetch.canonical, "save"):
            n = fulltext_fetch.refill("2026-10-20T09:15:00+08:00", sids=["skypost"])
        self.assertEqual(sorted(got), ["https://news.tvb.com/tc/1/", "https://skypost.hk/article/1/",
                                       "https://thecollectivehk.com/a/"])
        self.assertEqual(n, 3)
        for u in got:
            self.assertEqual(text(u)["text"], BODY.strip(), u)  # the page read replaces an earlier page read's junk
        self.assertEqual(text("https://old.example.com/a/")["text"], "")  # seen before the cutoff: left

    def test_2b_only(self):
        """--only: the named sources' posts alone"""
        corpus.db().execute("UPDATE posts SET cached_at=now()::text, text=''")
        with mock.patch.object(fulltext_fetch, "run") as run:
            fulltext_fetch.refill("2026-10-20T09:15:00+08:00", sids=["tvb"], only=True)
        self.assertEqual([u for _, u, _, _ in run.call_args[0][0]], ["https://news.tvb.com/tc/1/"])

    def test_2c_a_queue_drained_under_the_deadline(self):
        """refill is a queue (kv fulltext:refill), never a Job past its deadline (2026-10-20: refill Jobs with
        FULLTEXT_DEADLINE raised died at the 29-minute activeDeadlineSeconds): what one run doesn't reach stays queued,
        and each fulltext CronJob run reads the next chunk beside its candidates, under its own deadline"""
        # template junk longer than FULL_CHARS: not a candidate, only the queue brings these back
        corpus.db().execute("UPDATE posts SET cached_at=now()::text, text=repeat('{{hk.x}}', 120)")
        corpus.kv_set(fulltext_fetch.REFILL_KV, [])
        got = []

        def fetch_one(url, meta=None):
            got.append(url)
            return BODY, None, {}
        patches = (mock.patch.object(cache_fetch, "fetch_one", side_effect=fetch_one),
                   mock.patch.object(fulltext_fetch.media, "seen_page"), mock.patch.object(fulltext_fetch.canonical, "save"))
        for p in patches:
            p.start()
        try:
            with mock.patch.object(fulltext_fetch, "DEADLINE", -1):  # this run reaches nothing
                fulltext_fetch.refill("2026-10-20T09:15:00+08:00", sids=["skypost", "tvb"], only=True)
            self.assertEqual(got, [])
            self.assertEqual(len(corpus.kv_get(fulltext_fetch.REFILL_KV)), 2)
            with mock.patch.object(fulltext_fetch, "REFILL_CHUNK", 1):
                fulltext_fetch.run()  # the CronJob's run: one queued post
            self.assertEqual(len(got), 1)
            self.assertEqual(len(corpus.kv_get(fulltext_fetch.REFILL_KV)), 1)
            fulltext_fetch.run()
            self.assertEqual(sorted(got), ["https://news.tvb.com/tc/1/", "https://skypost.hk/article/1/"])
            self.assertEqual(corpus.kv_get(fulltext_fetch.REFILL_KV), [])
        finally:
            for p in patches:
                p.stop()

    def test_3_feed_text_kept_when_longer(self):
        corpus.ingest([{"url": "https://feed.example.com/a/", "title": "標題", "text": BODY, "sid": "rthk_local",
                        "source": "RTHK", "published": T}])
        fulltext_fetch.save(self.pid("https://feed.example.com/a/"), "短文。" * 10)
        self.assertEqual(text("https://feed.example.com/a/")["text"], BODY.strip())


if __name__ == "__main__":
    unittest.main()
