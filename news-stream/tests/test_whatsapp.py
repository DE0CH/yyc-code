#!/usr/bin/env python3
"""whatsapp_alerts end to end on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), with
the WhatsApp gateway (whatsapp/gateway.js) stubbed: no message leaves the machine.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_whatsapp.py

Covers: the message is Telegram's content as WhatsApp text (category in bold, mark + headline, source + link, 詳情);
a stream new to WhatsApp starts at its newest item (no backfill); every later item goes to its own stream's group, in
order; streams without a group are left alone; nothing is sent (and no cursor moves) while the linked device is not
connected; delivery is at most once (the cursor moves before the send, so a failed send is never repeated); one
Discord message when the device is unlinked or the number banned, and again only after it was linked in between;
a filed item wakes the sender at once (LISTEN yyc_live).
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import corpus, notify, whatsapp_alerts as wa  # noqa: E402

G1, G2 = "120363000000000001@g.us", "120363000000000002@g.us"
STREAMS = [{"id": "CEHK", "whatsapp_group": G1}, {"id": "HKIC", "whatsapp_group": G2}, {"id": "FSHK"}]
SENT, NOTES = [], []
STATE = {"state": "linked"}


def add(stream, n, title="t"):
    for i in range(n):
        corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                            ("2026-10-16", stream, f"https://x/{stream}/{title}{i}",
                             corpus.J({"title": f"{title}{i}", "source": "明報", "url": f"https://x/{stream}/{title}{i}"})))


def fake(method, path, body=None):
    if path == "/status":
        return dict(STATE)
    if path == "/send":
        if STATE["state"] != "linked":
            raise wa.GatewayDown("not connected")
        SENT.append((body["jid"], body["text"]))
        return {"id": f"m{len(SENT)}"}
    raise AssertionError(path)


class WhatsApp(unittest.TestCase):
    def setUp(self):
        SENT.clear(); NOTES.clear()
        STATE.clear(); STATE["state"] = "linked"
        wa.gateway = fake
        notify.deyao = lambda t: NOTES.append(t) or True
        corpus.db().execute("DELETE FROM stream_items")
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'whatsapp%%'")

    def test_text(self):
        t = wa.text({"title": "a<b", "source": "明報", "url": "https://x/?a=1&b=2", "sentiment": "neutral",
                     "labels": ["issue"], "post_id": 77}, "HKIC")
        self.assertEqual(t, "(🟡👀) a<b\n明報 ↗ https://x/?a=1&b=2\n詳情 https://newswatch.yyc-internal.com/item/77?p=HKIC")
        t = wa.text({"title": "t", "source": "明報", "url": "https://x/1", "sentiment": "positive", "category": "人事任命"})
        self.assertEqual(t, "*【人事任命】*\n(🟢) t\n明報 ↗ https://x/1")

    def test_flow(self):
        add("CEHK", 3, "old")  # before the group: never sent
        self.assertEqual(wa.send_due(STREAMS), 0)
        add("CEHK", 2, "a")
        add("HKIC", 1, "b")
        add("FSHK", 2, "c")  # no group
        self.assertEqual(wa.send_due(STREAMS), 3)
        self.assertEqual([j for j, _ in SENT], [G1, G2, G1])
        self.assertEqual(SENT[0][1], "a0\n明報 ↗ https://x/CEHK/a0")
        self.assertEqual(wa.send_due(STREAMS), 0)  # nothing twice

    def test_waits_while_not_linked(self):
        wa.send_due(STREAMS)
        before = corpus.kv_get(wa.KEY)
        STATE["state"] = "connecting"
        add("HKIC", 2, "w")
        self.assertEqual(wa.send_due(STREAMS), 0)
        self.assertEqual(corpus.kv_get(wa.KEY), before)  # no cursor moved: the items wait
        STATE["state"] = "linked"
        self.assertEqual(wa.send_due(STREAMS), 2)

    def test_at_most_once(self):
        wa.send_due(STREAMS)
        add("CEHK", 2, "f")

        def flaky(method, path, body=None):
            if path == "/send" and "f0" in body["text"]:
                raise wa.GatewayDown("socket closed mid-send")
            return fake(method, path, body)
        wa.gateway = flaky
        self.assertEqual(wa.send_due(STREAMS), 1)  # f0 failed: not retried; f1 still goes
        wa.gateway = fake
        self.assertEqual(wa.send_due(STREAMS), 0)
        self.assertEqual([t.split("\n")[0] for _, t in SENT], ["f1"])
        self.assertIn("socket closed", corpus.kv_get("whatsapp_error")["error"])

    def test_new_group_starts_now(self):
        add("FSHK", 2, "late")
        streams = STREAMS[:2] + [{"id": "FSHK", "whatsapp_group": "g3@g.us"}]
        wa.send_due(streams)
        self.assertFalse([1 for j, _ in SENT if j == "g3@g.us"])
        add("FSHK", 1, "now")
        wa.send_due(streams)
        self.assertEqual([t for j, t in SENT if j == "g3@g.us"], ["now0\n明報 ↗ https://x/FSHK/now0"])

    def test_discord_on_unlink_and_ban(self):
        wa.check_alerts({"state": "linked"})
        self.assertEqual(NOTES, [])
        wa.check_alerts({"state": "logged_out", "me": "447448090466"})
        wa.check_alerts({"state": "logged_out", "me": "447448090466"})
        self.assertEqual(len(NOTES), 1)  # once per outage
        self.assertIn("unlinked", NOTES[0])
        wa.check_alerts({"state": "linked"})
        wa.check_alerts({"state": "banned"})
        self.assertEqual(len(NOTES), 2)
        self.assertIn("banned", NOTES[1])
        wa.check_alerts({"state": "connecting"})  # a reconnect is not an alert
        self.assertEqual(len(NOTES), 2)

    def test_monitor_line(self):
        import monitor
        wa.send_due(STREAMS)
        add("CEHK", 2, "m")
        corpus.kv_set("whatsapp_status", {"state": "linked", "me": "447448090466", "since": "2026-10-16T12:00:00+08:00"})
        orig = wa.all_streams
        wa.all_streams = lambda: STREAMS
        try:
            m = monitor.whatsapp()
        finally:
            wa.all_streams = orig
        self.assertEqual(m["status"]["state"], "linked")
        self.assertEqual({s["stream"]: s["backlog"] for s in m["streams"]}, {"CEHK": 2, "HKIC": 0})


class WhatsAppWake(unittest.TestCase):  # after WhatsApp
    def test_filing_wakes_the_sender(self):
        """Deyao, 2026-10-23 (the pipeline tighter): a stream item filed anywhere wakes the sender within a second
        (LISTEN yyc_live, the live trigger's feed:<day>), not at its next IDLE check"""
        import threading, time
        wa.STOP.clear()
        wa.WAKE.clear()
        t = threading.Thread(target=wa.listen, daemon=True)
        t.start()
        try:
            time.sleep(1.0)  # LISTEN in place
            # LISTEN wakes it once as it connects (an item filed while the listener was away is sent now)
            self.assertTrue(wa.WAKE.is_set())
            wa.WAKE.clear()
            corpus.db().execute("UPDATE kv SET v=v WHERE k='nothing'")  # other writes don't wake it
            time.sleep(1.2)
            self.assertFalse(wa.WAKE.is_set(), "a write that files nothing woke it")
            t0 = time.time()
            corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                                ("2026-10-16", "CEHK", "https://x/CEHK/wake", corpus.J({  # a filed item carries seen
                                    "title": "wake", "source": "明報", "url": "https://x/CEHK/wake", "seen": "2026-10-16T10:00:00+08:00"})))
            self.assertTrue(wa.WAKE.wait(2), "woken by the filing")
            self.assertLess(time.time() - t0, 1.5)
        finally:
            wa.STOP.set()
            t.join(5)
            wa.STOP.clear()


if __name__ == "__main__":
    unittest.main()
