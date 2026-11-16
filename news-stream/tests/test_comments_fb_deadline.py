#!/usr/bin/env python3
"""A Facebook post's comment read given an end (comments_fetch.facebook_post's `end`) stops there and the next read
carries on where it stopped: a busy thread's full read (every comment page plus the replies; Skyposthk, 1,774 comments)
took 18 minutes on 20 Oct 2026. Each page and each reply page checks the end: a read cut there keeps what it got,
is not marked checked and saves the cursor, and the next read's full pass starts at that cursor instead of the first
page. TEST_DATABASE_URL, a throwaway database.

The two-pass reads (social_twopass.py, on the poller) are bounded (2026-10-20): each Facebook read has an end
(social_twopass.FB_READ_S) and a request cap (social_twopass.FB_PAGES): the newest comments, which are what the scorer
reads (streams.REPLY_CHARS of the newest), and no full pass over every older page, so one busy thread never stalls the
poller's fetcher.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_comments_fb_deadline.py
"""
import os, sys, time, types, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.setdefault("RESI_PROXY", "http://u:p@127.0.0.1:9")
os.environ.setdefault("TIKHUB_API", "test")
import corpus  # noqa: E402
import comments_fetch  # noqa: E402

HKT = timezone(timedelta(hours=8))
NOW = int(time.time())
CLOCK = [1000.0]  # the test's clock: every Facebook request takes one second


class FakeFB:
    """comment pages (10 a page, newest first) and reply pages over lists; each request moves CLOCK on a second"""
    def __init__(self, comments, replies):
        self.comments, self.replies, self.q, self.bytes, self.calls = comments, replies, comments_fetch.FB_QUERIES, 0, []

    def post_id(self, url):
        return "999"

    def node(self, start):
        chunk = self.comments[start:start + 10]
        edges = [{"node": {"legacy_fbid": cid, "body": {"text": f"c{cid}"}, "created_time": NOW - 60,
                           "feedback": {"id": f"f{cid}", "expansion_info": {"expansion_token": "t"},
                                        "replies_fields": {"total_count": len(self.replies.get(cid, []))}}}} for cid in chunk]
        total = len(self.comments) + sum(len(v) for v in self.replies.values())
        return {"comment_rendering_instance_for_feed_location": {"comments": {
            "edges": edges, "total_count": total,
            "page_info": {"end_cursor": str(start + 10), "has_next_page": start + 10 < len(self.comments)}}}}

    def gql(self, which, v):
        CLOCK[0] += 1
        self.calls.append(which)
        if which == "root":
            return self.node(0)
        if which == "page":
            return self.node(int(v["commentsAfterCursor"]))
        rs = self.replies.get(v["id"][1:], [])
        return {"replies_connection": {"edges": [{"node": {"legacy_fbid": r, "body": {"text": f"r{r}"}, "created_time": NOW - 30}}
                                                 for r in rs], "page_info": {"has_next_page": False}}}


def post(url, age_h=3):
    return {"url": url, "title": "t", "sid": "facebook", "source": "s", "section": "Facebook", "published": None, "age_h": age_h}


def rows(url):
    return corpus.db().execute("SELECT count(*) n FROM posts WHERE thread=%s", (url,)).fetchone()["n"]


class FacebookPostDeadline(unittest.TestCase):
    def setUp(self):
        self.clock = mock.patch.object(comments_fetch, "time", types.SimpleNamespace(time=lambda: CLOCK[0], sleep=lambda s: None))
        self.clock.start()

    def tearDown(self):
        self.clock.stop()

    def test_a_read_stops_at_the_end_and_carries_on(self):
        url = "https://www.facebook.com/x/posts/busy"
        fb = FakeFB([str(i) for i in range(1000, 0, -1)], {"500": ["r1", "r2", "r3"]})  # 100 pages, 3 replies
        comments_fetch.facebook_post(fb, post(url), end=CLOCK[0] + 30)
        self.assertLessEqual(CLOCK[0], 1000 + 31, "the read ran past the pass's end")
        first = rows(url)
        self.assertGreater(first, 0, "a cut read kept nothing")
        self.assertLess(first, 1003)
        self.assertNotIn("checked", corpus.kv_get("cm:" + url), "a cut read was marked checked")

        fb.calls = []
        comments_fetch.facebook_post(fb, post(url), end=float("inf"))
        self.assertEqual(rows(url), 1003, "the next check did not finish the thread")
        self.assertLessEqual(fb.calls.count("page") + fb.calls.count("root"), 100 - 30 + 5,
                             "the next check started the full read again from the first page")
        self.assertIn("checked", corpus.kv_get("cm:" + url), "a finished read is not marked checked")


    def test_b_capped_read_takes_the_newest_and_no_full_pass(self):
        url = "https://www.facebook.com/x/posts/capped"
        fb = FakeFB([str(i) for i in range(2000, 0, -1)], {"1990": ["r1"]})  # 200 pages
        comments_fetch.facebook_post(fb, post(url), end=float("inf"), pages=12)
        self.assertLessEqual(len(fb.calls), 12, f"{len(fb.calls)} requests for a read capped at 12")
        got = {r["text"] for r in corpus.db().execute("SELECT text FROM posts WHERE thread=%s", (url,))}
        self.assertIn("c2000", got, "the newest comment is missing")
        self.assertNotIn("c1", got, "a capped read went on to the oldest page (a full pass)")

    def test_the_two_pass_read_is_bounded(self):
        import social_twopass
        seen = {}

        def fake_post(fb, p, end=float("inf"), pages=None):
            seen.update(end=end, pages=pages)
            return 0
        social_twopass._local.__dict__.clear()
        with mock.patch.object(comments_fetch, "FB", lambda: types.SimpleNamespace(bytes=0)), \
                mock.patch.object(comments_fetch, "facebook_post", fake_post):
            social_twopass.read({"url": "https://www.facebook.com/x/posts/1", "title": "t"})
        self.assertLess(seen["end"], time.time() + 3600, "the poller's Facebook read has no end")
        self.assertTrue(seen["pages"], "the poller's Facebook read has no request cap")


if __name__ == "__main__":
    unittest.main()
