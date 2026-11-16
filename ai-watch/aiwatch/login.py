"""Sign the browser in to the apps that need an account, with YYC's company Google account (store yyc:
YYC_AI_WATCH_GMAIL / YYC_AI_WATCH_GMAIL_PASSWORD), and save the result as the browser state the workers use.

The rare half of a login split in two (claude-env CLAUDE.md "Logins that need Deyao split in two"): run in a Claude
session when a worker reports `login_needed`; the everyday runs never ask anyone. Google's sign-in from a new browser
asks to confirm it's you: an image reCAPTCHA (solved here by 2Captcha's reCAPTCHA Enterprise task, YYC's key) and a
code e-mailed to the account's recovery address, which Deyao relays in chat: this waits for it in CODE_FILE.

  python -m aiwatch.login --cdp http://127.0.0.1:9340 --code-file ~/aiwatch-code [--apps perplexity,deepseek,dola]
  python -m aiwatch.login --headed ... (no --cdp: its own Chrome on the YYC proxy)    then --save writes the state
  to the database (DATABASE_URL; from a session: cluster.sh db-forward)
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time

GOOGLE_SIGNIN = "https://accounts.google.com/signin/v2/identifier?hl=zh-HK&continue=https%3A%2F%2Fmyaccount.google.com%2F"


async def _visible(page, sel, timeout=1500):
    try:
        await page.wait_for_selector(sel, state="visible", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _type(page, sel, text):
    await page.click(sel)
    await page.keyboard.type(text, delay=55)


async def _mouse_click(page, el):
    """a real mouse click at an element's centre (a tile's own click can be refused: an overlay covers it)"""
    b = await el.bounding_box()
    if b:
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        await page.mouse.move(x, y, steps=8)
        await page.mouse.click(x, y)


async def _recaptcha(page):
    """Google's sign-in reCAPTCHA: a real click on the checkbox; when it answers with an image grid, 2Captcha's grid
    task (YYC's key) says which tiles to click, round after round (new tiles fade in), then 驗證. A token put into the
    page is not enough here: the form asks the widget, which was never solved (tried 2026-10-27)."""
    import base64
    from . import captcha
    anchor = next((f for f in page.frames if "recaptcha/enterprise/anchor" in f.url or "recaptcha/api2/anchor" in f.url), None)
    if not anchor:
        return False
    box = await (await anchor.frame_element()).bounding_box()
    await page.mouse.move(box["x"] - 120, box["y"] + 120)
    await page.mouse.move(box["x"] + 28, box["y"] + box["height"] / 2, steps=18)
    await page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
    await asyncio.sleep(4)
    for _ in range(8):
        try:
            if await anchor.evaluate("() => document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked')") == "true":
                break
        except Exception:  # noqa: BLE001 — the widget is gone: Google moved on
            break
        bf = next((f for f in page.frames if "/bframe" in f.url), None)
        if not bf:
            break
        el = await bf.query_selector("#rc-imageselect")
        if not el or not await el.is_visible():
            break
        instr = (await bf.inner_text(".rc-imageselect-desc-wrapper")).replace("\n", " ").strip()
        rows = len(await bf.query_selector_all("table.rc-imageselect-table-33 tr, table.rc-imageselect-table-44 tr, table.rc-imageselect-table-42 tr"))
        cells = await bf.query_selector_all("td.rc-imageselect-tile")
        cols = max(1, len(cells) // max(rows, 1))
        grid = await bf.query_selector("table[class*=rc-imageselect-table]")
        img = base64.b64encode(await grid.screenshot()).decode()
        sol = await captcha._solve({"type": "GridTask", "body": img, "comment": instr, "rows": rows, "columns": cols,
                                    "imgType": "recaptcha"}, timeout=180)
        for i in sol.get("click", []):
            if 1 <= i <= len(cells):
                await _mouse_click(page, cells[i - 1])
                await asyncio.sleep(0.6)
        await asyncio.sleep(3)  # tiles that fade in after a click get their own round
        # "click verify once there are none left": clicked tiles are replaced, so another round until none match
        if sol.get("click") and any(w in instr for w in ("none left", "沒有剩下", "直到", "沒有符合")):
            continue
        await _mouse_click(page, await bf.query_selector("#recaptcha-verify-button"))
        await asyncio.sleep(4)
    nxt = page.get_by_role("button", name="繼續").or_(page.get_by_role("button", name="Next"))
    if await nxt.count():
        await nxt.first.click()
    await asyncio.sleep(6)
    return True


async def _wait_code(code_file, timeout=3600):
    """the e-mailed code, written by Claude into code_file when Deyao sends it (deleted after reading)"""
    print(f"WAITING FOR CODE in {code_file}", flush=True)
    t = time.time()
    while time.time() - t < timeout:
        if os.path.exists(code_file) and open(code_file).read().strip():
            code = "".join(ch for ch in open(code_file).read() if ch.isdigit())
            os.remove(code_file)
            return code
        await asyncio.sleep(2)
    raise TimeoutError("no code")


async def _signed_in(ctx):
    """Google's session cookie SID is set only for a signed-in browser (myaccount.google.com also opens signed out)"""
    return any(c["name"] == "SID" for c in await ctx.cookies("https://accounts.google.com"))


async def google(page, code_file):
    """sign in to Google (no-op when already signed in) → True"""
    if await _signed_in(page.context):
        return True
    if "accounts.google.com/v3/signin/" not in page.url:  # else carry on where an earlier run stopped
        await page.goto(GOOGLE_SIGNIN, wait_until="domcontentloaded", timeout=90000)
        await asyncio.sleep(3)
    email, pw = os.environ["YYC_AI_WATCH_GMAIL"], os.environ["YYC_AI_WATCH_GMAIL_PASSWORD"]
    if await _visible(page, "#identifierId", 20000):
        await _type(page, "#identifierId", email)
        await page.click("#identifierNext")
    for _ in range(12):  # each pass handles whatever Google shows next
        await asyncio.sleep(4)
        url = page.url
        print("google:", url.split("?")[0], flush=True)
        if await _signed_in(page.context):
            return True
        if "/challenge/recaptcha" in url:
            await _recaptcha(page)
        elif await _visible(page, "input[name=Passwd]"):
            await _type(page, "input[name=Passwd]", pw)
            await page.click("#passwordNext")
        elif "/challenge/selection" in url:
            opt = page.get_by_text("驗證碼").or_(page.get_by_text("verification code")).first
            await opt.click()
        elif "/challenge/" in url and await _visible(page, "input[type=tel], input[name=Pin]"):
            code = await _wait_code(code_file)
            await page.fill("input[type=tel], input[name=Pin]", code)
            await page.keyboard.press("Enter")
        else:
            # "stay signed in", "not now" for passkeys/recovery prompts
            for name in ("略過", "暫時不要", "Not now", "Skip", "取消", "Cancel"):
                b = page.get_by_role("button", name=name)
                if await b.count():
                    await b.first.click()
                    break
    raise RuntimeError(f"Google sign-in did not finish (at {page.url.split('?')[0]})")


async def _google_sso(page, ctx, click):
    """click a site's "Continue with Google", pick the company account in the popup (or the same tab)"""
    try:
        async with ctx.expect_page(timeout=15000) as pop:
            await click()
        g = await pop.value
    except Exception:  # noqa: BLE001 — some sites (Dola) go to Google in the same tab
        g = page
    await asyncio.sleep(5)
    if "accounts.google" in g.url:
        acct = g.get_by_text(os.environ["YYC_AI_WATCH_GMAIL"].lower())
        if await acct.count():
            await acct.first.click()
            await asyncio.sleep(5)
        for name in ("繼續", "Continue", "允許", "Allow"):
            b = g.get_by_role("button", name=name)
            if await b.count():
                await b.first.click()
                await asyncio.sleep(5)
    await asyncio.sleep(6)


GOOGLE_BUTTON = re.compile(r"Google")


async def signed_in(app, page, ctx):
    """the app's own session is in the browser (its cookie or token; the pages' wording changes with the language)"""
    if app == "perplexity":
        return any("session-token" in c["name"] for c in await ctx.cookies("https://www.perplexity.ai"))
    if app == "deepseek":
        if "deepseek.com" not in page.url:
            await page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded")
            await asyncio.sleep(4)
        # userToken is there signed out too, as {"value": null}
        return bool(await page.evaluate("() => { try { return JSON.parse(localStorage.getItem('userToken') || '{}').value } catch (e) { return null } }"))
    if app == "dola":
        return any(c["name"] == "sessionid" for c in await ctx.cookies("https://www.dola.com"))
    return False


async def perplexity(page, ctx):
    if await signed_in("perplexity", page, ctx):
        return True
    from .engines import _goto
    await _goto(page, "https://www.perplexity.ai/")
    await asyncio.sleep(4)
    await page.get_by_role("button", name=re.compile(r"^(Sign In|登入|登錄|登录)$")).first.click()
    await asyncio.sleep(3)
    await _google_sso(page, ctx, lambda: page.get_by_role("button", name=GOOGLE_BUTTON).first.click())
    return await signed_in("perplexity", page, ctx)


async def deepseek(page, ctx):
    await page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded")
    await asyncio.sleep(6)
    if await signed_in("deepseek", page, ctx):
        return True
    await _google_sso(page, ctx, lambda: page.get_by_text(GOOGLE_BUTTON).filter(visible=True).first.click())
    return await signed_in("deepseek", page, ctx)


async def dola(page, ctx):
    if await signed_in("dola", page, ctx):
        return True
    await page.goto("https://www.dola.com/chat/", wait_until="domcontentloaded")
    await asyncio.sleep(6)
    await page.get_by_text(re.compile(r"^(Log In|登入|登錄|登录)$")).first.click()
    await asyncio.sleep(3)
    await _google_sso(page, ctx, lambda: page.get_by_text(GOOGLE_BUTTON).last.click())
    c = page.get_by_text(re.compile(r"^(Confirm|確認|确认)$"))
    if await c.count():
        await c.first.click()
        await asyncio.sleep(4)
    return await signed_in("dola", page, ctx)


async def gmail_code(gmail_page, sender, since, timeout=180):
    """the newest code in an e-mail from `sender` that arrived after `since` (epoch s), read in the company Gmail
    (a tab of a browser where Google is signed in) → the code"""
    t = time.time()
    while time.time() - t < timeout:
        await gmail_page.goto(f"https://mail.google.com/mail/u/0/#search/from%3A{sender}+newer_than%3A1h", wait_until="domcontentloaded")
        await asyncio.sleep(6)
        rows = gmail_page.locator("tr.zA")
        if await rows.count():
            await rows.first.click()
            await asyncio.sleep(4)
            body = await gmail_page.evaluate("() => document.body.innerText")
            # a code has a digit in it (a six-letter word after "code" is not one)
            m = (re.search(r"(?:code|代碼|驗證碼|验证码)\D{0,60}?\b((?=[A-Za-z]*\d)[A-Za-z0-9]{6})\b", body, re.I)
                 or re.search(r"\b(\d{6})\b", body))
            if m:
                return m[1]
        await asyncio.sleep(10)
    raise TimeoutError(f"no e-mail from {sender}")


async def perplexity_email(page, ctx, gmail_page):
    """Perplexity by an e-mailed code (its Google button needs Google signed in in THIS browser, and Google's sign-in
    is tied to the browser it happened in)"""
    if await signed_in("perplexity", page, ctx):
        return True
    from .engines import _goto
    await _goto(page, "https://www.perplexity.ai/")
    await asyncio.sleep(4)
    await page.get_by_role("button", name=re.compile(r"^(Sign In|登入|登錄|登录)$")).first.click()
    await asyncio.sleep(3)
    since = time.time()
    await page.fill("input[type=email]", os.environ["YYC_AI_WATCH_GMAIL"])
    await page.keyboard.press("Enter")
    await asyncio.sleep(5)
    code = await gmail_code(gmail_page, "perplexity", since)
    box = page.locator("input:not([type=email]):not([type=file])").first
    await box.click()
    await page.keyboard.type(code, delay=80)
    await page.keyboard.press("Enter")
    await asyncio.sleep(8)
    return await signed_in("perplexity", page, ctx)


GOOGLE_DOMAINS = re.compile(r"(^|\.)(google\.[a-z.]+|youtube\.com|gstatic\.com)$")


def merge_states(*states, drop_google=True):
    """one storage state from several browsers' (later ones win per cookie and per origin), by default without Google's
    own cookies: Google ties its sign-in to the browser it happened in, and the workers need only the apps' sessions"""
    cookies, origins = {}, {}
    for st in states:
        for c in st.get("cookies", []):
            if not (drop_google and GOOGLE_DOMAINS.search(c["domain"].lstrip("."))):
                cookies[(c["name"], c["domain"], c.get("path", "/"))] = c
        for o in st.get("origins", []):
            if not (drop_google and GOOGLE_DOMAINS.search(o["origin"].split("//")[-1])):
                origins[o["origin"]] = o
    return {"cookies": list(cookies.values()), "origins": list(origins.values())}


APPS = {"perplexity": perplexity, "deepseek": deepseek, "dola": dola}


async def run(args):
    from patchright.async_api import async_playwright
    pw = await async_playwright().start()
    if args.cdp:
        b = await pw.chromium.connect_over_cdp(args.cdp)
        ctx = b.contexts[0]
    else:
        from .browser import Browser
        holder = Browser(None, images=True)  # reCAPTCHA's picture grid
        await holder.__aenter__()
        ctx = holder.ctx
    flows = [p for p in ctx.pages if "accounts.google.com/v3/signin/challenge/" in p.url]
    page = flows[-1] if flows else await ctx.new_page()
    gmail = None
    if args.perplexity_email_via:
        gb = await pw.chromium.connect_over_cdp(args.perplexity_email_via)
        gmail = await gb.contexts[0].new_page()
    else:
        await google(page, os.path.expanduser(args.code_file))
    for name in [a for a in args.apps.split(",") if a]:
        ok = await (perplexity_email(page, ctx, gmail) if name == "perplexity" and gmail else APPS[name](page, ctx))
        print(f"{name}: {'signed in' if ok else 'NOT signed in'}", flush=True)
    state = await ctx.storage_state()
    if args.out:
        fd = os.open(os.path.expanduser(args.out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, json.dumps(state, ensure_ascii=False).encode())
        os.close(fd)
        print("state written:", args.out, f"({len(state.get('cookies', []))} cookies)")
    if args.save:
        from . import db
        db.save_browser_state(state, note="login.py")
        print("state saved to the database")


def main():
    ap = argparse.ArgumentParser()
    if "--merge" in sys.argv:
        return merge_main(ap)
    ap.add_argument("--cdp", help="attach to a running Chrome (dev) instead of starting one")
    ap.add_argument("--code-file", default="~/aiwatch-code")
    ap.add_argument("--apps", default="perplexity,deepseek,dola")
    ap.add_argument("--perplexity-email-via", help="CDP URL of a browser where Google is signed in: Perplexity signs in "
                    "by e-mailed code, read in that browser's Gmail (skips the Google sign-in here)")
    ap.add_argument("--out", help="also write the storage state to this file (mode 600)")
    ap.add_argument("--save", action="store_true", help="write the state to the database (DATABASE_URL)")
    ap.add_argument("--merge", nargs="+", metavar="STATE.json", help="only merge these state files into --out / --save")
    asyncio.run(run(ap.parse_args()))


def merge_main(ap):
    ap.add_argument("--merge", nargs="+", required=True)
    ap.add_argument("--out")
    ap.add_argument("--save", action="store_true")
    a = ap.parse_args()
    state = merge_states(*[json.load(open(os.path.expanduser(f))) for f in a.merge])
    print(f"merged: {len(state['cookies'])} cookies, {len(state['origins'])} origins")
    if a.out:
        fd = os.open(os.path.expanduser(a.out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, json.dumps(state, ensure_ascii=False).encode())
        os.close(fd)
    if a.save:
        from . import db
        db.save_browser_state(state, note="login.py --merge")
        print("state saved to the database")


if __name__ == "__main__":
    sys.exit(main())
