"""The consumer AI apps Hong Kong people use (Deyao, 2026-10-27): what each one shows a person who asks it a question,
read from the app's own page in a real Chrome on a Hong Kong home IP. No ChatGPT (Deyao). Free tiers only.

Each engine: async ask(page, ctx, question) → {"text": answer as shown, "citations": [{"url", "title"}], "meta": {...}}
or raises NoAnswer (the app showed none: e.g. Google gave no AI overview for this query) / LoginNeeded (the app wants a
login the stored state no longer has: `login.py` in a Claude session renews it) / Blocked (a wall we could not pass).
The page markup is the apps' own and changes: every reader below finds the answer by its visible structure (a heading,
the input box, the last message) rather than generated class names, and the fixtures in tests/ pin what each saw.
"""
import asyncio
import os
import re
import urllib.parse

from . import captcha, proxy
from .browser import settle

GOOGLE_HOSTS = re.compile(r"^https?://([^/]*\.)?(google\.|gstatic\.|googleusercontent\.|googleapis\.)")


class NoAnswer(Exception):
    pass


class LoginNeeded(Exception):
    pass


class Blocked(Exception):
    pass


async def _goto(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await cloudflare(page)


def _dbg(*a):
    if os.environ.get("AIWATCH_DEBUG"):
        print("[cf]", *a, flush=True)


CF_WALL = re.compile(r"(Performing security verification|Verify(ing)? you are (a )?human|正在執行安全驗證|正在驗證您是否是人類|確認您是真人|Just a moment)")


async def cloudflare(page, tries=3, again=True):
    """a Cloudflare "verify you are human" wall: a real mouse click on the Turnstile checkbox, then 2Captcha's token
    (captcha.solve_turnstile_challenge); raises Blocked when it stays. The tab comes to the front first: a background
    tab runs its timers at most once a second, too slow for the 1 ms render hook (the worker keeps several apps' tabs
    open at once; 2026-10-27 all three Perplexity asks of a case were blocked that way)."""
    first = True
    for _ in range(tries):
        await asyncio.sleep(3)
        try:
            body = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 400) : ''")
        except Exception:  # noqa: BLE001 — navigating
            continue
        if not CF_WALL.search(body):
            return
        _dbg("wall:", body[:60].replace("\n", " "))
        if first:
            await page.bring_to_front()
            first = False
        # the widget's iframe is in a closed shadow root: its box is the box of the hidden response field's parent
        box = await page.evaluate("""() => { const i = document.querySelector('input[name="cf-turnstile-response"]');
            const el = i && i.parentElement; if (!el) return null; const r = el.getBoundingClientRect();
            return r.width ? {x: r.x, y: r.y, w: r.width, h: r.height} : null }""")
        if box:
            x, y = box["x"] + 30, box["y"] + min(box["h"], 65) / 2
            await page.mouse.move(x - 150, y + 90)
            await page.mouse.move(x, y, steps=20)
            await page.mouse.click(x, y)
        await asyncio.sleep(8)
    body = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 400) : ''")
    if CF_WALL.search(body) and os.environ.get("TWOCAPTCHA_API"):
        try:
            _dbg("2captcha:", await captcha.solve_turnstile_challenge(page))
        except Exception as e:  # noqa: BLE001
            _dbg("2captcha failed:", e)
        for _ in range(15):  # after the token the page verifies, then loads the site
            await asyncio.sleep(2)
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 400) : ''")
            except Exception:  # noqa: BLE001 — navigating
                continue
            if not CF_WALL.search(body):
                break
    if CF_WALL.search(body) and again:
        await page.reload(wait_until="domcontentloaded")
        return await cloudflare(page, tries, again=False)
    if CF_WALL.search(body):
        raise Blocked("Cloudflare check not passed")


async def _google_open(page, ctx, url, session=None):
    """load a Google search page, solving /sorry/ (at most twice, a fresh challenge each time)"""
    for attempt in range(3):
        await _goto(page, url)
        if "/sorry/" not in page.url:
            return
        if attempt < 2:
            try:
                if await captcha.solve_sorry(page, ctx, proxy.parts("hk", session, "24h") if session else None):
                    return
            except Exception:  # noqa: BLE001 — a fresh challenge on the next load
                pass
    raise Blocked("Google /sorry/ not passed")


async def _resolve_google(ctx, href):
    """a Google /goto or /url link → the target URL (the redirect read, not followed)"""
    if href.startswith("/"):
        href = "https://www.google.com" + href
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
    for k in ("q", "url"):
        v = (q.get(k) or [""])[0]
        if v.startswith("http"):
            return v
    try:
        r = await ctx.request.get(href, max_redirects=0, timeout=20000)
        return r.headers.get("location") or href
    except Exception:  # noqa: BLE001
        return href


async def _google_sources(page, ctx):
    """the source cards of an AI overview / AI Mode answer ([data-src-id]: inline chips and the side list) → citations"""
    cards = await page.evaluate("""() => { const seen = new Set(), out = [];
      for (const c of document.querySelectorAll('[data-src-id]')) { const a = c.querySelector('a[href]'); if (!a) continue;
        const id = c.getAttribute('data-src-id'); if (seen.has(id)) continue; seen.add(id);
        out.push({href: a.getAttribute('href'), title: (a.getAttribute('aria-label') || c.innerText.split('\\n')[0] || '').trim()}); }
      return out }""")
    out, seen = [], set()
    for c in cards:
        url = await _resolve_google(ctx, c["href"])
        if url in seen or GOOGLE_HOSTS.match(url):
            continue
        seen.add(url)
        out.append({"url": url, "title": c["title"][:300]})
    return out


# an element's visible text without the source cards (the side list and the inline chips): hidden while it is read
HIDE_SOURCES = """(el) => { const hid = [...el.querySelectorAll('[data-src-id], [data-container-id="rhs-col"]')];
  const prev = hid.map(h => h.style.display); hid.forEach(h => h.style.display = 'none');
  const t = el.innerText; hid.forEach((h, i) => h.style.display = prev[i]); return t; }"""

AIO_JS = """async () => {
  const textWithout = """ + HIDE_SOURCES + """;
  const label = [...document.querySelectorAll('div,span,h1,h2')].find(e => /^(AI 概覽|AI Overview|AI 概览)$/.test(e.innerText.trim()));
  if (!label) return null;
  let box = label;
  while (box.parentElement && !box.parentElement.querySelector('#rso') && box.parentElement !== document.body) box = box.parentElement;
  const more = [...box.querySelectorAll('[role=button],button')].find(b => /^(顯示更多|Show more|显示更多)$/.test(b.innerText.trim()));
  if (more) { more.click(); await new Promise(r => setTimeout(r, 2500)); }
  return textWithout(box);
}"""


async def google_aio(page, ctx, question, session=None):
    """Google Search's AI 概覽 (AI Overview) on google.com.hk in Traditional Chinese: shown above the results for some
    queries only; none for this query = NoAnswer"""
    url = "https://www.google.com.hk/search?" + urllib.parse.urlencode({"q": question, "hl": "zh-HK", "gl": "hk"})
    await _google_open(page, ctx, url, session)
    await asyncio.sleep(4)
    text = await page.evaluate(AIO_JS)
    if not text:
        # some overviews load on a click ("顯示 AI 概覽") or stream in a little later
        await asyncio.sleep(6)
        text = await page.evaluate(AIO_JS)
    if not text:
        raise NoAnswer("no AI overview for this query")
    text = _strip_google(text)
    return {"text": text, "citations": await _google_sources(page, ctx), "meta": {"url": page.url}}


def _strip_google(text):
    lines = [x for x in text.split("\n") if x.strip()]
    drop = re.compile(r"^(AI 概覽|AI 模式|顯示全部|顯示更多|AI 答覆可能有錯.*|AI 可能會出錯.*|AI responses may include mistakes.*|\+\d+|·.*)$")
    return "\n".join(x for x in lines if not drop.match(x.strip())).strip()


AIMODE_JS = """() => {
  const textWithout = """ + HIDE_SOURCES + """;
  // the answer column of AI Mode: the largest block holding the question's turn, without the input box
  const main = document.querySelector('[data-container-id="main-col"]') || document.querySelector('[role=main]') || document.body;
  return textWithout(main);
}"""


async def google_aimode(page, ctx, question, session=None):
    """Google's AI 模式 (AI Mode, udm=50), the chat-style answer with sources"""
    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": question, "hl": "zh-HK", "gl": "hk", "udm": "50"})
    await _google_open(page, ctx, url, session)
    text = await settle(page, lambda p: p.evaluate(AIMODE_JS), quiet=6, timeout=120)
    text = _strip_google(text)
    i = text.find(question)
    if i >= 0:
        text = text[i + len(question):].strip()
    if len(text) < 40:
        raise NoAnswer("AI Mode gave no answer")
    return {"text": text, "citations": await _google_sources(page, ctx), "meta": {"url": page.url}}


def _bing_target(href):
    """a Bing /ck/a click link → its target (parameter u = "a1" + base64url of the URL), else the link itself"""
    import base64
    u = (urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("u") or [""])[0]
    if u.startswith("a1"):
        b = u[2:]
        try:
            return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode()
        except Exception:  # noqa: BLE001
            pass
    return href


BING_NONE = re.compile(r"沒有針對此問題的結果|没有针对此问题的结果|There are no results for")


async def copilot(page, ctx, question, session=None):
    """Microsoft Copilot as Hong Kong sees it without an account: Bing's Copilot Search (「AI 模式」 on bing.com). From HK,
    copilot.microsoft.com shows only a sign-in page, and Microsoft refused to create an account for YYC's address
    (2026-10-27, three tries: "unusual activity"). The answer is in an iframe (bing.com/search)."""
    url = "https://www.bing.com/copilotsearch?" + urllib.parse.urlencode({"q": question, "setlang": "zh-hk", "cc": "hk"})
    await _goto(page, url)

    async def read(p):
        for f in p.frames:
            if "bing.com/search" in f.url:
                return await f.evaluate("() => document.body ? document.body.innerText : ''")
        return ""

    async def read_or_none(p):
        t = await read(p)
        return t if not BING_NONE.search(t or "") else "NONE"

    text = await settle(page, read_or_none, quiet=4, timeout=150)
    if text == "NONE":
        raise NoAnswer("Copilot Search has no answer for this question (沒有針對此問題的結果)")
    frame = next(f for f in page.frames if "bing.com/search" in f.url)
    links = await frame.evaluate("""() => [...document.querySelectorAll('a[href*="/ck/a"]')].map(a => ({href: a.href,
        title: (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ')}))""")
    best = {}
    for l in links:
        tgt = _bing_target(l["href"])
        if tgt.startswith("http") and "bing.com" not in tgt and len(l["title"]) > len(best.get(tgt, "")):
            best[tgt] = l["title"]
    lines = [x for x in text.split("\n") if x.strip() and not re.fullmatch(r"\s*(\d+|。|其他 \+\d+ 個|上一個引文|下一個引文)\s*", x)]
    text = "\n".join(lines).strip()
    if len(text) < 40:
        raise NoAnswer("Copilot Search gave no answer")
    return {"text": text, "citations": [{"url": u, "title": t[:300]} for u, t in best.items()], "meta": {"url": page.url}}


async def perplexity(page, ctx, question, session=None):
    """Perplexity (free plan, signed in with YYC's Google account's address): /search/new?q= asks at once; the answer
    is the .prose blocks of the thread; its sources are on the 「Links」 tab"""
    await _goto(page, "https://www.perplexity.ai/search/new?" + urllib.parse.urlencode({"q": question}))
    if not any("session-token" in c["name"] for c in await ctx.cookies("https://www.perplexity.ai")):
        raise LoginNeeded("Perplexity is signed out")

    async def read(p):
        return await p.evaluate("""() => [...document.querySelectorAll('.prose')].map(e => e.innerText).join('\\n')""")

    text = await settle(page, read, quiet=6, timeout=180)
    for name in ("Got it", "知道了"):
        b = page.get_by_role("button", name=name)
        if await b.count():
            await b.first.click()
    cites = []
    tab = page.get_by_role("tab", name=re.compile(r"^(Links|連結|链接)$"))
    if await tab.count():
        await tab.first.click()
        await asyncio.sleep(3)
        cites = await page.evaluate("""() => { const seen = new Set(), out = [];
          for (const a of document.querySelectorAll('a[href^="http"]')) {
            if (/perplexity\\.ai/.test(a.href) || seen.has(a.href)) continue; seen.add(a.href);
            out.push({url: a.href, title: a.innerText.trim().replace(/\\s+/g, ' ').slice(0, 300)}); }
          return out }""")
    text = re.sub(r"\n?\b[\w.-]+\s\+\d+\b", "", text).strip()  # the inline source chips ("bcthk +1")
    if len(text) < 20:
        raise NoAnswer("Perplexity gave no answer")
    return {"text": text, "citations": cites, "meta": {"url": page.url}}


async def dola(page, ctx, question, session=None):
    """豆包 as Hong Kong gets it: Dola (dola.com, ByteDance's international 豆包, formerly Cici; 豆包 itself takes only a
    +86 number), signed in with YYC's Google account. A new chat per question; the answer is the last received
    message; its sources open from 「已搜尋 N 個關鍵字，參考 M 篇資料」"""
    await _goto(page, "https://www.dola.com/chat/")
    if not any(c["name"] == "sessionid" for c in await ctx.cookies("https://www.dola.com")):
        raise LoginNeeded("Dola is signed out")
    await asyncio.sleep(3)
    # the cookie notice and a background tab both swallowed the typing on the cluster (the chat never started)
    for name in ("我知道了", "OK", "Got it"):
        b = page.get_by_role("button", name=name)
        if await b.count():
            await b.first.click()
    await page.bring_to_front()
    ed = page.locator(".ProseMirror, textarea").first
    await ed.click()
    await page.keyboard.type(question, delay=30)
    await page.keyboard.press("Enter")
    for _ in range(10):
        await asyncio.sleep(1)
        if re.search(r"/chat/\d+", page.url):
            break
    else:
        send = page.locator("[data-testid=chat_input_send_button], button[aria-label*=send i], button[aria-label*=發送]")
        if await send.count():
            await send.first.click()
        await asyncio.sleep(5)
        if not re.search(r"/chat/\d+", page.url):
            raise RuntimeError("Dola did not start the chat (the question was not sent)")

    async def read(p):
        return await p.evaluate("""() => { const m = [...document.querySelectorAll('[data-testid=receive_message]')].pop();
            const t = m && m.querySelector('[data-testid=message_text_content]'); return t ? t.innerText : '' }""")

    text = await settle(page, read, quiet=8, timeout=240)
    cites = []
    refs = page.get_by_text(re.compile(r"參考\s*\d+\s*篇資料|参考\s*\d+\s*篇资料|\d+\s*sources"))
    if await refs.count():
        await refs.last.click()
        await asyncio.sleep(4)
        cites = await page.evaluate("""() => { const seen = new Set(), out = [];
          for (const a of document.querySelectorAll('a[href^="http"]')) {
            if (/dola\\.com|bytedance|byteimg/.test(a.href) || seen.has(a.href)) continue; seen.add(a.href);
            out.push({url: a.href, title: a.innerText.trim().replace(/\\s+/g, ' ').slice(0, 300)}); }
          return out }""")
    if len(text) < 20:
        raise NoAnswer("Dola gave no answer")
    return {"text": text.strip(), "citations": cites, "meta": {"url": page.url}}


async def deepseek(page, ctx, question, session=None):
    """DeepSeek's chat (chat.deepseek.com), signed in with an e-mail account (its Google sign-in opens a popup only
    after an hCaptcha, which Chrome blocks), 聯網搜尋 (web search) on so it can answer about recent events. Not yet run
    against a signed-in page (2026-10-27: the sign-up stalled on hCaptcha): the reader follows DeepSeek's markup as
    seen signed out; check it the first time the login is in place."""
    await _goto(page, "https://chat.deepseek.com/")
    await asyncio.sleep(3)
    token = await page.evaluate("() => { try { return JSON.parse(localStorage.getItem('userToken') || '{}').value } catch (e) { return null } }")
    if "sign_in" in page.url or not token:
        raise LoginNeeded("DeepSeek is signed out")
    search = page.get_by_role("button", name=re.compile(r"^(聯網搜尋|联网搜索|Search)$"))
    if await search.count():
        on = await search.first.evaluate("(b) => /selected|active|checked/.test(b.className) || b.getAttribute('aria-pressed') === 'true'")
        if not on:
            await search.first.click()
    box = page.locator("textarea").first
    await box.click()
    await page.keyboard.type(question, delay=30)
    await page.keyboard.press("Enter")

    async def read(p):
        return await p.evaluate("() => { const m = [...document.querySelectorAll('.ds-markdown')].pop(); return m ? m.innerText : '' }")

    text = await settle(page, read, quiet=8, timeout=300)
    cites = await page.evaluate("""() => { const m = [...document.querySelectorAll('.ds-markdown')].pop(); const seen = new Set(), out = [];
        const root = m ? m.closest('[class]').parentElement : document;
        for (const a of root.querySelectorAll('a[href^="http"]')) { if (/deepseek\\.com/.test(a.href) || seen.has(a.href)) continue;
          seen.add(a.href); out.push({url: a.href, title: a.innerText.trim().slice(0, 300)}); }
        return out }""")
    if len(text) < 20:
        raise NoAnswer("DeepSeek gave no answer")
    return {"text": text.strip(), "citations": cites, "meta": {"url": page.url}}
