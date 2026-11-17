#!/usr/bin/env python3
"""Live check on newswatch.yyc-internal.com that what approved accounts see comes with the page (web-app/README.md
"What only approved accounts see comes with the page"; the same claims as tests/test_gated_preload.py, against the
real Cloudflare Access).

    EMAIL_BOT_DIR=<claude-env>/.claude/skills/email-bot python3 tests/live_gated_preload.py [SITE]

Signs in as bot@deyaochen.com (a normal user of the YYC catalogue) with Access's e-mailed code, read from the bot's
mailbox (email-bot `list.py` through `session-secret --keys BOT_EMAIL_TOKEN`; Cloudflare sends about 5 codes in a few
minutes at most). Then, on a post of today's 串流 that has a stored full text:
1. the item page's HTML fetched with Access's cookie alone has the end of the full text and a scorer's reason, and is
   Cache-Control private, no-store; fetched with no cookie it has neither and is private, no-cache;
2. 串流's HTML with the cookie has an item's reason; without, not;
3. in the browser the item page's first load asks for none of /app/me, /app/item/text, /app/item/ai; hovering a 串流
   card requests the full text and the scores before the click, and the click is a page change in the app that shows
   the full text with no second request.
Prints JSON (no cookie, no code); exit 1 when a check fails. Screenshots: ~/artifacts/gated-preload/live/.
LIVE_STATE=<file> keeps the bot's session for the next run (no new code); the file is a secret.
"""
import json, os, re, subprocess, sys, time, urllib.error, urllib.request
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

SITE = (sys.argv[1] if len(sys.argv) > 1 else "https://newswatch.yyc-internal.com").rstrip("/")
EB = os.environ.get("EMAIL_BOT_DIR") or sys.exit("EMAIL_BOT_DIR is not set (claude-env's email-bot skill folder)")
BOT = "bot@deyaochen.com"
STATE = os.environ.get("LIVE_STATE")  # a file that keeps the bot's session between runs (a secret: outside any repo or record)
OUT = os.path.expanduser("~/artifacts/gated-preload/live")
UA = "Mozilla/5.0 (X11; Linux x86_64) yyc-live-check"
GATED = re.compile(r"/app/(me|item/text|item/ai|item/video|stream/scores)\b")
PLAIN = re.compile(r"[一-鿿A-Za-z0-9，。、]{14,40}")  # a run React writes into HTML as it is (nothing escaped)
os.makedirs(OUT, exist_ok=True)
fails = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        fails.append(what)


def bot(*args):
    return subprocess.run(["session-secret", "--keys", "BOT_EMAIL_TOKEN", "--exec", "python3", os.path.join(EB, "list.py"), *args],
                          capture_output=True, text=True).stdout


def mails(n=5):
    return [json.loads(l) for l in bot("--n", str(n)).splitlines() if l.startswith("{")]


def code_after(id0):
    for _ in range(45):
        for m in mails():
            if int(m["id"]) > id0 and "cloudflare" in json.dumps(m).lower():
                body = bot("--show", str(m["id"]))
                mm = re.search(r"\n(\d{6})\n", body) or re.search(r"\b(\d{6})\b", body)
                if mm:
                    return mm.group(1)
        time.sleep(4)
    return None


def get(path, cookie=None):
    h = {"User-Agent": UA, "Accept": "text/html"}
    if cookie:
        h["Cookie"] = "CF_Authorization=" + cookie
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(SITE + path, headers=h), timeout=60) as r:
            return r.status, r.headers, r.read().decode(), time.time() - t0
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read().decode(errors="replace"), time.time() - t0


def tail_run(text):
    """a run from the last third of the text that is not in its first two thirds (so not in the public excerpt)"""
    cut = len(text) * 2 // 3
    for m in PLAIN.finditer(text, cut):
        if m.group(0) not in text[:cut]:
            return m.group(0)
    return None


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-HK",
                              storage_state=STATE if STATE and os.path.exists(STATE) else None)
    ctx.set_default_timeout(60000)
    pg = ctx.new_page()
    pg.goto(SITE + "/app/me", wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    if "cloudflareaccess.com" in pg.url:
        id0 = max([int(m["id"]) for m in mails()] or [0])
        pg.fill("input[type=email], input[name=email]", BOT)
        pg.keyboard.press("Enter")
        code = code_after(id0)
        if not code:
            pg.screenshot(path=os.path.join(OUT, "no-code.png"))
            sys.exit("no Access code mail for the bot (Cloudflare limits codes: wait 15 minutes)")
        pg.fill("input[name=code], input[type=text], input[inputmode=numeric]", code)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(8000)
    me = pg.request.get(SITE + "/app/me")
    who = (me.json() or {}).get("email") if me.status == 200 else None
    check(who == BOT, f"signed in as the bot (/app/me {me.status})")
    if who != BOT:
        pg.screenshot(path=os.path.join(OUT, "not-signed-in.png"))
        sys.exit(1)
    cookie = next(c["value"] for c in ctx.cookies(SITE) if c["name"] == "CF_Authorization")
    if STATE:
        ctx.storage_state(path=STATE)
        os.chmod(STATE, 0o600)
    print("build:", get("/version")[2].strip())

    # a post of today's 串流 with a stored full text, and a reason to look for
    scores = pg.request.get(SITE + "/app/stream/scores").json().get("scores", {})
    pick = None
    for key, sc in scores.items():
        stream, url = key.split("|", 1)
        if not sc.get("reason") or not PLAIN.search(sc["reason"]):
            continue
        pg.goto(f"{SITE}/item?p={stream}&url={urllib.request.quote(url, safe='')}", wait_until="domcontentloaded")
        pg.wait_for_url(re.compile(r"/item/\d+"), timeout=30000)
        pid = int(re.search(r"/item/(\d+)", pg.url).group(1))
        text = (pg.request.get(f"{SITE}/app/item/text?id={pid}").json() or {}).get("text") or ""
        run = tail_run(text) if len(text) > 1500 else None
        if run:
            pick = {"stream": stream, "pid": pid, "run": run, "reason": PLAIN.search(sc["reason"]).group(0), "chars": len(text)}
            break
    if not pick:
        sys.exit("no post of today's 串流 with a long stored text and a reason")
    item = f"/item/{pick['pid']}?p={pick['stream']}"
    print("post:", item, "full text", pick["chars"], "characters")

    # 1
    code, h, html, secs = get(item, cookie)
    check(code == 200 and pick["run"] in html, f"the item page's HTML for the bot has the end of the full text ({secs:.2f} s)")
    check(pick["reason"] in html, "and the scorer's reason")
    check(h.get("Cache-Control") == "private, no-store", f"and is never stored ({h.get('Cache-Control')})")
    code, h, html, secs0 = get(item)
    check(code == 200 and pick["run"] not in html and pick["reason"] not in html, f"the public's HTML has neither ({secs0:.2f} s)")
    check(h.get("Cache-Control") == "private, no-cache", f"and is private, no-cache ({h.get('Cache-Control')})")
    # 2
    code, h, html, _ = get(f"/?p={pick['stream']}", cookie)
    check(code == 200 and pick["reason"] in html, "串流's HTML for the bot has the reason")
    code, h, html, _ = get(f"/?p={pick['stream']}")
    check(code == 200 and pick["reason"] not in html, "the public's has not")

    # 3
    page = ctx.new_page()
    asked, docs = [], []
    page.on("request", lambda r: GATED.search(r.url) and asked.append(urlsplit(r.url).path))
    page.on("request", lambda r: r.resource_type == "document" and docs.append(urlsplit(r.url).path))
    page.goto(SITE + item, wait_until="load")
    page.wait_for_function("() => document.documentElement.dataset.hydrated === '1'")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    check(pick["run"] in page.inner_text("#text"), "the browser shows the full text on the item page's first load")
    check(asked == [], f"and asked for none of it itself {asked}")
    page.screenshot(path=os.path.join(OUT, "item.png"))
    page.goto(f"{SITE}/?p={pick['stream']}", wait_until="load")
    page.wait_for_function("() => document.documentElement.dataset.hydrated === '1'")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    del asked[:], docs[:]
    card = page.locator("a.item").first
    card.scroll_into_view_if_needed()
    card.hover()
    for _ in range(100):
        if {"/app/item/text", "/app/item/ai"} <= set(asked):
            break
        page.wait_for_timeout(100)
    check(sorted(asked) == ["/app/item/ai", "/app/item/text"], f"hovering a 串流 card requests its full text and scores {asked}")
    page.wait_for_load_state("networkidle")
    card.click()
    page.wait_for_selector("#h-title")
    page.wait_for_timeout(1500)
    check(sorted(asked) == ["/app/item/ai", "/app/item/text"] and docs == [],
          f"the click is a page change in the app with no second request {asked} {docs}")
    page.screenshot(path=os.path.join(OUT, "item-after-hover.png"))
    browser.close()

print(json.dumps({"site": SITE, "post": item, "seconds_with_cookie": round(secs, 2), "seconds_public": round(secs0, 2),
                  "failed": fails}, ensure_ascii=False))
sys.exit(1 if fails else 0)
