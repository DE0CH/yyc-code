#!/usr/bin/env python3
"""One catalogue site never breaks the next (browser_fetch.main): after a site fails, the next site gets a fresh tab.
2026-10-20/21: 伊莉's 403 (net::ERR_HTTP_RESPONSE_CODE_FAILURE) left the shared tab navigating to Chrome's error page,
and that navigation interrupted the next site's goto ("interrupted by another navigation to chrome-error://"): 開飯
after 伊莉討論區 and 中國旅遊 after 伊莉部落格 failed on every run though each reads fine on its own. No browser, no
network.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_browser_isolation.py
"""
import os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import browser_fetch  # noqa: E402


class Page:
    n = 0

    def __init__(self):
        Page.n += 1
        self.id, self.closed, self.poisoned = Page.n, False, False

    def close(self):
        self.closed = True


class Ctx:
    def __init__(self):
        self.pages = [Page()]

    def new_page(self):
        p = Page()
        self.pages.append(p)
        return p

    def route(self, *a, **k):
        pass

    def close(self):
        pass


class PW:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def chromium(self):
        return self

    def launch_persistent_context(self, *a, **k):
        return Ctx()


def failing(pg):
    pg.poisoned = True
    raise RuntimeError("Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE")


def reads_clean(pg):
    if pg.poisoned:
        raise RuntimeError("Navigation is interrupted by another navigation to chrome-error://chromewebdata/")
    return [{"title": "一篇食評", "url": "https://x/1"}]


class Isolation(unittest.TestCase):
    def test_a_failed_site_gets_the_next_one_a_fresh_tab(self):
        sites = [("a", "伊莉討論區", failing), ("b", "開飯", reads_clean), ("c", "中國旅遊", reads_clean)]
        saved = {}
        with mock.patch.object(browser_fetch, "catalogue_sites", return_value=sites), \
                mock.patch.object(browser_fetch, "sync_playwright", return_value=PW()), \
                mock.patch.object(browser_fetch.corpus, "file_put", side_effect=lambda k, v: saved.update({k: v})):
            browser_fetch.main(catalogue=True)
        r = saved[browser_fetch.CATALOGUE_DOC]["sites"]
        self.assertFalse(r["a"]["ok"])
        self.assertTrue(r["b"]["ok"], r["b"])
        self.assertTrue(r["c"]["ok"], r["c"])


if __name__ == "__main__":
    unittest.main()
