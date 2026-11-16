#!/usr/bin/env python3
"""YYC AI's web_fetch from YYC's own capture (webfetch.py; Deyao, 2026-10-27):

1. an article the corpus holds with its text: kind cache, the stored text and the post's details page;
2. a site a source of ours reads: kind capture, read by the news stream's capture (cache_fetch.fetch_one), kept, and the
   next fetch of the URL is a cache hit without reading the page again;
3. none (the caller fetches as before): an unknown site, a forum post (only its own job reads it), a site that opens
   only in a real browser, a capture with no article text;
4. the route's token: unset = off, a wrong one refused.
On a throwaway database (TEST_DATABASE_URL), no network.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_webfetch.py
"""
import os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, corpus, follow, webfetch  # noqa: E402

corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read
TEXT = "香港政府今日公布新措施，" * 40
ARTICLE = "https://news.rthk.hk/rthk/ch/component/k2/1830001-20261027.htm"


class WebFetch(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM posts")
        webfetch._ensure()
        corpus.db().execute("DELETE FROM web_fetch_pages")

    def test_corpus_copy(self):
        corpus.ingest([{"sid": "rthk", "source": "香港電台", "title": "新措施", "url": ARTICLE, "text": TEXT}])
        with mock.patch.object(cache_fetch, "fetch_one", side_effect=AssertionError("no page read")):
            r = webfetch.web_fetch(ARTICLE)
        self.assertEqual(r["kind"], "cache")
        self.assertEqual(r["text"], TEXT)
        self.assertTrue(r["dashboard_url"].endswith(f"/item/{r['post_id']}"))

    def test_capture_then_cache(self):
        url = "https://news.rthk.hk/rthk/ch/component/k2/1830002-20261027.htm"
        with mock.patch.object(follow, "outlet_for", return_value=("rthk", "香港電台")), \
             mock.patch.object(follow, "readable", return_value=None), \
             mock.patch.object(cache_fetch, "fetch_one", return_value=(TEXT, "記者", {})) as f:
            r = webfetch.web_fetch(url)
            self.assertEqual((r["kind"], r["source"], r["text"]), ("capture", "香港電台", TEXT))
            again = webfetch.web_fetch(url)
        self.assertEqual(again["kind"], "cache")
        self.assertEqual(f.call_count, 1)  # read once

    def test_none(self):
        with mock.patch.object(follow, "outlet_for", return_value=None):
            self.assertEqual(webfetch.web_fetch("https://unknown.example/a")["kind"], "none")
        with mock.patch.object(follow, "outlet_for", return_value=("lihkg", "LIHKG")), \
             mock.patch.object(follow, "readable", return_value="Forum posts: read only by their own job"):
            self.assertIn("own job", webfetch.web_fetch("https://lihkg.com/thread/1")["why"])
        with mock.patch.object(follow, "outlet_for", return_value=("singpao", "成報")), \
             mock.patch.object(follow, "readable", return_value=None), \
             mock.patch.object(cache_fetch, "fetch_one", side_effect=cache_fetch.Challenge("cf")):
            self.assertIn("browser", webfetch.web_fetch("https://www.singpao.com.hk/x")["why"])
        with mock.patch.object(follow, "outlet_for", return_value=("rthk", "香港電台")), \
             mock.patch.object(follow, "readable", return_value=None), \
             mock.patch.object(cache_fetch, "fetch_one", return_value=("", None, {})):
            self.assertEqual(webfetch.web_fetch("https://news.rthk.hk/x")["kind"], "none")
        with self.assertRaises(ValueError):
            webfetch.web_fetch("ftp://x")

    def test_token(self):
        with mock.patch.dict(os.environ, {"WEB_FETCH_TOKEN": ""}):
            self.assertFalse(webfetch.token_ok("Bearer "))
        with mock.patch.dict(os.environ, {"WEB_FETCH_TOKEN": "s3cret"}):
            self.assertTrue(webfetch.token_ok("Bearer s3cret"))
            self.assertFalse(webfetch.token_ok("Bearer nope"))
            self.assertFalse(webfetch.token_ok(None))


if __name__ == "__main__":
    unittest.main()
