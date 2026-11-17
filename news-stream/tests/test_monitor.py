#!/usr/bin/env python3
"""Deyao's monitor (monitor.py computes and publishes it; the page and /app/monitor/data are the edge app's,
web-app/src/routes/-MonitorPage.tsx and src/server/monitor.ts): his Access identity only, read-only.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_monitor.py

1. The cluster's AI server answers 404 on every monitor path, for Deyao too, and never calls the monitor Deployment;
   the edge app reads the snapshot from Postgres (kv monitor:data: its monitor.published, run in Node by
   tests/edge_call.py). Its gate (404 to everyone but Deyao) and the rendered page are tests/test_edge_ops.py's.
   A forged Access token is no identity on the AI server.
2. No write method: POST answers 404 and PUT/PATCH/DELETE/OPTIONS 405 on every monitor path, for Deyao too; the
   monitor service itself answers 405 to anything but GET/HEAD; the page sends only GETs and is linked nowhere.
3. monitor.data() runs against the test database with no provider key (providers "not connected", never a figure)
   and outside a cluster (the jobs panel reports its error, the others still fill).
5. /data answers at once from the last snapshot while panels are slow (live 2026-10-15: the first /data after a pod
   start took 81 s, over web.py's 40 s, and the page showed a 502): each panel is computed by its own background
   loop, /readyz answers 200 only once every panel has its first snapshot, and each panel carries its age.
7. Every computed panel is published into kv monitor:data on its own (pods and loops never overwrite each other's
   panels); published() gives data()'s shape with each panel's age now; a new pod whose first try fails keeps the
   snapshot the old pod published.
8. OpenRouter's card shows the account's credit (GET /api/v1/credits with the management key yyc-monitor, Secret
   monitor-keys): bought, used and the balance in USD, plus key yyc-stream's own spend; without the management key the
   card still shows the key's spend and says what the balance needs. No projection.
6. A provider out of credit is said as what happened ("OpenRouter: out of credit — requests failing with
   'Insufficient credits' since 21:07 HKT 14 Oct"), never as a provider "refusing".
9. A stop clears itself once the provider answers again (Deyao, 2026-10-20: "2Captcha: balance used up … since 03:38 HKT
   17 Oct" still showed after the top-up, because only a successful solve cleared it and nothing solved): while a stop
   is recorded, the monitor asks the provider's own read-only API (balance, key limit, traffic left) at most every
   PROBE_EVERY seconds, and clears the record when it says requests can be served; it stays when the provider still
   has nothing left, or when the monitor holds no key to ask with.
4. The poller's per-source health tracking (app.track) and the usual-day rule (monitor.usual_day).
"""
import glob, io, json, os, re, sys, time, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.pop("ADMIN_DEV", None)
for k in ("OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY", "TIKHUB_API", "ANYAPI_API_KEY", "IPROYAL_API", "HETZNER_YYC_READ",
          "CAPSOLVER_API", "TWOCAPTCHA_API", "KUBERNETES_SERVICE_HOST", "MONITOR_WARM", "AWS_YYC_MONITOR_ACCESS_KEY_ID",
          "AWS_YYC_MONITOR_SECRET_ACCESS_KEY"):
    os.environ.pop(k, None)
os.chdir(HERE)
import app, monitor, web  # noqa: E402
from edge_call import edge  # noqa: E402

DEYAO = web.ADMIN_EMAIL
REAL_ACCESS_USER = web.access_user
USER = {"who": None}
PATHS = ["/app/monitor", "/app/monitor/", "/app/monitor/data", "/app/monitor/x"]
PAGE = os.path.join(HERE, "web-app", "src", "routes", "-MonitorPage.tsx")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def call(method, path, who=None, headers=None, host="newswatch.yyc-internal.com"):
    USER["who"] = who
    p, _, q = path.partition("?")
    env = {"REQUEST_METHOD": method, "PATH_INFO": p, "QUERY_STRING": q, "RAW_URI": path, "HTTP_HOST": host,
           "wsgi.input": io.BytesIO(b"{}"), "CONTENT_LENGTH": "2", "CONTENT_TYPE": "application/json"}
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    got = {}
    body = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]), headers=dict(h))))
    return got["status"], body


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import corpus
        corpus.kv_set(monitor.KV, {"telegram": {"stored_at": time.time() - 30, "value": {"streams": ["stub"]}}})
        web.access_user = lambda h: USER["who"]

    @classmethod
    def tearDownClass(cls):
        web.access_user = REAL_ACCESS_USER

    def test_ai_server_has_no_monitor(self):
        for who in (None, "michelle.yeoh@yyc.hk", DEYAO):
            for p in PATHS:
                for host in ("newswatch.yyc-internal.com", "yyc-beta.deyaochen.com"):
                    self.assertEqual(call("GET", p, who, host=host)[0], 404, (who, p, host))

    def test_the_site_reads_the_published_snapshot(self):
        d = edge("monitor.published")
        self.assertEqual(d["telegram"]["streams"], ["stub"])
        self.assertGreaterEqual(d["telegram"]["age_seconds"], 30)
        self.assertTrue(d["sources"]["pending"])

    def test_web_never_calls_the_monitor_service(self):
        self.assertFalse(hasattr(web, "MONITOR_URL"))
        self.assertNotIn("monitor.yyc.svc", read(os.path.join(HERE, "web.py")))

    def test_no_write_methods(self):
        for who in (None, DEYAO):
            for p in PATHS:
                self.assertEqual(call("POST", p, who)[0], 404, (who, p))
                for m in ("PUT", "PATCH", "DELETE", "OPTIONS"):
                    self.assertEqual(call(m, p, who)[0], 405, (m, who, p))


class RealAccess(unittest.TestCase):
    """with web.access_user as deployed: no token or a forged one is no identity (the AI routes answer 403)"""
    def test_forged_and_missing_tokens(self):
        web.access_user = REAL_ACCESS_USER
        web._certs.update(at=9e18, keys={"k": (3233, 17)})  # no network: a fixed key the forged token can't match
        try:
            fake = ".".join(__import__("base64").urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip("=")
                            for x in ({"alg": "RS256", "kid": "k"}, {"email": DEYAO, "aud": [web.ACCESS_AUD],
                                                                   "iss": web.ACCESS_TEAM, "exp": 9e12})) + ".AAAA"
            p = "/app/api/v1/profiles_list"
            self.assertEqual(call("GET", p)[0], 403)
            self.assertEqual(call("GET", p, headers={"Cf-Access-Jwt-Assertion": fake})[0], 403)
            self.assertEqual(call("GET", p, headers={"Cf-Access-Authenticated-User-Email": DEYAO})[0], 403)
        finally:
            web._certs.update(at=0, keys={})


class Service(unittest.TestCase):
    def wsgi(self, method, path):
        got = {}
        body = b"".join(monitor.application({"REQUEST_METHOD": method, "PATH_INFO": path},
                                            lambda s, h: got.update(status=int(s.split()[0]))))
        return got["status"], body

    def test_only_get(self):
        for m in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            for p in ("/data", "/healthz", "/"):
                self.assertEqual(self.wsgi(m, p)[0], 405, (m, p))
        self.assertEqual(self.wsgi("GET", "/healthz"), (200, b"ok"))
        self.assertEqual(self.wsgi("GET", "/nope")[0], 404)

    def test_data_without_keys_or_cluster(self):
        monitor.refresh_all()
        code, body = self.wsgi("GET", "/data")
        self.assertEqual(code, 200)
        d = json.loads(body)
        for k in ("sources", "telegram", "pipeline", "balances", "spending"):
            self.assertNotIn("error", d[k], k)
        self.assertIn("error", d["jobs"])  # not in a cluster: said, not faked
        provs = {b["id"]: b for b in d["balances"]["providers"]}
        for pid in ("openrouter", "tikhub", "anyapi", "iproyal", "hetzner", "aws", "exa", "cloudflare"):
            self.assertEqual(provs[pid]["status"], "not_connected", pid)
            self.assertTrue(provs[pid]["needs"])
            self.assertFalse(any(k.endswith(("_usd", "_gb")) for k in provs[pid]), pid)
        ip = next(s for s in d["spending"]["series"] if s["id"] == "iproyal")
        self.assertIsNone(ip["mtd_usd"])
        self.assertEqual(len(d["spending"]["days"]), 30)

    def test_page_sends_only_gets(self):
        src = read(PAGE) + read(os.path.join(HERE, "web-app", "src", "monitorData.ts"))
        self.assertNotRegex(src, r"(?i)method\s*:\s*['\"](post|put|patch|delete)")
        self.assertNotRegex(src, r"(?i)<form|alert\(|confirm\(|prompt\(")
        self.assertEqual(re.findall(r"fetch\(\"([^\"]+)\"", src), ["/app/monitor/data"])

    def test_not_in_navigation(self):
        src = os.path.join(HERE, "web-app", "src")
        pages = (glob.glob(os.path.join(src, "shell", "**", "*.ts*"), recursive=True)
                 + glob.glob(os.path.join(src, "routes", "*.tsx")))
        self.assertGreater(len(pages), 10)
        for f in pages:
            if "monitor" not in os.path.basename(f).lower():
                self.assertNotIn("/app/monitor", read(f), f)


KEY = {"data": {"limit": 150, "limit_remaining": 109.73, "usage": 40.27, "usage_daily": 0.97, "usage_weekly": 40.27,
                "usage_monthly": 40.27, "effective_budget": {"limit_usd": 150, "remaining_usd": 109.73,
                                                             "resets_at": "2026-11-01T00:00:00.000Z"}}}


CREDITS = {"data": {"total_credits": 390, "total_usage": 370.714545278}}


def or_get(url, headers=None, *a, **k):
    if url.endswith("/api/v1/credits"):
        assert headers["Authorization"] == "Bearer mgmt", "credits must be read with the management key"
        return CREDITS
    assert headers["Authorization"] == "Bearer test", "key usage must be read with key yyc-stream"
    return KEY


class OpenRouterCredit(unittest.TestCase):
    """Deyao, 2026-10-15: the monitor shows the OpenRouter account's credit balance (management key yyc-monitor,
    only in the monitor's Secret monitor-keys): bought, used, balance; never a run-out date or projection"""
    def tearDown(self):
        for k in ("OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY"):
            os.environ.pop(k, None)

    def test_credit_with_management_key(self):
        os.environ.update(OPENROUTER_API="test", OPENROUTER_MANAGEMENT_KEY="mgmt")
        with unittest.mock.patch.object(monitor, "get", or_get):
            b = monitor.bal_openrouter()
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["credits_bought_usd"], 390)
        self.assertAlmostEqual(b["credits_used_usd"], 370.7145, places=4)
        self.assertAlmostEqual(b["credit_balance_usd"], 19.2855, places=4)
        self.assertEqual(b["usage_month_usd"], 40.27)  # key yyc-stream's own spend, as before
        self.assertIn("/api/v1/credits", b["source"])
        self.assertNotIn("credit_needs", b)
        for k in NoBudgetDisplay.BUDGET_KEYS + ("projected_usd", "runs_out"):
            self.assertNotIn(k, b)

    def test_without_management_key_says_what_it_needs(self):
        os.environ["OPENROUTER_API"] = "test"
        with unittest.mock.patch.object(monitor, "get", or_get):
            b = monitor.bal_openrouter()
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["usage_month_usd"], 40.27)
        self.assertNotIn("credit_balance_usd", b)
        self.assertIn("OPENROUTER_MANAGEMENT_KEY", b["credit_needs"])

    def test_management_key_only_in_monitor_keys(self):
        """the management key is in the monitor's Secret alone: no other workload's env names monitor-keys"""
        dep = os.path.join(HERE, "deploy", "apps")
        for f in os.listdir(dep):
            if f.endswith(".yaml") and f != "monitor.yaml" and f != "kustomization.yaml":
                self.assertNotIn("monitor-keys", read(os.path.join(dep, f)), f)
        self.assertIn("OPENROUTER_MANAGEMENT_KEY", read(os.path.join(dep, "monitor.yaml")))


class NoBudgetDisplay(unittest.TestCase):
    """Deyao, 2026-10-15: "don't display when it runs out etc (the progress bar), because we removed the budget
    logic". The monitor shows facts (spend per day/month, per provider and job, balances, a provider's requests failing), never
    a cap's headroom, a run-out date or a projection held to a limit."""
    BUDGET_KEYS = ("limit_usd", "remaining_usd", "runs_out_at", "runs_out_before_reset", "resets_at", "cap_usd",
                   "month_cap_usd")

    def test_openrouter_card_has_no_cap_or_runout(self):
        os.environ["OPENROUTER_API"] = "test"
        try:
            with unittest.mock.patch.object(monitor, "get", lambda url, *a, **k: KEY):
                b = monitor.bal_openrouter()
        finally:
            os.environ.pop("OPENROUTER_API", None)
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["usage_month_usd"], 40.27)
        self.assertEqual(b["usage_today_usd"], 0.97)
        for k in self.BUDGET_KEYS:
            self.assertNotIn(k, b)

    def test_spending_has_no_caps(self):
        orb = {"id": "openrouter", "status": "ok", "usage_month_usd": 140.0, "rate_usd_per_hour": 5.0}
        ipr = {"id": "iproyal", "status": "ok", "price_per_gb_usd": 5.0, "by_day_gb": {"2026-10-01": 1.0}}
        with unittest.mock.patch.object(monitor, "panel", lambda name, fresh=False: {"providers": [orb, ipr]}):
            sp = monitor.spending()
        for s in sp["series"]:  # no cap and no projection at all (Deyao, 2026-10-16: the monitor shows facts)
            self.assertNotIn("cap_usd", s)
            self.assertNotIn("projected_usd", s)
            self.assertNotIn("projection", s)
        self.assertNotIn("total_projected_usd", sp)
        self.assertFalse(hasattr(monitor, "IPROYAL_MONTH_CAP"))

    def test_page_draws_no_meter_or_runout(self):
        src = read(PAGE)
        for s in ("meter", "runs_out", "Runs out", "remaining_usd", "limit_usd", "cap_usd", "month_cap", "left of",
                  "OpenRouter left", "before the reset", "without asking", "w.today || {}).cap"):
            self.assertFalse(s in src, s)


class Stops(unittest.TestCase):
    """a provider's requests failing for credit, balance or a limit is an event the monitor states plainly: who, the
    error the provider returned, since when"""
    def tearDown(self):
        import corpus
        corpus.kv_set("openrouter_limit", None)
        corpus.kv_set("social_limit", None)

    def test_none_when_nothing_fails(self):
        import corpus
        corpus.kv_set("openrouter_limit", None)
        corpus.kv_set("social_limit", None)
        self.assertEqual(monitor.stops()["providers"], [])

    def test_openrouter_and_social_stops(self):
        import corpus
        corpus.kv_set("openrouter_limit", {"since": "2026-10-14T21:07:00+08:00", "at": "2026-10-14T21:30:00+08:00",
                                           "message": "Insufficient credits. Add more using https://openrouter.ai/settings/credits",
                                           "notified": True})
        corpus.kv_set("social_limit", {"tikhub": {"since": "2026-10-15T08:00:00+08:00", "message": "402 balance",
                                                  "notified": False}})
        st = {p["id"]: p for p in monitor.stops()["providers"]}
        self.assertEqual(st["openrouter"]["name"], "OpenRouter")
        self.assertEqual(st["openrouter"]["tag"], "out of credit")
        self.assertEqual(st["openrouter"]["error_text"], "Insufficient credits")
        self.assertEqual(st["openrouter"]["since"], "2026-10-14T21:07:00+08:00")
        self.assertEqual(st["openrouter"]["last_failed"], "2026-10-14T21:30:00+08:00")
        self.assertTrue(st["openrouter"]["notified"])
        self.assertEqual(st["tikhub"]["name"], "TikHub")
        self.assertEqual(st["tikhub"]["tag"], "balance used up")
        self.assertIn("stops", monitor.PANELS)
        for p in st.values():
            self.assertNotIn("refus", json.dumps(p).lower())

    def test_tags_follow_the_recorded_error(self):
        for msg, tag in (("Insufficient balance", "balance used up"), ("Key limit exceeded", "limit reached"),
                         ("HTTP 402", "HTTP 402"), ("HTTP 403: forbidden", "HTTP 403"), (None, "requests failing")):
            self.assertEqual(monitor.stop_tag(msg), tag, msg)

    def test_key_limit_reason(self):
        import corpus
        corpus.kv_set("openrouter_limit", {"since": "2026-10-14T21:07:00+08:00", "message": "Key limit exceeded"})
        self.assertEqual(monitor.stops()["providers"][0]["tag"], "limit reached")


class StopsClearThemselves(unittest.TestCase):
    """the stop records (kv solver_limit, openrouter_limit, social_limit, proxy_out) are cleared by the monitor's probe
    of the provider's own API once it answers again, not only by the next job request that works"""
    KEYS = ("TWOCAPTCHA_API", "CAPSOLVER_API", "OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY", "TIKHUB_API",
            "ANYAPI_API_KEY", "IPROYAL_API")
    STOP = {"since": "2026-10-17T03:38:23+08:00", "at": "2026-10-19T22:20:05+08:00", "notified": True,
            "message": "ERROR_ZERO_BALANCE Your balance is zero or negative. Please top up your balance."}

    def setUp(self):
        import corpus
        for k in ("solver_limit", "openrouter_limit", "social_limit", "proxy_out"):
            corpus.kv_set(k, None)
        monitor._probed.clear()

    def tearDown(self):
        self.setUp()
        for k in self.KEYS:
            os.environ.pop(k, None)

    def ids(self):
        return {p["id"] for p in monitor.stops()["providers"]}

    def test_2captcha_clears_when_it_has_balance(self):
        import corpus
        corpus.kv_set("solver_limit", {"2captcha": self.STOP, "capsolver": dict(self.STOP, message="ERROR_ZERO_BALANCE")})
        os.environ.update(TWOCAPTCHA_API="k2", CAPSOLVER_API="kc")
        asked = []

        def post_read(url, body, timeout=25):
            asked.append(url)
            if "2captcha" in url:
                return {"errorId": 0, "balance": 50.0}
            return {"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE", "errorDescription": "zero"}
        with unittest.mock.patch.object(monitor, "post_read", post_read):
            self.assertEqual(self.ids(), {"capsolver"})
        self.assertEqual(set(corpus.kv_get("solver_limit")), {"capsolver"}, "the record itself is cleared")
        self.assertEqual(len(asked), 2)

    def test_no_key_no_probe(self):
        import corpus
        corpus.kv_set("solver_limit", {"2captcha": self.STOP})
        with unittest.mock.patch.object(monitor, "post_read", lambda *a, **k: self.fail("no key: nothing to ask with")):
            self.assertEqual(self.ids(), {"2captcha"})

    def test_probe_at_most_every_probe_every(self):
        import corpus
        corpus.kv_set("solver_limit", {"2captcha": self.STOP})
        os.environ["TWOCAPTCHA_API"] = "k2"
        asked = []

        def post_read(url, body, timeout=25):
            asked.append(url)
            return {"errorId": 0, "balance": 0}
        with unittest.mock.patch.object(monitor, "post_read", post_read):
            self.assertEqual(self.ids(), {"2captcha"})
            self.assertEqual(self.ids(), {"2captcha"})
        self.assertEqual(len(asked), 1)
        self.assertGreaterEqual(monitor.PROBE_EVERY, 60)

    def test_a_failing_probe_keeps_the_stop(self):
        import corpus
        corpus.kv_set("solver_limit", {"2captcha": self.STOP})
        os.environ["TWOCAPTCHA_API"] = "k2"

        def post_read(url, body, timeout=25):
            raise OSError("network down")
        with unittest.mock.patch.object(monitor, "post_read", post_read):
            self.assertEqual(self.ids(), {"2captcha"})

    def test_openrouter_clears_with_credit_and_key_room(self):
        import corpus
        corpus.kv_set("openrouter_limit", dict(self.STOP, message="Insufficient credits"))
        os.environ.update(OPENROUTER_API="test", OPENROUTER_MANAGEMENT_KEY="mgmt")
        with unittest.mock.patch.object(monitor, "get", or_get):
            self.assertEqual(self.ids(), set())
        self.assertIsNone(corpus.kv_get("openrouter_limit"))

    def test_openrouter_stays_without_credit(self):
        import corpus
        corpus.kv_set("openrouter_limit", dict(self.STOP, message="Insufficient credits"))
        os.environ.update(OPENROUTER_API="test", OPENROUTER_MANAGEMENT_KEY="mgmt")
        spent = {"data": {"total_credits": 390, "total_usage": 390.2}}
        with unittest.mock.patch.object(monitor, "get", lambda url, headers=None, *a, **k: spent if url.endswith("/credits") else KEY):
            self.assertEqual(self.ids(), {"openrouter"})

    def test_openrouter_stays_at_the_key_limit(self):
        import corpus
        corpus.kv_set("openrouter_limit", dict(self.STOP, message="Key limit exceeded"))
        os.environ.update(OPENROUTER_API="test", OPENROUTER_MANAGEMENT_KEY="mgmt")
        full = {"data": dict(KEY["data"], limit_remaining=0)}
        with unittest.mock.patch.object(monitor, "get", lambda url, headers=None, *a, **k: CREDITS if url.endswith("/credits") else full):
            self.assertEqual(self.ids(), {"openrouter"})

    def test_tikhub_and_anyapi(self):
        import corpus
        corpus.kv_set("social_limit", {"tikhub": dict(self.STOP, message="HTTP 402 Insufficient balance"),
                                       "anyapi": dict(self.STOP, message="HTTP 402 credits")})
        os.environ.update(TIKHUB_API="t", ANYAPI_API_KEY="a")

        def get(url, headers=None, *a, **k):
            if "tikhub" in url:
                return {"user_data": {"email": "x", "balance": 60.97, "free_credit": 0}}
            return {"usd": 0}
        with unittest.mock.patch.object(monitor, "get", get):
            self.assertEqual(self.ids(), {"anyapi"})
        self.assertEqual(set(corpus.kv_get("social_limit")), {"anyapi"})

    def test_iproyal_clears_with_traffic_left(self):
        import corpus
        corpus.kv_set("proxy_out", {"since": "2026-10-20T09:20:00+08:00", "at": "2026-10-20T09:34:00+08:00",
                                    "message": "402 Payment Required", "jobs": {"fulltext_fetch": "x"}, "notified": True})
        os.environ["IPROYAL_API"] = "i"

        def get(url, headers=None, *a, **k):
            assert "residential-subusers" in url, url
            return {"data": [{"username": "other", "traffic_available": 0}, {"username": "yycstream", "traffic_available": 9.98}]}
        with unittest.mock.patch.object(monitor, "get", get):
            self.assertEqual(self.ids(), set())
        self.assertIsNone(corpus.kv_get("proxy_out"))

    def test_iproyal_stays_when_empty(self):
        import corpus
        corpus.kv_set("proxy_out", {"since": "2026-10-20T09:20:00+08:00", "message": "402 Payment Required", "jobs": {}})
        os.environ["IPROYAL_API"] = "i"
        with unittest.mock.patch.object(monitor, "get", lambda *a, **k: {"data": [{"username": "yycstream", "traffic_available": 0}]}):
            self.assertEqual(self.ids(), {"iproyal"})


class Published(unittest.TestCase):
    """the snapshots go to Postgres (kv monitor:data), panel by panel; the page reads them there"""
    def setUp(self):
        import corpus
        monitor.stop_warm()
        monitor._cache.clear()
        corpus.kv_set(monitor.KV, None)
        self.real = dict(monitor.PANELS)

    def tearDown(self):
        monitor.PANELS.update(self.real)
        monitor._cache.clear()

    def test_each_panel_published_alone(self):
        import corpus
        monitor.PANELS["telegram"] = lambda: {"streams": ["a"]}
        monitor.PANELS["stops"] = lambda: {"providers": []}
        monitor.compute("telegram")
        monitor.compute("stops")
        kv = corpus.kv_get(monitor.KV)
        self.assertEqual(set(kv), {"telegram", "stops"})
        self.assertEqual(kv["telegram"]["value"]["streams"], ["a"])
        self.assertIn("computed_at", kv["telegram"]["value"])
        self.assertAlmostEqual(kv["telegram"]["stored_at"], monitor._cache["telegram"][0], places=3)
        # another pod's panel survives this pod's publish of a different one
        corpus.db().execute("UPDATE kv SET v = v || jsonb_build_object('jobs', jsonb_build_object('stored_at', 1, 'value', jsonb_build_object('cronjobs', '[]'::jsonb))) WHERE k = %s", (monitor.KV,))
        monitor.compute("telegram")
        self.assertEqual(corpus.kv_get(monitor.KV)["jobs"]["value"], {"cronjobs": []})

    def test_published_has_the_shape_of_data(self):
        monitor.PANELS["telegram"] = lambda: {"streams": ["a"]}
        monitor.compute("telegram")
        pub, mine = monitor.published(), monitor.data()
        self.assertEqual(list(pub), list(mine))
        for k in monitor.PANELS:
            if k == "telegram":
                self.assertEqual({x: y for x, y in pub[k].items() if x != "age_seconds"},
                                 {x: y for x, y in mine[k].items() if x != "age_seconds"})
                self.assertLess(abs(pub[k]["age_seconds"] - mine[k]["age_seconds"]), 1)
            else:
                self.assertEqual(pub[k], mine[k])
        self.assertRegex(pub["at"], r"\+08:00$")

    def test_a_new_pod_keeps_the_published_snapshot_on_failure(self):
        import corpus
        corpus.kv_set(monitor.KV, {"telegram": {"stored_at": time.time() - 100, "value": {"streams": ["old pod"], "computed_at": "x"}}})

        def boom():
            raise RuntimeError("provider down")
        monitor.PANELS["telegram"] = boom
        monitor.compute("telegram")  # this process has no snapshot yet
        v = monitor.published()["telegram"]
        self.assertEqual(v["streams"], ["old pod"])
        self.assertIn("provider down", v["refresh_error"])
        self.assertGreaterEqual(v["age_seconds"], 100)

    def test_a_database_outage_does_not_stop_the_loop(self):
        with unittest.mock.patch.object(monitor.corpus, "db", side_effect=RuntimeError("db down")):
            monitor.PANELS["telegram"] = lambda: {"streams": ["a"]}
            self.assertEqual(monitor.compute("telegram")["streams"], ["a"])

    def test_not_reachable_over_the_network(self):
        y = read(os.path.join(HERE, "deploy", "apps", "monitor.yaml"))
        self.assertNotRegex(y, r"kind:\s*Service\b")
        self.assertNotIn("podSelector: {matchLabels: {app: web}}", y)
        self.assertIn("ingress: []", y)


class Snapshot(unittest.TestCase):
    """/data serves the last snapshot at once; slow panels refresh behind it"""
    SLOW = 3.0

    def setUp(self):
        monitor.stop_warm()
        monitor._cache.clear()
        self.real = dict(monitor.PANELS)
        self.calls = {k: 0 for k in self.real}

        def slow(name):
            def f():
                self.calls[name] += 1
                time.sleep(self.SLOW)
                return {"n": self.calls[name]}
            return f
        for k in self.real:
            monitor.PANELS[k] = slow(k)

    def tearDown(self):
        monitor.stop_warm()
        monitor.PANELS.update(self.real)
        monitor._cache.clear()

    def get(self, path):
        got = {}
        t = time.time()
        body = b"".join(monitor.application({"REQUEST_METHOD": "GET", "PATH_INFO": path},
                                            lambda s, h: got.update(status=int(s.split()[0]))))
        return got["status"], body, time.time() - t

    def test_data_answers_at_once_while_panels_are_slow(self):
        code, body, took = self.get("/data")  # cold: nothing computed yet
        self.assertEqual(code, 200)
        self.assertLess(took, 2)
        d = json.loads(body)
        for k in self.real:
            self.assertTrue(d[k].get("pending"), k)
        self.assertEqual(self.get("/readyz")[0], 503)
        monitor.start_warm()
        deadline = time.time() + self.SLOW * 3 + 5  # every panel in parallel, not one after another
        while self.get("/readyz")[0] != 200:
            self.assertLess(time.time(), deadline, "never ready")
            time.sleep(0.2)
        code, body, took = self.get("/data")
        self.assertEqual(code, 200)
        self.assertLess(took, 2)  # served from the snapshot while the next refresh runs
        d = json.loads(body)
        for k in self.real:
            self.assertEqual(d[k]["n"], 1, k)
            self.assertIn("computed_at", d[k])
            self.assertGreaterEqual(d[k]["age_seconds"], 0)
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_failed_refresh_keeps_the_last_snapshot(self):
        monitor.PANELS["telegram"] = lambda: {"streams": ["ok"]}
        monitor.compute("telegram")

        def boom():
            raise RuntimeError("db down")
        monitor.PANELS["telegram"] = boom
        monitor.compute("telegram")
        v = monitor.snapshot("telegram")
        self.assertEqual(v["streams"], ["ok"])
        self.assertIn("db down", v["refresh_error"])

    def test_fulltext_counts_match_one_pass(self):
        """the two index-backed counts give what one pass over every post's text gave, over the posts seen since the
        restart cutoff (corpus.FLOOR: older ones are never read, so never waiting; tests/test_cutoff_work.py)"""
        import corpus
        monitor.PANELS.update(self.real)
        c = corpus.db()
        c.execute("DELETE FROM posts WHERE url LIKE 'https://ft.test/%%'")
        t = monitor.now()
        rows = [("a", "News", None, None, t), ("b", "News", "", None, t), ("c", "Blog", "body", "x", t),
                ("d", "Official", None, "x", t), ("e", "News", None, None, t - monitor.timedelta(days=5)),
                ("f", "Social", None, None, t)]
        for u, st, text, cached, seen in rows:
            c.execute("INSERT INTO posts (url, source_type, text, cached_at, seen) VALUES (%s, %s, %s, %s, %s)",
                      ("https://ft.test/" + u, st, text, cached, monitor.iso(seen)))
        try:
            ft = monitor.pipeline()["fulltext"]
            types = ["News", "Official", "Blog"]
            old = c.execute("""SELECT count(*) FILTER (WHERE text IS NULL AND cached_at IS NULL AND seen >= %s),
                                      count(*) FILTER (WHERE text IS NULL AND cached_at IS NULL AND seen >= %s),
                                      count(*) FILTER (WHERE length(text) > 0), count(*)
                               FROM posts WHERE thread IS NULL AND source_type = ANY(%s)""",
                            (monitor.iso(max(t - monitor.timedelta(days=2), monitor.datetime.fromisoformat(corpus.FLOOR))),
                             corpus.FLOOR, types)).fetchone()
            self.assertEqual((ft["waiting_recent_2d"], ft["waiting_all"], ft["with_text"], ft["posts"]), tuple(old))
            self.assertGreaterEqual(ft["posts"], 5)
        finally:
            c.execute("DELETE FROM posts WHERE url LIKE 'https://ft.test/%%'")

    def test_probe_waits_for_the_first_snapshot(self):
        y = read(os.path.join(HERE, "deploy/apps/monitor.yaml"))
        probe = y.split("readinessProbe:")[1].split("livenessProbe:")[0]
        self.assertIn("/readyz", probe)


class Rules(unittest.TestCase):
    def test_track(self):
        v = app.track({"ok": False, "error": "boom"}, {"last_ok": "T0", "fails": 0})
        self.assertEqual((v["fails"], v["last_ok"], v["last_error"]), (1, "T0", "boom"))
        since = v["fail_since"]
        v2 = app.track({"ok": False, "error": "boom2"}, v)
        self.assertEqual((v2["fails"], v2["fail_since"], v2["last_error"]), (2, since, "boom2"))
        v3 = app.track({"ok": True, "error": None}, v2)
        self.assertEqual((v3["fails"], v3["fail_since"], v3["last_error"]), (0, None, "boom2"))
        self.assertTrue(v3["last_ok"])

    def test_usual_day(self):
        self.assertIsNone(monitor.usual_day({}))
        self.assertIsNone(monitor.usual_day({0: 4, 1: 900}))  # one day old: its backlog day only
        self.assertEqual(monitor.usual_day({1: 5, 2: 5, 3: 6, 4: 5, 5: 4, 6: 5, 7: 5, 8: 1}), 5)
        self.assertEqual(monitor.usual_day({1: 10, 2: 12, 3: 900}), 11)  # first day (backlog) left out


if __name__ == "__main__":
    unittest.main()
