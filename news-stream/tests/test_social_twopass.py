#!/usr/bin/env python3
"""Social posts scored with their comments, twice (Deyao, 2026-10-14), on twopass.py like LIHKG threads, on a
throwaway database (TEST_DATABASE_URL), the model stubbed, no network:

1. Facebook, Instagram, Threads and YouTube posts are units (twopass.SOCIAL_URLS), whoever collected them (the
   followed pages and channels, the social searches); a broadcast part (#t=) and a comment row are not.
2. Their comments are read at capture by social_twopass.py, and the scorer waits for that read (twopass.FETCHER_URLS:
   a post nobody has started reading yet still waits); the first score includes the comments.
3. When the second scoring is due (published + 30 min), social_twopass reads the comments again, and the post is
   scored once more with them; the later score is the current one; one stream item (one Telegram message).
4. A post first caught 30+ minutes after publication is scored once.
5. The item page's divider says 留言 for a social post (回覆 stays for LIHKG).
6. Only posts after the scoring switch that were never scored are read at capture (no history re-read).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_social_twopass.py
"""
import os, re, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, social_twopass, streams, titles, twopass  # noqa: E402

HKT = corpus.HKT
STREAMS = app.STREAMS
CALLS = []


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000, **kw):
    """CEHK 90 when 李家超 is in a post's block (its comments included), else 30"""
    CALLS.append((purpose, prompt))
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for b in re.split(r"(?m)^(?=\d+\. \[)", body):
        m = re.match(r"(\d+)\. ", b)
        if m:
            sc = 90 if "李家超" in b else 30
            out.append({"i": int(m.group(1)), "sentiment": "neutral", "labels": [],
                        "streams": [{"id": "CEHK", "score": sc, "reason": "r", "category": "其他"}]})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, CALLS)  # stage 2 answers as stage 1 (tests/stage2.py)
titles.ensure = lambda posts: None
COMMENTS, READ = {}, []


def fake_read(p):
    READ.append(p["url"])
    corpus.add_comments({k: p[k] for k in ("url", "title", "sid", "source", "section") if p.get(k)},
                        [{"id": f"{i}", "author": "u", "text": t, "published": p["published"]} for i, t in enumerate(COMMENTS.get(p["url"], []))])


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def fb_post(n, pub, seen=None):
    url = f"https://www.facebook.com/page/posts/pfbid{n}"
    corpus.ingest([{"sid": "facebook", "source": "Page", "title": f"帖子{n}", "url": url, "text": "內文",
                    "published": pub.isoformat(), "section": "Facebook"}], seen=(seen or pub).isoformat())
    return url


def score(url):
    return [r[0] for r in corpus.db().execute("""SELECT s.score FROM scores s JOIN posts p ON p.id = s.post_id
                                                 WHERE p.url=%s AND s.stream='CEHK'""", (url,))]


class Social(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.ingest([{"sid": "facebook", "source": "Page", "title": "seed", "url": "https://www.facebook.com/page/posts/seed",
                        "section": "Facebook"}])  # the source is not on its first run
        corpus.kv_set("scoring", {"from_id": corpus.db().execute("SELECT max(id) FROM posts").fetchone()[0],
                                  "comments": False, "since": "2026-10-14T10:00:00+08:00"})

    def setUp(self):
        CALLS.clear()
        READ.clear()

    def run_at(self, t):
        with mock.patch.object(streams, "clock", Clock(t)), mock.patch.object(twopass, "_now", Clock(t)), \
                mock.patch.object(social_twopass, "now", Clock(t)), mock.patch.object(social_twopass, "read", fake_read):
            social_twopass.run_once()
            return streams.run_once(STREAMS)

    def test_1_units(self):
        rx = [re.compile(r) for r in twopass.SOCIAL_URLS]
        for u in ["https://www.facebook.com/digpin/posts/pfbid02GU2", "https://www.facebook.com/reel/2340616826791071/",
                  "https://www.instagram.com/p/Ded1a4rFynP/", "https://www.threads.com/@philo_bear/post/DeeojgIlDQY",
                  "https://www.threads.net/@passiontimes/post/DeeojgIlDQY", "https://www.youtube.com/watch?v=zID4AFkimLI"]:
            self.assertTrue(any(r.search(u) for r in rx), u)
        for u in ["https://www.youtube.com/watch?v=YP4F2SVf-BY#t=900", "https://news.test/1"]:
            self.assertFalse(any(r.search(u) for r in rx), u)

    def test_2_two_reads_two_scores(self):
        t = datetime.now(HKT).replace(microsecond=0)
        pub = t - timedelta(minutes=5)
        url = fb_post(1, pub, seen=t)
        with mock.patch.object(social_twopass, "run_once", lambda: 0):  # the fetcher hasn't come round yet
            with mock.patch.object(streams, "clock", Clock(t)), mock.patch.object(twopass, "_now", Clock(t)):
                self.assertEqual(streams.run_once(STREAMS), 0, "scored before its comments were read")
        COMMENTS[url] = ["第一則留言"]
        self.assertEqual(self.run_at(t), 1)
        self.assertEqual(READ, [url])
        self.assertIn("第一則留言", CALLS[-1][1])
        self.assertEqual(score(url), [30])
        self.assertEqual(self.run_at(t + timedelta(minutes=10)), 0)  # before the mark
        COMMENTS[url].append("李家超今日都有講")
        READ.clear()
        self.assertEqual(self.run_at(pub + timedelta(minutes=31)), 1)
        self.assertEqual(READ, [url], "the comments were not read again for the second scoring")
        self.assertEqual(score(url), [90], "the later score is not the current one")
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM stream_items WHERE url=%s", (url,)).fetchone()[0], 1)
        self.assertEqual(self.run_at(pub + timedelta(hours=3)), 0)  # never a third time
        self.assertEqual(READ, [url], "the comments were read a third time (Deyao, 2026-10-20: only the two scorings)")

    def test_3_late_capture_scored_once(self):
        t = datetime.now(HKT).replace(microsecond=0)
        url = fb_post(2, t - timedelta(minutes=45), seen=t)
        self.assertEqual(self.run_at(t), 1)
        self.assertEqual(self.run_at(t + timedelta(minutes=40)), 0)
        self.assertEqual(READ, [url])

    def test_4_divider_says_comments(self):
        tsx = open(os.path.join(HERE, "web-app", "src", "routes", "-ItemPage.tsx")).read()
        self.assertIn("以上${word}已納入評分", tsx)
        self.assertIn('? "回覆" : "留言"', tsx)

    def test_5_history_not_read(self):
        t = datetime.now(HKT).replace(microsecond=0)
        url = fb_post(3, t - timedelta(minutes=5), seen=t)
        corpus.db().execute("INSERT INTO scored (post_id, hash, at) SELECT id, 'x', %s FROM posts WHERE url=%s", (t.isoformat(), url))
        self.run_at(t)
        self.assertNotIn(url, READ)

    def test_6_host_prefilter(self):
        # run_once's host prefilter (twopass.FETCHER_HOSTS_RX) passes every post a fetcher reads: each pattern names its
        # host, and the host of any URL a pattern matches matches the prefilter (in Postgres too)
        for r in twopass.FETCHER_URLS:
            self.assertRegex(r, r"^\^https\??://[^/]+/", r)
        urls = ["https://www.facebook.com/digpin/posts/pfbid02GU2", "https://www.instagram.com/reel/Ab_1/",
                "https://www.threads.net/@a/post/DeeojgIlDQY", "https://www.threads.com/@a/post/DeeojgIlDQY",
                "https://www.youtube.com/watch?v=zID4AFkimLI", "https://www.eyny.com/thread-123-1-1.html",
                "https://www05.eyny.com/archiver/?tid-123.html", "http://www17.eyny.com/thread-9-1-1.html",
                "https://post76.hk/thread-1-1-1.html", "https://forums.androidcentral.com/threads/a-b.123/"]
        for u in urls:
            self.assertTrue(any(re.search(r, u) for r in twopass.FETCHER_URLS), u)
            host = u.split("/")[2]
            self.assertRegex(host, twopass.FETCHER_HOSTS_RX)
            self.assertTrue(corpus.db().execute("SELECT %s ~ %s", (host, twopass.FETCHER_HOSTS_RX)).fetchone()[0], host)
        for host in ["news.test", "m.facebook.com", "www.threads.org", "eyny.com", "wwwx.eyny.com"]:
            self.assertNotRegex(host, twopass.FETCHER_HOSTS_RX)


if __name__ == "__main__":
    unittest.main(verbosity=2)
