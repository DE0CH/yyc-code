#!/usr/bin/env python3
"""Complete data before AI, for alerts (Deyao, 2026-10-15): a news post is never scored, filed or sent on its headline
while its page can still be read. On a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again),
the model, the page reads and Telegram stubbed.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_fulltext_first.py

Covers (streams.due):
  - a news post with a short feed text waits for its page (cached_at), whoever reads it: fulltext_now.py on sight, or
    the 30-minute `fulltext` job (信報 and 香港討論區 through the proxy, the browser hosts); once read it is scored on
    the page text and filed on that score;
  - it is scored on the feed text only when the page truly can't be read: a host we never read (LinkedIn, the social
    platforms: cache_fetch.SKIP_HOSTS; Reuters, Bloomberg and FT are read since 15 Oct, paywall.py) at once; a page tried with nothing (cached_at set, no text); or after WAIT_TEXT
    (45 min, from the measured page-read times) with no page;
  - corpus.ingest computes search columns only for posts it inserts (a poll re-fetches thousands of known items; their
    jieba tokenisation held each new post invisible, and its page unread, for 12+ minutes on 15 Oct)
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
import ai, app, corpus, insights, item, sources, streams, telegram_alerts  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

STREAMS = app.STREAMS
NOW = datetime.now(app.HKT)


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    """a post whose text says 李家超 → CEHK 90; 港府 → CEHK 55; else unrelated (the model leaves CEHK out)"""
    if purpose not in ("scoring", "scoring_backfill"):
        return {"tags": []}
    body = prompt.split("Posts:\n", 1)[1]
    out = []
    for i in re.findall(r"(?m)^(\d+)\. \[", body):
        block = body.split(f"\n{i}. ", 1)[-1] if i != "0" else body
        block = block.split(f"\n{int(i) + 1}. ", 1)[0]
        st = ([{"id": "CEHK", "score": 90, "reason": "特首", "category": "言論及表態"}] if "李家超" in block else
              [{"id": "CEHK", "score": 55, "reason": "港府", "category": "其他"}] if "港府" in block else [])
        out.append({"i": int(i), "sentiment": "neutral", "labels": [], "streams": st})
    return {"posts": out}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc)  # stage 2 answers as stage 1 (tests/stage2.py)
WAIT_DEFAULT = streams.WAIT_TEXT

CALLS = []          # (method, params) of every Telegram call
MSG = {"n": 100}


def fake_call(method, **params):
    CALLS.append((method, params))
    if method == "sendMessage":
        MSG["n"] += 1
        return {"message_id": MSG["n"], "chat": {"id": params["chat_id"]}}
    return True


telegram_alerts.call = fake_call
telegram_alerts.GAP = 0
TG = [{"id": "CEHK", "telegram_chat": -1}]


def poll(items):
    sources.fetch_all = lambda extra: (list(items), {"x": {"ok": True, "items": len(items), "error": None}})
    sources.discuss_search = lambda qs: []
    app.after_poll = lambda g, f: insights.after_poll(g, f)
    app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
    app.load_state()
    app.poll_once()


def post(url, title, text, sid="rthk"):
    return {"sid": sid, "source": "RTHK", "title": title, "url": url, "published": NOW, "text": text}


def pid(url):
    return corpus.ids_for_urls([url])[url]


def filed(url, stream="CEHK"):
    return corpus.db().execute("SELECT count(*) FROM stream_items WHERE stream=%s AND url=%s", (stream, url)).fetchone()[0]


def sends():
    return [p for m, p in CALLS if m == "sendMessage"]


def rescore(url, title=None, text=None):
    with corpus.db() as c:
        if title is not None:
            c.execute("UPDATE posts SET title=%s WHERE url=%s", (title, url))
        if text is not None:
            c.execute("UPDATE posts SET text=%s WHERE url=%s", (text, url))
    streams.run_once(STREAMS)


def start_sending():
    corpus.kv_set(telegram_alerts.KEY, {"CEHK": corpus.db().execute(
        "SELECT coalesce(max(id), 0) FROM stream_items WHERE stream='CEHK'").fetchone()[0]})
    CALLS.clear()


streams.WAIT_TEXT = timedelta(minutes=45)  # the tests below hold it at the bound they test
CLOCK = {"now": None}
_real_clock = streams.clock
streams.clock = lambda: CLOCK["now"] or _real_clock()


def due_urls():
    return {p["url"] for p in streams.due(100)}


def read_page(url, text):
    import fulltext_fetch
    fulltext_fetch.save(pid(url), text)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.db()
        corpus.kv_set("scoring", {"since": NOW.isoformat(), "from_id": 0, "comments": False})
        poll([post("https://seed.test/0", "seed", "x" * 600)])  # the sources' first run (never filed)
        poll([post("https://seed.test/1", "seed2", "x" * 600, sid="hkej_instant"),
              post("https://seed.test/2", "seed3", "x" * 600, sid="reuters")])

    def setUp(self):
        CLOCK["now"] = None

    def test_0_wait_bound(self):
        self.assertEqual(WAIT_DEFAULT, timedelta(minutes=45))

    def test_1_waits_for_its_page_then_files_on_it(self):
        start_sending()
        url = "https://news.test/short-feed"
        poll([post(url, "記者會今日舉行", "李家超")])  # the feed's summary names the CE; the page decides
        self.assertNotIn(url, due_urls())
        CLOCK["now"] = datetime.now(app.HKT) + timedelta(minutes=30)
        self.assertNotIn(url, due_urls())  # 30 minutes on: still waiting
        streams.run_once(STREAMS)
        self.assertEqual(filed(url), 0)
        read_page(url, "天氣報告" * 80)  # its page: not about the CE
        self.assertIn(url, due_urls())
        streams.run_once(STREAMS)
        self.assertEqual(filed(url), 0)  # never filed on the headline
        telegram_alerts.send_due(TG)
        self.assertEqual(sends(), [])

    def test_2_proxy_host_waits_for_the_30_minute_job(self):
        url = "https://www1.hkej.com/dailynews/x/article/1"
        poll([post(url, "信報：李家超", "李家超", sid="hkej_instant")])
        self.assertNotIn(url, due_urls())
        read_page(url, "行政長官李家超今日表示……" * 30)
        self.assertIn(url, due_urls())
        streams.run_once(STREAMS)
        self.assertEqual(filed(url), 1)

    def test_3_never_read_host_at_once(self):
        url = "https://www.linkedin.com/posts/fstb-hk_x-2026-10-15"
        poll([post(url, "Hong Kong leader John Lee 李家超", "李家超", sid="fstb_linkedin")])
        self.assertIn(url, due_urls())
        # Reuters is read now (paywall.py, Deyao 2026-10-15): it waits for its page like any news post
        url = "https://www.reuters.com/world/china/x-2026-10-15/"
        poll([post(url, "Hong Kong leader John Lee 李家超", "李家超", sid="reuters")])
        self.assertNotIn(url, due_urls())

    def test_4_page_tried_with_nothing(self):
        url = "https://news.test/empty-page"
        poll([post(url, "李家超視察", "李家超")])
        self.assertNotIn(url, due_urls())
        read_page(url, "")
        self.assertIn(url, due_urls())

    def test_5_after_the_bound(self):
        url = "https://news.test/never-read"
        poll([post(url, "李家超出訪", "李家超")])
        CLOCK["now"] = datetime.now(app.HKT) + timedelta(minutes=44)
        self.assertNotIn(url, due_urls())
        CLOCK["now"] = datetime.now(app.HKT) + timedelta(minutes=46)
        self.assertIn(url, due_urls())

    def test_6_ingest_tokenises_only_new_posts(self):
        calls = []
        real = corpus.search_cols
        corpus.search_cols = lambda *a, **k: calls.append(a[0]) or real(*a, **k)
        try:
            items = [dict(post(f"https://known.test/{i}", f"舊聞{i}", "內容"), seen=None) for i in range(5)]
            corpus.ingest(items)
            calls.clear()
            new = corpus.ingest(items + [post("https://known.test/new", "新聞", "內容")])
        finally:
            corpus.search_cols = real
        self.assertEqual(len(new), 1)
        self.assertEqual(calls, ["新聞"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
