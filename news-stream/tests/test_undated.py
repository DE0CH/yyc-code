#!/usr/bin/env python3
"""Undated and old posts never reach 串流 or Telegram as today's news (the POL incident of 14 Oct 2026: two am730·Ray
Online columns of 5 and 6 Oct, listed as "1星期前", went out as new). On a throwaway database (TEST_DATABASE_URL; its
tables are dropped and made again), the model and every page stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_undated.py

Covers:
  - am730's column list: every relative time it prints (分鐘/小時/日/星期/個月/年前) gives a time; 熱血時報's
    MM-DD-YYYY URLs give the right day (not a day in December); a publish time more than 12 hours after the post was
    seen is not kept.
  - the publish time a page states (JSON-LD datePublished, article:published_time, a <time class=published>,
    China Daily's "Published: 16:55, October 14, 2026", HKET's content API for its JavaScript-only pages) fills a post
    that has none, when its full text is read (fulltext_now and the 30-minute job); a page without one takes the date
    of a dated post of the same article.
  - the scorer holds a post with no publish time until its page has been tried; a page date older than 2 days keeps it
    out of 串流 and Telegram; with no date anywhere it is filed on its seen time, unless it is from its source's first
    run.
  - a source's first run (its first posts in the corpus) is scored for 數據分析 but never filed or sent, except an
    explicit back-post (streams.file(…, backpost=True)).
  - an item whose full-text rescore fell under the threshold stays filed and is sent; one sent before its rescore
    stays too (filed ⇒ member, nothing recalled: tests/test_one_membership.py).
  - fulltext_fetch.backfill_dates() fills existing undated (and future-dated) posts from their pages without
    re-scoring, re-filing or re-sending anything.
"""
import json, os, re, sys, tempfile, unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, cache_fetch, corpus, fulltext_fetch, fulltext_now, insights, sources, sources_extra, streams  # noqa: E402
import telegram_alerts  # noqa: E402

STREAMS = app.STREAMS
NOW = datetime.now(app.HKT)
HKT = app.HKT


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    """李家超 in a post → CEHK 90; anything else unrelated"""
    if purpose not in ("scoring", "scoring_backfill"):
        return {"tags": []}
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i in re.findall(r"(?m)^(\d+)\. \[", body):
        block = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        block = block.split(f"\n{int(i) + 1}. ", 1)[0]
        st = [{"id": "CEHK", "score": 90, "reason": "特首", "category": "言論及表態"}] if "李家超" in block else []
        out.append({"i": int(i), "sentiment": "neutral", "labels": [], "streams": st})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, None)  # stage 2 answers as stage 1 (tests/stage2.py)
streams.WAIT_TEXT = timedelta(0)
SENT = []
telegram_alerts.post = lambda chat, it, sid=None: SENT.append(it["title"])
telegram_alerts.GAP = 0
TG = [{"id": "CEHK", "telegram_chat": -1}]


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"x": {"ok": True, "items": len(items), "error": None}})
    sources.discuss_search = lambda qs: []
    app.after_poll = lambda g, f: insights.after_poll(g, f)
    app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
    app.load_state()
    app.poll_once()


def post(url, title, text="短", sid="rthk", **kw):
    return dict({"sid": sid, "source": "RTHK", "title": title, "url": url, "published": NOW, "text": text}, **kw)


def pid(url):
    return corpus.ids_for_urls([url])[url]


def row(url):
    return corpus.db().execute("SELECT * FROM posts WHERE url=%s", (url,)).fetchone()


def items(stream="CEHK"):
    return [r[0] for r in corpus.db().execute("SELECT item FROM stream_items WHERE stream=%s ORDER BY id", (stream,))]


def start_sending():
    """Telegram's cursor at the newest item: only what is filed from here on is sent"""
    corpus.kv_set(telegram_alerts.KEY, {"CEHK": corpus.db().execute(
        "SELECT coalesce(max(id), 0) FROM stream_items WHERE stream='CEHK'").fetchone()[0]})
    SENT.clear()


def page_fetch(text, published=None):
    """cache_fetch.fetch_one stubbed: the page's text and the publish time it states"""
    def f(url, meta=None):
        if meta is not None and published:
            meta["published"] = published
        return text, None, {}
    return f


def drain_queue():
    with fulltext_now.Q.mutex:
        q = list(fulltext_now.Q.queue)
        fulltext_now.Q.queue.clear()
    for job in q:
        fulltext_now.fetch(*job)


AM730_LIST = """<ul>
<li><h6 class="newsitem-title"> <a href="/本地/1056768/ray-online-a">港台推出新媒體平台｜Ray Online</a></h6>
  <span class="newsitem-time">1星期前</span></li>
<li><h6 class="newsitem-title"> <a href="/本地/1050001/ray-online-b">舊文｜Ray Online</a></h6>
  <span class="newsitem-time">3個月前</span></li>
<li><h6 class="newsitem-title"> <a href="/本地/1040001/ray-online-c">更舊｜Ray Online</a></h6>
  <span class="newsitem-time">1年前</span></li>
<li><h6 class="newsitem-title"> <a href="/本地/1058001/ray-online-d">今日｜Ray Online</a></h6>
  <span class="newsitem-time">5小時前</span></li>
<li><h6 class="newsitem-title"> <a href="/本地/1057001/ray-online-e">前日｜Ray Online</a></h6>
  <span class="newsitem-time">2日前</span></li>
<li><h6 class="newsitem-title"> <a href="/本地/1058002/ray-online-f">剛剛｜Ray Online</a></h6>
  <span class="newsitem-time">12分鐘前</span></li>
</ul>"""


class A_Parsers(unittest.TestCase):
    def test_am730_relative_times(self):
        got = {x["title"]: x["time"] for x in sources_extra.am730_column("Ray Online")(AM730_LIST)}
        ago = {t: NOW - datetime.fromisoformat(v) for t, v in got.items()}
        self.assertTrue(all(got.values()), got)  # every relative time is read
        days = {t: round(v.total_seconds() / 86400) for t, v in ago.items()}
        self.assertEqual(days["港台推出新媒體平台"], 7)
        self.assertGreaterEqual(days["舊文"], 89)
        self.assertGreaterEqual(days["更舊"], 364)
        self.assertEqual(round(ago["今日"].total_seconds() / 3600), 5)
        self.assertEqual(days["前日"], 2)
        self.assertEqual(round(ago["剛剛"].total_seconds() / 60), 12)

    def test_passiontimes_month_first(self):
        self.assertEqual(sources.parse_time("10-14-2026").strftime("%Y-%m-%d"), "2026-10-14")
        self.assertEqual(sources.parse_time("10-05-2026").strftime("%Y-%m-%d"), "2026-10-05")  # not 10 May

    def test_page_dates(self):
        pd = cache_fetch.page_date
        self.assertEqual(pd('<meta property="article:published_time" content="2026-10-14T18:24:03+0800">'),
                         "2026-10-14T18:24+08:00")  # am730
        self.assertEqual(pd('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":'
                            '"2026-10-14T09:47:59.000Z"}</script>'), "2026-10-14T17:47+08:00")  # Nikkei Asia
        self.assertEqual(pd('<script type="application/ld+json">[{"@graph":[{"datePublished": "2026-10-14"}]}]</script>'),
                         "2026-10-14")  # 晴報: a day only (the bare day, tests/test_date_only.py)
        self.assertEqual(pd('<time class="published" datetime="10-14-2026">'), "2026-10-14")  # 熱血時報
        self.assertEqual(pd('<div><span>Published: 16:55, October 14, 2026</span></div>'),
                         "2026-10-14T16:55+08:00")  # China Daily HK
        self.assertEqual(pd('<meta itemprop="datePublished" content="2026-10-13 07:00:00">'), "2026-10-13T07:00+08:00")
        self.assertIsNone(pd("<html><body>沒有日期</body></html>"))
        self.assertIsNone(pd('<meta property="article:published_time" content="2099-01-01T00:00:00+08:00">'))  # future

    def test_hket_js_pages_through_its_api(self):
        calls = []

        def get(url, proxy=None, headers=None, chrome=False):
            calls.append((url, (headers or {}).get("Origin")))
            return json.dumps({"id": "4209080", "displayDate": "17:39 2026/10/14", "headline": {"main": "北水"},
                               "content": {"lead": "", "nonPaywall": "<p>《經濟通通訊社14日專訊》今日恆指低開62點。</p>"
                                                                     "<p>北水全日淨流出1.35億元。</p>"}})
        old, cache_fetch.get = cache_fetch.get, get
        try:
            meta = {}
            text, _, _ = cache_fetch.fetch_one("https://invest.hket.com/article/4209080/北水", meta=meta)
            self.assertIn("北水全日淨流出", text)
            self.assertEqual(meta["published"], "2026-10-14T17:39+08:00")
            self.assertEqual(calls[-1], ("https://data02.hket.com/content?id=4209080&channel=invest", "https://invest.hket.com"))
            cache_fetch.fetch_one("https://ps.hket.com/article/4209096/私宅?mtc=20023", meta={})
            self.assertEqual(calls[-1], ("https://data02.hket.com/content?id=4209096&channel=epc", "https://ps.hket.com"))
        finally:
            cache_fetch.get = old


class B_Flow(unittest.TestCase):
    """in order: the corpus has an established source (rthk) before the scorer's switch"""

    @classmethod
    def setUpClass(cls):
        poll([post("https://news.test/0", "開站前的新聞", sid="rthk"), post("https://col.test/0", "專欄舊文", sid="col_x")])
        streams.settings()  # the switch: posts after this are scored
        fulltext_now.cache_fetch.fetch_one = page_fetch("")
        drain_queue()

    def test_1_future_time_not_kept(self):
        poll([post("https://news.test/future", "未來的新聞", published=NOW + timedelta(days=2)),
              post("https://news.test/soon", "幾分鐘後", published=NOW + timedelta(minutes=10))])
        self.assertIsNone(row("https://news.test/future")["published"])
        self.assertIsNotNone(row("https://news.test/soon")["published"])  # a clock a few minutes ahead is fine
        drain_queue()

    def test_2_old_column_listed_undated(self):
        """the incident: a column the list shows as "1星期前" (here undated: the list gave nothing), headline about
        李家超; its page says 5 Oct → scored for 數據分析 at most, never 串流 or Telegram"""
        start_sending()
        url = "https://www.am730.com.hk/本地/1056768/ray-online"
        poll([post(url, "李家超的一星期｜Ray Online", sid="col_x", published=None)])
        self.assertNotIn(url, {p["url"] for p in streams.due(50)})  # held: no date, page not tried yet
        fulltext_now.cache_fetch.fetch_one = page_fetch("李家超上周出席活動。" * 60, "2026-10-05T08:00+08:00")
        drain_queue()
        self.assertEqual(row(url)["published"], "2026-10-05T08:00+08:00")  # the page's date
        self.assertEqual(row(url)["day"], "2026-10-05")
        streams.run_once(STREAMS)
        self.assertNotIn(url, [x["url"] for x in items()])
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, [])

    def test_3_undated_with_no_date_anywhere(self):
        """no date in the list or the page: filed on its seen time once the page has been tried"""
        start_sending()
        url = "https://news.test/nodate"
        poll([post(url, "李家超今日見記者", published=None)])
        self.assertNotIn(url, {p["url"] for p in streams.due(50)})
        fulltext_now.cache_fetch.fetch_one = page_fetch("李家超今日見記者，談及施政。" * 50)
        drain_queue()
        self.assertIsNone(row(url)["published"])
        self.assertIsNotNone(row(url)["cached_at"])
        self.assertIn(url, {p["url"] for p in streams.due(50)})
        streams.run_once(STREAMS)
        self.assertIn(url, [x["url"] for x in items()])
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, ["李家超今日見記者"])

    def test_4_undated_long_feed_text_still_reads_the_page_for_its_date(self):
        url = "https://news.test/longfeed"
        poll([post(url, "李家超長文", text="李家超" + "字" * 800, published=None)])
        self.assertNotIn(url, {p["url"] for p in streams.due(50)})
        fulltext_now.cache_fetch.fetch_one = page_fetch("短頁", "2026-10-06T10:00+08:00")
        drain_queue()
        r = row(url)
        self.assertEqual(r["published"], "2026-10-06T10:00+08:00")
        self.assertTrue(r["text"].startswith("李家超字"))  # the longer feed text is kept

    def test_5_sibling_date(self):
        """a page with no date: a dated post of the same article (same URL after cleanup) gives it"""
        # (another host form: the poll stores addresses without tracking parameters, so ?utm_source=x would be the same post)
        poll([post("https://m.sib.test/a/1", "同一篇", published=NOW - timedelta(hours=3))])
        drain_queue()
        url = "https://www.sib.test/a/1"
        poll([post(url, "同一篇（另一來源）", published=None, sid="rthk")])
        fulltext_now.cache_fetch.fetch_one = page_fetch("")
        drain_queue()
        self.assertEqual(row(url)["published"], row("https://m.sib.test/a/1")["published"])

    def test_6_first_run_is_backlog(self):
        """a new source's first read is backlog (Deyao, 2026-10-27: only a manual run processes a backlog): not scored,
        not in 數據分析, never 串流 or Telegram; its second read is live"""
        start_sending()
        poll([post("https://new.test/1", "李家超新來源首批", sid="brand_new", text="李家超" * 200),
              post("https://new.test/0", "李家超新來源首批（無日期）", sid="brand_new", text="李家超" * 200, published=None)])
        self.assertTrue(row("https://new.test/1")["first_run"])
        fulltext_now.cache_fetch.fetch_one = page_fetch("")
        drain_queue()
        streams.run_once(STREAMS)
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM scores WHERE post_id IN (%s, %s)",
                                             (pid("https://new.test/1"), pid("https://new.test/0"))).fetchone()[0], 0)
        self.assertNotIn(pid("https://new.test/1"), insights.members("CEHK", insights.today(), insights.today()))
        self.assertFalse({"https://new.test/1", "https://new.test/0"} & {x["url"] for x in items()})  # not 串流
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, [])
        poll([post("https://new.test/2", "李家超新來源第二輪", sid="brand_new", text="李家超" * 200)])  # its second run
        self.assertFalse(row("https://new.test/2")["first_run"])
        streams.run_once(STREAMS)
        self.assertIn("https://new.test/2", [x["url"] for x in items()])
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, ["李家超新來源第二輪"])

    def test_7_explicit_backpost(self):
        """a back-post Deyao asked for (e.g. a new stream's history) files first-run or old posts on purpose"""
        start_sending()
        poll([post("https://hist.test/1", "李家超舊聞補發", sid="brand_new_2", text="李家超" * 200,
                   published=NOW - timedelta(days=30))])
        p = dict(row("https://hist.test/1"))
        p["hash"] = "x"
        res = streams.score_posts(STREAMS, [p])
        streams.file(STREAMS, [p], res)
        self.assertNotIn("https://hist.test/1", [x["url"] for x in items()])
        streams.file(STREAMS, [p], res, backpost=True)
        it = [x for x in items() if x["url"] == "https://hist.test/1"]
        self.assertEqual(len(it), 1)
        self.assertTrue(it[0]["backpost"])
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, ["李家超舊聞補發"])

    def test_8_rescore_below_threshold_before_send(self):
        """filed at 90, rescored under the threshold before Telegram got to it: it stays filed and is sent (Deyao,
        2026-10-15: filed ⇒ member, nothing recalled)"""
        start_sending()
        url = "https://news.test/rescored"
        poll([post(url, "李家超出席", text="李家超" + "字" * 600)])
        streams.run_once(STREAMS)
        self.assertIn(url, [x["url"] for x in items()])
        with corpus.db() as c:
            c.execute("UPDATE posts SET title=%s, text=%s WHERE url=%s", ("一般新聞", "天氣" * 300, url))
        streams.run_once(STREAMS)  # rescored: no CEHK score any more
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, ["李家超出席"])
        self.assertIn(url, [x["url"] for x in items()])

        start_sending()  # an item already sent and then rescored out stays
        url2 = "https://news.test/sent-then-rescored"
        poll([post(url2, "李家超再出席", text="李家超" + "字" * 600)])
        streams.run_once(STREAMS)
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, ["李家超再出席"])
        with corpus.db() as c:
            c.execute("UPDATE posts SET title=%s, text=%s WHERE url=%s", ("一般新聞二", "天氣" * 300, url2))
        streams.run_once(STREAMS)
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, ["李家超再出席"])
        self.assertIn(url2, [x["url"] for x in items()])

    def test_9_backfill_dates(self):
        """existing undated and future-dated posts get the date their page states; nothing is re-scored, re-filed or
        re-sent"""
        start_sending()
        with corpus.db() as c:  # rows as the old code left them
            c.execute("UPDATE posts SET published=NULL WHERE url='https://news.test/0'")
            c.execute("UPDATE posts SET published='2026-12-10T00:00+08:00', day='2026-12-10' WHERE url='https://col.test/0'")
        p0, p1 = pid("https://news.test/0"), pid("https://col.test/0")
        scored = {r[0]: r[1] for r in corpus.db().execute("SELECT post_id, hash FROM scored")}
        n_items = corpus.db().execute("SELECT count(*) FROM stream_items").fetchone()[0]
        pages = {"https://news.test/0": "2026-10-13T09:00+08:00", "https://col.test/0": "2026-10-12T11:00+08:00"}

        def fetch(url, meta=None):
            if url in pages:
                meta["published"] = pages[url]
            return "頁面文字", None, {}
        fulltext_fetch.cache_fetch.fetch_one = fetch
        got = fulltext_fetch.backfill_dates()
        self.assertGreaterEqual(got["dated"], 2)
        self.assertEqual(row("https://news.test/0")["published"], "2026-10-13T09:00+08:00")
        self.assertEqual(row("https://news.test/0")["day"], "2026-10-13")
        self.assertEqual(row("https://col.test/0")["published"], "2026-10-12T11:00+08:00")
        # a page time after the post was first seen is an update time (HKET's displayDate), not its publish time
        with corpus.db() as c:
            c.execute("UPDATE posts SET published=NULL, seen='2026-10-12T19:45:00+08:00' WHERE url='https://news.test/nodate'")
        pages["https://news.test/nodate"] = "2026-10-14T18:00+08:00"
        fulltext_fetch.backfill_dates()
        self.assertIsNone(row("https://news.test/nodate")["published"])
        self.assertFalse(corpus.set_published(pid("https://news.test/nodate"), "2026-10-14T18:00+08:00"))
        self.assertEqual(row("https://news.test/0")["text"], "短")  # dates only: the text is left alone
        self.assertEqual({r[0]: r[1] for r in corpus.db().execute("SELECT post_id, hash FROM scored")}, scored)
        self.assertEqual(streams.due(50), [])  # nothing re-scored
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM stream_items").fetchone()[0], n_items)
        telegram_alerts.send_due(TG)
        self.assertEqual(SENT, [])


if __name__ == "__main__":
    unittest.main()
