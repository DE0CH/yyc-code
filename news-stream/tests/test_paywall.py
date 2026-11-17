#!/usr/bin/env python3
"""Full text from Reuters, Bloomberg and paywalled outlets (paywall.py; Deyao, 2026-10-15: "we should be reading all
texts including reuters bloomberg and FT", "pay captcha solvers … add the costs in", "for bloomberg we can just see
what happens as a proof of concept, otherwise don't break their terms … they usually send us an excerpt, and also
we can try to get the archived page from archive.is"). On a throwaway database (TEST_DATABASE_URL; its tables are
dropped and made again), every network call stubbed (the page shapes are the live ones of 15 Oct 2026, cut down).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_paywall.py

1. Reuters, Bloomberg, FT and Mingtiandi are no longer hosts we never read (cache_fetch.SKIP_HOSTS), so the
   full-text readers take them and the scorer waits for their text like any page.
2. Reuters: its DataDome interstitial (HTTP 401, var dd={…}) is solved by CapSolver through the yycstream proxy (RESI_PROXY) on
   one sticky session, the cookie is kept in kv and reused for the next page (no second solve); the text comes from
   Reuters' content API; the solve's charge (CapSolver's list price, USD 0.0025) lands in solver_spend. An IP ban
   ("t":"bv") takes a new proxy session, not a solve.
3. Bloomberg: the story from the page's own data (__NEXT_DATA__ story.body); its robot check keeps the feed's
   summary as an excerpt.
4. Paywalled outlets: an FT/Nikkei page's public part is kept as an excerpt; a page that declares itself paywalled
   but gives the whole story (SCMP) is not an excerpt; HKET's paid articles keep the free part only (never the
   API's paid text) as an excerpt.
5. The archive job: an excerpt post takes the newest archive.today snapshot's text (text_via 'archive' + the
   snapshot URL) when longer; with no snapshot the page is submitted once and looked up again later, given up after
   ARCHIVE_LOOKS; archive.today's captcha is solved once (2Captcha) and the run carries on; a mirror that doesn't
   answer a connection is skipped for the next (archive.is, …). With ISP_PROXY lookups and submissions go out on YYC's
   ISP IP (archive.today answers no datacenter IP since ~8 Nov 2026), each request under the ISP lock with no
   kept-alive tunnel; without it lookups go out from the cluster's IP, submissions through the residential proxy
   (archive.today captures nothing a datacenter IP submits). The snapshot extractor
   reads the snapshot's <div style=…> paragraphs.
6. Where the text came from is saved (fulltext_fetch.save: page / feed / excerpt) and shown on the item page
   (/api/item text_via, text_via_label, text_via_url).
7. Costs and stops: the monitor's spending has a solver series from solver_spend; a solver refusing for its balance
   is a safety stop (kv solver_limit, one Discord message, the monitor's stops panel), cleared by the next solve.
"""
import json, os, re, sys, unittest, urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
for k in ("CAPSOLVER_API", "TWOCAPTCHA_API", "DISCORD_WEBHOOK", "OPENROUTER_API", "TIKHUB_API",
          "ANYAPI_API_KEY", "IPROYAL_API", "HETZNER_YYC_READ", "RESI_PROXY"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import cache_fetch, corpus, fulltext_fetch, item, monitor, notify, paywall  # noqa: E402
# these fixtures' posts are of mid-October, before the restart cutoff; the cutoff's own rules: tests/test_cutoff_work.py
corpus.FLOOR = "2026-01-01T00:00:00+08:00"

RURL = "https://www.reuters.com/world/asia-pacific/imf-urges-china-deploy-bigger-fiscal-stimulus-2026-10-15/"
DD_PAGE = ("<html><body><script>var dd={'rt':'i','cid':'AHrlqAAAAAMA','hsh':'2013457ADA','b':1005349,'s':46743,"
           "'e':'c228','qp':'','host':'geo.captcha-delivery.com','cookie':'ciOY~YB'}</script>"
           '<script src="https://ct.captcha-delivery.com/i.js"></script></body></html>')
DD_BANNED = DD_PAGE.replace("'rt':'i'", "'rt':'c','t':'bv'")
R_JSON = json.dumps({"statusCode": 200, "result": {
    "published_time": "2026-10-15T03:47:00Z", "content_elements": [
        {"type": "paragraph", "content": "BEIJING, Oct 15 (Reuters) - The IMF urged China to deploy a <b>bigger</b> fiscal stimulus."},
        {"type": "header", "content": "Property"},
        {"type": "paragraph", "content": "The fund said the property slump was weighing on demand &amp; prices."}]}})
BBG = ('<html><head></head><body><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"story": {
    "publishedAt": "2026-10-15T02:00:00.000Z", "contentAccess": {"isMetered": True},
    "body": {"type": "document", "content": [
        {"type": "inline-newsletter", "content": []},
        {"type": "paragraph", "content": [{"type": "text", "value": "Japan's Takaichi named a new farm minister "},
                                          {"type": "link", "content": [{"type": "text", "value": "after a scandal"}]},
                                          {"type": "text", "value": " hit her cabinet."}]},
        {"type": "ad", "content": []},
        {"type": "heading", "content": [{"type": "text", "value": "Rice prices"}]},
        {"type": "list", "content": [{"type": "list-item", "content": [{"type": "text", "value": "Prices rose 3%."}]}]},
        {"type": "paragraph", "content": [{"type": "text", "value": "Takebe said transparency was needed."}]}]}}}}})
       + "</script></body></html>")
BBG_ROBOT = "<html><head><title>Bloomberg - Are you a robot?</title></head><body><div id='px-captcha'></div></body></html>"
FT_PAGE = ('<html><head><script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>'
           '</head><body><article><p>Hong Kong’s stock exchange is relaxing its listing standards in a bid to remain '
           'competitive with US and Chinese rivals.</p></article><div class="paywall">Subscribe to read</div></body></html>')
SCMP_LONG = ('<html><head><script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":"False"}</script>'
             '</head><body><article>' + "".join(f"<p>Paragraph {i} of a full SCMP story about the Legislative Council "
                                                f"meeting and the housing bill debate.</p>" for i in range(20))
             + "</article></body></html>")
SNAP = ('<html><body><div id="HEADER">archive.today webpage capture</div><div id="CONTENT"><div><div>'
        + "".join(f'<div style="font-size:18px;margin-block-end:18px;">Paragraph {i} of the archived FT story on the '
                  f'Hong Kong exchange relaxing its listing rules for weighted voting rights issuers.</div>' for i in range(12))
        + '<div style="x">Share this now</div></div></div><div><div style="y">Most read</div><div style="y">Another '
          'headline in the sidebar that is not the story</div></div></div></body></html>')
CAPTCHA_PAGE = ("<html><body><div id=\"g-recaptcha\"></div><script>var widgetId = grecaptcha.render('g-recaptcha', "
                "{'sitekey': '6LeQbtsSAAAAAHevV56qhVr_0JhQI7N-zTPoOoWJ'});</script></body></html>")


def add(url, text=None, sid="reuters", source_type="News", day="2026-10-15"):
    corpus.ingest([{"sid": sid, "source": sid, "title": "Title " + url[-20:], "url": url, "text": text,
                    "published": day + "T10:00:00+08:00", "seen": day + "T10:01:00+08:00"}])
    pid = corpus.ids_for_urls([url])[url]
    # a live post (a source's first read, or one after a gap, is backlog: corpus.seen_cond)
    corpus.db().execute("UPDATE posts SET first_run = NULL, source_type=%s, day=%s WHERE id=%s", (source_type, day, pid))
    return pid


class Resp:
    def __init__(self, status, text, url=None):
        self.status_code, self.text, self.url = status, text, url

    def json(self):
        return json.loads(self.text)


class Cookies(dict):
    def set(self, k, v, domain=None):
        self[k] = v

    def get(self, k, default=None, domain=None):
        return dict.get(self, k, default)


class FakeSession:
    """curl_cffi's Session: answers by URL from a script; records the proxy each session was made with"""
    made = []

    def __init__(self, script, proxy):
        self.script, self.proxy, self.cookies, self.headers = script, proxy, Cookies(), []
        FakeSession.made.append(self)

    def get(self, url, headers=None, timeout=None):
        self.headers.append(headers or {})
        return self.script(self, url)

    def close(self):
        pass


class Held:
    """sites_fetch.isp_lock's stand-in: records whether a block holds it"""
    def __init__(self):
        self.on, self.times = False, 0

    def __call__(self, wait=None):
        held = self

        class L:
            def __enter__(self):
                assert not held.on, "the ISP lock taken twice"
                held.on, held.times = True, held.times + 1

            def __exit__(self, *a):
                held.on = False
        return L()


SITEMAP = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
           '<url><loc>https://www.reuters.com/world/china/china-exports-2026-11-17/</loc><news:news><news:publication_date>'
           '2026-11-17T05:00:00Z</news:publication_date><news:title>China exports rise</news:title></news:news></url>'
           '</urlset>')


class Reuters(unittest.TestCase):
    def setUp(self):
        corpus.kv_set("paywall:datadome", None)
        corpus.db().execute("DELETE FROM solver_spend")
        corpus.kv_set("solver_limit", None)
        FakeSession.made = []
        self.env = mock.patch.dict(os.environ, {"RESI_PROXY": "http://yycstream:pw123@geo.iproyal.com:12321",
                                                "CAPSOLVER_API": "k"})
        self.env.start()
        self.balance = [5.0]
        self.tasks = []

    def tearDown(self):
        self.env.stop()

    def capsolver(self, url, body):
        if url.endswith("/getBalance"):
            return {"errorId": 0, "balance": self.balance[0]}
        if url.endswith("/createTask"):
            self.tasks.append(body["task"])
            return {"errorId": 0, "taskId": "t1"}
        self.balance[0] = round(self.balance[0] - 0.00125, 6)
        return {"errorId": 0, "status": "ready", "solution": {"cookie": "datadome=SOLVED123; Max-Age=31536000; Domain=.reuters.com"}}

    def run_fetch(self, script, url=RURL):
        meta = {}
        with mock.patch.object(paywall, "_session", lambda proxy=None, imp="chrome": FakeSession(script, proxy)), \
                mock.patch.object(paywall, "_post", self.capsolver), mock.patch.object(paywall.time, "sleep", lambda s: None):
            text, _, _ = cache_fetch.fetch_one(url, meta=meta)
        return text, meta

    def test_not_skipped_any_more(self):
        for h in ("reuters.com", "bloomberg.com", "ft.com", "mingtiandi.com"):
            self.assertFalse(("www." + h).endswith(cache_fetch.SKIP_HOSTS), h)
        self.assertTrue(add("https://www.reuters.com/world/x-2026-10-15/") in [c[0] for c in fulltext_fetch.candidates(50)])

    def test_solve_once_then_reuse(self):
        def script(s, url):
            self.assertIn("/pf/api/v3/content/fetch/article-by-id-or-url-v1", url)
            return Resp(200, R_JSON) if s.cookies.get("datadome") == "SOLVED123" else Resp(401, DD_PAGE)
        text, meta = self.run_fetch(script)
        self.assertEqual(text.split("\n"), ["BEIJING, Oct 15 (Reuters) - The IMF urged China to deploy a bigger fiscal stimulus.",
                                            "Property", "The fund said the property slump was weighing on demand & prices."])
        self.assertEqual(meta["via"], "solver")
        self.assertEqual(meta["published"], "2026-10-15T03:47:00Z")
        self.assertEqual(len(self.tasks), 1)
        t = self.tasks[0]
        self.assertEqual(t["type"], "DatadomeSliderTask")
        self.assertTrue(t["captchaUrl"].startswith("https://geo.captcha-delivery.com/interstitial/?initialCid=AHrlqAAAAAMA"))
        self.assertIn("referer=" + urllib.parse.quote(RURL, safe=""), t["captchaUrl"])
        self.assertEqual(t["userAgent"], paywall.DD_UA)
        # the solve and the reads go out on the same sticky session of the yycstream sub-user
        sid = corpus.kv_get("paywall:datadome")["sid"]
        self.assertEqual(t["proxy"], f"http:geo.iproyal.com:12321:yycstream:pw123_country-gb_session-{sid}_lifetime-24h")
        self.assertTrue(all(f"_session-{sid}_" in s.proxy for s in FakeSession.made))
        spent = corpus.db().execute("SELECT provider, purpose, usd FROM solver_spend").fetchall()
        self.assertEqual([(r["provider"], r["purpose"], r["usd"]) for r in spent], [("capsolver", "reuters", 0.0025)])
        # the next page reuses the cookie: no second solve
        self.run_fetch(script, RURL.replace("imf", "eu"))
        self.assertEqual(len(self.tasks), 1)

    def test_banned_ip_takes_a_new_session(self):
        def script(s, url):
            first = FakeSession.made.index(s) == 0
            if first:
                return Resp(401, DD_BANNED)
            return Resp(200, R_JSON) if s.cookies.get("datadome") == "SOLVED123" else Resp(401, DD_PAGE)
        text, meta = self.run_fetch(script)
        self.assertTrue(text.startswith("BEIJING"))
        self.assertEqual(len(self.tasks), 1)  # the ban was not sent to the solver
        self.assertNotEqual(FakeSession.made[0].proxy, FakeSession.made[1].proxy)

    def test_reuters_stays_on_yycstream_with_an_isp_ip(self):
        """17 Nov 2026: CapSolver can't load DataDome's captcha through the ISP IP (one connection at a time) and the
        cluster's IP gets Akamai's 403: with ISP_PROXY set the solve and the reads still go out on yycstream (Deyao)"""
        def script(s, url):
            return Resp(200, R_JSON) if s.cookies.get("datadome") == "SOLVED123" else Resp(401, DD_PAGE)
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://u:p@isp.test:12323"}), \
                mock.patch.object(paywall, "_session", lambda proxy=None, imp="chrome": FakeSession(script, proxy)), \
                mock.patch.object(paywall, "_post", self.capsolver), mock.patch.object(paywall.time, "sleep", lambda s: None):
            self.assertTrue(paywall.reuters(RURL).startswith("BEIJING"))
        self.assertTrue(all("geo.iproyal.com" in fs.proxy for fs in FakeSession.made))
        self.assertTrue(self.tasks[0]["proxy"].startswith("http:geo.iproyal.com:12321:yycstream:"))

    def test_the_source_reads_its_sitemaps_through_the_solved_session(self):
        """the poller's Reuters source: DataDome answers its sitemaps 401 since 30 Oct 2026; every page (the news
        sitemap, its second page, the full sitemap) is read with the solved cookie"""
        import sources, sources_extra
        asked = []

        def script(s, url):
            asked.append(url)
            return Resp(200, SITEMAP) if s.cookies.get("datadome") == "SOLVED123" else Resp(401, DD_PAGE)
        spec = next(x for x in sources_extra.ENGLISH if x[0] == "reuters")
        with mock.patch.object(paywall, "_session", lambda proxy=None, imp="chrome": FakeSession(script, proxy)), \
                mock.patch.object(paywall, "_post", self.capsolver), mock.patch.object(paywall.time, "sleep", lambda s: None), \
                mock.patch.object(sources, "fetch", lambda *a, **k: self.fail("a plain read of a Reuters sitemap")):
            rows = sources.fetch_source(spec)
        self.assertIn("https://www.reuters.com/world/china/china-exports-2026-11-17/", [r["url"] for r in rows])
        self.assertEqual(len(self.tasks), 1)
        self.assertTrue(any("from=50" in u for u in asked) and any(u.startswith(sources_extra.REUTERS_FULL) for u in asked))
        self.assertFalse(sources.escalates(spec), "never retried over yycstream by the poller")

    def test_zero_balance_is_a_safety_stop(self):
        sent = []

        def broke(url, body):
            if url.endswith("/getBalance"):
                return {"errorId": 0, "balance": 0}
            return {"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE", "errorDescription": "Insufficient balance"}
        with mock.patch.object(paywall, "_session", lambda proxy=None, imp="chrome": FakeSession(lambda s, u: Resp(401, DD_PAGE), proxy)), \
                mock.patch.object(paywall, "_post", broke), mock.patch.object(notify, "deyao", lambda t: sent.append(t) or True):
            for _ in range(2):
                with self.assertRaises(paywall.Blocked):
                    paywall.reuters(RURL)
        lim = corpus.kv_get("solver_limit")["capsolver"]
        self.assertIn("ERROR_ZERO_BALANCE", lim["message"])
        self.assertEqual(len(sent), 1)  # one message per episode
        self.assertEqual(self.tasks, [])  # (no solve went through)
        self.assertEqual([s["id"] for s in monitor.stops()["providers"]], ["capsolver"])
        paywall.limit_clear("capsolver")
        self.assertEqual(monitor.stops()["providers"], [])

    def test_slug_titled_reuters_posts_go_through_the_reader(self):
        """Reuters is a slug-titled source (its full sitemap's stories are titled from the URL): its posts are read
        through paywall.reuters too (not a plain page read, which gets DataDome's wall), the title from the API"""
        import fulltext_now
        pid = add("https://www.reuters.com/world/india/jio-ipo-2026-10-15/", None)

        def fake(url, meta=None):
            meta.update(via="solver", title="Jio sets IPO price band")
            return "Shares of India's Reliance rose 2.8% on Thursday. " * 20
        with mock.patch.object(paywall, "reuters", fake), \
                mock.patch.object(cache_fetch, "get", side_effect=AssertionError("plain page read")):
            fulltext_now.fetch(pid, "https://www.reuters.com/world/india/jio-ipo-2026-10-15/", "reuters")
        r = corpus.db().execute("SELECT title, text_via, length(text) AS n FROM posts WHERE id=%s", (pid,)).fetchone()
        self.assertEqual((r["title"], r["text_via"]), ("Jio sets IPO price band", "solver"))
        self.assertGreater(r["n"], 500)

    def test_api_challenge_is_json(self):
        """to the content API (Accept: application/json) DataDome answers {"url": …}, not the HTML page"""
        u = "https://geo.captcha-delivery.com/interstitial/?initialCid=AHrl&cid=FIn&referer=x&hash=2013&b=1&s=4"
        self.assertEqual(paywall.dd_challenge(json.dumps({"url": u}), RURL), (u, False))
        self.assertEqual(paywall.dd_challenge(json.dumps({"url": u + "&t=bv"}), RURL)[1], True)
        self.assertIsNone(paywall.dd_challenge('{"statusCode": 404}', RURL))

    def test_blocked_is_retried_later(self):
        self.assertTrue(issubclass(paywall.Blocked, fulltext_fetch.TRANSIENT))


class Bloomberg(unittest.TestCase):
    def test_story_from_page_data(self):
        meta = {}
        with mock.patch.object(cache_fetch, "get", lambda url, **k: BBG):
            text, _, _ = cache_fetch.fetch_one("https://www.bloomberg.com/news/articles/2026-10-15/japan-s-takaichi", meta=meta)
        self.assertEqual(text.split("\n"), ["Japan's Takaichi named a new farm minister after a scandal hit her cabinet.",
                                            "Rice prices", "Prices rose 3%.", "Takebe said transparency was needed."])
        self.assertEqual(meta["via"], "page_data")
        self.assertEqual(meta["published"], "2026-10-15T02:00:00.000Z")

    def test_robot_check_then_a_residential_exit(self):
        """the cluster's IP met the robot check (HTTP 403): the page once more from a residential exit (yycstream)"""
        import urllib.error
        def refused(url, **k):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        tried = []

        def sess(proxy=None, imp="chrome"):
            tried.append(proxy)
            return FakeSession(lambda s, u: Resp(403, BBG_ROBOT) if "_country-us_" in proxy else Resp(200, BBG), proxy)
        meta = {}
        with mock.patch.dict(os.environ, {"RESI_PROXY": "http://yycstream:pw123@geo.iproyal.com:12321"}), \
                mock.patch.object(cache_fetch, "get", refused), mock.patch.object(paywall, "_session", sess):
            text, _, _ = cache_fetch.fetch_one("https://www.bloomberg.com/news/articles/2026-10-15/japan-s-takaichi", meta=meta)
        self.assertTrue(text.startswith("Japan's Takaichi"))
        self.assertEqual(meta["via"], "page_data")
        self.assertEqual([re.search(r"_country-(\w+)_", p).group(1) for p in tried], ["us", "hk"])

    def test_robot_check_keeps_the_feed_summary_as_excerpt(self):
        url = "https://www.bloomberg.com/news/articles/2026-10-15/robot"
        pid = add(url, "Japan's prime minister named a new farm minister.", sid="bloomberg")
        meta = {}
        with mock.patch.object(cache_fetch, "get", lambda url, **k: BBG_ROBOT):
            text, _, _ = cache_fetch.fetch_one(url, meta=meta)
        self.assertEqual((text, meta["via"]), ("", "excerpt"))
        fulltext_fetch.save(pid, text, via=meta["via"])
        r = corpus.db().execute("SELECT text, text_via, cached_at FROM posts WHERE id=%s", (pid,)).fetchone()
        self.assertEqual((r["text"], r["text_via"]), ("Japan's prime minister named a new farm minister.", "excerpt"))
        self.assertIsNotNone(r["cached_at"])


class Excerpts(unittest.TestCase):
    def fetch(self, url, page):
        meta = {}
        with mock.patch.object(cache_fetch, "get", lambda u, **k: page):
            text, _, _ = cache_fetch.fetch_one(url, meta=meta)
        return text, meta.get("via")

    def test_ft_is_an_excerpt(self):
        text, via = self.fetch("https://www.ft.com/content/886d5a1b-f195-4966-9cc3-364ee09a1343", FT_PAGE)
        self.assertTrue(text.startswith("Hong Kong’s stock exchange"))
        self.assertEqual(via, "excerpt")

    def test_declared_paywall_with_whole_story_is_not(self):
        text, via = self.fetch("https://www.scmp.com/news/hong-kong/politics/article/3370999/legco", SCMP_LONG)
        self.assertGreater(len(text), paywall.EXCERPT_CHARS)
        self.assertIsNone(via)
        short = SCMP_LONG.replace("".join(f"<p>Paragraph {i} of a full SCMP story about the Legislative Council "
                                          f"meeting and the housing bill debate.</p>" for i in range(2, 20)), "")
        self.assertEqual(self.fetch("https://www.scmp.com/news/hong-kong/article/3370998/x", short)[1], "excerpt")

    def test_hket_paid_keeps_the_free_part(self):
        api = json.dumps({"displayDate": "17:39 2026/10/14", "paid": True, "content": {
            "lead": "", "nonPaywall": "<p>發展局下午公佈今季賣地計劃。</p>", "paywall": "<p>付費內容</p>",
            "full": "<p>發展局下午公佈今季賣地計劃。</p><p>付費內容</p>"}})
        text, via = self.fetch("https://ps.hket.com/article/4209476/x", api)
        self.assertEqual(text, "發展局下午公佈今季賣地計劃。")  # never the paid part
        self.assertEqual(via, "excerpt")

    def test_save_records_where_the_text_came_from(self):
        a = add("https://news.rthk.hk/a-2026-10-15", "短摘要", sid="rthk_local")
        fulltext_fetch.save(a, "完整的新聞內文" * 30, via="page")
        b = add("https://news.rthk.hk/b-2026-10-15", "饋送的全文" * 50, sid="rthk_local")
        fulltext_fetch.save(b, "短", via="page")
        got = {r["id"]: r["text_via"] for r in corpus.db().execute("SELECT id, text_via FROM posts WHERE id = ANY(%s)", ([a, b],))}
        self.assertEqual(got, {a: "page", b: "feed"})


class Archive(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM archive_scan")
        corpus.db().execute("UPDATE posts SET text_via=NULL WHERE text_via='excerpt'")
        corpus.db().execute("DELETE FROM solver_spend")

    def excerpt_post(self, url, text="Hong Kong’s stock exchange is relaxing its listing standards."):
        pid = add(url, text, sid="ft_hk", day=paywall.datetime.now(paywall.HKT).strftime("%Y-%m-%d"))
        corpus.set_text(pid, text, via="excerpt")
        return pid

    def test_mark_earlier_reads(self):
        """posts read before this reader on a paywalled outlet, with a short text, become excerpts for the job"""
        today = paywall.datetime.now(paywall.HKT).strftime("%Y-%m-%d")
        hkej = add("https://www1.hkej.com/dailynews/headline/article/4539712/x", None, sid="hkej_daily", day=today)
        corpus.set_text(hkej, "美CPI升3.7%憂加息" * 5)
        full = add("https://www.rthk.hk/x-full", None, sid="rthk_local", day=today)
        corpus.set_text(full, "短")
        self.assertGreaterEqual(paywall.mark_excerpts(), 1)
        got = {r["id"]: r["text_via"] for r in corpus.db().execute("SELECT id, text_via FROM posts WHERE id = ANY(%s)", ([hkej, full],))}
        self.assertEqual(got, {hkej: "excerpt", full: None})
        # an FT page whose read failed kept its feed summary (text_via 'feed', 15 Oct): an excerpt too
        ft = add("https://www.ft.com/content/feedonly", "FT summary", sid="ft_hk", day=today)
        fulltext_fetch.save(ft, "", via="page")
        self.assertEqual(corpus.db().execute("SELECT text_via FROM posts WHERE id=%s", (ft,)).fetchone()[0], "feed")
        paywall.mark_excerpts()
        self.assertEqual(corpus.db().execute("SELECT text_via FROM posts WHERE id=%s", (ft,)).fetchone()[0], "excerpt")

    def test_failed_read_of_a_paywalled_page_is_an_excerpt(self):
        import urllib.error
        pid = add("https://www.ft.com/content/failed-read", "FT summary", sid="ft_hk",
                  day=paywall.datetime.now(paywall.HKT).strftime("%Y-%m-%d"))
        corpus.db().execute("UPDATE posts SET cached_at=NULL WHERE id=%s", (pid,))
        def refused(url, **k):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        with mock.patch.object(cache_fetch, "get", refused):
            fulltext_fetch.run()
        self.assertEqual(corpus.db().execute("SELECT text_via FROM posts WHERE id=%s", (pid,)).fetchone()[0], "excerpt")

    def test_snapshot_text(self):
        t = paywall.archive_text(SNAP)
        self.assertEqual(len(t.split("\n")), 12)
        self.assertTrue(t.startswith("Paragraph 0 of the archived FT story"))
        self.assertNotIn("Share this", t)
        self.assertNotIn("sidebar", t)

    def test_snapshot_of_a_menu_is_no_text(self):
        """信報's snapshots of a paywalled page (2026-10-20): only the site menu (短 links 「即時新聞」「港股直擊」…) and a
        teaser under 40 characters a line; the menu won and 38 posts kept 640 characters of it as 'archive' text"""
        menu = "".join(f'<div style="x">{w}</div>' for w in ("即時新聞", "港股直擊", "香港財經", "中國財經", "國際財經", "地產新聞",
                                                              "時事脈搏", "分析評論", "信報時評", "地產評論", "今日信報", "政壇脈搏"))
        snap = f'<html><body><div id="CONTENT"><div>{menu}</div><div><div style="y">加菲貓漫畫　信報財經新聞</div></div></div></body></html>'
        self.assertEqual(paywall.archive_text(snap), "")

    def test_found_submitted_and_given_up(self):
        found = self.excerpt_post("https://www.ft.com/content/aaa")
        missing = self.excerpt_post("https://www.ft.com/content/bbb")
        calls = []

        def get(url, s=None, route="direct"):
            calls.append(url)
            self.assertEqual(route, "resi" if "/submit/" in url else "direct")  # submissions from a residential IP
            if url == "/newest/https://www.ft.com/content/aaa":
                return Resp(200, SNAP, "http://archive.ph/20261015033802/https://www.ft.com/content/aaa"), s
            if "/newest/" in url:
                return Resp(404, "<html>No results</html>", url), s
            return Resp(200, "<html>Loading</html>", url), s
        with mock.patch.object(paywall, "archive_get", get), mock.patch.object(paywall.time, "sleep", lambda s: None):
            stats = paywall.archive_run()
        self.assertEqual(stats, {"archived": 1, "submitted": 1})
        r = corpus.db().execute("SELECT text, text_via, text_via_url FROM posts WHERE id=%s", (found,)).fetchone()
        self.assertEqual(r["text_via"], "archive")
        self.assertEqual(r["text_via_url"], "https://archive.ph/20261015033802/https://www.ft.com/content/aaa")
        self.assertTrue(r["text"].startswith("Paragraph 0"))
        self.assertTrue(any(u.startswith("/submit/?url=https%3A%2F%2Fwww.ft.com%2Fcontent%2Fbbb") for u in calls))
        # the item page says where the text came from, with the snapshot's link
        v = item.view(found)
        self.assertEqual((v["text_via"], v["text_via_label"], v["text_via_url"]),
                         ("archive", "archive.today 存檔", "https://archive.ph/20261015033802/https://www.ft.com/content/aaa"))
        self.assertIn("內文來源", open(os.path.join(HERE, "web-app", "src", "routes", "-ItemPage.tsx")).read())
        # the missing one is looked up again on later runs (not submitted twice), then given up
        calls.clear()
        with mock.patch.object(paywall, "archive_get", get), mock.patch.object(paywall.time, "sleep", lambda s: None):
            for _ in range(paywall.ARCHIVE_LOOKS):
                paywall.archive_run()
        self.assertFalse(any("/submit/" in u for u in calls))
        self.assertEqual(sum("/newest/" in u for u in calls), paywall.ARCHIVE_LOOKS - 1)
        st = corpus.db().execute("SELECT status, looks FROM archive_scan WHERE post_id=%s", (missing,)).fetchone()
        self.assertEqual((st["status"], st["looks"]), ("none", paywall.ARCHIVE_LOOKS))

    def test_submissions_per_run_are_capped(self):
        for i in range(3):
            self.excerpt_post(f"https://www.ft.com/content/cap{i}")

        def get(url, s=None, route="direct"):
            return (Resp(404, "<html>No results</html>", url) if "/newest/" in url else Resp(200, "Loading", url)), s
        with mock.patch.object(paywall, "archive_get", get), mock.patch.object(paywall, "SUBMIT_MAX", 2), \
                mock.patch.object(paywall.time, "sleep", lambda s: None):
            self.assertEqual(paywall.archive_run(), {"submitted": 2, "submit_next_run": 1})
            self.assertEqual(paywall.archive_run(), {"waiting": 2, "submitted": 1})

    def test_a_mirror_that_does_not_answer_is_skipped(self):
        self.excerpt_post("https://www.ft.com/content/mir")
        seen = []

        class S:
            cookies = Cookies()

            def get(self, url, timeout=None):
                seen.append(url.split("/")[2])
                if url.startswith("https://archive.ph/"):
                    raise TimeoutError("curl: (28) Connection timed out after 15000 milliseconds")
                return Resp(200, SNAP, "https://archive.is/2026/https://www.ft.com/content/mir")
        with mock.patch.object(paywall, "_session", lambda *a, **k: S()), mock.patch.object(paywall.time, "sleep", lambda s: None):
            self.assertEqual(paywall.archive_run(), {"archived": 1})
        self.assertEqual(seen, ["archive.ph", "archive.is"])

    def test_isp_route(self):
        """with ISP_PROXY: lookups and submissions on the ISP IP, each request under its lock, no kept-alive tunnel,
        its bytes the ISP IP's (the session is made with isp=True)"""
        self.excerpt_post("https://www.ft.com/content/isp1")
        held, made, seen = Held(), [], []

        class S:
            cookies = Cookies()

            def get(self, url, timeout=None, headers=None):
                assert held.on, "a request outside the ISP lock"
                assert (headers or {}).get("Connection") == "close"
                seen.append(url)
                if "/newest/" in url:
                    return Resp(404, "<html>No results</html>", url)
                return Resp(200, "<html>Loading</html>", url)

        def sess(proxy=None, imp="chrome", isp=False):
            made.append((proxy, isp))
            return S()
        import sites_fetch
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://u:p@isp.test:12323"}), \
                mock.patch.object(sites_fetch, "isp_lock", held), mock.patch.object(paywall, "_session", sess), \
                mock.patch.object(paywall.time, "sleep", lambda s: None):
            self.assertEqual(paywall.archive_run(), {"submitted": 1})
        self.assertEqual(set(made), {("http://u:p@isp.test:12323", True)})
        self.assertTrue(any("/newest/" in u for u in seen) and any("/submit/" in u for u in seen))

    def test_isp_ip_refusing_a_second_client_is_waited_for(self):
        """the ISP IP answers "CONNECT tunnel failed, response 403" while another job's connection is still open (17 Nov
        2026: a run ended on all four mirrors): the same mirror is tried again after a pause"""
        self.excerpt_post("https://www.ft.com/content/busy")
        tries, slept = [], []

        class S:
            cookies = Cookies()

            def get(self, url, timeout=None, headers=None):
                tries.append(url.split("/")[2])
                if len(tries) <= 2:
                    raise ConnectionError("Failed to perform, curl: (7) CONNECT tunnel failed, response 403.")
                return Resp(200, SNAP, "https://archive.ph/2026/https://www.ft.com/content/busy")
        import sites_fetch
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://u:p@isp.test:12323"}), \
                mock.patch.object(sites_fetch, "isp_lock", Held()), \
                mock.patch.object(paywall, "_session", lambda *a, **k: S()), \
                mock.patch.object(paywall.time, "sleep", lambda s: slept.append(s)):
            self.assertEqual(paywall.archive_run(), {"archived": 1})
        self.assertEqual(tries, ["archive.ph"] * 3)
        self.assertIn(paywall.ISP_REFUSED_WAIT, slept)

    def test_cookies_kept_by_an_earlier_version_dont_stop_a_run(self):
        """the 06:17 UTC run of 15 Oct died on kv paywall:archive written before the cookies were kept per mirror
        ({name: value}): such entries are skipped (the next captcha solve writes them anew)"""
        corpus.kv_set("paywall:archive", {"cookies": {"qki": "x", "archive.is": {"qki": "y"}}})
        s, _, _ = paywall.archive_session()
        self.assertEqual(s.cookies.get("qki", domain="archive.is"), "y")
        corpus.kv_set("paywall:archive", None)

    def test_no_mirror_answering_ends_the_run_without_using_looks(self):
        """archive.today stops answering the cluster's IP at all after a burst of lookups (15 Oct): the run ends there
        and the posts keep their looks for later runs"""
        pids = [self.excerpt_post(f"https://www.ft.com/content/down{i}") for i in range(3)]

        def get(url, s=None, route="direct"):
            raise paywall.Unreachable("archive.today: no mirror answered (Timeout)")
        with mock.patch.object(paywall, "archive_get", get), mock.patch.object(paywall.time, "sleep", lambda s: None):
            self.assertEqual(paywall.archive_run(), {"unreachable": 1, "next_run": 2})
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM archive_scan WHERE post_id = ANY(%s) AND looks > 0",
                                             (pids,)).fetchone()[0], 0)

    def test_captcha_is_solved_once_and_kept(self):
        self.excerpt_post("https://www.ft.com/content/ccc")
        state = {"clear": False}
        cleared = []

        class S:
            cookies = Cookies()

            def get(self, url, timeout=None):
                if not state["clear"]:
                    return Resp(429, CAPTCHA_PAGE, url)
                return Resp(200, SNAP, "https://archive.ph/2026/https://www.ft.com/content/ccc")

        def clear(url, route="direct"):
            cleared.append(url)
            state["clear"] = True
        with mock.patch.object(paywall, "_session", lambda *a, **k: S()), mock.patch.object(paywall, "archive_clear", clear), \
                mock.patch.object(paywall.time, "sleep", lambda s: None), mock.patch.dict(os.environ, {"TWOCAPTCHA_API": "k"}):
            self.assertEqual(paywall.archive_run(), {"archived": 1})
        self.assertEqual(len(cleared), 1)

    def test_captcha_without_a_solver_key_stops_the_run(self):
        """no TWOCAPTCHA_API: no browser, no solve, no other solver; the run stops and every post keeps its looks"""
        pids = [self.excerpt_post(f"https://www.ft.com/content/nokey{i}") for i in range(2)]
        cleared = []

        class S:
            cookies = Cookies()

            def get(self, url, timeout=None):
                return Resp(429, CAPTCHA_PAGE, url)
        with mock.patch.object(paywall, "_session", lambda *a, **k: S()), \
                mock.patch.object(paywall, "archive_clear", lambda url, route="direct": cleared.append(url)), \
                mock.patch.object(paywall.time, "sleep", lambda s: None):
            os.environ.pop("TWOCAPTCHA_API", None)
            self.assertEqual(paywall.archive_run(), {"unreachable": 1, "next_run": 1})
        self.assertEqual(cleared, [])
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM archive_scan WHERE post_id = ANY(%s) AND looks > 0",
                                             (pids,)).fetchone()[0], 0)


class Spending(unittest.TestCase):
    def test_solver_series(self):
        corpus.db().execute("DELETE FROM solver_spend")
        paywall.spend("2captcha", "archive", 0.00299)
        paywall.spend("capsolver", "reuters", 0.0025)
        with mock.patch.object(monitor, "panel", lambda name, fresh=False: {"providers": []}):
            sp = monitor.spending()
        s = next(x for x in sp["series"] if x["id"] == "solvers")
        today = paywall.datetime.now(paywall.HKT).strftime("%Y-%m-%d")
        self.assertAlmostEqual(s["by_day"][today], 0.00549)
        self.assertIn("provider", s["source"])
        self.assertIn("--s4", open(os.path.join(HERE, "web-app", "src", "routes", "-MonitorPage.tsx")).read())

    def test_solver_balances_not_connected_without_keys(self):
        ids = {b["id"]: b["status"] for b in monitor.balances()["providers"]}
        self.assertEqual((ids["capsolver"], ids["2captcha"]), ("not_connected", "not_connected"))


if __name__ == "__main__":
    unittest.main()
