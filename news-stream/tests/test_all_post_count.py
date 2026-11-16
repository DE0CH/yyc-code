#!/usr/bin/env python3
"""A hot topic's all_post_count (insights._all_post_count): every post in the corpus whose search text holds the
topic title's first 12 characters. On 23 Oct 2026 its plain strpos over posts.hay read the whole table (8.7 GB),
20-40 s a topic, and the daily reports CronJob ran past its hour every hour from 09:05 HKT. It now looks the
title up in its search form (Simplified, as hay is) through the grams index. TEST_DATABASE_URL, a throwaway database.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_all_post_count.py

1. A Traditional title finds the posts carrying it (hay is Simplified), not those without it.
2. The query uses the grams index (EXPLAIN shows posts_grams), not a scan of posts.
3. A title too short to look up counts the topic's own posts.
4. The edge app's copy (web-app dashboard.allPostCounts: the site's 熱門話題 and yyc-mcp's analysis_hot_topics) gives
   the same counts. On 13 Nov 2026 it read all of posts once per call (strpos for every topic's title, no index) and
   analysis_hot_topics ended in Postgres's 30 s statement timeout; it also looked a Traditional title up as typed.
5. The edge app's copy does not scan posts either.
6. An English title is looked up by its whole words (the words index), in Python and in the edge app: its character
   pairs are in nearly every English post, so the grams lookup of "speech by ce a" alone ran past 30 s (13 Nov 2026).
   The count may differ by a post or two from the plain substring count (Deyao, 2026-11-13).
"""
import os, sys, unittest

import psycopg

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import corpus, insights, query, searchtext  # noqa: E402
from tests.edge_call import edge  # noqa: E402

TITLE = "警隊為朱振國舉行最高榮譽喪禮 遺體將安葬浩園"
ENGLISH = "Speech by CE at the Youth Development Summit"


class AllPostCount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = corpus.db()
        for t in ("posts",):
            db.execute(f"DELETE FROM {t}")
        corpus.ingest([{"url": f"https://a.example/{k}", "title": TITLE if k < 3 else f"其他新聞 {k} 號標題內容", "text": "內文",
                        "sid": "t", "source": "測試", "published": "2026-10-22T10:00:00+08:00"} for k in range(6)])
        corpus.ingest([{"url": f"https://e.example/{k}", "title": ENGLISH if k < 2 else "A speech at the summit by the CE", "text": "text",
                        "sid": "t", "source": "Test", "published": "2026-10-22T10:00:00+08:00"} for k in range(4)])

    def test_1_traditional_title_finds_its_posts(self):
        self.assertEqual(insights._all_post_count({"title": TITLE, "posts": [1]}), 3)
        self.assertEqual(insights._all_post_count({"title": "完全無關的標題內容不存在", "posts": [1, 2]}), 2)

    def test_2_uses_the_grams_index(self):
        term = query.searchtext.norm(TITLE)[:12]
        with psycopg.connect(os.environ["DATABASE_URL"]) as c:  # its own connection: the setting stays on it
            c.execute("SET enable_seqscan = off")  # a test table is tiny: no scan, so the plan shows the indexes
            plan = "\n".join(r[0] for r in c.execute(
                "EXPLAIN SELECT count(*) FROM posts WHERE (grams @@ %s::tsquery OR grams IS NULL) AND strpos(hay, %s) > 0",
                (" & ".join(query._tsq(g) for g in query._pairs(term)), term)).fetchall())
        self.assertIn("posts_grams", plan)
        self.assertNotIn("Seq Scan", plan)

    def test_3_short_title(self):
        self.assertEqual(insights._all_post_count({"title": "短", "posts": [1, 2, 3, 4]}), 4)

    TOPICS = [{"title": TITLE, "posts": [1]}, {"title": "完全無關的標題內容不存在", "posts": [1, 2]}, {"title": "短", "posts": [1, 2, 3, 4]}]

    def test_4_edge_same_counts(self):
        searchtext.publish_edge()  # the dictionary and OpenCC data the edge app norms a title with
        self.assertEqual(edge("dashboard.allPostCounts", self.TOPICS), [3, 2, 4])

    def test_5_edge_does_not_scan_posts(self):
        searchtext.publish_edge()
        name = os.environ["DATABASE_URL"].rsplit("/", 1)[1]
        scans = "SELECT coalesce(seq_scan, 0) FROM pg_stat_user_tables WHERE relname = 'posts'"
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            # a test table is tiny: with scans off for the app's (new) connections the plan must take the index
            c.execute(f'ALTER DATABASE "{name}" SET enable_seqscan = off')
            try:
                before = c.execute(scans).fetchone()[0]
                edge("dashboard.allPostCounts", self.TOPICS)
                c.execute("SELECT pg_stat_clear_snapshot()")
                self.assertEqual(c.execute(scans).fetchone()[0], before, "the edge app read all of posts")
            finally:
                c.execute(f'ALTER DATABASE "{name}" RESET enable_seqscan')

    def test_6_english_title_by_whole_words(self):
        self.assertEqual(insights._all_post_lookup(query.searchtext.norm(ENGLISH))[1][0], "'speech' & 'by' & 'ce'")
        self.assertEqual(insights._all_post_lookup("speech by center for x")[1][0], "'speech' & 'by'")  # "ce" is cut: not a word
        self.assertIn("words @@", insights._all_post_lookup(query.searchtext.norm(ENGLISH))[0])
        self.assertIn("grams @@", insights._all_post_lookup(query.searchtext.norm(TITLE))[0])  # Chinese: the pairs
        self.assertIn("grams @@", insights._all_post_lookup("international relations")[0])  # no whole word: the pairs
        self.assertEqual(insights._all_post_count({"title": ENGLISH, "posts": [1]}), 2)
        searchtext.publish_edge()
        name = os.environ["DATABASE_URL"].rsplit("/", 1)[1]
        used = "SELECT coalesce(idx_scan, 0) FROM pg_stat_user_indexes WHERE indexrelname = 'posts_words'"
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute(f'ALTER DATABASE "{name}" SET enable_seqscan = off')
            try:
                before = c.execute(used).fetchone()[0]
                self.assertEqual(edge("dashboard.allPostCounts", [{"title": ENGLISH, "posts": [1]}]), [2])
                c.execute("SELECT pg_stat_clear_snapshot()")
                self.assertGreater(c.execute(used).fetchone()[0], before, "the edge app did not use the words index")
            finally:
                c.execute(f'ALTER DATABASE "{name}" RESET enable_seqscan')


if __name__ == "__main__":
    unittest.main()
