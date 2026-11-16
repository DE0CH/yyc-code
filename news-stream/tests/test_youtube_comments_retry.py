#!/usr/bin/env python3
"""YouTube's comment reads (comments_fetch.yt_next, youtubei/v1/next from the cluster's IP) answer HTTP 403 now and then
in the job's burst of ~3,700 posts with 4 threads (114 of 3,704 on 20 Oct 2026, 02:15 UTC); the same videos read
moments later from another pod answered. A 403 or 429 is tried again after a pause (YT_RETRY_WAIT, at most
YT_RETRIES times) before it counts as an error. No network.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_youtube_comments_retry.py
"""
import io, json, os, sys, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import comments_fetch  # noqa: E402


class R(io.BytesIO):
    headers = {}


def answers(*codes):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        code = codes[len(calls) - 1] if len(calls) <= len(codes) else 200
        if code != 200:
            raise urllib.error.HTTPError(req.full_url, code, "Forbidden", {}, None)
        return R(json.dumps({"ok": True}).encode())
    return urlopen, calls


class Retry(unittest.TestCase):
    def test_403_then_ok(self):
        urlopen, calls = answers(403, 403)
        with mock.patch.object(comments_fetch.urllib.request, "urlopen", urlopen), mock.patch.object(comments_fetch.time, "sleep", lambda s: None):
            d, n = comments_fetch.yt_next({"videoId": "x"})
        self.assertEqual(d, {"ok": True})
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_the_retries(self):
        urlopen, calls = answers(*([403] * 10))
        with mock.patch.object(comments_fetch.urllib.request, "urlopen", urlopen), mock.patch.object(comments_fetch.time, "sleep", lambda s: None):
            with self.assertRaises(urllib.error.HTTPError):
                comments_fetch.yt_next({"videoId": "x"})
        self.assertEqual(len(calls), comments_fetch.YT_RETRIES + 1)

    def test_other_errors_not_retried(self):
        urlopen, calls = answers(404)
        with mock.patch.object(comments_fetch.urllib.request, "urlopen", urlopen), mock.patch.object(comments_fetch.time, "sleep", lambda s: None):
            with self.assertRaises(urllib.error.HTTPError):
                comments_fetch.yt_next({"videoId": "x"})
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
