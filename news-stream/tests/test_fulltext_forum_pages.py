#!/usr/bin/env python3
"""Forum-type posts that are not threads are read for their text like news (2026-10-20: hk.ulifestyle.com.hk topic and
activity pages, getjetso deal pages, asiaxpat jobs and articles stayed title-only: the full-text job read only news,
official and blog posts). A forum THREAD is not: its collector (LIHKG's API, 香港討論區's archiver, forum_fetch.py for
the catalogue forums: twopass.UNIT_URLS) gives its opening post's text.

    TEST_DATABASE_URL=postgresql:///yyc_t_forumpages python3 tests/test_fulltext_forum_pages.py
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, edge_config, fulltext_fetch, fulltext_now, insights, twopass  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)
from edge_call import edge  # noqa: E402

PAGES = ["https://hk.ulifestyle.com.hk/topic/detail/20048113/fixture", "https://www.getjetso.com/forum/thread-fixture-1",
         "https://hongkong.asiaxpat.com/jobs/fixture-1/"]
THREAD = "https://lihkg.com/thread/3999999/page/1"


class ForumPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        now = insights.now().isoformat(timespec="minutes")
        corpus.ingest([{"url": u, "title": f"一個論壇類網頁 {i}", "sid": "hk_fixture_forum", "source": "fixture", "published": now}
                       for i, u in enumerate(PAGES + [THREAD])])
        corpus.db().execute("UPDATE posts SET source_type='Forum', seen=%s", (now,))
        cls.ids = corpus.ids_for_urls(PAGES + [THREAD])
        edge_config.publish()

    def row(self, u):
        return dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (u,)).fetchone())

    def test_pages_are_read_threads_are_not(self):
        self.assertTrue(any(__import__("re").search(x, THREAD) for x in twopass.UNIT_URLS))
        got = {u for _, u, _, _ in fulltext_fetch.candidates(100)}
        for u in PAGES:
            self.assertIn(u, got)
            self.assertTrue(fulltext_fetch.will_read(self.row(u)), u)
            self.assertTrue(fulltext_now.wanted(self.row(u)), u)
            self.assertTrue(edge("item.view", self.ids[u])["text_due"], u)
        self.assertNotIn(THREAD, got)
        self.assertFalse(fulltext_fetch.will_read(self.row(THREAD)))
        self.assertFalse(fulltext_now.wanted(self.row(THREAD)))
        self.assertFalse(edge("item.view", self.ids[THREAD])["text_due"])

    def test_scorer_waits_for_the_page(self):
        """like a news post, a forum page is scored once its page was read (streams.due), never on its title alone"""
        import streams
        corpus.kv_set("scoring", {"from_id": 0, "comments": False})
        due = {p["url"] for p in streams.due(100)}
        self.assertFalse(set(PAGES) & due)
        corpus.db().execute("UPDATE posts SET cached_at=%s WHERE url=%s", (insights.now().isoformat(), PAGES[0]))
        self.assertIn(PAGES[0], {p["url"] for p in streams.due(100)})


if __name__ == "__main__":
    unittest.main()
