#!/usr/bin/env python3
"""Every post and comment of the followed Facebook pages and Instagram/Threads accounts (coverage audit, 2026-10-14),
on a throwaway database (TEST_DATABASE_URL), no network:

Facebook pages (fb_fetch.py)
1. Every page is checked on every run (no 60/120-minute tiers for quiet pages: they delayed a post up to 2 h).
2. A post that shows up after a newer one (its creation time is older than the newest post already read: a video
   finished processing, a scheduled post; live 14 Oct: 陳宗彝 12:16 and 14:01, 鄧銘心's 13 Oct reel) still comes in:
   each check reads back OVERLAP_H before the newest post, and a daily deep pass DEEP_H; a post id already read is
   never filed twice (pfbid permalinks change between reads: 30 duplicate rows on 13-14 Oct).
3. facebook.json keeps the posts of the last KEEP_H hours, so a poller that misses one run's file still gets them.
Comments (comments_fetch.py)
5. Facebook: a big thread is read to its end (the first read stopped at 30 pages = 300 comments and later checks stop
   at the newest known comment, so the rest never came: 唐生大地震 506 → 314), and when the post's total is above the
   rows held, a full pass reads every comment page and the replies of comments whose reply count grew.
6. Instagram/Threads: every comment: the logged-out page gives ~15, the rest through TikHub's paginated comments and replies (am730 163 → 14 before).
Instagram/Threads accounts (ig_fetch.py)
7. No account waits 3 hours (every_hours is gone); a busy account (3+ posts in an hour over the last 3 days) is read
   every BUSY_MIN minutes, the rest hourly.
8. A read whose 12 posts are all newer than the newest post known (more than 12 since the last read) pages back
   through TikHub's user posts until it meets a known post, so nothing between two reads is lost.
9. An account on Instagram and Threads under the same handle keeps each platform's posts and state apart, and
   instagram.json holds each post once: the same handle's posts were carried over by both accounts on every run and
   doubled (94,884 items for 2,676 posts, a 60 MB document whose every write took ~800 MB in Postgres and OOM-killed
   db-1, 15 Oct).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_social_coverage.py
"""
import json, os, sys, tempfile, time, unittest
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
import corpus, fb_fetch, comments_fetch, ig_fetch, watchlist  # noqa: E402

HKT = timezone(timedelta(hours=8))
TMP = tempfile.mkdtemp()
NOW = int(time.time())


# ---------------- Facebook timeline fake ----------------

class Resp:
    def __init__(self, body):
        self.content = body
        self.request_size, self.download_size, self.header_size = 5000, len(body), 300
        self.status_code = 200


class FakeFacebook:
    """Facebook's timeline request: pages[pid] = [(creation_time, post_id)], answered newest first, 3 a page,
    honouring afterTime/beforeTime/cursor; asked[pid] counts requests"""
    pages, asked = {}, {}

    def __init__(self, *a, **k):
        pass

    def post(self, url, data=None, **k):
        from urllib.parse import parse_qs
        v = json.loads(parse_qs(data)["variables"][0])
        pid = v["id"]
        FakeFacebook.asked[pid] = FakeFacebook.asked.get(pid, 0) + 1
        ps = sorted((p for p in FakeFacebook.pages.get(pid, []) if (v.get("afterTime") is None or p[0] >= v["afterTime"])
                     and (v.get("beforeTime") is None or p[0] <= v["beforeTime"])), reverse=True)
        start = int(v.get("cursor") or 0)
        chunk = ps[start:start + v.get("count", 3)]
        more = start + len(chunk) < len(ps)
        edges = [{"node": {"creation_time": t, "post_id": pid_,
                           "comet_sections": {"timestamp": {"story": {"url": f"https://www.facebook.com/x/posts/pfbid{pid_}{FakeFacebook.salt}"}},
                                              "content": {"message": {"text": f"post {pid_}"}}}}} for t, pid_ in chunk]
        body = {"data": {"node": {"timeline_list_feed_units": {"edges": edges,
                                                               "page_info": {"end_cursor": str(start + len(chunk)), "has_next_page": more}}}}}
        return Resp(json.dumps(body).encode())


FakeFacebook.salt = "a"


def fb_setup(pages):
    corpus.file_put("fb-session.json", {"post": "variables=" + json.dumps({"count": 3}), "headers": {}, "cookies": {}, "ua": "x",
                                        "captured": datetime.now(HKT).isoformat(timespec="seconds")})
    corpus.file_put("fb-ids.json", {f"https://www.facebook.com/{p}": p for p in pages})
    FakeFacebook.pages = pages


def fb_poll(names):
    with open(os.path.join(TMP, "fb_pages.json"), "w") as f:
        json.dump([{"group": "media", "name": n, "platform": "facebook", "url": f"https://www.facebook.com/{n}",
                    "why": "test", "since": "2026-10-12"} for n in names], f)
    with mock.patch.object(watchlist, "FILE", os.path.join(TMP, "fb_pages.json")), \
            mock.patch("curl_cffi.requests.Session", FakeFacebook), \
            mock.patch.object(fb_fetch, "capture_session", side_effect=AssertionError("no browser in tests")):
        fb_fetch.poll()
    return corpus.file_get("facebook.json")


class Facebook(unittest.TestCase):
    def setUp(self):
        corpus.file_put("facebook.json", {})
        FakeFacebook.asked = {}

    def test_1_every_page_every_run(self):
        fb_setup({"busy": [(NOW - 600 * i, f"1{i}") for i in range(1, 30)], "quiet": [(NOW - 5 * 86400, "20")]})
        fb_poll(["busy", "quiet"])
        before = FakeFacebook.asked.get("quiet", 0)
        fb_poll(["busy", "quiet"])
        self.assertGreater(FakeFacebook.asked.get("quiet", 0), before, "the quiet page was skipped on the second run")

    def test_2_late_post_and_no_duplicates(self):
        fb_setup({"pg": [(NOW - 7200, "31"), (NOW - 600, "32")]})
        got = fb_poll(["pg"])
        self.assertEqual(sorted(i["post_id"] for i in got["items"]), ["31", "32"])
        FakeFacebook.salt = "b"  # Facebook hands out another pfbid permalink for the same post
        FakeFacebook.pages["pg"].append((NOW - 3600, "33"))  # created before the newest post, visible only now
        st = corpus.file_get("facebook.json")
        st["state"]["pg"]["overlap"] = NOW - 3700  # the page's hourly overlap read is due
        corpus.file_put("facebook.json", st)
        got = fb_poll(["pg"])
        new = [i for i in got["items"] if i["url"].endswith("b")]
        self.assertEqual([i["post_id"] for i in new], ["33"], "a post already read was filed again, or the late one was missed")
        FakeFacebook.salt = "a"

    def test_3_deep_pass_daily(self):
        fb_setup({"pg2": [(NOW - 600, "41")]})
        fb_poll(["pg2"])
        FakeFacebook.pages["pg2"].append((NOW - 20 * 3600, "42"))  # 20 h older than the newest: past the overlap
        st = corpus.file_get("facebook.json")
        st["state"]["pg2"]["deep"] = "2000-01-01"  # the page's daily deep pass is due
        corpus.file_put("facebook.json", st)
        got = fb_poll(["pg2"])
        self.assertIn("42", [i["post_id"] for i in got["items"]])

    def test_4_items_kept_for_hours(self):
        fb_setup({"pg3": [(NOW - 600, "51")]})
        fb_poll(["pg3"])
        got = fb_poll(["pg3"])  # nothing new on the second run
        self.assertIn("51", [i["post_id"] for i in got["items"]], "the previous run's posts left the file at once")


# ---------------- comments ----------------

def post(url, age_h, sid="facebook"):
    return {"url": url, "title": "t", "sid": sid, "source": "s", "section": "Facebook", "published": None, "age_h": age_h}


def ago(h):
    return (datetime.now(HKT) - timedelta(hours=h)).isoformat(timespec="seconds")


class FakeFBComments:
    """CommentListComponentsRootQuery & co over a list of comments (newest first), 10 a page; replies by comment"""
    def __init__(self, comments, replies):
        self.comments, self.replies, self.q, self.bytes = comments, replies, comments_fetch.FB_QUERIES, 0

    def post_id(self, url):
        return "999"

    def node(self, start):
        chunk = self.comments[start:start + 10]
        edges = [{"node": {"legacy_fbid": cid, "body": {"text": f"c{cid}"}, "created_time": NOW - 60,
                           "feedback": {"id": f"f{cid}", "expansion_info": {"expansion_token": "t"},
                                        "replies_fields": {"total_count": len(self.replies.get(cid, []))}}}} for cid in chunk]
        total = len(self.comments) + sum(len(v) for v in self.replies.values())
        more = start + 10 < len(self.comments)
        return {"comment_rendering_instance_for_feed_location": {"comments": {
            "edges": edges, "total_count": total, "page_info": {"end_cursor": str(start + 10), "has_next_page": more}}}}

    def gql(self, which, v):
        if which == "root":
            return self.node(0)
        if which == "page":
            return self.node(int(v["commentsAfterCursor"]))
        cid = v["id"][1:]
        return {"replies_connection": {"edges": [{"node": {"legacy_fbid": r, "body": {"text": f"r{r}"}, "created_time": NOW - 30}}
                                                 for r in self.replies.get(cid, [])], "page_info": {"has_next_page": False}}}


def rows(url):
    return corpus.db().execute("SELECT count(*) n FROM posts WHERE thread=%s", (url,)).fetchone()["n"]


class FacebookComments(unittest.TestCase):
    def test_7_big_thread_read_to_its_end(self):
        url = "https://www.facebook.com/x/posts/big"
        fb = FakeFBComments([str(i) for i in range(1000, 0, -1)], {})
        comments_fetch.facebook_post(fb, post(url, 3))
        self.assertEqual(rows(url), 1000, "the first read stops at 300 comments")

    def test_8_full_pass_finds_old_replies(self):
        url = "https://www.facebook.com/x/posts/rep"
        fb = FakeFBComments([str(i) for i in range(2030, 2000, -1)], {})
        comments_fetch.facebook_post(fb, post(url, 3))
        fb.replies = {"2005": ["r1", "r2"]}  # replies to an old comment, no new comment
        comments_fetch.facebook_post(fb, post(url, 6))
        self.assertEqual(rows(url), 32)


class InstagramComments(unittest.TestCase):
    def test_9_every_comment_through_tikhub(self):
        url = "https://www.instagram.com/p/CODE123ab/"
        corpus.ingest([{"url": url, "title": "t", "sid": "instagram", "source": "am730 (Instagram)",
                        "published": ago(2), "section": "Instagram"}])
        page_html = '<script type="application/json">' + json.dumps({"xig_polaris_media": {"comment_count": 6, "comments_connection": {
            "edges": [{"node": {"pk": "1", "text": "a", "created_at": NOW - 100}}, {"node": {"pk": "2", "text": "b", "created_at": NOW - 90}}]}}}) + "</script>"

        post_url = url

        class R:
            status_code, url, text, content = 200, post_url, page_html, page_html.encode()

        class Http:
            def __init__(self, *a, **k):
                pass

            def get(self, *a, **k):
                return R()

        calls = []

        def th(path, params):
            calls.append(path)
            if path.endswith("fetch_post_comments"):
                if not params.get("pagination_token"):
                    return {"data": {"items": [{"id": "1", "text": "a", "created_at": NOW - 100, "child_comment_count": 0},
                                               {"id": "3", "text": "c", "created_at": NOW - 80, "child_comment_count": 1}]},
                            "pagination_token": "p2"}
                return {"data": {"items": [{"id": "4", "text": "d", "created_at": NOW - 70, "child_comment_count": 0},
                                           {"id": "5", "text": "e", "created_at": NOW - 60, "child_comment_count": 0}]}}
            return {"data": {"items": [{"id": "6", "text": "f", "created_at": NOW - 50}]}}
        p = dict(corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone())
        with mock.patch.object(comments_fetch, "tikhub", th, create=True):
            comments_fetch.instagram_post(Http(), p)
        self.assertEqual(rows(url), 6, f"TikHub calls: {calls}")


# ---------------- Instagram accounts ----------------

def ig_html(codes_times):
    edges = [{"node": {"code": c, "pk": str(((t * 1000 - ig_fetch.IG_EPOCH_MS) << 23)), "caption": {"text": c}}} for c, t in codes_times]
    return '<script type="application/json">' + json.dumps({"x": {"polaris_ordered_timeline_connection": {"edges": edges}}}) + "</script>"


class Instagram(unittest.TestCase):
    def test_10_no_three_hour_accounts(self):
        accounts = watchlist.accounts("instagram") + watchlist.accounts("threads")
        self.assertEqual([a["handle"] for a in accounts if "every_hours" in a], [])

    def test_11_busy_every_15_min(self):
        busy = [NOW - 600 * i for i in range(10)]  # 6 posts within an hour
        self.assertEqual(ig_fetch.every_minutes(busy, NOW), ig_fetch.BUSY_MIN)
        self.assertEqual(ig_fetch.every_minutes([NOW - 86400], NOW), 60)

    def test_12_gap_paged_through_tikhub(self):
        acct = [{"group": "kol", "name": "Busy", "platform": "instagram", "handle": "busyacct", "why": "test", "since": "2026-10-12"}]
        old = [(f"OLD{i:05d}", NOW - 7200 - 60 * i) for i in range(12)]
        new = [(f"NEW{i:05d}", NOW - 60 * i) for i in range(12)]
        mid = [(f"MID{i:05d}", NOW - 1200 - 60 * i) for i in range(5)]  # between the two reads, past the newest 12
        pages = [ig_html(old), ig_html(new)]

        class R:
            def __init__(self, html):
                self.text, self.url, self.status_code = html, "https://www.instagram.com/busyacct/", 200

        def fetch(http, url, hdr, cut):
            h = pages.pop(0)
            return R(h), h

        def th(path, params):
            items = [{"code": c, "taken_at": t} for c, t in sorted(new + mid + old, key=lambda x: -x[1])]
            i = int(params.get("pagination_token") or 0)
            return {"data": {"items": items[i:i + 12], "pagination_token": str(i + 12) if i + 12 < len(items) else None}}
        with open(os.path.join(TMP, "ig.json"), "w") as f:
            json.dump(acct, f)
        with mock.patch.object(ig_fetch, "fetch", fetch), mock.patch.object(watchlist, "FILE", os.path.join(TMP, "ig.json")), \
                mock.patch.object(ig_fetch, "tikhub", th, create=True), mock.patch("curl_cffi.requests.Session", lambda *a, **k: None):
            ig_fetch.main()
            st = corpus.file_get("instagram.json")
            st["accounts"]["busyacct"]["checked"] = ago(2)
            corpus.file_put("instagram.json", st)
            ig_fetch.main()
        codes = {i["url"].split("/p/")[1].strip("/") for i in corpus.file_get("instagram.json")["items"]}
        self.assertTrue({c for c, _ in mid} <= codes, "posts between two reads beyond the newest 12 were lost")

    def test_13_same_handle_on_instagram_and_threads_never_duplicates(self):
        acct = [{"group": "kol", "name": "Twin", "platform": "instagram", "handle": "twin", "why": "test", "since": "2026-10-12"},
                {"group": "kol", "name": "Twin", "platform": "threads", "handle": "twin", "why": "test", "since": "2026-10-12"}]

        def got(prefix, host):
            return [{"url": f"https://{host}/{prefix}{i}/", "ts": NOW - 60 * i, "text": f"{prefix}{i}",
                     "published": datetime.fromtimestamp(NOW - 60 * i, HKT).isoformat()} for i in range(5)]

        class R:
            text, url, status_code = "", "https://www.instagram.com/twin/", 200

        with open(os.path.join(TMP, "twin.json"), "w") as f:
            json.dump(acct, f)
        with mock.patch.object(ig_fetch, "fetch", lambda *a: (R(), "")), \
                mock.patch.object(ig_fetch, "posts", lambda html: got("IG", "www.instagram.com/p")), \
                mock.patch.object(ig_fetch, "threads_posts", lambda html, u: got("TH", "www.threads.net/@twin/post")), \
                mock.patch.object(watchlist, "FILE", os.path.join(TMP, "twin.json")), \
                mock.patch("curl_cffi.requests.Session", lambda *a, **k: None):
            corpus.file_put("instagram.json", {"accounts": {}, "items": []})
            for run in range(6):
                if run % 2:  # every other run both are due again; in between neither is (their posts carried over)
                    st = corpus.file_get("instagram.json")
                    for v in st["accounts"].values():
                        v["checked"] = ago(120)
                    corpus.file_put("instagram.json", st)
                ig_fetch.main()
        d = corpus.file_get("instagram.json")
        urls = [i["url"] for i in d["items"]]
        self.assertEqual(len(urls), len(set(urls)), "a post is held more than once")
        self.assertEqual(len(urls), 10)
        self.assertEqual(sum("threads.net" in u for u in urls), 5)
        self.assertEqual(len(d["accounts"]), 2, "Instagram and Threads state of one handle overwrite each other")


if __name__ == "__main__":
    unittest.main(verbosity=2)
