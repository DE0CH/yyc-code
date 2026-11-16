#!/usr/bin/env python3
"""The `fulltext` CronJob finishes inside its activeDeadlineSeconds (1740 s) whatever its backlog holds (no network,
no database: a simulated clock, a fake Chrome, the database calls mocked).

2026-10-14: every run failed with DeadlineExceeded for 6.6 h. The page reads stopped at DEADLINE, but the passes after
them had no time limit: 120 invest.hket.com pages rendered in Chrome at up to 45 s each (none ever rendered: the page
never reaches networkidle) and a browser pass of 40 pages at up to 180 s each. Those pages were never saved, so every
run started over on them.

    python3 tests/test_fulltext.py
"""
import os, sys, threading, unittest, urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cache_fetch, corpus, fulltext_fetch, sources  # noqa: E402

JOB_DEADLINE = 1740  # infra: the CronJob's activeDeadlineSeconds
HKET = ('{"id": "4209080", "displayDate": "17:39 2026/10/14", "content": {"lead": "", '
        '"nonPaywall": "<p>北水全日淨流出1.35億元。</p>"}}')
ARTICLE = "<html><body>" + "".join(f"<p>這是第{i}段新聞內文，內容足夠長，可以作為文章的正文段落。</p>" for i in range(8)) + "</body></html>"


class Clock:
    """a simulated monotonic clock: slow calls advance it instead of taking real time"""
    def __init__(self):
        self.t, self.lock = 1000.0, threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def advance(self, s):
        with self.lock:
            self.t += s


class FakePage:
    """Chrome that is as slow as the cluster's worst case: an invest.hket.com goto times out after its full timeout;
    a Cloudflare page takes 60 s to load and 75 s to clear its challenge"""
    def __init__(self, clock, visited, renders=False):
        self.clock, self.visited, self.url, self.renders = clock, visited, "", renders

    def goto(self, url, wait_until=None, timeout=30000):
        self.visited.append(url)
        self.url = url
        if "invest.hket.com" in url:
            self.clock.advance(15 if self.renders else timeout / 1000)
            if not self.renders:
                raise TimeoutError("Page.goto: Timeout exceeded")
            return
        self.clock.advance(60)

    def content(self):
        return ARTICLE

    def title(self):
        return "新聞"

    def close(self):
        pass


class FakeChrome:
    launches = 0

    def __init__(self, clock, visited, renders=False):
        self.page = FakePage(clock, visited, renders)
        self.chromium = self
        self.pages = [self.page]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def launch(self, **kw):
        return self

    def launch_persistent_context(self, *a, **kw):
        FakeChrome.launches += 1
        return self

    def route(self, *a, **k):  # nomedia.block
        pass

    def new_page(self):
        return self.page

    def close(self):
        pass


def fake_fetch(clock):
    def fetch(url, data=None, headers=None, proxy=None, timeout=30):
        clock.advance(0.5)
        if url.startswith("https://data02.hket.com/content"):
            return HKET
        if "singpao.com.hk" in url or "yzzk.com" in url:  # Cloudflare answers 403 before the challenge page
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        return ARTICLE
    return fetch


def simulate(todo, renders=False, saved=None):
    """one run of fulltext_fetch.run() over these candidates → (seconds it took, {pid: saved text}, pages Chrome opened)"""
    clock, visited, kv = Clock(), [], {}
    saved = {} if saved is None else saved

    def cleared(pg):
        clock.advance(75)
        return True

    def curl_refused(*a, **kw):
        clock.advance(0.5)
        return mock.MagicMock(status_code=403, text="")

    with mock.patch("time.monotonic", clock.now), \
         mock.patch("time.sleep", clock.advance), \
         mock.patch.object(fulltext_fetch, "candidates", return_value=todo), \
         mock.patch.object(fulltext_fetch, "reread", return_value=0), \
         mock.patch.object(fulltext_fetch, "save", side_effect=lambda pid, text, *a, **k: saved.__setitem__(pid, text)), \
         mock.patch.object(fulltext_fetch, "tries", return_value=1), \
         mock.patch.object(corpus, "kv_get", side_effect=lambda k, d=None: kv.get(k, d)), \
         mock.patch.object(corpus, "kv_set", side_effect=lambda k, v: kv.__setitem__(k, v)), \
         mock.patch.object(sources, "fetch", side_effect=fake_fetch(clock)), \
         mock.patch("curl_cffi.requests.get", side_effect=curl_refused), \
         mock.patch("patchright.sync_api.sync_playwright", lambda: FakeChrome(clock, visited, renders)), \
         mock.patch("browser_fetch.cleared", cleared), \
         mock.patch("shutil.copytree"), mock.patch("shutil.rmtree"), \
         mock.patch("builtins.print"), mock.patch.object(cache_fetch, "_last", [0.0]):
        start = clock.now()
        fulltext_fetch.run()
        return clock.now() - start, saved, visited


def backlog(n_invest=120, n_browser=120, n_plain=50):
    todo, pid = [], 0
    for i in range(n_invest):
        pid += 1
        todo.append((pid, f"https://invest.hket.com/article/{4209000 + i}/標題{i}", "hket_invest", "2026-10-14"))
    for i in range(n_browser):
        pid += 1
        h = "www.singpao.com.hk" if i % 2 else "www.yzzk.com"
        todo.append((pid, f"https://{h}/article/{i}", "x", "2026-10-14"))
    for i in range(n_plain):
        pid += 1
        todo.append((pid, f"https://news.example{i % 5}.hk/a/{i}", "x", "2026-10-14"))
    return todo


class Fulltext(unittest.TestCase):
    def test_run_finishes_before_the_job_deadline(self):
        took, saved, _ = simulate(backlog())
        self.assertLess(took, JOB_DEADLINE, f"the run took {took:.0f} s of simulated time")

    def test_browser_pages_not_reached_wait_for_the_next_run(self):
        todo = backlog(n_invest=0, n_browser=120, n_plain=0)
        took, saved, visited = simulate(todo)
        self.assertLess(took, JOB_DEADLINE)
        self.assertTrue(visited, "the browser pass read nothing")
        for pid, url, _, _ in todo:
            if url not in visited:
                self.assertNotIn(pid, saved, "a page the browser never opened was marked as tried")
        for pid, url, _, _ in todo:
            if url in visited:
                self.assertTrue(saved.get(pid), "a page the browser read lost its text")

    def test_hket_js_pages_use_the_content_api_not_chrome(self):
        todo = backlog(n_invest=120, n_browser=0, n_plain=0)
        took, saved, visited = simulate(todo)
        self.assertEqual(visited, [], "invest.hket.com went to Chrome")
        self.assertEqual(len(saved), 120)
        self.assertTrue(all("北水全日淨流出" in t for t in saved.values()))

    def test_browser_pass_restarts_chrome_and_saves_per_batch(self):
        """one headed Chrome kept open over 150 pages was OOMKilled at the pod's 1500Mi (2026-10-14, nothing saved):
        the pass runs in batches of BROWSER_BATCH pages, each a fresh Chrome, and saves after each batch"""
        todo = backlog(n_invest=0, n_browser=60, n_plain=0)
        FakeChrome.launches = 0
        saves_at_launch = []
        real = cache_fetch.browser_pass

        def counting(urls, *a, **kw):
            self.assertLessEqual(len(urls), fulltext_fetch.BROWSER_BATCH)
            saves_at_launch.append(len(saved_now))
            return real(urls, *a, **kw)
        saved_now = {}
        with mock.patch.object(fulltext_fetch, "RUN_BUDGET", 10 ** 6), \
             mock.patch.object(cache_fetch, "browser_pass", side_effect=counting):
            took, saved, visited = simulate(todo, saved=saved_now)
        self.assertGreater(FakeChrome.launches, 1)
        self.assertEqual(saves_at_launch[1], fulltext_fetch.BROWSER_BATCH, "the first batch was not saved before the second")


if __name__ == "__main__":
    unittest.main()
