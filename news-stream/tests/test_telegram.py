#!/usr/bin/env python3
"""telegram_alerts end to end on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), with
Telegram stubbed (no message leaves the machine).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_telegram.py

Covers: a stream new to Telegram starts at its newest item (no backlog); every later item goes to its own stream's
channel, in order, as mark + headline + source + link (HTML-escaped), with the category in bold when the item has one; streams without a channel are left alone; a Telegram
outage (429 / network) keeps the item for the next pass; a message Telegram refuses for good is skipped.
"""
import os, sys, threading, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus, telegram_alerts as tg  # noqa: E402

tg.GAP = 0
STREAMS = [{"id": "CEHK", "telegram_chat": -1001}, {"id": "HKIC", "telegram_chat": -1002}, {"id": "FSHK"}]
SENT = []


def add(stream, n, title="t"):
    for i in range(n):
        corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                            ("2026-10-13", stream, f"https://x/{stream}/{title}{i}",
                             corpus.J({"title": f"{title}{i}", "source": "明報", "url": f"https://x/{stream}/{title}{i}"})))


class Telegram(unittest.TestCase):
    def setUp(self):
        SENT.clear()
        tg.HOLD.clear()
        tg.LAST.clear()
        tg.call = lambda method, **p: SENT.append((p["chat_id"], p["text"]))

    def test_message(self):
        got = {}
        tg.call = lambda method, **p: got.update(p, method=method)
        tg.post(-1, {"title": "a<b", "source": "明報", "url": "https://x/?a=1&b=2", "sentiment": "neutral", "labels": ["issue"]})
        self.assertEqual(got["text"], '(🟡👀) a&lt;b\n<a href="https://x/?a=1&amp;b=2">明報 ↗</a>')
        self.assertEqual((got["method"], got["parse_mode"], got["link_preview_options"]), ("sendMessage", "HTML", {"is_disabled": True}))

    def test_category(self):
        got = {}
        tg.call = lambda method, **p: got.update(p)
        tg.post(-1, {"title": "t", "source": "明報", "url": "https://x/1", "sentiment": "positive", "category": "人事任命"})
        self.assertEqual(got["text"], '<b>【人事任命】</b>\n(🟢) t\n<a href="https://x/1">明報 ↗</a>')

    def test_flow(self):
        add("CEHK", 3, "old")  # before the bot: never sent
        self.assertEqual(tg.send_due(STREAMS), 0)
        add("CEHK", 2, "a<&>")
        add("HKIC", 1, "b")
        add("FSHK", 2, "c")  # no channel
        self.assertEqual(tg.send_due(STREAMS), 3)
        self.assertEqual([c for c, _ in SENT], [-1001, -1002, -1001])
        self.assertEqual(SENT[0][1], 'a&lt;&amp;&gt;0\n<a href="https://x/CEHK/a&lt;&amp;&gt;0">明報 ↗</a>')
        self.assertEqual(tg.send_due(STREAMS), 0)  # nothing twice
        self.assertEqual(tg.tags({"sentiment": "negative", "labels": ["crisis", "trend", "nope"]}), "(🔴👀) ")
        self.assertEqual(tg.tags({"labels": ["trend"]}), "(🔥) ")
        self.assertEqual(tg.tags({"title": "x", "source": "明報", "url": "https://x/"}), "")

        # an outage holds that channel and keeps the item, the other channels go on; once the hold passes it is sent
        def down(method, **p):
            if p["chat_id"] == -1002:
                raise tg.Retry(1)
            SENT.append((p["chat_id"], p["text"]))
        tg.call = down
        add("HKIC", 1, "d")
        add("CEHK", 1, "c")
        SENT.clear()
        self.assertEqual(tg.send_due(STREAMS), 1)  # CEHK's; HKIC held
        self.assertEqual([c for c, _ in SENT], [-1001])
        self.assertGreater(tg.HOLD["HKIC"], time.monotonic())
        tg.call = lambda method, **p: SENT.append((p["chat_id"], p["text"]))
        self.assertEqual(tg.send_due(STREAMS), 0)  # still held
        tg.HOLD.clear()
        SENT.clear()
        self.assertEqual(tg.send_due(STREAMS), 1)
        self.assertIn("d0", SENT[0][1])

        # refused for good (e.g. the bot lost its rights): skipped, the next item still goes
        def refuse(method, **p):
            if "e0" in p["text"]:
                raise ValueError("sendMessage 400: chat not found")
            SENT.append((p["chat_id"], p["text"]))
        tg.call = refuse
        SENT.clear()
        add("CEHK", 2, "e")
        self.assertEqual(tg.send_due(STREAMS), 1)
        self.assertIn("e1", SENT[0][1])

    def test_stop_after_message_in_hand(self):
        add("HKIC", 3, "s")
        def send_then_stop(method, **p):
            SENT.append((p["chat_id"], p["text"]))
            tg.STOP.set()  # SIGTERM arrives while this message is being sent
        tg.call = send_then_stop
        try:
            self.assertEqual(tg.send_due(STREAMS), 1)
        finally:
            tg.STOP.clear()
        tg.call = lambda method, **p: SENT.append((p["chat_id"], p["text"]))
        self.assertEqual(tg.send_due(STREAMS), 2)  # the next poller sends the rest, nothing twice
        self.assertEqual(len({t for _, t in SENT}), 3)

    def test_detail_link(self):
        """詳情 links to the post's item page: by the item's post_id, else by its URL; none when not in the corpus"""
        url = "https://news.test/dash-1"
        corpus.ingest([{"url": url, "title": "港投公司 測試", "source": "明報", "sid": "mingpao",
                        "published": "2026-10-12T10:00+08:00"}])
        pid = corpus.ids_for_urls([url])[url]
        t = tg.text({"title": "港投公司 測試", "source": "明報", "url": url}, "HKIC")
        self.assertIn(f' · <a href="https://newswatch.yyc-internal.com/item/{pid}?p=HKIC">詳情</a>', t)  # opens with the channel's stream
        self.assertIn('<a href="https://newswatch.yyc-internal.com/item/77?p=HKIC">詳情</a>',
                      tg.text({"title": "x", "source": "明報", "url": "https://news.test/other", "post_id": 77}, "HKIC"))
        self.assertNotIn("詳情", tg.text({"title": "x", "source": "明報", "url": "https://news.test/not-in-corpus"}, "HKIC"))

    def test_new_channel_starts_now(self):
        add("FSHK", 2, "late")
        streams = STREAMS[:2] + [{"id": "FSHK", "telegram_chat": -1003}]
        tg.send_due(streams)
        self.assertFalse([1 for c, _ in SENT if c == -1003])
        add("FSHK", 1, "now")
        tg.send_due(streams)
        self.assertEqual([t for c, t in SENT if c == -1003], ['now0\n<a href="https://x/FSHK/now0">明報 ↗</a>'])


class TelegramPace(unittest.TestCase):  # after Telegram (test_flow starts on an empty table)
    """Deyao, 2026-10-23 (the pipeline tighter): GAP is per channel, and a filing wakes the sender at once"""
    def setUp(self):
        SENT.clear()
        tg.HOLD.clear()
        tg.LAST.clear()
        tg.call = lambda method, **p: SENT.append((p["chat_id"], p["text"], time.monotonic()))
        tg.send_due(STREAMS)  # the cursors past what other tests left
        SENT.clear()

    def tearDown(self):
        tg.GAP = 0

    def test_gap_per_channel(self):
        tg.GAP = 0.6
        add("CEHK", 2, "p")
        add("HKIC", 1, "q")
        t0 = time.monotonic()
        self.assertEqual(tg.send_due(STREAMS), 3)
        first = sorted(w - t0 for c, t, w in SENT)
        self.assertLess(first[1], 0.3, "the second channel's item goes at once, not a GAP after the first")
        self.assertGreaterEqual(first[2], 0.55, "one channel's next item waits its GAP")

    def test_filing_wakes_the_sender(self):
        import streams as scoring
        tg.STOP.clear()
        t = threading.Thread(target=tg.loop, args=(STREAMS,), daemon=True)
        with mock.patch.object(tg, "IDLE", 30):
            t.start()
            time.sleep(0.5)
            add("CEHK", 1, "w")
            t0 = time.monotonic()
            scoring.FILED.set()
            while not SENT and time.monotonic() - t0 < 5:
                time.sleep(0.02)
            tg.STOP.set()
            scoring.FILED.set()
            t.join(5)
        tg.STOP.clear()
        self.assertTrue(SENT)
        self.assertLess(SENT[0][2] - t0, 1, "sent within a second of the filing, not at the next IDLE check")


if __name__ == "__main__":
    unittest.main()
