#!/usr/bin/env python3
"""Comments are read only for the two-pass scoring, and the Facebook session is renewed only when Facebook refuses it
(Deyao, 2026-10-20: IPRoyal yycstream at ~308 MB/h, Facebook comment reads ~172 MB/h of it), on a throwaway database
(TEST_DATABASE_URL), no network:

Comments
1. No scheduled re-checks on any platform: the `comments` CronJob is suspended, comments_fetch.py keeps only the
   readers the two-pass fetcher (social_twopass.py, in the poller) calls at capture and for the 30-minute second
   scoring, and watch_fetch.py reads no X/TikTok/微博/B站 comments at 6 h / 24 h.
2. fb_fetch.py records each new post's id (kv fbpid:<url>) as it files the post, so the capture read names the post
   by it at once (no post page read; reels have no id in their page). The comments job used to copy it from
   facebook.json.
Facebook session (fb_fetch.py)
3. A page that answers without a timeline (Facebook's node is the Page alone: 9 of 223 pages logged out, 20 Oct) is
   that page's error: it never renews the session. Before, three such pages in a run counted as a stale session and
   every 15-min run captured a new one in a browser (~3 MB, ~12 MB/h).
4. A session is kept however old it is while Facebook answers it (no 6-hour renewal).
5. A refused session (the probe of the busiest page gets no timeline) is renewed once, and the run reads on with it.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_comments_twopass_only.py
"""
import json, os, re, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.setdefault("RESI_PROXY", "http://u:p@127.0.0.1:9")
os.environ.setdefault("TIKHUB_API", "test")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, comments_fetch, fb_fetch, watch_fetch, watchlist  # noqa: E402

HKT = timezone(timedelta(hours=8))
TMP = tempfile.mkdtemp()
NOW = int(time.time())


class Resp:
    def __init__(self, body):
        self.content = body
        self.request_size, self.download_size, self.header_size = 5000, len(body), 300
        self.status_code = 200


class FakeFacebook:
    """the timeline request: pages[pid] = [(creation_time, post_id)], newest first, 3 an answer; pids in `bare` answer
    the Page node alone (no timeline); while `refused`, every answer is Facebook's error for a bad session"""
    pages, bare, refused = {}, set(), [False]

    def __init__(self, *a, **k):
        pass

    def post(self, url, data=None, **k):
        from urllib.parse import parse_qs
        v = json.loads(parse_qs(data)["variables"][0])
        pid = v["id"]
        if FakeFacebook.refused[0]:
            return Resp(json.dumps({"error": 1357004, "errorSummary": "Sorry, something went wrong"}).encode())
        if pid in FakeFacebook.bare:
            return Resp(json.dumps({"data": {"node": {"__typename": "Page", "id": pid}},
                                    "extensions": {"server_metadata": {"request_start_time_ms": 1}}}).encode())
        ps = sorted((p for p in FakeFacebook.pages.get(pid, []) if v.get("afterTime") is None or p[0] >= v["afterTime"]), reverse=True)
        start = int(v.get("cursor") or 0)
        chunk = ps[start:start + v.get("count", 3)]
        more = start + len(chunk) < len(ps)
        edges = [{"node": {"creation_time": t, "post_id": p_,
                           "comet_sections": {"timestamp": {"story": {"url": f"https://www.facebook.com/{pid}/posts/pfbid{p_}"}},
                                              "content": {"message": {"text": f"post {p_}"}}}}} for t, p_ in chunk]
        body = {"data": {"node": {"timeline_list_feed_units": {"edges": edges,
                                                               "page_info": {"end_cursor": str(start + len(chunk)), "has_next_page": more}}}}}
        return Resp(json.dumps(body).encode())


def session(age_h=0):
    return {"post": "variables=" + json.dumps({"count": 3}), "headers": {}, "cookies": {}, "ua": "x",
            "captured": (datetime.now(HKT) - timedelta(hours=age_h)).isoformat(timespec="seconds")}


def poll(pages, bare=(), age_h=0, refused=False):
    """one fb_fetch poll over these pages → (facebook.json, browser captures)"""
    corpus.file_put("facebook.json", {})
    corpus.file_put("fb-session.json", session(age_h))
    names = list(pages) + list(bare)
    corpus.file_put("fb-ids.json", {f"https://www.facebook.com/{p}": p for p in names})
    FakeFacebook.pages, FakeFacebook.bare, FakeFacebook.refused[0] = pages, set(bare), refused
    with open(os.path.join(TMP, "fb_pages.json"), "w") as f:
        json.dump([{"group": "media", "name": n, "platform": "facebook", "url": f"https://www.facebook.com/{n}",
                    "why": "test", "since": "2026-10-12"} for n in names], f)
    caps = []

    def capture():
        caps.append(1)
        FakeFacebook.refused[0] = False  # the new session is answered
        s = session()
        corpus.file_put("fb-session.json", s)
        return s
    with mock.patch.object(watchlist, "FILE", os.path.join(TMP, "fb_pages.json")), \
            mock.patch("curl_cffi.requests.Session", FakeFacebook), \
            mock.patch.object(fb_fetch, "capture_session", capture):
        fb_fetch.poll()
    return corpus.file_get("facebook.json"), len(caps)


class NoRechecks(unittest.TestCase):
    def test_1_comments_cronjob_suspended(self):
        y = open(os.path.join(HERE, "deploy", "apps", "cronjobs.yaml")).read()
        doc = next(d for d in y.split("\n---") if re.search(r"(?m)^  name: comments$", d))
        self.assertRegex(doc, r"(?m)^  suspend: true\b", "the comments CronJob still runs its re-checks")

    def test_1_no_schedule_in_comments_fetch(self):
        for name in ("RUN", "DUE", "due", "recent", "run_youtube", "run_facebook", "run_instagram", "main"):
            self.assertFalse(hasattr(comments_fetch, name), f"comments_fetch.{name}: a scheduled re-check is still there")
        for name in ("youtube_post", "facebook_post", "instagram_post", "FB"):  # social_twopass.py's readers stay
            self.assertTrue(hasattr(comments_fetch, name), name)

    def test_1_no_watch_comments(self):
        for name in ("run_comments", "COMMENT_AT_H", "COMMENT_PLATFORMS", "comments"):
            self.assertFalse(hasattr(watch_fetch, name), f"watch_fetch.{name}: X/TikTok/微博/B站 comments still read")
        src = open(os.path.join(HERE, "watch_fetch.py")).read()
        self.assertNotRegex(src, r"fetch_\w*comments", "watch_fetch.py still calls a comments endpoint")


class Facebook(unittest.TestCase):
    def test_2_post_ids_recorded_at_poll(self):
        got, _ = poll({"pg": [(NOW - 600, "71"), (NOW - 1200, "72")]})
        self.assertEqual(len(got["items"]), 2)
        for it in got["items"]:
            self.assertEqual(corpus.kv_get("fbpid:" + it["url"]), it["post_id"])

    def test_3_page_without_timeline_keeps_the_session(self):
        got, caps = poll({"busy": [(NOW - 600 * i, f"8{i}") for i in range(1, 5)]}, bare=["b1", "b2", "b3", "b4", "b5"])
        self.assertEqual(caps, 0, "pages without a public timeline renewed the session")
        self.assertTrue(got["state"]["busy"]["ok"])
        for b in ("b1", "b2", "b3", "b4", "b5"):
            self.assertFalse(got["state"][b]["ok"])

    def test_4_old_session_kept_while_answered(self):
        got, caps = poll({"busy": [(NOW - 600, "91")]}, age_h=30)
        self.assertEqual(caps, 0, "a working session was renewed for its age")
        self.assertEqual([i["post_id"] for i in got["items"]], ["91"])

    def test_5_refused_session_renewed_once(self):
        got, caps = poll({"busy": [(NOW - 600, "95")], "other": [(NOW - 900, "96")]}, refused=True)
        self.assertEqual(caps, 1)
        self.assertEqual(sorted(i["post_id"] for i in got["items"]), ["95", "96"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
