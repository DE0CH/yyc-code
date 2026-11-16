#!/usr/bin/env python3
"""成報 (browser_fetch.singpao) keeps what it read when one section page times out (2026-10-15).

    python3 tests/test_browser_singpao.py

Live 2026-10-15: the browser job reads 成報's home page and its 9 section pages; from 16:20 HKT one section's
Page.goto ran past 45 s on most runs and the whole site came back with 0 articles ("成報: 0 articles", monitor:
failing since 16:20) although the home page and the other sections had loaded. Now a section page that fails is
skipped (the next run tries it again); the site fails only when nothing at all could be read. And 成報 has a time
budget (SINGPAO_BUDGET): with every section page timing out, 9 × 45 s ran past the job's 300 s deadline (17:14 HKT)
and the job was killed before writing any site; now each page's timeout is capped to what is left and the remaining
pages wait for the next run.
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("DATABASE_URL", "postgresql:///nonexistent")
import browser_fetch  # noqa: E402

CLOCK = [0.0]
browser_fetch.time.monotonic = lambda: CLOCK[0]


class FakePage:
    """成報 with a link per page; pages in `slow` time out on goto"""

    def __init__(self, slow=()):
        self.slow, self.url, self.gotos = set(slow), None, 0

    def goto(self, url, wait_until=None, timeout=None):
        self.gotos += 1
        if any(url.endswith(s) for s in self.slow):
            CLOCK[0] += timeout / 1000
            raise TimeoutError(f"Page.goto: Timeout {timeout}ms exceeded. navigating to \"{url}\"")
        self.url = url

    def title(self):
        return "成報 - 香港"

    def wait_for_load_state(self, *a, **k):
        pass

    def eval_on_selector_all(self, selector, js):
        n = self.url.rsplit("news", 1)[-1] if "fi=news" in self.url else "0"
        return [[f"{browser_fetch.SINGPAO}index.php?fi=news{n or 1}&id={1000 + int(n or 0)}", f"成報新聞第{n}頁標題"]]


class Singpao(unittest.TestCase):
    def test_slow_section_skipped(self):
        got = browser_fetch.singpao(FakePage(slow=["fi=news8"]))
        ids = sorted(int(it["url"].rsplit("=", 1)[1]) for it in got)
        self.assertEqual(ids, [1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1009])  # every page but news8

    def test_budget_stops_reading(self):
        """every section page times out: the home page's links come back well within the job's deadline"""
        CLOCK[0] = 0.0
        pg = FakePage(slow=[f"fi=news{n}" for n in range(1, 10)])
        got = browser_fetch.singpao(pg)
        self.assertEqual(len(got), 1)  # the home page's
        self.assertLessEqual(CLOCK[0], browser_fetch.SINGPAO_BUDGET)
        self.assertLess(pg.gotos, 10)

    def test_nothing_read_fails(self):
        with self.assertRaises(Exception):
            browser_fetch.singpao(FakePage(slow=[browser_fetch.SINGPAO] + [f"fi=news{n}" for n in range(1, 10)]))


if __name__ == "__main__":
    unittest.main()
