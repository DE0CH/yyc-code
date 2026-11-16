#!/usr/bin/env python3
"""tools/stream_history.py on a throwaway database (TEST_DATABASE_URL; dropped and made again), the model stubbed:
one stream's history is scored (that stream only, comments left out), the posts at or above its threshold are filed
under their own publication day oldest first with one item per headline, other streams' scores and items stay as
they were, Telegram is armed from the stream's first item, and a rerun neither re-scores nor files twice.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_stream_history.py
"""
import os, re, sys, tempfile, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, corpus  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "tools"))
import stream_history as sh  # noqa: E402

CALLS = []


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    CALLS.append((purpose, prompt))
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i, line in re.findall(r"(?m)^(\d+)\. \[[^\]]*\] (.*)$", body):
        st = [{"id": "WFC", "score": 92, "reason": "宏福苑"}] if "宏福苑" in line else []
        out.append({"i": int(i), "sentiment": "neutral", "labels": [], "streams": st})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)


def add(n, title, published, thread=None):
    corpus.db().execute("""INSERT INTO posts (url, title, text, published, seen, source, website, thread)
                           VALUES (%s, %s, '', %s, %s, 'x', 'X', %s)""",
                        (f"https://x.hk/{n}", title, published, published, thread))


class History(unittest.TestCase):
    def test_wfc(self):
        corpus.db()  # schema
        add(1, "宏福苑 五級火", "2025-11-26T15:00+08:00")
        add(2, "宏福苑 五級火", "2025-11-26T16:00+08:00")      # same headline, same day: one item
        add(3, "宏福苑 獨立委員會", "2026-08-07T10:00+08:00")
        add(4, "李家超 出訪", "2026-08-07T11:00+08:00")
        add(5, "宏福苑 留言", "2026-08-07T12:00+08:00", thread="https://x.hk/3")  # a comment: never scored
        c = corpus.db()
        c.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at) SELECT 'CEHK', id, 90, 'x', '2026-08-07' FROM posts WHERE url='https://x.hk/4'")
        corpus.kv_set("telegram_sent", {"CEHK": 7})
        self.assertEqual(sh.estimate("WFC")["posts"], 4)
        sh.score("WFC", None)
        self.assertTrue(all(p == sh.PURPOSE for p, _ in CALLS))
        self.assertTrue(all('<topic id="WFC"' in pr and '<topic id="CEHK"' not in pr for _, pr in CALLS))
        sh.file("WFC")
        days = [r[0] for r in c.execute("SELECT day FROM stream_items WHERE stream='WFC' ORDER BY id")]
        self.assertEqual(days, ["2025-11-26", "2026-08-07"])
        self.assertEqual(c.execute("SELECT count(*) FROM scores WHERE stream='CEHK'").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT count(*) FROM stream_items WHERE stream<>'WFC'").fetchone()[0], 0)
        lo = c.execute("SELECT min(id) FROM stream_items WHERE stream='WFC'").fetchone()[0]
        self.assertEqual(corpus.kv_get("telegram_sent"), {"CEHK": 7, "WFC": lo - 1})
        self.assertEqual(c.execute("SELECT count(*) FROM tags WHERE profile='WFC'").fetchone()[0], 3)
        n = len(CALLS)
        s = sh.state("WFC")
        s["phase"] = "score"
        sh.save("WFC", s)
        sh.score("WFC", None)
        sh.file("WFC")
        self.assertEqual(len(CALLS), n)
        self.assertEqual(c.execute("SELECT count(*) FROM stream_items WHERE stream='WFC'").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
