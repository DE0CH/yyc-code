#!/usr/bin/env python3
"""The cost profile (Deyao, 2026-10-16: "make sure we are collecting usage data, so we can do a cost analysis tomorrow
… see what each component uses … similar to running a profiler"): every paid call recorded with the job that made it,
hourly snapshots of each provider's own usage counters, and infra/cost-profile.py's report. Throwaway database
(TEST_DATABASE_URL; its tables are dropped and made again); no network: every provider is a fake.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_cost_profile.py

1. costlog.job(): YYC_JOB when set, else the Kubernetes workload from the pod name (a CronJob's pod
   <cronjob>-<minutes>-<rand>, a Deployment's <deploy>-<rs hash>-<rand>), else the script's name.
2. Every OpenRouter call's ai_runs row carries the job and OpenRouter's generation id (the id OpenRouter's own records
   use), from chat(), chat_stream() and the transcriber.
3. Every TikHub request (tikhub_fetch.call, ig_fetch.tikhub, comments_fetch.tikhub) is a paid_calls row: endpoint,
   HTTP status, job; a refused one (402) too, marked not ok. Every AnyAPI search is a row with the cost AnyAPI states.
4. Proxy bytes are counted per job and site per HKT hour (kv proxy:jobbytes:<job>:<site>:<hour>), beside the per-site
   counters.
5. The monitor stores every balances snapshot in provider_snapshots (one row per provider), so the providers' own
   counters can be differenced over any window; TikHub's snapshot keeps TikHub's per-endpoint counts (uri_counts),
   OpenRouter's the key's all-time usage.
6. infra/cost-profile.py: for a window, the cost per component and sub-component (model × purpose, proxy per site and
   job, TikHub per endpoint and job, solver per site, fixed infra), provider figures where they exist with our log
   beside them, the share of the total, the projection to a 30-day month and to the month's end with its basis, the
   top items flagged; markdown + CSV.
"""
import importlib.util, io, json, os, sys, unittest, urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
os.environ["OPENROUTER_API"] = "test-key"
os.environ["TIKHUB_API"] = "test-key"
os.environ["ANYAPI_API_KEY"] = "test-key"
import ai, corpus, costlog, monitor, resi, tikhub_fetch, fb_search, ig_fetch, comments_fetch  # noqa: E402

spec = importlib.util.spec_from_file_location("cost_profile", os.path.join(HERE, "infra", "cost-profile.py"))
cost_profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cost_profile)


def rows(sql, args=()):
    return [dict(r) for r in corpus.db().execute(sql, args)]


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Job(unittest.TestCase):
    def setUp(self):
        costlog._job = None

    def tearDown(self):
        costlog._job = None

    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"YYC_JOB": "handmade", "HOSTNAME": "tikhub-29341234-ab1cd",
                                          "KUBERNETES_SERVICE_HOST": "10.0.0.1"}):
            self.assertEqual(costlog.job(), "handmade")

    def test_pod_names(self):
        for host, want in (("tikhub-29341234-ab1cd", "tikhub"), ("poller-7d9f8c6b5-x2k4p", "poller"),
                           ("web-5c7d8f9b4d-abcde", "web"), ("facebook-29341260-zz9q1", "facebook"),
                           ("transcribe-66b9c8d5f-7hj2k", "transcribe")):
            costlog._job = None
            env = {"HOSTNAME": host, "KUBERNETES_SERVICE_HOST": "10.0.0.1"}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("YYC_JOB", None)
                self.assertEqual(costlog.job(), want, host)

    def test_outside_a_cluster_the_script(self):
        with mock.patch.dict(os.environ, {"HOSTNAME": "laptop"}), mock.patch.object(sys, "argv", ["/x/tikhub_fetch.py"]):
            os.environ.pop("YYC_JOB", None)
            os.environ.pop("KUBERNETES_SERVICE_HOST", None)
            self.assertEqual(costlog.job(), "tikhub_fetch")


class OpenRouterCalls(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM ai_runs")
        costlog._job = "poller"

    def tearDown(self):
        costlog._job = None

    def test_chat_records_job_and_generation(self):
        body = {"id": "gen-123", "choices": [{"message": {"content": "ok"}}],
                "usage": {"cost": 0.0012, "prompt_tokens": 10, "completion_tokens": 2}}
        with mock.patch.object(ai, "_open", lambda req, t: FakeResp(json.dumps(body).encode())):
            ai.chat([{"role": "user", "content": "hi"}], "scoring")
        r = rows("SELECT purpose, model, usd, job, generation FROM ai_runs")
        self.assertEqual(r, [{"purpose": "scoring", "model": ai.MODEL, "usd": 0.0012, "job": "poller",
                              "generation": "gen-123"}])

    def test_stream_records_generation(self):
        evs = [{"id": "gen-s1", "choices": [{"delta": {"content": "a"}}]},
               {"id": "gen-s1", "choices": [], "usage": {"cost": 0.002, "prompt_tokens": 5, "completion_tokens": 1}}]
        raw = "".join(f"data: {json.dumps(e)}\n\n" for e in evs) + "data: [DONE]\n\n"
        with mock.patch.object(ai, "_open", lambda req, t: FakeResp(raw.encode())):
            list(ai.chat_stream([{"role": "user", "content": "hi"}], "agent"))
        self.assertEqual(rows("SELECT job, generation FROM ai_runs"), [{"job": "poller", "generation": "gen-s1"}])

    def test_record_without_generation_still_has_job(self):
        ai._record("transcribe", "qwen/qwen3-asr-1.7b", [{"content": ""}], {"cost": 0.0001})
        self.assertEqual(rows("SELECT job, generation FROM ai_runs"), [{"job": "poller", "generation": None}])

    def test_transcriber_passes_the_generation(self):
        import inspect, media
        src = inspect.getsource(media)
        self.assertIn("generation=resp.get(\"id\")", src)


class PaidCalls(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM paid_calls")
        costlog._job = "tikhub"
        os.environ.pop("TIKHUB_REPLAY_DIR", None)

    def tearDown(self):
        costlog._job = None

    def test_tikhub_fetch_call(self):
        ok = json.dumps({"code": 200, "data": {"x": 1}}).encode()
        with mock.patch.object(tikhub_fetch.urllib.request, "urlopen", lambda req, timeout=None: FakeResp(ok)), \
                mock.patch.object(tikhub_fetch, "_pace", lambda: None):
            tikhub_fetch.call("/api/v1/twitter/web/fetch_search_timeline", {"keyword": "x"})

        def refuse(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, io.BytesIO(b'{"detail":"x"}'))
        with mock.patch.object(tikhub_fetch.urllib.request, "urlopen", refuse), \
                mock.patch.object(tikhub_fetch, "_pace", lambda: None):
            with self.assertRaises(tikhub_fetch.TikHubError):
                tikhub_fetch.call("/api/v1/twitter/web/fetch_user_post_tweet", {"screen_name": "x"})
        r = rows("SELECT provider, job, endpoint, status, ok FROM paid_calls ORDER BY id")
        self.assertEqual(r, [
            {"provider": "tikhub", "job": "tikhub", "endpoint": "/api/v1/twitter/web/fetch_search_timeline",
             "status": 200, "ok": True},
            {"provider": "tikhub", "job": "tikhub", "endpoint": "/api/v1/twitter/web/fetch_user_post_tweet",
             "status": 402, "ok": False}])

    def _curl(self, mod):
        class R:
            status_code = 200
            text = "{}"

            def json(self):
                return {"data": {"items": []}}
        with mock.patch("curl_cffi.requests.get", lambda *a, **k: R()), mock.patch.object(tikhub_fetch, "_pace", lambda: None):
            mod.tikhub("/api/v1/instagram/v2/fetch_post_comments", {"code": "x"})

    def test_ig_and_comments_calls(self):
        self._curl(ig_fetch)
        self._curl(comments_fetch)
        r = rows("SELECT endpoint, status, ok FROM paid_calls ORDER BY id")
        self.assertEqual(len(r), 2)
        self.assertTrue(all(x["endpoint"] == "/api/v1/instagram/v2/fetch_post_comments" and x["ok"] for x in r))

    def test_anyapi_search(self):
        body = json.dumps({"costUsd": 0.03, "output": {"data": {"items": []}}}).encode()
        with mock.patch.object(fb_search.urllib.request, "urlopen", lambda req, timeout=None: FakeResp(body)):
            fb_search.search("Galbot")
        r = rows("SELECT provider, endpoint, usd, basis, ok FROM paid_calls")
        self.assertEqual(r, [{"provider": "anyapi", "endpoint": "facebook.search_posts", "usd": 0.03,
                              "basis": "provider (costUsd in the response)", "ok": True}])

    def test_replay_records_nothing(self):
        os.environ["TIKHUB_REPLAY_DIR"] = "/nonexistent"
        try:
            with self.assertRaises(tikhub_fetch.TikHubError):
                tikhub_fetch.call("/api/v1/x/y", {})
        finally:
            os.environ.pop("TIKHUB_REPLAY_DIR")
        self.assertEqual(rows("SELECT * FROM paid_calls"), [])


class ProxyPerJob(unittest.TestCase):
    def test_count_per_job_and_site(self):
        costlog._job = "facebook"
        try:
            corpus.db().execute("DELETE FROM kv WHERE k LIKE 'proxy:%%'")
            resi.count(1000, "https://www.facebook.com/x")
            resi.count(500, "https://www.facebook.com/y")
            hour = datetime.now(corpus.HKT).strftime("%Y-%m-%dT%H")
            self.assertEqual(float(corpus.kv_get(f"proxy:jobbytes:facebook:facebook:{hour}")), 1500)
            self.assertEqual(float(corpus.kv_get(f"proxy:bytes:facebook:{hour}")), 1500)
        finally:
            costlog._job = None


class Snapshots(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM provider_snapshots")

    def test_balances_are_kept(self):
        fake = {"providers": [{"id": "tikhub", "status": "ok", "balance_usd": 3.2, "uri_counts": {"/a": 4}},
                              {"id": "exa", "status": "not_connected"}]}
        with mock.patch.dict(monitor.PANELS, {"balances": lambda: dict(fake)}):
            monitor.compute("balances")
        r = rows("SELECT provider, status, data FROM provider_snapshots ORDER BY provider")
        self.assertEqual([(x["provider"], x["status"]) for x in r], [("exa", "not_connected"), ("tikhub", "ok")])
        self.assertEqual(r[1]["data"]["uri_counts"], {"/a": 4})

    def test_tikhub_snapshot_has_endpoint_counts(self):
        def get(url, h=None, **k):
            if "get_user_info" in url:
                return {"user_data": {"email": "y", "balance": 1.0, "free_credit": 0}}
            return {"time_zone": "America/Los_Angeles",
                    "data": {"date": "2026-10-16", "usage": 2.5, "balance_usage": 2.5, "free_credit_usage": 0,
                             "total_request_per_day": 9, "paid_request_per_day": 9, "uri_counts": {"/api/v1/a": 9}}}
        with mock.patch.object(monitor, "get", get):
            b = monitor.bal_tikhub()
        self.assertEqual(b["uri_counts"], {"/api/v1/a": 9})
        self.assertEqual(b["paid_requests_today"], 9)

    def test_openrouter_snapshot_has_all_time_usage(self):
        with mock.patch.object(monitor, "get", lambda url, *a, **k: {"data": {"usage": 120.5, "usage_monthly": 3,
                                                                              "usage_daily": 1, "usage_weekly": 2}}), \
                mock.patch.dict(os.environ, {"OPENROUTER_API": "k"}):
            os.environ.pop("OPENROUTER_MANAGEMENT_KEY", None)
            b = monitor.bal_openrouter()
        self.assertEqual(b["usage_total_usd"], 120.5)


T0 = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=24)


class Profile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = corpus.db()
        for t in ("ai_runs", "paid_calls", "provider_snapshots", "solver_spend"):
            db.execute(f"DELETE FROM {t}")
        db.execute("DELETE FROM kv WHERE k LIKE 'proxy:%%'")
        at = T0 + timedelta(hours=2)
        for purpose, model, usd, n in (("images", "anthropic/claude-haiku-5.5", 0.01, 100),
                                       ("scoring", "anthropic/claude-haiku-5.5", 0.002, 50),
                                       ("transcribe", "qwen/qwen3-asr-1.7b", 0.001, 10)):
            for _ in range(n):
                db.execute("INSERT INTO ai_runs (at, purpose, model, usd, job) VALUES (%s,%s,%s,%s,%s)",
                           (at, purpose, model, usd, "images" if purpose == "images" else "poller"))
        db.execute("INSERT INTO ai_runs (at, purpose, model, usd) VALUES (%s,'scoring','m',5)", (T0 - timedelta(hours=1),))
        for _ in range(30):
            db.execute("INSERT INTO paid_calls (at, provider, job, endpoint, status, ok) VALUES (%s,'tikhub','watch',"
                       "'/api/v1/twitter/web/fetch_user_post_tweet',200,true)", (at,))
        db.execute("INSERT INTO paid_calls (at, provider, job, endpoint, status, ok) VALUES (%s,'tikhub','watch',"
                   "'/api/v1/twitter/web/fetch_user_post_tweet',402,false)", (at,))
        db.execute("INSERT INTO paid_calls (at, provider, job, endpoint, usd, basis, ok) VALUES (%s,'anyapi','tikhub',"
                   "'facebook.search_posts',0.03,'provider (costUsd in the response)',true)", (at,))
        db.execute("INSERT INTO solver_spend (at, day, provider, purpose, usd) VALUES (%s,'2026-10-16','2captcha','archive',0.003)",
                   (at.astimezone(corpus.HKT).isoformat(),))
        hour = at.astimezone(corpus.HKT).strftime("%Y-%m-%dT%H")
        corpus.kv_add(f"proxy:bytes:youtube:{hour}", 2_000_000_000)
        corpus.kv_add(f"proxy:jobbytes:media:youtube:{hour}", 2_000_000_000)
        corpus.kv_add(f"proxy:bytes:facebook:{hour}", 500_000_000)
        corpus.kv_add(f"proxy:jobbytes:facebook:facebook:{hour}", 500_000_000)
        snaps = [
            (T0, "openrouter", {"usage_total_usd": 100.0, "status": "ok"}),
            (T1, "openrouter", {"usage_total_usd": 101.3, "status": "ok"}),
            (T0, "tikhub", {"status": "ok", "balance_usd": 10, "usage_today_date": "2026-10-15", "usage_today_usd": 1.0,
                            "uri_counts": {"/api/v1/twitter/web/fetch_user_post_tweet": 5}}),
            (T0 + timedelta(hours=10), "tikhub", {"status": "ok", "balance_usd": 9, "usage_today_date": "2026-10-15",
                                                  "usage_today_usd": 1.2, "uri_counts": {"/api/v1/twitter/web/fetch_user_post_tweet": 25}}),
            (T1, "tikhub", {"status": "ok", "balance_usd": 8.9, "usage_today_date": "2026-10-16", "usage_today_usd": 0.1,
                            "uri_counts": {"/api/v1/twitter/web/fetch_user_post_tweet": 11}}),
            (T0, "iproyal", {"status": "ok", "price_per_gb_usd": 3.86, "subusers": [{"username": "yycstream", "traffic_used": 10.0}]}),
            (T1, "iproyal", {"status": "ok", "price_per_gb_usd": 3.86, "subusers": [{"username": "yycstream", "traffic_used": 12.5}]}),
            (T0, "anyapi", {"status": "ok", "balance_usd": 16.6}), (T1, "anyapi", {"status": "ok", "balance_usd": 16.57}),
            (T0, "2captcha", {"status": "ok", "balance_usd": 2.5}), (T1, "2captcha", {"status": "ok", "balance_usd": 2.4}),
        ]
        for t, p, d in snaps:
            db.execute("INSERT INTO provider_snapshots (at, provider, status, data) VALUES (%s,%s,%s,%s)",
                       (t, p, d.get("status"), corpus.J(d)))
        cls.prices = {"/api/v1/twitter/web/fetch_user_post_tweet": 0.001}
        cls.hetzner = {"currency": "EUR", "eur_usd": 1.1, "items": [{"name": "yyc-cluster (cx33)", "hourly_net": 0.0128},
                                                                     {"name": "volume data", "hourly_net": 0.001}]}
        cls.p = cost_profile.profile(T0, T1, tikhub_prices=cls.prices, hetzner=cls.hetzner, live=False)

    def comp(self, cid):
        return next(c for c in self.p["components"] if c["id"] == cid)

    def test_openrouter_provider_figure_and_split(self):
        c = self.comp("openrouter")
        self.assertAlmostEqual(c["provider_usd"], 1.3, places=6)
        self.assertAlmostEqual(c["app_usd"], 1.11, places=6)  # the call before the window is not counted
        subs = {s["name"]: s for s in c["subs"]}
        self.assertAlmostEqual(subs["images × anthropic/claude-haiku-5.5"]["app_usd"], 1.0)
        self.assertEqual(subs["images × anthropic/claude-haiku-5.5"]["calls"], 100)

    def test_tikhub_per_endpoint_and_job(self):
        c = self.comp("tikhub")
        # provider: TikHub's own day counters differenced over the window (its day rolls over before T1: 25-5 + 11
        # requests; USD 1.2-1.0 + 0.1), and its per-endpoint counts priced at its own endpoint list
        self.assertEqual(c["provider_calls"], 31)
        self.assertAlmostEqual(c["provider_usd"], 0.3, places=6)
        ep = {e["endpoint"]: e for e in c["provider_endpoints"]}
        self.assertAlmostEqual(ep["/api/v1/twitter/web/fetch_user_post_tweet"]["usd"], 0.031, places=6)
        subs = {s["name"]: s for s in c["subs"]}
        s = subs["watch · /api/v1/twitter/web/fetch_user_post_tweet"]
        self.assertEqual(s["calls"], 30)  # ok calls only: TikHub bills successful requests
        self.assertAlmostEqual(s["app_usd"], 0.03, places=6)

    def test_proxy_per_site_from_provider_gb(self):
        c = self.comp("iproyal")
        self.assertAlmostEqual(c["provider_usd"], 2.5 * 3.86, places=4)
        subs = {s["name"]: s for s in c["subs"]}
        self.assertIn("youtube (media)", subs)
        self.assertGreater(subs["youtube (media)"]["share_of_component"], 0.79)

    def test_solver_anyapi_hetzner(self):
        self.assertAlmostEqual(self.comp("solvers")["app_usd"], 0.003)
        self.assertAlmostEqual(self.comp("anyapi")["provider_usd"], 0.03, places=6)
        h = self.comp("hetzner")
        self.assertAlmostEqual(h["provider_usd"], (0.0128 + 0.001) * 24 * 1.1, places=6)

    def test_shares_projection_and_flags(self):
        p = self.p
        self.assertAlmostEqual(sum(c["share"] for c in p["components"]), 1.0, places=6)
        self.assertEqual(p["components"][0]["id"], "iproyal")  # sorted biggest first
        self.assertTrue(p["components"][0]["top"])
        self.assertAlmostEqual(p["projection"]["per_30_days_usd"], p["total_usd"] * 30, places=4)
        self.assertIn("basis", p["projection"])
        self.assertIn("month_end_usd", p["projection"])

    def test_partial_snapshots_fall_back_to_our_log(self):
        # snapshots covering only part of the window (collection started late) are shown, never used as the cost
        p = cost_profile.profile(T0 - timedelta(hours=24), T1, tikhub_prices=self.prices, hetzner=self.hetzner, live=False)
        c = next(x for x in p["components"] if x["id"] == "openrouter")
        self.assertLess(c["provider_coverage"], 0.95)
        self.assertEqual(c["cost_source"], "our log")
        self.assertAlmostEqual(c["cost_usd"], c["app_usd"] + 0, places=6)  # 1.11 + the earlier call (5)
        self.assertAlmostEqual(c["app_usd"], 6.11, places=6)

    def test_markdown_and_csv(self):
        md, csv_text = cost_profile.markdown(self.p), cost_profile.to_csv(self.p)
        self.assertIn("2026-10-16 03:00 UTC", md)
        self.assertIn("provider", md)
        self.assertIn("youtube (media)", csv_text)
        self.assertTrue(csv_text.splitlines()[0].startswith("component,sub,"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
