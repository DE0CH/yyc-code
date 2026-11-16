#!/usr/bin/env python3
"""Deyao's monitor in the edge app shows what is true now (Deyao, 2026-10-16; tests/test_monitor_now.py has the data
side). Rendered by `vite dev` against EDGE_NOW_DB (default yyc_dev_monnow: a throwaway database; the monitor needs only
kv monitor:data), signed in with this test's own Access key (as tests/test_edge_ops.py).

    EDGE_NOW_DB=yyc_dev_monnow python3 tests/test_edge_monitor_now.py

1. WhatsApp: /app/monitor/data carries the whatsapp panel (not "pending"), and the page has a WhatsApp section (in the
   jump bar): the linked device's state, its number, the primary phone acp-hln9ilimxqzruvccu, the last disconnect
   (muted while linked), each group's state (up to date / N waiting), the last item sent and when.
2. Spending: the last 6 h and 24 h, per purpose the model in use now with its rate since it took over, the replaced
   model (Argon) shown as replaced; no "projected" anywhere on the page.
3. Old errors never look live: Telegram's send error from 27.7 h ago (sends worked since) is not red; a source that is
   answering shows its earlier error muted; history scoring's "OpenRouter limit" recorded 5 h ago, with OpenRouter
   answering again, is not a red badge and says how old it is. A current error (a failing source, a WhatsApp send
   error with no send after it) stays red.
4. Per job (Deyao, 2026-10-16): IPRoyal traffic per job, each job's sites behind a tap, in MB/GB only (no USD in
   that panel), the unit named (decimal: 1 GB = 1,000,000,000 bytes, as IPRoyal counts it) and IPRoyal's own figure
   beside our count; TikHub and AnyAPI USD per job, each job's endpoints with calls and failed calls (HTTP statuses),
   and TikHub's own figure beside ours. Each for the last 6 h, 24 h and this month, biggest first.
5. WebKit iPhone 390 px and Chromium 1600 px: no console error, no sideways scroll; screenshots in SHOTS.
"""
import json, os, re, signal, subprocess, sys, time, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
DB = os.environ.get("EDGE_NOW_DB", "yyc_dev_monnow")


def only_data_update(text):
    """a hydration report whose only differing line is <html data-update="current">"""
    tree = text.split("hydration-mismatch", 1)[-1]
    diff = [re.sub(r"\s+", " ", l.strip()) for l in tree.splitlines() if re.match(r"^\s*[+-]\s", l)]
    return "hydration-mismatch" in text and diff == ['- data-update="current"']

assert DB != "yyc_dev_spa", "never write to the shared copy"
os.environ["EDGE_DB"] = DB
os.environ["EDGE_PORT"] = os.environ.get("EDGE_NOW_PORT", "5187")
sys.path.insert(0, os.path.join(HERE, "tests"))
subprocess.run(["createdb", DB], stderr=subprocess.DEVNULL)
import test_edge_ops as ops  # noqa: E402  (token, get, hydrated, no_sideways; DATABASE_URL = DB)
from playwright.sync_api import sync_playwright  # noqa: E402
import corpus  # noqa: E402

SHOTS = os.environ.get("SHOTS") or os.path.expanduser("~/artifacts/monitor-v2/test")
NOW = time.time()
HKT = timezone(timedelta(hours=8))


def ago(**kw):
    return (datetime.now(HKT) - timedelta(**kw)).isoformat(timespec="seconds")


def bw(key, label, f):
    """one window of the per-job breakdown, scaled by f"""
    return {"key": key, "label": label, "start": "2026-09-30T16:00:00+00:00",
            "proxy": {"bytes": int(f * 1_210_000_000), "provider_bytes": int(f * 1_300_000_000), "provider_coverage": 1.0,
                      "provider_basis": "IPRoyal /residential-subusers: yycstream's traffic_used", "basis": "our count",
                      "bytes_per_gb": 1000000000,
                      "counted_from": "2026-10-15T23" if key == "month" else None,
                      "by_job": [{"job": "transcribe", "bytes": int(f * 1_130_000_000),
                                  "sites": [{"site": "youtube", "bytes": int(f * 1_130_000_000)}]},
                                 {"job": "comments", "bytes": int(f * 80_000_000),
                                  "sites": [{"site": "facebook", "bytes": int(f * 60_000_000)},
                                            {"site": "instagram", "bytes": int(f * 20_000_000)}]}]},
            "tikhub": {"usd": 0.14 * f, "calls": int(120 * f), "failed_calls": 4, "provider_usd": 0.15 * f,
                       "provider_coverage": 1.0, "provider_basis": "TikHub user/get_user_daily_usage", "basis": "our log",
                       "by_job": [{"job": "comments", "usd": 0.1 * f, "calls": int(100 * f), "failed_calls": 4,
                                   "endpoints": [{"endpoint": "/api/v1/instagram/v2/fetch_post_comments", "platform": "instagram",
                                                  "calls": int(100 * f), "failed_calls": 4, "failed_statuses": {"400": 3, "402": 1},
                                                  "unit_usd": 0.001, "usd": 0.1 * f}]},
                                  {"job": "watch", "usd": 0.04 * f, "calls": int(20 * f), "failed_calls": 0,
                                   "endpoints": [{"endpoint": "/api/v1/twitter/web/fetch_user_post_tweet", "platform": "twitter",
                                                  "calls": int(20 * f), "failed_calls": 0, "failed_statuses": {},
                                                  "unit_usd": 0.002, "usd": 0.04 * f}]}]},
            "anyapi": {"usd": 0.06, "calls": 2, "failed_calls": 1, "provider_usd": None, "provider_coverage": 0.0,
                       "provider_basis": "AnyAPI GET /v1/balance", "basis": "our log",
                       "by_job": [{"job": "tikhub", "usd": 0.06, "calls": 2, "failed_calls": 1,
                                   "endpoints": [{"endpoint": "facebook.search_posts", "platform": "facebook", "calls": 2,
                                                  "failed_calls": 1, "failed_statuses": {"402": 1}, "unit_usd": None,
                                                  "usd": 0.06}]}]}}


AT = ago(seconds=0)
SNAP = {
    "stops": {"stored_at": NOW - 30, "value": {"providers": [], "computed_at": AT}},
    "balances": {"stored_at": NOW - 60, "value": {"providers": [], "computed_at": AT}},
    "telegram": {"stored_at": NOW - 20, "value": {"computed_at": AT, "note": "backlog = items filed after the last one sent",
        "streams": [{"stream": "CEHK", "name": "特首", "channel": True, "items": 900, "last_sent_id": 2150, "backlog": 0,
                     "last_sent_item_seen": ago(minutes=9), "last_sent_at": ago(minutes=8)}],
        "last_error": {"at": ago(hours=27.7), "stream": None, "current": False, "resolved_at": ago(minutes=8),
                       "error": "OperationalError('consuming input failed: SSL error: unexpected eof while reading')"}}},
    "whatsapp": {"stored_at": NOW - 20, "value": {"computed_at": AT, "note": "linked = connected",
        "phone": {"id": "acp-hln9ilimxqzruvccu", "group": "ag-hln9ilimxqzrt8xos", "region": "cn-shanghai"},
        "status": {"state": "linked", "me": "447448090466", "name": "YYC 新聞監察", "since": ago(hours=3),
                   "last_close": {"code": 428, "reason": "Connection Closed", "at": ago(hours=3)}},
        "streams": [{"stream": "CEHK", "name": "特首", "last_sent_id": 2150, "newest_id": 2150, "backlog": 0,
                     "last_sent_item_seen": ago(minutes=9), "last_sent_at": ago(minutes=8)},
                    {"stream": "HKIC", "name": "創新科技", "last_sent_id": 2083, "newest_id": 2086, "backlog": 3,
                     "last_sent_item_seen": ago(minutes=40), "last_sent_at": ago(minutes=39)}],
        "last_error": {"at": ago(minutes=30), "stream": "HKIC", "current": True, "error": "item 2084 not sent: socket closed"}}},
    "sources": {"stored_at": NOW - 30, "value": {"computed_at": AT, "counts": {"total": 2, "failing": 1, "off": 0, "quiet": 0, "proxy": 0, "browser": 0},
        "last_poll": ago(minutes=2), "rule": "quiet = …",
        "rows": [{"id": "rthk", "name": "RTHK", "kind": "direct", "state": "failing", "fails": 2, "error": "HTTP 503 now",
                  "error_current": True, "posts_24h": 1, "usual_per_day": 40, "items": 0, "last_ok": ago(hours=1)},
                 {"id": "hkej", "name": "信報", "kind": "proxy", "state": "ok", "posts_24h": 12, "usual_per_day": 10, "items": 20,
                  "last_ok": ago(minutes=3), "last_error": "HTTP 429 long ago", "last_error_at": ago(hours=20),
                  "error_current": False}]}},
    "jobs": {"stored_at": NOW - 20, "value": {"computed_at": AT, "cronjobs": [], "deployments": []}},
    "pipeline": {"stored_at": NOW - 25, "value": {"computed_at": AT, "scoring": {"awaiting": 3}, "fulltext": {},
        "history_scoring": {"on": True, "state": "limit", "at": ago(hours=5), "left": 100, "posts": 50, "spent_usd": 1.2,
                            "state_current": False}}},
    "spending": {"stored_at": NOW - 60, "value": {"computed_at": AT, "month": "2026-10", "note": "USD",
        "days": ["2026-10-15", "2026-10-16"],
        "series": [{"id": "openrouter", "name": "OpenRouter (AI)", "by_day": {"2026-10-15": 85.0, "2026-10-16": 16.0},
                    "mtd_usd": 101.0, "source": "our log"}],
        "fixed": [], "total_mtd_usd": 101.0,
        "windows": [{"hours": 6, "total_usd": 2.4, "components": [
                        {"id": "openrouter", "name": "OpenRouter (AI models, key yyc-stream)", "cost_usd": 2.4, "cost_source": "our log",
                         "subs": [{"name": "transcribe × qwen/qwen3-asr-1.7b", "cost_usd": 1.2, "calls": 900}]}]},
                    {"hours": 24, "total_usd": 9.6, "components": [
                        {"id": "openrouter", "name": "OpenRouter (AI models, key yyc-stream)", "cost_usd": 9.6, "cost_source": "our log",
                         "subs": [{"name": "transcribe × qwen/qwen3-asr-1.7b", "cost_usd": 4.8, "calls": 3600}]}]}],
        "proxy_unit": {"bytes_per_gb": 1000000000, "bytes_per_mb": 1000000,
                       "note": "decimal units, as IPRoyal counts them: 1 GB = 1,000,000,000 bytes, 1 MB = 1,000,000 bytes"},
        "breakdown": [bw(k, l, f) for k, l, f in (("6h", "Last 6 h", 0.25), ("24h", "Last 24 h", 1.0), ("month", "This month (HKT)", 4.0))],
        "models": [{"purpose": "transcribe", "model": "qwen/qwen3-asr-1.7b", "since": ago(hours=23), "last_call": ago(minutes=1),
                    "previous_model": "google/gemini-4-argon", "replaced_at": ago(hours=23, minutes=1),
                    "rate_usd_per_hour": 0.6271, "rate_from": ago(hours=23), "rate_hours": 23.0, "usd_since": 14.42},
                   {"purpose": "scoring", "model": "anthropic/claude-haiku-5.5", "since": ago(days=2), "last_call": ago(minutes=2),
                    "previous_model": None, "replaced_at": None, "rate_usd_per_hour": 0.0444, "rate_from": ago(hours=24),
                    "rate_hours": 24.0, "usd_since": 5.94}],
        "ai_models": [{"purpose": "transcribe", "model": "google/gemini-4-argon", "current": False, "usd": 51.91, "calls": 8155,
                       "last_24h_usd": 0.0, "last_call": ago(hours=23, minutes=1)},
                      {"purpose": "transcribe", "model": "qwen/qwen3-asr-1.7b", "current": True, "usd": 14.42, "calls": 78652,
                       "last_24h_usd": 4.8, "last_call": ago(minutes=1)}]}},
}


class MonitorNow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        corpus.db()  # the schema
        corpus.kv_set("monitor:data", SNAP)
        cls.vars = os.path.join(APP, ".dev.vars")
        if not os.path.exists(cls.vars):
            with open(cls.vars, "w") as f, open(cls.vars + ".example") as ex:
                f.write(ex.read())
        with open(cls.vars) as f:
            cls.vars_before = f.read()
        with open(cls.vars, "w") as f:
            f.write("\n".join(l for l in cls.vars_before.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                    "\nACCESS_TEST_JWKS=" + ops.jwks() + "\n")
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=f"postgres://claude:dev@127.0.0.1:5432/{DB}")
        cls.log = open(os.path.join(SHOTS, "dev.log"), "w")  # noqa: SIM115
        cls.dev = subprocess.Popen(["npx", "vite", "dev", "--port", str(ops.PORT), "--strictPort"], cwd=APP, env=env,
                                   stdout=cls.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + 600
        while True:
            try:
                if ops.get("/app/monitor/data", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})[0] == 200:
                    break
            except Exception:
                pass
            if cls.dev.poll() is not None or time.time() > deadline:
                cls.tearDownClass()
                raise RuntimeError("the dev server did not start: " + os.path.join(SHOTS, "dev.log"))
            time.sleep(2)

    @classmethod
    def tearDownClass(cls):
        try:
            os.killpg(cls.dev.pid, signal.SIGTERM)
            cls.dev.wait(timeout=30)
        except Exception:
            pass
        cls.log.close()
        with open(cls.vars, "w") as f:
            f.write(cls.vars_before)

    def test_data_has_whatsapp(self):
        code, h, body = ops.get("/app/monitor/data", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})
        d = json.loads(body)
        self.assertNotIn("pending", d["whatsapp"])
        self.assertEqual(d["whatsapp"]["phone"]["id"], "acp-hln9ilimxqzruvccu")

    def test_page(self):
        with sync_playwright() as pw:
            for name, launch, ctx_args in (
                    ("webkit-390", pw.webkit, dict(pw.devices["iPhone 13"], viewport={"width": 390, "height": 844})),
                    ("chromium-1600", pw.chromium, dict(viewport={"width": 1600, "height": 1000}))):
                b = launch.launch()
                ctx = b.new_context(**ctx_args, timezone_id="Asia/Hong_Kong",
                                    extra_http_headers={"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})
                ctx.route(re.compile(r"^https://fonts\."), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
                p = ctx.new_page()
                errors = []
                # (React's dev build also reports <html data-update="current">, which lib/update.ts sets before
                # hydration on every page; production React doesn't check attributes: test_proof_page.only_data_update)
                p.on("console", lambda m: m.type == "error" and not only_data_update(m.text) and errors.append(m.text))
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.goto(ops.BASE + "/app/monitor", wait_until="networkidle", timeout=180000)
                p.wait_for_selector("#wa .item", timeout=60000)
                ops.hydrated(p, "[data-f=all]")
                # 1. WhatsApp
                self.assertEqual(p.locator(".jump a[href='#whatsapp']").count(), 1)
                wa = p.inner_text("#whatsapp")
                for s in ("linked", "+447448090466", "acp-hln9ilimxqzruvccu", "last disconnect", "up to date", "3 waiting",
                          "item 2150"):
                    self.assertIn(s, wa, (name, s))
                self.assertRegex(wa, r"item 2150, sent \d+ min ago")
                self.assertEqual(p.locator("#whatsapp .item:has-text('Linked device') .err").count(), 0)
                self.assertIn("socket closed", p.inner_text("#whatsapp .err"))  # current: no send after it
                # 2. spending now
                sp = p.inner_text("#spending")
                for s in ("Last 6 h", "Last 24 h", "USD 9.60", "qwen/qwen3-asr-1.7b", "USD 0.6271/h", "google/gemini-4-argon",
                          "replaced"):
                    self.assertIn(s, sp, (name, s))
                self.assertNotIn("rojected", p.inner_text("main"))
                # 4. per job
                px = p.locator("#proxy-traffic")
                t = px.inner_text()
                for s in ("IPRoyal traffic", "yycstream", "transcribe", "1.13 GB", "4.52 GB", "282.5 MB", "comments",
                          "1 GB = 1,000,000,000 bytes", "IPRoyal's own figure", "5.20 GB", "Our count",
                          "begins Thu 15 Oct 23:00 HKT"):
                    self.assertIn(s, t, (name, s))
                self.assertNotIn("USD", t, name)
                self.assertFalse(px.locator("details:has-text('transcribe') .sites").first.is_visible(), name)
                px.locator("details:has-text('comments') summary").first.click()
                self.assertIn("instagram", px.locator("details:has-text('comments')").first.inner_text(), name)
                # biggest first
                self.assertLess(t.index("transcribe"), t.index("comments"), name)
                th = p.locator("#tikhub-calls").inner_text()
                for s in ("TikHub", "USD", "comments", "watch", "0.1000", "4 failed", "Failed calls", "TikHub's own figure", "0.1500"):
                    self.assertIn(s, th, (name, s))
                p.locator("#tikhub-calls details:has-text('comments') summary").first.click()
                tc = p.locator("#tikhub-calls details:has-text('comments')").first.inner_text()
                for s in ("instagram", "fetch_post_comments", "402", "400"):
                    self.assertIn(s, tc, (name, s))
                p.locator("#anyapi-calls details summary").first.click()
                self.assertIn("facebook.search_posts", p.inner_text("#anyapi-calls"), name)
                for sec in ("proxy-traffic", "tikhub-calls"):
                    p.locator("#" + sec).screenshot(path=os.path.join(SHOTS, f"{sec}-{name}.png"))
                # 3. old errors are not red
                self.assertEqual(p.locator("#telegram .err").count(), 0, name)
                self.assertIn("SSL error", p.inner_text("#telegram"))
                self.assertIn("since then sends worked", p.inner_text("#telegram"))
                p.click("[data-f=all]")
                hk = p.locator("#src .item:has-text('信報')")
                self.assertEqual(hk.locator(".err, pre:not(.past)").count(), 0)
                self.assertIn("Earlier error", hk.inner_text())
                self.assertEqual(p.locator("#src .item:has-text('RTHK') pre:not(.past)").count(), 1)  # current stays red
                hs = p.locator("#pipe .card:has-text('History scoring')")
                self.assertEqual(hs.locator(".pill.bad").count(), 0, name)
                self.assertRegex(hs.inner_text(), r"limit, 5\.\d h ago")
                self.assertTrue(ops.no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"monitor-{name}.png"), full_page=True)
                self.assertEqual(errors, [], name)
                b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
