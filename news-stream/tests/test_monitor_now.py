#!/usr/bin/env python3
"""Deyao's monitor shows what is true NOW (Deyao, 2026-10-16, looking at /app/monitor on his iPhone):

1. WhatsApp: the panel the edge page reads (kv monitor:data) carries the linked device's state and last disconnect,
   each stream group's last item sent and WHEN it was sent (kv whatsapp_sent_at, written by whatsapp_alerts.send_due
   on every send that worked), the backlog, and the primary phone (the cloud phone acp-hln9ilimxqzruvccu).
2. Spending tracks what things cost now: the last 6 h and 24 h per component (infra/cost-profile.py's profile, the
   same logic as the cost profile), per purpose the model in use now, since when, the model it replaced, and its rate
   from the spend since it took over (at most the last 24 h); this month per purpose × model. No projection anywhere.
3. No stale error looks live: an error carries `current`. Telegram's / WhatsApp's last send error is not current once
   a later send to that stream (or any stream, for an error with none) worked (kv telegram_sent_at /
   whatsapp_sent_at); a source's last error is current only while the source is failing; history scoring's
   "OpenRouter limit" is current only while OpenRouter's stop (kv openrouter_limit) is still recorded.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_monitor_now.py
"""
import json, os, sys, unittest, unittest.mock
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
for k in ("OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY", "TIKHUB_API", "ANYAPI_API_KEY", "IPROYAL_API",
          "HETZNER_YYC_READ", "CAPSOLVER_API", "TWOCAPTCHA_API", "KUBERNETES_SERVICE_HOST", "MONITOR_WARM"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, monitor, telegram_alerts, whatsapp_alerts  # noqa: E402

HKT = corpus.HKT


def iso(dt):
    return dt.astimezone(HKT).isoformat(timespec="seconds")


def item(sid, n=1):
    db = corpus.db()
    ids = []
    for i in range(n):
        url = f"https://x.test/{sid}/{len(ids)}-{datetime.now().timestamp()}"
        ids.append(db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s) RETURNING id",
                              ("2026-10-16", sid, url, corpus.J({"title": f"t{i}", "url": url, "source": "X",
                                                                  "seen": iso(datetime.now(HKT))}))).fetchone()[0])
    return ids


class WhatsAppPanel(unittest.TestCase):
    def setUp(self):
        for k in ("whatsapp_status", "whatsapp_sent", "whatsapp_sent_at", "whatsapp_error"):
            corpus.kv_set(k, None)
        corpus.db().execute("DELETE FROM stream_items")

    def test_send_records_when(self):
        sid = "CEHK"
        corpus.kv_set("whatsapp_sent", {sid: 0})
        item(sid, 2)
        s = [{"id": sid, "whatsapp_group": "1@g.us"}]
        with unittest.mock.patch.object(whatsapp_alerts, "gateway", lambda m, p, b=None, **k: {"state": "linked"}):
            self.assertEqual(whatsapp_alerts.send_due(s), 2)
        at = (corpus.kv_get("whatsapp_sent_at") or {}).get(sid)
        self.assertTrue(at, "the time of the last send that worked is kept per stream")
        self.assertLess(abs((datetime.now(HKT) - datetime.fromisoformat(at)).total_seconds()), 60)

    def test_panel(self):
        t = datetime.now(HKT)
        corpus.kv_set("whatsapp_status", {"state": "linked", "me": "447448090466", "since": iso(t - timedelta(hours=2)),
                                          "last_close": {"code": 428, "reason": "Connection Closed",
                                                         "at": iso(t - timedelta(hours=2))}})
        ids = item("CEHK", 3)
        corpus.kv_set("whatsapp_sent", {"CEHK": ids[1]})
        corpus.kv_set("whatsapp_sent_at", {"CEHK": iso(t - timedelta(minutes=5))})
        w = monitor.whatsapp()
        self.assertEqual(w["phone"]["id"], "acp-hln9ilimxqzruvccu")
        self.assertEqual(w["status"]["state"], "linked")
        row = next(r for r in w["streams"] if r["stream"] == "CEHK")
        self.assertEqual(row["last_sent_id"], ids[1])
        self.assertEqual(row["backlog"], 1)
        self.assertEqual(row["last_sent_at"], iso(t - timedelta(minutes=5)))
        self.assertIn("whatsapp", monitor.PANELS)

    def test_error_current_until_a_later_send(self):
        t = datetime.now(HKT)
        corpus.kv_set("whatsapp_error", {"at": iso(t - timedelta(hours=3)), "stream": "CEHK", "error": "socket dropped"})
        corpus.kv_set("whatsapp_sent_at", {"CEHK": iso(t - timedelta(hours=4))})
        self.assertTrue(monitor.whatsapp()["last_error"]["current"])
        corpus.kv_set("whatsapp_sent_at", {"CEHK": iso(t - timedelta(hours=1))})
        e = monitor.whatsapp()["last_error"]
        self.assertFalse(e["current"])
        self.assertEqual(e["resolved_at"], iso(t - timedelta(hours=1)))


class TelegramErrors(unittest.TestCase):
    def setUp(self):
        for k in ("telegram_sent", "telegram_sent_at", "telegram_error"):
            corpus.kv_set(k, None)
        corpus.db().execute("DELETE FROM stream_items")

    def test_send_records_when(self):
        sid = "CEHK"
        corpus.kv_set("telegram_sent", {sid: 0})
        item(sid, 1)
        s = [{"id": sid, "telegram_chat": "-100"}]
        with unittest.mock.patch.object(telegram_alerts, "post", lambda *a, **k: {"ok": True}), \
                unittest.mock.patch.object(telegram_alerts, "GAP", 0):
            telegram_alerts.send_due(s)
        self.assertTrue((corpus.kv_get("telegram_sent_at") or {}).get(sid))

    def test_old_error_without_stream_is_resolved_by_any_later_send(self):
        """the live case: the old in-cluster database's SSL eof, 27.7 h ago, stream None; sends have worked since"""
        t = datetime.now(HKT)
        corpus.kv_set("telegram_error", {"at": iso(t - timedelta(hours=27.7)), "stream": None,
                                         "error": "OperationalError('consuming input failed: SSL error')"})
        self.assertTrue(monitor.telegram()["last_error"]["current"], "no send recorded after it: still current")
        corpus.kv_set("telegram_sent_at", {"HKIC": iso(t - timedelta(minutes=3))})
        self.assertFalse(monitor.telegram()["last_error"]["current"])

    def test_error_of_one_stream_needs_that_streams_send(self):
        t = datetime.now(HKT)
        corpus.kv_set("telegram_error", {"at": iso(t - timedelta(hours=1)), "stream": "CEHK", "error": "chat not found"})
        corpus.kv_set("telegram_sent_at", {"HKIC": iso(t - timedelta(minutes=3)), "CEHK": iso(t - timedelta(hours=2))})
        self.assertTrue(monitor.telegram()["last_error"]["current"])


class SettleRule(unittest.TestCase):
    def test_settle(self):
        self.assertIsNone(monitor.settle(None, ["2026-10-16T10:00:00+08:00"]))
        e = {"at": "2026-10-16T09:00:00+08:00", "error": "x"}
        self.assertTrue(monitor.settle(e, [])["current"])
        self.assertTrue(monitor.settle(e, [None, "2026-10-16T08:00:00+08:00"])["current"])
        s = monitor.settle(e, ["2026-10-16T02:30:00Z"])  # 10:30 HKT, a different zone spelling
        self.assertFalse(s["current"])
        self.assertEqual(s["resolved_at"], "2026-10-16T02:30:00Z")

    def test_source_rows(self):
        corpus.db().execute("INSERT INTO poll_state (k, v) VALUES ('source_status', %s) ON CONFLICT (k) DO UPDATE SET v=excluded.v",
                            (corpus.J({"a": {"ok": True, "last_error": "old 500", "last_error_at": "2026-10-15T01:00:00+08:00",
                                             "last_ok": "2026-10-16T01:00:00+08:00"},
                                       "b": {"ok": False, "error": "HTTP 500", "fails": 3}}),))
        rows = {r["id"]: r for r in monitor.sources()["rows"]}
        self.assertFalse(rows["a"]["error_current"])
        self.assertTrue(rows["b"]["error_current"])

    def test_history_scoring_limit_only_current_while_the_stop_holds(self):
        corpus.kv_set("score_backfill", {"on": True, "state": "limit", "at": iso(datetime.now(HKT) - timedelta(hours=5))})
        corpus.kv_set("openrouter_limit", None)
        self.assertFalse(monitor.history_scoring()["state_current"])
        corpus.kv_set("openrouter_limit", {"since": iso(datetime.now(HKT)), "message": "Key limit exceeded"})
        self.assertTrue(monitor.history_scoring()["state_current"])
        corpus.kv_set("openrouter_limit", None)
        corpus.kv_set("score_backfill", {"on": True, "state": "running", "at": iso(datetime.now(HKT))})
        self.assertTrue(monitor.history_scoring()["state_current"])


class Spending(unittest.TestCase):
    """transcription switched from google/gemini-4-argon to qwen/qwen3-asr-1.7b; the view shows what it costs now"""
    @classmethod
    def setUpClass(cls):
        db = corpus.db()
        db.execute("DELETE FROM ai_runs")
        db.execute("DELETE FROM ai_spend")
        cls.end = datetime.now(timezone.utc)
        cls.switch = cls.end - timedelta(hours=10)
        rows = []
        for h in range(30, 10, -1):  # Argon: USD 2.00 an hour until the switch
            rows.append(("transcribe", "google/gemini-4-argon", cls.end - timedelta(hours=h, minutes=30), 2.0))
        for h in range(10, 0, -1):  # Qwen: USD 0.20 an hour since
            rows.append(("transcribe", "qwen/qwen3-asr-1.7b", cls.end - timedelta(hours=h) + timedelta(minutes=1), 0.2))
        for h in range(40, 0, -1):  # scoring: one model all along, USD 0.10 an hour
            rows.append(("scoring", "anthropic/claude-haiku-5.5", cls.end - timedelta(hours=h) + timedelta(minutes=2), 0.1))
        rows.append(("agent_turn", None, cls.end - timedelta(hours=1), 9.0))  # logged twice: never counted
        for p, m, at, u in rows:
            db.execute("INSERT INTO ai_runs (purpose, model, at, usd) VALUES (%s, %s, %s, %s)", (p, m, at, u))
            day = at.astimezone(HKT).strftime("%Y-%m-%d")
            if p != "agent_turn":
                db.execute("""INSERT INTO ai_spend (day, purpose, usd) VALUES (%s, %s, %s) ON CONFLICT (day, purpose)
                              DO UPDATE SET usd = ai_spend.usd + excluded.usd""", (day, p, u))
        with unittest.mock.patch.object(monitor, "panel", lambda name, fresh=False: {"providers": []}):
            cls.sp = monitor.spending()

    def test_current_model_and_rate(self):
        m = {r["purpose"]: r for r in self.sp["models"]}
        tr = m["transcribe"]
        self.assertEqual(tr["model"], "qwen/qwen3-asr-1.7b")
        self.assertEqual(tr["previous_model"], "google/gemini-4-argon")
        since = datetime.fromisoformat(tr["since"])
        self.assertLess(abs((since - (self.switch + timedelta(minutes=1))).total_seconds()), 2)
        self.assertTrue(tr["replaced_at"])
        # the rate is the spend since Qwen took over over the hours since then: USD 2.00 / ~9.98 h
        self.assertAlmostEqual(tr["rate_usd_per_hour"], 2.0 / ((self.end - since).total_seconds() / 3600), delta=0.01)
        self.assertLess(tr["rate_usd_per_hour"], 0.25)
        sc = m["scoring"]
        self.assertIsNone(sc["previous_model"])
        self.assertAlmostEqual(sc["rate_usd_per_hour"], 0.1, delta=0.01)  # last 24 h, not since its first call
        self.assertNotIn("agent_turn", m)

    def test_windows(self):
        w = {x["hours"]: x for x in self.sp["windows"]}
        self.assertEqual(sorted(w), [6, 24])
        o24 = next(c for c in w[24]["components"] if c["id"] == "openrouter")
        # last 24 h: Argon's last 13 hours before the switch (13 × 2.00), Qwen 10 × 0.20, scoring 24 × 0.10
        self.assertAlmostEqual(o24["cost_usd"], 13 * 2.0 + 10 * 0.2 + 24 * 0.1, delta=0.21)
        o6 = next(c for c in w[6]["components"] if c["id"] == "openrouter")
        self.assertAlmostEqual(o6["cost_usd"], 6 * 0.2 + 6 * 0.1, delta=0.11)
        self.assertNotIn("google/gemini-4-argon", json.dumps(w[6]), "Argon is history: not in the last 6 h")

    def test_month_by_purpose_and_model(self):
        rows = {(r["purpose"], r["model"]): r for r in self.sp["ai_models"]}
        self.assertIn(("transcribe", "google/gemini-4-argon"), rows)
        self.assertFalse(rows[("transcribe", "google/gemini-4-argon")]["current"])
        self.assertTrue(rows[("transcribe", "qwen/qwen3-asr-1.7b")]["current"])

    def test_bars(self):
        """every spend item as a bar (Deyao, 2026-10-21): per provider and per part, the last 24 h (the bar, the order)
        beside this month's figure, from the same data as the rest of the panel"""
        b = self.sp["bars"]
        for rows in (b["providers"], b["items"]):
            v = [r["h24_usd"] or 0 for r in rows]
            self.assertEqual(v, sorted(v, reverse=True))
        o = next(p for p in b["providers"] if p["id"] == "openrouter")
        w24 = next(c for c in next(w for w in self.sp["windows"] if w["hours"] == 24)["components"] if c["id"] == "openrouter")
        # the provider's row counts its running parts; the replaced model (Argon) is turned off, in the other list
        self.assertAlmostEqual(o["h24_usd"] + o["off_h24_usd"], w24["cost_usd"], places=4)
        self.assertAlmostEqual(o["off_h24_usd"], 13 * 2.0, delta=0.21)
        argon = next(r for r in b["items"] if r["name"] == "transcribe × google/gemini-4-argon")
        self.assertFalse(argon["live"])
        self.assertIn("replaced", argon["reason"])
        it = {r["name"]: r for r in b["items"] if r["id"] == "openrouter"}
        am = {f"{r['purpose']} × {r['model']}": r for r in self.sp["ai_models"]}
        q = "transcribe × qwen/qwen3-asr-1.7b"
        self.assertAlmostEqual(it[q]["h24_usd"], am[q]["last_24h_usd"], places=4)
        self.assertAlmostEqual(it[q]["month_usd"], am[q]["usd"], places=4)
        self.assertNotIn("transcribe × google/gemini-4-argon", [r["name"] for r in b["items"] if (r["h6_usd"] or 0) > 0])

    def test_turned_off(self):
        """turned off = its job is not running now (Deyao, 2026-10-21: out of every ranking, its own list)"""
        state, _ = monitor.job_states({"cronjobs": [{"name": "watch"}, {"name": "comments", "suspended": True}],
                                       "deployments": [{"name": "poller", "replicas": 1}, {"name": "images", "replicas": 0},
                                                       {"name": "transcribe", "replicas": 1, "off": "turned off by Deyao"}]})
        self.assertEqual([state(j)[0] for j in ("watch", "poller", "(job not recorded)", "-")], [True] * 4)
        for j in ("comments", "images", "transcribe", "opus-trial", "run-1021002453-9945"):
            self.assertFalse(state(j)[0], j)
            self.assertTrue(state(j)[1], j)
        # no jobs data: nothing can be called turned off
        self.assertTrue(monitor.job_states({})[0]("opus-trial")[0])

    def test_no_projection(self):
        txt = json.dumps(self.sp).lower()
        for word in ("project", "per_30_days", "month_end", "run_out", "runout"):
            self.assertNotIn(word, txt, word)


if __name__ == "__main__":
    unittest.main(verbosity=2)
