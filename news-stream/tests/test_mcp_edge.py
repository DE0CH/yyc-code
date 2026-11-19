#!/usr/bin/env python3
"""YYC's news-monitoring REST API (mcp-edge/, Deployment yyc-mcp): personal and service tokens, YYC's access catalogue
as the list of who may use it, read-only tools over the same Postgres the edge site reads.

    MCP_DB=yyc_dev_own MCP_PG_PORT=5432 python3 tests/test_mcp_edge.py

Builds and starts the Node server (dist/server.js) on the dev copy of real data (MCP_DB, read as the app's data) and a
fresh database <MCP_DB>_mcp_test for its own tables (registry), plus a fake Access OIDC app on a local port for the web
pages' sign-in (/tokens, /admin): /authorization sends the browser straight back with a code naming the account the
test chose, /token and /userinfo answer for it. The server reads the issuer from OIDC_ISSUER (production: the Access
app "YYC MCP sign-in") and the catalogue from CATALOGUE, a temporary file the test edits (production:
the Secret yyc-catalogue).

1. No token: /api/v1/<tool> 401; /mcp is gone (404).
2. An account not on the catalogue can't sign in to /tokens (「沒有權限」); an "@domain" entry covers its addresses.
3. The tools over REST with real data: posts_search, post_get, streams_list, stream_items, analysis_posts, search,
   fetch; posts_search skips the substring-only matches while the word matches fill the page (total_exact false) and
   returns them past the word matches; bad arguments → 400 naming the problem.
4. /api/v1/openapi.json is public, lists every tool, bearer auth only.
5. The catalogue: an address added to it can make a token; removed, its token stops working once the server re-reads
   the file (403). /admin is the call log, for the catalogue's admins only.
6. Personal tokens: minted at /tokens (shown once), work on /api/v1, revoked → 401; a SERVICE_TOKENS token works, also
   for an identity outside the catalogue (YYC AI).
7. Rate limit: past the per-user limit → 429 with Retry-After. Audit: every call is logged (email, tool, via, status)
   and Deyao reads the log at /admin/api/audit.
8. Without the search dictionary (kv search:edge), posts_search falls back to a substring match and says so.
9. The front page (/) explains the API and the tokens.
10. Hardening: X-YYC-User names the person only for a service token (an ordinary personal token's header is ignored
    in the call log); a cancelled web sign-in → 400 page; a session cookie with a changed signature is refused.
"""
import base64, hashlib, http.server, json, os, re, secrets, signal, subprocess, sys, threading, time, unittest
import urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "mcp-edge")
DB = os.environ.get("MCP_DB", "yyc_dev_own")
PG_PORT = os.environ.get("MCP_PG_PORT", "5432")
MCP_DB = DB + "_mcp_test"
PORT = int(os.environ.get("MCP_PORT", "8788"))
FAKE = int(os.environ.get("MCP_FAKE_PORT", "8799"))
BASE = f"http://localhost:{PORT}"
DEYAO, UK, OTHER = "chendeyao000@gmail.com", "second@example.org", "colleague@example.com"
CATALOGUE = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"mcp-catalogue-{PORT}.json")
SERVICE = "yyc_pat_service" + secrets.token_urlsafe(24)   # a token set as the SERVICE_TOKENS secret
MACHINE = "yyc_pat_machine" + secrets.token_urlsafe(24)   # one for an identity outside the catalogue (YYC AI)

# ---- the catalogue (re-read by the server every 30 s) and a fake Access OIDC app ----

def write_catalogue(users):
    with open(CATALOGUE + ".tmp", "w") as f:
        json.dump({"users": users, "admins": [DEYAO]}, f)
    os.replace(CATALOGUE + ".tmp", CATALOGUE)


STATE = {"email": DEYAO}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/oidc/x/authorization":
            assert q.get("response_type") == ["code"] and q.get("code_challenge_method") == ["S256"], q
            code = "c-" + base64.urlsafe_b64encode(STATE["email"].encode()).decode()
            loc = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"code": code, "state": q["state"][0]})
            self.send_response(302)
            self.send_header("location", loc)
            self.end_headers()
        elif u.path == "/oidc/x/userinfo":
            email = base64.urlsafe_b64decode(self.headers["authorization"].split()[1][2:]).decode()
            self._json(200, {"sub": "a-" + hashlib.sha1(email.encode()).hexdigest()[:12], "email": email,
                             "name": email.split("@")[0]})
        else:
            self._json(404, {})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode()
        if self.path == "/oidc/x/token":
            f = urllib.parse.parse_qs(body)
            assert f.get("code_verifier"), "Access is asked with PKCE"
            assert f.get("client_secret") == ["test-secret"], "a confidential client"
            self._json(200, {"access_token": "t-" + f["code"][0][2:], "token_type": "Bearer", "expires_in": 3600})
        else:
            self._json(404, {})


# ---- a browser (cookies by hand: the __Host- cookies are Secure, urllib's jar won't send them over http) ----

class Browser:
    def __init__(self):
        self.cookies = {}

    def req(self, method, url, data=None, headers=None):
        h = dict(headers or {})
        if self.cookies:
            h["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data, doseq=True).encode()
            h.setdefault("content-type", "application/x-www-form-urlencoded")
        r = urllib.request.Request(url, data=data, method=method, headers=h)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(r, timeout=60)
            code, hdrs, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            code, hdrs, body = e.code, e.headers, e.read()
        for c in hdrs.get_all("set-cookie") or []:
            k, v = c.split(";")[0].split("=", 1)
            if "max-age=0" in c.lower() or "expires=thu, 01 jan 1970" in c.lower():
                self.cookies.pop(k, None)
            else:
                self.cookies[k] = v
        return code, hdrs, body.decode("utf-8", "replace")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(method, path, body=None, token=None, headers=None):
    h = {"accept": "application/json, text/event-stream"}
    h.update(headers or {})
    if token:
        h["authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["content-type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, resp.headers, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def tool(tok, name, args):
    code, _, body = call("POST", f"/api/v1/{name}", args, tok)
    assert code == 200, (code, body[:500])
    return json.loads(body)


def admin_login(email=DEYAO, path="/admin"):
    STATE["email"] = email
    b = Browser()
    code, h, page = b.req("GET", BASE + path)
    assert code == 302 and h["location"].startswith(BASE + "/login"), (code, page[:300])
    code, h, page = b.req("GET", h["location"])
    assert code == 302 and h["location"].startswith(ISSUER + "/authorization"), (code, page[:300])
    _, gh, _ = Browser().req("GET", h["location"])
    code, h, page = b.req("GET", gh["location"])
    assert code == 302, (code, page[:300])
    code, h, page = b.req("GET", BASE + path)
    return b, code, page


def mint(email=DEYAO, label="test"):
    """a personal token for `email`, made at /tokens as a signed-in person does"""
    b, code, page = admin_login(email, "/tokens")
    assert code == 200, (code, page[:300])
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    _, _, page = b.req("POST", BASE + "/tokens", {"csrf": csrf, "label": label, "days": "30"})
    return re.search(r"(yyc_pat_[A-Za-z0-9_-]{30,})", page).group(1)


FAKE_URL = f"http://127.0.0.1:{FAKE}"
ISSUER = FAKE_URL + "/oidc/x"
PROC = None


def seed_own():
    """on a database of its own (CI: no posts), what the tools read on a developer's copy of real data: the schema, the
    site's config and search dictionary, CEHK posts naming 李家超 as a word (with 特首, authors, scores, today's
    stream items) and a few holding it only inside other words (李家超市, 李家超群: the substring fallback's)"""
    from datetime import datetime, timedelta, timezone
    os.environ["DATABASE_URL"] = f"postgresql:///{DB}"
    sys.path.insert(0, HERE)
    import corpus, edge_config, searchtext
    db = corpus.db()  # the schema
    if db.execute("SELECT 1 FROM posts LIMIT 1").fetchone():
        return  # a copy of real data
    now = datetime.now(timezone(timedelta(hours=8)))
    word = [{"url": f"https://mcp-fixture.example/ce/{i}", "title": f"特首李家超今日出席第{i}場活動",
             "text": f"行政長官李家超今日出席第{i}場活動並致辭。" * 4, "sid": "rthk_en", "source": "RTHK",
             "author": f"記者{i % 5}", "published": (now - timedelta(hours=i)).isoformat(timespec="minutes")}
            for i in range(1, 61)]
    sub = [{"url": f"https://mcp-fixture.example/sub/{i}", "title": f"{'李家超市' if i % 2 else '李家超群'}第{i}則",
            "text": "與特首無關的地方消息。", "sid": "rthk_en", "source": "RTHK",
            "published": (now - timedelta(hours=i)).isoformat(timespec="minutes")} for i in range(1, 9)]
    corpus.ingest(word + sub)
    ids = corpus.ids_for_urls([x["url"] for x in word])
    at = now.isoformat(timespec="seconds")
    for k, x in enumerate(word):
        pid = ids[x["url"]]
        db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES ('CEHK', %s, %s, "
                   "'about the Chief Executive', 'news', %s) ON CONFLICT DO NOTHING", (pid, 90 - k % 40, at))
        if k < 10:
            it = {"url": x["url"], "title": x["title"], "source": "RTHK", "stream": "CEHK", "sentiment": "neutral",
                  "labels": [], "published": x["published"], "seen": at, "id": pid}
            db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                       (now.date().isoformat(), x["url"], corpus.J(it)))
    edge_config.publish()
    searchtext.publish_edge(force=True)


def setUpModule():
    global PROC
    seed_own()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FAKE), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    subprocess.run(["node", "build.mjs"], cwd=APP, check=True)
    admin = ["psql", "-q", "-p", PG_PORT, "-d", "postgres", "-c"]
    subprocess.run(admin + [f"DROP DATABASE IF EXISTS {MCP_DB}"], check=True)
    subprocess.run(admin + [f"CREATE DATABASE {MCP_DB}"], check=True)
    pg = f"postgres://claude:dev@127.0.0.1:{PG_PORT}/"
    write_catalogue([DEYAO, UK, "@yyc.test"])
    env = dict(os.environ, PORT=str(PORT), BASE_URL=BASE, OIDC_CLIENT_ID="test-client", OIDC_CLIENT_SECRET="test-secret",
               OIDC_ISSUER=ISSUER, CATALOGUE=CATALOGUE, CONSENT_SECRET=secrets.token_hex(32), RATE_PER_MIN="60",
               SERVICE_TOKENS=json.dumps({hashlib.sha256(SERVICE.encode()).hexdigest(): DEYAO,
                                          hashlib.sha256(MACHINE.encode()).hexdigest(): "yyc-ai@ai.yyc-internal.com"}),
               DATABASE_URL=pg + DB, MCP_DATABASE_URL=pg + MCP_DB, EGRESS_ALLOW="127.0.0.1")
    PROC = subprocess.Popen(["node", "dist/server.js"], cwd=APP, env=env, stdout=open("/tmp/mcp-edge-dev.log", "w"),
                            stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=3)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("the server did not start: see /tmp/mcp-edge-dev.log")


def tearDownModule():
    if PROC:
        os.killpg(PROC.pid, signal.SIGTERM)


class T01NoToken(unittest.TestCase):
    def test_unauthenticated(self):
        code, h, _ = call("GET", "/api/v1/posts_search?query=x")
        self.assertEqual(code, 401)
        self.assertIn("Bearer", h["www-authenticate"])
        code, _, _ = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(code, 404, "no MCP")


class T03NotOnCatalogue(unittest.TestCase):
    def test_denied(self):
        b, code, page = admin_login(OTHER, "/tokens")
        self.assertEqual(code, 403)
        self.assertIn("沒有權限", page)
        self.assertIn(OTHER, page)

    def test_domain_entry(self):
        self.assertTrue(mint("someone@yyc.test"))
        b, code, _ = admin_login("someone@notyyc.test", "/tokens")
        self.assertEqual(code, 403)


class T04Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tok = mint()
    def test_tools(self):
        streams = tool(self.tok, "streams_list", {})
        ids = [s["id"] for s in streams["streams"]]
        self.assertIn("CEHK", ids)
        found = tool(self.tok, "posts_search", {"query": "李家超", "page_size": 5})
        self.assertGreater(found["total"], 0)
        p = found["posts"][0]
        for k in ("id", "title", "url", "source", "time", "item"):
            self.assertIn(k, p)
        post = tool(self.tok, "post_get", {"id": p["id"]})
        self.assertEqual(post["id"], p["id"])
        self.assertIn("text", post)
        self.assertIn("scores", post)
        one = tool(self.tok, "posts_search", {"query": "李家超", "stream": "CEHK", "min_score": 50, "page_size": 3})
        for x in one["posts"]:
            self.assertGreaterEqual(x["score"], 50)
        day = tool(self.tok, "stream_items", {"stream": "CEHK"})
        self.assertIn("items", day)
        self.assertIn("days", day)
        a = tool(self.tok, "analysis_posts", {"stream": "CEHK"})
        self.assertIn("facets", a)
        self.assertIn("people", a["facets"])
        s = tool(self.tok, "search", {"query": "李家超"})
        self.assertTrue(s["results"] and {"id", "title", "url"} <= set(s["results"][0]))
        f = tool(self.tok, "fetch", {"id": s["results"][0]["id"]})
        self.assertTrue({"id", "title", "text", "url"} <= set(f))

    def test_posts_search_word_matches_first(self):
        """a query with more word matches than the page skips the substring-only part (total = word matches, a lower
        bound); past the word matches it still returns the substring-only rows, with the exact total"""
        full = tool(self.tok, "posts_search", {"query": "李家超", "page_size": 2})
        self.assertEqual(full.get("total_exact"), False, full)
        self.assertGreater(full["total"], 2)
        self.assertEqual([p["match"] for p in full["posts"]], ["word", "word"])
        self.assertIn("longer words are not counted", full["more"])
        past = tool(self.tok, "posts_search", {"query": "李家超", "page_size": 50, "page": full["total"] // 50 + 1})
        self.assertNotIn("total_exact", past)
        self.assertGreaterEqual(past["total"], full["total"])
        n = full["total"] % 50  # the last word matches, then the substring-only ones
        self.assertEqual([p["match"] for p in past["posts"]][:n], ["word"] * n)
        self.assertEqual(set(p["match"] for p in past["posts"][n:]) | {"substring"}, {"substring"})
        self.assertEqual(len(past["posts"]), min(50, past["total"] - (full["total"] // 50) * 50))

    def test_bad_args(self):
        code, _, body = call("POST", "/api/v1/post_get", {"id": "abc"}, self.tok)
        self.assertEqual(code, 400)
        self.assertIn("id", json.loads(body)["error"])


class T05Rest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tok = mint()

    def test_openapi(self):
        code, _, body = call("GET", "/api/v1/openapi.json")
        self.assertEqual(code, 200)
        spec = json.loads(body)
        self.assertTrue(spec["openapi"].startswith("3.1"))
        self.assertIn("/api/v1/posts_search", spec["paths"])
        for n in ("streams_list", "posts_search", "post_get", "stream_items", "analysis_posts", "daily_summary", "search", "fetch"):
            self.assertIn(f"/api/v1/{n}", spec["paths"])
        self.assertEqual(list(spec["components"]["securitySchemes"]), ["bearer"])

    def test_calls(self):
        code, _, body = call("GET", "/api/v1/posts_search?" + urllib.parse.urlencode({"query": "李家超", "page_size": 2}),
                             token=self.tok)
        self.assertEqual(code, 200, body)
        self.assertGreater(json.loads(body)["total"], 0)
        code, _, body = call("POST", "/api/v1/streams_list", {}, token=self.tok)
        self.assertEqual(code, 200, body)
        code, _, body = call("GET", "/api/v1/post_get?id=abc", token=self.tok)
        self.assertEqual(code, 400)
        self.assertIn("id", json.loads(body)["error"])

class T06Catalogue(unittest.TestCase):
    def test_add_then_remove(self):
        email = "staff@example.com"
        self.assertEqual(admin_login(email, "/tokens")[1], 403)
        write_catalogue([DEYAO, UK, "@yyc.test", email])
        try:
            time.sleep(31)  # the server re-reads the catalogue every 30 s
            t = mint(email)
            code, _, _ = call("GET", "/api/v1/streams_list", token=t)
            self.assertEqual(code, 200)
        finally:
            write_catalogue([DEYAO, UK, "@yyc.test"])
        time.sleep(31)
        code, _, body = call("GET", "/api/v1/streams_list", token=t)
        self.assertEqual(code, 403, body)
        self.assertIn("not on YYC's list", body)

    def test_call_log_page(self):
        b, code, page = admin_login()
        self.assertEqual(code, 200, page[:300])
        self.assertIn('<html lang="en">', page)
        self.assertIn("call log", page)
        self.assertNotIn("Approve", page)

    def test_non_admin_refused(self):
        b, code, page = admin_login(UK)
        self.assertEqual(code, 403)


class T07Pat(unittest.TestCase):
    def test_mint_use_revoke(self):
        b, code, page = admin_login(DEYAO, "/tokens")
        self.assertEqual(code, 200, page[:300])
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        code, h, page = b.req("POST", BASE + "/tokens", {"csrf": csrf, "label": "script", "days": "365"})
        self.assertEqual(code, 200)
        pat = re.search(r"(yyc_pat_[A-Za-z0-9_-]{30,})", page).group(1)
        code, _, body = call("GET", "/api/v1/streams_list", token=pat)
        self.assertEqual(code, 200, body)
        code, h, page = b.req("GET", BASE + "/tokens")
        self.assertNotIn(pat, page, "a token is shown only once")
        tid = re.search(r'name="id" value="([^"]+)"', page).group(1)
        b.req("POST", BASE + "/tokens/revoke", {"csrf": csrf, "id": tid})
        code, _, _ = call("GET", "/api/v1/streams_list", token=pat)
        self.assertEqual(code, 401)

    def test_service_token(self):
        code, _, body = call("GET", "/api/v1/streams_list", token=SERVICE)
        self.assertEqual(code, 200, body)
        code, _, body = call("GET", "/api/v1/streams_list", token=MACHINE)
        self.assertEqual(code, 200, body)
        code, _, _ = call("GET", "/api/v1/streams_list", token=SERVICE + "x")
        self.assertEqual(code, 401)

    def test_not_on_catalogue_cannot_mint(self):
        b, code, page = admin_login("nobody@example.com", "/tokens")
        self.assertEqual(code, 403)
        self.assertIn("沒有權限", page)


class T08RateAudit(unittest.TestCase):
    def test_rate_limit_and_audit(self):
        t = mint(UK)     # its own budget: RATE_PER_MIN=60 in the test
        seen = [call("GET", "/api/v1/streams_list", token=t) for _ in range(70)]
        limited = [x for x in seen if x[0] == 429]
        self.assertTrue(limited)
        self.assertTrue(limited[0][1].get("retry-after"))
        b, code, page = admin_login()
        code, _, body = b.req("GET", BASE + "/admin/api/audit?limit=50")
        self.assertEqual(code, 200)
        rows = json.loads(body)["calls"]
        self.assertTrue(any(x["email"] == UK and x["tool"] == "streams_list" and x["via"] == "rest" for x in rows))


class T09NoDictionary(unittest.TestCase):
    def test_substring_fallback(self):
        """a database without the search dictionary (Neon before the switch): posts_search still answers, and says how"""
        psql = ["psql", "-p", PG_PORT, "-d", DB, "-Atc"]
        subprocess.run(psql + ["CREATE TABLE IF NOT EXISTS kv_saved AS SELECT * FROM kv WHERE k = 'search:edge'; DELETE FROM kv WHERE k = 'search:edge'"], check=True)
        try:
            time.sleep(31)  # the server re-reads search:edge every 30 s
            t = mint(DEYAO)
            r = json.loads(call("GET", "/api/v1/posts_search?" + urllib.parse.urlencode({"query": "李家超 特首", "page_size": 3}), token=t)[2])
            self.assertIn("substring", r.get("search_mode", ""), r)
            self.assertGreater(r["total"], 0)
            code, _, body = call("GET", "/api/v1/reports_list", token=t)
            self.assertIn(code, (200, 400), body)
        finally:
            subprocess.run(psql + ["INSERT INTO kv SELECT * FROM kv_saved ON CONFLICT DO NOTHING; DROP TABLE kv_saved"], check=True)


class T11FrontPage(unittest.TestCase):
    def test_front_page(self):
        code, _, page = call("GET", "/", headers={"accept": "text/html"})
        self.assertEqual(code, 200)
        self.assertIn(BASE + "/api/v1/", page)
        self.assertIn("/tokens", page)
        self.assertNotIn("/mcp", page)
        self.assertIn('name="viewport"', page)
        self.assertNotIn("<script", page)


class T12Hardening(unittest.TestCase):
    def test_acting_for_only_with_a_service_token(self):
        b, code, page = admin_login(DEYAO, "/tokens")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        _, _, page = b.req("POST", BASE + "/tokens", {"csrf": csrf, "label": "spoof", "days": "30"})
        pat = re.search(r"(yyc_pat_[A-Za-z0-9_-]{30,})", page).group(1)
        call("GET", "/api/v1/sources_list", token=pat, headers={"x-yyc-user": "victim@yyc.test"})
        call("GET", "/api/v1/sources_list", token=SERVICE, headers={"x-yyc-user": "staff@yyc.test"})
        time.sleep(1)
        code, _, body = call("GET", "/admin/api/audit?limit=20", token=SERVICE)
        rows = [x for x in json.loads(body)["calls"] if x["tool"] == "sources_list"]
        clients = [x["client"] for x in rows]
        self.assertIn("personal-token", clients)
        self.assertIn("service token for staff@yyc.test", clients)
        self.assertFalse(any("victim" in (c or "") for c in clients), clients)

    def test_cancelled_web_sign_in(self):
        b = Browser()
        code, h, _ = b.req("GET", BASE + "/login?next=/tokens")
        state = urllib.parse.parse_qs(urllib.parse.urlparse(h["location"]).query)["state"][0]
        code, _, page = b.req("GET", BASE + "/callback?" + urllib.parse.urlencode({"error": "access_denied", "state": state}))
        self.assertEqual(code, 400, page[:300])
        self.assertIn("登入已取消", page)

    def test_tampered_session(self):
        b, code, _ = admin_login()
        self.assertEqual(code, 200)
        k = next(k for k in b.cookies if k.endswith("yyc-session"))
        v = b.cookies[k]
        b.cookies[k] = v[:-2] + ("AA" if v[-2:] != "AA" else "BB")
        code, h, _ = b.req("GET", BASE + "/admin")
        self.assertEqual(code, 302)
        self.assertIn("/login", h["location"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
