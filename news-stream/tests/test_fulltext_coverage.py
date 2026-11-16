#!/usr/bin/env python3
"""Every news post gets its article text (coverage audit, 2026-10-14). Measured on 13-14 Oct (posts of those days):

  - 明報 instant news (news.mingpao.com/ins/…): 175 posts with no text. Every article page answers Cloudflare's managed
    challenge ("Just a moment...", HTTP 403) to any plain client, Chrome fingerprint or residential IP alike; the 403
    was saved as "nothing on the page" and never retried. A challenge page answered with a 4xx is now a Challenge, so
    the browser pass reads it (like finance.mingpao.com).
  - Posts whose feed carried a short summary (RTHK's RSS, 明報's daily-paper RSS, 政府新聞公報/政府新聞網; 171 + 107 + 50
    posts) were never read by the 30-minute job: it took only posts with no text at all. It now takes every untried
    post whose text is under FULL_CHARS (fulltext_now's own bar).
  - 商業電台's AMP pages (/news/amp/…) gave "": the article data is on the plain page.
  - HKET's content API: the lead is the start of nonPaywall, so the text began twice.
  - 資本 Capital (Next.js): the page's HTML holds only the footer; the article is in the self.__next_f flight data.
  - Pages tried by an extractor that was fixed later stay empty: `fulltext_fetch.py retry HOST [DAYS]` makes a
    host's recent empty pages due again (ps.hket.com: 175 posts saved "" before its content API was read).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_fulltext_coverage.py
"""
import json, os, sys, tempfile, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import cache_fetch, corpus, fulltext_fetch, sources  # noqa: E402
# these fixtures' posts are of mid-October, before the restart cutoff; the cutoff's own rules: tests/test_cutoff_work.py
corpus.FLOOR = "2026-01-01T00:00:00+08:00"

CF_PAGE = ('<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head><body>'
           '<div class="main-content"><noscript>Enable JavaScript and cookies to continue</noscript></div>'
           '<script>window._cf_chl_opt={cvId: "3"}</script></body></html>')
ARTICLE = "<html><body>" + "".join(f"<p>這是第{i}段新聞內文，內容足夠長，可以作為文章的正文段落。</p>" for i in range(8)) + "</body></html>"


def add(url, text, published="2026-10-14T10:00:00+08:00", source_type="News", sid="rthk_local", seen=None):
    corpus.ingest([{"sid": sid, "source": "RTHK 香港電台", "title": "標題 " + url[-8:], "url": url,
                    "published": published, "seen": seen or published[:16] + ":30+08:00", "text": text}])
    pid = corpus.ids_for_urls([url])[url]
    # a live post (a source's first read, or one after a gap, is backlog: corpus.seen_cond)
    corpus.db().execute("UPDATE posts SET first_run = NULL, source_type=%s WHERE id=%s", (source_type, pid))
    return pid


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class Coverage(unittest.TestCase):
    def test_short_feed_text_is_read(self):
        short = add("https://news.rthk.hk/rthk/ch/component/k2/1873972-20261014.htm", "律政司司長今日表示，" * 5)
        full = add("https://news.rthk.hk/rthk/ch/component/k2/1873973-20261014.htm", "長" * 800)
        ids = [c[0] for c in fulltext_fetch.candidates(100)]
        self.assertIn(short, ids)      # 50 characters of RSS summary: the page has the story
        self.assertNotIn(full, ids)    # the feed carried the article

    def test_unreadable_hosts_never_crowd_out_readable_pages(self):
        """rows on hosts we never read (SKIP_HOSTS: Google News links, LinkedIn; Reuters/Bloomberg were ~700 a day
        until paywall.py read them) are left out in SQL, not after LIMIT"""
        for i in range(12):
            add(f"https://news.google.com/rss/articles/2026-10-20-x{i}", None, published=f"2026-10-20T1{i % 10}:00:00+08:00")
        real = add("https://news.rthk.hk/rthk/ch/component/k2/1870001-20261019.htm", None, published="2026-10-19T10:00:00+08:00")
        self.assertEqual(corpus.db().execute("SELECT day FROM posts WHERE id=%s", (real,)).fetchone()["day"], "2026-10-19")
        self.assertEqual([c[0] for c in fulltext_fetch.candidates(4)][:1], [real])

    def test_challenge_answered_with_403_goes_to_the_browser(self):
        def refused(url, **kw):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        with mock.patch.object(sources, "fetch", side_effect=refused), \
             mock.patch("curl_cffi.requests.get", return_value=Resp(403, CF_PAGE)), \
             mock.patch.object(cache_fetch, "pace"):
            with self.assertRaises(cache_fetch.Challenge):
                cache_fetch.fetch_one("https://news.mingpao.com/ins/%e6%b8%af%e8%81%9e/article/20261014/s00001/1791950522162/x")

    def test_one_site_one_queue(self):
        """HKET's subdomains (inews, news, topick, video, specialist…) share one rate limit: read in parallel, one
        thread each, they drew HTTP 429s (15 pages given up in the 14 Oct 15:22 UTC run)"""
        todo = [(1, "https://inews.hket.com/article/1/a", "x", "d"), (2, "https://video.hket.com/video/2", "x", "d"),
                (3, "https://topick.hket.com/article/3/c", "x", "d"), (4, "https://news.rthk.hk/x/4", "x", "d"),
                (5, "https://www.info.gov.hk/gia/general/1.htm", "x", "d"), (6, "https://www.news.gov.hk/tc/1", "x", "d")]
        by_site, _ = fulltext_fetch.plan(todo)
        self.assertEqual(sorted(len(v) for v in by_site.values()), [1, 1, 1, 3])
        self.assertEqual(cache_fetch.site("https://inews.hket.com/a"), "hket.com")
        self.assertEqual(cache_fetch.site("https://www.info.gov.hk/a"), "info.gov.hk")
        self.assertEqual(cache_fetch.site("https://news.mingpao.com/a"), "mingpao.com")

    def test_429_after_the_chrome_fingerprint_is_retried_later(self):
        def busy(url, **kw):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        with mock.patch.object(sources, "fetch", side_effect=busy), \
             mock.patch("curl_cffi.requests.get", return_value=Resp(429, "<html>slow down</html>")), \
             mock.patch.object(cache_fetch, "pace"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                cache_fetch.fetch_one("https://inews.hket.com/article/4208669/x")
        self.assertEqual(e.exception.code, 429)
        self.assertIsInstance(e.exception, fulltext_fetch.TRANSIENT)

    def test_mingpao_instant_refusals_go_to_the_browser(self):
        """明報's instant news answers per section: 港聞 200, 經濟 Cloudflare's 403 challenge, 娛樂 an IIS "400 Bad
        Request - Invalid URL" to every plain client (live, 15 Oct); a real browser reads all of them"""
        def bad(url, **kw):
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
        with mock.patch.object(sources, "fetch", side_effect=bad), \
             mock.patch("curl_cffi.requests.get", return_value=Resp(400, "<h2>Bad Request - Invalid URL</h2>")), \
             mock.patch.object(cache_fetch, "pace"):
            with self.assertRaises(cache_fetch.Challenge):
                cache_fetch.fetch_one("https://news.mingpao.com/ins/%e5%a8%9b%e6%a8%82/article/20261014/s00007/1791990602893/x")
            with self.assertRaises(urllib.error.HTTPError):  # its daily paper (pns) is not a browser page
                cache_fetch.fetch_one("https://news.mingpao.com/pns/%e8%a6%81%e8%81%9e/article/20261014/s00001/1/x")

    def test_plain_403_is_still_an_error(self):
        def refused(url, **kw):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        with mock.patch.object(sources, "fetch", side_effect=refused), \
             mock.patch("curl_cffi.requests.get", return_value=Resp(403, "<html><title>403 Forbidden</title></html>")), \
             mock.patch.object(cache_fetch, "pace"):
            with self.assertRaises(urllib.error.HTTPError) as e:
                cache_fetch.fetch_one("https://www.inmediahk.net/node/x")
        self.assertEqual(e.exception.code, 403)

    def test_881903_amp_page_reads_the_plain_page(self):
        asked = []
        page = 'x VueApp.main({"article": {"content": "<p>' + "蔡若蓮表示會聽取學界意見。" * 20 + '</p>"}}) y'

        def get(url, **kw):
            asked.append(url)
            return page if "/amp/" not in url else "<html>amp</html>"
        with mock.patch.object(cache_fetch, "get", side_effect=get):
            text, _, _ = cache_fetch.fetch_one("https://www.881903.com/news/amp/local/2654713")
        self.assertEqual(asked, ["https://www.881903.com/news/local/2654713"])
        self.assertIn("蔡若蓮", text)

    def test_hket_lead_not_repeated(self):
        lead = "三亞—太古地產宣布，公司旗下首個度假式零售項目正式命名"
        d = {"id": "4209175", "displayDate": "17:39 2026/10/14",
             "content": {"lead": lead, "nonPaywall": f"<p>{lead}「三亞中免太古里」。</p><p>第二段。</p>"}}
        with mock.patch.object(cache_fetch, "get", return_value=json.dumps(d)):
            text, _, _ = cache_fetch.fetch_one("https://ps.hket.com/article/4209175/x")
        self.assertEqual(text.count("三亞—太古地產宣布"), 1, text)

    def test_nextjs_flight_data(self):
        body = "<p>" + "富途證券宣佈推出全新品牌宣傳企劃，適逢公司成立十四週年。" * 6 + "</p><p>第二段內文。</p>"
        chunk = json.dumps("5:[\"$\",\"div\",null,{\"dangerouslySetInnerHTML\":{\"__html\":\"" + body.replace('"', '\\"') + "\"}}]")
        page = ('<html><body><div>© 2025 CAPITAL PUBLISHING LIMITED</div>'
                f'<script>self.__next_f.push([1,{chunk}])</script></body></html>')
        with mock.patch.object(cache_fetch, "get", return_value=page):
            text, _, _ = cache_fetch.fetch_one("https://www.capital-hk.com/5070758xg707582")
        self.assertIn("富途證券宣佈", text)
        self.assertNotIn("CAPITAL PUBLISHING", text)

    def test_retry_makes_a_hosts_empty_pages_due_again(self):
        empty = add("https://ps.hket.com/article/4209167/x", None, sid="hket_sitemap")
        good = add("https://ps.hket.com/article/4209168/y", None, sid="hket_sitemap")
        other = add("https://news.example.hk/a/1", None, sid="x")
        corpus.set_text(empty, "")
        corpus.set_text(good, "內文" * 400)
        corpus.set_text(other, "")
        n = fulltext_fetch.retry("ps.hket.com", days=10000)
        self.assertEqual(n, 1)
        ids = [c[0] for c in fulltext_fetch.candidates(100)]
        self.assertIn(empty, ids)
        self.assertNotIn(good, ids)
        self.assertNotIn(other, ids)


    def test_browser_pass_opens_each_page_in_a_new_tab_and_closes_it(self):
        """one tab over 25 Cloudflare pages grew one renderer to 494 MB and the job to 1.1 GB (fulltext run of
        14 Oct 15:22 UTC; two runs before it were OOMKilled at 1500Mi): each page gets its own tab, closed after"""
        tabs = []

        class Tab:
            def __init__(self):
                self.closed = False
                tabs.append(self)

            def goto(self, url, **kw):
                pass

            def content(self):
                return ARTICLE

            def close(self):
                self.closed = True

        class Ctx:
            pages = []

            def route(self, *a, **k):  # nomedia.block
                pass

            def new_page(self):
                return Tab()

            def close(self):
                pass

        class PW:
            def __init__(self):
                self.chromium = self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def launch_persistent_context(self, *a, **kw):
                return Ctx()

        urls = [f"https://www.yzzk.com/article/{i}" for i in range(5)]
        with mock.patch("patchright.sync_api.sync_playwright", PW), mock.patch("browser_fetch.cleared", return_value=True), \
             mock.patch("shutil.copytree"), mock.patch("shutil.rmtree"), mock.patch("time.sleep"), \
             mock.patch.object(cache_fetch, "pace"):
            got = cache_fetch.browser_pass(urls, profile="t")
        self.assertEqual(len(got), 5)
        self.assertTrue(all(got[u] for u in urls))
        self.assertGreaterEqual(len(tabs), 5)
        self.assertTrue(all(t.closed for t in tabs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
