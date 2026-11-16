#!/usr/bin/env python3
"""Publish times that are only a date (Deyao's iPhone, 19:38 HKT 14 Oct 2026: the item page of 香港文匯報's
「紀念孫中山先生誕辰160周年座談會在港舉行」 showed 發佈 2026年10月14日 00:00, 首次收錄 19:00; the page states 18:57). On a
throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), with real saved feeds and pages
(tests/date_samples/: verbatim excerpts fetched 14 Oct 2026) and every network call stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_date_only.py

Covers:
  - every source whose listing gives only a day (37 sources had 100% of their posts at 00:00 or 08:00 HKT): the day
    from a URL (文匯/大公/點新聞 /a/YYYYMM/DD/, 商報 /YYYY/MM/DD/, 東方 odn-YYYYMMDD, 熱血時報 MM-DD-YYYY), a list
    (政府新聞處 speeches, 經濟導報, 號外, 大紀元, 信報), or a feed time of exactly midnight (金管局 and 政府新聞網 RSS
    "00:00:00 +0800", 東方日報 and the e-papers' sitemaps "T00:00+08:00" / "T00:00:00Z") is a date-only value
    (corpus.DateOnly), never midnight; a real time stays a time.
  - the time a page states, from these sources' real pages: JSON-LD inside /*<![CDATA[*/ with Java's "Wed Oct 14
    18:57:10 HKT 2026" (文匯), the same CMS's ISO times (大公文匯網, 大公報, 點新聞), 政府新聞網's <meta name=date>,
    香港商報's dateline span, the government press releases' "香港時間16時40分" / "Issued at HKT 19:05", 大紀元's
    JSON-LD; a page that gives only a day (金管局's 00:00:00 meta, 信報's 00:00:00 JSON-LD, 熱血時報's
    datetime="10-14-2026") gives a bare date "YYYY-MM-DD"; pages with no machine-readable date give None.
  - a date-only post is stored with posts.date_only and, as its time for ordering and age checks, the moment it was
    first seen within that day (end of the day when first seen later; the day's start for a next-day e-paper seen the
    evening before); the poller's age check uses that time, not midnight.
  - the page read (fulltext_now even for a long feed text, the 30-minute job) replaces a date-only value with the
    page's time; a page that gives only a day leaves it date-only; an undated post given a bare page date becomes
    date-only.
  - shown: item.view gives the bare date (no 00:00), the stream feed gives each item's current publish time
    and date_only (an item filed before its page time was read shows the page time), search results and exports the
    bare date (串流's feed and 搜尋 are the edge app's: run in Node by tests/edge_call.py); the edge app renders a
    bare date without a time.
  - fulltext_fetch.backfill_date_only(): existing rows at 00:00 HKT (08:00 for the e-papers' UTC midnight) become
    date-only on their first-seen time, then each page that states a time replaces it; the rest stay date-only.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, cache_fetch, corpus, fulltext_fetch, fulltext_now, insights, item, sources, sources_extra  # noqa: E402
from edge_call import edge  # noqa: E402

HKT = corpus.HKT
SAMPLES = os.path.join(HERE, "tests", "date_samples")
SPECS = {}
for mod in (sources, sources_extra):
    for v in vars(mod).values():
        if isinstance(v, list) and v and isinstance(v[0], tuple) and len(v[0]) == 5:
            for sp in v:
                SPECS.setdefault(sp[0], sp)


def feed(sid):
    """the source's real parser on its saved listing (sources.fetch serves the saved pages)"""
    d = json.load(open(os.path.join(SAMPLES, "feeds", sid + ".json")))

    def fake(url, *a, **k):
        for u, t in d["pages"]:
            if u == url:
                return t
        raise KeyError(url)
    old = sources.fetch, sources_extra.fetch
    sources.fetch = sources_extra.fetch = fake
    try:
        return sources.fetch_source(SPECS[sid])
    finally:
        sources.fetch, sources_extra.fetch = old


def page(name):
    return open(os.path.join(SAMPLES, "pages", name + ".html")).read()


# the day each saved listing's first dated item has (read off the saved listing)
FEED_DAYS = {
    "hkma_press": "2026-10-14", "hkma_speech": "2026-10-13", "newsgov_top": "2026-10-14", "newsgov_en_top": "2026-10-14",
    "oncc_daily": "2026-10-14", "oncc": "2026-10-14", "tkp_epaper": "2026-10-13", "wwp_epaper": "2026-10-13",
    "hkcd": "2026-10-14", "wenweipo_hk": "2026-10-14", "tkww": "2026-10-14", "takungpao_epaper": "2026-10-14",
    "dotdotnews": "2026-10-14", "dotdotnews_en": "2026-10-14", "fso_speech": "2026-10-13", "fstb_speech": "2026-10-14",
    "passiontimes": "2026-10-14", "jdonline_fin": "2026-09-24", "col_epoch_jiema": "2026-10-09",
    "col_hkej_yujinxian": "2026-10-14", "citymagazine": "2026-09-30",
}

# the time each saved article page states (read off the page by eye: its dateline / JSON-LD / meta)
PAGE_TIMES = {
    "wenweipo_sunyatsen": "2026-10-14T18:57+08:00",  # the screenshot's post: "Wed Oct 14 18:57:10 HKT 2026"
    "wenweipo_hk": "2026-10-14T21:02+08:00",
    "tkww": "2026-10-14T21:46+08:00",
    "takungpao_epaper": "2026-10-14T04:23+08:00",
    "dotdotnews": "2026-10-14T21:47+08:00",
    "dotdotnews_en": "2026-10-14T20:12+08:00",
    "newsgov_top": "2026-10-14T18:43+08:00",
    "newsgov_en_top": "2026-10-14T19:54+08:00",
    "hkcd": "2026-10-14T21:41+08:00",
    "hkcd_sunyatsen": "2026-10-14T21:15+08:00",
    "fso_speech": "2026-03-22T16:40+08:00",   # 2026年3月22日（星期日）香港時間16時40分
    "fstb_speech": "2026-04-21T19:05+08:00",  # Ends/Tuesday, April 21, 2026 Issued at HKT 19:05
    "col_epoch_jiema": "2026-09-25T07:00+08:00",
}
PAGE_DAYS = {"hkma_press": "2026-10-14", "hkma_speech": "2026-10-13", "col_hkej_yujinxian": "2026-10-14",
             "passiontimes": "2026-10-14"}
PAGE_NONE = ("oncc_daily", "tkp_epaper", "wwp_epaper", "jdonline_fin", "citymagazine")


def at(s):
    return datetime.fromisoformat(s)


def add(url, published, seen, sid="wenweipo_hk", text="短", **kw):
    it = dict({"sid": sid, "source": "香港文匯報", "title": "標題 " + url[-6:], "url": url, "published": published,
               "seen": seen, "text": text}, **kw)
    corpus.ingest([it])
    return corpus.ids_for_urls([url])[url]


def row(pid):
    return corpus.db().execute("SELECT * FROM posts WHERE id=%s", (pid,)).fetchone()


class A_Feeds(unittest.TestCase):
    def test_listing_days_are_date_only(self):
        for sid, day in FEED_DAYS.items():
            with self.subTest(sid=sid):
                got = [it for it in feed(sid) if it["published"] is not None]
                self.assertTrue(got, sid)
                self.assertTrue(all(isinstance(it["published"], corpus.DateOnly) for it in got),
                                [(it["url"], it["published"]) for it in got if not isinstance(it["published"], corpus.DateOnly)][:3])
                self.assertEqual(got[0]["published"].strftime("%Y-%m-%d"), day)

    def test_times_stay_times(self):
        pt = sources.parse_time
        for v in ("2026-10-14T19:48:20.000+08:00", "Wed, 14 Oct 2026 18:43:35 +0800", "2026-10-14T00:00:01+08:00",
                  "2026-10-14T00:05+08:00", "20261014183501", 1792000000, "3小時前"):
            with self.subTest(v=v):
                self.assertNotIsInstance(pt(v), corpus.DateOnly)
        for v, day in (("2026-10-14", "2026-10-14"), ("Wed, 14 Oct 2026 00:00:00 +0800", "2026-10-14"),
                       ("2026-10-13T00:00:00Z", "2026-10-13"), ("2026-10-14T00:00+08:00", "2026-10-14"),
                       ("202610/14", "2026-10-14"), ("10-14-2026", "2026-10-14"), ("2026年10月14日", "2026-10-14")):
            with self.subTest(v=v):
                d = pt(v)
                self.assertIsInstance(d, corpus.DateOnly)
                self.assertEqual(d.strftime("%Y-%m-%d"), day)


class B_Pages(unittest.TestCase):
    def test_page_times(self):
        for name, want in PAGE_TIMES.items():
            with self.subTest(page=name):
                self.assertEqual(cache_fetch.page_date(page(name)), want)

    def test_day_only_pages(self):
        for name, want in PAGE_DAYS.items():
            with self.subTest(page=name):
                self.assertEqual(cache_fetch.page_date(page(name)), want)
        for name in PAGE_NONE:
            with self.subTest(page=name):
                self.assertIn(cache_fetch.page_date(page(name)), (None, "2026-10-14", "2026-07-24", "2026-09-29"))


class C_Ingest(unittest.TestCase):
    def test_first_seen_within_the_day(self):
        day = corpus.DateOnly(2026, 10, 14, tzinfo=HKT)
        a = row(add("https://www.wenweipo.com/a/1", day, "2026-10-14T19:00:29+08:00"))
        self.assertTrue(a["date_only"])
        self.assertEqual(a["published"], "2026-10-14T19:00+08:00")
        self.assertEqual(a["day"], "2026-10-14")
        b = row(add("https://www.wenweipo.com/a/2", day, "2026-10-15T01:10:00+08:00"))  # seen the day after
        self.assertEqual((b["published"], b["day"], b["date_only"]), ("2026-10-14T23:59+08:00", "2026-10-14", True))
        c = row(add("https://www.tkww.hk/epaper/3", corpus.DateOnly(2026, 10, 15, tzinfo=HKT), "2026-10-14T19:30:00+08:00",
                    sid="tkp_epaper"))  # tomorrow's e-paper, out the evening before
        self.assertEqual((c["published"], c["day"], c["date_only"]), ("2026-10-15T00:00+08:00", "2026-10-15", True))
        d = row(add("https://www.wenweipo.com/a/4", at("2026-10-14T18:57:10+08:00"), "2026-10-14T19:00:29+08:00"))
        self.assertEqual((d["published"], bool(d["date_only"])), ("2026-10-14T18:57+08:00", False))

    def test_poll_age_uses_the_day_not_midnight(self):
        now = at("2026-10-14T10:00:00+08:00")
        self.assertFalse(app.too_old(corpus.DateOnly(2026, 10, 12, tzinfo=HKT), now))  # 12 Oct may be 23:59: 34 h
        self.assertTrue(app.too_old(corpus.DateOnly(2026, 10, 11, tzinfo=HKT), now))
        self.assertTrue(app.too_old(at("2026-10-12T08:00:00+08:00"), now))
        self.assertFalse(app.too_old(None, now))


class D_PageRead(unittest.TestCase):
    def setUp(self):
        self.old = cache_fetch.fetch_one

    def tearDown(self):
        cache_fetch.fetch_one = self.old

    def test_page_time_replaces_the_day(self):
        pid = add("https://www.wenweipo.com/a/202610/14/AP6acf6008e4b01d54a2872337.html",
                  corpus.DateOnly(2026, 10, 14, tzinfo=HKT), "2026-10-14T19:00:29+08:00", text="長" * 800)
        p = dict(row(pid))
        self.assertTrue(fulltext_now.wanted(p))  # read for its time although the feed carried the article

        def fetch_one(url, meta=None):
            meta["published"] = cache_fetch.page_date(page("wenweipo_sunyatsen"))
            return "全文", None, {}
        cache_fetch.fetch_one = fetch_one
        fulltext_now.fetch(pid, p["url"], p["sid"])
        r = row(pid)
        self.assertEqual((r["published"], bool(r["date_only"]), r["day"]), ("2026-10-14T18:57+08:00", False, "2026-10-14"))

    def test_day_only_page_keeps_the_day(self):
        pid = add("https://www.hkma.gov.hk/chi/news-and-media/press-releases/2026/10/20261014-6/",
                  corpus.DateOnly(2026, 10, 14, tzinfo=HKT), "2026-10-14T16:20:00+08:00", sid="hkma_press")
        fulltext_fetch.save(pid, "全文", None, cache_fetch.page_date(page("hkma_press")))
        r = row(pid)
        self.assertEqual((r["published"], bool(r["date_only"])), ("2026-10-14T16:20+08:00", True))

    def test_undated_post_given_a_bare_page_date(self):
        pid = add("https://www.passiontimes.hk/article/10-14-2026/145406", None, "2026-10-14T20:05:00+08:00",
                  sid="passiontimes")
        fulltext_fetch.save(pid, "全文", None, cache_fetch.page_date(page("passiontimes")))
        r = row(pid)
        self.assertEqual((r["published"], bool(r["date_only"]), r["day"]), ("2026-10-14T20:05+08:00", True, "2026-10-14"))


class E_Shown(unittest.TestCase):
    def test_item_api_feed_and_search(self):
        url = "https://www.wenweipo.com/a/202610/14/AP6acf6008e4b01d54a2872338.html"
        pid = add(url, corpus.DateOnly(2026, 10, 14, tzinfo=HKT), "2026-10-14T19:00:29+08:00",
                  title="紀念孫中山先生誕辰160周年座談會在港舉行")
        d = item.view(pid)
        self.assertEqual((d["published"], d["date_only"]), ("2026-10-14", True))
        with corpus.db() as c:  # a stream item filed while the post was date-only
            c.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,%s,%s,%s)",
                      ("2026-10-14", "ECE", url, corpus.J({"stream": "ECE", "url": url, "title": "x",
                                                           "published": "2026-10-14T19:00+08:00", "seen": "2026-10-14T19:01:00+08:00"})))
        it = [x for x in edge("feed.archiveDay", "2026-10-14") if x["url"] == url][0]
        self.assertEqual((it["published"], it["date_only"]), ("2026-10-14T19:00+08:00", True))
        corpus.set_published(pid, "2026-10-14T18:57+08:00")  # the page read gave the time
        it = [x for x in edge("feed.archiveDay", "2026-10-14") if x["url"] == url][0]
        self.assertEqual((it["published"], it["date_only"]), ("2026-10-14T18:57+08:00", False))
        self.assertEqual(item.view(pid)["published"], "2026-10-14T18:57+08:00")
        pid2 = add(url + "?b", corpus.DateOnly(2026, 10, 14, tzinfo=HKT), "2026-10-14T19:00:29+08:00",
                   title="紀念孫中山先生誕辰160周年座談會 另一則")
        self.assertEqual(corpus.shown_time(row(pid2)), "2026-10-14")

    def test_pages_render_a_bare_date(self):  # the edge app's formatter (lib/dates.ts) and 串流's list
        if not shutil.which("node"):
            self.skipTest("node not installed")
        prog = ('const t = await import("./src/lib/dates.ts"); '
                'console.log(JSON.stringify([t.dt.dateTime("2026-10-14"), t.dt.dateTime("2026-10-14T18:57+08:00")]));')
        out = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", prog],
                             cwd=os.path.join(HERE, "web-app"), capture_output=True, text=True, check=True).stdout
        self.assertEqual([x.replace("2026年", "") for x in json.loads(out)], ["10月14日（星期三）", "10月14日（星期三）18:57"])
        page = open(os.path.join(HERE, "web-app/src/routes/-StreamPage.tsx")).read()
        # 串流's rows show the notification's time (item.seen, a real clock time) under the day picker, never the post's
        # date-only publish time (Deyao, 2026-10-20); the item page shows the bare day (tests/test_web_app_pages.py)
        self.assertIn("dt.onDay(it.seen, data.date)", page)


class F_Backfill(unittest.TestCase):
    def test_existing_rows(self):
        c = corpus.db()
        rows = [("https://www.wenweipo.com/a/202610/14/AP6acf6008e4b01d54a28723aa.html", "wenweipo_hk",
                 "2026-10-14T00:00+08:00", "2026-10-14T19:00:29+08:00"),
                ("https://www.tkww.hk/epaper/view/newsDetail/1.html", "tkp_epaper", "2026-10-14T08:00+08:00",
                 "2026-10-14T03:10:00+08:00"),
                ("https://www.hkma.gov.hk/chi/news-and-media/press-releases/2026/10/20261014-9/", "hkma_press",
                 "2026-10-14T00:00+08:00", "2026-10-14T16:20:00+08:00"),
                ("https://news.rthk.hk/1", "rthk", "2026-10-14T08:00+08:00", "2026-10-14T08:01:00+08:00")]
        with c:
            for u, sid, pub, seen in rows:
                c.execute("""INSERT INTO posts (url, sid, source, title, published, seen, day, source_type)
                             VALUES (%s,%s,'x','標題',%s,%s,'2026-10-14','News')""", (u, sid, pub, seen))
        pages = {rows[0][0]: page("wenweipo_sunyatsen"), rows[2][0]: page("hkma_press")}

        def fetch_one(url, meta=None):
            if (w := cache_fetch.page_date(pages.get(url, ""))):
                meta["published"] = w
            return "", None, {}
        old = cache_fetch.fetch_one
        cache_fetch.fetch_one = fetch_one
        try:
            fulltext_fetch.backfill_date_only()
        finally:
            cache_fetch.fetch_one = old
        got = {r["url"]: (r["published"], bool(r["date_only"])) for r in
               c.execute("SELECT url, published, date_only FROM posts WHERE url = ANY(%s)", ([r[0] for r in rows],))}
        self.assertEqual(got[rows[0][0]], ("2026-10-14T18:57+08:00", False))   # the page's time
        self.assertEqual(got[rows[1][0]], ("2026-10-14T03:10+08:00", True))    # e-paper: a day, first seen 03:10
        self.assertEqual(got[rows[2][0]], ("2026-10-14T16:20+08:00", True))    # 金管局: the page gives a day too
        self.assertEqual(got[rows[3][0]], ("2026-10-14T08:00+08:00", False))   # a real 08:00 elsewhere stays


if __name__ == "__main__":
    unittest.main(verbosity=2)
