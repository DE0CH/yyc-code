#!/usr/bin/env python3
"""A filed item whose later score is under the threshold says plainly what happened (Deyao, 2026-10-15). On a
throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), no network.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_rescored_line.py

The case: post 5680354 (洪錦鉉, CEHK) scored 55 at 12:09 (threshold 50), was filed in CEHK and sent to Telegram; the
second social pass at 12:26 scored it 30. Nothing about scoring changes (filed ⇒ member, nothing recalled); the AI 分析
card (approved accounts) used to show a green 「已收錄」 badge beside 30/100, which read as a contradiction. Now:

  1. /app/item/ai (the edge app's item.aiScores, run in Node by tests/edge_call.py) gives that stream a `rescored` sentence with both times, both scores, the threshold and whether the
     item was sent: 「12:09 首次評分 55 分，已收錄並發送；12:26 重新評分為 30 分（低於門檻 50），已發送的項目保留。」
  2. not sent yet (the Telegram cursor is behind the item) and a stream with no channel say so; a score now under the
     floor says 「重新評分低於 20 分」; a filed item that still passes, or a stream it was never filed in, has none.
  3. the edge app's ItemPage renders the sentence and a neutral badge 「已收錄（重新評分後低於門檻）」 (source check;
     its typecheck runs in its build).
"""
import json, os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import app, corpus, edge_config  # noqa: E402
from edge_call import edge  # noqa: E402

BADGE = "已收錄（重新評分後低於門檻）"


def make(url, filed_score, now_score, sent=True, stream="CEHK", passes=True, at1="2026-10-15T12:09:57+08:00",
         at2="2026-10-15T12:26:14+08:00"):
    """a post filed in `stream` at filed_score (at1), its current score now_score (at2; None = under the floor)"""
    corpus.ingest([{"url": url, "title": "香港首份五年發展規劃", "text": "施政報告" * 60, "sid": "facebook",
                    "source": "洪錦鉉 HUNG Kam-in", "published": "2026-10-15T11:54+08:00"}])
    pid = corpus.ids_for_urls([url])[url]
    db = corpus.db()
    db.execute("""INSERT INTO scored (post_id, hash, sentiment, labels, at) VALUES (%s, 'h', 'positive', '[]', %s)""",
               (pid, at2))
    if now_score is not None:
        db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,%s,%s,%s)",
                   (stream, pid, now_score, "僅提及施政報告作背景", "施政及政策", at2))
    if passes:
        for n, at, sc in ((1, at1, filed_score), (2, at2, now_score)):
            res = {"streams": {stream: {"score": sc}} if sc is not None else {}}
            db.execute("INSERT INTO score_passes (post_id, pass, at, comments, result) VALUES (%s,%s,%s,0,%s)",
                       (pid, n, at, json.dumps(res)))
    sid = db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,%s,%s,%s) RETURNING id",
                     ("2026-10-15", stream, url, corpus.J({"url": url, "stream": stream, "score": filed_score,
                                                           "seen": at1, "post_id": pid}))).fetchone()[0]
    cur = corpus.kv_get("telegram_sent", {}) or {}
    cur[stream] = sid if sent else sid - 1
    corpus.kv_set("telegram_sent", cur)
    return pid


def publish(no_channel=False):
    """the site's config as the poller publishes it (edge_config.py); no_channel: no stream has a Telegram channel"""
    doc = edge_config.build()
    if no_channel:
        doc["streams"] = [dict(st, telegram=False) for st in doc["streams"]]
    corpus.kv_set("edge:config", doc)


def entry(pid, stream="CEHK"):
    return {s["stream"]: s for s in edge("item.aiScores", pid)["streams"]}[stream]


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.db()
        corpus.kv_set("scoring", {"from_id": 0, "comments": False})
        corpus.kv_set("stream_thresholds", {"CEHK": 50})
        cls.pid = make("https://www.facebook.com/hungkamin/posts/x1", 55, 30)
        publish()

    def test_1_the_case(self):
        e = entry(self.pid)
        self.assertEqual((e["score"], e["passed"], e["member"]), (30, False, True))
        self.assertEqual(e["rescored"], "12:09 首次評分 55 分，已收錄並發送；12:26 重新評分為 30 分（低於門檻 50），已發送的項目保留。")

    def test_2_variants(self):
        p = make("https://news.test/not-sent", 60, 40, sent=False)
        self.assertEqual(entry(p)["rescored"],
                         "12:09 首次評分 60 分，已收錄；12:26 重新評分為 40 分（低於門檻 50），項目保留在串流，並照常發送。")
        p = make("https://news.test/floor", 80, None, passes=False)  # content changed: no score_passes, under floor
        self.assertEqual(entry(p)["rescored"],
                         "12:09 評分 80 分，已收錄並發送；12:26 重新評分低於 20 分（門檻 50），已發送的項目保留。")
        p = make("https://news.test/next-day", 55, 30, at2="2026-10-16T09:01:00+08:00")
        self.assertEqual(entry(p)["rescored"],
                         "10月15日（星期四）12:09 首次評分 55 分，已收錄並發送；10月16日（星期五）09:01 重新評分為 30 分（低於門檻 50），已發送的項目保留。")
        p = make("https://news.test/still-passes", 90, 70)
        self.assertIsNone(entry(p)["rescored"])
        self.assertIsNone(entry(self.pid, "HKIC")["rescored"])  # never filed there

    def test_2b_no_channel(self):
        publish(no_channel=True)
        try:
            self.assertEqual(entry(self.pid)["rescored"],
                             "12:09 首次評分 55 分，已收錄；12:26 重新評分為 30 分（低於門檻 50），已收錄的項目保留。")
        finally:
            publish()

    def test_3_web_app(self):
        tsx = open(os.path.join(HERE, "web-app", "src", "routes", "-ItemPage.tsx")).read()
        api = open(os.path.join(HERE, "web-app", "src", "api.ts")).read()
        self.assertIn("rescored", api)
        self.assertIn("s.rescored", tsx)
        self.assertIn(BADGE, tsx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
