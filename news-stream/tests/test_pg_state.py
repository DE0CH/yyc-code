#!/usr/bin/env python3
"""The app's state in Postgres, end to end, with the model calls stubbed (no OpenRouter call, no cost), on a throwaway
database (TEST_DATABASE_URL; its tables are dropped and made again).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_pg_state.py

Covers: a poll (stream_items, poll_seen, poll_state, the corpus and tags) and 串流's feed; what an AI summary read
(insights.make_context); profiles and settings through infra/control.py; advisory locks between processes; the jobs'
documents; many threads ingesting at once.
"""
import io, json, os, re, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, edge_config, flags, insights, sources, streams, web  # noqa: E402
from edge_call import edge  # noqa: E402
_flag = flags.on  # the agent sits behind the flag `ai`, off on both sites (Deyao, 2026-10-14; tests/test_ai_flags.py
flags.on = lambda name, host="": True if name == "ai" else _flag(name, host)  # covers it off): on here
sys.path.insert(0, os.path.join(HERE, "infra"))
import control  # noqa: E402

USER = {"who": None}
web.access_user = lambda h: USER["who"]


def call(method, path, body=None, who=None):
    USER["who"] = who
    raw = json.dumps(body or {}).encode()
    p, _, q = path.partition("?")
    env = {"REQUEST_METHOD": method, "PATH_INFO": p, "QUERY_STRING": q, "RAW_URI": path,
           "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json"}
    got = {}
    data = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]))))
    lines = [json.loads(x) for x in data.decode().splitlines() if x.strip()]
    return got["status"], (lines[0] if len(lines) == 1 else lines)



def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    ai._record(purpose, model or ai.MODEL, [{"content": prompt}], {"cost": 0.002, "prompt_tokens": 50, "completion_tokens": 5})
    if purpose == "scoring":  # streams.py: a post with 測試新聞 is the first stream's (90)
        posts = re.findall(r"(?m)^(\d+)\. \[[^\]]*\] (.*)$", prompt.split("Posts:\n", 1)[1])
        return {"posts": [{"i": int(i), "sentiment": "negative", "labels": ["crisis"],
                           "streams": [{"id": app.STREAMS[0]["id"], "score": 90, "reason": "測試"}] if "測試新聞" in t else []}
                          for i, t in posts]}
    n = len(re.findall(r"(?m)^\d+\. \[", prompt))
    return {"tags": [{"i": i, "relevant": True, "sentiment": "negative", "labels": ["crisis"]} for i in range(n)]}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, None)  # stage 2 answers as stage 1 (tests/stage2.py)


class Poll(unittest.TestCase):
    def test_poll_and_feed(self):
        now = app.datetime.now(app.HKT)
        got = [{"sid": "rthk", "source": "RTHK", "title": f"李家超 測試新聞 {i}",
                "url": f"https://news.test/{i}", "published": now, "text": "內文"} for i in range(3)]
        got.append({"sid": "rthk", "source": "RTHK", "title": "無關新聞", "url": "https://news.test/other", "published": now})
        sources.fetch_all = lambda extra: (list(got), {"rthk": {"ok": True, "items": len(got), "error": None}})
        sources.discuss_search = lambda qs: []
        app.after_poll = lambda g, f: insights.after_poll(g, f)  # in this thread, so the test can check the corpus
        app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
        streams.settings()  # the switch point before the poll, so its posts are scored
        streams.WAIT_TEXT = streams.timedelta(0)  # no page fetch to wait for
        app.load_state()
        app.poll_once()
        app.poll_once()  # the same items again: all seen, nothing new
        self.assertEqual(streams.run_once(app.STREAMS), 4)  # every new post is scored, matched or not
        self.assertEqual(streams.run_once(app.STREAMS), 0)
        day = now.strftime("%Y-%m-%d")
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM poll_seen").fetchone()[0], 4)
        corpus.kv_set("edge:config", edge_config.build())
        feed = edge("feed.feed", day)  # 串流 (the edge app's, tests/edge_call.py)
        self.assertTrue(feed["last_poll"])
        self.assertTrue(feed["source_status"]["rthk"]["ok"])
        self.assertEqual(len(feed["items"]), 3)
        self.assertEqual(feed["days"], [day])
        self.assertEqual(feed["counts"][app.STREAMS[0]["id"]], {"total": 3, "day": 3})  # list length, no sentiment shares
        ids = corpus.ids_for_urls(["https://news.test/0", "https://news.test/other"])
        self.assertEqual(len(ids), 2)  # every fetched post is in the corpus, scored in or not
        self.assertEqual(len(insights.members(app.STREAMS[0]["id"], day, day)), 3)


class SummaryContext(unittest.TestCase):
    def test_make_context(self):
        """the posts an AI summary read, kept once under a content hash (kv ctx:<id>), listed under it on 數據分析"""
        read = [{"id": i} for i in (3, 2, 1)]
        cid = insights.make_context("daily", "CEHK", "AI 每日摘要：測試", "每日報告", "摘要", read, 5)
        self.assertEqual(cid, insights.make_context("daily", "CEHK", "AI 每日摘要：測試", "每日報告", "摘要", read, 5))
        c = corpus.kv_get("ctx:" + cid)
        self.assertEqual((c["ids"], c["total"], c["note"]), ([3, 2, 1], 5, "AI 讀了其中 3 篇（共 5 篇，最新的優先）"))


class Control(unittest.TestCase):
    """Deyao's control plane is a script (infra/control.py); the website has no admin routes"""
    def control(self, *argv, stdin=None):
        out = io.StringIO()
        old_out, old_in = sys.stdout, sys.stdin
        sys.stdout, sys.stdin = out, io.StringIO(json.dumps(stdin)) if stdin is not None else sys.stdin
        try:
            control.main(list(argv))
        finally:
            sys.stdout, sys.stdin = old_out, old_in
        return out.getvalue()

    def test_profiles_and_settings(self):
        sid = app.STREAMS[0]["id"]
        with self.assertRaises(BaseException):  # nothing to save: a profile is its stream in streams.json
            self.control("save", "-", stdin={"id": sid, "alerts": True})
        self.assertEqual(json.loads(self.control("set", "report_hour", "30"))["report_hour"], 23)
        self.assertEqual([p["id"] for p in insights.profiles()], [s["id"] for s in app.STREAMS])
        self.assertIn(sid, self.control())
        self.assertEqual(json.loads(self.control("tool-spend")), {"spend": []})
        with self.assertRaises(BaseException):  # no budgets to read or reset
            self.control("budgets")


class Locks(unittest.TestCase):
    def test_advisory_lock_across_processes(self):
        child = subprocess.Popen([sys.executable, "-c", "import corpus, time\nwith corpus.lock('tikhub'):\n print('held', flush=True)\n time.sleep(3)"],
                                 cwd=HERE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "held")
        self.assertTrue(corpus.locked("tikhub"))
        with corpus.lock("tikhub", wait=False) as got:
            self.assertFalse(got)
        t = time.time()
        with corpus.lock("tikhub") as got:  # waits for the other process
            self.assertTrue(got)
            self.assertGreater(time.time() - t, 1)
        child.wait()
        self.assertFalse(corpus.locked("tikhub"))


class Documents(unittest.TestCase):
    def test_job_documents(self):
        self.assertRaises(LookupError, corpus.file_get, "browser.json")
        self.assertEqual(corpus.file_get("social_runs.json", []), [])
        now = app.datetime.now(app.HKT).isoformat(timespec="seconds")
        corpus.file_put("browser.json", {"fetched": now, "sites": {"singpao": {"name": "成報", "ok": True, "error": None,
                                         "items": [{"title": "成報新聞\x00", "url": "https://sp.test/1"}]}}})
        items, status = sources.browser_file()
        self.assertEqual((len(items), status["singpao"]["ok"]), (1, True))
        corpus.file_put("tikhub.json", {"fetched": now, "ok": True, "error": None, "items": [],
                                        "checks": {"tiktok|測試": {"ok": True, "items": 0, "error": None, "checked": now}}})
        items, status = sources.tikhub_file()
        self.assertTrue(status["tikhub_tiktok"]["ok"])
        self.assertEqual(corpus.kv_add("replies:bytes:test:total", 1000), 1000)
        self.assertEqual(corpus.kv_add("replies:bytes:test:total", 234), 1234)


class Concurrency(unittest.TestCase):
    def test_threads_ingest(self):
        errs = []

        def work(t):
            try:
                for i in range(40):  # every thread files the same 40 URLs: each is new exactly once
                    corpus.ingest([{"url": f"https://c.test/{i}", "title": f"t{i}", "sid": "rthk", "source": "RTHK",
                                    "engagement": {"likes": t}}])
                    corpus.kv_set(f"t:{t}", i)
            except Exception as e:
                errs.append(e)
        ts = [threading.Thread(target=work, args=(t,)) for t in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(corpus.db().execute("SELECT count(*) FROM posts WHERE url LIKE 'https://c.test/%%'").fetchone()[0], 40)

    def test_transaction_rolls_back(self):
        c = corpus.db()
        with self.assertRaises(RuntimeError):
            with c:
                c.execute("INSERT INTO kv (k, v) VALUES ('rollback', '1')")
                raise RuntimeError
        self.assertIsNone(corpus.kv_get("rollback"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
