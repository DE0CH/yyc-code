#!/usr/bin/env python3
"""LIHKG likes and dislikes (replies_fetch.py), on a throwaway database (TEST_DATABASE_URL), no network:

1. A thread page files each reply with LIHKG's like_count and dislike_count, and the thread with its own counts.
2. Re-reading a page refreshes the counts of replies filed earlier on it.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_lihkg_votes.py
"""
import json, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, replies_fetch  # noqa: E402


def page(tid, msgs, likes=1, dislikes=9):
    """a LIHKG thread page: msgs = [(msg_num, like, dislike)]"""
    return {"thread_id": tid, "cat_id": 5, "category": {"name": "時事台"}, "title": f"帖{tid}", "create_time": 1791949651,
            "user_nickname": "樓主", "no_of_reply": max(n for n, _, _ in msgs), "like_count": likes,
            "dislike_count": dislikes, "total_page": 1,
            "item_data": [{"msg_num": n, "msg": f"第{n}樓", "user_nickname": f"u{n}", "reply_time": 1791949651 + n,
                           "like_count": lk, "dislike_count": dk} for n, lk, dk in msgs]}


class Px:
    direct = True


def eng(url):
    r = corpus.db().execute("SELECT engagement FROM posts WHERE url=%s", (url,)).fetchone()
    return json.loads(r["engagement"]) if r and r["engagement"] else None


class Votes(unittest.TestCase):
    def test_1_thread_page(self):
        pages = {1: page(100, [(1, 0, 0), (2, 5, 1), (3, 0, 4)])}
        with mock.patch.object(replies_fetch, "lihkg_json", lambda px, path, ref=None: pages[1]):
            new, used = replies_fetch.lihkg_thread(Px(), 100, 3, 10)
        self.assertEqual((new, used), (2, 1))
        self.assertEqual(eng("https://lihkg.com/thread/100/page/1#c2"), {"likes": 5, "dislikes": 1})
        self.assertEqual(eng("https://lihkg.com/thread/100/page/1#c3"), {"likes": 0, "dislikes": 4})
        self.assertEqual(eng("https://lihkg.com/thread/100/page/1"), {"replies": 3, "likes": 1, "dislikes": 9, "comments": 2})

    def test_2_refresh(self):
        pages = {1: page(100, [(1, 0, 0), (2, 6, 3), (3, 0, 4), (4, 1, 0)])}
        with mock.patch.object(replies_fetch, "lihkg_json", lambda px, path, ref=None: pages[1]):
            new, _ = replies_fetch.lihkg_thread(Px(), 100, 4, 10)
        self.assertEqual(new, 1)
        self.assertEqual(eng("https://lihkg.com/thread/100/page/1#c2"), {"likes": 6, "dislikes": 3})

if __name__ == "__main__":
    unittest.main()
