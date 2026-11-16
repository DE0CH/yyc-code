"""One Chrome per worker (Patchright, headed on Xvfb like the news stream's browser jobs), on YYC's
residential proxy with a Hong Kong exit, and the apps' logins as Playwright storage state kept in the database
(table browser_state), so workers hold no state of their own and any number of them can run.

The browser is Chrome for Testing at a pinned version (CHROME_PATH, the Dockerfile's CFT_VERSION), not Google Chrome
stable: Perplexity's Cloudflare challenge accepted 2Captcha's Turnstile token in Chrome for Testing 151 and refused it,
round after round, in Google Chrome 156 on the same exit IP (2026-10-27).

Proxy traffic (Deyao, 2026-10-29: IPRoyal bandwidth; IPRoyal's per-host report for 27-28 Oct): only the apps' own
pages and APIs go through yycstream. Public static hosts (the apps' code CDNs, the Turnstile widget, analytics and
telemetry beacons: DIRECT) load from the cluster's own IP; Chrome's background services (component and model
downloads, push messaging, Safe Browsing, updates: about 0.25 GB in those two days) are off; and the worker's browser
loads no images (images=False; the sign-in flow keeps them for reCAPTCHA's picture grid). Measured 2026-10-29, one
question to Perplexity, Dola, Google AI 概覽 and Copilot from the cluster: 14.32 MB through the proxy before, 5.89 MB
after, every app answering (Perplexity's Cloudflare check passed with the widget loaded directly).
"""
import asyncio
import json
import os
import secrets

from patchright.async_api import async_playwright

from . import proxy

LOCALE, TZ = "zh-HK", "Asia/Hong_Kong"
# hosts Chrome reaches directly, not through the proxy (Chrome's proxy bypass list syntax): public code CDNs, the
# Turnstile widget, analytics/telemetry; never an app's own page or API host, nor ByteDance's security SDK (mssdk)
DIRECT = ",".join((
    "sf-flow-web-cdn.ciciai.com", "*.ibyteimg.com", "*.byteimg.com", "mon-sg.byteintlapi.com", "*.byteoversea.com",
    "pplx-next-static-public.perplexity.ai", "frontend-cdn.perplexity.ai", "count.perplexity.ai",
    "*.gstatic.com", "www.googletagmanager.com", "pagead2.googlesyndication.com", "*.doubleclick.net",
    "r.bing.com", "fscdn.eppo.cloud", "browser-intake-datadoghq.com", "challenges.cloudflare.com", "*.gvt1.com",
))
# Chrome's own background traffic off (component and model downloads, push messaging, Safe Browsing, updates)
QUIET_ARGS = ["--disable-background-networking", "--disable-component-update"]
QUIET_FEATURES = "OptimizationHints,OptimizationGuideModelDownloading,OptimizationHintsFetching,MediaRouter"


class Browser:
    """async with Browser(state) as b: page = await b.page()  → b.state() is the current storage state"""

    def __init__(self, state=None, session=None, images=False):
        self._state, self.images = state, images
        # one sticky exit IP per browser for 24 h: a login seen from one HK home address, not a new one per request
        self.session = session or secrets.token_hex(4)

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        kw = {"executable_path": os.environ.get("CHROME_PATH", "/opt/chrome/chrome"), "headless": False,
              "args": ["--disable-quic", "--disable-features=UseDnsHttpsSvcb,UseDnsHttpsSvcbAlpn," + QUIET_FEATURES,
                       "--lang=zh-HK"] + QUIET_ARGS}
        if not self.images:
            kw["args"].append("--blink-settings=imagesEnabled=false")
        px = proxy.browser_proxy("hk", self.session, "24h")
        if px:
            kw["proxy"] = dict(px, bypass=DIRECT)
        elif not os.environ.get("AIWATCH_NO_PROXY"):
            raise RuntimeError("RESI_PROXY not set")  # never quietly from the datacenter IP
        self.browser = await self._pw.chromium.launch(**kw)
        self.ctx = await self.browser.new_context(storage_state=self._state or None, locale=LOCALE, timezone_id=TZ,
                                                  viewport={"width": 1366, "height": 900})
        return self

    async def page(self):
        return await self.ctx.new_page()

    async def state(self):
        return await self.ctx.storage_state()

    async def __aexit__(self, *exc):
        try:
            await self.browser.close()
        finally:
            await self._pw.stop()


async def settle(page, read, quiet=6.0, timeout=180.0, step=1.5):
    """wait until read(page) (a coroutine → str) stops changing for `quiet` seconds (an answer has finished streaming)
    → the last value; raises TimeoutError after `timeout` with no settled value"""
    last, same, waited = None, 0.0, 0.0
    while waited < timeout:
        await asyncio.sleep(step)
        waited += step
        try:
            cur = await read(page)
        except Exception:
            cur = None
        if cur and cur == last:
            same += step
            if same >= quiet:
                return cur
        else:
            same = 0.0
        last = cur
    if last:
        return last
    raise TimeoutError("no answer appeared")


def dumps(state):
    return json.dumps(state, ensure_ascii=False)
