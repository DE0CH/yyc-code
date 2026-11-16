#!/usr/bin/env python3
"""The full read-only WiseSearch relay (Deyao, 2026-10-29: "full feature relay for Wisers … only the read-only endpoints,
do not relay the write endpoints (e.g. bookmarks) … do not relay tracking components"; full text from YYC's own
corpus first, "for the ones we don't, log it").

    python3 tests/test_wisers_full.py        (a local Postgres at 127.0.0.1 with login claude/dev for the corpus case)

Against the fake WisersOne (tests/fake_wisers.py):
1. paths.py: every row is a path of WisersOne's own app (tests/fixtures/wisers_paths.txt, its bundle's path
   constants), no excluded path (writes, tracking, e-mail, the shared session's current search) is a row, and the
   relay refuses a path that is not a row before anything is sent;
2. no tracking: the relay's browser never requests the tag manager on WisersOne's home page; no call carries
   createSearchHistoryFlag true;
3. the form's lookups (config, options, labels, suggestions, media, checks) and the derived calls (tab counts, filter
   panel, hot keywords, similar, excerpt, merge counts) answer, the derived ones with the job's own criteria only
   (a caller's criteria are ignored), each sent once and then answered from the job's cache;
4. documents: Wisers when the corpus has nothing (logged, charged), the same document again from the kept copy (no
   second Wisers call), a quota refusal (60003) is not kept and says so; the corpus case: a post with the item's url
   answers with no Wisers call at all (logged served=corpus);
5. images: the quota gate runs once per document for everyone, the files are kept; a download is a job that hands
   Wisers explicit document ids and returns the file;
6. a document opened while a 45 s search runs answers within a few seconds (calls run side by side).
"""
import json, os, re, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "wisers"))
from fake_wisers import FakeWisers, Store, new_ns  # noqa: E402
import paths  # noqa: E402

W = FakeWisers(captcha=False, latency=0.2)
S = W.S
PG = dict(host="127.0.0.1", user="claude", password="dev")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def b64(d):
    import base64
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


TOKEN = f"{b64({'alg': 'RS256'})}.{b64({'email': 'michelle.yeoh@yyc.hk'})}.sig"  # no ACCESS_AUD here: the email is read as is


def corpus_db():
    """a throwaway database with a posts table holding the fake's doc2 article (its url), or None without Postgres"""
    try:
        import psycopg
        name = "wisers_test_" + str(os.getpid())
        with psycopg.connect(dbname="postgres", autocommit=True, connect_timeout=3, **PG) as c:
            c.execute(f"DROP DATABASE IF EXISTS {name}")
            c.execute(f"CREATE DATABASE {name}")
        with psycopg.connect(dbname=name, autocommit=True, **PG) as c:
            c.execute("CREATE TABLE posts (id BIGINT PRIMARY KEY, url TEXT, url_key TEXT, canonical_url TEXT, website TEXT, "
                      "source TEXT, title TEXT, text TEXT, day TEXT, ai_title TEXT, stitle TEXT, thread TEXT)")
            body = "我們自己的全文。" * 80
            c.execute("INSERT INTO posts VALUES (1, %s, %s, NULL, '明報網站', '明報', %s, %s, '2026-10-16', NULL, NULL, NULL)",
                      ("https://news.example.com/a2?id=1", "news.example.com/a2?id=1", "全文 報道 2", body))
        return name
    except Exception as e:  # noqa: BLE001
        print(f"(no local Postgres: the corpus case is skipped: {e})")
        return None


class Relay:
    def __init__(self, db=None):
        self.tmp = tempfile.mkdtemp(prefix="wisers-full-")  # the relay's own log file only
        self.ns = new_ns("full")
        self.store = Store(self.ns)
        W.stale_state(self.ns)
        self.port, self.stats = free_port(), free_port()
        pg = dict(WISERS_PG_HOST="127.0.0.1", WISERS_PG_USER="claude", WISERS_PG_PASSWORD="dev", WISERS_PG_DB=db,
                  WISERS_PG_SSL="disable") if db else {}
        e = dict(os.environ, **W.env(self.ns), PYTHONUNBUFFERED="1", WISERS_PROBE_SECS="0", WISERS_LOGIN_GAP="0",
                 WISERS_STATS_PORT=str(self.stats), **pg)
        self.log = open(os.path.join(self.tmp, "relay.log"), "w")  # noqa: SIM115
        self.p = subprocess.Popen([sys.executable, os.path.join(HERE, "wisers", "relay.py"), "--port", str(self.port),
                                   "--prefix", "/app/wisers/"], env=e, stdout=self.log, stderr=subprocess.STDOUT)
        t = time.time()
        while time.time() - t < 60:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=2)
                break
            except Exception:
                time.sleep(0.2)

    def req(self, path, data=None, raw=False, timeout=60):
        path = urllib.parse.quote(path, safe="/?&=%+:,")
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/app/wisers/api/{path}",
                                   data=json.dumps(data).encode() if data is not None else None,
                                   headers={"Cf-Access-Jwt-Assertion": TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                b = resp.read()
                return resp.status, (b if raw else json.loads(b)), resp.headers.get("Content-Type")
        except urllib.error.HTTPError as e:
            b = e.read()
            return e.code, (b if raw else json.loads(b or b"{}")), e.headers.get("Content-Type")

    def get(self, path, **k):
        return self.req(path, **k)[:2]

    def search(self, q):
        code, d = self.get("search?" + q)
        t = time.time()
        while d.get("job", {}).get("state") == "running" and time.time() - t < 120:
            code, d = self.get(f"job/{d['job']['id']}?wait=10")
        return d

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(30)
        except subprocess.TimeoutExpired:
            self.p.kill()

    def calls(self):
        return [json.loads(x) for x in self.store.lines("calls.jsonl")]


class FullRelay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = corpus_db()
        cls.r = Relay(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.r.stop()

    def test_1_paths_are_wisersone_paths_and_writes_are_none(self):
        with open(os.path.join(HERE, "tests", "fixtures", "wisers_paths.txt")) as f:
            bundle = set(f.read().split())
        for row in paths.ROWS:
            self.assertTrue(row[0] in bundle or row[0] + "/" in bundle, f"{row[0]} is not a path of WisersOne's app")
        for p in paths.EXCLUDED:
            self.assertIsNone(paths.row(p.rstrip("/")), f"{p} is excluded but relayed")
        for p in ("search/addNewsToMyFolder", "tagDocs", "preference/searchplanadd", "emailDocuments", "search/writeClickLog"):
            self.assertIsNone(paths.row(p))

    def test_2_no_tracking_and_no_search_history(self):
        d = self.r.search("q=歷史&period=7")
        self.assertEqual(d["job"]["state"], "ok")
        stats = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.r.stats}/stats", timeout=5))
        self.assertGreaterEqual(stats["tracking_blocked"], 1)  # the home page's tag manager was asked for and aborted
        for b in S["bodies"]:
            c = b["requests"]["g0"]["params"].get("criteria")
            if isinstance(c, dict):
                self.assertNotEqual(c.get("createSearchHistoryFlag"), True)
        it = d["result"]["items"][0]
        self.assertEqual((it["page"], it["has"]["img"], it["has"]["pdf"], it["circulation"]), ("A07", True, True, 75000))
        self.assertEqual(d["result"]["crit"]["pubType"], "all")

    def test_3_lookups_and_derived_calls(self):
        for path in ("config", "options", "labels?group=subject&id=0", "labels/suggest?group=subject&q=施政",
                     "labels/names?ids=a,b", "suggest?q=施政", "media?q=明", "media/names?ids=p1,p2", "media/p1",
                     "check?q=施政&period=7"):
            code, d = self.r.get(path)
            self.assertEqual(code, 200, (path, d))
        self.assertEqual(self.r.get("suggest?q=施政")[1]["words"], ["施政報告", "施政新聞"])
        self.assertFalse(self.r.get("check?q=的&period=7")[1]["ok"])  # a stop word
        self.assertEqual(self.r.get("check?q=清單&period=custom&from=2026-10-01&to=2026-10-29"), (200, {"ok": True, "problems": []}))
        self.assertEqual(self.r.get("search?and=&or=&nand=x")[0], 400)  # logic mode with only NOT words
        d = self.r.search("q=派生&period=7&mt=print")
        jid = d["job"]["id"]
        before = dict(S["paths"])
        for kind in ("counts", "media", "match", "hot", "hot_negative", "merge", "labels&type=subject&id=0",
                     "similar&doc=doc1&key=c1", "excerpt&doc=doc1"):
            code, x = self.r.get(f"derived?job={jid}&kind={kind}&keyword=injected&pubType=web")
            self.assertEqual(code, 200, (kind, x))
            echo = (x["data"] or {}).get("criteriaEcho") if isinstance(x["data"], dict) else None
            if echo:  # the job's own criteria, never the caller's
                self.assertEqual((echo["keyword"], echo["pubType"]), ("派生", "print"))
        self.assertEqual(self.r.get(f"derived?job={jid}&kind=counts")[1]["data"]["mediaCount"]["print"], 10)
        self.assertEqual(S["paths"].get("search/getMediaTypeCount", 0) - before.get("search/getMediaTypeCount", 0), 1)  # cached
        self.assertEqual(self.r.get("derived?job=nope&kind=counts")[0], 404)

    def test_4_documents(self):
        d = self.r.search("q=全文&period=7")
        jid = d["job"]["id"]
        n0 = S["paths"].get("search/getDocumentByDocId", 0)
        code, x = self.r.get(f"doc/doc1?job={jid}&reason=test")
        self.assertEqual(code, 200, x)
        self.assertEqual(x["from"], "wisers")
        self.assertIn("<mark>全文</mark>", x["html"])
        self.assertNotIn("script", x["html"])
        self.assertNotIn("onclick", x["html"])
        self.assertEqual((x["heading"]["page"], x["heading"]["source"]), ("A07", "明報"))
        self.assertEqual([s["docId"] for s in x["same_page"]], ["sp1", "doc1"])
        self.assertEqual(x["images"]["count"], 2)
        self.assertEqual(self.r.get(f"doc/doc1?job={jid}")[1]["from"], "wisers")  # the kept copy
        self.assertEqual(S["paths"]["search/getDocumentByDocId"] - n0, 1)
        q = self.r.get("doc/q60003x")[1]
        self.assertTrue(q["quota"]["blocking"])
        self.assertFalse(self.r.store.exists("docs/q60003x.json"))
        lines = [c for c in self.r.calls() if c["path"] == "search/getDocumentByDocId"]
        self.assertEqual([(c["doc"], c["outcome"], c["charged"]) for c in lines][-3:],
                         [("doc1", "ok", "yes"), ("doc1", "cached", "no"), ("q60003x", "quota", "no")])
        self.assertEqual(lines[-3]["who"], "michelle.yeoh@yyc.hk")
        if self.db:  # doc2's url is a post in the corpus: no Wisers call at all
            n1 = S["paths"]["search/getDocumentByDocId"]
            code, y = self.r.get(f"doc/doc2?job={jid}")
            self.assertEqual((code, y["from"]), (200, "corpus"), y)
            self.assertIn("我們自己的<mark>全文</mark>", y["html"])
            self.assertEqual(y["heading"]["headline"], "全文 報道 2")
            self.assertEqual(S["paths"]["search/getDocumentByDocId"], n1)
            self.assertEqual(self.r.calls()[-1]["served"], "corpus")

    def test_5_images_and_download(self):
        self.r.search("q=圖片&period=7")
        g0 = S["paths"].get("search/writeClickLogByClickType", 0)
        code, d = self.r.get("doc/doc1/images")
        self.assertEqual((code, d["count"]), (200, 2), d)
        code, png, _ = self.r.req("doc/doc1/image/1?size=thumb", raw=True)
        self.assertEqual((code, png[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.r.req("doc/doc1/image/1?size=full", raw=True)[0], 200)
        self.assertEqual(self.r.req("doc/doc1/image/1?size=full", raw=True)[0], 200)
        self.assertEqual(S["paths"]["search/writeClickLogByClickType"] - g0, 1)  # booked once for the document
        self.assertEqual(len([f for f in S["files"] if "/getImageByDocId/doc1/1" in f]), 1)  # kept
        self.assertEqual(self.r.get("doc/q60004x/image/1")[0], 402)
        code, j, _ = self.r.req("download", data={"docs": ["doc1", "doc2"], "content": "headline-content", "format": "txt"})
        self.assertEqual(code, 200, j)
        did = j["download"]["id"]
        t = time.time()
        while j["download"]["state"] == "running" and time.time() - t < 30:
            time.sleep(0.5)
            j = self.r.get(f"download/{did}")[1]
        self.assertEqual(j["download"]["state"], "ok", j)
        self.assertEqual(S["download"]["onWhat"], "selected")
        self.assertEqual(S["download"]["docIds"], ["doc1", "doc2"])
        code, body, ctype = self.r.req(f"download/{did}?file=1", raw=True)
        self.assertEqual((code, body.decode().split()), (200, ["doc1", "doc2"]))

    def test_6_calls_run_beside_a_search(self):
        jid = self.r.get("search?q=slow45並行&period=7", timeout=40)[1]["job"]["id"]  # api/search itself waits 20 s
        t = time.time()
        code, x = self.r.get("doc/doc3")
        self.assertEqual(code, 200, x)
        self.assertLess(time.time() - t, 8)
        self.assertEqual(self.r.get(f"job/{jid}")[1]["job"]["state"], "running")

    def test_7_refused(self):
        self.assertEqual(self.r.get("labels?group=evil&id=0")[0], 400)
        self.assertEqual(self.r.get("derived?job=x&kind=../../etc")[0], 404)
        import relay
        with self.assertRaises(PermissionError):
            relay.Call("search/addNewsToMyFolder", {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
