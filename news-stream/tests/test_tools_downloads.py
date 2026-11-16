#!/usr/bin/env python3
"""The data API's tools end to end (tools/: /app/api/v1 and MCP) and the AI server through the REAL server (gunicorn, as
on the cluster), on a throwaway database (TEST_DATABASE_URL; its tables are dropped and made again), the model stubbed
(no OpenRouter call).

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_tools_downloads.py

Covers: every tool offered has a case here (adding a tool without one fails) and returns a usable result; arguments
that don't match a tool's schema are refused with a message a model can act on; a non-Latin-1 header fails loudly
instead of gunicorn dropping the response, and gunicorn serves the AI server's answers; the AI routes follow the `ai`
flag per site.
"""
import io, json, os, re, socket, subprocess, sys, tempfile, time, unittest, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ADMIN_DEV", None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import ai, app, corpus, flags, insights, sources, streams, tools, web  # noqa: E402
corpus.MARK_BACKLOG = False  # these posts are live posts, not a source's first read (backlog: tests/test_backlog.py)

# the tests exercise every feature: a copy of flags.json with every flag on for the beta site (the shipped file keeps
# some off there too, e.g. the AI features: tests/test_ai_flags.py checks those)
_f = json.load(open(flags.FILE))
for _v in _f["flags"].values():
    _v["targeting"] = {"if": [{"in": [{"var": "host"}, ["yyc-beta.deyaochen.com"]]}, "on", "off"]}
flags.FILE = os.environ["FLAGS_FILE"] = os.path.join(os.environ["DATA_DIR"], "flags.json")  # gunicorn's too
json.dump(_f, open(flags.FILE, "w"))
os.environ.pop("FLAGD_HOST", None)

USER = {"who": None}
web.access_user = lambda h: USER["who"]
STREAM = app.STREAMS[0]["id"]


BETA = "yyc-beta.deyaochen.com"  # every feature flag is on there (flags.json): the tests exercise every feature


def call(method, path, body=None, who=None, host=BETA):
    USER["who"] = who
    raw = json.dumps(body or {}).encode()
    p, _, q = path.partition("?")
    env = {"REQUEST_METHOD": method, "PATH_INFO": p, "QUERY_STRING": q, "RAW_URI": path, "HTTP_HOST": host,
           "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "CONTENT_TYPE": "application/json"}
    got = {}
    data = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]), headers=dict(h))))
    return got["status"], got["headers"], data


def fake_jsonc(prompt, purpose, model=None, max_tokens=4000):
    if purpose == "scoring":  # streams.py: a post with 測試新聞 is the first stream's (90)
        posts = re.findall(r"(?m)^(\d+)\. \[[^\]]*\] (.*)$", prompt.split("Posts:\n", 1)[1])
        return {"posts": [{"i": int(i), "sentiment": "negative", "labels": ["crisis"],
                           "streams": [{"id": app.STREAMS[0]["id"], "score": 90, "reason": "測試"}] if "測試新聞" in t else []}
                          for i, t in posts]}
    n = len(re.findall(r"(?m)^\d+\. \[", prompt))
    return {"tags": [{"i": i, "relevant": True, "sentiment": "negative", "labels": ["crisis"]} for i in range(n)]}


import stage2  # noqa: E402
ai.jsonc = stage2.agree(fake_jsonc, None)  # stage 2 answers as stage 1 (tests/stage2.py)


def seed():
    """one poll: three posts, scored into the first stream"""
    streams.settings()  # the switch point before the poll, so its posts are scored
    streams.WAIT_TEXT = streams.timedelta(0)  # no page fetch to wait for
    now = app.datetime.now(app.HKT)
    got = [{"sid": "rthk", "source": "RTHK", "title": f"李家超 測試新聞 {i}",
            "url": f"https://news.test/{i}", "published": now, "text": "內文"} for i in range(3)]
    sources.fetch_all = lambda extra: (list(got), {"rthk": {"ok": True, "items": len(got), "error": None}})
    sources.discuss_search = lambda qs: []
    app.after_poll = lambda g, f: insights.after_poll(g, f)
    app.threading = type("T", (), {"Thread": lambda target, args, daemon: type("X", (), {"start": lambda s: target(*args)})()})
    app.load_state()
    app.poll_once()
    streams.run_once(app.STREAMS)


seed()
POST_ID = corpus.ids_for_urls(["https://news.test/0"])["https://news.test/0"]

# one valid call per tool, and what its result must hold
CASES = {
    "profiles_list": ({}, lambda r: any(p["id"] == STREAM for p in r["profiles"])),
    "posts_search": ({"profile": STREAM}, lambda r: r["total"] == 3 and r["posts"][0]["url"].startswith("https://news.test/")
                     and r["posts"][0]["item"].startswith("/item/")),
    "posts_get": ({"id": POST_ID}, lambda r: r["id"] == POST_ID and r["item"] == f"/item/{POST_ID}"),
    "stats_overview": ({"profile": STREAM}, lambda r: "metrics" in r and "scores_on_last_day" in r and r["note"]),
    "topics_hot": ({"profile": STREAM, "top": 5}, lambda r: r["topics"] and "title" in r["topics"][0]),
    "authors_top": ({"profile": STREAM}, lambda r: isinstance(r["authors"], list) and r["note"]),
    "sources_top": ({"profile": STREAM, "by": "website"}, lambda r: r["sources"] and r["note"]),
    "trend": ({"profile": STREAM, "bucket": "day"}, lambda r: r["trend"] and r["note"]),
    "reports_daily": ({"profile": STREAM}, lambda r: "day" in r),
    "wisers_results": ({"query": "x"}, lambda r: "articles" in r),
    "social_results": ({"profile": STREAM}, lambda r: "posts" in r),
    "skill_load": ({"name": "exec-summary"}, lambda r: r["name"] == "exec-summary" and "## Steps" in r["instructions"]),
}


class Tools(unittest.TestCase):
    def test_every_offered_tool_works(self):
        self.assertEqual(set(CASES), {t.name for t in tools.tools(paid=False)})  # a new free tool needs its case here
        for name, (args, ok) in CASES.items():
            with self.subTest(tool=name):
                r = tools.call(name, args, {"email": "a@yyc.hk"})
                self.assertTrue(ok(r), r)
                json.dumps(r, ensure_ascii=False, default=str)  # it goes back to the model as JSON

    def test_refused_arguments(self):
        for name, args, says in [("topics_hot", {"profile": STREAM, "top": "10"}, "top must be an integer"),
                                 ("topics_hot", {}, "profile is required"),
                                 ("topics_hot", {"profile": STREAM, "top": 99}, "top must be at most 30"),
                                 ("posts_search", {"profile": STREAM, "sentiment": "x"}, "sentiment must be one of"),
                                 ("sources_top", {"profile": STREAM, "by": "x"}, "by must be one of"),
                                 ("trend", {"profile": STREAM, "colour": 1}, "colour is not a parameter"),
                                 ("topics_hot", {"profile": STREAM, "colour": 1}, "colour is not a parameter"),
                                 ("topics_hot", {"profile": "NOPE"}, "no profile 'NOPE'"),
                                 ("posts_search", {"from": "13/10/2026", "query": "x"}, "from must be YYYY-MM-DD"),
                                 ("posts_search", {}, "give a profile or a query"),
                                 ("skill_load", {"name": "nope"}, "no skill 'nope'; skills: action-plan"),
                                 ("delete_everything", {}, "unknown tool")]:
            with self.subTest(tool=name, args=args), self.assertRaises(ValueError) as e:
                tools.call(name, args, {"email": "a@yyc.hk"})
            self.assertIn(says, str(e.exception))

    def test_feature_flags_by_site(self):
        """the main site is the simple monitor (stream + analysis); the beta site has every section"""
        for method, path in (("GET", f"/api/ai/estimate?kind=persona&p={STREAM}&author=x"), ("POST", "/app/ai/persona")):
            with self.subTest(path=path):
                self.assertEqual(call(method, path, who="a@yyc.hk", host="newswatch.yyc-internal.com")[0], 404)
                self.assertNotEqual(call(method, path, who="a@yyc.hk")[0], 404)
        self.assertEqual(flags.public("newswatch.yyc-internal.com"),
                         {"search": False, "ai": False, "reports": False, "pictures": False,
                          "transcription": False})
        self.assertTrue(all(flags.public(BETA).values()))

    def test_paid_tool_spend_no_budget(self):
        import tikhub_fetch
        from tools import spend
        os.environ["TIKHUB_API"] = "test"
        tikhub_fetch.search = lambda p, kw: ([{"source": "s", "title": kw, "url": "https://x/1", "published": "2026-10-13"}], None) \
            if p != "threads" else ([], "HTTP 400 upstream")
        who = {"email": "spend-test@yyc.hk"}
        r = tools.call("live_social_search", {"platform": "youtube", "query": "測試"}, who)
        self.assertEqual(r["charged_usd"], 0.002)
        with self.assertRaises(ValueError) as e:  # a failed search is not charged
            tools.call("live_social_search", {"platform": "threads", "query": "測試"}, who)
        self.assertIn("not charged", str(e.exception))
        for q in ("b", "c", "d"):  # no per-account limit: every call runs, each recorded
            self.assertEqual(tools.call("live_social_search", {"platform": "youtube", "query": q}, who)["charged_usd"], 0.002)
        mine = [x for x in spend.everyone() if x["email"] == who["email"]][0]
        self.assertEqual((mine["calls"], round(mine["usd"], 4)), (5, 0.008))  # the failed call: recorded, USD 0
        with self.assertRaises(ValueError):  # paid tools need an account (their cost is recorded against it)
            tools.call("live_social_search", {"platform": "youtube", "query": "b"}, {})
        self.assertTrue(all("PAID: " in x["function"]["description"] and "budget" not in x["function"]["description"]
                            for x in tools.openai_tools() if x["function"]["name"].startswith("live_")))

    def test_bad_header_is_loud(self):
        h = app.Handler({"REQUEST_METHOD": "GET", "PATH_INFO": "/", "QUERY_STRING": "", "wsgi.input": io.BytesIO()})
        with self.assertRaises(app.BadHeader):
            h.send(200, "text/plain", b"", extra={"Content-Disposition": 'attachment; filename="負面.csv"'})


class RealServer(unittest.TestCase):
    """gunicorn validates headers that the WSGI calls above don't: this is where the CSV button used to die"""

    @classmethod
    def setUpClass(cls):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        env = dict(os.environ, ADMIN_DEV="1")
        cls.p = subprocess.Popen([sys.executable, "-m", "gunicorn", "--chdir", HERE, "--bind", f"127.0.0.1:{cls.port}",
                                  "--workers", "1", "app:application"], env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE)
        deadline = time.time() + 180  # importing the app (jieba, OpenCC, the schema step) takes 10+ s on a busy box
        while time.time() < deadline and cls.p.poll() is None:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/healthz", timeout=5)
                return
            except Exception:
                time.sleep(0.2)
        cls.p.kill()
        raise RuntimeError("gunicorn did not start: " + cls.p.communicate()[1].decode(errors="replace")[-2000:])

    @classmethod
    def tearDownClass(cls):
        cls.p.terminate()
        cls.p.wait(10)

    def get(self, path):
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"Host": BETA}), timeout=30) as r:
            return r.status, r.headers, r.read()

    def test_ai_server_answers(self):
        code, hdr, body = self.get(f"/api/ai/estimate?kind=persona&p={STREAM}&author=x")  # the persona's projected cost (no model call)
        self.assertEqual(code, 200)
        self.assertIn("usd", json.loads(body))
        code, hdr, body = self.get("/app/api/v1/openapi.json")
        self.assertEqual(code, 200)
        self.assertIn("/app/api/v1/posts_search", json.dumps(json.loads(body)["paths"]))
        no_redirect = type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda *a: None})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/app/login?next=/item/1", headers={"Host": BETA})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.build_opener(no_redirect).open(req, timeout=30)
        self.assertEqual((e.exception.code, e.exception.headers["Location"]), (302, "/item/1"))


if __name__ == "__main__":
    unittest.main()
