#!/usr/bin/env python3
"""The monitor's provider cards for Hetzner, Cloudflare and Exa (Deyao, 2026-10-16: "connect these as well"), and the
cost profile's Hetzner and Cloudflare components from the monitor's snapshots. No network: every provider is a fake.

    TEST_DATABASE_URL=postgresql://…/yyc_test python3 tests/test_provider_cards.py

1. Hetzner (HETZNER_YYC_READ, read-only token yyc-monitor of project yyc): the project's servers, IPs and volumes at
   Hetzner's list prices; without the token the card says what it needs.
2. Cloudflare (CLOUDFLARE_YYC_MONITOR_TOKEN + CLOUDFLARE_YYC_ACCOUNT_ID: token yyc-monitor, Account Analytics Read +
   Billing Read on account YYC): the account's subscriptions from Cloudflare (Workers Paid USD 5.00/month), and the
   billing period's Workers, Durable Objects and KV usage from Cloudflare's GraphQL analytics against the plan's
   included amounts, with the list-price cost of anything beyond them; without the token or the account id the card
   says what it needs.
3. Exa: Exa's per-key usage API (Team Management) is enabled only by Exa support, so the card shows Exa's own charged
   cost of each call (costDollars.total in Exa's responses, kept in tool_spend) this month and today, says the per-key
   API is not available and the plan's free credit; without EXA_API_KEY it says what it needs.
4. Spending lists Cloudflare's subscription beside Hetzner as fixed monthly costs.
5. cost-profile.py: Cloudflare = the subscription spread over the window's hours + the rise of the usage beyond the
   included amounts, from the monitor's snapshots; Hetzner from the monitor's snapshots when no --hetzner-json is
   given; Cloudflare is no longer listed as free.
"""
import importlib.util, json, os, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
import psycopg  # noqa: E402

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
os.chdir(HERE)
for k in ("HETZNER_YYC_READ", "CLOUDFLARE_YYC_MONITOR_TOKEN", "CLOUDFLARE_YYC_ACCOUNT_ID", "EXA_API_KEY",
          "KUBERNETES_SERVICE_HOST", "MONITOR_WARM"):
    os.environ.pop(k, None)
import corpus, monitor  # noqa: E402
from tools import spend  # noqa: E402

spec = importlib.util.spec_from_file_location("cost_profile", os.path.join(HERE, "infra", "cost-profile.py"))
cost_profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cost_profile)

ACCOUNT = "00916611232966eaf40906a57d09394e"
SUBS = {"success": True, "result": [
    {"rate_plan": {"id": "workers_paid", "public_name": "Workers Paid"}, "price": 5, "currency": "USD",
     "frequency": "monthly", "state": "Paid", "current_period_start": "2026-10-16T03:02:29Z",
     "current_period_end": "2026-11-16T00:00:00Z"},
    {"rate_plan": {"id": "images_v2_stream_basic", "public_name": "Images Stream Basic"}, "price": 0, "currency": "USD",
     "frequency": "monthly", "state": "Paid", "current_period_start": "2026-10-16T03:02:29Z",
     "current_period_end": "2026-11-16T00:00:00Z"}]}
GQL = {"data": {"viewer": {"accounts": [{
    "w": [{"dimensions": {"scriptName": "yyc-web"}, "sum": {"requests": 12_000_000, "cpuTimeUs": 24_250_500_000, "errors": 3}},
          {"dimensions": {"scriptName": "yyc-mcp"}, "sum": {"requests": 83, "cpuTimeUs": 698_040, "errors": 0}}],
    "do": [{"dimensions": {"scriptName": "yyc-web"}, "sum": {"requests": 1_500_000}}],
    "dop": [{"dimensions": {"namespaceId": "n1"}, "sum": {"activeTime": 4_000_000_000_000}}],  # 4e6 s × 0.125 GB = 500,000 GB-s
    "kv": [{"dimensions": {"actionType": "read"}, "sum": {"requests": 3}},
           {"dimensions": {"actionType": "write"}, "sum": {"requests": 4}},
           {"dimensions": {"actionType": "delete"}, "sum": {"requests": 1}}]}]}}, "errors": None}


def cf_get(url, headers=None, *a, **k):
    assert headers["Authorization"] == "Bearer cf-token", "Cloudflare must be read with token yyc-monitor"
    assert url == f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/subscriptions", url
    return SUBS


GQL_CALLS = []


def cf_gql(token, query, variables):
    assert token == "cf-token"
    GQL_CALLS.append(variables)
    return GQL


class Hetzner(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("HETZNER_YYC_READ", None)

    def test_without_token(self):
        b = monitor.bal_hetzner()
        self.assertEqual(b["status"], "not_connected")
        self.assertIn("HETZNER_YYC_READ", b["needs"])

    def test_list_prices(self):
        os.environ["HETZNER_YYC_READ"] = "hz"
        price = lambda m: [{"location": "fsn1", "price_monthly": {"net": str(m)}}]  # noqa: E731

        def get(url, headers=None, *a, **k):
            assert headers["Authorization"] == "Bearer hz"
            return {"servers": {"servers": [{"name": "yyc-cluster", "location": {"name": "fsn1"}, "status": "running",
                                             "created": "2026-10-13", "backup_window": None,
                                             "server_type": {"name": "cx33", "prices": price(5.49)}}]},
                    "pricing": {"pricing": {"currency": "EUR", "vat_rate": "19.00", "server_backup": {"percentage": "20"},
                                            "primary_ips": [{"type": "ipv4", "prices": price(0.5)}],
                                            "volume": {"price_per_gb_month": {"net": "0.044"}}}},
                    "primary_ips": {"primary_ips": []}, "volumes": {"volumes": [{"name": "data", "size": 10}]}}[
                url.rsplit("/", 1)[1]]
        with mock.patch.object(monitor, "get", get):
            b = monitor.bal_hetzner()
        self.assertEqual(b["status"], "ok")
        self.assertAlmostEqual(b["list_monthly_net"], 5.49 + 0.44, places=4)
        self.assertEqual(b["currency"], "EUR")


class Cloudflare(unittest.TestCase):
    def setUp(self):
        GQL_CALLS.clear()

    def tearDown(self):
        for k in ("CLOUDFLARE_YYC_MONITOR_TOKEN", "CLOUDFLARE_YYC_ACCOUNT_ID"):
            os.environ.pop(k, None)

    def test_without_token_or_account(self):
        b = monitor.bal_cloudflare()
        self.assertEqual(b["status"], "not_connected")
        self.assertIn("CLOUDFLARE_YYC_MONITOR_TOKEN", b["needs"])
        os.environ["CLOUDFLARE_YYC_MONITOR_TOKEN"] = "cf-token"
        b = monitor.bal_cloudflare()
        self.assertEqual(b["status"], "not_connected")
        self.assertIn("CLOUDFLARE_YYC_ACCOUNT_ID", b["needs"])

    def card(self):
        os.environ.update(CLOUDFLARE_YYC_MONITOR_TOKEN="cf-token", CLOUDFLARE_YYC_ACCOUNT_ID=ACCOUNT)
        with mock.patch.object(monitor, "get", cf_get), mock.patch.object(monitor, "cf_graphql", cf_gql):
            return monitor.bal_cloudflare()

    def test_subscription_and_usage(self):
        b = self.card()
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["plan"], "Workers Paid")
        self.assertEqual(b["subscriptions_monthly_usd"], 5)
        self.assertEqual([s["name"] for s in b["subscriptions"]], ["Workers Paid", "Images Stream Basic"])
        self.assertEqual((b["period_start"], b["period_end"]), ("2026-10-16T03:02:29Z", "2026-11-16T00:00:00Z"))
        # the analytics are read for the billing period
        self.assertEqual(GQL_CALLS[0]["start"], "2026-10-16T03:02:29Z")
        u = {x["id"]: x for x in b["usage"]}
        self.assertEqual(u["workers_requests"]["used"], 12_000_083)
        self.assertEqual(u["workers_requests"]["included"], 10_000_000)
        self.assertAlmostEqual(u["workers_requests"]["overage_usd"], 2_000_083 / 1e6 * 0.30, places=6)
        self.assertAlmostEqual(u["workers_cpu_ms"]["used"], 24_251_198.04, places=2)
        self.assertEqual(u["workers_cpu_ms"]["included"], 30_000_000)
        self.assertEqual(u["workers_cpu_ms"]["overage_usd"], 0)
        self.assertEqual(u["do_requests"]["used"], 1_500_000)
        self.assertAlmostEqual(u["do_requests"]["overage_usd"], 0.5 * 0.15, places=6)
        self.assertAlmostEqual(u["do_duration_gb_s"]["used"], 500_000, places=3)
        self.assertAlmostEqual(u["do_duration_gb_s"]["overage_usd"], 0.1 * 12.5, places=6)
        self.assertEqual(u["kv_reads"]["used"], 3)
        self.assertEqual(u["kv_writes"]["used"], 5)  # writes + deletes (+ lists): each 1 million included
        self.assertAlmostEqual(b["overage_usd"], sum(x["overage_usd"] for x in b["usage"]), places=9)
        self.assertEqual(b["by_script"]["yyc-web"]["requests"], 12_000_000)
        self.assertIn("subscriptions", b["source"])
        self.assertIn("developers.cloudflare.com/workers/platform/pricing", b["price_basis"])
        self.assertNotIn("projected_usd", b)

    def test_in_balances_and_spending(self):
        os.environ.update(CLOUDFLARE_YYC_MONITOR_TOKEN="cf-token", CLOUDFLARE_YYC_ACCOUNT_ID=ACCOUNT)
        with mock.patch.object(monitor, "get", cf_get), mock.patch.object(monitor, "cf_graphql", cf_gql):
            provs = {p["id"]: p for p in monitor.balances()["providers"]}
        self.assertEqual(provs["cloudflare"]["status"], "ok")
        with mock.patch.object(monitor, "panel", lambda name, fresh=False: {"providers": list(provs.values())}):
            sp = monitor.spending()
        fixed = {f["id"]: f for f in sp["fixed"]}
        self.assertEqual(fixed["cloudflare"]["monthly"], 5)
        self.assertEqual(fixed["cloudflare"]["currency"], "USD")


class Exa(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("EXA_API_KEY", None)

    def test_without_key(self):
        b = monitor.bal_exa()
        self.assertEqual(b["status"], "not_connected")
        self.assertIn("EXA_API_KEY", b["needs"])

    def test_exa_charged_cost_from_its_responses(self):
        os.environ["EXA_API_KEY"] = "exa"
        db = spend._db()
        db.execute("DELETE FROM tool_spend")
        now = datetime.now(timezone.utc)
        for tool, usd, prov, at in (("live_web_search", 0.012, 0.01, now), ("live_page_fetch", 0.001, 0.001, now),
                                    ("live_web_search", 0.012, None, now),  # Exa gave no cost: not Exa's figure
                                    ("live_tiktok_search", 0.03, 0.03, now),  # not Exa
                                    ("live_web_search", 0.012, 0.02, now - timedelta(days=40))):  # an earlier month
            db.execute("INSERT INTO tool_spend (at, email, tool, usd, provider_cost) VALUES (%s,'a@b',%s,%s,%s)",
                       (at, tool, usd, prov))
        b = monitor.bal_exa()
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["calls_month"], 3)
        self.assertAlmostEqual(b["exa_charged_month_usd"], 0.011, places=6)
        self.assertEqual(b["calls_today"], 3)
        self.assertAlmostEqual(b["exa_charged_today_usd"], 0.011, places=6)
        self.assertIn("Team Management", b["usage_api"])
        self.assertIn("support", b["usage_api"])
        self.assertIn("costDollars", b["source"])
        self.assertIn("10", b["plan"])


T0 = datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=24)


class Profile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = corpus.db()
        for t in ("ai_runs", "paid_calls", "provider_snapshots", "solver_spend"):
            db.execute(f"DELETE FROM {t}")
        for t, p, d in ((T0, "cloudflare", {"status": "ok", "subscriptions_monthly_usd": 5, "overage_usd": 0.0,
                                            "period_start": "2026-10-16T03:02:29Z",
                                            "usage": [{"id": "workers_requests", "name": "Workers requests", "used": 100}]}),
                        (T1, "cloudflare", {"status": "ok", "subscriptions_monthly_usd": 5, "overage_usd": 0.2,
                                            "period_start": "2026-10-16T03:02:29Z",
                                            "usage": [{"id": "workers_requests", "name": "Workers requests", "used": 900}]}),
                        (T0, "hetzner", {"status": "ok", "currency": "EUR", "list_monthly_net": 7.3}),
                        (T1, "hetzner", {"status": "ok", "currency": "EUR", "list_monthly_net": 7.3})):
            db.execute("INSERT INTO provider_snapshots (at, provider, status, data) VALUES (%s,%s,%s,%s)",
                       (t, p, d["status"], corpus.J(d)))
        cls.p = cost_profile.profile(T0, T1, live=False, eur_usd=1.1)

    def comp(self, cid):
        return next(c for c in self.p["components"] if c["id"] == cid)

    def test_cloudflare_from_snapshots(self):
        c = self.comp("cloudflare")
        self.assertAlmostEqual(c["provider_usd"], 5 * 24 / 730 + 0.2, places=6)
        self.assertEqual(c["cost_source"], "provider")
        subs = {s["name"]: s for s in c["subs"]}
        self.assertAlmostEqual(subs["subscriptions (Workers Paid USD 5.00/month)"]["provider_usd"], 5 * 24 / 730, places=6)
        self.assertAlmostEqual(subs["usage beyond the included amounts"]["provider_usd"], 0.2, places=6)
        self.assertEqual(c["usage_increase"]["workers_requests"], 800)

    def test_hetzner_from_snapshots(self):
        h = self.comp("hetzner")
        self.assertAlmostEqual(h["provider_usd"], 7.3 / 730 * 24 * 1.1, places=6)
        self.assertEqual(h["cost_source"], "provider")

    def test_cloudflare_not_listed_free(self):
        self.assertFalse(any("Cloudflare" in name for name, _ in self.p["free"]))


if __name__ == "__main__":
    unittest.main()
