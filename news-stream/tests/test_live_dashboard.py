#!/usr/bin/env python3
"""The live dashboard's event log and its read (Deyao, 2026-10-21: watch the pipeline live), on a throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql:///yyc_test_live python3 tests/test_live_dashboard.py

1. events.py: emit() queues and returns at once, never raises (a bad payload, no database); flush() writes the queue
   on its own connection with the event's own time; strings are cut; a failed write is logged and dropped; a full queue
   drops new events; rows older than KEEP are pruned.
2. The pipeline's emits: a post recognised as a copy or a followed link records no event (the Duplicates and Links
   panels were removed, Deyao 2026-10-28); comments filed (corpus.add_comments) → comments with the count and the most liked;
   a page read (fulltext_fetch.save) → fulltext ok with route and length, a failure with its reason; both scoring stages
   (streams.score_posts with the models stubbed) → stage1 with every stream's score, stage2 per checked stream;
   a Telegram send (telegram_alerts.send_due) → alert, ok or not; a source failing two polls in a row and
   recovering (app.note_problem) → problem. YYC AI's activity (monitor.ai_activity / live_once, from operator
   snapshots): a first snapshot sets the cursor only; then model calls per person, sandbox starts and releases, host
   events, egress errors and a change of status become events of kind ai, once each (a second round adds nothing), in
   one transaction with the cursor; kv live:sys holds the slim state and the systems health.
3. The read (the edge app's live.liveData, in Node): every panel newest first with its post's title joined, ingest
   from posts (no comments), filed from stream_items; a first read the newest 12 per panel, a later one the oldest 40
   after the cursor (a burst handed over in order); the counts per 10 minutes and hour;
   the scored streams with their thresholds on the first read; malformed cursors are ignored (live.parseCursors);
   kv live:sys rides along on every read (sys).
4. Load: EXPLAIN of each query uses its index (no sequential scan of posts or events).
"""
import json, os, sys, time, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, edge_config, events, follow, fulltext_fetch, streams, telegram_alerts  # noqa: E402
import app  # noqa: E402
from edge_call import edge  # noqa: E402

events._loop = lambda: None  # the tests flush by hand (no writer thread racing them)


def node(code):
    """run an ES module snippet in web-app/ (tsx) → its printed JSON"""
    import subprocess
    r = subprocess.run(["npx", "tsx", "--eval", code], cwd=os.path.join(HERE, "web-app"), capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)


def rows(kind=None):
    events.flush()
    q = "SELECT kind, post_id, data, at FROM events" + (" WHERE kind=%s" if kind else "") + " ORDER BY id"
    return [dict(r) for r in corpus.db().execute(q, (kind,) if kind else ())]


def post(url, title, text=None, sid="test_src", source="測試來源", **kw):
    it = {"url": url, "title": title, "text": text, "sid": sid, "source": source, "published": "2026-10-21T10:00:00+08:00", **kw}
    return corpus.ingest([it])


class Events(unittest.TestCase):
    def setUp(self):
        events.flush()
        corpus.db().execute("DELETE FROM events")

    def test_emit_flush(self):
        t0 = datetime.now(timezone.utc)
        events.emit("problem", None, key="x", reason="r" * 1000, nested={"a": ["b" * 900]})
        got = rows("problem")
        self.assertEqual(len(got), 1)
        self.assertEqual(len(got[0]["data"]["reason"]), events.CUT)
        self.assertEqual(len(got[0]["data"]["nested"]["a"][0]), events.CUT)
        self.assertLess(abs((got[0]["at"] - t0).total_seconds()), 5)

    def test_never_raises(self):
        events.emit("x", "not a number")  # a bad post id: logged, dropped
        events.emit("x", None, obj=object())  # not JSON: written as its str
        events.flush()
        with mock.patch.object(corpus.DB, "executemany", side_effect=RuntimeError("db down")):
            events.emit("problem", None, key="lost")
            self.assertEqual(events.flush(), 0)  # logged and dropped
        self.assertEqual([r["data"].get("key") for r in rows("problem")], [])
        self.assertEqual(len(rows("x")), 1)

    def test_queue_full(self):
        with mock.patch.object(events, "QUEUE_MAX", 3):
            for i in range(5):
                events.emit("q", None, i=i)
        self.assertEqual([r["data"]["i"] for r in rows("q")], [0, 1, 2])

    def test_prune(self):
        corpus.db().execute("INSERT INTO events (at, kind) VALUES (now() - interval '4 days', 'old'), (now(), 'new')")
        events._state["pruned"] = 0
        events.emit("new", None)
        events.flush()
        kinds = [r["kind"] for r in corpus.db().execute("SELECT kind FROM events")]
        self.assertNotIn("old", kinds)
        self.assertIn("new", kinds)


class Emits(unittest.TestCase):
    def setUp(self):
        events.flush()
        corpus.db().execute("DELETE FROM events")

    def test_copies_still_join_their_article_without_events(self):
        a = post("https://news.example.com/a/1?utm_source=x", "Headline A")[0]
        b = post("https://news.example.com/a/1?utm_source=y&utm_medium=z", "Headline A copy", sid="other", source="另一來源")[0]
        text = "這是一篇很長的文章內容。" * 30
        c = post("https://one.example.com/x", "Same story", text)[0]
        d = post("https://two.example.org/y", "Same story", text, sid="two", source="二")[0]
        art = dict(corpus.db().execute("SELECT id, article_id FROM posts WHERE id = ANY(%s)", ([a, b, c, d],)).fetchall())
        self.assertEqual((art[b], art[d]), (art[a] or a, art[c] or c))
        self.assertEqual(rows(), [])

    def test_comments(self):
        pid = post("https://forum.example.com/t/9", "A thread")[0]
        corpus.add_comments({"url": "https://forum.example.com/t/9", "title": "A thread", "sid": "test_src", "source": "論壇"},
                            [{"id": 1, "author": "甲", "text": "first", "likes": 2},
                             {"id": 2, "author": "乙", "text": "most liked", "likes": 9}], total=30)
        got = rows("comments")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["post_id"], pid)
        d = got[0]["data"]
        self.assertEqual((d["new"], d["total"]), (2, 30))
        self.assertEqual(d["top"], [{"text": "most liked", "author": "乙", "likes": 9}, {"text": "first", "author": "甲", "likes": 2}])
        corpus.add_comments({"url": "https://forum.example.com/t/9", "title": "A thread", "sid": "test_src"},
                            [{"id": 1, "author": "甲", "text": "first"}])  # nothing new: no event
        self.assertEqual(len(rows("comments")), 1)

    def test_fulltext(self):
        pid = post("https://page.example.com/s/1", "Story")[0]
        import cache_fetch
        cache_fetch._seen.route = "proxy"
        fulltext_fetch.save(pid, "全文第一行。\n第二行。" * 20, url="https://page.example.com/s/1")
        pid2 = post("https://page.example.com/s/2", "Story 2")[0]
        cache_fetch._seen.route = "direct"
        fulltext_fetch.save(pid2, "", via=None, failed=True, reason="HTTPError: HTTP Error 403: Forbidden")
        got = rows("fulltext")
        self.assertEqual([(r["post_id"], r["data"]["ok"], r["data"]["route"]) for r in got], [(pid, True, "proxy"), (pid2, False, "direct")])
        self.assertTrue(got[0]["data"]["head"].startswith("全文第一行"))
        self.assertGreater(got[0]["data"]["chars"], 100)
        self.assertEqual(got[1]["data"]["reason"], "HTTPError: HTTP Error 403: Forbidden")

    def test_link(self):
        pid = post("https://src.example.com/p/1", "Linking post")[0]
        tid = post("https://dst.example.com/p/2", "Linked article", sid="dst")[0]
        c = corpus.db()
        rid = c.execute("""INSERT INTO follow_links (post_id, origin, url, status, at) VALUES (%s, %s, %s, 'pending', %s)
                           RETURNING id""", (pid, "https://src.example.com/p/1", "https://dst.example.com/p/2", "2026-10-21T10:00:00+08:00")).fetchone()[0]
        follow.record(rid, {"status": "duplicate", "reason": None, "url": "https://dst.example.com/p/2", "host": "dst.example.com", "target_id": tid})
        with mock.patch.object(follow, "route", return_value={"status": "duplicate", "reason": None, "url": "https://dst.example.com/p/2",
                                                              "host": "dst.example.com", "target_id": tid}):
            follow.mirror("https://reddit.com/r/hk/comments/abc", "https://dst.example.com/p/2")
            follow.mirror("https://reddit.com/r/hk/comments/abc", "https://dst.example.com/p/2")
        self.assertEqual(corpus.db().execute("SELECT status, target_id FROM follow_links WHERE id=%s", (rid,)).fetchone()[:], ("duplicate", tid))
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM follow_links WHERE origin LIKE 'https://reddit.com/%%'").fetchone()[0], 1)
        self.assertEqual(rows(), [])  # recorded in follow_links, no live event

    def test_stages(self):
        pid = post("https://score.example.com/1", "Scored post", "內容" * 50)[0]
        p = dict(corpus.db().execute("SELECT * FROM posts WHERE id=%s", (pid,)).fetchone())
        sts = streams.all_streams()
        ids = [s["id"] for s in sts]

        def ask(streams_, posts, purpose, model=None, checks=None):
            if model == streams.CONFIRM_MODEL:
                return {0: {"sentiment": "neutral", "labels": ["monitor"], "streams": {ids[0]: {"score": 35, "reason": "passing mention", "category": None}}, "raw": "{}"}}
            return {0: {"sentiment": "positive", "labels": [], "raw": "{}",
                        "streams": {k: {"score": 70 if k == ids[0] else 5, "reason": "main subject" if k == ids[0] else "unrelated", "category": None} for k in ids}}}
        with mock.patch.object(streams, "_ask", side_effect=ask), mock.patch.object(streams, "confirm_title"):
            streams.score_posts(sts, [p], "scoring")
        s1, s2 = rows("stage1"), rows("stage2")
        self.assertEqual(len(s1), 1)
        self.assertEqual(s1[0]["data"]["scores"][ids[0]], 70)
        self.assertEqual(set(s1[0]["data"]["scores"]), set(ids))
        self.assertEqual((s1[0]["data"]["best"], s1[0]["data"]["reason"]), (ids[0], "main subject"))
        self.assertEqual(set(s1[0]["data"]["reasons"]), set(ids))
        self.assertEqual(s2[0]["data"]["checks"], {ids[0]: {"s1": 70, "s2": 35, "reason": "passing mention", "s1reason": "main subject"}})
        self.assertEqual((s2[0]["data"]["sentiment"], s2[0]["data"]["labels"]), ("neutral", ["monitor"]))

    def test_alert(self):
        pid = post("https://alert.example.com/1", "Alerted")[0]
        st = [{"id": "CEHK", "telegram_chat": "-100"}]
        items = {"CEHK": [(11, {"post_id": pid, "title": "Alerted"}), (12, {"post_id": pid, "title": "Refused"})]}
        sent = []

        def fake_post(chat, it, sid):
            if it["title"] == "Refused":
                raise ValueError("chat not found")
            sent.append(it)
        with mock.patch.object(telegram_alerts, "cursors", return_value={"CEHK": 0}), \
             mock.patch.object(telegram_alerts, "pending", side_effect=lambda s, a: list(items[s])), \
             mock.patch.object(telegram_alerts, "post", side_effect=fake_post), mock.patch.object(telegram_alerts, "GAP", 0):
            telegram_alerts.send_due(st)
        got = rows("alert")
        self.assertEqual([(r["post_id"], r["data"]["channel"], r["data"]["ok"], r["data"]["item"]) for r in got],
                         [(pid, "telegram", True, 11), (pid, "telegram", False, 12)])
        self.assertEqual(got[1]["data"]["error"], "chat not found")

    def test_problem(self):
        prev = {}
        for ok in (False, False, False, True, True):
            v = app.track({"ok": ok, "items": 0, "error": None if ok else "HTTP 503"}, prev)
            app.note_problem("rthk", v, prev)
            prev = v
        got = rows("problem")
        self.assertEqual([r["data"]["state"] for r in got], ["failing", "recovered"])
        self.assertEqual((got[0]["data"]["key"], got[0]["data"]["reason"]), ("rthk", "HTTP 503"))
        self.assertEqual(got[1]["data"]["fails"], 3)


class Read(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        edge_config.publish()
        corpus.db().execute("INSERT INTO poll_state (k, v) VALUES ('last_poll', %s) ON CONFLICT (k) DO UPDATE SET v=excluded.v",
                            (json.dumps("2026-10-21T10:05:00+08:00"),))

    def test_read(self):
        events.flush()
        corpus.db().execute("DELETE FROM events")
        corpus.db().execute("DELETE FROM kv WHERE k = 'live:sys'")
        a = post("https://read.example.com/1", "First post")[0]
        b = post("https://read.example.com/2", "Second post")[0]
        corpus.add_comments({"url": "https://read.example.com/2", "title": "Second post", "sid": "test_src"},
                            [{"id": 1, "text": "a comment"}])
        events.emit("fulltext", a, ok=True, chars=10, head="text", route="direct")
        events.emit("problem", None, what="source", key="rthk", state="failing")
        corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-21', 'CEHK', 'https://read.example.com/1', %s)",
                            (json.dumps({"post_id": a, "title": "First post", "source": "測試來源", "score": 80, "screen": 72,
                                         "category": "施政", "sentiment": "neutral", "labels": ["monitor"], "seen": "2026-10-21T10:06:00+08:00"}),))
        events.flush()
        d = edge("live.liveData", {}, {"rates": True})
        self.assertEqual([c["title"] for c in d["panels"]["ingest"]][:2], ["Second post", "First post"])  # newest first, no comment row
        self.assertNotIn("a comment", [c["title"] for c in d["panels"]["ingest"]])
        self.assertEqual(d["panels"]["fulltext"][0]["title"], "First post")
        self.assertEqual(d["panels"]["fulltext"][0]["source"], "測試來源")
        self.assertNotIn("dup", d["panels"])
        self.assertIsNone(d["sys"])
        corpus.kv_set("live:sys", {"at": "2026-10-28T12:00:00+08:00", "ai": {"health": {"state": "ok"}}, "health": {"providers": []}})
        self.assertEqual(edge("live.liveData", {}, {})["sys"]["ai"]["health"]["state"], "ok")
        self.assertEqual(d["panels"]["problem"][0]["key"], "rthk")
        self.assertIsInstance(d["panels"]["problem"][0]["id"], int)  # the event's id, never the payload's
        self.assertEqual(d["panels"]["comments"][0]["post"], b)
        self.assertEqual((d["panels"]["filed"][0]["stream"], d["panels"]["filed"][0]["score"], d["panels"]["filed"][0]["screen"]), ("CEHK", 80, 72))
        self.assertEqual(d["last_poll"], int(datetime.fromisoformat("2026-10-21T10:05:00+08:00").timestamp() * 1000))
        self.assertTrue(d["streams"] and all("threshold" in s for s in d["streams"]))
        # the cursors: nothing new → every panel empty, no config sent
        self.assertEqual(d["rates"]["fulltext"], [1, 1])
        self.assertGreaterEqual(d["rates"]["ingest"][1], 2)
        cur = d["cursors"]
        d2 = edge("live.liveData", cur, {})
        self.assertEqual({k: len(v) for k, v in d2["panels"].items()}, {k: 0 for k in d2["panels"]})
        self.assertNotIn("streams", d2)
        self.assertEqual(d2["cursors"], cur)
        c = post("https://read.example.com/3", "Third post")[0]
        events.emit("fulltext", c, ok=False, reason="HTTP 404", route="direct")
        events.flush()
        d3 = edge("live.liveData", cur, {})
        self.assertEqual([x["title"] for x in d3["panels"]["ingest"]], ["Third post"])
        self.assertEqual([(x["title"], x["ok"], x["reason"]) for x in d3["panels"]["fulltext"]], [("Third post", False, "HTTP 404")])

    def test_cap_and_cursors(self):
        for i in range(50):
            events.emit("problem", None, state="failing", key=f"h{i}.example.com")
        events.flush()
        d = edge("live.liveData", {}, {})
        self.assertEqual(len(d["panels"]["problem"]), 12)  # a first read: the newest PANEL_FIRST
        self.assertEqual(d["panels"]["problem"][0]["key"], "h49.example.com")
        # a later read: the OLDEST after the cursor, in order, so a burst is handed over across reads (none skipped)
        cur = dict(d["cursors"], problem=d["panels"]["problem"][0]["id"] - 50)
        d2 = edge("live.liveData", cur, {})
        self.assertEqual([c["key"] for c in d2["panels"]["problem"]][::-1], [f"h{i}.example.com" for i in range(40)])
        self.assertEqual(d2["more"], ["problem"])
        d3 = edge("live.liveData", d2["cursors"], {})
        self.assertEqual([c["key"] for c in d3["panels"]["problem"]][::-1], [f"h{i}.example.com" for i in range(40, 50)])
        self.assertEqual(d3["more"], [])
        self.assertEqual(node("import { parseCursors } from './src/server/live.ts';"
                              "console.log(JSON.stringify(parseCursors('ingest:12,dup:x,bogus:3,filed:7,stage1:')))"),
                         {"ingest": 12, "filed": 7})

    def test_plans_use_indexes(self):
        # the read's statements, each on its index (posts_live_id, the stream_items key, events_kind)
        c = corpus.db()
        c.execute("ANALYZE")
        with c:
            c.execute("SET LOCAL enable_seqscan = off")  # a tiny test table: ask whether an index path exists at all
            plans = [
                "SELECT p.id FROM posts p WHERE p.thread IS NULL AND p.id > 0 ORDER BY p.id DESC LIMIT 40",
                "SELECT si.id FROM stream_items si WHERE si.id > 0 ORDER BY si.id DESC LIMIT 40",
                "SELECT e.id FROM events e WHERE e.kind = 'ai' AND e.id > 0 ORDER BY e.id DESC LIMIT 40",
            ]
            for q in plans:
                plan = "\n".join(r[0] for r in c.execute("EXPLAIN " + q))
                self.assertNotIn("Seq Scan", plan, q + "\n" + plan)
                self.assertIn("Index", plan)


def snap(at, calls=None, starts=(), vm=(), egress=(), state="ok", today="2026-10-27T16:00:00.000Z"):
    """a YYC AI operator snapshot (jarvis3 chat/src/web/ops.ts) with only what the live loop reads"""
    calls = calls or {}
    return {"at": at, "today": today, "health": {"state": state, "findings": [{"level": "problem", "text": "Host x not answering"}] if state != "ok" else []},
            "budget": {"status": {"spentUsd": 1.0, "limitUsd": 100}, "total": {"today": 1.0, "callsToday": sum(n for n, _ in calls.values())},
                       "byUser": [{"owner": o, "callsToday": n, "today": u} for o, (n, u) in calls.items()],
                       "byModel": [{"model": "anthropic/claude-opus-5.5", "callsToday": sum(n for n, _ in calls.values()), "today": 1.0}]},
            "starts": {"summary": {"n": 1}, "list": list(starts)}, "events": list(vm), "egress": {"recent": list(egress)},
            "service": {"deployments": [{"name": "web", "ready": 2, "desired": 2, "state": "ok", "pods": [{"restarts": 1}, {"restarts": 0}]}]},
            "hosts": {"list": [{"name": "aws-1", "kind": "aws", "status": "active", "reachable": True, "memTotal": 2**34}]},
            "sandboxes": {"maxActive": 10, "list": [{"owner": "michelle.yeoh@yyc.hk", "status": "ready", "ageS": 60, "idleS": 5}]},
            "recent": {"failedStarts": 0}, "scaler": {"plan": {"available": 11}}, "people": []}


class AiActivity(unittest.TestCase):
    def setUp(self):
        events.flush()
        corpus.db().execute("DELETE FROM events")
        corpus.db().execute("DELETE FROM kv WHERE k LIKE 'live:%%'")

    def test_rounds(self):
        import monitor
        s1 = snap("2026-10-28T04:00:00.000Z", {"michelle.yeoh@yyc.hk": (10, 0.5)})
        s2 = snap("2026-10-28T04:00:10.000Z", {"michelle.yeoh@yyc.hk": (14, 0.62), "deyaochen@yyc.hk": (1, 0.01)},
                  starts=[{"at": "2026-10-28T04:00:05.000Z", "what": "start", "ok": True, "ms": 4100, "host": "aws-1", "owner": "deyaochen@yyc.hk"}],
                  vm=[{"at": "2026-10-28T04:00:06.000Z", "kind": "host_launch_failed", "host": "aws-2", "data": {"error": "InsufficientInstanceCapacity"}}],
                  egress=[{"at": "2026-10-28T04:00:07.000Z", "kind": "model", "host": "openrouter.ai", "path": "/api/v1/messages", "status": 402,
                           "decision": "allow", "owner": "michelle.yeoh@yyc.hk"},
                          # a provider glitch (Jarvis 3 marks it quiet): no card
                          {"at": "2026-10-28T04:00:08.000Z", "kind": "model", "host": "openrouter.ai", "path": "/api/v1/messages", "status": 502,
                           "decision": "allow", "owner": "michelle.yeoh@yyc.hk", "quiet": True}], state="problem")
        seq = iter([s1, s2, s2])
        with mock.patch.object(monitor, "ai_snapshot", side_effect=lambda: next(seq)), \
                mock.patch.object(monitor, "health_slim", return_value={"providers": []}):
            self.assertTrue(monitor.live_once())
            self.assertEqual(rows("ai"), [])  # the first snapshot only sets the cursor
            monitor.live_once()
            got = [r["data"] for r in rows("ai")]
            self.assertEqual([g["what"] for g in got], ["start", "host", "egress", "calls", "calls", "status"])
            calls = {g["owner"]: (g["calls"], round(g["usd"], 2)) for g in got if g["what"] == "calls"}
            self.assertEqual(calls, {"michelle.yeoh@yyc.hk": (4, 0.12), "deyaochen@yyc.hk": (1, 0.01)})
            self.assertEqual(got[-1]["state"], "problem")
            monitor.live_once()  # the same snapshot again: nothing new
            self.assertEqual(len(rows("ai")), 6)
        doc = corpus.kv_get("live:sys")
        self.assertEqual(doc["ai"]["service"][0]["restarts"], 1)
        self.assertEqual(doc["ai"]["sandboxes"][0]["owner"], "michelle.yeoh@yyc.hk")
        self.assertEqual(doc["ai"]["hosts"][0]["mem_total_gib"], 16.0)

    def test_new_day_and_unreadable(self):
        import monitor
        cur = monitor.ai_activity(snap("2026-10-28T15:59:50.000Z", {"a@yyc.hk": (50, 2.0)}), None)[1]
        ev, _ = monitor.ai_activity(snap("2026-10-28T16:00:05.000Z", {"a@yyc.hk": (3, 0.1)}, today="2026-10-28T16:00:00.000Z"), cur)
        self.assertEqual([(e[1]["calls"], e[1]["calls_today"]) for e in ev], [(3, 3)])  # counted from 0 on the new day
        with mock.patch.object(monitor, "ai_snapshot", side_effect=RuntimeError("no route to web.jarvis")), \
                mock.patch.object(monitor, "health_slim", return_value={"providers": []}):
            monitor.live_once()
        self.assertIn("no route", corpus.kv_get("live:sys")["ai"]["error"])

    def test_provider_lines(self):
        import monitor
        f = monitor.provider_line({"id": "openrouter", "status": "ok", "credit_balance_usd": 61.5,
                                   "keys": [{"name": "yyc-chat", "usage_today_usd": 2.959, "usage_month_usd": 25.3}]})
        self.assertEqual(f, [["credit", "USD 61.50"], ["yyc-chat", "USD 2.96 today · USD 25.30 month"]])
        self.assertEqual(monitor.provider_line({"id": "2captcha", "status": "ok", "balance_usd": 3.1234}), [["balance", "USD 3.123"]])
        self.assertEqual(monitor.provider_line({"id": "anyapi", "status": "error"}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
