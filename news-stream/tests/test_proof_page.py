#!/usr/bin/env python3
"""The proof view /app/proof (Deyao, 2026-10-20: "where i can just click arrow to see each surfaced database entry, so
i can check if they look correct"), rendered by `vite dev` against PROOF_DB (default yyc_dev_proof: a copy of real
catalogue posts plus this test's own fixture sources zz_proof_*), signed in with this test's own Access key
(tests/test_edge_ops.py). The flag's POST, which yyc-edge sends to the cluster, is handed to web.py in this process.

    PROOF_DB=yyc_dev_proof python3 tests/test_proof_page.py

1. Approved accounts only: /app/proof/data and /app/proof/entry answer 403 without an Access token, 200 with any
   approved account's; /app/monitor/flags is Deyao's (404 to anyone else). Without a token the page says so.
2. ?sid=a,b: the default view lists the entries that reached a stream, newest first ("1 / 2"); → and the keyboard's
   ArrowRight step to the next, ← back, the URL keeps the entry (?at=); the neighbour was read before the step (no
   request for it after); a swipe steps on a phone. The entry shows the source, sid, feed URL, title, author, the
   published and first-seen times, the URL with "Open the original", the full stored text, how it was fetched, the comment and link
   counts, Backfill, every stream's score with its threshold.
3. "Everything collected" lists every entry ("1 / 6"), and is remembered after a reload (localStorage); the counter and arrows
   follow it. An empty default view says how many were collected and offers the full view.
4. "Wrong" + a note (Deyao, 2026-10-21: notes and flags kept in the browser, downloaded as a file): the record is in
   localStorage yyc-proof-notes, nothing is sent, the page says so inline (a toast, no popup) and counts it "not
   downloaded"; the keyboard (test_keys): X, O, 1-9, N + Enter / Esc, J/K, V, M, Space, ?, D (the download's file and
   JSON), C + Y; at 1440 x 830, 1280 x 800 and 1710 x 1010 the entry fits with no page scroll and the text scrolls in
   its own panel. The monitor's Flags section links a proof.py flag to its entry; the test's flags are deleted after.
5. ?catalogue=1 walks every sites.json source: "Source i / n" and "Next source".
6. WebKit iPhone 390 px and Chromium 1600 px: no console error (hydration), no sideways scroll; screenshots in SHOTS.
7. The item page shows its one "Only visible to you (Deyao)" box (English, a link to the post in the proof view) to
   Deyao alone: not to a colleague's approved account, not signed out.
8. "Passed stage 1" (Deyao, 2026-10-22: "another tab that is everything that pass stage 1"): the same proof view over
   the entries stage 1 scored at or over streams.SCREEN for a stream, with its count in the tab; an entry waiting for
   stage 2 shows stage 1's score and reason and "pending", one stage 2 rejected says "rejected by stage 2" with both
   stages' scores and reasons; V steps through the three views. 390 and 1600 px, no sideways scroll, screenshots.
9. The stream view ?stream=WFC&above=10 (Deyao, 2026-10-22: "show me everything about wang fuk court that scored
   above 10"): the header says "Everything 宏福苑 scored above 10 (stage 1 or 2), from N sources", the first tab is
   "In 宏福苑"; the posts from several sources with a WFC score over 10 are in it, one at 10 is not; stepping and the
   tabs keep stream and above in the address; an unknown stream id says so. 390 px (WebKit) and 1600 px, no sideways
   scroll, screenshots.
The proof view is in English (Deyao's own tool; NAVIGATION.md "Languages"), its dates dtEn.
"""
import io, json, os, re, signal, subprocess, sys, time, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
DB = os.environ.get("PROOF_DB", "yyc_dev_proof")
assert DB != "yyc_dev_spa", "never write to the shared copy"
os.environ["EDGE_DB"] = DB
os.environ["EDGE_PORT"] = os.environ.get("PROOF_PORT", "5189")
sys.path.insert(0, os.path.join(HERE, "tests"))
subprocess.run(["createdb", DB], stderr=subprocess.DEVNULL)
import test_edge_ops as ops  # noqa: E402  (token, jwks, get, hydrated, no_sideways; DATABASE_URL = DB)
from playwright.sync_api import sync_playwright  # noqa: E402
import ai, app, corpus, edge_config, insights, proof, web  # noqa: E402

SHOTS = os.environ.get("SHOTS") or os.path.expanduser("~/artifacts/proof-view/test")
HKT = timezone(timedelta(hours=8))
SA, SB = "zz_proof_a", "zz_proof_b"
SW, SX = "zz_proof_w", "zz_proof_x"  # the stream view's (9)
COLLEAGUE = "colleague@yyc.hk"


def ago(h):
    return (datetime.now(HKT).replace(microsecond=0) - timedelta(hours=h)).isoformat()


def cut_off(text):
    """WebKit's report of a load of the app's own that a navigation cut off ("… due to access control checks.",
    its wording for a cross-origin refusal, which a same-origin load cannot get): the page's re-reads (the socket's
    invalidations, the version check) can be in flight at any reload or page change"""
    host = ops.BASE.split("//", 1)[1]
    return text.strip().endswith("due to access control checks.") and f"{host}/" in text


def only_data_update(text):
    """a hydration report whose only differing line is <html data-update="current">"""
    tree = text.split("hydration-mismatch", 1)[-1]
    diff = [re.sub(r"\s+", " ", l.strip()) for l in tree.splitlines() if re.match(r"^\s*[+-]\s", l)]
    return "hydration-mismatch" in text and diff == ['- data-update="current"']


def forward_flag(route, request):
    """yyc-edge sends POST /app/proof/flag to the cluster: here, web.py in this process, as the token's account"""
    import jwt
    tok = request.headers.get("cf-access-jwt-assertion")
    who = jwt.decode(tok, options={"verify_signature": False})["email"] if tok else None
    web.access_user = lambda h: who
    raw = (request.post_data or "{}").encode()
    env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/app/proof/flag", "QUERY_STRING": "", "RAW_URI": "/app/proof/flag",
           "HTTP_HOST": "newswatch.yyc-internal.com", "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)),
           "CONTENT_TYPE": "application/json"}
    got = {}
    body = b"".join(app.application(env, lambda s, h: got.update(status=int(s.split()[0]))))
    route.fulfill(status=got["status"], content_type="application/json", body=body)


class ProofPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        db = corpus.db()
        db.execute("DELETE FROM posts WHERE sid LIKE 'zz_proof_%%'")
        db.execute("SELECT setval(pg_get_serial_sequence('posts','id'), greatest(1, (SELECT max(id) FROM posts)))")
        corpus.kv_set("scoring", corpus.kv_get("scoring") or {"from_id": 0, "comments": False})
        items = [  # newest last in the list below: published h hours ago
            dict(url="https://zz.example/a1", title="甲一：最舊的帖子", h=6, sid=SA),
            dict(url="https://zz.example/a2", title="甲二：已收錄於串流", h=5, sid=SA),
            dict(url="https://zz.example/a3", title="甲三：未達門檻", h=4, sid=SA),
            dict(url="https://zz.example/a4", title="甲四：達到門檻", h=3, sid=SA),
            dict(url="https://zz.example/a5", title="甲五：未評分", h=2, sid=SA),
            dict(url="https://zz.example/b1", title="乙一：另一來源 " + "Averyveryverylongwordwithoutanyspaces" * 3, h=1, sid=SB),
        ]
        corpus.ingest([{"url": i["url"], "title": i["title"], "sid": i["sid"], "source": "測試來源" + i["sid"][-1].upper(),
                        "author": "記者甲", "published": ago(i["h"]), "source_type": "News", "region": "HK",
                        "text": f"{i['title']}的內文。\n第二段，連結 https://zz.example/ref/{i['h']} 。" * 3} for i in items])
        corpus.ingest([{"url": "https://zz.example/a4#c1", "title": "留言", "text": "一則留言", "sid": SA, "source": "測試來源A",
                        "thread": "https://zz.example/a4", "published": ago(2)}])
        # the catalogue view (?catalogue=1) steps through sites.json's sources: on a copy of real data they have posts;
        # on a database of its own (CI) the first three get one each
        import sites_fetch
        cat = [x["id"] for x in sites_fetch.load_read()]  # sources that are read (a region switched off may be left out)
        if not db.execute("SELECT 1 FROM posts WHERE sid = ANY(%s) LIMIT 1", (cat,)).fetchone():
            corpus.ingest([{"url": f"https://zz-catalogue.example/{sid}/1", "title": f"目錄來源 {sid} 的帖子", "sid": sid,
                            "source": sid, "published": ago(k + 1), "source_type": "News", "region": "HK",
                            "text": f"目錄來源 {sid} 的內文。" * 5} for k, sid in enumerate(cat[:3])])
        ids = corpus.ids_for_urls(i["url"] for i in items)
        cls.ids = [ids[i["url"]] for i in items]
        a1, a2, a3, a4, a5, b1 = cls.ids
        db.execute("UPDATE posts SET text_via='page', cached_at=%s WHERE id=%s", (ago(0), a4))
        # a4: a long text (it scrolls in its own panel at laptop sizes) and every stream's score stored, 0-19 too
        db.execute("UPDATE posts SET text = text || repeat(E'\\n長文段落：' || repeat('內容', 40), 50) WHERE id=%s", (a4,))
        st = "CEHK"
        cls.th = (corpus.kv_get("stream_thresholds") or {}).get(st) or 50
        db.execute("DELETE FROM stream_items WHERE url LIKE 'https://zz.example/%%'")
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                   (ago(5)[:10], st, items[1]["url"], corpus.J({"url": items[1]["url"], "title": items[1]["title"], "seen": ago(5), "stream": st})))
        for pid, sc in ((a3, cls.th - 10), (a4, cls.th + 20)):
            db.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at) VALUES (%s,%s,%s,%s,%s) "
                       "ON CONFLICT DO NOTHING", (st, pid, sc, f"測試理由 {sc}", ago(0)))
        db.execute("INSERT INTO scored (post_id, hash, sentiment, labels, at) SELECT id, 'x', 'neutral', '[]', %s FROM posts "
                   "WHERE id = ANY(%s) ON CONFLICT DO NOTHING", (ago(0), [a3, a4]))
        db.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at, model) VALUES ('HKIC', %s, 3, '無關', %s, %s) "
                   "ON CONFLICT DO NOTHING", (a4, ago(0), ai.MODEL))
        db.execute("UPDATE scored SET all_scores = TRUE WHERE post_id = %s", (a4,))
        # "Passed stage 1" (8): a pair waiting for stage 2 (a stage-1 event, no stage 2 yet) and a pair stage 2 rejected
        # 52 older rejected pairs of their own posts (a second page)
        import streams
        db.execute("DELETE FROM events WHERE post_id = ANY(%s)", (cls.ids,))
        db.execute("INSERT INTO events (kind, post_id, data) VALUES ('stage1', %s, %s)",
                   (b1, corpus.J({"model": ai.MODEL, "scores": {st: 72, "HKIC": 5}, "reasons": {st: "測試：等待第二關", "HKIC": "無關"}})))
        db.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at, model, screen, screen_reason) "
                   "VALUES ('FSHK', %s, 20, '第二關：其實無關', %s, %s, 55, '第一關：可能相關') ON CONFLICT DO NOTHING",
                   (a3, ago(0.2), streams.CONFIRM_MODEL))
        # the stream view (9): WFC scores on posts of two more sources, newer than the rest
        cls.wfc = []
        for k, (sid, sc, model) in enumerate(((SW, 60, None), (SX, 12, ai.MODEL), (SW, 10, ai.MODEL))):
            u = f"https://zz.example/w{k}"
            corpus.ingest([{"url": u, "title": f"宏福苑測試 {k}", "sid": sid, "source": "測試來源" + sid[-1].upper(),
                            "published": ago(0.3 - k * 0.1), "text": "宏福苑的內文。"}])
            pid = corpus.ids_for_urls([u])[u]
            cls.wfc.append(pid)
            db.execute("INSERT INTO scores (stream, post_id, score, reason, scored_at, model) VALUES ('WFC',%s,%s,'宏福苑理由',%s,%s) "
                       "ON CONFLICT DO NOTHING", (pid, sc, ago(0), model))
        old = corpus.kv_get("edge:config") or {}
        cfg = edge_config.build()
        cfg["sources"] = old.get("sources")
        corpus.kv_set("edge:config", cfg)
        proof.ensure()
        db.execute("DELETE FROM proof_flags WHERE url LIKE 'https://zz.example/%%'")
        # the dev server, verifying Access tokens against this test's key
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
                if ops.get(f"/app/proof/data?sid={SA}", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})[0] == 200:
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
            corpus.db().execute("DELETE FROM proof_flags WHERE url LIKE 'https://zz.example/%%'")
        except Exception:
            pass
        try:
            os.killpg(cls.dev.pid, signal.SIGTERM)
            cls.dev.wait(timeout=30)
        except Exception:
            pass
        cls.log.close()
        with open(cls.vars, "w") as f:
            f.write(cls.vars_before)

    # 1
    def test_access(self):
        a1 = self.ids[0]
        for path in (f"/app/proof/data?sid={SA}", f"/app/proof/entry?id={a1}"):
            self.assertEqual(ops.get(path)[0], 403, path)
            self.assertEqual(ops.get(path, {"Cf-Access-Jwt-Assertion": ops.token(COLLEAGUE, ops.ROGUE)})[0], 403, path)
            self.assertEqual(ops.get(path, {"Cf-Access-Jwt-Assertion": ops.token(COLLEAGUE)})[0], 200, path)
        d = json.loads(ops.get(f"/app/proof/data?sid={SA},bad%20sid!,{SB}", {"Cf-Access-Jwt-Assertion": ops.token(COLLEAGUE)})[2])
        self.assertEqual(d["sids"], [SA, SB])
        self.assertEqual(d["counts"], {"all": 6, "stream": 2, "stage1": 3})  # a3, a4 (stored), b1 (waiting for stage 2)
        self.assertEqual(ops.get("/app/monitor/flags", {"Cf-Access-Jwt-Assertion": ops.token(COLLEAGUE)})[0], 404)
        self.assertEqual(ops.get("/app/monitor/flags", {"Cf-Access-Jwt-Assertion": ops.token(ops.DEYAO)})[0], 200)
        self.assertEqual(ops.get("/app/proof/entry?id=999999999", {"Cf-Access-Jwt-Assertion": ops.token(COLLEAGUE)})[0], 404)

    def browsers(self, pw):
        return (("webkit-390", pw.webkit, dict(pw.devices["iPhone 13"], viewport={"width": 390, "height": 844})),
                ("chromium-1600", pw.chromium, dict(viewport={"width": 1600, "height": 1000})))

    def open(self, b, ctx_args, email=ops.DEYAO):
        ctx = b.new_context(**ctx_args, timezone_id="Asia/Hong_Kong",
                            extra_http_headers={"Cf-Access-Jwt-Assertion": ops.token(email)} if email else {})
        ctx.route(re.compile(r"^https://fonts\."), lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        ctx.route(re.compile(r"/app/proof/flag$"), forward_flag)
        p = ctx.new_page()
        p.errors = []
        # (React's dev build also reports <html data-update="current">, which lib/update.ts sets before hydration on
        # every page: not this page's, and production React doesn't check attributes)
        p.on("console", lambda m: m.type == "error" and not only_data_update(m.text) and not cut_off(m.text)
             and p.errors.append(m.text))
        p.on("pageerror", lambda e: not cut_off(str(e)) and p.errors.append(str(e)))
        p.entry_reqs = []
        p.on("request", lambda r: "/app/proof/entry" in r.url and p.entry_reqs.append(int(r.url.rsplit("=", 1)[1])))
        return ctx, p

    def count(self, p):
        return p.inner_text(".steps .count").strip()

    def wait_entry(self, p, pid):
        p.wait_for_selector(f"article.entry[data-id='{pid}']", timeout=60000)

    # 2, 3, 4, 6
    def test_walk(self):
        a1, a2, a3, a4, a5, b1 = self.ids
        with sync_playwright() as pw:
            for name, launch, ctx_args in self.browsers(pw):
                b = launch.launch()
                ctx, p = self.open(b, ctx_args)
                p.goto(ops.BASE + f"/app/proof?sid={SA},{SB}", wait_until="networkidle", timeout=180000)
                ops.hydrated(p, "#next")
                p.evaluate("localStorage.removeItem('yyc-proof-view')")
                p.reload(wait_until="networkidle")
                ops.hydrated(p, "#next")
                # 2: the default view, newest first
                self.wait_entry(p, a4)
                self.assertEqual(self.count(p), "1 / 2", name)
                self.assertIn(f"at={a4}", p.url)
                art = p.inner_text("article.entry")
                for s in ("甲四：達到門檻", SA, "記者甲", "Published", "first seen", "https://zz.example/a4", "Open the original", "第二段",
                          "Comments, replies", "Links in the text", "Backfill", "CEHK", "Threshold", "Stage 1", "Stage 2", "passed",
                          f"測試理由 {self.th + 20}", "Wrong", "Correct", "Note"):
                    self.assertIn(s, art, (name, s))
                self.assertEqual(p.locator("article.entry .state dt", has_text="Comments, replies").locator("xpath=following-sibling::dd[1]").inner_text().strip(), "1")
                self.assertNotRegex(p.inner_text("article.entry .state"), r"[\u4e00-\u9fff]", name)  # English labels
                self.assertTrue(p.locator("#prev").is_disabled())
                # the neighbour is read before the step
                deadline = time.time() + 20
                while a2 not in p.entry_reqs and time.time() < deadline:
                    p.wait_for_timeout(200)
                self.assertIn(a2, p.entry_reqs, name)
                before = len(p.entry_reqs)
                p.click("#next")
                self.wait_entry(p, a2)
                self.assertEqual(self.count(p), "2 / 2", name)
                self.assertIn(f"at={a2}", p.url)
                self.assertNotIn(a2, p.entry_reqs[before:], name)
                self.assertTrue(p.locator("#next").is_disabled())
                p.keyboard.press("ArrowLeft")
                self.wait_entry(p, a4)
                p.keyboard.press("ArrowRight")
                self.wait_entry(p, a2)
                p.click("#prev")
                self.wait_entry(p, a4)
                # 3: every entry, remembered
                p.click("[data-view=all]")
                self.wait_entry(p, a4)
                self.assertEqual(self.count(p), "3 / 6", name)  # b1, a5, a4, …: the shown entry stays
                p.reload(wait_until="networkidle")
                ops.hydrated(p, "#next")
                p.wait_for_function("document.querySelector('.steps .count').textContent.trim() === '3 / 6'", timeout=30000)
                self.assertEqual(p.get_attribute("[data-view=all]", "aria-pressed"), "true")
                p.click("#prev"); self.wait_entry(p, a5)
                p.click("#prev"); self.wait_entry(p, b1)
                self.assertIn("Not scored", p.inner_text("article.entry"))
                self.assertTrue(ops.no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"proof-all-{name}.png"), full_page=True)
                # 4: a flag and a note, kept in this browser (no request leaves the page for them)
                posts_out = []
                p.on("request", lambda r: r.method != "GET" and posts_out.append(r.url))
                p.click(".flag-open")
                p.wait_for_selector(".flag-open[aria-pressed=true]", timeout=30000)
                p.click(".note-open")
                p.fill("#flag-note", f"測試：標題不對（{name}）")
                p.click(".flag-send")
                p.wait_for_selector(f".note-text:has-text('{name}')", timeout=10000)
                self.assertIn("flagged as wrong", p.inner_text(".flag-msg"))
                rec = p.evaluate(f"JSON.parse(localStorage.getItem('yyc-proof-notes'))['{b1}']")
                self.assertEqual((rec["verdict"], rec["url"], rec["sid"]), ("wrong", "https://zz.example/b1", SB))
                self.assertIn(name, rec["note"])
                self.assertEqual(posts_out, [], name)
                self.assertIn("1 not downloaded", p.inner_text(".pend"))
                p.click("[data-view=stream]")
                self.wait_entry(p, a4)
                self.assertEqual(self.count(p), "1 / 2", name)
                p.screenshot(path=os.path.join(SHOTS, f"proof-stream-{name}.png"), full_page=True)
                # the monitor's Flags section (flags written by proof.py)
                proof.add(b1, f"測試：標題不對（{name}）", ops.DEYAO)
                p.goto(ops.BASE + "/app/monitor", wait_until="networkidle", timeout=180000)
                p.wait_for_selector(f"#flag-list .item:has-text('{name}')", timeout=60000)
                link = p.locator(f"#flag-list .item:has-text('{name}') a").first.get_attribute("href")
                self.assertEqual(link, f"/app/proof?sid={SB}&at={b1}")
                p.locator("#flags").screenshot(path=os.path.join(SHOTS, f"monitor-flags-{name}.png"))
                self.assertEqual(p.errors, [], name)
                ctx.close()
                b.close()
        for x in proof.flags(open_only=False):
            if x["url"].startswith("https://zz.example/"):
                proof.delete(x["id"])

    def test_swipe_and_empty(self):
        a1, a2, a3, a4, a5, b1 = self.ids
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            ctx, p = self.open(b, dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True))
            p.goto(ops.BASE + f"/app/proof?sid={SA}", wait_until="networkidle", timeout=180000)
            ops.hydrated(p, "#next")
            p.evaluate("localStorage.removeItem('yyc-proof-view')")
            p.reload(wait_until="networkidle"); ops.hydrated(p, "#next")
            self.wait_entry(p, a4)
            cdp = ctx.new_cdp_session(p)
            def swipe(x0, x1, y=400):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y}]})
                for k in range(1, 6):
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x0 + (x1 - x0) * k / 5, "y": y}]})
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            swipe(330, 60)
            self.wait_entry(p, a2)
            swipe(60, 330)
            self.wait_entry(p, a4)
            # an empty default view says what was collected and offers the full view
            p.goto(ops.BASE + f"/app/proof?sid={SB}", wait_until="networkidle")
            p.wait_for_selector(".card.empty", timeout=60000)
            self.assertIn("1 post collected from these sources; none reached a stream", p.inner_text(".card.empty"))
            ops.hydrated(p, ".card.empty button")
            p.click(".card.empty button")
            self.wait_entry(p, b1)
            self.assertEqual(self.count(p), "1 / 1")
            # not signed in: the page says so (Cloudflare Access is in front of /app on the real site)
            ctx2, p2 = self.open(b, dict(viewport={"width": 390, "height": 844}), email=None)
            p2.goto(ops.BASE + f"/app/proof?sid={SA}", wait_until="networkidle")
            self.assertIn("Only for accounts Deyao has approved", p2.inner_text("main"))
            self.assertEqual(p.errors + p2.errors, [])
            b.close()

    # 7: the item page's Deyao-only box (lib/DeyaoOnly.tsx; NAVIGATION.md "Languages")
    def test_deyao_only_box(self):
        a4 = self.ids[3]
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for email, shown in ((ops.DEYAO, True), (COLLEAGUE, False), (None, False)):
                ctx, p = self.open(b, dict(viewport={"width": 390, "height": 844}), email=email)
                p.goto(ops.BASE + f"/item/{a4}", wait_until="networkidle", timeout=180000)
                ops.hydrated(p, "main")
                if shown:
                    p.wait_for_selector(".deyao-only", timeout=30000)
                    box = p.locator(".deyao-only")
                    self.assertEqual(box.count(), 1)  # one box per page
                    self.assertIn("Only visible to you (Deyao)", box.inner_text())
                    self.assertEqual(box.get_attribute("lang"), "en")
                    self.assertEqual(box.locator("a", has_text="proof view").get_attribute("href"), f"/app/proof?sid={SA}&at={a4}")
                    self.assertTrue(ops.no_sideways(p))
                    box.screenshot(path=os.path.join(SHOTS, "item-deyao-only-390.png"))
                else:
                    p.wait_for_timeout(3000)  # /app/me has answered
                    self.assertEqual(p.locator(".deyao-only").count(), 0, email)
                self.assertEqual(p.errors, [], email)
                ctx.close()
            b.close()

    # 5
    def test_keys(self):
        """the whole check by keyboard (Deyao, 2026-10-21), and the laptop layout: one entry, no page scroll"""
        a1, a2, a3, a4, a5, b1 = self.ids
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for w, h in ((1440, 830), (1280, 800), (1710, 1010)):
                ctx, p = self.open(b, dict(viewport={"width": w, "height": h}, accept_downloads=True))
                p.goto(ops.BASE + f"/app/proof?sid={SA},{SB}&at={a4}", wait_until="networkidle", timeout=180000)
                ops.hydrated(p, "#next")
                p.evaluate("localStorage.removeItem('yyc-proof-notes'); localStorage.removeItem('yyc-proof-view')")
                p.reload(wait_until="networkidle"); ops.hydrated(p, "#next")
                self.wait_entry(p, a4)
                fit = p.evaluate("[document.documentElement.scrollHeight, innerHeight, document.documentElement.scrollWidth, innerWidth]")
                self.assertLessEqual(fit[0], fit[1] + 1, (w, h, "page scrolls"))
                self.assertLessEqual(fit[2], fit[3], (w, h, "sideways"))
                panel = p.evaluate("(() => { const t = document.querySelector('.textpanel'); return [t.scrollHeight, t.clientHeight]; })()")
                self.assertGreater(panel[0], panel[1], "the long text scrolls in its own panel")
                self.assertTrue(p.locator(".textcard.over").count(), "the fade at the text's foot")
                low = p.inner_text("[data-low]")
                self.assertRegex(low, r"HKIC 3\b", "the exact score under 20")
                for sel in (".scores .sgrid", ".answers", ".flag .verdict", ".steps"):  # never cut: inside the window
                    bb = p.locator(sel).bounding_box()
                    self.assertLessEqual(bb["y"] + bb["height"], h, (w, h, sel))
                p.screenshot(path=os.path.join(SHOTS, f"proof-fit-{w}x{h}.png"))
                if (w, h) != (1440, 830):
                    ctx.close(); continue
                k = p.keyboard
                # Space scrolls the text panel, not the page
                k.press("Space"); p.wait_for_timeout(600)
                self.assertGreater(p.evaluate("document.querySelector('.textpanel').scrollTop"), 0)
                self.assertEqual(p.evaluate("scrollY"), 0)
                k.press("m"); p.wait_for_selector("article.entry.more"); k.press("m"); p.wait_for_selector("article.entry:not(.more)")
                self.assertTrue(p.locator(".post .links li").first.is_visible())  # the links: shown without a click
                k.press("l"); p.wait_for_selector(".post .links.all"); k.press("l"); p.wait_for_selector(".post .links:not(.all)")
                self.assertTrue(p.locator(".answers").is_visible())
                k.press("x"); p.wait_for_selector(".toast:has-text('Flagged as wrong')")
                k.press("o"); p.wait_for_selector(".toast:has-text('Marked correct')")
                k.press("2"); p.wait_for_selector(".toast:has-text('score flagged as wrong')")
                k.press("n"); p.wait_for_selector("#flag-note:focus")
                k.type("j and k typed here stay text"); k.press("Escape")
                p.wait_for_selector("#flag-note", state="detached")
                k.press("n"); k.type("測試筆記"); k.press("Shift+Enter"); k.type("第二行"); k.press("Enter")
                p.wait_for_selector(".note-text:has-text('第二行')")
                rec = p.evaluate(f"JSON.parse(localStorage.getItem('yyc-proof-notes'))['{a4}']")
                # a stream's score flagged wrong takes the "correct" mark off
                self.assertEqual((rec["verdict"], rec["note"]), (None, "測試筆記\n第二行"))
                self.assertEqual(len(rec["wrong_streams"]), 1)
                self.assertTrue(any(x["stream"] == "HKIC" and x["score"] == 3 for x in rec["scores"]))
                self.assertIn("1 not downloaded", p.inner_text(".pend"))
                with p.expect_download() as d:
                    k.press("d")
                dl = d.value
                self.assertRegex(dl.suggested_filename, r"^yyc-proof-notes-\d{4}-\d{2}-\d{2}-\d{4}\.json$")
                got = json.loads(open(dl.path(), encoding="utf-8").read())
                self.assertEqual([r["id"] for r in got["records"]], [a4])
                self.assertEqual(got["records"][0]["note"], "測試筆記\n第二行")
                self.assertIn("all downloaded", p.inner_text(".pend"))
                # steps, view, help
                k.press("j"); self.wait_entry(p, a2)
                k.press("k"); self.wait_entry(p, a4)
                for v in ("stage1", "all", "stream"):  # V steps through the three views
                    k.press("v"); p.wait_for_function(f"document.querySelector('[data-view={v}]').getAttribute('aria-pressed') === 'true'")
                k.press("?"); p.wait_for_selector(".keys-overlay"); self.assertIn("full screen", p.inner_text(".keys-overlay"))
                k.press("Escape"); p.wait_for_selector(".keys-overlay", state="detached")
                k.press("Control+d")  # the browser's own shortcut: not ours
                # clear: asks first, in the page
                k.press("c"); p.wait_for_selector(".confirm"); k.press("Escape"); p.wait_for_selector(".confirm", state="detached")
                self.assertIsNotNone(p.evaluate(f"JSON.parse(localStorage.getItem('yyc-proof-notes'))['{a4}']"))
                k.press("c"); k.press("y"); p.wait_for_selector(".toast:has-text('cleared')")
                self.assertEqual(p.evaluate("localStorage.getItem('yyc-proof-notes')"), "{}")
                # F: the Fullscreen API is asked (headless may refuse it: then the page says so inline)
                k.press("f"); p.wait_for_timeout(500)
                self.assertTrue(p.evaluate("!!document.fullscreenElement") or p.locator(".toast:has-text('Full screen')").count() > 0)
                self.assertEqual(p.errors, [])
                ctx.close()
            b.close()

    def test_catalogue(self):
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            ctx, p = self.open(b, dict(viewport={"width": 1600, "height": 1000}))
            p.goto(ops.BASE + "/app/proof?catalogue=1", wait_until="networkidle", timeout=180000)
            ops.hydrated(p, "#next")
            p.click("[data-view=all]")
            p.wait_for_selector("article.entry", timeout=60000)
            n = len(json.load(open(os.path.join(HERE, "sites.json"))))
            self.assertIn(f"Every catalogue source ({n})", p.inner_text(".head"))
            row = p.inner_text(".srcrow")
            self.assertRegex(row, rf"Source \d+ / {n}")
            sid0 = p.get_attribute("article.entry", "data-id")
            nxt = p.locator(".srcbtns button", has_text="Next source")
            if nxt.is_enabled():
                nxt.click()
                p.wait_for_function(f"document.querySelector('article.entry') && document.querySelector('article.entry').dataset.id !== '{sid0}'", timeout=30000)
                self.assertNotEqual(p.inner_text(".srcrow"), row)
            p.screenshot(path=os.path.join(SHOTS, "proof-catalogue-chromium-1600.png"), full_page=True)
            self.assertEqual(p.errors, [])
            b.close()

    # 8
    def test_stage1(self):
        a1, a2, a3, a4, a5, b1 = self.ids
        with sync_playwright() as pw:
            for name, launch, ctx_args in self.browsers(pw):
                b = launch.launch()
                ctx, p = self.open(b, ctx_args)
                p.goto(ops.BASE + f"/app/proof?sid={SA},{SB}", wait_until="networkidle", timeout=180000)
                ops.hydrated(p, "#next")
                self.assertEqual(p.inner_text("[data-view=stage1]").split(), ["Passed", "stage", "1", "3"])
                p.click("[data-view=stage1]")
                # newest first: b1 (waiting for stage 2), a4, a3; the same proof view, the same steps (the entry shown stays)
                self.wait_entry(p, a4)
                self.assertEqual(self.count(p), "2 / 3", name)
                p.click("#prev"); self.wait_entry(p, b1)
                self.assertEqual(self.count(p), "1 / 3", name)
                row = p.locator(".scores .sc[data-stream=CEHK]")
                self.assertEqual(row.get_attribute("data-outcome"), "pending")
                t = row.inner_text()
                for x in ("72", "pending", "waiting for stage 2", "測試：等待第二關", str(self.th)):
                    self.assertIn(x, t, (name, x))
                self.assertEqual(p.locator(".scores .sc[data-stream=HKIC]").count(), 0)  # stage 1 under 20: the low line
                self.assertTrue(ops.no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"proof-stage1-pending-{name}.png"), full_page=name.startswith("webkit"))
                p.click("#next"); self.wait_entry(p, a4)
                p.click("#next"); self.wait_entry(p, a3)
                self.assertEqual(self.count(p), "3 / 3", name)
                row = p.locator(".scores .sc[data-stream=FSHK]")
                self.assertEqual(row.get_attribute("data-outcome"), "rejected by stage 2")
                t = row.inner_text()
                for x in ("55", "20", "rejected by stage 2", "第二關：其實無關", "第一關：可能相關"):
                    self.assertIn(x, t, (name, x))
                p.screenshot(path=os.path.join(SHOTS, f"proof-stage1-rejected-{name}.png"), full_page=name.startswith("webkit"))
                # remembered like the other views
                p.reload(wait_until="networkidle"); ops.hydrated(p, "#next")
                p.wait_for_function("document.querySelector('[data-view=stage1]').getAttribute('aria-pressed') === 'true'", timeout=30000)
                self.wait_entry(p, a3)
                self.assertEqual(p.errors, [], name)
                p.evaluate("localStorage.removeItem('yyc-proof-view')")
                ctx.close()
                b.close()

    # 9
    def test_stream_view(self):
        w0, w1, w2 = self.wfc  # WFC 60 (at its threshold), 12 (stage 1), 10 (not over 10); w2 is the newest
        d = json.loads(ops.get("/app/proof/data?stream=WFC&above=10", {"Cf-Access-Jwt-Assertion": ops.token(COLLEAGUE)})[2])
        got = [e["id"] for e in d["entries"]]
        self.assertEqual([x for x in got if x in self.wfc], [w1, w0])
        self.assertTrue({SW, SX} <= {s["sid"] for s in d["sources"]})
        with sync_playwright() as pw:
            for name, launch, ctx_args in self.browsers(pw):
                b = launch.launch()
                ctx, p = self.open(b, ctx_args)
                p.goto(ops.BASE + f"/app/proof?stream=WFC&above=10&at={w0}", wait_until="networkidle", timeout=180000)
                ops.hydrated(p, "#next")
                p.evaluate("localStorage.removeItem('yyc-proof-view')")
                p.reload(wait_until="networkidle"); ops.hydrated(p, "#next")
                self.wait_entry(p, w0)
                head = p.inner_text(".head")
                self.assertRegex(head, r"Everything 宏福苑 scored above 10 \(stage 1 or 2\), from \d+ sources?", name)
                self.assertEqual(p.inner_text("[data-view=stream]").split()[:2], ["In", "宏福苑"], name)
                self.assertIn("宏福苑理由", p.inner_text("article.entry"))
                self.assertTrue(ops.no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"proof-wfc-stream-{name}.png"), full_page=name.startswith("webkit"))
                # every post in the set: w1 (WFC 12) is the newest, w0 next; stepping keeps the stream view's address
                p.click("[data-view=all]")
                self.wait_entry(p, w0)
                p.click("#prev"); self.wait_entry(p, w1)
                self.assertEqual(self.count(p).split(" / ")[0], "1", name)
                for x in ("stream=WFC", "above=10", f"at={w1}"):
                    self.assertIn(x, p.url, (name, x))
                p.click("#next"); self.wait_entry(p, w0)
                self.assertIn("stream=WFC", p.url)
                self.assertTrue(ops.no_sideways(p), name)
                p.screenshot(path=os.path.join(SHOTS, f"proof-wfc-all-{name}.png"), full_page=name.startswith("webkit"))
                self.assertEqual(p.errors, [], name)
                p.evaluate("localStorage.removeItem('yyc-proof-view')")
                ctx.close()
                b.close()
            b = pw.chromium.launch()
            ctx, p = self.open(b, dict(viewport={"width": 390, "height": 844}))
            p.goto(ops.BASE + "/app/proof?stream=NOPE", wait_until="networkidle", timeout=180000)
            p.wait_for_selector("main .card", timeout=60000)
            self.assertIn("No scored stream has the id NOPE", p.inner_text("main"))
            self.assertEqual(p.errors, [])
            b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
