#!/usr/bin/env python3
"""Chinese-first search (searchtext.py, query.py; Deyao, 2026-10-14), on a throwaway database (TEST_DATABASE_URL; its
tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_chinese_search.py

Traditional finds Simplified and the reverse (選舉/选举, 後/后, 裡/裏/里), a query of several words, a name the
dictionary lacks (found by the substring fallback), Cantonese (嘅/咗/佢哋), English (case, full-width, phrases), word
matches ranked before substring-only ones (corpus.match_ids), display text unchanged, rows indexed
before the change (legacy hay) still found, and corpus.reindex() bringing them to the current version.
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, query, searchtext  # noqa: E402

POSTS = [  # key, title, text, published
    ("trad_vote", "選舉委員會界別分組選舉結果公布", "選舉事務處今日公布結果。", "2026-10-01T09:00+08:00"),
    ("simp_vote", "选举委员会选举投票率创新高", "选举管理委员会表示满意。", "2026-10-02T09:00+08:00"),
    ("trad_after", "會議後來延期", "政府其後宣布新安排", "2026-10-03T09:00+08:00"),
    ("simp_after", "会议后来取消", "", "2026-10-04T09:00+08:00"),
    ("trad_inside1", "大樓裡面發現漏水", "", "2026-10-05T09:00+08:00"),
    ("trad_inside2", "商場裏面人山人海", "", "2026-10-05T10:00+08:00"),
    ("simp_inside", "车厢里面很拥挤", "", "2026-10-05T11:00+08:00"),
    ("ce", "行政長官李家超今日出席立法會答問會", "李家超回應議員提問。", "2026-10-06T09:00+08:00"),
    ("ce_split", "李家超今日", "其後出席立法會。", "2026-10-06T10:00+08:00"),
    ("name_word", "何君堯今日表示", "", "2026-09-01T09:00+08:00"),       # older, but a word match
    ("name_sub", "記者問如何君堯回應", "", "2026-10-07T09:00+08:00"),     # newer, substring only (如何|君|堯)
    ("canto", "連登熱話", "佢哋已經做咗好多嘢，呢個係政府嘅責任。", "2026-10-08T09:00+08:00"),
    ("eng", "HSBC raises Hong Kong rates", "The bank said elections in Hong Kong were calm.", "2026-10-09T09:00+08:00"),
    ("eng_fw", "ＨＳＢＣ年報", "", "2026-10-09T10:00+08:00"),
    ("eng_hyphen", "Hong-Kong based fund", "", "2026-10-09T11:00+08:00"),
]


def keys(q):
    """the POSTS keys parsed query q matches, in match_ids order"""
    ids = corpus.match_ids(query.parse(q), "2000-01-01", "2100-01-01")
    by = {r["id"]: r["url"].rsplit("/", 1)[1] for r in corpus.db().execute("SELECT id, url FROM posts")}
    return [by[i] for i in ids]


class ChineseSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.ingest([{"url": f"https://c/{k}", "title": t, "text": x, "source": "明報", "sid": "mingpao_ins_all",
                        "published": p} for k, t, x, p in POSTS])
        corpus.add_comments({"url": "https://c/canto", "title": "連登熱話", "sid": "lihkg", "source": "LIHKG"},
                            [{"id": "r1", "text": "佢講咗好耐都冇用", "author": "u1", "published": "2026-10-08T10:00+08:00"}])

    def test_traditional_and_simplified_find_each_other(self):
        for q in ("選舉", "选举"):
            got = set(keys(q))
            self.assertTrue({"trad_vote", "simp_vote"} <= got, (q, got))
        for q in ("後來", "后来"):
            self.assertEqual(set(keys(q)), {"trad_after", "simp_after"}, q)
        self.assertTrue({"trad_after", "simp_after"} <= set(keys("後")))
        for q in ("裡面", "裏面", "里面"):  # the HK variants merge with the standard forms
            self.assertEqual(set(keys(q)), {"trad_inside1", "trad_inside2", "simp_inside"}, q)

    def test_several_words(self):
        self.assertEqual(keys('"行政長官李家超"'), ["ce"])                  # one term of two words: a phrase
        self.assertEqual(set(keys("李家超 AND 立法會")), {"ce", "ce_split"})
        self.assertEqual(keys("李家超今日出席立法會"), ["ce"])             # words in order, adjacent
        self.assertEqual(keys("李家超出席立法會"), [])                     # not adjacent in any post
        self.assertEqual(set(keys("选举 AND 委员会 NOT 投票率")), {"trad_vote"})
        self.assertEqual(keys("李家超 NEARBY/5 立法會"), ["ce"])

    def test_name_outside_the_dictionary(self):
        self.assertEqual(searchtext.tokens(searchtext.norm("如何君堯")), ["如何", "君", "尧"])  # 何君堯 is not a word
        got = keys("何君堯")
        self.assertEqual(got, ["name_word", "name_sub"])  # the word match first although it is older
        self.assertEqual(corpus.word_matched(query.parse("何君堯"), corpus.match_ids(query.parse("何君堯"), "2000-01-01", "2100-01-01")),
                         {corpus.ids_for_urls(["https://c/name_word"])["https://c/name_word"]})

    def test_cantonese(self):
        self.assertIn("canto", keys("咗"))
        self.assertIn("canto", keys("嘅"))
        self.assertIn("canto", keys("佢哋"))
        self.assertIn("canto", keys('"做咗好多嘢"'))

    def test_english(self):
        self.assertEqual(set(keys("hsbc")), {"eng", "eng_fw"})          # case and full-width letters
        self.assertEqual(set(keys('"hong kong"')), {"eng", "eng_hyphen"})  # words: the hyphen is a break
        self.assertIn("eng", keys("election"))                          # substring fallback: elections
        self.assertEqual(keys("elections"), ["eng"])

    def test_ranking(self):  # word matches before substring-only ones (corpus.match_ids)
        self.assertEqual([k for k in keys("何君堯") if k.startswith("name_")], ["name_word", "name_sub"])

    def test_display_text_unchanged(self):
        r = corpus.db().execute("SELECT title, text, hay, stitle, search_v FROM posts WHERE url='https://c/trad_vote'").fetchone()
        self.assertEqual((r["title"], r["text"]), (POSTS[0][1], POSTS[0][2]))
        self.assertIn("选举委员会", r["hay"])
        self.assertEqual(r["stitle"], "选举委员会界别分组选举结果公布")
        self.assertEqual(r["search_v"], searchtext.VERSION)

    def test_rows_indexed_before_the_change(self):
        """a row as the old code wrote it (hay = raw lower case, its grams, no words/stitle/version) is found while it
        waits for reindex(), and reindex() rewrites only such rows"""
        old_hay = "舊稿 選舉委員會\n明報"
        corpus.db().execute("""INSERT INTO posts (url, title, text, hay, grams, source, day, published)
                               VALUES ('https://c/legacy', '舊稿 選舉委員會', '', %s, array_to_tsvector(%s::text[]), '明報',
                                       '2026-10-01', '2026-10-01T08:00+08:00')""", (old_hay, corpus.grams(old_hay)))
        self.assertIn("legacy", keys("選舉"))            # as typed
        self.assertIn("legacy", keys("TopicSearchOnly 舊稿"))
        self.assertEqual(corpus.reindex(batch=2, pause=0), 1)
        self.assertIn("legacy", keys("选举"))            # now in Simplified too
        self.assertEqual(corpus.reindex(pause=0), 0)
        corpus.db().execute("UPDATE posts SET search_v='old' WHERE url LIKE 'https://c/trad%%'")
        self.assertEqual(corpus.reindex(pause=0), 4)
        corpus.db().execute("DELETE FROM posts WHERE url='https://c/legacy'")

    def test_index_is_used(self):
        where, args = query.to_sql(query.parse("选举 AND 李家超"))
        with corpus.db() as c:
            c.execute("SET LOCAL enable_seqscan = off")
            plan = "\n".join(r[0] for r in c.execute("EXPLAIN SELECT p.id FROM posts p WHERE " + where, args))
        self.assertIn("posts_words", plan)
        self.assertIn("posts_grams", plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)
