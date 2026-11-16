#!/usr/bin/env python3
"""Filed means member, and nothing is recalled (Deyao, 2026-10-15: "there shouldn't be recalling of messages"). On a
throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), the model and Telegram stubbed (no
message leaves the machine).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_one_membership.py

Membership has one rule: a post is a member of a stream when it scores at or above the stream's threshold OR it was
filed in that stream (stream_items). Once filed, an item stays in 串流, its Telegram message stays as sent (never
deleted or edited), the item page shows it in that stream with its current score next to the score it was filed on,
and 數據分析 counts it (insights.members, tags).

Covers:
  - filed and sent, then rescored under the threshold: the item stays in stream_items, no Telegram call but the send,
    the item page lists the stream (public) and, for approved accounts, the filed score and the current score;
    數據分析's members and the tags keep it, through sync_tags too;
  - filed, rescored under the threshold before Telegram got to it: still sent (the channel is 串流's copy);
  - a threshold raised after filing: the filed post stays a member;
  - the withdrawal machinery is gone: no `withdrawn` table, no streams.withdraw / reconcile / orphans, no
    telegram_alerts.retract_due.
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
import ai, app, corpus, edge_config, insights, item, sources, streams, telegram_alerts  # noqa: E402
from edge_call import edge  # noqa: E402

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
streams.WAIT_TEXT = timedelta(0)

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


def members(stream="CEHK"):
    day = NOW.strftime("%Y-%m-%d")
    return set(insights.members(stream, day, day))


def tagged(p, stream="CEHK"):
    r = corpus.db().execute("SELECT relevant FROM tags WHERE profile=%s AND post_id=%s", (stream, p)).fetchone()
    return bool(r and r[0])


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.db()
        corpus.kv_set("scoring", {"since": NOW.isoformat(), "from_id": 0, "comments": False})
        poll([post("https://seed.test/0", "seed", "x" * 600)])  # the sources' first run (never filed)

    def test_1_filed_and_sent_then_rescored_out_stays(self):
        """POL's 信報·新聞點評 case: filed at 90 and sent, its full text then scores under the floor"""
        start_sending()
        url = "https://news.test/filed-then-low"
        poll([post(url, "李家超出席論壇", "李家超" + "字" * 600)])
        streams.run_once(STREAMS)
        self.assertEqual(filed(url), 1)
        telegram_alerts.send_due(TG)
        self.assertEqual(len(sends()), 1)

        rescore(url, title="AI革命手術成功", text="人工智能與失業率" * 50)  # not about the CE now
        telegram_alerts.send_due(TG)
        self.assertEqual(filed(url), 1)  # stays in 串流
        self.assertEqual([m for m, p in CALLS], ["sendMessage"])  # nothing deleted or edited, nothing sent again
        self.assertIn(pid(url), members())  # 數據分析: filed ⇒ member
        self.assertTrue(tagged(pid(url)))
        streams.sync_tags(STREAMS)  # every poller start
        self.assertTrue(tagged(pid(url)))

        v = item.view(pid(url))  # the public item page: in CEHK
        self.assertEqual([s["stream"] for s in v["streams"]], ["CEHK"])
        self.assertIn("CEHK", v["matched"])
        self.assertNotIn("withdrawn", v)
        corpus.kv_set("edge:config", edge_config.build())
        a = {s["stream"]: s for s in edge("item.aiScores", pid(url))["streams"]}["CEHK"]  # approved accounts: both scores
        self.assertEqual((a["score"], a["kept"]["score"], a["member"]), (None, 90, True))

    def test_2_rescored_out_before_sending_is_still_sent(self):
        start_sending()
        url = "https://news.test/low-before-send"
        poll([post(url, "李家超出訪", "李家超" + "字" * 600)])
        streams.run_once(STREAMS)
        rescore(url, title="天氣", text="天氣報告" * 80)
        telegram_alerts.send_due(TG)
        self.assertEqual(filed(url), 1)
        self.assertEqual([p["text"].count("李家超出訪") for p in sends()], [1])  # the channel holds what 串流 holds

    def test_3_threshold_raised_after_filing(self):
        start_sending()
        url = "https://news.test/raised"
        poll([post(url, "港府回應", "港府回應" * 80)])  # 55
        streams.run_once(STREAMS)
        self.assertEqual(filed(url), 1)
        sys.path.insert(0, os.path.join(HERE, "infra"))
        import thresholds
        thresholds.main(["set", "CEHK", "60"])
        try:
            self.assertEqual(filed(url), 1)
            self.assertIn(pid(url), members())
            self.assertTrue(tagged(pid(url)))
            telegram_alerts.send_due(TG)
            self.assertEqual(len(sends()), 1)
        finally:
            thresholds.main(["set", "CEHK", "50"])

    def test_4_no_withdrawal_machinery(self):
        self.assertIsNone(corpus.db().execute("SELECT to_regclass('withdrawn')").fetchone()[0])
        for mod, name in ((streams, "withdraw"), (streams, "reconcile"), (streams, "orphans"),
                          (telegram_alerts, "retract_due"), (telegram_alerts, "current_score"), (item, "withdrawn")):
            self.assertFalse(hasattr(mod, name), f"{mod.__name__}.{name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
