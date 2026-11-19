#!/usr/bin/env python3
"""The edge app's 串流 and item page (web-app/: -StreamPage.tsx, -ItemPage.tsx), against its dev server on a database
seeded here with the cases the pages must show (a post with a very long English outlet name, title, URL and commenter
name; 205 comments with the scoring divider; pictures; video clips in every state; an article mate; a filed item rescored
under its threshold; Galbot's market still being classified; near-empty posts, rejected and waiting).

    # the dev server on the same database (news-stream/web-app):
    CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=postgres://…/yyc_dev_x npx vite dev --port 5185
    WEB_APP_DATABASE_URL=postgresql:///yyc_dev_x WEB_APP_URL=http://127.0.0.1:5185 python3 tests/test_web_app_pages.py

Never on a database anyone else uses: the seed adds posts (and replaces its own on every run). The approved-account
parts (/app/*, behind Cloudflare Access in production) are read as an approved account: the browser sends an Access
token signed with the test's key (ACCESS_TEST_KEY, whose public half the dev server takes from .dev.vars: devserver.py).

WebKit (iPhone profile, 390 px) and Chromium (1600 px): no console errors (hydration), no sideways scroll, the stream
picker marks and switches streams with history entries, the day and the filters are URL params, the item page's
sections, and the old URLs (/?p=&d=, /item?url=&p= → 302 keeping p and the #hash, /post/<id> → 301 /item/<id>#text).
"""
import hashlib, json, os, sys, time, unittest, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
import devserver  # noqa: E402
devserver.ensure(5185)  # a dev server of the test's own when none is given (CI)
DB = os.environ.get("WEB_APP_DATABASE_URL") or sys.exit("WEB_APP_DATABASE_URL is not set (the dev server's database)")
BASE = os.environ.get("WEB_APP_URL", "http://127.0.0.1:5185")
os.environ["DATABASE_URL"] = DB
os.environ.setdefault("FLAGS_FILE", os.path.join(HERE, "deploy/apps/flags/flags.json"))
os.chdir(HERE)
import ai, corpus, datefmt, insights, streams  # noqa: E402
import jwt  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ai.model_name = lambda m: "Claude Haiku 5.5"  # no OpenRouter call

FX = "https://yyc-fixture.example/"
LONG_URL = (FX + "gia/general/202610/15/P2026101500614.htm?utm_source=newsletter&utm_medium=email"
            "&utm_campaign=ElectionCommitteeSubsectorOrdinaryElections2026NominationFormsReceivedToday")
DE_LONG = "Donaudampfschifffahrtsgesellschaftskapitänswitwe" * 3  # one unbreakable word wider than a phone
OUTLET = "Government press releases (English) of the Information Services Department, Hong Kong SAR Government"
PRESS = ("The Returning Officers for the various subsectors of the 2026 Election Committee Subsector Ordinary Elections "
         "received a total of 206 nomination forms today. ") * 12 + "\n\nFull list: " + LONG_URL
PIC = lambda fill: "data:image/svg+xml," + urllib.parse.quote(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="800" height="500" fill="{fill}"/></svg>')
FONTS = "".join(f'@font-face{{font-family:"Noto Sans HK";font-weight:{w};src:local("Noto Sans CJK HK{n}")}}'
                for w, n in ((400, ""), (500, ""), (700, " Bold")))


def seed():
    """the fixture posts (replacing an earlier run's) → {name: post id}"""
    db = corpus.db()
    for t in ("posts", "stream_items"):  # a copied database can have its identity sequences behind its rows
        db.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT max(id) FROM {t}))")
    db.execute("DELETE FROM stream_items WHERE url LIKE %s", (FX + "%",))
    db.execute("DELETE FROM posts WHERE url LIKE %s", (FX + "%",))
    db.execute("DELETE FROM media WHERE key LIKE %s", ("fixture:%",))
    db.execute("DELETE FROM images WHERE hash LIKE %s", ("fixture%",))
    now = insights.now()
    T = (now - timedelta(hours=2)).isoformat(timespec="minutes")  # not "fresh": no 處理中 for what never comes
    day = T[:10]
    main = {"url": LONG_URL, "title": "217 more nomination forms for Election Committee Subsector Ordinary Elections "
            "received today " + DE_LONG, "text": PRESS, "sid": "infogov_press_en", "source": OUTLET, "author": DE_LONG,
            "published": T}
    mate = {"url": FX + "mate", "title": "217 more nomination forms received (syndicated copy)", "text": PRESS,
            "sid": "rthk_en", "source": "RTHK English News", "published": T}
    thin = {"url": FX + "thin", "title": "👍", "text": "👍 #好", "sid": "rthk_en", "source": "RTHK English News", "published": T}
    wait = {"url": FX + "wait", "title": "📷", "text": "📷", "sid": "rthk_en", "source": "RTHK English News", "published": T}
    streams.settings()  # kv scoring (the poller's, made on its first run): posts after this moment are scored
    corpus.ingest([main, mate, thin, wait])
    comments = [{"id": f"k{i}", "author": ("A reader with a very long display name " + DE_LONG) if i == 3 else f"讀者{i}",
                 "text": ("See " + LONG_URL + " " + DE_LONG) if i == 3 else "支持" * (40 + i),
                 "published": (now - timedelta(hours=2) + timedelta(minutes=i)).isoformat(timespec="minutes"), "likes": i}
                for i in range(205)]
    corpus.add_comments(main, comments, total=231)
    ids = corpus.ids_for_urls([main["url"], mate["url"], thin["url"], wait["url"]])
    pid, mid = ids[main["url"]], ids[mate["url"]]
    cids = [r["id"] for r in db.execute("SELECT id FROM posts WHERE thread=%s ORDER BY id", (main["url"],)).fetchall()]
    db.execute("UPDATE posts SET website=%s, platform=%s, cached_at=%s, text_via='archive', text_via_url=%s, "
               "article_id=%s, engagement=%s WHERE id=%s",
               (OUTLET, OUTLET, T, "https://archive.ph/fixture", 900000000 + pid,
                json.dumps({"views": 12345, "likes": 67, "comments": 231}), pid))
    db.execute("UPDATE posts SET article_id=%s WHERE id=%s", (900000000 + pid, mid))
    db.execute("UPDATE posts SET seen=%s WHERE url LIKE %s OR thread LIKE %s", (T, FX + "%", FX + "%"))  # not fresh
    # scored with its first ten comments (the divider after the tenth); the streams' scores now
    db.execute(f"""INSERT INTO scored (post_id, hash, sentiment, labels, at, comments_upto)
                   SELECT id, {streams.HASH}, 'negative', '["issue","monitor"]', %s, %s FROM posts p WHERE id=%s""",
               (T, cids[9], pid))
    cats = next(s for s in insights.streams() if s["id"] == "CEHK")["categories"]
    cat = cats[0]
    for st, score in (("CEHK", 30), ("GALBOT", 80), ("HKIC", 75)):
        db.execute("INSERT INTO scores (stream, post_id, score, reason, category, scored_at) VALUES (%s,%s,%s,%s,%s,%s)",
                   (st, pid, score, "The post reports nomination forms received " + DE_LONG, cat if st == "CEHK" else None, T))
    # filed in CEHK at 70 (now 30: rescored under the threshold) and in HKIC at 60 (now 75: 收錄時 60 分，重新評分後 75 分)
    for st, filed in (("CEHK", 70), ("HKIC", 60)):
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)",
                   (day, st, main["url"], corpus.J(dict(main, stream=st, score=filed, reason="filed " + DE_LONG,
                    category=cat if st == "CEHK" else None, sentiment="negative", labels=["issue", "monitor"],
                    seen=T, slot=T, website=OUTLET, section=None, post_id=pid, relevant=True))))
    # pictures: one meaningful (with text read from it), one not, one not scored yet
    for i, (h, score) in enumerate((("fixture1", 82), ("fixture2", 12), ("fixture3", None))):
        url = PIC("#4a7" if i == 0 else "#a74" if i == 1 else "#47a")
        db.execute("INSERT INTO post_images (post_id, url, pos, hash) VALUES (%s,%s,%s,%s)", (pid, url, i, h))
        if score is not None:
            db.execute("INSERT INTO images (hash, url, width, height, score, reason, text, model, at) "
                       "VALUES (%s,%s,800,500,%s,%s,%s,'google/gemini-4-argon',%s)",
                       (h, url, score, "A table of the nomination forms by subsector", "Subsector  Forms\nTotal  206", T))
    # clips: a model's transcript, the uploader's captions, one in the queue, one given up
    for i, (status, model, tr, err) in enumerate((("done", "qwen/qwen3-asr-1.7b", "[0:00] 今日收到提名表格。\n[0:07] 共 206 份。", None),
                                                 ("done", "youtube-captions:zh-Hant", "[0:00] 字幕一行", None),
                                                 ("queued", None, None, None), ("failed", None, None, "HTTP 403 from the host"))):
        key = f"fixture:{i}"
        db.execute("INSERT INTO media (key, url, kind, host, status, duration, transcript, model, error, first_seen, done_at) "
                   "VALUES (%s,%s,'video','www.youtube.com',%s,%s,%s,%s,%s,%s,%s)",
                   (key, FX + f"clip{i}", status, 95.0 + i, tr, model, err, f"{T}:0{i}", T if status == "done" else None))
        db.execute("INSERT INTO post_media (post_id, key) VALUES (%s,%s)", (pid, key))
    # near-empty: rejected, and waiting for a transcript
    for name, st, waits in (("thin", "rejected", []), ("wait", "waiting", ["media"])):
        db.execute(f"""INSERT INTO thin_posts (post_id, hash, units, status, waits, first_at, check_at, at)
                       SELECT id, {streams.HASH}, 1, %s, %s, %s, %s, %s FROM posts p WHERE url=%s""",
                   (st, json.dumps(waits), T, T, T, FX + name))
    # 串流 under its day picker (Deyao, 2026-10-20): the day and the order are the notification's (when the item was
    # filed and sent to the stream's Telegram/WhatsApp, which follow stream_items.id), and rows show only the time.
    # In HKIC (no categories) and, for late and dateonly, in CEHK (categories: still one list in send order, the
    # category a label on the card; Deyao, 2026-10-20), filed in this id order after the main post:
    #   late      posted yesterday 23:40, filed today at T+10 min (stream_items.day = its publish day, yesterday)
    #   dateonly  the source gave only today's date, filed at T+20 min
    #   yday      posted and filed yesterday (22:00): only on yesterday's page
    CEHK_CAT = {"late": 1, "dateonly": 2}  # the main post is cats[0]: grouped by category, CEHK would read main, late, dateonly
    t0 = datetime.fromisoformat(T)
    yday = (t0 - timedelta(days=1)).strftime("%Y-%m-%d")
    extra = {
        "late": dict(published=f"{yday}T23:40:00+08:00", seen=(t0 + timedelta(minutes=10)).isoformat(timespec="seconds"), day=yday),
        "dateonly": dict(published=f"{day}T{T[11:16]}:00+08:00", seen=(t0 + timedelta(minutes=20)).isoformat(timespec="seconds"),
                         day=day, date_only=True),
        "yday": dict(published=f"{yday}T21:00:00+08:00", seen=f"{yday}T22:00:00+08:00", day=yday),
    }
    for name, x in extra.items():
        post = {"url": FX + name, "title": f"Fixture {name}: a post for the day picker", "text": PRESS[:400],
                "sid": "rthk_en", "source": "RTHK English News", "published": x["published"]}
        corpus.ingest([post])
        xid = corpus.ids_for_urls([post["url"]])[post["url"]]
        db.execute("UPDATE posts SET seen=%s, date_only=%s WHERE id=%s", (x["seen"], x.get("date_only"), xid))
        db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'HKIC', %s, %s)",
                   (x["day"], post["url"], corpus.J(dict(post, stream="HKIC", score=70, reason="filed", category=None,
                    sentiment="neutral", labels=[], seen=x["seen"], slot=x["seen"], website="RTHK English News",
                    section=None, post_id=xid, relevant=True))))
        if name in CEHK_CAT:  # CEHK too, under categories that sort the other way from the send order
            db.execute("INSERT INTO stream_items (day, stream, url, item) VALUES (%s, 'CEHK', %s, %s)",
                       (x["day"], post["url"], corpus.J(dict(post, stream="CEHK", score=70, reason="filed",
                        category=cats[CEHK_CAT[name]], sentiment="neutral", labels=[], seen=x["seen"], slot=x["seen"],
                        website="RTHK English News", section=None, post_id=xid, relevant=True))))
        extra[name]["id"] = xid
    if not corpus.kv_get("edge:config"):  # a database of its own (CI): the site's config, which the poller publishes
        import edge_config
        edge_config.publish()
    return {"main": pid, "mate": mid, "extra": extra, "yday": yday, "T": T, "comment": cids[3], "thin": ids[thin["url"]], "wait": ids[wait["url"]], "day": day,
            "main_url": main["url"]}


def access_token(email="chendeyao000@gmail.com"):
    """what Cloudflare Access sends for an approved account (access.ts: the main site's /app audience, the team's
    issuer), signed with the test's key"""
    now = int(time.time())
    return jwt.encode({"email": email, "aud": ["8b644dde1fadd76a241cace4428d57ebe8fccb6cdfe0af987e5f35bc7e31dd9b"],
                       "iss": "https://yyc-hk.cloudflareaccess.com", "iat": now, "exp": now + 3600},
                      open(os.environ["ACCESS_TEST_KEY"], "rb").read(), algorithm="RS256", headers={"kid": "test"})


OVERFLOW_JS = """() => {
  const W = document.documentElement.clientWidth, bad = [];
  const clipped = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement)
      if (getComputedStyle(p).overflowX !== "visible") return true; return false; };
  for (const el of document.body.querySelectorAll("*")) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === "hidden") continue;
    if ((r.right > W + 1 || r.left < -1) && !clipped(el)) bad.push(el.tagName + "." + el.className + " " + Math.round(r.right));
  }
  return {W, sw: document.documentElement.scrollWidth, bad: bad.slice(0, 8)};
}"""


class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ids = seed()
        cls.pw = sync_playwright().start()
        cls.chromium, cls.webkit = cls.pw.chromium.launch(), cls.pw.webkit.launch()
        cls.warm()

    @classmethod
    def tearDownClass(cls):
        cls.chromium.close(); cls.webkit.close(); cls.pw.stop()

    @classmethod
    def warm(cls):  # the dev server compiles on first use
        for path in ("/?p=CEHK", f"/item/{cls.ids['main']}"):
            urllib.request.urlopen(BASE + path, timeout=180).read()

    # ---- browsers ----
    def open(self, engine, approved=False):
        if engine == "webkit":
            ctx = self.webkit.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                          device_scale_factor=3)
        else:
            ctx = self.chromium.new_context(viewport={"width": 1600, "height": 1000})
        ctx.set_default_timeout(60000)
        if approved:
            ctx.set_extra_http_headers({"Cf-Access-Jwt-Assertion": access_token()})
        page = ctx.new_page()
        page.errors = []
        page.on("console", lambda m: m.type == "error" and page.errors.append(m.text[:300]))
        page.on("pageerror", lambda e: page.errors.append("pageerror: " + str(e)[:300]))
        return ctx, page

    def go(self, page, path):
        page.goto(BASE + path, wait_until="load", timeout=120000)
        page.add_style_tag(content=FONTS)
        # React has taken over the server-rendered page (the shell marks <html data-hydrated> in an effect)
        page.wait_for_function("() => document.documentElement.dataset.hydrated === '1'", timeout=120000)

    def no_sideways(self, page, what):
        r = page.evaluate(OVERFLOW_JS)
        self.assertLessEqual(r["sw"], r["W"], f"{what}: the page scrolls sideways {r}")
        self.assertEqual(r["bad"], [], f"{what}: something sticks out past the screen {r}")

    def clean(self, page, what):
        # (a 404 page's own status is one; so is WebKit's note when the page, left while its /live socket is still
        # connecting, closes it on pagehide: live.ts does that on purpose, for the back/forward cache)
        self.assertEqual([e for e in page.errors if "Failed to load resource" not in e
                          and "WebSocket is closed before the connection is established" not in e], [], f"{what}: console errors")

    # ---- 串流 ----
    def stream_checks(self, engine):
        ctx, page = self.open(engine)
        try:
            self.go(page, "/?p=CEHK")
            page.wait_for_selector(f'a.item[data-url="{LONG_URL}"]')
            self.no_sideways(page, f"串流 {engine}")
            self.clean(page, f"串流 {engine}")
            href = page.locator(f'a.item[data-url="{LONG_URL}"]').get_attribute("href")
            self.assertEqual(href, "/item?" + urllib.parse.urlencode({"p": "CEHK", "url": LONG_URL}),
                             "a card links /item?p=<stream>&url=<publisher url>")
            self.day_picker_rows(page, engine)
            # the picker: CEHK marked; picking HKIC is a new history entry, Back returns to CEHK
            pick = ".page-stream .side a.stream" if engine == "chromium" else ".yyc-streams a[data-p]"
            self.assertEqual(page.locator(f'{pick}[aria-current="page"]').get_attribute("data-p"), "CEHK")
            page.locator(f'{pick}[data-p="HKIC"]').click()
            page.wait_for_url("**/?p=HKIC")
            self.assertEqual(page.locator(f'{pick}[aria-current="page"]').get_attribute("data-p"), "HKIC")
            page.go_back()
            page.wait_for_url("**/?p=CEHK")
            self.assertEqual(page.locator(f'{pick}[aria-current="page"]').get_attribute("data-p"), "CEHK")
            # the day row: ‹ goes to the day before (?d=), 回到今日 back; the stream stays
            page.locator('button[aria-label="前一日"]').click()
            page.wait_for_url("**d=*")
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(page.url).query))
            self.assertEqual(q.get("p"), "CEHK")
            self.assertLess(q["d"], self.ids["day"])
            page.wait_for_selector(".days .today")
            page.locator(".days .today").click()
            page.wait_for_function("() => !location.search.includes('d=')")
            # filters are URL params
            if engine == "webkit":
                page.locator('button[aria-label="搜尋及篩選"]').click()
                chip = page.locator('.panel button.chip:has-text("負面")')
            else:
                chip = page.locator('.side button.chip:has-text("負面")')
            chip.click()
            page.wait_for_url("**sent=negative*")
            self.assertEqual(chip.get_attribute("aria-pressed"), "true")
            page.wait_for_selector(f'a.item[data-url="{LONG_URL}"]')
            self.assertTrue(page.locator(".status").inner_text().startswith("顯示 "))
            self.clean(page, f"串流 after clicks {engine}")
        finally:
            ctx.close()

    def day_picker_rows(self, page, engine):
        """under 串流's day picker: the notification's day and order, and only the time on each row"""
        x, i = self.ids["extra"], self.ids
        no_date = lambda texts, what: [self.assertNotRegex(t, r"[月日年]|星期", f"{what} {engine}: a date under the day picker") for t in texts]
        # CEHK (categories): the card's time is the time only
        no_date(page.locator("a.item .meta .num").all_inner_texts(), "串流 CEHK card")
        # CEHK is one list in send order like its groups, newest first, whatever the categories (Deyao, 2026-10-20):
        # no category headings; each card carries its category as a label, as the group message does
        urls = [u for u in page.eval_on_selector_all("a.item", "els => els.map(e => e.dataset.url)") if u.startswith(FX)]
        self.assertEqual(urls, [FX + "dateonly", FX + "late", LONG_URL], f"串流 CEHK order {engine}")
        self.assertEqual(page.locator(".slot.cat").count(), 0, f"串流 CEHK: no category headings {engine}")
        self.assertFalse([t for t in page.locator(".slot").all_inner_texts() if "（" in t], f"串流 CEHK: a heading with a count {engine}")
        cats = next(s for s in insights.streams() if s["id"] == "CEHK")["categories"]
        catl = lambda u: page.locator(f'a.item[data-url="{u}"] .cat').inner_text()
        self.assertEqual([catl(FX + "dateonly"), catl(FX + "late"), catl(LONG_URL)], [cats[2], cats[1], cats[0]],
                         f"串流 CEHK: each card's category label {engine}")
        # HKIC today: newest notification first = stream_items.id order reversed; late (posted yesterday) is today's
        self.go(page, "/?p=HKIC")
        page.wait_for_selector(f'a.item[data-url="{FX}dateonly"]')
        urls = [u for u in page.eval_on_selector_all("a.item", "els => els.map(e => e.dataset.url)") if u.startswith(FX)]
        self.assertEqual(urls, [FX + "dateonly", FX + "late", LONG_URL], f"串流 HKIC order {engine}")
        no_date(page.locator("a.item .meta .num").all_inner_texts(), "串流 HKIC card")
        no_date(page.locator(".slot").all_inner_texts(), "串流 HKIC half-hour row")
        self.assertEqual(page.locator("a.item .cat").count(), 0, f"串流 HKIC: no category label (the stream has none) {engine}")
        num = lambda u: page.locator(f'a.item[data-url="{u}"] .meta .num').inner_text()
        self.assertEqual(num(FX + "late"), datefmt.time(x["late"]["seen"]), "the time shown is the notification's")
        self.assertEqual(num(FX + "dateonly"), datefmt.time(x["dateonly"]["seen"]))
        self.assertEqual(page.locator(f'a.item[data-url="{FX}yday"]').count(), 0, "filed yesterday: not on today's page")
        self.assertIn(datefmt.date(i["day"]), page.locator(".days").inner_text(), "the picker keeps the date")
        # yesterday: only what was sent yesterday, times only
        self.go(page, f"/?p=HKIC&d={i['yday']}")
        page.wait_for_selector(f'a.item[data-url="{FX}yday"]')
        self.assertEqual(page.locator(f'a.item[data-url="{FX}late"]').count(), 0, "sent today: not on yesterday's page")
        self.assertEqual(num(FX + "yday"), "22:00")
        no_date(page.locator("a.item .meta .num").all_inner_texts(), "串流 past day card")
        no_date(page.locator(".slot").all_inner_texts(), "串流 past day half-hour row")
        self.no_sideways(page, f"串流 past day {engine}")
        self.go(page, "/?p=CEHK")
        page.wait_for_selector(f'a.item[data-url="{LONG_URL}"]')

    def test_stream_webkit_390(self):
        self.stream_checks("webkit")

    def test_stream_chromium_1600(self):
        self.stream_checks("chromium")

    # ---- the item page ----
    def item_checks(self, engine, approved):
        ctx, page = self.open(engine, approved)
        i = self.ids
        try:
            self.go(page, f"/item/{i['main']}?p=CEHK")
            for sel in ("#h-title", "#text", "#pictures", "#media", "#thread", "#ai", "#tags", "#article"):
                page.wait_for_selector(sel)
            body = lambda sel: page.locator(sel).inner_text()
            self.assertIn("205 則（來源顯示 231 則）", body("#thread h2"))  # every comment filed (item.thread: no cap)
            self.assertEqual(page.locator("#thread li.cm").count(), 205)
            self.assertEqual(page.locator("#thread li.cm:visible").count(), 20, "20 comments shown until 顯示全部")
            self.assertIn("以上留言已納入評分", body("#thread li.scorecut"))
            self.assertIn("；以下是之後收集的留言", body("#thread li.scorecut"))
            # the pipelines turned off (Deyao, 2026-10-20: cost) are absent: a picture not scored yet and a clip queued
            # for its transcript (no subtitles) are left out (item.ts pictures, media)
            import flags
            on = corpus.kv_get("edge:config")["flags"].get(flags.MAIN_HOST) or {}
            self.assertIn(f"其他圖片（{2 if on.get('pictures') else 1}）", body("#pictures"))
            self.assertIn("1 張有資訊", body("#pictures h2"))
            m = body("#media")
            if on.get("transcription"):
                self.assertIn("4 段", m); self.assertIn("逐字稿排隊中，第 ", m)  # static: no spinner for pipeline data (Deyao, 2026-10-16)
            else:
                self.assertIn("3 段", m); self.assertNotIn("排隊", m)
            self.assertIn("YouTube 上載者字幕（zh-Hant）", m)
            self.assertEqual(page.locator("#media .ai-t").count(), 1, "「AI 轉錄」 on the model's transcript only")
            self.assertIn("1 則", body("#article h2"))
            page.wait_for_function("() => [...document.querySelectorAll('section h2')].some(h => h.textContent.startsWith('同一事件的其他報道'))"
                                   " && !document.querySelector('[data-wait]')")
            origin = body(".card:has(h2:text-is('來源及時間'))")
            self.assertIn("內文來源", origin); self.assertIn("archive.today 存檔", origin)
            ai_card = body("#ai")
            if approved:
                page.wait_for_selector("#thread .tx >> nth=0")
                self.assertIn("全文保存於", body("#text")); self.assertIn("來自archive.today 存檔", body("#text"))
                page.wait_for_selector("#ai .sc")
                ai_card = body("#ai")
                self.assertIn("已收錄（重新評分後低於門檻）", ai_card)
                self.assertIn("重新評分為 30 分（低於門檻 50）", body("#ai .rescored"))
                self.assertIn("收錄時 60 分，重新評分後 75 分", ai_card)
                # the 市場 line: a split stream whose market classifier runs (two or more children on; one child on, or
                # no split, shows none: item.ts scoring, a turned-off feature is absent)
                cfg = corpus.kv_get("edge:config")
                split = [st for st in cfg["streams"] if st.get("split") and
                         sum(1 for ch in cfg["streams"] if ch.get("parent") == st["id"] and cfg["on"].get(ch["id"])) > 1]
                if split:
                    self.assertIn("市場：", ai_card); self.assertIn("尚未分類（排隊中）", ai_card)
                else:
                    self.assertNotIn("市場：", ai_card)
                self.assertNotIn("系統保存了全文", body("#text"))
            else:
                self.assertIn("系統保存了全文", body("#text"))
                self.assertIn("只供 Deyao 批准的帳戶查看", ai_card)
            self.assertIn("整體判斷", ai_card)
            self.no_sideways(page, f"item {engine} approved={approved}")
            page.locator("#unfold").click()
            self.assertEqual(page.locator("#thread li.cm:visible").count(), 205)
            self.clean(page, f"item {engine} approved={approved}")

            # a comment row: its thread's parent, this one marked
            self.go(page, f"/item/{i['comment']}")
            page.wait_for_selector("#thread li.cm.me")
            self.assertIn("的一則留言", body("#thread"))
            self.no_sideways(page, f"comment {engine}")
            # the article mate: its 同一文章的其他帖子 row carries the long author name
            self.go(page, f"/item/{i['mate']}")
            page.wait_for_selector("#article li")
            self.no_sideways(page, f"article mate {engine}")
            # near-empty posts
            self.go(page, f"/item/{i['thin']}")
            # (approved: the AI card's rows replace the public line once /app/item/ai answers)
            page.wait_for_function("t => document.querySelector('#ai b.thin')?.textContent === t", arg="內容不足，未評分")
            self.go(page, f"/item/{i['wait']}")
            # (approved: the AI card's rows replace the public line once /app/item/ai answers)
            page.wait_for_function("t => document.querySelector('#ai b.thin')?.textContent === t", arg="內容不足，等待更多內容")
            self.clean(page, f"thin {engine}")
        finally:
            ctx.close()

    def test_item_release_time(self):
        """詳情 shows the post's release time as discovered (published), or only its date when the source gave only a
        date, never a time we made up or the time we filed it (Deyao, 2026-10-20)"""
        x, i = self.ids["extra"], self.ids
        for engine in ("webkit", "chromium"):
            ctx, page = self.open(engine)
            try:
                pub = lambda: page.locator(".times span:has-text('發佈') b").inner_text()
                self.go(page, f"/item/{i['main']}?p=CEHK")
                self.assertEqual(pub(), datefmt.date_time(i["T"]), engine)
                self.go(page, f"/item/{x['late']['id']}?p=HKIC")
                self.assertEqual(pub(), datefmt.date_time(x["late"]["published"]), engine)
                self.go(page, f"/item/{x['dateonly']['id']}?p=HKIC")
                self.assertEqual(pub(), datefmt.date(i["day"]), f"{engine}: a date-only post shows its date alone")
                self.clean(page, f"item release time {engine}")
            finally:
                ctx.close()

    def test_remembered_stream(self):
        """a link without ?p= while the browser remembers another stream than the server's own pick: no hydration error,
        the remembered stream is shown, and it is already in the server-rendered page (no flash of another stream on a
        phone). 2026-10-20: the item page of a post matched to several streams logged a React hydration error"""
        import re
        i = self.ids
        marked = lambda page: page.locator('.yyc-streams a[aria-current="page"]').get_attribute("data-p")
        for engine in ("webkit", "chromium"):
            ctx, page = self.open(engine)
            try:
                # the page's own pick for this post, with nothing remembered
                self.go(page, f"/item/{i['main']}")
                page.wait_for_url("**p=*")
                d0 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(page.url).query))["p"]
                ctx.clear_cookies(); page.evaluate("() => { try { localStorage.clear(); } catch {} }")
                other = next(s for s in ("HKIC", "CEHK", "GALBOT") if s != d0)  # kept or matched, not the default
                self.go(page, f"/?p={other}")  # the browser now remembers `other`
                page.errors.clear()
                for path in (f"/item/{i['main']}", f"/item/{i['main']}#thread", "/"):
                    self.go(page, path)
                    page.wait_for_url(f"**p={other}*")
                    self.assertEqual(marked(page), other, f"{engine} {path}: the remembered stream is shown")
                    page.wait_for_timeout(500)
                    self.clean(page, f"remembered stream {engine} {path}")
                    html = ctx.request.get(BASE + path.split("#")[0]).text()  # what the server renders for this browser
                    self.assertRegex(html, rf'data-p="{other}"[^>]*aria-current="page"',
                                     f"{engine} {path}: the server already marks the remembered stream")
                # a remembered stream this post neither kept nor matched: the page's own pick, still no error
                self.go(page, "/?p=POL")
                page.errors.clear()
                self.go(page, f"/item/{i['main']}")
                page.wait_for_url(f"**p={d0}*")
                self.assertEqual(marked(page), d0)
                page.wait_for_timeout(500)
                self.clean(page, f"remembered unmatched stream {engine}")
            finally:
                ctx.close()

    def test_item_webkit_390(self):
        self.item_checks("webkit", False)

    def test_item_chromium_1600(self):
        self.item_checks("chromium", False)

    def test_item_approved_webkit_390(self):
        self.item_checks("webkit", True)

    def test_item_approved_chromium_1600(self):
        self.item_checks("chromium", True)

    # ---- not found ----
    def test_not_found(self):
        for engine in ("webkit", "chromium"):
            ctx, page = self.open(engine)
            try:
                for path, msg in (("/item/999999999", "系統沒有這個編號的帖子。"),
                                  ("/item?" + urllib.parse.urlencode({"url": FX + "never-collected"}), "這個連結不在系統收集的帖子之中。")):
                    self.go(page, path)
                    page.wait_for_function("t => document.body.innerText.includes(t)", arg=msg)
                    self.assertIn("找不到這則帖子", page.locator("main").inner_text())
                self.clean(page, f"not found {engine}")  # (a missing post is a value: no unhandled error on hydration)
            finally:
                ctx.close()

    # ---- old URLs ----
    def test_old_urls(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        def head(path):
            try:
                r = opener.open(BASE + path, timeout=120)
                return r.status, r.headers.get("Location")
            except urllib.error.HTTPError as e:
                return e.code, e.headers.get("Location")
        i = self.ids
        self.assertEqual(head(f"/post/{i['main']}"), (301, f"/item/{i['main']}#text"))
        self.assertEqual(head("/item?" + urllib.parse.urlencode({"url": i["main_url"], "p": "HKIC"})),
                         (302, f"/item/{i['main']}?p=HKIC"))
        code, _ = head(f"/?p=CEHK&d={i['day']}")
        self.assertEqual(code, 200)
        ctx, page = self.open("chromium")
        try:  # the #hash survives the 302 (a #text link from /post/<id> redirects and sent reports)
            page.goto(BASE + "/item?" + urllib.parse.urlencode({"url": i["main_url"], "p": "HKIC"}) + "#text", timeout=120000)
            page.wait_for_selector("#h-title")
            u = urllib.parse.urlsplit(page.url)
            self.assertEqual((u.path, u.query, u.fragment), (f"/item/{i['main']}", "p=HKIC", "text"))
            self.assertEqual(page.locator('.yyc-streams a[aria-current="page"]').get_attribute("data-p"), "HKIC")
            # /?p=&d= shows that stream and day
            self.go(page, f"/?p=HKIC&d={i['day']}")
            page.wait_for_selector(f'a.item[data-url="{LONG_URL}"]')
            self.assertEqual(page.locator('.side a.stream[aria-current="page"]').get_attribute("data-p"), "HKIC")
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
