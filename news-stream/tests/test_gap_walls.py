#!/usr/bin/env python3
"""The source-gap hosts that needed a route or new code (Deyao, 2026-10-23: "add the real gaps"; the audit's "wall"
and "parser" rows): each a watched source of the catalogue (sites.json, sites_fetch.py), read by the route the
cluster gets through (yycstream with the site's own exit country, Chrome on the cluster's IP or on yycstream) or by
a parser of its own (sources_extra.SITE_PARSERS), and each story's text from the API its page uses when the page holds
none (cache_fetch.api_story). One test per parser and route; on a throwaway database (TEST_DATABASE_URL), no network:
saved answer snippets.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_gap_walls.py
"""
import base64, json, os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("RESI_PROXY", "http://yycstream:pw@geo.iproyal.com:12321")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import browser_fetch, cache_fetch, corpus, follow, resi, sites_fetch, sources, sources_extra as se  # noqa: E402

HKT = corpus.HKT
SITES = {s["id"]: s for s in sites_fetch.load()}
# a Google News result of each gap host (kv source_gaps, 2026-10-23) → the source that reads it now
GAPS = {
    "https://www.businesstimes.com.hk/articles/349901/x/": "hk_businesstimes",
    "https://www.sl886.com/blog/46101": "hk_sl886_blog",
    "https://www.lifestyleasia.com/hk/whats-on/star-ferry-set-to-raise-prices-on-january-25/": "hk_lifestyleasia",
    "https://www.flamingwheels.online/m/20261023/156672.htm": "hk_flamingwheels",
    "https://www.pressreader.com/hong-kong/wen-wei-po-1/20261017/281715506590427": "hk_pressreader_wenweipo",
    "https://www.student.hk/commonDetial?rid=WS6adab6d8c1f493b388cd5bfc": "hk_studenthk",
    "https://www.smg.gov.mo/zh/news-detail/1089": "mo_smg_news",
    "https://huacheng.gz-cmc.com/pages/2026/10/16/3d77becaf1694c008ce82a1b46b89143.html": "cn_huacheng_tianxia",
    "https://www.crntt.tw/doc/docDetail.jsp?coluid=46&kindid=0&docid=160280128": "tw_crntt_home",
    "https://news.cts.com.tw/cna/local/202610/202610153090165.html": "tw_cts",
    "https://www.fclnews.com/232832/": "tw_fclnews",
    "https://www.anntw.com/articles/20261018-Dnvx": "tw_anntw",
    "https://pronews.tw/2026/10/15/321606/": "tw_pronews",
    "https://www.tcpttw.com/local/2026/10/20/322172/": "tw_tcpttw",
    "https://www.cdns.com.tw/articles/1475855": "tw_cdns",
    "https://taidaily.com/2026/10/21/584521/": "tw_taidaily",
    "https://www.yesmedia.com.tw/x/": "tw_yesmedia",
    "https://today.line.me/tw/v3/article/LXe7qZ0": "tw_linetoday",
    "https://hao.cnyes.com/post/273285": "tw_cnyes_hao",
    "https://ocacnews.net/article/433136": "tw_ocacnews",
    "https://www.contentplatform.info/articles/546724/x": "tw_contentplatform",
    "https://www.chinesedaily.com/article/detail-709239.html": "gl_chinesedaily",
    "https://www.sinchew.com.my/news/20261022/mysinchew/7984333": "gl_sinchew",
    "https://themalaysianreserve.com/2026/10/20/x/": "gl_malaysianreserve",
    "https://opengovasia.com/hong-kong-iam-smart-adds-ai-assistant-to-improve-public-services/": "gl_opengovasia",
    "https://law.asia/iomed-first-anniversary-hong-kong/": "gl_lawasia",
    "https://www.miragenews.com/hku-innovation-week-showcases-120-breakthroughs-1760835/": "gl_miragenews",
    "https://app.dealroom.co/news/note/chinaamc-lesha-bank-launch-1b-sharia-compliant-china-equity-fund": "gl_dealroom_news",
    "https://japannews.yomiuri.co.jp/editorial/political-pulse/20261017-353832/": "gl_japannews",
    "https://easternherald.com/2026/10/23/x/": "gl_easternherald",
    "https://www.tradingview.com/news/gelonghui:60b3fd4c7acdf:0/": "gl_tradingview_en",
    "https://tw.tradingview.com/news/gelonghui:60b3fd4c7acdf:0/": "gl_tradingview_zh",
    "https://www.moomoo.com/hant/news/post/1001528865": "gl_moomoo_news",
    "https://www.thinkchina.sg/society/big-read-hong-kong-wants-more-babies-couples-need-more-cash-grants": "gl_thinkchina_politics",
    "https://www.mofa.gov.ae/en/MediaHub/News/2026/10/22/uae-sudan": "gl_mofa_uae_en",
}


def at(url):
    """a canned answer per URL substring → a fake sources.fetch / cache_fetch.get"""
    def f(answers):
        def fetch(u, *a, **k):
            for key, v in answers.items():
                if key in u:
                    return v(u, k) if callable(v) else v
            raise AssertionError("unexpected request " + u)
        return fetch
    return f(url)


class Sources(unittest.TestCase):
    def test_every_gap_host_has_a_source(self):
        # (地球物理氣象局's list is on cms.smg.gov.mo, its stories on www.smg.gov.mo: its "link" template names that host)
        follow._outlets.clear()
        for url, sid in GAPS.items():
            self.assertIn(sid, SITES, sid)
            got = follow.outlet_for(url)
            self.assertIsNotNone(got, url)
            self.assertEqual(urlsite(SITES[got[0]]), urlsite(SITES[sid]), url)

    def test_taiwan_outlets_are_region_taiwan(self):
        for sid in ("tw_cts", "tw_fclnews", "tw_linetoday", "tw_cnyes_hao", "tw_ocacnews", "tw_contentplatform"):
            self.assertEqual(SITES[sid]["region"], "Taiwan", sid)


def urlsite(s):
    return cache_fetch.site(s["url"])


class Routes(unittest.TestCase):
    def test_taiwan_sites_list_and_pages_on_a_taiwan_exit(self):
        """華視, 台灣新聞雲報, 台灣醒報…: CloudFront 403 / Cloudflare's challenge to the cluster's IP and to Hong Kong exits,
        200 on a Taiwan one (probed 2026-10-23): the list on yycstream tw, its pages too (cache_fetch.PROXY_COUNTRY)"""
        for sid in ("tw_cts", "tw_fclnews", "tw_anntw", "tw_tcpttw", "tw_cdns", "tw_taidaily", "tw_yesmedia"):
            self.assertEqual(SITES[sid]["proxy"], "tw", sid)
        seen = []
        with mock.patch.object(sites_fetch.sources, "fetch", side_effect=lambda u, proxy=None, **k: seen.append(proxy) or
                               '<a href="https://news.cts.com.tw/cna/local/202610/202610233093189.html">陳玉珍成立金門縣長競選服務處</a>'):
            rows = sites_fetch.read(SITES["tw_cts"])
        self.assertEqual(rows[0]["url"], "https://news.cts.com.tw/cna/local/202610/202610233093189.html")
        self.assertIn("_country-tw", seen[0])
        self.assertEqual(cache_fetch.proxy_country("news.cts.com.tw"), "tw")
        self.assertEqual(cache_fetch.proxy_country("www.hkej.com"), "hk")
        pages = []
        with mock.patch.object(cache_fetch, "get", side_effect=lambda u, proxy=None, headers=None, isp=False:
                               pages.append(proxy) or "<html><body><p>" + "颱風來襲" * 80 + "</p></body></html>"):
            text, _, _ = cache_fetch.fetch_one("https://www.fclnews.com/233641/")
        self.assertIn("颱風來襲", text)
        self.assertIn("_country-tw", pages[0])

    def test_other_exit_countries(self):
        self.assertEqual(SITES["gl_malaysianreserve"]["proxy"], "my")
        self.assertEqual(SITES["gl_japannews"]["proxy"], "jp")
        self.assertEqual(cache_fetch.proxy_country("japannews.yomiuri.co.jp"), "jp")

    def test_browser_sites_and_their_exits(self):
        """Cloudflare's managed challenge passes in Chrome on the cluster's IP (星洲, OpenGov Asia, Dealroom, HKBT,
        SL886, 僑新聞's Incapsula); Lifestyle Asia (CloudFront refuses the cluster's IP, AWS WAF's challenge passes on a
        Hong Kong residential IP) and 華人今日網 (US) take Chrome on yycstream"""
        for sid in ("gl_sinchew", "gl_opengovasia", "gl_dealroom_news", "hk_businesstimes", "hk_sl886_blog", "tw_ocacnews"):
            self.assertTrue(SITES[sid].get("browser"), sid)
            self.assertIsNone(cache_fetch.browser_country(SITES[sid]["url"]), sid)
        prox = browser_fetch.catalogue_proxies()
        self.assertEqual(prox.get("hk_lifestyleasia"), "hk")
        self.assertEqual(prox.get("gl_chinesedaily"), "us")
        self.assertEqual(cache_fetch.browser_country("https://www.lifestyleasia.com/hk/whats-on/x/"), "hk")

    def test_browser_pass_reads_proxied_pages_in_their_own_chrome(self):
        calls = []

        def fake(urls, profile, until, dates, country):
            calls.append((tuple(urls), profile, country))
            return {u: "text" for u in urls}
        with mock.patch.object(cache_fetch, "_chrome_pages", side_effect=fake):
            got = cache_fetch.browser_pass(["https://www.sinchew.com.my/news/20261023/nation/1",
                                            "https://www.lifestyleasia.com/hk/whats-on/x/"])
        self.assertEqual(sorted(calls), [(("https://www.lifestyleasia.com/hk/whats-on/x/",), "cache-profile-hk", "hk"),
                                         (("https://www.sinchew.com.my/news/20261023/nation/1",), "cache-profile", "")])
        self.assertEqual(len(got), 2)

    def test_catalogue_browser_runs_a_proxied_site_in_a_chrome_on_its_exit(self):
        launched = []

        class Ctx:
            pages = []

            def new_page(self):
                return object()

            def on(self, *a):
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

            def launch_persistent_context(self, prof, **k):
                launched.append((prof, k.get("proxy")))
                return Ctx()
        saved = {}
        sites = [("gl_sinchew", "星洲", lambda pg: [{"title": "t", "url": "https://www.sinchew.com.my/news/1"}]),
                 ("hk_lifestyleasia", "Lifestyle Asia", lambda pg: [{"title": "t", "url": "https://www.lifestyleasia.com/hk/x/"}])]
        with mock.patch.object(browser_fetch, "catalogue_sites", return_value=sites), \
                mock.patch.object(browser_fetch, "sync_playwright", return_value=PW()), \
                mock.patch.object(browser_fetch.nomedia, "block"), \
                mock.patch.object(browser_fetch.resi, "playwright", return_value={"server": "http://127.0.0.1:1"}) as pw, \
                mock.patch.object(browser_fetch.corpus, "file_put", side_effect=lambda k, v: saved.update({k: v})):
            browser_fetch.main(catalogue=True)
        self.assertEqual(launched[0][1], None)
        self.assertIn("browser-profile-sites-hk", launched[1][0])  # its own profile (chrome_state: a temporary folder)
        self.assertEqual(launched[1][1], {"server": "http://127.0.0.1:1"})
        self.assertEqual(pw.call_args.args[0], "hk")
        self.assertTrue(saved[browser_fetch.CATALOGUE_DOC]["sites"]["hk_lifestyleasia"]["ok"])


FLAMING = json.dumps({"totalRecords": 2, "list": [
    {"id": 156704, "title": "法國財政風險成核心變量", "createDate": "2026-10-23 14:46", "columnName": "├ 外匯",
     "content": "<p>匯通財經APP訊——周五亞洲時段。</p><p>第二段。</p>"},
    {"id": 156703, "title": " ", "createDate": "2026-10-23 14:45", "content": ""}]})


class Parsers(unittest.TestCase):
    def test_flamingwheels(self):
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=FLAMING) as f:
            rows = sites_fetch.read(SITES["hk_flamingwheels"])
        self.assertIn(b"columnId=-1", f.call_args.kwargs["data"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://www.flamingwheels.online/m/20261023/156704.htm")
        self.assertEqual(rows[0]["time"], "2026-10-23T14:46:00+08:00")
        self.assertEqual(rows[0]["section"], "外匯")
        self.assertEqual(rows[0]["text"], "匯通財經APP訊——周五亞洲時段。\n第二段。")

    def test_line_today(self):
        page = {"modules": [{"type": "AD", "source": "AD", "listings": [{"id": "ad"}]},
                            {"type": "HEADLINE", "source": "LISTING", "listings": [{"id": "L1"}]},
                            {"type": "THUMBNAIL_LIST", "source": "CATEGORY_MOST_VIEW", "listings": [{"id": "module:x"}]}]}
        tab = {"modules": [{"type": "THUMBNAIL_LIST", "source": "LISTING", "name": "最新訊息(QUERY)", "listings": [{"id": "L2"}, {"id": "L1"}]}]}
        l1 = {"items": [{"title": "選委會抽籤", "publisher": "自由電子報", "publishTimeUnix": 1792756219000,
                         "url": {"hash": "LXe7qZ0"}, "categoryName": "政治"}]}
        l2 = {"items": [{"title": "選委會抽籤", "url": {"hash": "LXe7qZ0"}}, {"title": "颱風", "url": {"hash": "Nv5PO3k"}},
                        {"title": "", "url": {"hash": "x"}}]}
        answers = {"webapi/portal/page/top": json.dumps(page), "webapi/portal/page/": json.dumps(tab),
                   "listings/L1": json.dumps(l1), "listings/L2": json.dumps(l2)}
        with mock.patch.object(sites_fetch.sources, "fetch", side_effect=at(answers)):
            rows = sites_fetch.read(SITES["tw_linetoday"])
        self.assertEqual({r["url"] for r in rows}, {"https://today.line.me/tw/v3/article/LXe7qZ0",
                                                    "https://today.line.me/tw/v3/article/Nv5PO3k"})
        first = next(r for r in rows if r["url"].endswith("LXe7qZ0"))
        self.assertEqual(first["author"], "自由電子報")
        self.assertEqual(sources.parse_time(first["time"]).astimezone(HKT).isoformat(), "2026-10-23T19:50:19+08:00")
        self.assertTrue(SITES["tw_linetoday"].get("aggregator"))

    def test_pressreader(self):
        def api(path, token=None):
            if path.startswith("catalog/v2/publications/25x3"):
                return {"latestIssue": {"key": "25x32026102300000000001001"}}
            if path.startswith("toc/?issue=25x32026102300000000001001"):
                return {"Pages": [{"SectionName": "要聞", "Articles": [{"Id": 281487873337962, "Title": "第七批重點企落戶",
                                                                       "Byline": "記者 萬霜靈"}]},
                                  {"SectionName": "要聞", "Articles": [{"Id": 281487873337962, "Title": "第七批重點企落戶"}]}]}
            raise AssertionError(path)
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=json.dumps({"bearerToken": "tok"})), \
                mock.patch.object(se, "pressreader_get", side_effect=api):
            rows = sites_fetch.read(SITES["hk_pressreader_wenweipo"])
        self.assertEqual(rows, [{"title": "第七批重點企落戶", "time": "2026-10-23", "section": "要聞", "author": "記者 萬霜靈",
                                 "url": "https://www.pressreader.com/hong-kong/wen-wei-po-1/20261023/281487873337962"}])
        self.assertEqual(se._pr_token["token"], "tok")

    def test_student_hk(self):
        ms = 1792742400000
        sign = se.student_hk_sign("newNews/getNewsColumnData", ms)
        n, h = list(sign), len(sign) // 2  # the swaps undone in the other order give the base64 of the request
        for c in (3, 8, 2, 6):
            for i in range(0, h, c):
                n[i], n[h + i] = n[h + i], n[i]
        self.assertEqual(json.loads(base64.b64decode("".join(n))),
                         {"url": "newNews/getNewsColumnData", "method": "post", "timestamp": ms})
        answer = json.dumps({"success": True, "data": [{"total": 2, "list": [
            {"id": "WS6adab6d8c1f493b388cd5bfc", "title": "DSE2027｜個人陳述", "pubDate": "2026-10-22 09:00:00",
             "content": "<p>寫個人陳述。</p>", "mapProperties": {"authorValue": "學友社"}}]}]})
        sent = []
        with mock.patch.object(sites_fetch.sources, "fetch", side_effect=AssertionError("the home page is not read")), \
                mock.patch.object(se, "fetch", side_effect=lambda u, data=None, headers=None: sent.append(headers) or answer):
            rows = sites_fetch.read(SITES["hk_studenthk"])
        self.assertTrue(sent and all(h.get("sign") for h in sent))
        self.assertEqual(rows[0]["time"], "2026-10-22T09:00:00+08:00")
        self.assertEqual(rows[0]["text"], "寫個人陳述。")
        self.assertTrue(rows[0]["url"].startswith("https://www.student.hk/commonDetial?rid=WS6adab6d8c1f493b388cd5bfc&columnId="))

    def test_smg_lists_from_two_days_back_with_the_story(self):
        answer = json.dumps([{"id": 1093, "startdate": "2026-10-23 10:00:00", "translations": {"zh_TW": {
            "title": "氣象小主播大賽研學活動圓滿舉辦", "content": "<p>2026粵港澳氣象小主播大賽。</p>"}}}])
        urls = []
        with mock.patch.object(sites_fetch.sources, "fetch", side_effect=lambda u, **k: urls.append(u) or answer):
            rows = sites_fetch.read(SITES["mo_smg_news"])
        self.assertTrue(urls[0].endswith("datefrom=" + (datetime.now(HKT) - timedelta(days=2)).strftime("%Y-%m-%d")))
        self.assertEqual(rows[0]["url"], "https://www.smg.gov.mo/zh/news-detail/1093")
        self.assertEqual(rows[0]["time"], "2026-10-23T10:00:00+08:00")
        self.assertEqual(rows[0]["text"], "2026粵港澳氣象小主播大賽。")

    def test_cnyes_hao(self):
        answer = json.dumps({"payload": {"Root": {"Page_文章": {"List_文章": [
            {"文章Id": 273887, "標題": "財聞早知道", "長文發布時間": "2026-10-23T06:03:12Z", "RenderedPlain_內容": "納指重挫2.6%"}]}}}})
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=answer) as f:
            rows = sites_fetch.read(SITES["tw_cnyes_hao"])
        self.assertEqual(json.loads(f.call_args.kwargs["data"])["p2"], 1)
        self.assertEqual(rows, [{"title": "財聞早知道", "url": "https://hao.cnyes.com/post/273887",
                                 "time": "2026-10-23T06:03:12Z", "section": "", "text": "納指重挫2.6%"}])

    def test_huacheng_tradingview_moomoo_lists(self):
        hua = json.dumps({"list": [{"data": {"title": "財政部：前三季度財政收入", "publishTime": "2026-10-23 15:54:43",
                                             "url": "https://huacheng.gz-cmc.com/pages/2026/10/23/329f.html"}}]})
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=hua):
            rows = sites_fetch.read(SITES["cn_huacheng_tianxia"])
        self.assertEqual((rows[0]["url"], rows[0]["time"]), ("https://huacheng.gz-cmc.com/pages/2026/10/23/329f.html",
                                                             "2026-10-23T15:54:43+08:00"))
        tv = json.dumps({"items": [{"title": "機器人板塊再現主升浪", "published": 1792741238,
                                    "storyPath": "/news/gelonghui:fb97811e3acdf:0/"}]})
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=tv):
            rows = sites_fetch.read(SITES["gl_tradingview_zh"])
        self.assertEqual(rows[0]["url"], "https://tw.tradingview.com/news/gelonghui:fb97811e3acdf:0/")
        mm = json.dumps({"data": {"list": [{"title": "胡塞武裝加大襲擊", "timestamp": 1792723020,
                                             "url": "https://www.moomoo.com/hant/news/post/1002108253/x"}]}})
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=mm) as f:
            rows = sites_fetch.read(SITES["gl_moomoo_news"])
        self.assertEqual(f.call_args.kwargs["headers"].get("x-news-site-lang"), "1")
        self.assertEqual(rows[0]["url"], "https://www.moomoo.com/hant/news/post/1002108253/x")

    def test_dealroom_and_mofa_patterns(self):
        dr = (r'''<script>window.__staticRouterHydrationData = JSON.parse("{\"feedNotes\":{\"items\":[{\"id\":165017,'''
              r'''\"title\":\"Xona raises $250M Series D\",\"slug\":\"xona-raises-250m-series-d\",\"text\":\"x\"}]}}")''')
        rows = sites_fetch.regex_list(SITES["gl_dealroom_news"])(dr)
        self.assertEqual((rows[0]["title"], rows[0]["url"]), ("Xona raises $250M Series D",
                                                              "https://app.dealroom.co/news/note/xona-raises-250m-series-d"))
        mofa = ('<h2 class="line-clamp-3" id="news-heading-D2E0">Joint Statement between the UAE and Singapore</h2>'
                '<div><p class="description">x</p></div><a class="aegov-link" href="/en/MediaHub/News/2026/10/21/UAE-Singapore" '
                'aria-labelledby="news-heading-D2E0">View Details</a>')
        with mock.patch.object(sites_fetch.sources, "fetch", return_value=mofa):
            rows = sites_fetch.read(SITES["gl_mofa_uae_en"])
        self.assertEqual((rows[0]["title"], rows[0]["url"]), ("Joint Statement between the UAE and Singapore",
                                                              "https://www.mofa.gov.ae/en/MediaHub/News/2026/10/21/UAE-Singapore"))


class Stories(unittest.TestCase):
    """cache_fetch.api_story: the pages that hold no story"""
    def test_tradingview(self):
        story = json.dumps({"published": 1792736838, "astDescription": {"type": "root", "children": [
            {"type": "p", "children": ["By Stella Qiu"]},
            {"type": "p", "children": ["A selloff ", {"type": "url", "children": ["in tech"]}, " lost steam."]}]}})
        asked = []
        with mock.patch.object(cache_fetch, "get", side_effect=lambda u, **k: asked.append(u) or story):
            text, _, _ = cache_fetch.fetch_one("https://www.tradingview.com/news/reuters.com,2026:newsml_L1N46905K:0-tech-selloff/")
        self.assertEqual(text, "By Stella Qiu\nA selloff in tech lost steam.")
        self.assertIn("id=tag%3Areuters.com%2C2026%3Anewsml_L1N46905K%3A0&lang=en", asked[0])
        with mock.patch.object(cache_fetch, "get", side_effect=lambda u, **k: asked.append(u) or story):
            cache_fetch.fetch_one("https://tw.tradingview.com/news/gelonghui:fb97811e3acdf:0/")
        self.assertIn("id=gelonghui%3Afb97811e3acdf%3A0&lang=zh-Hant", asked[-1])

    def test_moomoo_post(self):
        d = json.dumps({"data": {"data": {"timestamp": 1792723020, "source": "蓋世汽車網", "contentTrans": "<p>正文一。</p>"}}})
        with mock.patch.object(cache_fetch, "get", side_effect=lambda u, headers=None, **k: d if headers == {"x-news-site-lang": "1"} else ""):
            meta = {}
            text, author, _ = cache_fetch.fetch_one("https://www.moomoo.com/hant/news/post/1001528865", meta=meta)
        self.assertEqual((text, author), ("正文一。", "蓋世汽車網"))
        self.assertTrue(meta["published"].startswith("2026-10-23"))

    def test_line_today_article_names_its_original(self):
        d = json.dumps({"data": {"content": "<p>OpenAI 與藥物開發新創合作。</p>", "publisher": "科技新報",
                                 "publishTimeUnix": 1792756219000, "sourceUrl": "https://technews.tw/2026/10/23/x/"}})
        with mock.patch.object(cache_fetch, "get", return_value=d):
            meta = {}
            text, author, _ = cache_fetch.fetch_one("https://today.line.me/tw/v3/article/Nv5PO3k", meta=meta)
        self.assertEqual((text, author), ("OpenAI 與藥物開發新創合作。", "科技新報"))
        self.assertEqual(meta["original"], "https://technews.tw/2026/10/23/x/")  # the aggregator's original (follow.pointer)

    def test_pressreader_article(self):
        with mock.patch.object(se, "pressreader_get", return_value={"Articles": [{"Byline": "記者", "Blocks": ["第一段。", "第二段。"]}]}) as g:
            text, author, _ = cache_fetch.fetch_one("https://www.pressreader.com/hong-kong/wen-wei-po-1/20261023/281487873337962")
        self.assertIn("articles=281487873337962", g.call_args.args[0])
        self.assertEqual((text, author), ("第一段。\n第二段。", "記者"))

    def test_smg_article(self):
        d = json.dumps([{"startdate": "2026-10-16 10:00:00", "translations": {"zh_TW": {"content": "<p>新網站上線。</p>"}}}])
        with mock.patch.object(cache_fetch, "get", side_effect=lambda u, **k: d if "cms.smg.gov.mo/zh_TW/api/news/1089" in u else ""):
            meta = {}
            text, _, _ = cache_fetch.fetch_one("https://www.smg.gov.mo/zh/news-detail/1089", meta=meta)
        self.assertEqual(text, "新網站上線。")
        self.assertEqual(meta["published"], "2026-10-16T10:00+08:00")


if __name__ == "__main__":
    unittest.main()
