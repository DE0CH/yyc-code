#!/usr/bin/env python3
"""亞洲週刊 / 明報月刊 (browser_fetch.details_site): a load that shows no article links is loaded once more, and a site
that still shows none says what the page was (2026-10-16).

    python3 tests/test_browser_details_retry.py

Live 2026-10-16: the browser job read 0 articles from 亞洲週刊 on the 06:44 and 08:14 UTC runs and fine on the runs
between ("no articles (challenge not passed?)"); the job had finished in 72 s of its 300 s, so one more load fit.
A diagnostic job from the cluster read the page 7 times out of 7 (95 links each). The error now names the page's title
and URL, so the next miss shows whether it was Cloudflare's challenge or the site.
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("DATABASE_URL", "postgresql:///nonexistent")
import browser_fetch  # noqa: E402

CLOCK = [0.0]
browser_fetch.time.monotonic = lambda: CLOCK[0]
browser_fetch.time.sleep = lambda s: CLOCK.__setitem__(0, CLOCK[0] + s)
LINK = ["https://www.yzzk.com/article/details/" + "封面專題/2026-42/1791900000000/標題", "亞洲週刊封面專題標題"]


class FakePage:
    """the first `empty` loads show no article links"""

    def __init__(self, empty, title="亞洲週刊"):
        self.empty, self.loads, self.url, self._title = empty, 0, None, title

    def goto(self, url, wait_until=None, timeout=None):
        self.loads += 1
        self.url = url

    def title(self):
        return self._title

    def wait_for_load_state(self, *a, **k):
        pass

    def locator(self, selector):  # cleared(): no challenge checkbox to tick
        return type("L", (), {"count": lambda self: 0})()

    def eval_on_selector_all(self, selector, js):
        return [] if self.loads <= self.empty else [LINK]


class Details(unittest.TestCase):
    def setUp(self):
        CLOCK[0] = 0.0
        browser_fetch.START = 0.0

    def test_second_load_reads(self):
        pg = FakePage(empty=1)
        got = browser_fetch.details_site("https://www.yzzk.com/")(pg)
        self.assertEqual(pg.loads, 2)
        self.assertEqual([it["title"] for it in got], ["亞洲週刊封面專題標題"])

    def test_first_load_enough(self):
        pg = FakePage(empty=0)
        self.assertEqual(len(browser_fetch.details_site("https://www.yzzk.com/")(pg)), 1)
        self.assertEqual(pg.loads, 1)

    def test_still_empty_names_the_page(self):
        pg = FakePage(empty=9, title="Just a moment...")
        with self.assertRaises(RuntimeError) as c:
            browser_fetch.details_site("https://www.yzzk.com/")(pg)
        self.assertEqual(pg.loads, 2)
        self.assertIn("Just a moment...", str(c.exception))
        self.assertIn("https://www.yzzk.com/", str(c.exception))

    def test_no_second_load_near_the_deadline(self):
        """the job has a 300 s deadline: past RETRY_BEFORE seconds into the job, no second load"""
        CLOCK[0] = browser_fetch.RETRY_BEFORE + 1
        pg = FakePage(empty=1)
        with self.assertRaises(RuntimeError):
            browser_fetch.details_site("https://www.yzzk.com/")(pg)
        self.assertEqual(pg.loads, 1)


if __name__ == "__main__":
    unittest.main()
