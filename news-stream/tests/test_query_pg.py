#!/usr/bin/env python3
"""query.py's language on Postgres: every operator, against a Python reference of the same rules, on a throwaway
database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_query_pg.py

1. Hand-made posts with the expected matches per query (phrases, OR by whitespace, AND/OR/NOT and their binding,
   NEARBY/n and NEAR, FindIn per field, IncData/ExcData by source type, website, sid, platform or source,
   TopicSearchOnly, comment rows, profile filters).
2. NEARBY fuzz: the SQL near() against the Python rule (non-overlapping matches, within n + the longer term's length)
   on random strings, empty terms and overlapping terms included.
"""
import os, random, re, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus, query  # noqa: E402


def near_ref(hay, a, b, n):
    """the rule as the SQLite version computed it in Python"""
    if hay is None:
        return False
    pa = [m.start() for m in re.finditer(re.escape(a), hay)]
    if not pa:
        return False
    pb = [m.start() for m in re.finditer(re.escape(b), hay)]
    return any(abs(x - y) <= n + max(len(a), len(b)) for x in pa for y in pb)


POSTS = [  # url, title, text, author, source, sid, extra
    ("u1", "滙豐業績公布", "HSBC 今日公布業績", "記者甲", "明報", "mingpao", {}),
    ("u2", "Hong Kong Bank news", "the hong kong bank said", "Reporter", "RTHK", "rthk", {}),
    ("u3", "港投公司投資半導體", "港投 晶片 投資", None, "香港01", "hk01", {}),
    ("u4", "港投公司年報", "沒有相關字", None, "香港01", "hk01", {}),
    ("u5", "陳茂波談預算", "陳茂波表示，明年的財政預算將會", None, "信報", "hkej", {}),
    ("u6", "陳茂波出席活動", "陳茂波今日出席了一個與房屋和土地有關的長篇活動，之後很久很久才談到預算", None, "信報", "hkej", {}),
    ("u7", "施政報告重點", "房屋政策", "編輯部", "LIHKG討論區", "lihkg", {}),
    ("u8", "討論施政報告", "施政報告好差", "user1", "LIHKG討論區", "lihkg", {}),
    ("u9", "Galbot 機械人", "銀河通用 Galbot G1", "kol", "TikTok", "tikhub_tiktok", {}),
    ("u10", "HSBC 出售業務", "滙豐 出售 [售]", None, "經濟日報", "hket", {}),
    ("u11", "thread title 施政報告", None, None, "LIHKG討論區", "lihkg", {}),
]
COMMENTS = [("u11", "c1", "我覺得施政報告唔掂", "commenter")]


def setup():
    items = []
    for url, title, text, author, source, sid, extra in POSTS:
        items.append(dict({"url": "https://x/" + url, "title": title, "text": text, "author": author, "source": source,
                           "sid": sid, "published": "2026-10-12T10:00+08:00"}, **extra))
    corpus.ingest(items, seen="2026-10-12T10:00:00+08:00")
    for t, cid, text, author in COMMENTS:
        thread = next(i for i in items if i["url"] == "https://x/" + t)
        corpus.add_comments(thread, [{"id": cid, "text": text, "author": author, "published": "2026-10-12T11:00+08:00"}])


def m(q, f=None):
    ids = corpus.match_ids(query.parse(q), "2026-10-01", "2026-10-31", f)
    return sorted(r["url"].replace("https://x/", "") for r in corpus.posts(ids))


class Language(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup()

    def test_terms(self):
        self.assertEqual(m("滙豐"), ["u1", "u10"])
        self.assertEqual(m("hsbc"), ["u1", "u10"])  # case-insensitive
        self.assertEqual(m('"hong kong bank"'), ["u2"])
        self.assertEqual(m("滙豐 Galbot"), ["u1", "u10", "u9"])  # whitespace = OR

    def test_boolean(self):
        self.assertEqual(m("港投 AND (半導體 OR 晶片)"), ["u3"])
        self.assertEqual(m("HSBC NOT (出售 \"[售]\")"), ["u1"])
        self.assertEqual(m("滙豐 Galbot NOT 出售"), ["u1", "u9"])  # A B NOT C = (A OR B) AND NOT C
        self.assertEqual(m("NOT 香港"), sorted(["u1", "u2", "u5", "u6", "u7", "u8", "u9", "u10", "u11", "u11#cc1"]))

    def test_near(self):
        self.assertEqual(m("陳茂波 NEARBY/20 預算"), ["u5"])
        self.assertEqual(m("陳茂波 NEARBY/60 預算"), ["u5", "u6"])
        self.assertEqual(m("陳茂波 NEAR 預算"), ["u5"])

    def test_fields(self):
        self.assertEqual(m("FindIn(title: 施政報告)"), ["u11", "u11#cc1", "u7", "u8"])  # a comment row keeps its thread's title
        self.assertEqual(m("FindIn(content: 房屋)"), ["u6", "u7"])
        self.assertEqual(m("FindIn(author: 編輯)"), ["u7"])
        self.assertEqual(m("施政報告 TopicSearchOnly"), ["u11", "u11#cc1", "u7", "u8"])

    def test_data(self):
        self.assertEqual(m("IncData(Forum) 施政報告"), ["u11", "u11#cc1", "u7", "u8"])
        self.assertEqual(m("ExcData(Forum) 施政報告"), [])
        self.assertEqual(m("IncData(TikTok)"), ["u9"])
        self.assertEqual(m("IncData(tikhub_tiktok)"), ["u9"])
        self.assertEqual(m("IncData(Video)"), ["u9"])

    def test_filters(self):
        self.assertEqual(m("施政報告", {"comments_only": True}), ["u11#cc1"])
        self.assertEqual(m("港投", {"exclude_websites": ["香港01"]}), [])
        self.assertEqual(m("滙豐", {"source_types": ["News"], "regions": ["HK"]}), ["u1", "u10"])

    def test_errors(self):
        for bad in ("(a", "a NEARBY (b OR c)", "FindIn(nowhere: x)"):
            self.assertRaises(query.QueryError, query.parse, bad)


class NearFuzz(unittest.TestCase):
    def test_fuzz(self):
        rnd = random.Random(7)
        cases = []
        for _ in range(3000):
            hay = "".join(rnd.choice("aab港投c ") for _ in range(rnd.randint(0, 60)))
            a = "".join(rnd.choice("ab港") for _ in range(rnd.randint(0, 3)))
            b = "".join(rnd.choice("ab投c") for _ in range(rnd.randint(1, 3)))
            cases.append((hay, a, b, rnd.randint(0, 12)))
        cases += [("aaaa", "aa", "aa", 0), ("", "", "", 0), (None, "a", "b", 5), ("abab", "aba", "b", 0)]
        rows = corpus.db().execute("SELECT near(h, a, b, n) FROM unnest(%s::text[], %s::text[], %s::text[], %s::int[]) AS t(h, a, b, n)",
                                   ([c[0] for c in cases], [c[1] for c in cases], [c[2] for c in cases], [c[3] for c in cases])).fetchall()
        for c, r in zip(cases, rows):
            self.assertEqual(bool(r[0]), near_ref(*c), c)


if __name__ == "__main__":
    unittest.main(verbosity=2)
