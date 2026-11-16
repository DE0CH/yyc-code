#!/usr/bin/env python3
"""報告 and Deyao's monitor in the edge app (web-app/, TanStack Start on the Worker runtime), rendered by the vite dev
server against a copy of real data with fixtures written into it (EDGE_DB, default yyc_dev_ops: never yyc_dev_spa).

    EDGE_DB=yyc_dev_ops python3 tests/test_edge_ops.py        # starts `vite dev` on EDGE_PORT (default 5183)

Signing in: the test makes its own RSA key and signs Cloudflare Access tokens with it; the dev server verifies them
against that key (ACCESS_TEST_JWKS in web-app/.dev.vars, read only when import.meta.env.DEV: a production build has no
such branch, so there only Cloudflare's keys count). The line is removed again at the end.

1. /app/monitor and /app/monitor/data: a plain 404 (text/plain, X-Robots-Tag noindex) to no identity, to a token for
   another account and to a token signed by an unknown key; Deyao gets the page rendered on the server from the
   fixture snapshot (kv monitor:data): the stop line, the balance card (OpenRouter's credit: balance, bought, used), a pending panel, each panel's age, the sources
   filter; X-Robots-Tag noindex and <meta name=robots content=noindex>. WebKit iPhone 390 px and Chromium 1600 px: no
   console error (hydration), no sideways scroll; a source's error shown in place, a long one clamped to 2 lines behind
   "more" (only shown when text is hidden). Unknown paths under /app/monitor/: 404 for Deyao too.
2. /reports/ on the beta host: the stream's reports, 全部設定檔 (?all=1, replacing the URL), the file links' URLs; a
   file is served with its Content-Type. On the main host (flag off): 404 for the page, /api/reports and a file.
"""
import json, os, re, signal, subprocess, sys, time, unittest, urllib.error, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
DB = os.environ.get("EDGE_DB", "yyc_dev_ops")
assert DB != "yyc_dev_spa", "never write to the shared copy"
PORT = int(os.environ.get("EDGE_PORT", "5183"))
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = os.path.expanduser("~/artifacts/spa/ops")
DEYAO, OTHER = "chendeyao000@gmail.com", "colleague@yyc.hk"
AUD, ISS = "8b644dde1fadd76a241cace4428d57ebe8fccb6cdfe0af987e5f35bc7e31dd9b", "https://yyc-hk.cloudflareaccess.com"
BETA, MAIN = "yyc-beta.deyaochen.com", "newswatch.yyc-internal.com"
os.environ["DATABASE_URL"] = f"postgresql:///{DB}"
sys.path.insert(0, HERE)

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
import corpus  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ROGUE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(email, key=KEY):
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    now = int(time.time())
    return jwt.encode({"email": email, "aud": [AUD], "iss": ISS, "iat": now, "exp": now + 3600}, pem, algorithm="RS256",
                      headers={"kid": "test"})


def jwks():
    pub = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
    pub.update(kid="test", alg="RS256", use="sig")
    return json.dumps({"keys": [pub]})


def get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


NOW = time.time()
SNAP = {
    "stops": {"stored_at": NOW - 42, "value": {"providers": [{"id": "openrouter", "name": "OpenRouter", "tag": "out of credit",
              "error_text": "Insufficient credits", "since": "2026-10-14T21:07:00+08:00",
              "message": "Insufficient credits. Add more using https://openrouter.ai/settings/credits", "notified": True}],
              "computed_at": "2026-10-15T10:00:00+08:00"}},
    "balances": {"stored_at": NOW - 100, "value": {"providers": [
        {"id": "openrouter", "name": "OpenRouter", "status": "ok", "account": "key yyc-stream", "source": "provider API (GET /api/v1/key)",
         "usage_month_usd": 40.27, "usage_today_usd": 0.97, "rate_usd_per_hour": 0.09, "rate_basis": "our log",
         "credits_bought_usd": 390, "credits_used_usd": 370.714545278, "credit_balance_usd": 19.285454722},
        {"id": "hetzner", "name": "Hetzner", "status": "not_connected", "needs": "HETZNER_YYC_READ in Secret monitor-keys"}],
        "computed_at": "2026-10-15T10:00:00+08:00"}},
    "spending": {"stored_at": NOW - 60, "value": {"month": "2026-10", "note": "USD; days are HKT", "days": ["2026-10-14", "2026-10-15"],
        "series": [{"id": "openrouter", "name": "OpenRouter", "by_day": {"2026-10-14": 1.2345, "2026-10-15": 0.5}, "mtd_usd": 1.7345,
                    "source": "our log", "projected_usd": 3.1, "projection": "this month's pace"}],
        "ai_purposes": [{"purpose": "scoring", "mtd_usd": 1.5}], "fixed": [], "total_mtd_usd": 1.7345, "total_projected_usd": 3.1,
        "computed_at": "2026-10-15T10:00:00+08:00"}},
    "sources": {"stored_at": NOW - 30, "value": {"counts": {"total": 4, "failing": 2, "off": 0, "quiet": 0, "proxy": 1, "browser": 1},
        "last_poll": "2026-10-15T09:58:00+08:00", "rule": "quiet = under a quarter of its usual day",
        "rows": [{"id": "rthk", "name": "RTHK " + "Verylongoutletnamewithoutspaces" * 4, "kind": "feed", "state": "failing", "fails": 3,
                  "error": "HTTP 503 " + "x" * 300, "posts_24h": 1234, "usual_per_day": 40, "items": 0, "last_ok": "2026-10-15T08:00:00+08:00",
                  "fail_since": "2026-10-15T09:00:00+08:00"},
                 {"id": "tmhk", "name": "TMHK", "kind": "site", "state": "failing", "fails": 2,
                  "error": "URLError(TimeoutError('_ssl.c:983: The handshake operation timed out'))", "posts_24h": 0,
                  "usual_per_day": 0, "items": 0, "last_ok": "2026-10-15T08:30:00+08:00", "fail_since": "2026-10-15T09:10:00+08:00"},
                 {"id": "hkej", "name": "信報", "kind": "proxy", "state": "ok", "posts_24h": 12, "usual_per_day": 10, "items": 20,
                  "last_ok": "2026-10-15T09:58:00+08:00"},
                 {"id": "mingpao", "name": "明報月刊", "kind": "browser", "state": "ok", "posts_24h": 2, "usual_per_day": 2, "items": 5,
                  "last_ok": "2026-10-15T09:30:00+08:00"}],
        "computed_at": "2026-10-15T10:00:00+08:00"}},
    "jobs": {"stored_at": NOW - 20, "value": {"cronjobs": [{"name": "browser", "schedule": "14,44 * * * *", "last_finished": "failed",
        "recent": [{"state": "failed", "start": "2026-10-15T01:44:00Z"}], "last_success": "2026-10-15T01:14:00Z",
        "last_schedule": "2026-10-15T01:44:00Z"}], "deployments": [{"name": "web", "ready": 2, "replicas": 2, "image": "ghcr.io/de0ch/yyc-news-stream:abc"}],
        "computed_at": "2026-10-15T10:00:00+08:00"}},
    "pipeline": {"stored_at": NOW - 25, "value": {"scoring": {"awaiting": 12345}, "fulltext": {"waiting_recent_2d": 3, "waiting_all": 4567},
        "computed_at": "2026-10-15T10:00:00+08:00"}},
}  # telegram: no snapshot yet (pending)


def hydrated(page, sel):
    """React has taken over the server-rendered page (dev mode loads the modules after the page: seconds)"""
    page.wait_for_function(f"Object.keys(document.querySelector({json.dumps(sel)}) || {{}}).some(k => k.startsWith('__react'))",
                           timeout=120000)


def no_sideways(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


class EdgeOps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        c = corpus.db()
        cls.old_snap = corpus.kv_get("monitor:data")
        corpus.kv_set("monitor:data", SNAP)
        c.execute("DELETE FROM report_files WHERE profile LIKE 'ZZTEST%%'")
        pid = c.execute("SELECT v->'profiles'->0->>'id' FROM kv WHERE k = 'edge:config'").fetchone()[0]
        cls.pid = pid
        rows = [("2099-01-02", pid, n, t, b) for n, t, b in (
            ("format1.html", "text/html; charset=utf-8", "<p>格式一</p>".encode()), ("report.pdf", "application/pdf", b"%PDF-1.4 t"),
            ("summary.json", "application/json", b"{}"))]
        rows += [("2099-01-02", "ZZTEST_OTHER", "summary.json", "application/json", b"{}")]
        c.execute("DELETE FROM report_files WHERE day = '2099-01-02'")
        c.executemany("INSERT INTO report_files (day, profile, name, ctype, body) VALUES (%s, %s, %s, %s, %s)", rows)
        # the dev server, verifying Access tokens against this test's key
        cls.vars = os.path.join(APP, ".dev.vars")
        if not os.path.exists(cls.vars):
            with open(cls.vars, "w") as f, open(cls.vars + ".example") as ex:
                f.write(ex.read())
        with open(cls.vars) as f:
            cls.vars_before = f.read()
        with open(cls.vars, "w") as f:
            f.write("\n".join(l for l in cls.vars_before.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                    "\nACCESS_TEST_JWKS=" + jwks() + "\n")
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=f"postgres://claude:dev@127.0.0.1:5432/{DB}")
        cls.log = open(os.path.join(SHOTS, "dev.log"), "w")  # noqa: SIM115 (closed in tearDownClass)
        cls.dev = subprocess.Popen(["npx", "vite", "dev", "--port", str(PORT), "--strictPort"], cwd=APP, env=env,
                                   stdout=cls.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + 600
        while True:
            try:
                if get("/app/me")[0]:
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
        c = corpus.db()
        c.execute("DELETE FROM report_files WHERE day = '2099-01-02'")
        corpus.kv_set("monitor:data", cls.old_snap)

    # ---- the monitor ----

    def test_monitor_404_for_everyone_else(self):
        for who in (None, token(OTHER), token(DEYAO, ROGUE), token(DEYAO.upper() + ".evil.com")):
            for p in ("/app/monitor", "/app/monitor/", "/app/monitor/data", "/app/monitor/x"):
                code, h, body = get(p, {"Cf-Access-Jwt-Assertion": who} if who else {})
                self.assertEqual((code, body), (404, b"not found"), (p, who and jwt.decode(who, options={"verify_signature": False})["email"]))
                self.assertEqual(h.get("Content-Type"), "text/plain")
                self.assertEqual(h.get("X-Robots-Tag"), "noindex")
        self.assertEqual(get("/app/monitor/x", {"Cf-Access-Jwt-Assertion": token(DEYAO)})[0], 404)

    def test_monitor_data_for_deyao(self):
        code, h, body = get("/app/monitor/data", {"Cf-Access-Jwt-Assertion": token(DEYAO)})
        self.assertEqual(code, 200)
        self.assertEqual(h.get("X-Robots-Tag"), "noindex")
        d = json.loads(body)
        self.assertEqual(d["stops"]["providers"][0]["tag"], "out of credit")
        self.assertGreaterEqual(d["stops"]["age_seconds"], 42)
        self.assertTrue(d["telegram"]["pending"])
        self.assertRegex(d["at"], r"\+08:00$")

    def test_monitor_page(self):
        code, h, body = get("/app/monitor", {"Cf-Access-Jwt-Assertion": token(DEYAO)})
        self.assertEqual(code, 200)
        self.assertEqual(h.get("X-Robots-Tag"), "noindex")
        html = body.decode()
        self.assertIn('name="robots" content="noindex"', html)
        self.assertIn("YYC Monitor", html)  # rendered on the server, from the snapshot
        self.assertIn("OpenRouter: out of credit — requests failing with 'Insufficient credits' since Wed 14 Oct 21:07 HKT", html)
        # the OpenRouter account's credit (management key yyc-monitor): balance, bought, used; no projection
        self.assertIn("USD 19.29", html)
        self.assertRegex(html, r"Bought</dt><dd>USD 390\.00")
        self.assertRegex(html, r"Used</dt><dd>USD 370\.71")
        with sync_playwright() as pw:
            for name, launch, ctx_args in (
                    ("webkit-390", pw.webkit, dict(pw.devices["iPhone 13"], viewport={"width": 390, "height": 844})),
                    ("chromium-1600", pw.chromium, dict(viewport={"width": 1600, "height": 1000}))):
                b = launch.launch()
                ctx = b.new_context(**ctx_args, timezone_id="Asia/Hong_Kong",
                                    extra_http_headers={"Cf-Access-Jwt-Assertion": token(DEYAO)})
                ctx.route(re.compile(r"^https://fonts\."), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
                p = ctx.new_page()
                errors = []
                p.on("console", lambda m: m.type == "error" and errors.append(m.text))
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.goto(BASE + "/app/monitor", wait_until="networkidle", timeout=180000)
                p.wait_for_selector("#bal .card", timeout=60000)
                p.wait_for_function("document.querySelector('#upd').textContent.includes('updated')", timeout=60000)
                hydrated(p, "[data-f=all]")
                ov = p.inner_text("#overview")
                self.assertIn("OpenRouter: out of credit — requests failing with 'Insufficient credits' since Wed 14 Oct 21:07 HKT", ov)
                self.assertRegex(ov, r"stops as of Thu 15 Oct 10:00 HKT, \d+ (s|min) old")
                self.assertIn("out of credit", p.inner_text("#bal"))
                self.assertIn("Needs: HETZNER_YYC_READ", p.inner_text("#bal"))
                self.assertIn("first snapshot", p.inner_text("#telegram"))
                self.assertIn("12,345", p.inner_text("#tiles"))
                self.assertEqual(p.locator("#src .item").count(), 2)  # Problems: the failing sources only
                # errors are shown in place, no tap (Deyao, 2026-10-22): a short one whole and with no toggle; a long
                # one clamped to 2 lines with "more", which expands it and stays expanded across a redraw
                short, long_ = p.locator("[data-id='err:tmhk']"), p.locator("[data-id='err:rthk']")
                self.assertIn("Error now: URLError(TimeoutError('_ssl.c:983: The handshake operation timed out'))", short.inner_text())
                self.assertFalse(short.locator("button.more").is_visible(), name)
                pre = long_.locator("pre")
                lines = lambda: pre.evaluate("e => Math.round(e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight))")
                self.assertEqual(lines(), 2, name)
                self.assertTrue(long_.locator("button.more").is_visible(), name)
                self.assertEqual(long_.locator("button.more").inner_text(), "more")
                long_.locator("button.more").click()
                self.assertGreater(lines(), 2, name)
                self.assertEqual(long_.locator("button.more").inner_text(), "less")
                p.fill("#q", "rthk")
                p.fill("#q", "")
                self.assertGreater(lines(), 2, name)  # still expanded after the list was redrawn
                long_.locator("button.more").click()
                self.assertEqual(lines(), 2, name)
                p.click("[data-f=all]")
                self.assertEqual(p.locator("#src .item").count(), 4)
                p.fill("#q", "信報")
                self.assertEqual(p.locator("#src .item").count(), 1)
                p.fill("#q", "")
                self.assertEqual(p.evaluate("localStorage.getItem('yyc-monitor-filter')"), "all")
                self.assertEqual(p.locator("#sp svg rect.hit").count(), 2)
                self.assertTrue(no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"monitor-{name}.png"), full_page=True)
                self.maxDiff = None; self.assertEqual(errors, [], name)
                b.close()

    # ---- 報告 ----

    def test_reports_flag_off_on_main_site(self):
        for p in ("/reports/", "/reports", "/api/reports", f"/reports/2099-01-02/{self.pid}/report.pdf"):
            code, h, body = get(p, {"X-Forwarded-Host": MAIN})
            self.assertEqual(code, 404, p)
            self.assertEqual(json.loads(body), {"error": "not found"})

    def test_report_files_served(self):
        hb = {"X-Forwarded-Host": BETA}
        code, h, body = get(f"/reports/2099-01-02/{self.pid}/report.pdf", hb)
        self.assertEqual((code, h.get("Content-Type"), body), (200, "application/pdf", b"%PDF-1.4 t"))
        code, h, body = get(f"/reports/2099-01-02/{self.pid}/format1.html", hb)
        self.assertEqual((code, h.get("Content-Type"), body.decode()), (200, "text/html; charset=utf-8", "<p>格式一</p>"))
        for p in (f"/reports/2099-01-02/{self.pid}/none.pdf", f"/reports/2099-01-02/{self.pid}/x.exe"):
            self.assertEqual(get(p, hb)[0], 404, p)
        code, h, body = get("/api/reports", hb)
        self.assertEqual(json.loads(body)["reports"][0], {"day": "2099-01-02", "profile": self.pid, "files": [
            f"/reports/2099-01-02/{self.pid}/{n}" for n in ("format1.html", "report.pdf", "summary.json")]})

    def test_reports_page(self):
        with sync_playwright() as pw:
            for name, launch, ctx_args in (
                    ("webkit-390", pw.webkit, dict(pw.devices["iPhone 13"], viewport={"width": 390, "height": 844})),
                    ("chromium-1600", pw.chromium, dict(viewport={"width": 1600, "height": 1000}))):
                b = launch.launch()
                ctx = b.new_context(**ctx_args, extra_http_headers={"X-Forwarded-Host": BETA})
                ctx.route(re.compile(r"^https://fonts\."), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
                p = ctx.new_page()
                errors = []
                p.on("console", lambda m: m.type == "error" and errors.append(m.text))
                p.on("pageerror", lambda e: errors.append(str(e)))
                code = p.goto(f"{BASE}/reports/?p={self.pid}", wait_until="networkidle", timeout=180000).status
                self.assertEqual(code, 200)
                p.wait_for_selector("#list .day", timeout=60000)
                hydrated(p, ".seg button")
                self.assertEqual(p.inner_text("#list .day >> nth=0"), "2099-01-02")
                card = p.locator("#list .card").first
                self.assertEqual(card.locator(".sub").inner_text(), self.pid)
                self.assertEqual([a.inner_text() for a in card.locator(".links a").all()], ["社交概況（格式一）", "PDF 報告", "數據 JSON"])
                self.assertEqual(card.locator(".links a").first.get_attribute("href"), f"/reports/2099-01-02/{self.pid}/format1.html")
                self.assertEqual(card.locator(".links a").first.get_attribute("target"), "_blank")
                self.assertNotIn("ZZTEST_OTHER", p.inner_text("#list"))
                self.assertEqual(p.get_attribute("[aria-pressed=true]", "aria-pressed"), "true")
                hist = p.evaluate("history.length")
                p.click("text=全部設定檔")
                p.wait_for_function("location.search.includes('all=1')", timeout=30000)
                self.assertIn("ZZTEST_OTHER", p.inner_text("#list"))
                self.assertIn("當日沒有相關帖子。", p.inner_text("#list"))
                self.assertEqual(p.evaluate("history.length"), hist)  # replaced, not a new entry
                self.assertIn(f"p={self.pid}", p.evaluate("location.search"))
                self.assertTrue(no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"reports-{name}.png"), full_page=False)
                self.maxDiff = None; self.assertEqual(errors, [], name)
                b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
