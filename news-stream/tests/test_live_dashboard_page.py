#!/usr/bin/env python3
"""The live dashboard /app/live (Deyao, 2026-10-21: watch the pipeline live on two displays), rendered by `vite dev`
against LIVE_DB (default yyc_dev_live: emptied and filled with this test's fixtures), signed in with this test's own
Access key (tests/test_edge_ops.py's token and dev-server keys).

    LIVE_DB=yyc_dev_live python3 tests/test_live_dashboard_page.py

1. Deyao only: /app/live, /app/live/data and an unknown path under /app/live/ answer a plain 404 to no identity and to
   a colleague's approved account; Deyao gets the page (noindex) and the data.
2. Fixed layout (Deyao: "text size and layout should be fixed"): ?half=left at 1920 x 1200 and ?half=right at
   1920 x 1080 draw exactly lib/liveLayout.ts LAYOUTS in px (the same boxes in a window of another size: nothing is
   fitted), every panel wholly on its own display, so on the 3840-wide canvas no panel crosses the seam x = 1920
   (Deyao: "a panel cannot span two displays"); Stage 1 and Stage 2 side by side on the left, Filed, Alerts and
   Problems above YYC AI and Systems on the right; every card has its panel's fixed height (CARD_H) and each panel
   shows cardsIn() cards; type at fixed px sizes; live.css has no vw/vh/clamp(). One window at 1440 x 830: every panel
   on screen. A phone (390 x 844): one column (phoneLayout), no sideways scroll.
3. The cards: every panel shows its fixture as one self-contained card (Full text's route, length and five lines, a
   failure in red with its reason, Stage 1's every topic with score and reason, Stage 2's "<stage-1 model> → <stage-2 model>" with both
   reasons, each panel's header naming its stage's model (edge config `models`), Comments' three most liked, YYC AI's
   activity cards (model calls, a failed sandbox start in red, a start refused at the account's own limit in grey) beside its state from kv live:sys (status, sandboxes,
   today's spend and budget), Systems' providers with their figures and source, pods, hosts, nodes, Postgres's load,
   space and backups, the WiseSearch relay, the object storage buckets, …), and
   each header its counts in the last 10 minutes and hour.
4. One card at a time, paced to the arrival rate (Deyao: "one new item at a time", "roughly similar to the rate it's
   coming in"; 2026-10-27: "equal to the rate … the download eta algorithm"): three events written at once: the oldest
   goes on top at once, the other two wait their turn (each due within MAX_WAIT); no animation or transition anywhere.
5. Stale: the poller's last poll 20 minutes ago greys the screen and says "No data since HH:MM" (HKT); the read
   failing for STALE_MS does the same, and the pacing functions (lib/liveLayout enqueue/step/behind) in Node: cards go on
   screen as fast as they arrive (clumps of 10 a minute: one every 6 s; 20 a minute: every 3 s), oldest first, none
   skipped, none waiting over MAX_WAIT; "+N behind" past what MIN_GAP can show.
6. F toggles full screen; the page takes a screen wake lock; no console error (hydration).
Screenshots in SHOTS. With LIVE_PG (a postgres:// URL, e.g. the production database, read-only role or not: the
page's connection is read-only) the same server renders live data and only the screenshots are taken (class Shots).
"""
import json, os, signal, subprocess, sys, time, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
REAL = os.environ.get("LIVE_PG")
DB = os.environ.get("LIVE_DB", "yyc_dev_live")
assert DB != "yyc_dev_spa", "never write to the shared copy"
os.environ["EDGE_DB"] = DB
os.environ["EDGE_PORT"] = os.environ.get("LIVE_PORT", "5191")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
SHOTS = os.environ.get("SHOTS") or os.path.expanduser("~/artifacts/live-dashboard/" + ("live" if REAL else "test"))
HKT = timezone(timedelta(hours=8))
TODAY = datetime.now(HKT).strftime("%Y-%m-%d")  # the page shows a time of today as HH:MM, of another day as DD/MM HH:MM
if not REAL:
    subprocess.run(["createdb", DB], stderr=subprocess.DEVNULL)
    import psycopg  # noqa: E402
    with psycopg.connect(f"postgresql:///{DB}", autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
import test_edge_ops as ops  # noqa: E402  (token, jwks, get, hydrated, no_sideways; DATABASE_URL = DB)
from playwright.sync_api import sync_playwright  # noqa: E402
import ai, corpus, edge_config, events, monitor  # noqa: E402
# the stages' models by display name, as edge_config publishes them (no OpenRouter call in tests)
ai.model_name = lambda m: {ai.MODEL: "Qwen3.7 Flash"}.get(m, "DeepSeek V4.1 Flash")

events._loop = lambda: None
COLLEAGUE = "colleague@yyc.hk"
LEFT = ["ingest", "fulltext", "comments", "stage1", "stage2"]
RIGHT = ["filed", "alert", "problem", "ai", "sys"]


def only_data_update(text):
    """a hydration report whose only differing line is <html data-update="current"> (as tests/test_proof_page.py)"""
    import re
    tree = text.split("hydration-mismatch", 1)[-1]
    diff = [re.sub(r"\s+", " ", l.strip()) for l in tree.splitlines() if re.match(r"^\s*[+-]\s", l)]
    return "hydration-mismatch" in text and diff == ['- data-update="current"']


def node(code):
    r = subprocess.run(["npx", "tsx", "--eval", code], cwd=APP, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)


def last_poll(minutes_ago):
    corpus.db().execute("INSERT INTO poll_state (k, v) VALUES ('last_poll', %s) ON CONFLICT (k) DO UPDATE SET v=excluded.v",
                        (json.dumps((datetime.now(HKT) - timedelta(minutes=minutes_ago)).replace(microsecond=0).isoformat()),))


def fixtures():
    edge_config.publish()
    last_poll(1)
    ing = corpus.ingest([{"url": f"https://news.example.com/{i}", "title": f"Ingested headline {i} 特首出席活動", "sid": "rthk",
                          "source": "香港電台", "published": "2026-10-21T10:00:00+08:00"} for i in range(30)])
    a, b = ing[-1], ing[-2]
    events.emit("fulltext", a, ok=True, chars=4321, head="第一行全文內容。第二行全文內容。第三行全文內容。", route="proxy", via="page")
    events.emit("fulltext", b, ok=False, route="direct", reason="HTTPError: HTTP Error 403: Forbidden")
    events.emit("comments", a, new=12, read=12, total=345, top=[{"text": "最多讚好的留言", "author": "網民", "likes": 88},
                {"text": "第二多讚好", "author": "乙", "likes": 20}, {"text": "第三條留言", "author": "丙", "likes": 3}])
    sts = [s["id"] for s in json.loads(json.dumps(corpus.kv_get("edge:config")))["streams"] if s["id"] in corpus.kv_get("edge:config")["scored"]]
    events.emit("stage1", a, model="haiku", purpose="scoring", scores={k: (70 if i == 0 else 5 * i) for i, k in enumerate(sts)},
                best=sts[0], reason="The CE is the main subject",
                reasons={k: ("The CE is the main subject" if i == 0 else f"reason for {k}") for i, k in enumerate(sts)})
    events.emit("stage2", a, model="gemini", purpose="scoring", sentiment="neutral", labels=["monitor"],
                checks={sts[0]: {"s1": 70, "s2": 35, "reason": "a passing mention", "s1reason": "stage 1 thought it central"}})
    corpus.db().execute("INSERT INTO stream_items (day, stream, url, item) VALUES ('2026-10-21', %s, %s, %s)",
                        (sts[0], "https://news.example.com/29", json.dumps({"post_id": a, "title": "Filed headline", "source": "香港電台",
                         "score": 82, "screen": 75, "category": "施政", "sentiment": "positive", "labels": ["chance"],
                         "seen": datetime.now(HKT).replace(microsecond=0).isoformat()})))
    events.emit("alert", a, channel="telegram", stream=sts[0], item=1, ok=True)
    events.emit("alert", a, channel="whatsapp", stream=sts[0], item=1, ok=False, error="gateway down")
    events.emit("problem", None, what="source", key="rthk", state="failing", since="2026-10-21T09:00:00+08:00", reason="HTTP 503")
    events.emit("problem", None, what="cronjob", key="cronjob:fulltext", state="recovered", since="2026-10-21T08:00:00+08:00",
                reason="fulltext ran again")
    events.flush()
    ai_fixtures()
    return a, b, sts


def ai_fixtures():
    """YYC AI's activity (events kind ai) and kv live:sys as monitor.live_once writes them"""
    t = datetime.now(timezone.utc)
    z = lambda s: (t - timedelta(seconds=s)).isoformat(timespec="milliseconds").replace("+00:00", "Z")  # noqa: E731
    for at, d in ((z(50), {"what": "start", "owner": "michelle.yeoh@yyc.hk", "host": "aws-922e69", "ok": True, "ms": 4210}),
                  (z(40), {"what": "calls", "owner": "michelle.yeoh@yyc.hk", "calls": 7, "usd": 0.1834, "calls_today": 92, "usd_today": 1.80,
                           "models": {"anthropic/claude-opus-5.5": 7}}),
                  (z(30), {"what": "start", "owner": "deyaochen@yyc.hk", "host": None, "ok": False, "ms": 180000, "reason": "host_boot_timeout"}),
                  # refused at the test account's own number (hermit's notifyPersonLimit false): grey, never red
                  (z(25), {"what": "start", "owner": "bot@deyaochen.com", "host": None, "ok": False, "ms": 60000, "reason": "pool_full",
                           "quiet": True}),
                  (z(20), {"what": "egress", "kind": "model", "host": "openrouter.ai", "path": "/api/v1/messages", "status": 402,
                           "decision": "allow", "owner": "michelle.yeoh@yyc.hk"})):
        corpus.db().execute("INSERT INTO events (at, kind, data) VALUES (%s, 'ai', %s)", (at, json.dumps(d)))
    sys_fixture()


def sys_fixture(edit=None, age=0):
    """kv live:sys as monitor.live_once writes it; `edit(snap, health)` changes the snapshot and the health area first
    (test 3b's states), `age` makes it that many seconds old"""
    t = datetime.now(timezone.utc)
    z = lambda s: (t - timedelta(seconds=s)).isoformat(timespec="milliseconds").replace("+00:00", "Z")  # noqa: E731
    snap = {"at": z(5), "today": z(3600), "recentMinutes": 15, "errors": {},
            "health": {"state": "warming", "findings": [{"level": "warming", "text": "Host aws-1b2c3d booting (40 s)"}]},
            "service": {"deployments": [{"name": n, "ready": 2, "desired": 2, "state": "ok", "pods": [{"restarts": 0}]}
                                        for n in ("pigeon", "magpie", "hermit", "egress", "tools", "cloudflared")]},
            "hosts": {"maxHosts": 3, "list": [
                {"name": "hermit-gw", "kind": "gateway", "gateway": True, "status": "active", "reachable": True, "cpus": 4, "load1": 0.3,
                 "memTotal": 8127721472, "memAvailable": 6460899328},
                {"name": "aws-922e69", "kind": "aws", "status": "active", "reachable": True, "sandboxes": 1, "slots": 12, "cpus": 4,
                 "load1": 0.16, "memTotal": 16464273408, "memAvailable": 15742693376},
                # still booting: it cannot answer yet, and that is not a problem
                {"name": "aws-1b2c3d", "kind": "aws", "status": "booting", "reachable": False, "sandboxes": 0, "slots": 12}] +
                [{"name": f"aws-a0000{i}", "kind": "aws", "status": "active", "reachable": True, "sandboxes": 2, "slots": 12, "cpus": 4,
                  "load1": 0.5, "memTotal": 16464273408, "memAvailable": 12000000000} for i in range(4)]},
            "sandboxes": {"maxActive": 10, "list": [{"owner": "michelle.yeoh@yyc.hk", "status": "ready", "host": "aws-922e69", "ageS": 610, "idleS": 4},
                                                    {"owner": "chendeyao000@gmail.com", "status": "ready", "host": "aws-922e69", "ageS": 3000, "idleS": 900}]},
            "budget": {"status": {"spentUsd": 23.07, "limitUsd": 100, "since": "2026-10-21T02:05:52.000Z", "exhausted": False},
                       "total": {"today": 2.96, "month": 24.64, "callsToday": 225},
                       "byUser": [{"owner": "michelle.yeoh@yyc.hk", "callsToday": 92, "today": 1.80}, {"owner": "chendeyao000@gmail.com", "callsToday": 133, "today": 1.16}],
                       "byModel": [{"model": "anthropic/claude-opus-5.5", "callsToday": 225, "today": 2.96}]},
            "people": [{"owner": "michelle.yeoh@yyc.hk", "today": 2, "live": 1}, {"owner": "tony.leung@example.com", "today": 0, "live": 0},
                       {"owner": "gone@example.com", "today": 0, "live": 0, "former": True}],
            "recent": {"failedStarts": 1, "modelErrors": 1, "hostErrors": 0, "blocked": 0},
            "starts": {"summary": {"n": 14, "ok": 13, "failed": 1, "p50Ms": 4300, "p95Ms": 9100}}, "scaler": {"plan": {"available": 11, "waiting": 0}}}
    bal = [{"id": "openrouter", "name": "OpenRouter", "status": "ok", "credit_balance_usd": 61.52, "source": "provider API (GET /api/v1/credits)",
            "keys": [{"name": "yyc-chat", "usage_today_usd": 2.96, "usage_month_usd": 25.30}, {"name": "yyc-stream", "usage_today_usd": 1.54, "usage_month_usd": 339.28}]},
           {"id": "tikhub", "name": "TikHub", "status": "ok", "balance_usd": 12.4, "usage_today_usd": 0.31, "source": "provider API (user/get_user_info)"},
           {"id": "iproyal", "name": "IPRoyal (residential)", "status": "ok", "left_gb": 3.2, "unallocated_gb": 0, "source": "provider API (/v1/me)"},
           {"id": "anyapi", "name": "AnyAPI", "status": "error", "error": "HTTPError 500"},
           # AWS as monitor.bal_aws gives it (YYC's account on 2026-11-17: its real figures and source)
           {"id": "aws", "name": "AWS (account yyc)", "status": "ok", "month_usd": 1237.31, "yesterday_usd": 105.29,
            "services_month": [{"name": "EC2", "usd": 1128.80}, {"name": "Tax", "usd": 206.22}, {"name": "EC2 other (disks, traffic)", "usd": 101.61}],
            "running_types": [{"type": "m7i.xlarge", "n": 12}, {"type": "m7i.2xlarge", "n": 2}, {"type": "t3.large", "n": 1}],
            "source": "provider API (Cost Explorer GetCostAndUsage: UnblendedCost per UTC day and service, read every 6 h at "
                      "USD 0.01 a request; EC2 DescribeInstances in eu-central-1); AWS completes a day's cost up to 24 h after it ends"},
           # the rest of production's providers (2026-11-17: eleven, the right display was 4 lines short with them)
           {"id": "capsolver", "name": "CapSolver", "status": "ok", "balance_usd": 4.213, "source": "provider API (getBalance)"},
           {"id": "2captcha", "name": "2Captcha", "status": "ok", "balance_usd": 7.882, "source": "provider API (getBalance)"},
           {"id": "hetzner", "name": "Hetzner Cloud (project yyc)", "status": "ok", "servers": [{}] * 9, "currency": "EUR",
            "list_monthly_net": 412.66, "source": "provider API (servers, pricing)"},
           {"id": "cloudflare", "name": "Cloudflare (account YYC)", "status": "ok", "plan": "Workers Paid", "overage_usd": 0.42,
            "source": "provider API (GraphQL analytics)"},
           {"id": "exa", "name": "Exa (the data API's web search; free tier)", "status": "ok", "exa_charged_month_usd": 0.0123,
            "calls_month": 37, "source": "provider API (usage)"}]
    health = {"balances_at": z(120), "providers": [{"id": p["id"], "name": p["name"], "status": p["status"], "figs": monitor.provider_line(p),
                                                    "source": p.get("source"), "error": p.get("error")} for p in bal],
              "stops": [], "deployments": {"n": 12, "ok": 11, "short": ["wisers 0/1"]},
              "cronjobs": {"n": 24, "late": [], "failed": ["forums"], "running": 3, "off": 2},
              "nodes": [{"name": f"yyc-pool-small-worker{i}", "ready": True, "pressure": [], "unschedulable": False} for i in range(1, 6)],
              # monitor.database / wisers / storage as health_slim slims them
              "postgres": {"pod": "pg-1", "connections": {"total": 85, "max": 300, "by_state": {"idle": 80, "active": 4, "idle in transaction": 1},
                                                          "waiting_on_locks": 0},
                           "longest_transaction": {"seconds": 95.0, "database": "app", "user": "app", "application": "yyc-news-stream", "state": "active"},
                           "tps": 48.3, "cpu_cores": 1.39, "node_cores": 4, "memory_mib": 4907, "memory_limit_mib": 6144,
                           "space": {"pvc": "pg-1", "capacity_bytes": 105089261568, "used_bytes": 88e9, "free_bytes": 17e9, "floor_bytes": 20 * 2**30},
                           "databases": [{"name": "app", "bytes": 24288335539}, {"name": "jarvis", "bytes": 19199667}], "wal_bytes": 3187671040,
                           "archive": {"last_archived": None, "seconds_since_archived": 25, "waiting": 0,
                                       "last_backup": TODAY + "T03:57:09+08:00", "last_failed": None, "problems": []}},
              "wisers": {"state": "held", "since": TODAY + "T14:02:00+08:00", "probe": {"at": TODAY + "T16:12:00+08:00", "ok": True, "secs": 1.2},
                         "failures": {}, "logins_1h": 0, "failed_logins_1h": 0, "today": 12, "problems": []},
              "storage": {"buckets": [{"name": "yyc-pg-pitr", "objects": 8194, "bytes": 159413804689},
                                      {"name": "yyc-pg-backups", "objects": 6869, "bytes": 122001146482},
                                      {"name": "yyc-tiktok-videos", "objects": 390, "bytes": 4437131926}] +
                                     [{"name": f"yyc-extra-{i}", "objects": 10 + i, "bytes": 2 ** 30 * (3 - i * 0.2)} for i in range(9)],
                          "bytes": 285852083097, "objects": 15453, "computed_at": TODAY + "T16:00:00+08:00"}}
    if edit:
        edit(snap, health)
    corpus.kv_set("live:sys", {"at": (datetime.now(HKT) - timedelta(seconds=age)).isoformat(timespec="seconds"),
                               "ai": monitor.ai_slim(snap), "health": health})


class Server:
    """vite dev against the database, verifying Access tokens against the test's key (test_edge_ops)"""
    def __init__(self, pg):
        os.makedirs(SHOTS, exist_ok=True)
        self.vars = os.path.join(APP, ".dev.vars")
        if not os.path.exists(self.vars):
            with open(self.vars, "w") as f, open(self.vars + ".example") as ex:
                f.write(ex.read())
        with open(self.vars) as f:
            self.before = f.read()
        with open(self.vars, "w") as f:
            f.write("\n".join(l for l in self.before.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                    "\nACCESS_TEST_JWKS=" + ops.jwks() + "\nJARVIS_LIVE_SECRET=" + JARVIS_SECRET + "\n")
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=pg)
        self.log = open(os.path.join(SHOTS, "dev.log"), "w")  # noqa: SIM115
        self.dev = subprocess.Popen(["npx", "vite", "dev", "--port", str(ops.PORT), "--strictPort"], cwd=APP, env=env,
                                    stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + 600
        while True:
            try:
                if ops.get("/version")[0]:
                    break
            except Exception:
                pass
            if self.dev.poll() is not None or time.time() > deadline:
                self.stop()
                raise RuntimeError("the dev server did not start: " + os.path.join(SHOTS, "dev.log"))
            time.sleep(2)

    def stop(self):
        try:
            os.killpg(self.dev.pid, signal.SIGTERM)
            self.dev.wait(timeout=30)
        except Exception:
            pass
        self.log.close()
        with open(self.vars, "w") as f:
            f.write(self.before)


def boxes(page):
    return page.evaluate("""[...document.querySelectorAll('.lp')].map(e => { const r = e.getBoundingClientRect();
        return { id: e.dataset.panel, x: r.left, r: r.right, y: r.top, b: r.bottom }; })""")


JARVIS_SECRET = "dev-jarvis"
# Jarvis 4's push (its router, every 5 s): 34 running, all sent; the Worker keeps at most 32 (the right display's two
# columns of 16; Deyao, 2026-11-17: his sessions went past the 16 it was drawn for)
JARVIS = {"total": 34, "sessions": [
    {"name": "Live dashboard: booting host colour", "state": "working", "jobs": 0, "subagents": 0},
    {"name": "Cathay seat for the HKG flight", "state": "waiting", "jobs": 0, "subagents": 0},
    {"name": "YYC AI image compaction", "state": "background", "jobs": 2, "subagents": 1},
    # paused or idle with a wakeup/cron armed (its next one; a watch alone has no time) (Deyao, 2026-11-17)
    {"name": "Deploy watch for aviary", "state": "scheduled", "jobs": 0, "subagents": 0, "next": int(time.time() * 1000) + 3600_000},
    {"name": "Watching the CI run", "state": "scheduled", "jobs": 0, "subagents": 0},
    {"name": "booting session", "state": "booting", "jobs": 0, "subagents": 0},
    {"name": "Qwen Code PDF output differs; fix deliver file display", "state": "working", "jobs": 1, "subagents": 0}] +
    [{"name": f"idle session {i}", "state": "idle", "jobs": 0, "subagents": 0} for i in range(27)]}


def push_jarvis(doc, secret=JARVIS_SECRET):
    """POST /live/jarvis as Jarvis 4's router does → the status code"""
    import urllib.error, urllib.request
    t = int(time.time() * 1000)
    body = {**doc, "at": t, "sessions": [{**x, "since": t - 600_000} for x in doc["sessions"]]}
    req = urllib.request.Request(ops.BASE + "/live/jarvis", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Jarvis-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def open_page(br, path, w, h, errors=None):
    """the dashboard itself: /app/live?frame=1[&half=…] (what the shell at /app/live shows in its frame)"""
    path = path + ("&" if "?" in path else "?") + "frame=1" if path.startswith("/app/live") and "frame=" not in path else path
    ctx = br.new_context(viewport={"width": w, "height": h})
    tok = ops.token(ops.DEYAO)  # signed in on the site only (a header on Google Fonts' requests fails their CORS check)
    ctx.route(ops.BASE + "/**", lambda r: r.continue_(headers={**r.request.headers, "cf-access-jwt-assertion": tok}))
    ctx.add_init_script("""window.__wake = 0; Object.defineProperty(navigator, 'wakeLock', { configurable: true,
        value: { request: () => { window.__wake++; return Promise.resolve({ release: () => Promise.resolve() }); } } });""")
    page = ctx.new_page()
    if errors is not None:
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(ops.BASE + path, wait_until="domcontentloaded", timeout=180000)
    ops.hydrated(page, ".live")
    page.wait_for_selector(".lp li:not(.dim)", timeout=60000)
    return ctx, page


@unittest.skipIf(REAL, "LIVE_PG: screenshots of live data only")
class LivePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a, cls.b, cls.sts = fixtures()
        cls.srv = Server(f"postgres://claude:dev@127.0.0.1:5432/{DB}")
        assert push_jarvis(JARVIS, "wrong") == 403
        assert push_jarvis(JARVIS) == 200
        cls.pw = sync_playwright().start()
        cls.br = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.br.close()
        cls.pw.stop()
        cls.srv.stop()

    def test_1_deyao_only(self):
        for who in (None, ops.token(COLLEAGUE)):
            for p in ("/app/live", "/app/live?half=left", "/app/live/data", "/app/live/x"):
                code, h, body = ops.get(p, {"Cf-Access-Jwt-Assertion": who} if who else {})
                self.assertEqual((code, body), (404, b"not found"), p)
        code, h, body = ops.get("/app/live?half=left", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})
        self.assertEqual(code, 200)
        self.assertEqual(h.get("X-Robots-Tag"), "noindex")
        code, h, body = ops.get("/app/live/data?cfg=1", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual(set(d["panels"]), set(LEFT + RIGHT) - {"sys"})
        self.assertEqual(d["sys"]["ai"]["health"]["state"], "warming")
        self.assertEqual(ops.get("/app/live/x", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})[0], 404)

    def test_2_fixed_layout_and_seam(self):
        L = node("import { LAYOUTS, CARD_H, cardsIn, phoneLayout } from './src/lib/liveLayout.ts';"
                 "const all = { ...LAYOUTS, phone: phoneLayout(390) };"
                 "console.log(JSON.stringify({ L: all, H: CARD_H, n: Object.fromEntries(Object.entries(all).map(([m, l]) => [m, Object.fromEntries(l.panels.map(b => [b.id, cardsIn(b)]))])) }))")
        errors, canvas = [], []
        for mode, path, w, h in (("left", "/app/live?half=left", 1920, 1200), ("right", "/app/live?half=right", 1920, 1080),
                                 ("single", "/app/live", 1440, 830), ("left", "/app/live?half=left", 1600, 900),
                                 ("phone", "/app/live", 390, 844)):
            ctx, page = open_page(self.br, path, w, h, errors)
            spec = L["L"][mode]
            got = boxes(page)
            self.assertEqual([(b["id"], round(b["x"]), round(b["y"]), round(b["r"] - b["x"]), round(b["b"] - b["y"])) for b in got],
                             [(p["id"], p["x"], p["y"], p["w"], p["h"]) for p in spec["panels"]], (mode, w, h))  # the px boxes, whatever the window
            # nothing hangs below its panel (Deyao, 2026-11-12: Systems' Postgres, WiseSearch and object storage fell
            # off the screen): every line of Systems and Jarvis 4 ends inside its box
            over = page.evaluate("""[...document.querySelectorAll('.lp')].flatMap(p => { const b = p.getBoundingClientRect().bottom;
                return [...p.querySelectorAll('.st > *, .js > *')].filter(e => e.getBoundingClientRect().bottom > b + 0.5)
                  .map(e => p.dataset.panel + ': ' + e.textContent.slice(0, 50)); })""")
            self.assertEqual(over, [], (mode, w, h))
            for b in got:
                self.assertTrue(0 <= b["x"] and b["r"] <= spec["w"] and b["b"] <= spec["h"], b)
                n = page.locator(f".lp[data-panel={b['id']}] li").count()
                self.assertLessEqual(n, L["n"][mode][b["id"]], b["id"])
                if b["id"] == "ingest":  # 30 fixture posts: the panel is full
                    self.assertEqual(n, L["n"][mode]["ingest"])
                heights = page.eval_on_selector_all(f".lp[data-panel={b['id']}] li", "els => els.map(e => e.getBoundingClientRect().height)")
                self.assertTrue(all(abs(x - L["H"][b["id"]]) < 0.01 for x in heights), (b["id"], heights))
            if mode == "phone":
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))  # no sideways scroll
                self.assertGreater(page.evaluate("document.documentElement.scrollHeight"), h)  # one column, it scrolls
                page.screenshot(path=os.path.join(SHOTS, "phone-390x844.png"), full_page=True)
            elif (w, h) == (spec["w"], spec["h"]):
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight"))
                page.screenshot(path=os.path.join(SHOTS, f"{mode}-{w}x{h}.png"))
            if mode in ("left", "right") and w == 1920:
                canvas += [(b["id"], b["x"] + (0 if mode == "left" else 1920), b["r"] + (0 if mode == "left" else 1920)) for b in got]
                self.assertGreaterEqual(page.evaluate("window.__wake"), 1)  # (dev mode runs effects twice)
            sizes = page.evaluate("""['.lp .t', '.lp .x', '.lp .m', '.lp > h2'].map(s => getComputedStyle(document.querySelector(s)).fontSize)""")
            self.assertEqual(sizes, ["12px", "10px", "10px", "10px"])
            # the pixel font at its design sizes, shipped with the site (public/fonts/: not one the PC must have), loaded
            fonts = page.evaluate("""document.fonts.ready.then(() => [...document.fonts].filter(f => f.family.includes('Fusion Pixel'))
                .map(f => f.family.replace(/"/g, '') + ':' + f.status).sort())""")
            self.assertEqual(fonts, ["Fusion Pixel 10:loaded", "Fusion Pixel 12:loaded"], fonts)
            # on the pixel (Deyao: "text is small but not aligned on the pixel making it harder to read"; the two
            # displays are 1x): every font size in whole px, and every line of text starts on a whole pixel
            fonts = page.evaluate("[...new Set([...document.querySelectorAll('.live *')].map(e => getComputedStyle(e).fontSize))]")
            self.assertTrue(all(float(f[:-2]).is_integer() for f in fonts), fonts)
            off = page.evaluate("""[...document.querySelectorAll('.live-bar, .lp > h2, .lp li, .lp .t, .lp .x, .lp .m, .lp .m > *, .lp .topic, .lp .topic > *, .lp .tag')]
                .map(e => [e.className || e.tagName, e.getBoundingClientRect().top, e.getBoundingClientRect().left])
                .filter(([, t, l]) => Math.abs(t - Math.round(t)) > 0.01 || Math.abs(l - Math.round(l)) > 0.01)""")
            self.assertEqual(off[:10], [], (mode, w, h, len(off)))
            ctx.close()
        for pid, x, r in canvas:  # the seam: no panel crosses x = 1920 of the 3840-wide canvas
            self.assertFalse(x < 1920 < r, (pid, x, r))
        rows = lambda m, top: [b["id"] for b in sorted([b for b in L["L"][m]["panels"] if (b["y"] > 100) == top], key=lambda b: b["x"])]  # noqa: E731
        self.assertEqual((rows("left", False), rows("left", True)), (["ingest", "fulltext", "comments"], ["stage1", "stage2"]))
        self.assertEqual((rows("right", False), rows("right", True)), (["filed", "alert", "problem", "jarvis"], ["ai", "sys"]))
        import re
        css = re.sub(r"/\*.*?\*/", "", open(os.path.join(APP, "src", "styles", "live.css")).read(), flags=re.S)  # rules, not comments
        self.assertNotRegex(css, r"\d(vw|vh|vmin|vmax)\b|clamp\(|@container")
        self.assertEqual([e for e in errors if not only_data_update(e)], [])

    def test_3_cards(self):
        ctx, page = open_page(self.br, "/app/live", 1440, 830)
        # the one-window box is too small for every Systems line in full: the compact form, every provider and check
        # still there, each in its state's colour, never a link to the rest (Deyao, 2026-11-17: "it should just fit")
        sp = page.locator(".lp[data-panel=sys]")
        self.assertNotIn("more line", sp.inner_text())
        self.assertEqual(sp.locator("a").count(), 0)
        # (Deyao, 2026-11-17) a provider's API that can't be read is blind (magenta), not red; a Deployment with no pod
        # ready is red; free space under its floor and one failed CronJob run are cautions (amber)
        self.assertEqual(sp.locator(".pc.blindc", has_text="AnyAPI").count(), 1)
        self.assertEqual(sp.locator(".row.why", has_text="Deployments").count(), 1)
        for x in ("Postgres:", "CronJobs"):
            self.assertEqual(sp.locator(".row.warnc", has_text=x).count(), 1, x)
            self.assertEqual(sp.locator(".row.why", has_text=x).count(), 0, x)
        t = lambda p: page.inner_text(f".lp[data-panel={p}]")  # noqa: E731
        self.assertIn("Ingested headline 29", t("ingest"))
        self.assertIn("香港電台", t("ingest"))
        self.assertIn("proxy", t("fulltext"))
        self.assertIn("4,321 characters", t("fulltext"))
        self.assertIn("第三行全文內容", t("fulltext"))
        # a news source's failure is quiet: most likely their end, outside our control (Deyao, 2026-11-17)
        self.assertEqual(page.locator(".lp[data-panel=fulltext]").locator("li.bad, li.cau, .why, .warn, .badt").count(), 0)
        failed = page.locator(".lp[data-panel=fulltext] li", has_text="not fetched")
        self.assertEqual(failed.count(), 1)
        self.assertIn("HTTP Error 403", failed.inner_text())
        # an alert refused for good is a service lost: red
        self.assertEqual(page.locator(".lp[data-panel=alert] li.bad", has_text="gateway down").count(), 1)
        self.assertIn("+12 new, 345 on the platform", t("comments"))
        for x in ("最多讚好的留言", "第二多讚好", "第三條留言"):
            self.assertIn(x, t("comments"))
        self.assertEqual(page.locator(".lp[data-panel=stage1] .topic").count(), len(self.sts))
        self.assertEqual(page.locator(".lp[data-panel=stage1] .topic.pass").count(), 1)
        self.assertEqual(page.locator(".lp[data-panel=stage1] .topic").first.inner_text().split("\n")[0], self.sts[0])  # highest first
        for k in self.sts[1:]:
            self.assertIn(f"reason for {k}", t("stage1"))
        self.assertIn("Qwen3.7 70 → DeepSeek 35", t("stage2"))
        self.assertIn("Qwen3.7: stage 1 thought it central", t("stage2"))
        self.assertIn("DeepSeek: a passing mention", t("stage2"))
        self.assertIn("rejected (threshold", t("stage2"))
        self.assertIn("score 82 (stage 1: 75)", t("filed"))
        self.assertIn("Filed headline", t("filed"))
        self.assertIn("Telegram", t("alert"))
        self.assertIn("gateway down", t("alert"))
        self.assertIn("failing", t("problem"))
        self.assertIn("HTTP 503", t("problem"))
        self.assertIn("recovered", t("problem"))
        # dark cockpit (Deyao, 2026-11-02): the problem tile and an all-fine status light nothing up
        self.assertEqual(page.locator(".lp[data-panel=problem] li.bad, .lp[data-panel=problem] .badt, .lp[data-panel=problem] .why").count(), 0)
        self.assertIn("4 Stage 1, Qwen3.7 Flash", page.inner_text(".lp[data-panel=stage1] > h2"))  # the stage's model
        self.assertIn("5 Stage 2, DeepSeek V4.1 Flash", page.inner_text(".lp[data-panel=stage2] > h2"))
        self.assertIn("/10 min", page.inner_text(".lp[data-panel=fulltext] > h2"))
        self.assertIn("2 /10 min, 2 /h", page.inner_text(".lp[data-panel=fulltext] > h2"))
        ctx.close()
        # YYC AI and Systems (kv live:sys), at their real size on the right display
        ctx, page = open_page(self.br, "/app/live?half=right", 1920, 1080)
        page.wait_for_selector("[data-ai-state]", timeout=20000)
        ai_ = page.inner_text(".lp[data-panel=ai]")
        for x in ("warming up", "Host aws-1b2c3d booting", "Sandboxes now: 2 of 10 max", "michelle.yeoh@yyc", "active now", "idle 15 min",
                  "USD 2.96", "225 model calls", "budget: USD 23.07 of USD 100 used since 10-21", "2 conversations today",
                  "1 failed starts", "14 starts, 1 failed", "median 4.3 s", "model calls", "+7 calls", "sandbox start failed",
                  "host_boot_timeout", "model 402", "sandbox started"):
            self.assertIn(x, ai_)
        # errors that are over (the failed start, the 402) are cautions: amber, not red (Deyao, 2026-11-17)
        self.assertEqual(page.locator(".lp[data-panel=ai] li.cau").count(), 2)
        self.assertEqual(page.locator(".lp[data-panel=ai]").locator("li.bad, .badt, .why").count(), 0)
        self.assertEqual(page.locator(".lp[data-panel=ai] li.cau .tag.warn", has_text="sandbox start failed").count(), 1)
        self.assertEqual(page.locator("[data-ai-state] .row.warnc", has_text="1 failed starts").count(), 1)
        # the bot's start refused at its own limit: a plain grey card, neither red nor amber (Deyao, 2026-11-17)
        refused = page.locator(".lp[data-panel=ai] li", has_text="sandbox start refused")
        self.assertEqual(refused.count(), 1)
        self.assertIn("at its own sandbox limit", refused.inner_text())
        self.assertEqual(refused.locator(".badt, .why, .warn").count(), 0)
        self.assertNotIn("bad", refused.get_attribute("class") or "")
        self.assertNotIn("cau", refused.get_attribute("class") or "")
        # warming up is an advisory, not a caution: blue (deyao-web-design status colours)
        self.assertEqual(page.locator("[data-ai-state] .tag.info", has_text="warming up").count(), 1)
        self.assertEqual(page.locator("[data-ai-state] .row.infoc", has_text="Host aws-1b2c3d booting").count(), 1)
        # a view-only monitor: nothing on it can be clicked, no links or buttons (Deyao, 2026-11-17)
        self.assertEqual(page.locator("a, button, input, select, textarea, [onclick], [role=button], [role=link]").count(), 0)
        sys_ = page.inner_text(".lp[data-panel=sys]")
        for x in ("OpenRouter", "credit USD 61.52", "yyc-chat USD 2.96 today · USD 25.30 month", "provider API", "TikHub",
                  "balance USD 12.40, today USD 0.31", "yycstream left 3.2 GB", "AnyAPI", "can't read: HTTPError 500",
                  "nodes: 5 of 5 ready", "short: wisers 0/1", "last run failed: forums",
                  "YYC AI pods: pigeon 2/2", "gateway", "AWS host", "1 of 12 sandboxes",
                  # Postgres, WiseSearch, object storage (Deyao, 2026-11-05)
                  "Postgres load: 85 of 300 connections (4 active, 1 idle in transaction)", "48.3 transactions/s",
                  "CPU 1.39 of 4 cores", "memory 4.8 GiB of 6.0 GiB", "longest open transaction 2 min (app, yyc-news-stream, active)",
                  "WAL archive: last 25 s ago, 0 waiting", "Postgres space: 15.8 GiB free of 97.9 GiB (alert under 20.0 GiB)", "volume pg-1",
                  "WiseSearch: logged in since 14:02", "health check ok (1.2 s", "no failed calls in 15 min", "12 searches today",
                  "Object storage: 266.2 GiB in 15,453 objects, 12 buckets", "Hetzner's bucket stats", "yyc-pg-pitr 148.5 GiB, 8,194 objects",
                  "yyc-extra-8"):
            self.assertIn(x, sys_)
        # under the free-space floor: the space line in amber (near a limit; nobody is stopped), never red; the load and
        # WiseSearch lines quiet
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.warnc", has_text="Postgres space").count(), 1)
        for x in ("Postgres space", "Postgres load", "WiseSearch"):
            self.assertEqual(page.locator(".lp[data-panel=sys] .row.why", has_text=x).count(), 0, x)
        self.assertEqual(page.locator(".lp[data-panel=sys]").locator(".row.warnc, .row.blindc", has_text="Postgres load").count(), 0)
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.blindc", has_text="AnyAPI").count(), 1)
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.why", has_text="wisers 0/1").count(), 1)
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.warnc", has_text="last run failed: forums").count(), 1)
        # a booting sandbox host is starting, not wrong: blue, never red, and no "not answering" (Deyao, 2026-11-12)
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.infoc", has_text="aws-1b2c3d").count(), 1)
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.why", has_text="aws-1b2c3d").count(), 0)
        self.assertNotIn("not answering", page.locator(".lp[data-panel=sys] .row", has_text="aws-1b2c3d").inner_text())
        self.assertEqual(page.locator(".lp[data-panel=sys] .row.why", has_text="aws-922e69").count(), 0)
        self.assertNotIn("more lines", sys_)
        # AWS (Deyao, 2026-11-17): the month and yesterday on the provider's line, then the services and the instances
        for x in ("AWS (account yyc)", "month USD 1,237.31 · yesterday USD 105.29", "by service EC2 1,128.80 · Tax 206.22 · EC2 other 101.61",
                  "running now 12 × m7i.xlarge, 2 × m7i.2xlarge, 1 × t3.large"):
            self.assertIn(x, sys_)
        # Jarvis 4 (Deyao, 2026-11-12; 32 on the right display since 2026-11-17): in the router's order, each its state word
        # in its colour, the 2 more of the 34 in the header
        jp = page.locator(".lp[data-panel=jarvis]")
        self.assertEqual(jp.locator(".jr").count(), 32)
        self.assertIn("2 more", jp.locator("h2").inner_text())
        self.assertEqual(jp.locator(".jr[data-state=working] .tag.inkf", has_text="working").count(), 2)
        self.assertEqual(jp.locator(".jr[data-state=waiting] .tag.warn", has_text="needs you").count(), 1)
        self.assertEqual(jp.locator(".jr[data-state=idle] .tag.warn", has_text="idle").count(), 25)  # amber (Deyao, 2026-11-17)
        self.assertEqual(jp.locator(".jr[data-state=background] .tag.info", has_text="background").count(), 1)
        self.assertEqual(jp.locator(".jr[data-state=booting] .tag.info", has_text="booting").count(), 1)
        # scheduled is blue like background, with its next wake (HKT) or "watching" (Deyao, 2026-11-17)
        self.assertEqual(jp.locator(".jr[data-state=scheduled] .tag.info", has_text="scheduled").count(), 2)
        self.assertRegex(jp.locator(".jr[data-state=scheduled]", has_text="Deploy watch").inner_text(), r"next (\d\d-\d\d )?\d\d:\d\d")
        self.assertIn("watching", jp.locator(".jr[data-state=scheduled]", has_text="Watching the CI").inner_text())
        self.assertEqual(jp.locator(".tag.badt").count(), 0)  # nothing here is a failure
        self.assertRegex(jp.locator(".jr[data-state=background]").inner_text(), r"for \d+ min, 2 background jobs, 1 subagent")
        self.assertIn("Cathay seat for the HKG flight", jp.locator(".jr[data-state=waiting]").inner_text())
        ctx.close()
        # one window: two columns of 8
        ctx, page = open_page(self.br, "/app/live", 1440, 830)
        cells = page.evaluate("[...document.querySelectorAll('.lp[data-panel=jarvis] .jr')].map(e => Math.round(e.getBoundingClientRect().left))")
        self.assertEqual((len(cells), len(set(cells))), (16, 2))
        self.assertIn("18 more", page.locator(".lp[data-panel=jarvis] h2").inner_text())
        ctx.close()
        # the right display (Deyao, 2026-11-17: more than 16 running): two columns of 16, the 32 the Worker keeps, every
        # title whole (a 54-character one among them)
        ctx, page = open_page(self.br, "/app/live?half=right", 1920, 1080)
        cells = page.evaluate("[...document.querySelectorAll('.lp[data-panel=jarvis] .jr')].map(e => Math.round(e.getBoundingClientRect().left))")
        self.assertEqual((len(cells), len(set(cells))), (32, 2))
        cut = page.evaluate("[...document.querySelectorAll('.lp[data-panel=jarvis] .jn')].filter(e => e.scrollWidth > e.clientWidth).map(e => e.textContent)")
        self.assertEqual(cut, [])
        self.assertEqual(page.locator(".lp[data-panel=jarvis] .jn", has_text="Qwen Code PDF output differs; fix deliver file display").count(), 1)
        ctx.close()

    def test_3b_state_colours(self):
        """Deyao, 2026-11-17: red = a service lost right now; a replica down with others serving, something near a
        limit, a failing backup and an error that is over are amber; a read that failed and old figures are blind
        (magenta); one person stopped is red"""
        ctx, page = open_page(self.br, "/app/live?half=right", 1920, 1080)
        page.wait_for_selector("[data-ai-state]", timeout=20000)
        sp, ap = page.locator(".lp[data-panel=sys]"), page.locator("[data-ai-state]")

        def amber(snap, h):
            d = snap["service"]["deployments"]
            d[0].update(ready=1, state="problem")  # a hermit from before "watch": short, the other pod serves
            d[1].update(ready=1, state="watch")
            snap["health"] = {"state": "blind", "findings": [{"level": "blind", "text": "Could not read budget: timeout"},
                                                             {"level": "watch", "text": "pigeon: 1 of 2 pods ready"}]}
            snap["hosts"]["list"][3].update(reachable=False, sandboxes=0)  # aws-a00000: nobody on it
            h["deployments"] = {"n": 12, "ok": 11, "short": ["mcp 1/2"]}
            h["nodes"][0]["ready"] = False
            h["nodes"][1]["pressure"] = ["DiskPressure"]
            h["postgres"]["connections"]["total"] = 290
            h["postgres"]["archive"]["problems"] = ["the base backup failed at 03:57"]
            h["wisers"].update(problems=["3 call(s) failed in the last 15 min (3 timeout)"], down=False)
            h["storage"]["refresh_error"] = "HTTP 503"
            h["storage"]["buckets"][0] = {"name": "yyc-pg-pitr", "error": "AccessDenied"}
        sys_fixture(amber)
        page.wait_for_selector(".lp[data-panel=sys] .row.warnc:has-text('mcp 1/2')", timeout=20000)
        for x in ("YYC AI pods: pigeon 1/2, magpie 1/2", "aws-a00000", "mcp 1/2", "last run failed: forums", "nodes: 3 of 5 ready",
                  "Postgres load: 290 of 300", "the base backup failed", "Postgres space", "WiseSearch: logged in"):
            self.assertEqual(sp.locator(".row.warnc", has_text=x).count(), 1, x)
        for x in ("AnyAPI", "Object storage:"):
            self.assertEqual(sp.locator(".row.blindc", has_text=x).count(), 1, x)
        self.assertEqual(sp.locator(".bk.blindc", has_text="yyc-pg-pitr can't read").count(), 1)
        self.assertEqual(sp.locator(".why").count(), 0, sp.locator(".why").all_inner_texts())
        self.assertEqual(ap.locator(".tag.blindt", has_text="can't see").count(), 1)
        self.assertEqual(ap.locator(".row.blindc", has_text="Could not read budget").count(), 1)
        self.assertEqual(ap.locator(".row.warnc", has_text="pigeon: 1 of 2 pods ready").count(), 1)
        self.assertEqual(ap.locator(".why, .badt").count(), 0)
        page.screenshot(path=os.path.join(SHOTS, "right-states-amber-blind.png"))

        def red(snap, h):
            snap["service"]["deployments"][0].update(ready=0, state="problem")
            snap["health"] = {"state": "problem", "findings": [{"level": "problem", "text": "pigeon: 0 of 2 pods ready"}]}
            snap["hosts"]["list"][0]["reachable"] = False  # the gateway
            snap["hosts"]["list"][1]["reachable"] = False  # aws-922e69, with one sandbox on it
            h["deployments"] = {"n": 12, "ok": 10, "short": ["wisers 0/1", "mcp 1/2"]}
            h["cronjobs"].update(late=["forums"], failed=["forums"])
            for n in h["nodes"]:
                n["ready"] = False
            h["wisers"].update(problems=["its health check failed 2 times in a row (timeout: )"], down=True)
            h["postgres"] = {"error": "timeout"}
        sys_fixture(red, age=180)
        page.wait_for_selector(".lp[data-panel=sys] .row.why:has-text('pigeon 0/2')", timeout=20000)
        for x in ("YYC AI pods: pigeon 0/2", "hermit-gw", "aws-922e69", "wisers 0/1", "late: forums", "nodes: 0 of 5 ready", "WiseSearch: logged in"):
            self.assertEqual(sp.locator(".row.why", has_text=x).count(), 1, x)
        self.assertEqual(sp.locator(".row.blindc", has_text="Postgres: can't read: timeout").count(), 1)
        # the figures are three minutes old: blind, in the header of both panels
        self.assertEqual(page.locator(".lp[data-panel=sys] > h2 .blind", has_text="no update since").count(), 1)
        self.assertEqual(ap.locator(".tag.badt", has_text="problem").count(), 1)
        self.assertEqual(ap.locator(".row.why", has_text="pigeon: 0 of 2 pods ready").count(), 1)
        colour = lambda sel: page.locator(sel).first.evaluate("e => getComputedStyle(e).color")  # noqa: E731
        self.assertEqual(colour(".lp[data-panel=sys] .row.blindc"), "rgb(244, 114, 208)")  # magenta, its own colour
        self.assertEqual(colour(".lp[data-panel=sys] .row.why"), "rgb(255, 77, 90)")
        page.screenshot(path=os.path.join(SHOTS, "right-states-red.png"))
        sys_fixture()
        ctx.close()

    def test_4_one_at_a_time(self):
        ctx, page = open_page(self.br, "/app/live?half=left", 1920, 1200)
        page.evaluate("""window.__ins = []; const el = document.querySelector('.lp[data-panel=fulltext]');
            new MutationObserver(() => window.__ins.push([performance.now(), Number(el.dataset.inserted),
              el.querySelector('li')?.innerText || ''])).observe(el, { attributes: true, attributeFilter: ['data-inserted'] });""")
        ids = []
        for i in range(3):
            pid = corpus.ingest([{"url": f"https://news.example.com/one-{i}", "title": f"One by one {i}", "sid": "rthk", "source": "香港電台"}])[0]
            events.emit("fulltext", pid, ok=True, chars=99, head=f"第 {i} 篇", route="ISP IP")
            ids.append(pid)
        events.flush()  # all three in one write: one read brings them together
        # paced (lib/liveLayout enqueue/gapAt): the first card goes on at once (the panel was idle), the rest one at a
        # time at the panel's measured rate, at most MAX_WAIT (120 s) after they arrived: 2 left → at most 60 s apart
        page.wait_for_function("window.__ins.length >= 1", timeout=20000)
        ins = page.evaluate("window.__ins")
        self.assertEqual(ins[0][1], 1)
        self.assertIn("One by one 0", ins[0][2])  # oldest first
        el = ".lp[data-panel=fulltext]"
        self.assertEqual(page.get_attribute(el, "data-queued"), "2")  # the rest wait their turn, one at a time
        self.assertTrue(300 <= int(page.get_attribute(el, "data-gap")) <= 60000, page.get_attribute(el, "data-gap"))
        self.assertEqual(page.evaluate("document.getAnimations().length"), 0)
        moving = page.evaluate("""[...document.querySelectorAll('.live *')].filter(e => { const s = getComputedStyle(e);
            return (s.transitionDuration !== '0s' && s.transitionProperty !== 'none') || s.animationName !== 'none'; }).length""")
        self.assertEqual(moving, 0)
        ctx.close()

    def test_5_stale(self):
        last_poll(20)
        hhmm = (datetime.now(HKT) - timedelta(minutes=20)).strftime("%H:%M")  # when it was set: a slow page load is minutes
        try:
            ctx, page = open_page(self.br, "/app/live?half=right", 1920, 1080)
            page.wait_for_selector(".live-stale .live-stale-note", timeout=15000)
            note = page.inner_text(".live-stale-note")
            self.assertIn("No data since " + hhmm, note)
            self.assertIn("grayscale", page.evaluate("getComputedStyle(document.querySelector('.lp')).filter"))
            page.screenshot(path=os.path.join(SHOTS, "stale-right-1920x1080.png"))
            ctx.close()
        finally:
            last_poll(1)
        got = node("import { stale, STALE_MS } from './src/lib/liveLayout.ts';"
                   "const t = Date.parse('2026-10-21T02:00:00Z');"
                   "console.log(JSON.stringify([stale(t, t - 1000, t - 60e3), stale(t, t - STALE_MS - 1, t - 60e3), stale(t, t - 1000, t - 13 * 60e3)]))")
        self.assertIsNone(got[0])
        self.assertEqual(got[1]["since"], int(datetime.fromisoformat("2026-10-21T02:00:00+00:00").timestamp() * 1000) - 30001)
        self.assertIn("poller", got[2]["why"])
        self.assertEqual(node("import { hhmm } from './src/lib/liveLayout.ts'; console.log(JSON.stringify(hhmm(Date.parse('2026-10-21T02:05:00Z'))))"), "10:05")
        # pacing: cards go on screen as fast as they arrive (the decaying rate), not 10 at once then a minute of nothing
        pace = node("import { newPace, enqueue, step, behind, gapAt, MIN_GAP, MAX_WAIT } from './src/lib/liveLayout.ts';"
                    "function run(seed, per, T) { let p = newPace([{ id: 5, t: 0 }], seed); const ins = []; const at = {}; let id = 5;"
                    "  for (let t = 0; t <= T; t += 50) {"
                    "    if (t % 60000 === 0) { const cs = Array.from({ length: per(t) }, () => ({ id: ++id, t: 0 })); cs.forEach(c => at[c.id] = t); p = enqueue(p, cs, t); }"
                    "    const [c, q] = step(p, t); p = q; if (c) ins.push([c.id, t, t - at[c.id]]); }"
                    "  return ins; }"
                    "let o = newPace([]); o = enqueue(o, [{ id: 8, t: 0 }, { id: 6, t: 0 }], 0);"
                    "let b = newPace([]); b = enqueue(b, Array.from({ length: 500 }, (_, i) => ({ id: i + 1, t: 0 })), 0);"
                    "console.log(JSON.stringify({ steady: run(100, () => 10, 1200000), faster: run(100, t => t < 600000 ? 10 : 20, 1800000),"
                    "  order: [step(o, 0)[0].id], behind: behind(b), bgap: gapAt(b, 0), min: MIN_GAP, maxWait: MAX_WAIT }))")
        def avg_gap(ins, after):
            ts = [x[1] for x in ins if x[1] > after]
            return sum(b - a for a, b in zip(ts, ts[1:])) / (len(ts) - 1)
        for ins in (pace["steady"], pace["faster"]):
            self.assertEqual([x[0] for x in ins], sorted(x[0] for x in ins))  # oldest first, none skipped
            self.assertLessEqual(max(x[2] for x in ins), pace["maxWait"])  # none waits over MAX_WAIT
        self.assertAlmostEqual(avg_gap(pace["steady"], 600000), 6000, delta=600)  # 10 a minute: one every 6 s
        self.assertAlmostEqual(avg_gap(pace["faster"], 1200000), 3000, delta=300)  # 20 a minute: one every 3 s
        gaps = [b[1] - a[1] for a, b in zip(pace["steady"], pace["steady"][1:])][20:]
        self.assertLess(max(gaps), 15000, gaps)  # no minute-long pause between clumps
        self.assertGreater(min(gaps), 3000, gaps)  # and no burst of ten at once
        self.assertEqual(pace["order"], [6])
        self.assertEqual(pace["behind"], 100)  # 500 queued, 400 fit in MAX_WAIT at MIN_GAP
        self.assertEqual(pace["bgap"], pace["min"])  # never under MIN_GAP

    def test_6_fullscreen(self):
        ctx, page = open_page(self.br, "/app/live?half=left", 1920, 1200)
        page.keyboard.press("f")
        page.wait_for_function("!!document.fullscreenElement", timeout=5000)
        page.keyboard.press("f")
        page.wait_for_function("!document.fullscreenElement", timeout=5000)
        ctx.close()

    def test_7_systems_lines_fit(self):
        """Deyao, 2026-11-17 ("add aws usage tracking …, make sure it doesn't overflow"; then "it doesn't mean you should
        have a link to show there's more text!!! it should just fit"): with production's eleven providers, in every
        layout every Systems line is shown (each provider, Postgres, WiseSearch, object storage; no "more lines" link),
        none ends below the box or past its right edge, no part of a line but the last is cut, and AWS's figures are
        whole (no ellipsis) wherever they are shown"""
        names = ("OpenRouter", "TikHub", "IPRoyal", "AnyAPI", "AWS", "CapSolver", "2Captcha", "Hetzner Cloud", "Cloudflare", "Exa")
        for path, w, h in (("/app/live?half=right", 1920, 1080), ("/app/live", 1440, 830), ("/app/live", 390, 844)):
            ctx, page = open_page(self.br, path, w, h)
            page.wait_for_selector(".lp[data-panel=sys] [data-provider=aws]", timeout=20000)
            got = page.evaluate("""() => {
                const p = document.querySelector('.lp[data-panel=sys]'), st = p.querySelector('.st');
                const box = p.getBoundingClientRect(), out = { below: [], cut: [], aws: [], spill: st.scrollHeight - st.clientHeight,
                                                              cols: st.querySelectorAll(':scope > .col').length || 1 };
                for (const col of (st.querySelector(':scope > .col') ? st.querySelectorAll(':scope > .col') : [st])) {
                    let aws = false;
                    for (const r of col.children) {
                        const rr = r.getBoundingClientRect(), text = r.innerText.replace(/\\s+/g, ' ').slice(0, 60);
                        if (rr.bottom > box.bottom + 0.5 || rr.right > box.right + 0.5) out.below.push(text);
                        if (!r.classList.contains('row')) { aws = false; continue; }
                        const kids = [...r.children], named = r.querySelector('.pn, .ind');
                        // every part but the last is fixed: it must end inside the row; the last one shrinks with an ellipsis
                        kids.slice(0, -1).forEach(k => { if (k.getBoundingClientRect().right > rr.right + 0.5) out.cut.push(text); });
                        const cell = r.querySelector('.pc [data-provider=aws]');  // the compact form: AWS's cell
                        if (cell) {
                            const c = cell.parentElement;
                            out.aws.push({ text: c.innerText, whole: c.scrollWidth <= c.clientWidth && c.getBoundingClientRect().right <= rr.right + 0.5 });
                            continue;
                        }
                        if (named && named.textContent) aws = named.dataset.provider === 'aws';
                        else if (!named) aws = false;
                        if (aws) {
                            const fig = named.textContent ? kids[1] : kids[kids.length - 1];
                            out.aws.push({ text, whole: fig.scrollWidth <= fig.clientWidth && fig.getBoundingClientRect().right <= rr.right + 0.5 });
                        }
                    }
                }
                return out;
            }""")
            text = page.inner_text(".lp[data-panel=sys]")
            page.screenshot(path=os.path.join(SHOTS, f"systems-aws-{w}x{h}.png"), full_page=w == 390)
            self.assertEqual(got["below"], [], (w, h))
            self.assertLessEqual(got["spill"], 0, (w, h))
            self.assertEqual(got["cut"], [], (w, h))
            self.assertNotIn("more line", text, (w, h))
            self.assertEqual(page.locator(".lp[data-panel=sys] a").count(), 0, (w, h))
            for x in names + ("Postgres", "WiseSearch", "Object storage", "nodes", "CronJobs", "Deployments", "YYC AI pods", "gateway"):
                self.assertIn(x, text, (w, h, x))
            self.assertTrue(got["aws"] and got["aws"][0]["text"].startswith("AWS"), (w, h, got["aws"]))
            self.assertEqual([x["text"] for x in got["aws"] if not x["whole"]], [], (w, h))
            if w == 1920:  # the right display: every line in full (two columns), AWS's three among them
                self.assertEqual(len(got["aws"]), 3, got["aws"])
                for x in ("by service EC2 1,128.80", "running now 12 × m7i.xlarge", "yyc-extra-8", "longest open transaction"):
                    self.assertIn(x, text)
            ctx.close()


    def test_8_new_build_without_reload(self):
        """Deyao, 2026-11-17: "a pushed update can update the page (for example, an embedded frame) without causing a
        refresh on the browser (which makes the tab exit full screen)": /app/live is a shell with the dashboard in a frame;
        when /version names a newer build, a second frame loads it out of sight and replaces the first once it has drawn,
        the top page is never reloaded and stays full screen; F works from inside the frame; the shell holds the wake lock"""
        ctx = self.br.new_context(viewport={"width": 1920, "height": 1080})
        tok = ops.token(ops.DEYAO)
        ctx.route(ops.BASE + "/**", lambda r: r.continue_(headers={**r.request.headers, "cf-access-jwt-assertion": tok}))
        ctx.add_init_script("""window.__wake = 0; Object.defineProperty(navigator, 'wakeLock', { configurable: true,
            value: { request: () => { window.__wake++; return Promise.resolve({ release: () => Promise.resolve() }); } } });""")
        page = ctx.new_page()
        page.goto(ops.BASE + "/app/live?half=right", wait_until="domcontentloaded", timeout=180000)
        page.wait_for_selector(".live-shell iframe[data-frame='0'].on", timeout=60000)
        first = page.frame_locator("iframe[data-frame='0']")
        first.locator(".lp li:not(.dim)").first.wait_for(timeout=60000)
        self.assertEqual(first.locator(".live").first.get_attribute("data-mode"), "right")
        page.evaluate("window.__mark = 1")
        self.assertGreaterEqual(page.evaluate("window.__wake"), 1)
        page.keyboard.press("f")
        page.wait_for_function("!!document.fullscreenElement", timeout=5000)
        # a deploy: /version names another build
        page.route("**/version", lambda r: r.fulfill(status=200, body="a-newer-build", headers={"content-type": "text/plain"}))
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_selector(".live-shell iframe[data-frame='1'].on", state="attached", timeout=90000)
        page.wait_for_selector(".live-shell[data-frames='1']", state="attached", timeout=10000)
        page.frame_locator("iframe[data-frame='1']").locator(".lp li:not(.dim)").first.wait_for(timeout=30000)
        self.assertEqual(page.evaluate("window.__mark"), 1)  # the top page was not reloaded
        self.assertTrue(page.evaluate("!!document.fullscreenElement"))  # and is still full screen
        # F pressed inside the frame leaves full screen (the shell's)
        page.frame_locator("iframe[data-frame='1']").locator(".live").first.click(position={"x": 5, "y": 5})
        page.keyboard.press("f")
        page.wait_for_function("!document.fullscreenElement", timeout=5000)
        ctx.close()


@unittest.skipUnless(REAL, "set LIVE_PG to screenshot live data")
class Shots(unittest.TestCase):
    def test_shots(self):
        srv = Server(REAL)
        try:
            with sync_playwright() as pw:
                br = pw.chromium.launch()
                for path, w, h, name in (("/app/live?half=left", 1920, 1200, "left-1920x1200"),
                                         ("/app/live?half=right", 1920, 1080, "right-1920x1080"),
                                         ("/app/live", 1440, 830, "single-1440x830"), ("/app/live", 390, 844, "phone-390x844")):
                    ctx, page = open_page(br, path, w, h)
                    page.wait_for_timeout(8000)  # two more reads
                    for b in boxes(page):
                        self.assertTrue(b["x"] >= 0 and b["r"] <= w, b)
                    page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=w < 900)
                    ctx.close()
                br.close()
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
