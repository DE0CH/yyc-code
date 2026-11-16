#!/usr/bin/env python3
"""A URL longer than a btree index row never stops the poll (2026-10-16).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_long_urls.py

Live 2026-10-16 08:36 HKT: a Google News result for 巴士的報 (gnews_15/16/17) had a 3,633-byte news.google.com link.
Once decoded, poll_once stored that link in poll_seen, whose primary key is a btree on url (max 2,704 bytes a row):
"index row size 3648 exceeds btree version 4 maximum 2704 for index poll_seen_pkey" rolled back the whole poll every
5 minutes, so no feed was read and nothing new was filed until it was fixed (2.5 h). Now:

1. A poll with an over-long Google link completes: last_poll moves, the publisher's article and the other items are filed.
2. The long link is remembered as seen (poll_seen keeps a digest of it), so the next poll doesn't decode it again.
"""
import json, os, random, re, string, sys, tempfile, unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
os.environ.pop("RESI_PROXY", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, insights, sources  # noqa: E402

app.after_poll = lambda g, f: insights.after_poll(g, f)  # in this thread, so the test can check the corpus
app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
sources.discuss_search = lambda qs: []

RND = random.Random(16)  # incompressible, like the live link (Postgres compresses a repetitive key under the limit)
LONG_AID = "CBMi" + "".join(RND.choice(string.ascii_letters + string.digits) for _ in range(3600))  # live: 3,633 bytes


class FakeGoogle:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, proxy=None, data=None):
        if method == "GET":
            aid = re.search(r"articles/([^?/]+)", url).group(1)
        else:
            aid = json.loads(json.loads(data["f.req"])[0][0][1])[2]
        self.calls.append((method, aid))
        if method == "GET":
            return f'<c-wiz data-n-a-sg="SIG" data-n-a-ts="1760000000"></c-wiz>'
        pub = "https://pub.test/long-google" if aid == LONG_AID else f"https://pub.test/{aid}"
        return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", json.dumps(["garturlres", pub, 1])]])


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"gnews_15": {"ok": True, "items": len(items), "error": None}})
    app.poll_once()


def posts_like(pat):
    return [r[0] for r in corpus.db().execute("SELECT url FROM posts WHERE url LIKE %s", (pat,))]


class LongUrls(unittest.TestCase):
    def setUp(self):
        app.state.clear()
        with corpus.db() as c:
            c.execute("DELETE FROM poll_state")
        app.load_state()

    def test_long_google_link(self):
        g = app.google_get = FakeGoogle()
        now = app.datetime.now(app.HKT)
        items = [
            {"sid": "gnews_15", "source": "Google News", "section": "巴士的報", "title": "長連結 新聞",
             "url": f"https://news.google.com/rss/articles/{LONG_AID}?oc=5", "published": now},
            {"sid": "gnews_15", "source": "Google News", "section": "某報", "title": "短連結 新聞",
             "url": "https://news.google.com/rss/articles/SHORT1?oc=5", "published": now},
        ]
        poll(items)  # must not raise
        self.assertEqual(app.poll_get("last_poll"), app.state["last_poll"])
        self.assertIsNotNone(app.poll_get("last_poll"))
        self.assertEqual(posts_like("https://pub.test/SHORT1"), ["https://pub.test/SHORT1"])
        self.assertEqual(posts_like("https://pub.test/long-google"), ["https://pub.test/long-google"])
        # the next poll: nothing decoded or filed again
        n_calls = len(g.calls)
        n_posts = corpus.db().execute("SELECT count(*) FROM posts").fetchone()[0]
        poll(items)
        self.assertEqual(len(g.calls), n_calls)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts").fetchone()[0], n_posts)
        self.assertEqual(app.seen_urls([items[0]["url"]]), {items[0]["url"]})


if __name__ == "__main__":
    unittest.main()
