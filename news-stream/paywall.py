#!/usr/bin/env python3
"""Full text from the outlets a plain page read can't give (Deyao, 2026-10-15: "we should be reading all texts
including reuters bloomberg and FT"; "pay captcha solvers … add the costs in, and for bloomberg we can just see what
happens as a proof of concept, otherwise don't break their terms, and even with paywall they usually send us an
excerpt, and also we can try to get the archived page from archive.is"; "the whole website is a proof of concept").

By outlet (cache_fetch.fetch_one calls the first two; the full-text readers save what comes back with its origin in
posts.text_via / text_via_url, which the item page shows):
  - Reuters (no paywall; its DataDome bot wall answers every plain client 401 with an interstitial "device check"):
    Reuters' own content API (/pf/api/v3/content/fetch/article-by-id-or-url-v1, the JSON its pages are built from,
    ~20 KB against ~480 KB for the page) read with a DataDome cookie that CapSolver solves (DatadomeSliderTask: the
    interstitial and the slider captcha), through YYC's ISP IP (ISP_PROXY, flat rate; Deyao 2026-11-17: a solver before
    residential traffic) or, without it, the residential proxy sub-user yycstream (RESI_PROXY, GB exit) on one sticky IP,
    for the solve and the reads, with the User-Agent CapSolver requires. The poller's Reuters source reads its sitemaps
    the same way (reuters_page: DataDome guards them too since 30 Oct 2026). The cookie, the proxy session and
    the User-Agent are kept in kv (paywall:datadome) and reused across pages, threads and runs; a new solve only when
    Reuters answers 401/403 again (a ban of the IP, "t":"bv", takes a new proxy session). text_via 'solver'.
  - Bloomberg (proof of concept): the story as Bloomberg's own page data carries it to any logged-out reader
    (__NEXT_DATA__ story.body; the page is metered, not cut). text_via 'page_data'. A page that answers with its
    robot check instead is kept as an excerpt (below).
  - Paywalled outlets (ARCHIVE_PAGES: FT, Nikkei Asia, Mingtiandi, 信報's paper, HKET's paid sections; and any page
    that declares isAccessibleForFree false yet gives under EXCERPT_CHARS): the public excerpt the page gives is kept
    (text_via 'excerpt'; their terms are kept: hidden paid text in a page or API is not read), then `paywall.py
    archive` (CronJob paywall, every 30 min) takes the text of the page's newest archive.today snapshot (text_via
    'archive', the snapshot's URL in text_via_url) when it is longer than the excerpt, on YYC's ISP IP (archive.today
    answers no datacenter IP since ~8 Nov 2026; ROUTES below). A page with no snapshot is
    submitted once; later runs look again (ARCHIVE_LOOKS times at most, ARCHIVE_DAYS days back). archive.today answers
    datacenter and residential IPs alike with a reCAPTCHA (HTTP 429): 2Captcha solves it in a real Chrome (run under
    xvfb-run) and the clearance cookies are kept in kv (paywall:archive) for plain requests until it asks again.
Costs (Deyao: "add the costs in"): every solve's charge as the provider states it (2Captcha: getTaskResult's cost;
CapSolver: its price list, DD_PRICE, which its balance confirmed) in table solver_spend, shown on the monitor; the proxy traffic is
counted per site (resi.py: reuters, bloomberg, archive) for the monitor, inside IPRoyal's usage report for yycstream. 2Captcha is YYC's own account; CapSolver is Deyao's own
(EXPENSES.md, "Captcha solvers").

    python3 paywall.py archive [DAYS]     one archive run (excerpt posts of the last DAYS days, default ARCHIVE_DAYS)
    python3 paywall.py mark [DAYS]        posts read before this reader on ARCHIVE_PAGES outlets, short: excerpts
    python3 paywall.py <url>…             what the readers get for these pages (test; writes nothing but kv sessions)
"""
import html, json, os, random, re, string, sys, time, urllib.parse
from datetime import datetime, timedelta

import corpus
import datefmt
import nomedia
import resi

HKT = corpus.HKT
TIMEOUT = 40
CAPSOLVER = "https://api.capsolver.com"
TWOCAPTCHA = "https://api.2captcha.com"
# CapSolver solves DataDome only for the User-Agents it lists (docs.capsolver.com, DataDome); curl_cffi's matching
# Chrome fingerprint goes with it
DD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
DD_IMPERSONATE = "chrome150"
DD_COUNTRY = "gb"          # Reuters reads cleanly from a GB residential IP (lessons/25)
DD_SOLVES = 3              # solves one read may try (a banned IP or a failed solve: a new proxy session)
# CapSolver's charge per DataDome solve: its price list (docs.capsolver.com/en/pricing: DatadomeSlider USD 2.50 per
# 1,000). Its getBalance lags the solves by minutes, so a before/after read is 0; the balance confirmed the price on
# 15 Oct 2026 (3 solves: USD 5.9730 → 5.9655)
DD_PRICE = 0.0025
# archive.today's mirrors (one archive, one set of snapshots): a mirror that stops answering a connection (archive.ph
# from the cluster after ~40 lookups, 15 Oct 2026, while archive.is still answered) is skipped for the next one
MIRRORS = ("archive.ph", "archive.is", "archive.li", "archive.md")
ARCHIVE_DAYS = 3
ARCHIVE_LOOKS = 6          # newest-snapshot lookups per post (one per run, 30 min apart) before it is given up
ARCHIVE_MAX = 400          # posts one run looks at (newest first)
ARCHIVE_GAP = 4.0          # seconds between two archive.today requests (at 2 s archive.ph stopped answering the cluster)
ARCHIVE_BUDGET = 1500      # seconds one run may take (the CronJob's activeDeadlineSeconds is 1740); the rest: next run
SUBMIT_MAX = 60            # submissions one run sends (protects archive.today from a burst: the first run met 1,140
                           # earlier excerpts); the rest wait, their looks not counted, for the next runs
EXCERPT_CHARS = 1000       # a page that declares itself paywalled and gives less than this: an excerpt
SOLVER_HOSTS = ("reuters.com",)
DIRECT_HOSTS = ("bloomberg.com",)
# outlets whose pages give the public only an excerpt (hard paywalls), by host and path
ARCHIVE_PAGES = re.compile(r"^https?://([^/]+\.)?(ft\.com|nikkei\.com|mingtiandi\.com|wsj\.com|economist\.com|"
                           r"barrons\.com|theinformation\.com|caixinglobal\.com)/|"
                           r"^https?://www1\.hkej\.com/dailynews/|^https?://(ps|specialist|paper)\.hket\.com/")
# the readers' origin labels (posts.text_via)
VIA = {"feed": "來源的訂閱源", "page": "出版機構網頁", "browser": "出版機構網頁（瀏覽器）", "solver": "出版機構（驗證碼服務）",
       "page_data": "出版機構網頁資料", "excerpt": "付費牆公開摘錄", "archive": "archive.today 存檔",
       "original": "原文出版機構網頁"}  # an aggregator's post (headline4hk): the story it copies, read from its outlet


class Blocked(ConnectionError):
    """the site's bot wall or paywall kept the text back (the caller keeps what it has: an excerpt). A
    ConnectionError, so the full-text readers try the page again later (fulltext_fetch.TRANSIENT, TRIES times)"""


class Unreachable(Blocked):
    """archive.today answers no connection from this IP on any mirror (it does so for a while after a burst of
    lookups): the run stops; nobody's looks are used up"""


def host(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _session(proxy=None, impersonate="chrome", **isp):
    return resi.session(proxy, impersonate, **isp)  # counts the proxied bytes per site (resi.py); isp=True: the ISP IP's


def _post(url, body):
    from curl_cffi import requests
    return requests.post(url, json=body, timeout=TIMEOUT).json()


# ---- costs ----

def spend(provider, purpose, usd, note=None):
    """one paid solve, at the provider's own figure (solver_spend; the monitor's 驗證碼 series)"""
    now = datetime.now(HKT)
    corpus.db().execute("INSERT INTO solver_spend (at, day, provider, purpose, usd, note) VALUES (%s, %s, %s, %s, %s, %s)",
                        (now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d"), provider, purpose, float(usd or 0),
                         note))


# A solver or the proxy refusing for its balance is a safety stop (YYC CLAUDE.md "Budgets are safety stops"): kv
# solver_limit {provider: {since, at, message, notified}}, one Discord message per episode, shown on the monitor's
# stops panel; the first solve that works clears it. The proxy's own refusal (IPRoyal out of traffic) is resi.py's
# single record (kv proxy_out), not this one
LIMIT_RE = re.compile(r"ZERO_BALANCE|NO_SLOT|insufficient|balance|credit|traffic|HTTP 402|HTTP 407", re.I)
LIMIT_NAMES = {"capsolver": "CapSolver (Deyao's account)", "2captcha": "2Captcha (YYC's account)"}


def limit_hit(prov, message):
    import notify
    now = datetime.now(HKT).isoformat(timespec="seconds")
    lim = dict(corpus.kv_get("solver_limit") or {})
    cur = lim.get(prov) or {"since": now, "notified": False}
    cur.update(message=str(message)[:300], at=now)
    if not cur.get("notified"):
        cur["notified"] = notify.deyao(
            f"Waiting on you: {LIMIT_NAMES.get(prov, prov)} is refusing YYC's paywalled/blocked-site reads with "
            f"\"{str(message)[:250]}\" since {datefmt.when(cur['since'])} HKT. Top it up; the readers try "
            "again by themselves.")
    lim[prov] = cur
    corpus.kv_set("solver_limit", lim)


def limit_clear(prov):
    lim = dict(corpus.kv_get("solver_limit") or {})
    if lim.pop(prov, None):
        corpus.kv_set("solver_limit", lim or None)


def _failed(prov, message):
    """a solve that failed: a safety stop when the provider says it is its balance; → Blocked to raise"""
    e = Blocked(f"{prov}: {message}")
    e.limit = bool(LIMIT_RE.search(str(message)))
    if e.limit:
        limit_hit(prov, message)
    return e


def solve_datadome(captcha_url, proxy, purpose):
    """CapSolver DatadomeSliderTask → the datadome cookie value; the charge (DD_PRICE, CapSolver's list price) in
    solver_spend"""
    key = os.environ.get("CAPSOLVER_API") or ""
    if not key:
        raise Blocked("CAPSOLVER_API not set")
    u = urllib.parse.urlparse(proxy)
    t = _post(CAPSOLVER + "/createTask", {"clientKey": key, "task": {
        "type": "DatadomeSliderTask", "captchaUrl": captcha_url, "userAgent": DD_UA,
        "proxy": f"http:{u.hostname}:{u.port}:{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password)}"}})
    if not t.get("taskId"):
        raise _failed("capsolver", f"{t.get('errorCode')} {t.get('errorDescription')}")
    res = {}
    for _ in range(40):
        time.sleep(3)
        res = _post(CAPSOLVER + "/getTaskResult", {"clientKey": key, "taskId": t["taskId"]})
        if res.get("status") != "processing":
            break
    if res.get("status") == "ready":  # CapSolver charges solved tasks only
        spend("capsolver", purpose, DD_PRICE, "DatadomeSliderTask (CapSolver's list price)")
    m = re.search(r"datadome=([^;]+)", (res.get("solution") or {}).get("cookie") or "")
    if not m:
        raise _failed("capsolver", f"{res.get('status')} {res.get('errorCode')} {res.get('errorDescription')}")
    limit_clear("capsolver")
    return m.group(1)


def solve_recaptcha(page_url, sitekey, purpose):
    """2Captcha RecaptchaV2TaskProxyless → the token; the charge 2Captcha states (cost) in solver_spend"""
    key = os.environ.get("TWOCAPTCHA_API") or ""
    if not key:
        raise Blocked("TWOCAPTCHA_API not set")
    t = _post(TWOCAPTCHA + "/createTask", {"clientKey": key, "task": {
        "type": "RecaptchaV2TaskProxyless", "websiteURL": page_url, "websiteKey": sitekey}})
    if not t.get("taskId"):
        raise _failed("2captcha", f"{t.get('errorCode')} {t.get('errorDescription')}")
    res = {}
    for _ in range(36):
        time.sleep(5)
        res = _post(TWOCAPTCHA + "/getTaskResult", {"clientKey": key, "taskId": t["taskId"]})
        if res.get("status") != "processing":
            break
    if res.get("status") == "ready":
        spend("2captcha", purpose, float(res.get("cost") or 0), "RecaptchaV2TaskProxyless")
        limit_clear("2captcha")
        return res["solution"]["gRecaptchaResponse"]
    raise _failed("2captcha", f"{res.get('status')} {res.get('errorCode')} {res.get('errorDescription')}")


# ---- Reuters: DataDome, solved by CapSolver, on the residential proxy ----

def proxy_url(sid, country=DD_COUNTRY):
    """RESI_PROXY (IPRoyal sub-user yycstream, resi.py) on a sticky IP in `country` (GB by default) for this session id"""
    u = resi.url(country, session=sid, lifetime="24h")
    if not u:
        raise Blocked("RESI_PROXY not set")
    return u


def dd_challenge(page, url):
    """a DataDome block → (the captcha/interstitial URL CapSolver takes, banned?) or None. Two shapes: an HTML page
    (var dd={…}, to a page request) and JSON {"url": "https://geo.captcha-delivery.com/…"} (to an API request)"""
    try:
        j = json.loads(page or "")
    except ValueError:
        j = None
    if isinstance(j, dict) and "captcha-delivery.com/" in str(j.get("url")):
        return j["url"], "t=bv" in j["url"]
    m = re.search(r"var dd=(\{.*?\})", page or "")
    if not m:
        return None
    d = json.loads(m.group(1).replace("'", '"'))
    if d.get("t") == "bv":
        return None, True
    q = {"initialCid": d["cid"], "hash": d["hsh"], "cid": d["cookie"], "referer": url, "s": d["s"], "dm": "cd"}
    if d.get("rt") == "i":
        q["b"] = d.get("b")
        path = "interstitial"
    else:
        q.update(t=d.get("t", "fe"), e=d.get("e", ""))
        path = "captcha"
    return f"https://{d.get('host', 'geo.captcha-delivery.com')}/{path}/?" + urllib.parse.urlencode(q), False


def reuters_api(url):
    path = urllib.parse.urlparse(url).path
    # Breakingviews: Reuters' full sitemap lists its columns as /columns/<column>/<slug>/, an address reuters.com itself
    # answers 404 (page and content API); the story lives at /commentary/breakingviews/<slug>/ (checked 2026-10-20)
    path = re.sub(r"^/columns/[^/]+/([^/]+/?)$", r"/commentary/breakingviews/\1", path)
    q = json.dumps({"website_url": path, "website": "reuters"}, separators=(",", ":"))
    return "https://www.reuters.com/pf/api/v3/content/fetch/article-by-id-or-url-v1?query=" + urllib.parse.quote(q)


ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def reuters_text(doc):
    """the API's JSON (or a page's Fusion.globalContent) → (text, published ISO or None)"""
    r = doc.get("result") or doc
    out = []
    for c in r.get("content_elements") or []:
        t = c.get("type")
        if t in ("paragraph", "text", "header"):
            out.append(c.get("content") or "")
        elif t == "list":
            out.extend(i.get("content") or "" for i in c.get("items") or [])
    # Reuters puts zero-width characters between words (U+200B/U+200C/U+200D/U+2060/U+FEFF): out, so search finds the words
    text = "\n".join(x for x in (re.sub(r"\s+", " ", ZERO_WIDTH.sub("", html.unescape(re.sub(r"<[^>]+>", "", s)))).strip()
                                 for s in out) if x)
    return text, r.get("published_time") or r.get("display_time")


def _dd_state():
    return corpus.kv_get("paywall:datadome") or {}


def isp_proxy():
    """YYC's IPRoyal ISP IP (env ISP_PROXY, flat rate; one client at a time: sites_fetch.isp_lock), or None"""
    return os.environ.get("ISP_PROXY") or None


def isp_turn(route):
    """the ISP IP's lock while a block uses it (route "isp"), else nothing"""
    if route != "isp":
        return __import__("contextlib").nullcontext()
    import sites_fetch
    return sites_fetch.isp_lock()


def _dd_get(url, accept, referer=None):
    """GET a reuters.com URL with the solved DataDome session (kv paywall:datadome) → (session, response) for the
    first answer that is not DataDome's (200, 404 …). The route is YYC's ISP IP when ISP_PROXY is set (flat rate,
    Deyao 2026-11-17: a solver before residential traffic), its lock held for the reads and the solve (CapSolver
    connects through the same proxy, and the ISP IP takes one client at a time: the session is closed before it); else
    yycstream on a sticky GB IP (a banned IP takes a new session). referer: the page the solver is told the challenge
    came from (an API read: its article's URL). Raises Blocked when it can't be read."""
    route = "isp" if isp_proxy() else "resi"
    with isp_turn(route):
        st = _dd_state()
        if st.get("route", "resi") != route:  # a cookie is bound to the IP it was solved on
            st = {}
        for attempt in range(DD_SOLVES + 1):
            if not st.get("sid"):
                st = {"sid": "".join(random.choices(string.ascii_lowercase + string.digits, k=8)), "route": route}
            px = isp_proxy() if route == "isp" else proxy_url(st["sid"])
            s = _session(px, DD_IMPERSONATE, **({"isp": True} if route == "isp" else {}))
            if st.get("cookie"):
                s.cookies.set("datadome", st["cookie"], domain=".reuters.com")
            try:
                h = {"User-Agent": DD_UA, "Accept": accept, "Accept-Language": "en-GB,en;q=0.9"}
                r = s.get(url, headers=dict(h, **(NO_KEEPALIVE if route == "isp" else {})), timeout=TIMEOUT)
            except resi.ProxyOut:
                raise  # out of traffic (resi.py: one record, kv proxy_out): a pause for the readers, not a failed try
            except Exception as e:
                raise Blocked(f"reuters via the {route} route: {e!r}"[:300])
            ch = dd_challenge(r.text, referer or url) if r.status_code in (401, 403) else None
            if ch is None:
                got = s.cookies.get("datadome", domain=".reuters.com")  # DataDome renews its cookie as it goes
                if got and got != st.get("cookie"):
                    st["cookie"] = got
                    corpus.kv_set("paywall:datadome", st)
                return s, r
            if attempt == DD_SOLVES:
                break
            s.close()  # the ISP IP's one connection: CapSolver's comes next
            with corpus.lock("paywall:datadome"):  # one solve at a time across threads and pods
                now = _dd_state()
                if now.get("cookie") and now.get("cookie") != st.get("cookie") and now.get("route", "resi") == route:
                    st = now  # another reader solved meanwhile
                    continue
                curl, banned = ch
                if banned:
                    if route == "isp":  # one fixed IP: no other to take
                        raise Blocked("reuters: DataDome banned the ISP IP")
                    st = {}  # this IP is banned: a new sticky IP
                    continue
                try:
                    st = {"sid": st["sid"], "route": route, "cookie": solve_datadome(curl, px, "reuters"),
                          "at": datetime.now(HKT).isoformat(timespec="seconds")}
                except Blocked as e:
                    print("reuters solve:", e, flush=True)
                    if getattr(e, "limit", False):  # the solver's balance: no point trying again now (a safety stop)
                        raise
                    st = {} if route == "resi" else {"sid": st["sid"], "route": route}
                    continue
                corpus.kv_set("paywall:datadome", st)
    raise Blocked("reuters: DataDome kept answering")


def reuters(url, meta=None):
    """Reuters' article text through its content API, with a solved DataDome cookie (_dd_get) → text; meta gets
    "published" and "via" 'solver'. Raises Blocked when it can't be read."""
    meta = {} if meta is None else meta
    s, r = _dd_get(reuters_api(url), "application/json", referer=url)
    if r.status_code == 200:
        doc = r.json()
        text, published = reuters_text(doc)
        if published:
            meta["published"] = published
        if (doc.get("result") or {}).get("title"):  # the story's own headline (its sitemap's stories: from the URL)
            meta["title"] = doc["result"]["title"]
        meta["via"] = "solver"
        return text
    if r.status_code == 404:  # not an article the API knows (a video or live page): its page's own data
        s, r = _dd_get(url, "text/html,application/xhtml+xml,*/*;q=0.8")
        i = r.text.find("Fusion.globalContent=") if r.status_code == 200 else -1
        if i < 0:
            raise Blocked(f"reuters: not in the content API, page HTTP {r.status_code}")
        text, published = reuters_text(json.JSONDecoder().raw_decode(r.text[i + 21:])[0])
        if published:
            meta["published"] = published
        meta["via"] = "solver"
        return text
    raise Blocked(f"reuters: HTTP {r.status_code}")


def reuters_page(url):
    """a reuters.com sitemap (the poller's Reuters source: DataDome guards the sitemaps too since 30 Oct 2026) with
    the solved DataDome session → its text"""
    _, r = _dd_get(url, "application/xml,text/xml;q=0.9,*/*;q=0.8")
    if r.status_code != 200:
        raise Blocked(f"reuters {url[:100]}: HTTP {r.status_code}")
    return r.text


def reuters_read(url):
    """sources.READERS's reader for sid reuters → (text, route)"""
    return reuters_page(url), "isp" if isp_proxy() else "yycstream"


# ---- Bloomberg: the page's own data ----

def _nodes_text(o):
    if isinstance(o, dict):
        if o.get("type") == "text" and isinstance(o.get("value"), str):
            return o["value"]
        return "".join(_nodes_text(c) for c in o.get("content") or [])
    if isinstance(o, list):
        return "".join(_nodes_text(c) for c in o)
    return ""


BBG_COUNTRIES = ("us", "hk")  # residential exits that passed Bloomberg's robot check (15 Oct 2026; a GB one did not)


def bloomberg(url, meta=None):
    """a Bloomberg story (proof of concept): the page from the cluster's own IP; when Bloomberg's robot check
    (PerimeterX, HTTP 403) answers instead, once more from each residential exit in BBG_COUNTRIES on RESI_PROXY
    (~75 KB a page compressed); → bloomberg_text. Raises Blocked when every try met the robot check."""
    import cache_fetch
    tries = []
    try:
        return bloomberg_text(cache_fetch.get(url), meta)
    except Exception as e:  # urllib.error.HTTPError 403 (the robot check), or a page without the story
        tries.append(f"direct: {e!r}"[:120])
    if resi.configured():
        for c in BBG_COUNTRIES:
            px = proxy_url("".join(random.choices(string.ascii_lowercase + string.digits, k=8)), c)
            try:
                r = _session(px).get(url, headers={"Accept-Language": "en-US,en;q=0.9"}, timeout=TIMEOUT)
                if r.status_code == 200:
                    return bloomberg_text(r.text, meta)
                tries.append(f"{c}: HTTP {r.status_code}")
            except resi.ProxyOut:
                raise
            except Exception as e:
                tries.append(f"{c}: {e!r}"[:120])
    raise Blocked("bloomberg: " + "; ".join(tries))


def bloomberg_text(page, meta=None):
    """a Bloomberg article page → the story's text from __NEXT_DATA__ (paragraphs, headings, list items, quotes);
    raises Blocked on its robot check or a page without the story"""
    meta = {} if meta is None else meta
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page or "", re.S)
    if not m:
        raise Blocked("bloomberg: no page data (robot check?)")
    story = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("story") or {}
    out = []
    for c in (story.get("body") or {}).get("content") or []:
        t = c.get("type")
        if t in ("paragraph", "heading", "blockquote", "quote"):
            out.append(_nodes_text(c).strip())
        elif t == "list":
            out.extend(_nodes_text(i).strip() for i in c.get("content") or [])
    text = "\n".join(x for x in out if x)
    if not text:
        raise Blocked("bloomberg: no story body in the page data")
    if story.get("publishedAt"):
        meta["published"] = story["publishedAt"]
    meta["via"] = "page_data"
    return text


# ---- paywalled outlets: the excerpt, then archive.today ----

def failed_via(url):
    """the origin to save for a page whose read failed: an ARCHIVE_PAGES outlet's feed summary is an excerpt (the
    archive job looks for the rest), anything else's is the feed's"""
    return "excerpt" if ARCHIVE_PAGES.search(url or "") else "page"


def declares_paywall(page):
    return bool(re.search(r'"isAccessibleForFree"\s*:\s*"?(false|False)"?', page or ""))


def excerpt(url, page, text):
    """is this read only the public part of a paywalled article (an archive.today copy may have more)?"""
    if ARCHIVE_PAGES.search(url):
        return True
    return declares_paywall(page) and len((text or "").strip()) < EXCERPT_CHARS


ARCHIVE_JUNK = re.compile(r"^(share this|share|tweet|email|linkedin|facebook|whatsapp|copy link|print|"
                          r"subscribe|sign up|advertisement)\b", re.I)


def archive_text(page):
    """an archive.today snapshot → the article text. The snapshot is the page made static: every <p> became a
    <div style=…> inside #CONTENT; the article is the parent whose text-only children carry the most text."""
    import lxml.html
    from collections import defaultdict
    from cache_fetch import JUNK, ptext
    try:
        doc = lxml.html.fromstring(page)
    except Exception:
        return ""
    root = (doc.xpath('//*[@id="CONTENT"]') or [doc])[0]
    for bad in root.xpath(".//script|.//style|.//noscript|.//nav|.//footer|.//header|.//aside|.//form|.//button"):
        bad.drop_tree()
    groups = defaultdict(list)
    for el in root.iter("div", "p", "h2", "h3", "li", "blockquote"):
        if el.xpath("./div|./p|./ul|./ol|./table|./section|./article|./figure"):
            continue
        t = ptext(el)
        if len(t) >= 12 and not JUNK.search(t[:80]) and not ARCHIVE_JUNK.search(t):
            groups[el.getparent()].append(t)
    if not groups:
        return ""
    long_text = lambda ps: sum(len(p) for p in ps if len(p) >= 40)
    best = max(groups.values(), key=long_text)
    if not long_text(best):  # no paragraph at all: menus and labels only (信報's snapshots, 2026-10-20), no article
        return ""
    return "\n".join(dict.fromkeys(best))[:20000]


# archive.today is read on YYC's ISP IP (env ISP_PROXY, flat rate, one client at a time: each request under its lock,
# bytes in kv isp:bytes), lookups, snapshot pages and submissions alike: since ~8 Nov 2026 archive.today answers no
# connection from datacenter IPs (the cluster, Hetzner, LeaseWeb HK, a US host: TCP or TLS timeouts) and the ISP IP
# reads it (17 Nov: 200). Without ISP_PROXY: lookups from the cluster's own IP, submissions through the residential
# proxy (archive.today takes no capture from a datacenter IP: its submit page answers "Loading" and nothing is
# captured, tried 15 Oct 2026). Each route keeps its own clearance cookies (bound to its IP): kv paywall:archive,
# paywall:archive:isp and paywall:archive:resi (with the proxy's sticky session id).
ROUTES = {"direct": "paywall:archive", "isp": "paywall:archive:isp", "resi": "paywall:archive:resi"}
NO_KEEPALIVE = {"Connection": "close"}  # the ISP IP's one client: no tunnel left open between two lock holds


def lookup_route():
    return "isp" if isp_proxy() else "direct"


def submit_route():
    return "isp" if isp_proxy() else "resi"


def archive_session(route="direct"):
    st = corpus.kv_get(ROUTES[route]) or {}
    proxy = None
    if route == "resi":
        st.setdefault("sid", "".join(random.choices(string.ascii_lowercase + string.digits, k=8)))
        proxy = proxy_url(st["sid"])
    if route == "isp":
        proxy = isp_proxy()
        s = _session(proxy, isp=True)
    else:
        s = _session(proxy)
    for dom, jar in (st.get("cookies") or {}).items():  # {mirror domain: {name: value}} (each mirror clears apart)
        if isinstance(jar, dict):  # a bare cookie (kept before the mirrors, 15 Oct) is skipped: a solve renews it
            for k, v in jar.items():
                s.cookies.set(k, v, domain=dom)
    return s, st, proxy


def captcha_page(r):
    return r.status_code == 429 or "g-recaptcha" in (r.text or "")[:60000]


SEND_CAPTCHA = """async (tok) => {
  await new Promise(res => { const s = document.createElement('script'); s.crossOrigin = 'anonymous';
    s.src = 'https://cdn.jsdelivr.net/npm/@thumbmarkjs/thumbmarkjs/dist/thumbmark.umd.js'; s.onload = res; s.onerror = res;
    document.head.appendChild(s); setTimeout(res, 10000); });
  let fp; try { fp = (await new ThumbmarkJS.Thumbmark().get()).components; } catch (e) { fp = {error: true, stage: 'get', message: String(e)}; }
  const body = 'response=' + encodeURIComponent(tok) + '&location=' + encodeURIComponent(location.href) +
               '&xden=A50&fp=' + encodeURIComponent(JSON.stringify(fp));
  const r = await fetch('/cdn-cgi/l/chk_captcha', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body});
  return r.status; }"""


def archive_clear(url, route="direct"):
    """archive.today's reCAPTCHA, in a real Chrome on the route's IP: 2Captcha's token sent the way the captcha page's
    own callback sends it (its fingerprint script, then POST /cdn-cgi/l/chk_captcha), then the clearance cookies →
    kv (ROUTES). Pictures, fonts, video and audio are never loaded, on either route (nomedia.block: text only)."""
    from patchright.sync_api import sync_playwright
    with corpus.lock(ROUTES[route]), isp_turn(route):
        _, st, proxy = archive_session(route)
        kw = {}
        if proxy:
            u = urllib.parse.urlparse(proxy)
            kw["proxy"] = {"server": "http://127.0.0.1:%d" % resi.forwarder(  # the login sent unasked (resi.playwright)
                u.hostname, u.port, urllib.parse.unquote(u.username), urllib.parse.unquote(u.password))}
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome", headless=False, **kw)
            try:
                ctx = b.new_context(no_viewport=True)
                nomedia.block(ctx)  # text only (nomedia.py)
                if proxy:
                    counted = resi.count_browser(ctx, "archive", isp=route == "isp")
                pg = ctx.new_page()
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    if proxy and route == "resi":
                        resi.check_browser(e)  # IPRoyal out of traffic → resi.ProxyOut
                    raise
                time.sleep(2)
                m = re.search(r"""['"]sitekey['"]\s*:\s*['"]([^'"]+)""", pg.content())
                if m:
                    status = pg.evaluate(SEND_CAPTCHA, solve_recaptcha(pg.url, m.group(1), "archive"))
                    if status != 200:
                        raise Blocked(f"archive.today refused the captcha answer: HTTP {status}")
                cookies = dict(st.get("cookies") or {})
                for c in ctx.cookies():
                    if "archive" in c["domain"]:
                        cookies.setdefault(c["domain"].lstrip("."), {})[c["name"]] = c["value"]
                corpus.kv_set(ROUTES[route], dict(st, cookies=cookies, at=datetime.now(HKT).isoformat(timespec="seconds")))
            finally:
                if proxy:
                    counted()
                b.close()


def archive_get(path, s=None, route="direct"):
    """GET a path of archive.today on a route with its clearance cookies, on the first mirror that answers, solving
    its captcha once when it asks → (response, session)"""
    if s is None:
        s, _, _ = archive_session(route)
    kw = {"headers": NO_KEEPALIVE} if route == "isp" else {}
    last = None
    for m in MIRRORS:
        url = f"https://{m}{path}"
        try:
            with isp_turn(route):
                r = s.get(url, timeout=(15, 60), **kw)
        except resi.ProxyOut:
            raise  # out of traffic: every mirror would say the same
        except Exception as e:  # the mirror doesn't answer from this IP: the next one
            last = e
            continue
        if captcha_page(r):
            if not os.environ.get("TWOCAPTCHA_API"):  # no solver key (TWOCAPTCHA_API unset): no
                # browser, no other solver; Unreachable stops the run and leaves the posts pending with their looks
                raise Unreachable("archive.today: captcha, and no 2Captcha key (TWOCAPTCHA_API) to answer it")
            archive_clear(url, route)
            s, _, _ = archive_session(route)
            with isp_turn(route):
                r = s.get(url, timeout=(15, 60), **kw)
            if captcha_page(r):
                raise Blocked("archive.today: captcha again after a solve")
        return r, s
    raise Unreachable(f"archive.today: no mirror answered ({last!r})"[:300])


def archive_lookup(url, s=None, route=None):
    """→ (snapshot URL, its text) of the page's newest archive.today snapshot, or (None, "") when there is none"""
    r, s = archive_get("/newest/" + url, s, route=route or lookup_route())
    if r.status_code == 404 or "/newest/" in r.url:
        return None, ""
    if r.status_code != 200:
        raise Blocked(f"archive.today: HTTP {r.status_code}")
    return re.sub(r"^http:", "https:", r.url), archive_text(r.text)


def archive_submit(url):
    """ask archive.today to capture the page (its submit form's request, as a reader sends it) from the residential
    route (the ISP IP, else the residential proxy); the capture takes minutes, later runs look for it"""
    r, _ = archive_get("/submit/?" + urllib.parse.urlencode({"url": url}), route=submit_route())
    return r.status_code


def mark_excerpts(days=ARCHIVE_DAYS):
    """posts read before 15 Oct 2026 on an ARCHIVE_PAGES outlet whose text is shorter than EXCERPT_CHARS → excerpt
    (the archive job then looks for them); → rows marked"""
    since = (datetime.now(HKT) - timedelta(days=days)).strftime("%Y-%m-%d")
    pat = ARCHIVE_PAGES.pattern  # the same syntax in Postgres regular expressions
    return corpus.db().execute(
        """UPDATE posts SET text_via = 'excerpt' WHERE thread IS NULL AND day >= %s AND coalesce(text_via, 'feed') = 'feed'
           AND cached_at IS NOT NULL AND length(coalesce(text, '')) < %s AND url ~ %s""",
        (since, EXCERPT_CHARS, pat)).rowcount


def archive_candidates(limit=ARCHIVE_MAX, days=ARCHIVE_DAYS):
    since = (datetime.now(HKT) - timedelta(days=days)).strftime("%Y-%m-%d")
    return corpus.db().execute(
        f"""SELECT p.id, p.url, p.text, a.looks, a.submitted_at FROM posts p LEFT JOIN archive_scan a ON a.post_id = p.id
           WHERE p.text_via = 'excerpt' AND p.day >= %s AND coalesce(a.status, 'pending') = 'pending'
             AND {corpus.seen_cond("p", "archive")}
           ORDER BY p.id DESC LIMIT %s""", (since, limit)).fetchall()


def _scan(pid, **kw):
    cols = ", ".join(kw)
    corpus.db().execute(
        f"INSERT INTO archive_scan (post_id, {cols}) VALUES (%s, {', '.join(['%s'] * len(kw))}) "
        f"ON CONFLICT (post_id) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in kw)}",
        (pid, *kw.values()))


def save_archive(pid, text, snapshot):
    """the archived copy's text when it is longer than what the post has (the excerpt)"""
    import cache_fetch
    r = corpus.db().execute("SELECT text FROM posts WHERE id=%s", (pid,)).fetchone()
    if not r or len((r["text"] or "").strip()) >= len(text.strip()):
        return False
    corpus.set_text(pid, text.strip()[:cache_fetch.TEXT_MAX], via="archive", via_url=snapshot)
    import events  # the live dashboard's Full text panel
    events.emit("fulltext", pid, ok=True, chars=len(text.strip()), head=text.strip()[:700], via="archive", route="archive.today")
    return True


def archive_run(limit=ARCHIVE_MAX, days=ARCHIVE_DAYS):
    """one run of the archive job (CronJob paywall): every excerpt post of the last `days` days still pending"""
    from collections import Counter
    stats = Counter()
    route = lookup_route()
    s, _, _ = archive_session(route)
    now = datetime.now(HKT).isoformat(timespec="seconds")
    start = time.monotonic()
    down = False
    for r in archive_candidates(limit, days):
        if down or time.monotonic() - start > ARCHIVE_BUDGET:
            stats["next_run"] += 1
            continue
        looks = (r["looks"] or 0) + 1
        try:
            snap, text = archive_lookup(r["url"], s, route)
            s, _, _ = archive_session(route)  # a solve inside renews the cookies
            if snap:
                saved = save_archive(r["id"], text, snap) if text else False
                _scan(r["id"], looks=looks, status="done", snapshot=snap, at=now, chars=len(text))
                stats["archived" if saved else "snapshot_no_more_text"] += 1
            elif not r["submitted_at"] and stats["submitted"] >= SUBMIT_MAX:
                stats["submit_next_run"] += 1
                continue
            elif not r["submitted_at"]:
                code = archive_submit(r["url"])
                _scan(r["id"], looks=looks, status="pending", submitted_at=now, at=now, error=f"submitted: HTTP {code}")
                stats["submitted"] += 1
            else:
                _scan(r["id"], looks=looks, status="pending" if looks < ARCHIVE_LOOKS else "none", at=now)
                stats["waiting" if looks < ARCHIVE_LOOKS else "none"] += 1
        except resi.ProxyOut as e:  # IPRoyal out of traffic (resi.py): a pause, no look counted; the rest wait
            stats[resi.STATUS] += 1
            down = True
            print("archive: proxy out of traffic:", repr(e)[:160], flush=True)
            continue
        except Unreachable as e:  # the IP is shut out for now: stop, the rest wait with their looks
            stats["unreachable"] += 1
            down = True
            print("archive:", e, flush=True)
            continue
        except Blocked as e:
            _scan(r["id"], looks=looks, status="pending" if looks < ARCHIVE_LOOKS else "failed", at=now, error=str(e)[:300])
            stats["blocked"] += 1
            print("archive:", r["url"][:100], e, flush=True)
        except Exception as e:
            _scan(r["id"], looks=looks, status="pending" if looks < ARCHIVE_LOOKS else "failed", at=now, error=repr(e)[:300])
            stats["failed"] += 1
            print("archive:", r["url"][:100], repr(e)[:150], flush=True)
        time.sleep(ARCHIVE_GAP)
    print("archive:", dict(stats), flush=True)
    return dict(stats)


if __name__ == "__main__":
    if sys.argv[1:2] == ["archive"]:  # python3 paywall.py archive [DAYS]
        with corpus.lock("paywall:archive-run", wait=False) as got:
            if got:
                with corpus.stage("archive"):  # a restart starts from now (corpus.STAGES): the backlog waits
                    archive_run(days=int(sys.argv[2]) if len(sys.argv) > 2 else ARCHIVE_DAYS)
            else:
                print("archive: another run holds the lock", flush=True)
    elif sys.argv[1:2] == ["mark"]:  # python3 paywall.py mark [DAYS]: earlier reads of paywalled outlets → excerpts
        print("marked:", mark_excerpts(int(sys.argv[2]) if len(sys.argv) > 2 else ARCHIVE_DAYS))
    else:
        import cache_fetch
        for u in sys.argv[1:]:
            meta = {}
            try:
                t, _, _ = cache_fetch.fetch_one(u, meta=meta)
            except Exception as e:
                t = f"FAILED {e!r}"[:200]
            print(f"== {u}\nvia={meta.get('via')} published={meta.get('published')} chars={len(t)}\n{t[:600]}\n")
