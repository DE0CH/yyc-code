"""Google's /sorry/ reCAPTCHA ("unusual traffic") solved with 2Captcha on YYC's own key (TWOCAPTCHA_API), the method of
claude-env's google-search skill: the sitekey and data-s off the page, a proxied and a proxyless task raced (2Captcha
often answers UNSOLVABLE for one variant only), the token put into the form and its callback called. A data-s works
once: on failure the caller loads the search again for a fresh challenge.
"""
import asyncio
import json
import os
import urllib.request

API = "https://api.2captcha.com"


def _post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


async def _solve(task, timeout=150):
    key = os.environ["TWOCAPTCHA_API"]
    r = await asyncio.to_thread(_post, "/createTask", {"clientKey": key, "task": task})
    if r.get("errorId"):
        raise RuntimeError(f"2captcha createTask: {r.get('errorCode')}")
    tid, waited = r["taskId"], 0
    while waited < timeout:
        await asyncio.sleep(5)
        waited += 5
        r = await asyncio.to_thread(_post, "/getTaskResult", {"clientKey": key, "taskId": tid})
        if r.get("errorId"):
            raise RuntimeError(f"2captcha: {r.get('errorCode')}")
        if r.get("status") == "ready":
            return r["solution"]
    raise TimeoutError("2captcha: no solution in time")


async def solve_sorry(page, ctx, proxy_parts=None):
    """page is on google.*/sorry/: solve it and submit → True when the page left /sorry/"""
    info = await page.evaluate("""() => { const el = document.querySelector('#recaptcha, .g-recaptcha, [data-sitekey]');
        return el ? {sitekey: el.getAttribute('data-sitekey'), dataS: el.getAttribute('data-s')} : null }""")
    if not info or not info.get("sitekey"):
        raise RuntimeError("on /sorry/ but no reCAPTCHA sitekey")
    cookies = "; ".join(f"{c['name']}={c['value']}" for c in await ctx.cookies("https://www.google.com"))
    ua = await page.evaluate("navigator.userAgent")
    base = {"type": "RecaptchaV2TaskProxyless", "websiteURL": page.url, "websiteKey": info["sitekey"],
            "recaptchaDataSValue": info.get("dataS") or "", "userAgent": ua, "cookies": cookies}
    tasks = [_solve(base)]
    if proxy_parts:
        host, port, user, pw = proxy_parts
        tasks.append(_solve({**base, "type": "RecaptchaV2Task", "proxyType": "http", "proxyAddress": host,
                             "proxyPort": port, "proxyLogin": user, "proxyPassword": pw}))
    sol, errs = None, []
    for fut in asyncio.as_completed(tasks):
        try:
            sol = await fut
            break
        except Exception as e:  # noqa: BLE001 — the other variant may still solve it
            errs.append(str(e))
    if not sol:
        raise RuntimeError("; ".join(errs))
    token = sol.get("gRecaptchaResponse") or sol.get("token")
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
        await page.evaluate("""(tok) => {
          document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response').forEach(t => { t.value = tok; });
          const cbs = []; const walk = (o, d) => { if (!o || d > 4) return; for (const k in o) { try { const v = o[k];
            if (k === 'callback' && typeof v === 'function') cbs.push(v); else if (typeof v === 'object') walk(v, d + 1); } catch (e) {} } };
          try { walk(window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients, 0); } catch (e) {}
          if (cbs.length) cbs[0](tok); else (document.querySelector('#captcha-form') || document.forms[0]).submit();
        }""", token)
    return "/sorry/" not in page.url


# Cloudflare challenge pages: the widget's render options (sitekey, action, cData, chlPageData) are captured by
# replacing turnstile.render before the page loads (2Captcha's own method, claude-env 2captcha skill), solved by the
# API, and the captured callback is called with the token.
TURNSTILE_HOOK = """() => { window.__ts = {params: null, callback: null};
  // every 1 ms: the challenge page calls render right after turnstile appears (a 10 ms poll missed it, 2026-10-27);
  // a property setter instead broke the page (its loader sees 'turnstile' in window and skips itself)
  const iv = setInterval(() => { if (!window.turnstile) return; clearInterval(iv);
    window.turnstile.render = (c, o) => { window.__ts.params = {sitekey: o.sitekey, action: o.action, data: o.cData,
      pagedata: o.chlPageData}; window.__ts.callback = o.callback; return 'intercepted'; }; }, 1); }"""


async def solve_turnstile_challenge(page):
    """page shows a Cloudflare challenge: hook, reload, solve, submit → True when the wall is gone. Patchright evaluates
    in an isolated world by default; the hook and the widget live in the page's own (isolated_context=False)."""
    await page.add_init_script("(" + TURNSTILE_HOOK + ")()")
    await page.reload(wait_until="domcontentloaded")
    params = None
    for _ in range(20):
        await asyncio.sleep(1)
        params = await page.evaluate("() => window.__ts && window.__ts.params", isolated_context=False)
        if params:
            break
    if not params:
        return False
    task = {"type": "TurnstileTaskProxyless", "websiteURL": page.url, "websiteKey": params["sitekey"],
            "userAgent": await page.evaluate("navigator.userAgent")}
    for k, v in (("action", params.get("action")), ("data", params.get("data")), ("pagedata", params.get("pagedata"))):
        if v:
            task[k] = v
    sol = await _solve(task)
    await page.evaluate("(tok) => { try { window.__ts.callback(tok) } catch (e) {} }", sol["token"], isolated_context=False)
    await asyncio.sleep(8)
    return True
