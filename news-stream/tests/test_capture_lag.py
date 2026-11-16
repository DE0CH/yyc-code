#!/usr/bin/env python3
"""Capture lag of the followed Facebook pages and Instagram/Threads accounts (measured live 2026-10-15), on a throwaway
database (TEST_DATABASE_URL), no network:

1. A page or account read for the first time brings its older posts (Facebook reads back FIRST_HOURS, Instagram's
   profile shows its 12 newest, pinned ones included): that is a backlog, not new posts. Those posts are marked
   first_run like a new source's first read (scored for 數據分析, never a stream item or a Telegram post), and their
   首次收錄 is not a capture delay. Live: the watchlist expansion of 15 Oct 05:00 HKT filed 3,051 such posts (1,965
   Facebook, 1,333 Instagram) as new, with first-seen 15 h to 130 days after their publish time; 6 became stream items.
   A post published after the page was first read is a new post as before.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_capture_lag.py
"""
import json, os, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.setdefault("RESI_PROXY", "http://u:p@127.0.0.1:9")
os.environ.setdefault("TIKHUB_API", "test")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, fb_fetch, ig_fetch, sources, watchlist  # noqa: E402
from test_social_coverage import FakeFacebook, fb_setup, ig_html  # noqa: E402

HKT = timezone(timedelta(hours=8))
TMP = tempfile.mkdtemp()
NOW = int(time.time())


def first_run(url):
    r = corpus.db().execute("SELECT first_run FROM posts WHERE url=%s", (url,)).fetchone()
    return None if r is None else bool(r["first_run"])


def fb_poll(names):
    with open(os.path.join(TMP, "fb_pages.json"), "w") as f:
        json.dump([{"group": "media", "name": n, "platform": "facebook", "url": f"https://www.facebook.com/{n}",
                    "why": "test", "since": "2026-10-12"} for n in names], f)
    with mock.patch.object(watchlist, "FILE", os.path.join(TMP, "fb_pages.json")), \
            mock.patch("curl_cffi.requests.Session", FakeFacebook), \
            mock.patch.object(fb_fetch, "capture_session", side_effect=AssertionError("no browser in tests")):
        fb_fetch.poll()
    items, _ = sources.facebook_file()
    corpus.ingest(items)
    return corpus.file_get("facebook.json")


class Backlog(unittest.TestCase):
    def test_1_facebook_new_page_backlog_marked(self):
        corpus.file_put("facebook.json", {})
        FakeFacebook.asked = {}
        # the corpus already holds a facebook post, so the sid is not new: only the page is
        corpus.ingest([{"sid": "facebook", "source": "Old page", "section": "Facebook", "title": "old", "published": None,
                        "url": "https://www.facebook.com/old/posts/1"}])
        fb_setup({"newpage": [(NOW - 20 * 3600, "51"), (NOW - 3 * 3600, "52")]})
        got = fb_poll(["newpage"])
        urls = {i["post_id"]: i["url"] for i in got["items"]}
        self.assertEqual(sorted(urls), ["51", "52"])
        for pid in ("51", "52"):
            self.assertTrue(first_run(urls[pid]), f"post {pid} from the page's first read was filed as new")
        FakeFacebook.pages["newpage"].append((NOW + 60, "53"))  # published after the page was first read
        st = corpus.file_get("facebook.json")  # the page's next check is due (fb_fetch.every_minutes)
        for v in st["state"].values():
            v["checked"] = (datetime.now(HKT) - timedelta(hours=4)).isoformat(timespec="seconds")
        corpus.file_put("facebook.json", st)
        got = fb_poll(["newpage"])
        url53 = next(i["url"] for i in got["items"] if i["post_id"] == "53")
        self.assertIs(first_run(url53), False, "a post published after the first read is a new post")

    def test_2_instagram_new_account_backlog_marked(self):
        acct = [{"group": "kol", "name": "Fresh", "platform": "instagram", "handle": "freshacct", "why": "test", "since": "2026-10-12"}]
        corpus.ingest([{"sid": "instagram", "source": "Other (Instagram)", "section": "Instagram", "title": "x", "published": None,
                        "url": "https://www.instagram.com/p/OTHER00001/"}])
        old = [("PINNED0001", NOW - 130 * 86400), ("OLDPOST001", NOW - 20 * 3600)]
        later = [("NEWPOST001", NOW + 60)] + old
        pages = [ig_html(old), ig_html(later)]

        class R:
            def __init__(self, html):
                self.text, self.url, self.status_code = html, "https://www.instagram.com/freshacct/", 200

        def fetch(http, url, hdr, cut):
            h = pages.pop(0)
            return R(h), h
        with open(os.path.join(TMP, "ig.json"), "w") as f:
            json.dump(acct, f)
        corpus.file_put("instagram.json", {"accounts": {}, "items": []})
        with mock.patch.object(ig_fetch, "fetch", fetch), mock.patch.object(watchlist, "FILE", os.path.join(TMP, "ig.json")), \
                mock.patch("curl_cffi.requests.Session", lambda *a, **k: None):
            ig_fetch.main()
            corpus.ingest(sources.instagram_file()[0])
            for c in ("PINNED0001", "OLDPOST001"):
                self.assertTrue(first_run(f"https://www.instagram.com/p/{c}/"), f"{c} from the account's first read was filed as new")
            st = corpus.file_get("instagram.json")
            st["accounts"]["freshacct"]["checked"] = (datetime.now(HKT) - timedelta(hours=2)).isoformat(timespec="seconds")
            corpus.file_put("instagram.json", st)
            ig_fetch.main()
            corpus.ingest(sources.instagram_file()[0])
        self.assertIs(first_run("https://www.instagram.com/p/NEWPOST001/"), False, "a post after the first read is a new post")

    def test_3_known_account_posts_stay_new(self):
        """an account read before this marking existed (state without "since") is not treated as new"""
        corpus.file_put("facebook.json", {})
        fb_setup({"oldpage": [(NOW - 600, "61")]})
        corpus.file_put("facebook.json", {"state": {"oldpage": {"newest_ts": NOW - 3600, "checked": "2026-10-15T00:00:00+08:00",
                                                                 "deep": datetime.now(HKT).strftime("%Y-%m-%d"),
                                                                 "overlap": NOW, "ids": {}, "recent": []}}, "items": []})
        got = fb_poll(["oldpage"])
        url = next(i["url"] for i in got["items"] if i["post_id"] == "61")
        self.assertIs(first_run(url), False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
