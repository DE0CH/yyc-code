#!/usr/bin/env python3
"""corpus.ingest on Neon: a poll or a job re-reads thousands of known posts. Ingest ran the whole batch in ONE
transaction, one round trip per known post, so a refreshed post's row stayed locked until the batch's end: on Neon
(2026-10-16) an ingest transaction stayed open 4 minutes, and set_transcript / set_text / another ingest waited on
its row locks for 2+ minutes (the transcriber, the page reads and the jobs behind them stalled). The batch's work
(classification, search columns) happens before any write, the writes are short transactions of their own, and the
result is unchanged. TEST_DATABASE_URL, a throwaway database.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_ingest_short_locks.py
"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402
import corpus  # noqa: E402


def item(n, **kw):
    return dict({"url": f"https://example.com/lock/{n}", "title": f"標題 {n}", "text": f"內容 {n}", "sid": "locktest",
                 "source": "Lock test"}, **kw)


class IngestShortLocks(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM posts WHERE sid IN ('locktest', 'locktest2')")

    def locked(self, url):
        """is the row of `url` locked by another transaction right now? (a separate connection, NOWAIT)"""
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            try:
                c.execute("SELECT id FROM posts WHERE url=%s FOR UPDATE NOWAIT", (url,))
                return False
            except psycopg.errors.LockNotAvailable:
                return True

    def test_a_refreshed_row_is_not_locked_while_the_batch_goes_on(self):
        corpus.ingest([item(0, engagement={"likes": 1})])
        seen = []
        real = corpus.search_cols

        def spy(*a, **kw):  # the work of a later, new post in the same batch
            seen.append(self.locked(item(0)["url"]))
            return real(*a, **kw)
        corpus.search_cols = spy
        try:
            new = corpus.ingest([item(0, engagement={"likes": 5})] + [item(n) for n in range(1, 4)])
        finally:
            corpus.search_cols = real
        self.assertEqual(len(new), 3)
        self.assertTrue(seen)
        self.assertFalse(any(seen), "the refreshed post's row stayed locked while the batch went on")
        eng = corpus.db().execute("SELECT engagement FROM posts WHERE url=%s", (item(0)["url"],)).fetchone()[0]
        self.assertEqual(json.loads(eng), {"likes": 5})

    def test_results_unchanged(self):
        first = corpus.ingest([item(1, engagement={"likes": 1}), item(2, text=None)])
        self.assertEqual(len(first), 2)
        # re-seen: counts merged, a missing text filled, a duplicate within the batch is one post
        again = corpus.ingest([item(1, engagement={"shares": 2}), item(2, text="補上的內容", author="作者"),
                               item(3), item(3, engagement={"likes": 7})])
        self.assertEqual(len(again), 1)
        rows = {r["url"]: r for r in corpus.db().execute(
            "SELECT url, text, author, engagement, hay FROM posts WHERE sid='locktest'")}
        self.assertEqual(json.loads(rows[item(1)["url"]]["engagement"]), {"likes": 1, "shares": 2})
        self.assertEqual(rows[item(2)["url"]]["text"], "補上的內容")
        self.assertIn("作者", rows[item(2)["url"]]["hay"])  # the search columns follow the fill
        self.assertEqual(json.loads(rows[item(3)["url"]]["engagement"]), {"likes": 7})
        self.assertEqual(len(rows), 3)

    def test_first_run_marks_a_new_source(self):
        corpus.ingest([item(10, sid="locktest2")])
        corpus.ingest([item(11, sid="locktest2")])
        fr = dict(corpus.db().execute("SELECT url, first_run FROM posts WHERE sid='locktest2'").fetchall())
        self.assertTrue(fr[item(10)["url"]])
        self.assertFalse(fr[item(11)["url"]])

    def test_a_batch_larger_than_a_chunk(self):
        n = corpus.INGEST_CHUNK * 2 + 5
        self.assertEqual(len(corpus.ingest([item(100 + k) for k in range(n)])), n)
        self.assertEqual(len(corpus.ingest([item(100 + k, engagement={"likes": k}) for k in range(n)])), 0)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts WHERE sid='locktest' AND engagement IS NOT NULL")
                         .fetchone()[0], n)


if __name__ == "__main__":
    unittest.main()
