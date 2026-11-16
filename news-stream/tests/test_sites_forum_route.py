#!/usr/bin/env python3
"""A catalogue site read through a forum's client (sites.json "forum": a key of forum_threads.json): its pages take the
forum's route and checks (forum_fetch.Client.get: the ISP IP with the HK fallback, eyny's proof of work and 18+ notice),
one client per forum per run. 伊莉's listings were "browser" sites: Chrome on the cluster's IP gets 403
(net::ERR_HTTP_RESPONSE_CODE_FAILURE, every run since they were added, 2026-10-20), while forum_fetch reads the same
site's threads. No network.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_sites_forum_route.py
"""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.chdir(HERE)
import forum_fetch, sites_fetch  # noqa: E402

PAGE = '<html><body><a href="tid-123-1.html">一個討論 thread title</a><a href="tid-124-1.html">另一個討論 title</a></body></html>'


class ForumRoute(unittest.TestCase):
    def test_eyny_listings_use_the_forum_client(self):
        sites = {s["id"]: s for s in sites_fetch.load()}
        for sid in ("hk_www05_eyny", "hk_blog_eyny"):
            self.assertEqual(sites[sid].get("forum"), "eyny", sid)
            self.assertFalse(sites[sid].get("browser"), sid)

    def test_unknown_forum_is_refused(self):
        with self.assertRaises(ValueError):
            sites_fetch.check({"id": "x", "name": "x", "url": "https://x/", "kind": "listing", "href": "a",
                               "type": "Forum", "region": "HK", "forum": "nosuchforum"})

    def test_pages_go_through_the_forum_clients_get(self):
        sites_fetch._forum_clients.clear()
        site = {s["id"]: s for s in sites_fetch.load()}["hk_www05_eyny"]
        with mock.patch.object(forum_fetch.Client, "get", autospec=True, return_value=PAGE) as get:
            rows = sites_fetch.read(site)
            sites_fetch.read(site)
        self.assertEqual(get.call_count, 2)
        self.assertIs(get.call_args_list[0][0][0], get.call_args_list[1][0][0], "one client per forum per run")
        self.assertEqual(get.call_args_list[0][0][0].f["key"], "eyny")
        self.assertEqual({r["url"].rsplit("/", 1)[-1] for r in rows}, {"tid-123-1.html", "tid-124-1.html"})


if __name__ == "__main__":
    unittest.main()
