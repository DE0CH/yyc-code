#!/usr/bin/env python3
"""The monitor's Spending area breaks the metered providers down per job (Deyao, 2026-10-16: "in addition to the
OpenRouter job API cost breakdown by jobs, do an IPRoyal traffic breakdown; don't convert that to a dollar figure, just
GB/MB", then the same for TikHub, and AnyAPI). Data side; tests/test_edge_monitor_now.py renders it. Throwaway
database (TEST_DATABASE_URL); no network.

1. IPRoyal traffic (infra/cost-profile.py proxy_traffic, the same aggregation the cost profile's IPRoyal component
   uses): our count per job, each job's sites, biggest first, in BYTES (never USD), bytes a site counted in an hour
   with no job record as "(job not recorded)"; beside it IPRoyal's own figure for sub-user yycstream (its traffic_used
   counter, which is in decimal GB: 1 GB = 10^9 bytes, as IPRoyal's data-usage-report shows: 8323 B = 8.323 KB =
   0.008323 MB) with how much of the window its snapshots cover.
2. TikHub: USD per job, each job's endpoints (platform, calls, failed calls with their HTTP statuses, unit price,
   USD), biggest first; failed calls cost nothing (TikHub bills successful requests only: its own per-endpoint counts
   match our successful calls). TikHub's own figure (its day usage) beside our total.
3. AnyAPI the same way: the cost each search states, failed calls separate.
4. monitor.spending() carries the three for the last 6 h, the last 24 h and this month (HKT); this month's IPRoyal
   figure is IPRoyal's own usage report (GB per day) for the month's days. The proxy part has no USD anywhere.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_spend_breakdown.py
"""
import importlib.util, json, os, sys, unittest, unittest.mock
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
for k in ("OPENROUTER_API", "OPENROUTER_MANAGEMENT_KEY", "TIKHUB_API", "ANYAPI_API_KEY", "IPROYAL_API",
          "KUBERNETES_SERVICE_HOST", "MONITOR_WARM"):
    os.environ.pop(k, None)
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
import corpus, monitor  # noqa: E402

spec = importlib.util.spec_from_file_location("cost_profile", os.path.join(HERE, "infra", "cost-profile.py"))
cost_profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cost_profile)

HKT = corpus.HKT
END = datetime.now(timezone.utc).replace(microsecond=0)
PRICES = {"/api/v1/instagram/v2/fetch_post_comments": 0.001, "/api/v1/twitter/web/fetch_user_post_tweet": 0.002}


def hour(dt):
    return dt.astimezone(HKT).strftime("%Y-%m-%dT%H")


def keys(obj):
    """every key in a nested JSON value"""
    if isinstance(obj, dict):
        return set(obj) | set().union(*(keys(v) for v in obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*(keys(v) for v in obj)) if obj else set()
    return set()


def setup_data():
    db = corpus.db()
    for t in ("paid_calls", "provider_snapshots"):
        db.execute(f"DELETE FROM {t}")
    db.execute("DELETE FROM kv WHERE k LIKE 'proxy:%%'")
    h1, h20 = hour(END - timedelta(hours=1)), hour(END - timedelta(hours=20))
    # last hour: transcribe 3 GB on youtube; comments 200 MB facebook + 50 MB instagram; plus 10 MB of facebook with
    # no job record (counted before jobs were recorded)
    for job, site, n in (("transcribe", "youtube", 3_000_000_000), ("comments", "facebook", 200_000_000),
                         ("comments", "instagram", 50_000_000)):
        corpus.kv_add(f"proxy:jobbytes:{job}:{site}:{h1}", n)
    for site, n in (("youtube", 3_000_000_000), ("facebook", 210_000_000), ("instagram", 50_000_000)):
        corpus.kv_add(f"proxy:bytes:{site}:{h1}", n)
    # 20 h ago: poller 400 MB on hkej (in the last 24 h, not the last 6 h)
    corpus.kv_add(f"proxy:jobbytes:poller:hkej:{h20}", 400_000_000)
    corpus.kv_add(f"proxy:bytes:hkej:{h20}", 400_000_000)
    # IPRoyal's own counter: yycstream's traffic_used, GB, every 5 min over the last 25 h (rising 0.16 GB an hour)
    for i in range(25 * 12 + 1):
        at = END - timedelta(minutes=5 * (25 * 12 - i))
        d = {"status": "ok", "price_per_gb_usd": 3.86,
             "subusers": [{"username": "yycstream", "traffic_used": 10.0 + 0.16 * i / 12}]}
        db.execute("INSERT INTO provider_snapshots (at, provider, status, data) VALUES (%s,'iproyal','ok',%s)",
                   (at, corpus.J(d)))
    # TikHub: comments 100 ok + 3 failed (400) + 1 refused (402) on instagram comments; watch 20 ok on twitter
    at = END - timedelta(hours=2)
    for job, ep, status, ok, n in (("comments", "/api/v1/instagram/v2/fetch_post_comments", 200, True, 100),
                                   ("comments", "/api/v1/instagram/v2/fetch_post_comments", 400, False, 3),
                                   ("comments", "/api/v1/instagram/v2/fetch_post_comments", 402, False, 1),
                                   ("watch", "/api/v1/twitter/web/fetch_user_post_tweet", 200, True, 20)):
        for _ in range(n):
            db.execute("INSERT INTO paid_calls (at, provider, job, endpoint, status, ok) VALUES (%s,'tikhub',%s,%s,%s,%s)",
                       (at, job, ep, status, ok))
    for i in range(7 * 12 + 1):  # TikHub's own day usage: USD 0.02 an hour
        t = END - timedelta(minutes=5 * (7 * 12 - i))
        db.execute("INSERT INTO provider_snapshots (at, provider, status, data) VALUES (%s,'tikhub','ok',%s)",
                   (t, corpus.J({"status": "ok", "usage_today_date": "d", "usage_today_usd": 1 + 0.02 * i / 12,
                                 "uri_counts": {}})))
    # AnyAPI: 2 searches at USD 0.03 as stated, 1 failed
    for usd, ok, st in ((0.03, True, 200), (0.03, True, 200), (None, False, 402)):
        db.execute("INSERT INTO paid_calls (at, provider, job, endpoint, status, ok, usd) VALUES "
                   "(%s,'anyapi','tikhub','facebook.search_posts',%s,%s,%s)", (at, st, ok, usd))


class Breakdowns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_data()

    def test_proxy_per_job_and_site_in_bytes(self):
        p = cost_profile.proxy_traffic(END - timedelta(hours=6), END)
        jobs = [j["job"] for j in p["by_job"]]
        self.assertEqual(jobs, ["transcribe", "comments", "(job not recorded)"])  # biggest first; hkej is 20 h ago
        c = p["by_job"][1]
        self.assertEqual(c["bytes"], 250_000_000)
        self.assertEqual([(s["site"], s["bytes"]) for s in c["sites"]], [("facebook", 200_000_000), ("instagram", 50_000_000)])
        self.assertEqual(p["by_job"][2]["sites"], [{"site": "facebook", "bytes": 10_000_000}])
        self.assertEqual(p["bytes"], 3_260_000_000)
        self.assertEqual(p["bytes_per_gb"], 10 ** 9)
        # IPRoyal's own: 0.16 GB an hour × 6 h, in bytes (decimal GB)
        self.assertAlmostEqual(p["provider_bytes"], 0.96e9, delta=1e6)
        self.assertGreaterEqual(p["provider_coverage"], 0.95)
        self.assertIn("yycstream", p["provider_basis"])
        self.assertFalse(any("usd" in k.lower() for k in keys(p)), keys(p))

    def test_24h_has_the_poller(self):
        p = cost_profile.proxy_traffic(END - timedelta(hours=24), END)
        self.assertIn("poller", [j["job"] for j in p["by_job"]])
        self.assertEqual(p["bytes"], 3_660_000_000)
        # our hourly counts began 20 h ago in this data: the page says so beside the gap
        self.assertEqual(p["counted_from"], hour(END - timedelta(hours=20)))

    def test_cost_profile_iproyal_uses_the_same_counts(self):
        c = cost_profile.iproyal(END - timedelta(hours=6), END)
        self.assertEqual(sum(s["bytes"] for s in c["subs"]), 3_260_000_000)
        self.assertIn("youtube (transcribe)", {s["name"] for s in c["subs"]})
        self.assertIn("facebook (job not recorded)", {s["name"] for s in c["subs"]})

    def test_tikhub_per_job_and_endpoint(self):
        t = cost_profile.calls_by_job("tikhub", END - timedelta(hours=6), END, PRICES)
        self.assertEqual([j["job"] for j in t["by_job"]], ["comments", "watch"])  # 0.10 before 0.04
        c = t["by_job"][0]
        self.assertAlmostEqual(c["usd"], 0.1, places=9)
        self.assertEqual((c["calls"], c["failed_calls"]), (100, 4))
        e = c["endpoints"][0]
        self.assertEqual(e["platform"], "instagram")
        self.assertEqual(e["failed_statuses"], {"400": 3, "402": 1})
        self.assertEqual(e["unit_usd"], 0.001)
        self.assertAlmostEqual(t["usd"], 0.14, places=9)
        self.assertEqual((t["calls"], t["failed_calls"]), (120, 4))

    def test_tikhub_component_has_provider_and_jobs(self):
        c = cost_profile.tikhub(END - timedelta(hours=6), END, PRICES)
        self.assertAlmostEqual(c["provider_usd"], 0.12, delta=0.001)
        self.assertEqual(c["by_job"][0]["job"], "comments")

    def test_anyapi(self):
        a = cost_profile.calls_by_job("anyapi", END - timedelta(hours=6), END, {})
        self.assertAlmostEqual(a["usd"], 0.06, places=9)
        self.assertEqual((a["calls"], a["failed_calls"]), (2, 1))
        self.assertEqual(a["by_job"][0]["endpoints"][0]["platform"], "facebook")


class SpendingBreakdown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_data()
        month = datetime.now(HKT).strftime("%Y-%m")
        bal = {"providers": [{"id": "iproyal", "status": "ok", "price_per_gb_usd": 3.86,
                              "by_day_gb": {f"{month}-01": 1.5, "2000-01-01": 99.0, datetime.now(HKT).strftime("%Y-%m-%d"): 2.25}}]}
        with unittest.mock.patch.object(monitor, "panel", lambda name, fresh=False: bal), \
                unittest.mock.patch.object(monitor, "tikhub_prices", lambda: PRICES):
            cls.sp = monitor.spending()

    def test_windows(self):
        b = self.sp["breakdown"]
        self.assertEqual([w["key"] for w in b], ["6h", "24h", "month"])
        for w in b:
            for part in ("proxy", "tikhub", "anyapi"):
                self.assertIn(part, w)
        self.assertEqual(b[0]["proxy"]["by_job"][0]["job"], "transcribe")
        self.assertIn("poller", [j["job"] for j in b[1]["proxy"]["by_job"]])
        self.assertEqual(b[0]["tikhub"]["by_job"][0]["endpoints"][0]["failed_statuses"]["402"], 1)

    def test_month_provider_is_iproyals_report(self):
        m = self.sp["breakdown"][2]["proxy"]
        if datetime.now(HKT).day == 1:  # the 1st: both days are today
            self.assertAlmostEqual(m["provider_bytes"], 2.25e9, delta=1)
        else:
            self.assertAlmostEqual(m["provider_bytes"], 3.75e9, delta=1)  # this month's days only
        self.assertIn("report", m["provider_basis"])
        self.assertEqual(m["provider_coverage"], 1.0)

    def test_unit_is_named(self):
        u = self.sp["proxy_unit"]
        self.assertEqual(u["bytes_per_gb"], 10 ** 9)
        self.assertEqual(u["bytes_per_mb"], 10 ** 6)
        self.assertIn("1,000,000,000 bytes", u["note"])

    def test_proxy_has_no_usd(self):
        for w in self.sp["breakdown"]:
            self.assertFalse(any("usd" in k.lower() for k in keys(w["proxy"])), w["key"])
            self.assertNotIn("USD", json.dumps(w["proxy"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
