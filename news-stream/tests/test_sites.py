#!/usr/bin/env python3
"""The catalogue sites (sites.json, sites_fetch.py; Deyao, 2026-10-20: add the WiseSearch catalogue's Macau and Hong
Kong sources YYC lacked), on a throwaway database (TEST_DATABASE_URL), no network:

1. sites.json is valid: unique ids, known kind/type/region, what each kind needs; no id clashes with another source.
2. Each kind's reader: RSS, Atom, WordPress REST, a sitemap (news:title and slug titles), a sitemap index, an HTML
   listing, a dated e-paper table of contents (yesterday's when today's is not up), a JSON list; a GB2312 page is
   decoded by its own charset.
3. A run reads only the sites that are due, one host at a time, keeps the links the poller has not read (poll_seen)
   and drops them once read or older than KEEP; a failing site is a failing status line, not a failed run.
4. The poller sees them (sources.sites_file): one status line per site, the job's line "sites", a stale document
   fails every site; the corpus classifies them by sites.json (Newspaper / Magazine, region Macau) and a new site's
   first read is backlog (first_run).

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_sites.py
"""
import json, os, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, sources, sources_extra, sites_fetch  # noqa: E402

HKT = timezone(timedelta(hours=8))
NOW = datetime.now(HKT).replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


RSS = f"""<rss><channel><item><title>澳門新聞一</title><link>https://mo.example/a/1</link>
<pubDate>{(NOW - timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S +0800')}</pubDate><description>短</description></item>
<item><title>舊聞</title><link>https://mo.example/a/0</link><pubDate>Mon, 01 Sep 2025 10:00:00 +0800</pubDate></item>
</channel></rss>"""
ATOM = f"""<feed><entry><title>Blog post</title><link rel="alternate" href="https://blog.example/p/1"/>
<published>{iso(NOW - timedelta(hours=2))}</published><author><name>Ann</name></author><summary>x</summary></entry></feed>"""
WP = json.dumps([{"title": {"rendered": "雜誌 &amp; 文章"}, "link": "https://mag.example/2026/x/",
                  "date_gmt": (NOW - timedelta(hours=3)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}])
SITEMAP = f"""<urlset><url><loc>https://news.example/hk/2026/abc-titled</loc><news:news><news:title>有標題</news:title>
<news:publication_date>{iso(NOW)}</news:publication_date></news:news></url>
<url><loc>https://news.example/hk/2026/the-slug-title</loc><lastmod>{iso(NOW)}</lastmod></url>
<url><loc>https://news.example/about</loc><lastmod>{iso(NOW)}</lastmod></url></urlset>"""
INDEX = """<sitemapindex><sitemap><loc>https://news.example/sm-2026-10.xml</loc></sitemap>
<sitemap><loc>https://news.example/sm-2026-09.xml</loc></sitemap><sitemap><loc>https://news.example/pages.xml</loc></sitemap></sitemapindex>"""
LISTING = """<a href="/news/20261020/123.html">今日港聞標題</a><a href="/about">關於我們的網站</a>
<a href="https://other.example/x">外站連結文字</a>"""
TOC = """<a href=content_9.htm>澳門日報頭版新聞</a><a href='node_3.htm'>第三版</a>"""


def site(**kw):
    s = {"id": "t_site", "name": "測試", "url": "https://mo.example/feed", "kind": "rss", "type": "Newspaper", "region": "Macau"}
    s.update(kw)
    return s


class Data(unittest.TestCase):
    def test_sites_json(self):
        sites = sites_fetch.load()
        ids = [s["id"] for s in sites]
        others = {sp[0] for sp in sources.SOURCES + sources_extra.EXTRA}
        self.assertFalse(set(ids) & others, "a catalogue id clashes with a source of sources.py / sources_extra.py")
        for s in sites:
            self.assertIn(s["region"], ("HK", "Macau", "Mainland", "Taiwan", "Global"))

    def test_load_refuses_bad_rows(self):
        for bad in ([site(kind="ftp")], [site(type="Paper")], [site(), site()], [site(kind="listing")], [site(url="x")]):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(bad, f)
            with self.assertRaises(ValueError):
                sites_fetch.load(f.name)


class Readers(unittest.TestCase):
    def read(self, s, pages):
        def fake(url, **kw):
            if url not in pages:
                raise sources.urllib.error.HTTPError(url, 404, "nf", {}, None)
            return pages[url]
        with mock.patch.object(sources, "fetch", side_effect=fake):
            return sites_fetch.read(s)

    def test_rss_and_atom(self):
        got = self.read(site(), {"https://mo.example/feed": RSS})
        self.assertEqual([r["title"] for r in got], ["澳門新聞一", "舊聞"])
        got = self.read(site(url="https://blog.example/atom", type="Blog"), {"https://blog.example/atom": ATOM})
        self.assertEqual((got[0]["title"], got[0]["url"], got[0]["author"]), ("Blog post", "https://blog.example/p/1", "Ann"))

    def test_blogger_atom_single_quotes(self):
        atom = f"""<feed><entry><title type='text'>Blogger post</title><link rel='replies' href='https://b.example/c'/>
<link rel='alternate' type='text/html' href='https://b.example/2026/10/post.html'/><published>{iso(NOW)}</published></entry></feed>"""
        got = self.read(site(url="https://b.example/feeds/posts/default", type="Blog"), {"https://b.example/feeds/posts/default": atom})
        self.assertEqual(got[0]["url"], "https://b.example/2026/10/post.html")

    def test_wp_json(self):
        got = self.read(site(kind="wp_json", url="https://mag.example/wp-json/wp/v2/posts"), {"https://mag.example/wp-json/wp/v2/posts": WP})
        self.assertEqual(got[0]["title"], "雜誌 & 文章")
        self.assertTrue(sources.parse_time(got[0]["time"]) > NOW - timedelta(hours=4))

    def test_sitemap_and_index(self):
        s = site(kind="sitemap", url="https://news.example/sm.xml", keep=r"/hk/")
        got = self.read(s, {"https://news.example/sm.xml": SITEMAP})
        self.assertEqual(sorted(r["title"] for r in got), ["the slug title", "有標題"])
        s = site(kind="sitemap_index", url="https://news.example/index.xml", match=r"sm-\d", first=1, keep=r"/hk/")
        got = self.read(s, {"https://news.example/index.xml": INDEX, "https://news.example/sm-2026-10.xml": SITEMAP})
        self.assertEqual(len(got), 2)

    def test_listing(self):
        s = site(kind="listing", url="https://news.example/latest", href=r"^/news/(?P<time>\d{8})/\d+\.html$")
        got = self.read(s, {"https://news.example/latest": LISTING})
        self.assertEqual([(r["title"], r["url"]) for r in got], [("今日港聞標題", "https://news.example/news/20261020/123.html")])

    def test_listing_card_takes_its_heading(self):
        """a link wrapping a card (Now 財經: heading, summary, time) is titled by its heading, not all its text"""
        page = ('<a href="/news/post.php?id=1"><img src="x.jpg"></a><li><a href="/news/post.php?id=2&type=local">'
                '<h3>恒指夜期丨報23074 升50點</h3><div class="leading">【信報財經新聞】夜市期貨靠穩，截至晚上約08時58分…</div>'
                '<div class="timeStamp"> 【本地】 信報財經新聞 40分鐘前</div></a></li>'
                '<a href="/news/post.php?id=3"><h4>本地</h4>李家超出席一個地球高峰論壇</a>')
        s = site(kind="listing", url="https://fin.example/news/", href=r"^/news/post\.php\?id=\d+")
        got = self.read(s, {"https://fin.example/news/": page})
        self.assertEqual(sorted((r["url"], r["title"]) for r in got),
                         [("https://fin.example/news/post.php?id=2&type=local", "恒指夜期丨報23074 升50點"),
                          ("https://fin.example/news/post.php?id=3", "本地李家超出席一個地球高峰論壇")])  # a short heading: the link's text

    def test_epaper_falls_back_to_yesterday(self):
        y = NOW - timedelta(days=1)
        s = site(kind="epaper", url="https://www.macaodaily.com/html/{yyyy}-{mm}/{dd}/node_2.htm", href=r"^content_\d+\.htm$")
        got = self.read(s, {f"https://www.macaodaily.com/html/{y:%Y-%m}/{y:%d}/node_2.htm": TOC})
        self.assertEqual(got[0]["url"], f"https://www.macaodaily.com/html/{y:%Y-%m}/{y:%d}/content_9.htm")
        self.assertEqual(str(sources.parse_time(got[0]["time"]))[:10], f"{y:%Y-%m-%d}")

    def test_epaper_unpublished_day_answers_500(self):
        """正報 answers a day it has not published with 500 "System Error" (2026-10-22): yesterday's issue, not a
        failing site; a 500 on every day is still the site's failure"""
        y = NOW - timedelta(days=1)
        s = site(kind="epaper", url="https://www.chengpou.example/daily/{yyyy}{mm}{dd}/2.html", href=r"^content_\d+\.htm$")
        today_url = f"https://www.chengpou.example/daily/{NOW:%Y%m%d}/2.html"
        pages = {f"https://www.chengpou.example/daily/{y:%Y%m%d}/2.html": TOC}

        def fake(url, **kw):
            if url == today_url:
                raise sources.urllib.error.HTTPError(url, 500, "System Error", {}, None)
            if url not in pages:
                raise sources.urllib.error.HTTPError(url, 404, "nf", {}, None)
            return pages[url]
        with mock.patch.object(sources, "fetch", side_effect=fake):
            got = sites_fetch.read(s)
        self.assertEqual(got[0]["url"], f"https://www.chengpou.example/daily/{y:%Y%m%d}/content_9.htm")

        def down(url, **kw):
            raise sources.urllib.error.HTTPError(url, 500, "System Error", {}, None)
        with mock.patch.object(sources, "fetch", side_effect=down):
            with self.assertRaises(sources.urllib.error.HTTPError):
                sites_fetch.read(s)

    def test_json_list(self):
        s = site(kind="json", url="https://api.example/list", items="data.list", title="t", link="u", time="ts", base="https://www.example/")
        got = self.read(s, {"https://api.example/list": json.dumps({"data": {"list": [{"t": "標題", "u": "/n/1", "ts": 1760900000}]}})})
        self.assertEqual((got[0]["title"], got[0]["url"]), ("標題", "https://www.example/n/1"))

    def test_json_nested_template_and_time_format(self):
        s = site(kind="json", url="https://api.example/news?lang=zh", items="data.newsList.*.data", title="title",
                 link="https://www.example/zh-hant/sharelink/news/{id}", time="date", time_format="%m/%d/%Y %H:%M:%S")
        body = json.dumps({"data": {"newsList": [{"data": [{"id": 7, "title": "澳門本地", "date": "10/20/2026 12:11:00"}]},
                                                 {"data": [{"id": 8, "title": "體育", "date": "10/19/2026 08:00:00"}]}]}})
        got = self.read(s, {"https://api.example/news?lang=zh": body})
        self.assertEqual([(r["title"], r["url"]) for r in got], [("澳門本地", "https://www.example/zh-hant/sharelink/news/7"),
                                                                ("體育", "https://www.example/zh-hant/sharelink/news/8")])
        self.assertEqual(got[0]["time"], "2026-10-20T12:11:00+08:00")

    def test_regex_and_xml_and_cst(self):
        page = '<h3>保安司活動一</h3><p>..</p><a href="/cht/n/1.aspx">閱讀更多></a><h3>活動二</h3><a href="/cht/n/2.aspx">閱讀更多></a>'
        s = site(kind="regex", url="https://gss.example/list", pattern=r"<h3>(?P<title>[^<]+)</h3>.*?href=\"(?P<url>/cht/n/\d+\.aspx)\"")
        got = self.read(s, {"https://gss.example/list": page})
        self.assertEqual([(r["title"], r["url"]) for r in got], [("保安司活動一", "https://gss.example/cht/n/1.aspx"),
                                                                ("活動二", "https://gss.example/cht/n/2.aspx")])
        xml = "<news><newsitem><newsid>77</newsid><newsdate>2026/10/16</newsdate><newstitle>行政長官出席活動</newstitle></newsitem></news>"
        s = site(kind="xml", url="https://gce.example/list.xml", item="newsitem", title="newstitle", time="newsdate",
                 link="https://gce.example/read_news2.aspx?lang=cn&newsid={newsid}", time_format="%Y/%m/%d")
        got = self.read(s, {"https://gce.example/list.xml": xml})
        self.assertEqual((got[0]["title"], got[0]["url"], got[0]["time"][:10]),
                         ("行政長官出席活動", "https://gce.example/read_news2.aspx?lang=cn&newsid=77", "2026-10-16"))
        cst = RSS.replace("+0800", "CST")
        got = self.read(site(), {"https://mo.example/feed": cst})
        self.assertEqual(sources.parse_time(got[0]["time"]).utcoffset(), timedelta(hours=8))

    def test_yearly_list_falls_back_to_last_year(self):
        y = datetime.now(HKT).year
        s = site(kind="rss", url="https://gov.example/press_{yyyy}.xml")
        got = self.read(s, {f"https://gov.example/press_{y - 1}.xml": RSS})  # this year's 404 (early January)
        self.assertEqual(got[0]["title"], "澳門新聞一")

    def test_dated_url(self):
        s = site(kind="json", url="https://api.example/section?date={yyyy}-{mm}-{dd}&x={keep}", items="data", link="u")
        self.assertEqual(sites_fetch.toc_url(s, NOW), f"https://api.example/section?date={NOW:%Y-%m-%d}&x={{keep}}")

    def test_charset(self):
        body = '<meta charset="gb2312"><a href="content_1.htm">澳门日报新闻标题</a>'.encode("gb2312")
        self.assertEqual(sources.body_charset(body), "gb18030")
        self.assertIn("澳门日报", body.decode(sources.body_charset(body)))


class Dates(unittest.TestCase):
    """the date shapes the catalogue's sites write (2026-10-20: IPD/Planning Department English, 聖德肋撒醫院, UNICEF,
    翠華 came in undated): day-month-year words, month-day-year words, dd/mm/yyyy (Hong Kong's order)"""
    def test_word_and_slash_dates(self):
        for raw, day in (("6 Oct 2026", "2026-10-06"), ("19 August 2026", "2026-08-19"), ("20 Oct 2026", "2026-10-20"),
                         ("October 20, 2026", "2026-10-20"), ("Oct 5, 2026", "2026-10-05"), ("05/10/2026", "2026-10-05"),
                         ("20/10/2026", "2026-10-20"), ("2026/10/20", "2026-10-20"), ("2026.10.20", "2026-10-20"),
                         ("20-10-2026", "2026-10-20"), ("2026年10月20日", "2026-10-20"),
                         ("2026 年 10 月 16 日", "2026-10-16"), ("10-16,2026", "2026-10-16")):
            t = sources.parse_time(raw)
            self.assertIsNotNone(t, raw)
            self.assertEqual(t.isoformat()[:10], day, raw)
            self.assertIsInstance(t, corpus.DateOnly, raw)

    def test_explicit_charset(self):
        class R:
            headers = {"Content-Type": "text/html"}
            status = 200

            def read(self):
                return "澳門日報".encode("big5")

            def close(self):
                pass
        with mock.patch("urllib.request.OpenerDirector.open", return_value=R()):
            self.assertEqual(sources.fetch("https://x.example/", charset="big5"), "澳門日報")


class AwsWaf(unittest.TestCase):
    """browser_fetch.cleared waits out AWS WAF's challenge page too (Prestige HK, 2026-10-20: its title is the site's,
    the page holds the challenge script; a real Chrome clears it in ~8 s)"""
    def test_waits_for_aws_waf(self):
        import browser_fetch

        class Pg:
            def __init__(self):
                self.n = 0

            def title(self):
                return "Prestige Online"

            def content(self):
                self.n += 1
                return '<script src="https://abc.token.awswaf.com/x/challenge.js"></script>' if self.n < 3 else "<html>ok</html>"

            def wait_for_load_state(self, *a, **k):
                pass

            def locator(self, sel):
                return mock.Mock(count=lambda: 0)
        pg = Pg()
        with mock.patch.object(browser_fetch.time, "sleep"):
            self.assertTrue(browser_fetch.cleared(pg))
        self.assertEqual(pg.n, 3)  # it looked until the challenge was gone


class Run(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM kv")
        corpus.db().execute("DELETE FROM poll_seen")

    def run_with(self, sites, pages, now=NOW):
        calls = []

        def fake(url, **kw):
            calls.append(url)
            if isinstance(pages.get(url), Exception):
                raise pages[url]
            return pages[url]
        with mock.patch.object(sources, "fetch", side_effect=fake):
            return sites_fetch.run(sites, now=now), calls

    def test_due_unseen_and_failures(self):
        a = site(id="mo_a")
        b = site(id="mo_b", url="https://down.example/feed")
        pages = {"https://mo.example/feed": RSS, "https://down.example/feed": OSError("refused")}
        d, calls = self.run_with([a, b], pages)
        self.assertEqual(d["sites"]["mo_a"]["ok"], True)
        self.assertEqual(d["sites"]["mo_b"]["ok"], False)
        self.assertEqual([it["url"] for it in d["items"]], ["https://mo.example/a/1"])  # the 2025 item is too old
        # the next run (10 min later): nothing due (Newspaper every 30 min), the waiting link is still offered
        d, calls = self.run_with([a, b], pages, NOW + timedelta(minutes=10))
        self.assertEqual(calls, [])
        self.assertEqual(len(d["items"]), 1)
        # once the poller has read it (poll_seen), it is no longer offered
        corpus.db().execute("INSERT INTO poll_seen (url, at) VALUES ('https://mo.example/a/1', %s)", (iso(NOW),))
        d, _ = self.run_with([a, b], pages, NOW + timedelta(minutes=20))
        self.assertEqual(d["items"], [])

    def test_conditional_and_quiet(self):
        a = site(id="mo_c")
        calls = []

        def fake(url, **kw):
            calls.append(dict(kw.get("headers") or {}))
            if (kw.get("headers") or {}).get("If-None-Match") == '"v1"':
                raise sources.urllib.error.HTTPError(url, 304, "Not Modified", {}, None)
            if kw.get("meta") is not None:
                kw["meta"].update(etag='"v1"', last_modified=None)
            return RSS
        with mock.patch.object(sources, "fetch", side_effect=fake):
            d = sites_fetch.run([a], now=NOW)
            self.assertEqual(d["sites"]["mo_c"]["etag"], '"v1"')
            self.assertEqual(d["sites"]["mo_c"]["quiet"], 0)  # its first read brought a new link
            st = dict(d["sites"]["mo_c"], tried=0)
            d["sites"]["mo_c"] = st
            corpus.file_put("sites.json", d)
            d = sites_fetch.run([a], now=NOW + timedelta(minutes=40))
        self.assertEqual(calls[-1].get("If-None-Match"), '"v1"')
        self.assertEqual((d["sites"]["mo_c"]["ok"], d["sites"]["mo_c"]["unchanged"], d["sites"]["mo_c"]["quiet"]), (True, 1, 1))
        self.assertEqual(len(d["items"]), 1)  # the waiting link is still offered
        base = 30 * 60
        self.assertEqual(sites_fetch.every(a, {"quiet": 0}), base)
        self.assertEqual(sites_fetch.every(a, {"quiet": 2}), 4 * base)
        self.assertEqual(sites_fetch.every(a, {"quiet": 9}), 6 * base)  # 6× its base, 6 h at most
        # a failing site is tried again at its base cadence, whatever its quiet count (2026-10-21: bllnr, failing since
        # a node's blocked reads, waited 6 h for the try that would have passed on another route)
        self.assertEqual(sites_fetch.every(a, {"quiet": 9, "fails": 2}), base)

    def test_escalates_to_the_isp_ip(self):
        a = site(id="mo_e")
        seen = []

        def fake(url, **kw):
            seen.append(kw.get("proxy"))
            if not kw.get("proxy"):
                raise sources.urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return RSS
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp.example:1"}), \
             mock.patch.object(sites_fetch, "isp_lock", side_effect=lambda: __import__("contextlib").nullcontext()), \
             mock.patch.object(sites_fetch, "_impersonated", side_effect=sources.urllib.error.URLError("no chrome here")), \
             mock.patch.object(sources, "fetch", side_effect=fake):
            d = sites_fetch.run([a], now=NOW)  # the first failure escalates (2026-10-21)
            self.assertEqual((d["sites"]["mo_e"]["ok"], d["sites"]["mo_e"]["auto_route"]), (True, "isp"))
            d["sites"]["mo_e"]["tried"] = 0
            corpus.file_put("sites.json", d)
            seen.clear()
            sites_fetch.run([a], now=NOW + timedelta(minutes=80))
        self.assertEqual(seen, ["http://isp.example:1"])  # straight to the route that reads

    def test_escalates_to_chromes_tls(self):
        """Cover Magazine (2026-10-21): urllib's TLS fails on every route (a timeout direct, EOF through the proxies),
        curl_cffi as Chrome reads it through the ISP IP: the job finds that and keeps it"""
        a = site(id="mo_t")
        imp = []

        def fake(url, **kw):
            raise sources.urllib.error.URLError("_ssl.c:1000: EOF occurred in violation of protocol")

        def chrome(url, proxy, kw, isp=False):
            imp.append((proxy, isp))
            if not isp:
                raise sources.urllib.error.URLError("timed out")
            return RSS
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp.example:1"}), \
             mock.patch.object(sites_fetch, "isp_lock", side_effect=lambda: __import__("contextlib").nullcontext()), \
             mock.patch.object(sites_fetch, "_impersonated", side_effect=chrome), \
             mock.patch.object(sources, "fetch", side_effect=fake):
            d = sites_fetch.run([a], now=NOW)
            st = d["sites"]["mo_t"]
            self.assertEqual((st["ok"], st["auto_route"], st["auto_impersonate"]), (True, "isp", True))
            st["tried"] = 0
            corpus.file_put("sites.json", d)
            imp.clear()
            sites_fetch.run([a], now=NOW + timedelta(minutes=80))
        self.assertEqual(imp, [("http://isp.example:1", True)])  # straight to the route that reads

    def test_escalation_prefers_rows_and_covers_own_routes(self):
        """2026-10-21: a site with a route of its own (sites.json "proxy": "isp") that fails there tries the others;
        a route answering an empty list (a challenge page parsed as a feed) loses to one with rows; YouTube never goes
        over yycstream"""
        a = site(id="mo_p", proxy="isp")
        seen = []
        EMPTY = "<rss><channel></channel></rss>"

        def fake(url, **kw):
            seen.append(kw.get("proxy"))
            if kw.get("proxy") == "http://isp.example:1":
                raise sources.urllib.error.HTTPError(url, 508, "Loop Detected", {}, None)
            return RSS if kw.get("proxy") else EMPTY  # direct: an empty "feed"; yycstream: the real one
        with mock.patch.dict(os.environ, {"ISP_PROXY": "http://isp.example:1"}), \
             mock.patch.object(sites_fetch, "isp_lock", side_effect=lambda: __import__("contextlib").nullcontext()), \
             mock.patch.object(sites_fetch, "_impersonated", side_effect=sources.urllib.error.URLError("no chrome here")), \
             mock.patch.object(sources.resi, "configured", return_value=True), \
             mock.patch.object(sources.resi, "url", return_value="http://resi.example:2"), \
             mock.patch.object(sources, "fetch", side_effect=fake):
            d = sites_fetch.run([a], now=NOW)
            st = d["sites"]["mo_p"]
            self.assertEqual((st["ok"], st["auto_route"], st["items"]), (True, "hk", 2))
            seen.clear()
            st["tried"] = 0
            corpus.file_put("sites.json", d)
            sites_fetch.run([a], now=NOW + timedelta(minutes=40))
            self.assertEqual(seen, ["http://resi.example:2"])  # the found route, over the site's own, for a day
            # a found route that answers an empty list is escalated again (bllnr kept direct-as-Chrome, 0 rows)
            d = corpus.file_get("sites.json")
            d["sites"]["mo_p"].update(tried=0, auto_route="direct", auto_impersonate=False, auto_since=time.time())
            corpus.file_put("sites.json", d)
            d = sites_fetch.run([a], now=NOW + timedelta(minutes=80))
            self.assertEqual((d["sites"]["mo_p"]["auto_route"], d["sites"]["mo_p"]["items"]), ("hk", 2))
            y = site(id="mo_y", url="https://www.youtube.com/feeds/videos.xml?channel_id=UCx")
            seen.clear()
            def gone(url, **kw):
                seen.append(kw.get("proxy"))
                raise sources.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            with mock.patch.object(sources, "fetch", side_effect=gone):
                d = sites_fetch.run([y], now=NOW)
            self.assertEqual(seen, [None, "http://isp.example:1"])  # direct, then the ISP IP only
        self.assertFalse(d["sites"]["mo_y"]["ok"])
        self.assertNotIn("http://resi.example:2", seen)

    def test_poller_view(self):
        a = site(id="mo_a")
        self.run_with([a], {"https://mo.example/feed": RSS})
        items, status = sources.sites_file()
        self.assertEqual(status["mo_a"]["ok"], True)
        self.assertEqual(status["sites"]["ok"], True)
        self.assertEqual(items[0]["source"], "測試")
        with mock.patch.object(corpus, "SITE_META", {"mo_a": ("Newspaper", "Macau", "測試")}):
            cl = corpus.classify(items[0])
        self.assertEqual((cl["source_type"], cl["region"], cl["website"]), ("Newspaper", "Macau", "測試"))
        # stale: every site fails
        d = corpus.file_get("sites.json")
        d["fetched"] = iso(NOW - timedelta(hours=2))
        corpus.file_put("sites.json", d)
        items, status = sources.sites_file()
        self.assertEqual((items, status["mo_a"]["ok"], status["sites"]["ok"]), ([], False, False))

    def test_first_read_is_backlog(self):
        with mock.patch.object(corpus, "SITE_META", {"mo_new": ("Newspaper", "Macau", "測試")}):
            corpus.ingest([{"sid": "mo_new", "source": "測試", "title": "第一篇", "url": "https://mo.example/a/9",
                            "published": NOW}], iso(NOW))
        r = corpus.db().execute("SELECT first_run, source_type, region FROM posts WHERE url='https://mo.example/a/9'").fetchone()
        self.assertEqual(tuple(r), (True, "Newspaper", "Macau"))

    def test_gnews_macau_publisher(self):
        cl = corpus.classify({"sid": "gnews_1", "source": "澳門日報電子版", "url": "https://www.macaodaily.com/x"})
        self.assertEqual(cl["region"], "Macau")

    def test_taiwan_region(self):
        """Taiwan is a region of its own (source gaps, 2026-10-23): a Taiwan site of sites.json, a Taiwan publisher of
        Google News, a linked .tw page; the market classifier's fallback puts Taiwan with hk (only Mainland is mainland)"""
        import streams
        tw = [s for s in sites_fetch.load() if s["region"] == "Taiwan"]
        self.assertTrue(tw)
        cl = corpus.classify({"sid": tw[0]["id"], "source": tw[0]["name"], "url": tw[0]["url"]})
        self.assertEqual(cl["region"], "Taiwan")
        for pub in ("聯合新聞網", "自由時報電子報", "中時新聞網", "中央社 CNA"):
            self.assertEqual(corpus.classify({"sid": "gnews_12", "source": pub, "url": "https://x.example/a"})["region"], "Taiwan", pub)
        self.assertEqual(corpus.classify({"sid": "gnews_12", "source": "世界新聞網", "url": "https://x.example/a"})["region"], "Global")
        self.assertEqual(corpus.link_meta("https://www.kcg.gov.tw/News_Content.aspx?n=1"), ("Official", "Taiwan"))



class OffRegions(unittest.TestCase):
    """Mainland, Taiwan and Global sites are not read (Deyao, 2026-10-28: cost), but the exceptions; their hosts are
    ignored by link following, unless a read source shares the host"""
    def test_off(self):
        import follow
        sites = {s["id"]: s for s in sites_fetch.load()}
        read = {s["id"] for s in sites_fetch.load_read()}
        self.assertNotIn("tw_yahoo", read)
        self.assertNotIn("cn_sina_k", read)
        self.assertNotIn("gl_bignewsnetwork", read)
        for sid in sites_fetch.OFF_EXCEPT + ("hk_hkgpao",):
            self.assertIn(sid, read)
        self.assertTrue(all(s["region"] in ("HK", "Macau") or sid in sites_fetch.OFF_EXCEPT for sid, s in sites.items() if sid in read))
        self.assertEqual(follow.skip_reason("https://tw.news.yahoo.com/a-1.html"), follow.IGNORED)
        self.assertIsNone(follow.skip_reason("https://www.locpg.gov.cn/jsdt/2026-10/28/c_1.htm"))
        with mock.patch.object(sites_fetch, "read", side_effect=AssertionError("an off site was read")) as r, \
                mock.patch.object(sites_fetch, "load", return_value=[sites["tw_yahoo"]]):
            sites_fetch.run(deadline=__import__("time").time() + 5)
        r.assert_not_called()

if __name__ == "__main__":
    unittest.main()
