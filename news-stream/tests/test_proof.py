#!/usr/bin/env python3
"""The proof view's data and its one write (Deyao, 2026-10-20: "click arrow to see each surfaced database entry, so i
can check if they look correct"), on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test_proof python3 tests/test_proof.py

1. POST /app/proof/flag (web.py → proof.py): approved accounts only (403 otherwise); a flag stores the post's id, URL
   and sid, the note (trimmed, at most 2000 characters, empty = none) and the account (lower case); an unknown post or
   a malformed id is 400. proof.py lists, shows, resolves and deletes flags (how Claude reads and fixes them).
2. The list (the edge app's proof.list, in Node): the given sids' posts, newest first, comments left out; member = the
   post reached a stream (a stream_items row, or a scored stream's score at or over its threshold; under the threshold
   or unscored is not); counts for both views; ?catalogue=1 walks every sites.json source in its order (edge:config
   `catalogue` = sites.json), each newest first; malformed sids are dropped.
3. One entry (proof.entry): the source, its feed URL, title, author, published and first-seen times, URL, the full
   stored text, how it was fetched (text_via, cached_at), first_run, the comment count, the links in the text, every
   stream's score with its threshold, and the post's flags. The monitor's list (proof.openFlags): open flags only.
4. The router (edge/worker.js): POST /app/proof/flag goes to the cluster, GET /app/proof and its reads to the app.
5. The "Passed stage 1" view (Deyao, 2026-10-22: "another tab that is everything that pass stage 1"): an entry is in it
   when stage 1 scored it at or over edge:config `screen` (streams.SCREEN) for a scored stream: stage 2 then filed it,
   rejected it or passed it, or stage 1 was the only stage (before two-stage), or it waits for stage 2 (its latest
   stage-1 event has a pair at or over the screen and no successful stage-2 event follows; a failed stage 2 still
   waits). Not in it: stage 1 under the screen, a stage-2 score with no stage-1 one (a filed post re-checked), a market
   child's own row only, a stage-1 event that stage 2 answered. Counts per view and per source; the entry lists the
   waiting pairs with stage 1's score and reason from the event.
6. The stream view (?stream=ID&above=N; Deyao, 2026-10-22: "show me everything about wang fuk court that scored above
   10"): every post from any source whose score for that stream is over N at stage 1 (scores.screen) or stage 2
   (scores.score); at N is not over; another stream's score, however high, does not count; comments are not entries.
   Newest first; the sources are those the posts come from, the most posts first; N defaults to 10 (also for a
   malformed N); the views are about that stream alone (filed in it or at its threshold; stage 1 at or over the screen
   for it, a waiting stage-1 event for another stream does not count); an id that is not a scored stream gives no
   stream and no entries.
"""
import json, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.pop("ADMIN_DEV", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, edge_config, insights, proof, web  # noqa: E402
from edge_call import edge  # noqa: E402

ai.model_name = lambda m: "Claude Haiku 5.5"  # no OpenRouter call in tests
USER = {"who": None}
web.access_user = lambda h: USER["who"]
SITES = json.load(open(os.path.join(HERE, "sites.json"), encoding="utf-8"))
A, B = SITES[0]["id"], SITES[1]["id"]  # two catalogue sources


def post(path, body, who):
    import io
    USER["who"] = who
    raw = json.dumps(body).encode()
    env = {"REQUEST_METHOD": "POST", "PATH_INFO": path, "QUERY_STRING": "", "RAW_URI": path, "HTTP_HOST": "newswatch.yyc-internal.com",
           "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json"}
    got = {}
    out = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]))))
    return got["status"], json.loads(out or b"{}")


class Proof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus.kv_set("scoring", {"from_id": 0, "comments": False})
        T = insights.now()
        iso = lambda h: (T.replace(minute=0, second=0, microsecond=0) - __import__("datetime").timedelta(hours=h)).isoformat(timespec="seconds")
        cls.items = [
            {"url": "https://a.example/1", "title": "甲一 最舊", "text": "內文一 https://x.example/link1 。", "sid": A, "source": "甲",
             "published": iso(5)},
            {"url": "https://a.example/2", "title": "甲二 收錄", "text": "內文二", "sid": A, "source": "甲", "published": iso(3)},
            {"url": "https://a.example/3", "title": "甲三 達標", "text": "內文三", "sid": A, "source": "甲", "published": iso(2)},
            {"url": "https://a.example/4", "title": "甲四 未達標", "text": "內文四", "sid": A, "source": "甲", "published": iso(1)},
            {"url": "https://b.example/1", "title": "乙一", "text": "乙內文", "sid": B, "source": "乙", "published": iso(4)},
            {"url": "https://c.example/1", "title": "丙一", "text": "丙", "sid": "other_src", "source": "丙", "published": iso(0)},
        ]
        corpus.ingest(cls.items)
        corpus.ingest([{"url": "https://a.example/1#c1", "title": "留言", "text": "一則留言", "sid": A, "source": "甲",
                        "thread": "https://a.example/1", "published": iso(4)}])
        ids = corpus.ids_for_urls(i["url"] for i in cls.items)
        cls.a1, cls.a2, cls.a3, cls.a4, cls.b1, cls.c1 = (ids[i["url"]] for i in cls.items)
        db = corpus.db()
        db.execute("UPDATE posts SET text_via='page', cached_at=%s WHERE id=%s", (iso(0), cls.a1))
        th = {s["id"]: 50 for s in insights.streams()}
        corpus.kv_set("stream_thresholds", th)
        cls.stream = next(s["id"] for s in insights.streams() if s["id"] == "CEHK")
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                   (iso(3)[:10], cls.stream, cls.items[1]["url"], corpus.J(dict(cls.items[1], stream=cls.stream, seen=iso(3)))))
        for pid, sc in ((cls.a3, 80), (cls.a4, 10)):
            db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,%s,%s,%s)",
                       (cls.stream, pid, sc, "理由" + str(sc), None, iso(0)))
        db.execute("""INSERT INTO scored (post_id, hash, sentiment, labels, at) SELECT id, 'x', 'neutral', '[]', %s
                      FROM posts WHERE id = ANY(%s)""", (iso(0), [cls.a3, cls.a4]))
        corpus.kv_set("openrouter_models", {"at": 9e12, "models": {ai.MODEL: {"name": "Anthropic: Claude Haiku 5.5"}}})
        corpus.kv_set("edge:config", edge_config.build())

    # 1
    def test_flag_route(self):
        self.assertEqual(post("/app/proof/flag", {"id": self.a1, "note": "x"}, None)[0], 403)
        code, d = post("/app/proof/flag", {"id": self.a1, "note": "  時間不對  "}, "Deyao@Example.com")
        self.assertEqual(code, 200)
        f = d["flag"]
        self.assertEqual((f["post_id"], f["url"], f["sid"], f["note"], f["email"]),
                         (self.a1, "https://a.example/1", A, "時間不對", "deyao@example.com"))
        self.assertTrue(f["at"].endswith("+08:00"))
        self.assertIsNone(post("/app/proof/flag", {"id": self.a2, "note": "   "}, "a@yyc.hk")[1]["flag"]["note"])
        self.assertEqual(len(post("/app/proof/flag", {"id": self.a2, "note": "長" * 3000}, "a@yyc.hk")[1]["flag"]["note"]), 2000)
        self.assertEqual(post("/app/proof/flag", {"id": 999999, "note": ""}, "a@yyc.hk")[0], 400)
        self.assertEqual(post("/app/proof/flag", {"id": "abc"}, "a@yyc.hk")[0], 400)
        # proof.py: how Claude reads and fixes them
        opened = proof.flags()
        self.assertEqual([x["post_id"] for x in opened][-1], self.a1)
        shown = proof.show(f["id"])
        self.assertEqual(shown["post"]["url"], "https://a.example/1")
        r = proof.resolve(f["id"], "fixed the time")
        self.assertIsNotNone(r["resolved_at"])
        self.assertNotIn(f["id"], [x["id"] for x in proof.flags()])
        self.assertIn(f["id"], [x["id"] for x in proof.flags(open_only=False)])
        # the monitor lists open ones only
        mon = edge("proof.openFlags")["flags"]
        self.assertNotIn(f["id"], [x["id"] for x in mon])
        self.assertTrue(all(x["post_id"] == self.a2 for x in mon))
        self.assertEqual(mon[0]["title"], "甲二 收錄")
        for x in proof.flags(open_only=False):
            proof.delete(x["id"])
        self.assertEqual(proof.flags(open_only=False), [])

    # 2
    def test_list(self):
        d = edge("proof.list", [A, B], False)
        self.assertEqual([e["id"] for e in d["entries"]], [self.a4, self.a3, self.a2, self.b1, self.a1])  # newest first, no comment
        member = {e["id"]: e["member"] for e in d["entries"]}
        self.assertEqual(member, {self.a4: False, self.a3: True, self.a2: True, self.b1: False, self.a1: False})
        self.assertEqual(d["counts"], {"all": 5, "stream": 2, "stage1": 1})  # a3: CEHK 80, no model (before two-stage)
        self.assertEqual({e["id"]: e["stage1"] for e in d["entries"]}[self.a3], True)
        src = {s["sid"]: s for s in d["sources"]}
        self.assertEqual((src[A]["total"], src[A]["stream"], src[A]["url"], src[A]["catalogue"]), (4, 2, SITES[0]["url"], True))

    def test_catalogue(self):
        self.assertEqual([s["id"] for s in corpus.kv_get("edge:config")["catalogue"]], [s["id"] for s in SITES])
        d = edge("proof.list", [], True)
        self.assertEqual(d["sids"], [s["id"] for s in SITES])
        self.assertEqual([e["id"] for e in d["entries"]], [self.a4, self.a3, self.a2, self.a1, self.b1])  # source by source
        self.assertNotIn(self.c1, [e["id"] for e in d["entries"]])

    # 3
    def test_entry(self):
        proof.add(self.a1, "內文不全", "deyao@example.com")
        e = edge("proof.entry", self.a1)
        self.assertEqual((e["sid"], e["source"], e["feed"]["url"], e["title"], e["url"]), (A, "甲", SITES[0]["url"], "甲一 最舊", "https://a.example/1"))
        self.assertEqual((e["text_via"], e["comments"], e["first_run"]), ("page", 1, True))
        self.assertTrue(e["cached_at"] and e["seen"] and e["published"])
        self.assertIn("內文一", e["text"])
        self.assertEqual(e["links"], ["https://x.example/link1"])
        self.assertEqual([f["note"] for f in e["flags"]], ["內文不全"])
        e3 = edge("proof.entry", self.a3)
        sc = {s["stream"]: s for s in e3["scores"]}[self.stream]
        self.assertEqual((sc["score"], sc["threshold"], sc["passed"], sc["reason"]), (80, 50, True, "理由80"))
        e2 = edge("proof.entry", self.a2)
        self.assertEqual([s["stream"] for s in e2["streams"]], [self.stream])
        self.assertIsNone(edge("proof.entry", self.c1)["feed"])  # not a catalogue source
        for x in proof.flags(open_only=False):
            proof.delete(x["id"])

    # 4
    def test_router(self):
        js = r"""
const calls = [];
globalThis.fetch = async (r) => { calls.push(r.url.includes("workers.dev") ? "APP" : "ORIGIN"); return new Response("ok"); };
const w = (await import(process.argv[1])).default;
const env = { EDGE_SECRET: "s" };
for (const [m, p] of [["POST", "/app/proof/flag"], ["GET", "/app/proof?sid=a"], ["GET", "/app/proof/data?sid=a"],
                      ["GET", "/app/proof/entry?id=1"], ["GET", "/app/monitor/flags"], ["POST", "/app/ai/persona"]])
  await w.fetch(new Request("https://newswatch.yyc-internal.com" + p, { method: m, body: m === "POST" ? "{}" : undefined }), env);
console.log(JSON.stringify(calls));
"""
        r = subprocess.run(["node", "--input-type=module", "-e", js, os.path.join(HERE, "edge", "worker.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["ORIGIN", "APP", "APP", "APP", "APP", "ORIGIN"])

    # 5
    def test_stage1(self):
        import streams
        from datetime import timedelta
        db = corpus.db()
        T = insights.now().replace(microsecond=0)
        at = lambda m: (T - timedelta(minutes=m)).isoformat()
        G, H = streams.CONFIRM_MODEL, ai.MODEL
        SID = "zz_s1"
        urls = [f"https://s1.example/{k}" for k in range(1, 11)]
        corpus.ingest([{"url": u, "title": "第一關 " + u.rsplit("/", 1)[1], "text": "內文", "sid": SID, "source": "測試",
                        "published": at(600 - k)} for k, u in enumerate(urls)])
        ids = corpus.ids_for_urls(urls)
        p = [ids[u] for u in urls]
        ins = lambda st, pid, score, reason, model, screen=None, sr=None: db.execute(
            "INSERT INTO scores (stream, post_id, score, reason, scored_at, model, screen, screen_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (st, pid, score, reason, at(5), model, screen, sr))
        ev = lambda kind, pid, m, **d: db.execute("INSERT INTO events (at, kind, post_id, data) VALUES (%s,%s,%s,%s)", (at(m), kind, pid, corpus.J(d)))
        ins("CEHK", p[0], 80, "二：相關", G, 70, "一：相關")                       # filed
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,'CEHK',%s,%s)", (at(5)[:10], urls[0], corpus.J({"seen": at(5)})))
        ins("CEHK", p[1], 30, "二：無關", G, 60, "一：可能相關")                   # rejected by stage 2
        ins("HKIC", p[2], 65, "只有一關", None)                                  # stage 1 only (before two-stage)
        ins("CEHK", p[3], 15, "一：低", H)                                       # under the screen
        ins("HKIC", p[4], 70, "二：已收錄的覆核", G, 10, "一：低")                  # a filed post re-checked: stage 1 under
        ins("GONE", p[5], 85, "二：銀河", G, 75, "一：銀河")                        # a stream streams.json lacks
        ev("stage1", p[6], 5, model=H, scores={"CEHK": 72, "HKIC": 10}, reasons={"CEHK": "一：等第二關", "HKIC": "低"})  # waiting
        ev("stage1", p[7], 4, model=H, scores={"CEHK": 66}, reasons={"CEHK": "一：第二關失敗"})                      # stage 2 failed
        ev("stage2", p[7], 3, model=G, failed="no answer from the model; scored again next round")
        ev("stage1", p[8], 4, model=H, scores={"CEHK": 50}, reasons={"CEHK": "一：已覆核"})                          # stage 2 answered
        ev("stage2", p[8], 3, model=G, checks={})
        ev("stage1", p[9], 4, model=H, scores={"CEHK": 19, "GONE": 90}, reasons={})                           # under / not scored
        corpus.kv_set("edge:config", edge_config.build())
        d = edge("proof.list", [SID], False)
        self.assertEqual(d["screen"], streams.SCREEN)
        got = {e["id"]: e["stage1"] for e in d["entries"]}
        self.assertEqual({k for k, v in got.items() if v}, {p[0], p[1], p[2], p[6], p[7]})
        self.assertEqual(d["counts"], {"all": 10, "stream": 3, "stage1": 5})  # stream: p0 filed, p2 65, p4 70
        self.assertEqual(d["sources"][0]["stage1"], 5)
        e = edge("proof.entry", p[6])
        self.assertEqual([(x["stream"], x["s1"], x["r1"], x["threshold"]) for x in e["pending"]], [("CEHK", 72, "一：等第二關", 50)])
        self.assertEqual(e["screen"], streams.SCREEN)
        self.assertEqual([x["stream"] for x in edge("proof.entry", p[7])["pending"]], ["CEHK"])
        self.assertEqual(edge("proof.entry", p[8])["pending"], [])
        sc = {x["stream"]: x for x in edge("proof.entry", p[1])["scores"]}["CEHK"]
        self.assertEqual((sc["score"], sc["screen"]["score"], sc["screen"]["reason"], sc["passed"]), (30, 60, "一：可能相關", False))

    # 6
    def test_stream_view(self):
        import streams
        from datetime import timedelta
        db = corpus.db()
        T = insights.now().replace(microsecond=0)
        at = lambda m: (T - timedelta(minutes=m)).isoformat()
        G, H = streams.CONFIRM_MODEL, ai.MODEL
        W1, W2 = "zz_w1", "zz_w2"
        rows = [  # (sid, minutes ago, [(stream, score, model, screen)])
            (W1, 100, [("WFC", 11, H, None)]),                       # 0 stage 1 only, 11: over 10
            (W1, 90, [("WFC", 10, H, None)]),                        # 1 at 10: not over
            (W2, 80, [("WFC", 9, G, 11)]),                           # 2 stage 2 under, stage 1 over: in
            (W1, 70, [("WFC", 5, G, 10)]),                           # 3 both at or under: out
            (W2, 60, [("WFC", 80, None, None)]),                     # 4 at the threshold (50): in WFC, passed stage 1
            (W1, 50, [("CEHK", 95, None, None), ("WFC", 2, H, None)]),  # 5 another stream's score: out
            (W1, 40, [("WFC", 30, G, 45), ("CEHK", 90, None, None)]),   # 6 in; CEHK 90 does not make it "in WFC"
            (W1, 30, [("WFC", 12, H, None)]),                        # 7 in; a waiting CEHK stage-1 event: not stage 1 here
            ("zz_w3", 20, [("WFC", 60, G, 60)]),                     # 8 another source, filed in WFC
        ]
        urls = [f"https://w.example/{k}" for k in range(len(rows))]
        corpus.ingest([{"url": u, "title": f"宏福苑 {k}", "text": "內文", "sid": r[0], "source": "來源" + r[0][-1],
                        "published": at(r[1])} for k, (u, r) in enumerate(zip(urls, rows))])
        corpus.ingest([{"url": "https://w.example/6#c1", "title": "留言", "text": "宏福苑留言", "sid": W1, "source": "來源1",
                        "thread": urls[6], "published": at(35)}])
        ids = corpus.ids_for_urls(urls + ["https://w.example/6#c1"])
        p = [ids[u] for u in urls]
        for pid, (_, _, sc) in zip(p, rows):
            for st, score, model, screen in sc:
                db.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at, model, screen) VALUES (%s,%s,%s,'r',%s,%s,%s)",
                           (st, pid, score, at(1), model, screen))
        db.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at, model) VALUES ('WFC',%s,50,'r',%s,%s)",
                   (ids["https://w.example/6#c1"], at(1), H))  # a comment: never an entry
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,'WFC',%s,%s)", (at(20)[:10], urls[8], corpus.J({"seen": at(20)})))
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s,'CEHK',%s,%s)", (at(40)[:10], urls[6], corpus.J({"seen": at(40)})))
        db.execute("INSERT INTO events (at, kind, post_id, data) VALUES (%s,'stage1',%s,%s)",
                   (at(2), p[7], corpus.J({"model": H, "scores": {"CEHK": 70, "WFC": 12}, "reasons": {}})))
        corpus.kv_set("edge:config", edge_config.build())
        self.assertIn("WFC", corpus.kv_get("edge:config")["scored"])
        d = edge("proof.list", [], False, {"id": "WFC", "above": "10"})
        self.assertEqual((d["stream"]["id"], d["stream"]["above"], d["catalogue"]), ("WFC", 10, False))
        self.assertEqual([e["id"] for e in d["entries"]], [p[8], p[7], p[6], p[4], p[2], p[0]])  # newest first, no comment
        self.assertEqual({e["id"]: e["member"] for e in d["entries"]},
                         {p[8]: True, p[7]: False, p[6]: False, p[4]: True, p[2]: False, p[0]: False})
        self.assertEqual({e["id"]: e["stage1"] for e in d["entries"]},
                         {p[8]: True, p[7]: False, p[6]: True, p[4]: True, p[2]: False, p[0]: False})
        self.assertEqual(d["counts"], {"all": 6, "stream": 2, "stage1": 3})
        self.assertEqual([s["sid"] for s in d["sources"]], [W1, W2, "zz_w3"])  # 3, 2, 1 posts
        self.assertEqual([(s["total"], s["stream"], s["stage1"]) for s in d["sources"]], [(3, 0, 1), (2, 1, 1), (1, 1, 1)])
        self.assertEqual(d["sids"], [W1, W2, "zz_w3"])
        # the sources' own view still counts every stream (p6 filed in CEHK, CEHK 90; p7 waits for stage 2 on CEHK)
        n = {e["id"]: (e["member"], e["stage1"]) for e in edge("proof.list", [W1], False)["entries"]}
        self.assertEqual((n[p[6]], n[p[7]]), ((True, True), (False, True)))
        # N
        self.assertEqual([e["id"] for e in edge("proof.list", [], False, {"id": "WFC", "above": "30"})["entries"]], [p[8], p[6], p[4]])
        self.assertEqual(len(edge("proof.list", [], False, {"id": "WFC", "above": "0"})["entries"]), 9)  # every WFC score is over 0
        for bad in (None, "x", "-1", "101", "10.5"):
            self.assertEqual(edge("proof.list", [], False, {"id": "WFC", "above": bad})["stream"]["above"], 10, bad)
        # the stream view wins over sids / catalogue in the same call
        self.assertEqual(len(edge("proof.list", [A], True, {"id": "WFC", "above": "10"})["entries"]), 6)
        # not a scored stream
        for sid in ("NOPE", "GONE"):
            u = edge("proof.list", [], False, {"id": sid, "above": "10"})
            self.assertEqual((u["stream"], u["entries"], u["sources"]), (None, [], []), sid)


if __name__ == "__main__":
    unittest.main()
