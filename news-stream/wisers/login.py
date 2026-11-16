"""Log in to WisersOne (慧科; YYC's account, dedicated to the relay: company decision, Deyao 2026-10-21) and save the
session; never runs a search.

The relay (relay.py) calls login() by itself whenever it finds the saved session expired (Deyao, 2026-10-16), so this
script is only for a manual check or login (Deyao taking the session back by hand, e.g. after the monitor shows the
relay logged out): the running relay opens the session it saves without a restart. Env: WISERS_GROUP_ID, WISERS_USER_ID, WISERS_PASSWORD (Jarvis 2 store
`yyc`; cluster Secret `wisers`), TWOCAPTCHA_API (the image captcha), DATABASE_URL (the saved session: state.json in
the relay's store, Postgres; store.py). A saved session that still works is reused, so the login (which ends any other
session of the user: one session per user) happens only when needed. Writes status.json and after-login.png to OUTDIR.
Usage: python3 news-stream/wisers/login.py [OUTDIR]
  on the cluster: kubectl -n yyc exec deploy/wisers -- python3 /app/wisers/login.py /tmp/login
WISERS_HOME / WISERS_LOGIN_URL override Wisers' addresses (tests point them at a local fake)."""
import base64, json, os, sys, time, urllib.parse, urllib.request

sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # news-stream/: nomedia.py
import nomedia  # noqa: E402
import store  # noqa: E402  wisers/, next to this file

HOME = os.environ.get("WISERS_HOME", "https://vertical-new-search.wisersone.com/wevo/home")
LOGIN_URL = os.environ.get("WISERS_LOGIN_URL",
                           "https://login.wisers.net/?clang=en-zh&fromUrl=https%3A%2F%2Fwww.wisersone.com&product=wiseone")
LOGIN_HOST = urllib.parse.urlsplit(LOGIN_URL).netloc
STATE = "state.json"  # the browser's saved session (Playwright storage state), in the relay's store


def load_state():
    """the saved session as Playwright's storage state, or None"""
    return store.read_json(STATE)


def save_state(ctx):
    store.write_json(STATE, ctx.storage_state())


def on_login(url):
    """Wisers sent the browser to its login page: the session has expired"""
    return LOGIN_HOST in (url or "")


def keep(frame_url):
    """the requests nomedia.block lets through: the login page's own (its captcha picture, which 2Captcha reads);
    every other picture, font or clip of Wisers' pages is never loaded (text only, Deyao 2026-10-20)"""
    return on_login(frame_url)


def solve(png):
    """2Captcha ImageToText; at most 160 s"""
    key = os.environ.get("TWOCAPTCHA_API")
    if not key:  # no solver key (TWOCAPTCHA_API unset): the login fails cleanly, no other solver
        raise RuntimeError("2Captcha: TWOCAPTCHA_API not set, the image captcha can't be answered")

    def post(u, d):
        r = urllib.request.Request(u, data=json.dumps(d).encode(), headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(r, timeout=60))
    t = post("https://api.2captcha.com/createTask", {"clientKey": key, "task": {
        "type": "ImageToTextTask", "body": base64.b64encode(png).decode(), "case": True}})
    if not t.get("taskId"):
        raise RuntimeError(f"2Captcha: {t.get('errorCode') or t}")
    for _ in range(40):
        time.sleep(4)
        r = post("https://api.2captcha.com/getTaskResult", {"clientKey": key, "taskId": t["taskId"]})
        if r.get("status") == "ready":
            return r["solution"]["text"]
        if r.get("errorId"):
            raise RuntimeError(f"2Captcha: {r.get('errorCode')}")
    raise RuntimeError("2Captcha: no answer in 160 s")


def login(pg, log=print, solver=None):
    """fill Wisers' login form on Playwright (sync) page `pg` with the stored credentials, solving the image captcha
    (3 tries); True when the browser has left the login page. The caller saves the context's storage state."""
    solver = solver or solve
    # wait for the form, never for "networkidle": the login page long-polls api/listenUserScan (its WeChat QR login)
    # for ~15 s at a time with a ~2 s pause, so the network is quiet only in that pause; on the relay's path it often
    # never was, and two automatic logins ran out at 60 s with the form already there (2026-11-05 09:12 and 09:23 HKT)
    pg.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    ins = pg.locator("input.NewInputField__Input-sc-1j8023t-2")
    ins.nth(2).wait_for(state="visible", timeout=60000)
    img = pg.locator("img[class*=CaptchaField__CaptchaImage]")
    try:  # the captcha is drawn a moment after the fields (0.2-0.8 s); none when the browser has a saved password
        img.wait_for(state="visible", timeout=10000)
    except Exception:
        pass
    ins.nth(0).fill(os.environ["WISERS_GROUP_ID"])
    ins.nth(1).fill(os.environ["WISERS_USER_ID"])
    ins.nth(2).fill(os.environ["WISERS_PASSWORD"])
    for attempt in range(3):
        pg.wait_for_timeout(1500)
        if img.count():  # hidden when the browser has a saved password for the group (localStorage)
            pg.locator("input[class*=CaptchaField__Input]").fill(solver(img.screenshot()))
        pg.locator("input[type=submit]").click()
        pg.wait_for_timeout(6000)
        if not on_login(pg.url):
            return True
        err = pg.evaluate("document.body.innerText.slice(0,400)")
        log(f"Wisers login: still on the login page after try {attempt + 1}: {err.replace(chr(10), ' ')[:200]}")
        if img.count():
            img.click()  # a fresh captcha
    return False


def main():
    try:  # the news-stream image has Patchright + Google Chrome (CHROME); a dev box may have Playwright only
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/artifacts/wisers")
    os.makedirs(out, exist_ok=True)

    def status(**k):
        json.dump(dict(k, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), open(out + "/status.json", "w"))
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}))
        saved = load_state()
        ctx = b.new_context(viewport={"width": 1366, "height": 900}, locale="zh-HK", storage_state=saved)
        nomedia.block(ctx, allow=keep)  # text only; the login page's captcha is kept
        pg = ctx.new_page()
        if saved:  # still signed in? then no login at all
            pg.goto(HOME, wait_until="domcontentloaded", timeout=60000)  # as the relay opens it (never "networkidle")
            pg.wait_for_timeout(3000)
            if not on_login(pg.url):
                status(step="done", url=pg.url, logged_in=True, reused=True)
                print("saved session still valid:", pg.url)
                save_state(ctx)
                b.close()
                return
            print("saved session expired; logging in")
        status(step="login")
        ok = login(pg)
        print("URL", pg.url)
        pg.wait_for_timeout(5000)
        pg.screenshot(path=out + "/after-login.png")
        save_state(ctx)
        status(step="done", url=pg.url, logged_in=ok)
        b.close()


if __name__ == "__main__":
    main()
