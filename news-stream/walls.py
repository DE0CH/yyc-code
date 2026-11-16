#!/usr/bin/env python3
"""Robot walls that are a proof of work, passed in Python (no browser): the readers then get the real page.

  - SiteGround's sgcaptcha (集誌社 thecollectivehk.com, 2026-10-20). From the cluster's IP every page answers HTTP 202
    with a 370-byte meta refresh to /.well-known/sgcaptcha/ (header sg-captcha: challenge). That page holds
    `sgchallenge="<bits>:<…>:"` and `sgsubmit_url`; its script counts up a big-endian counter (1-4 bytes) appended to
    the challenge string until SHA-1 of the two starts with <bits> zero bits (21 live: about 1 s in Python), then
    GETs sgsubmit_url&sol=<base64 of challenge+counter>&s=<ms>:<hashes>, which sets the cookie _I_. The reader took
    the 202 stub for the article and kept "" (「來源只提供標題」).
  - eQualit.ie Deflect (banjax "sha-inv"; 追光者 pulsehknews.com, 2026-10-20): HTTP 429 「Verifying if your connection
    is secure..」 with the cookie deflect_challenge3 (60 bytes: HMAC 0-20, solution 20-52 with an 8-byte big-endian
    counter at 44-52, expiry 52-60) and new_solver(<bits>); the counter is counted up until SHA-256 of bytes 0-52 has
    <bits> zero bits, the cookie written back, the page asked again. The cookie is bound to the client's IP (24 h).
    Banjax bans an IP that fails many challenges, so each wall is answered once per page, never in a loop.

  - A js_clearance cookie page (中國攝影新聞通訊社 zgsyxwtx.com, 2026-10-21): HTTP 200, 256 bytes, a script that sets
    document.cookie="js_clearance=<its token>" and reloads. The cookie is set in Python and the page asked again
    (Chrome with nomedia's block never reached domcontentloaded behind it: 90 s timeouts on every run).

  - BytePlus WAF's "Security Check in Progress..." (開飯 OpenRice, www.openrice.com, 2026-10-21; server Byte-nginx):
    HTTP 200, 18 KB, an obfuscated script holding atob('<base64 JSON>') = {"v": {"a", "b", "c"}, "s"}; it counts n up
    from 0 until SHA-256(base64-decoded a + str(n)) equals base64-decoded c (n is small: 2 and 4 seen), adds
    "d": btoa(str(n)), writes the JSON back as the cookie _wafchallengeid (base64, Max-Age=1) and reloads; the server
    then answers the page and sets _waftokenid. Chrome looped on it (ERR_TOO_MANY_REDIRECTS on www.openrice.com/,
    ERR_ABORTED in the sites-browser job), Python passes it. An IP that asks many times in a row (dozens of answers
    in minutes) gets ByteDance's slider captcha instead (ttVerifyCenter, verify_scene tob_waf): Unsolved, tried again
    at the site's next turn.

One curl_cffi session per (host, proxy) keeps each wall's cookies, so one answer serves the site's later pages until
the wall asks again. A wall that is not passed raises Unsolved (the full-text readers treat it like a Challenge:
tried again later, never saved as an empty article)."""
import base64, hashlib, json, re, struct, threading, time, urllib.parse

JSC = re.compile(r'var t="([0-9a-f]{16,64})";\s*document\.cookie="js_clearance="\+t')
BYTEPLUS = re.compile(r"atob\('(eyJ[A-Za-z0-9+/=]+)'\)")
BYTEPLUS_MAX = 1000000  # the page's own script stops counting here
SG_STUB = re.compile(r"""/\.well-known/sgcaptcha/[^"'\s>]*""")
TIMEOUT = 20
_sessions, _lock = {}, threading.Lock()


class Unsolved(Exception):
    pass


def forget():
    """drop every kept session (tests)"""
    with _lock:
        _sessions.clear()


def which(body, status=200):
    """the wall a response is, or None"""
    head = (body or "")[:20000]
    if "sgcaptcha" in head and (status == 202 or "sgchallenge" in head or SG_STUB.search(head)):
        return "sgcaptcha"
    if "new_solver(" in head and "deflect" in head.lower():
        return "deflect"
    if len(body or "") < 2000 and JSC.search(head):
        return "js_clearance"
    if len(body or "") < 200000 and "_wafchallengeid" in body and BYTEPLUS.search(body):  # its JSON sits past 20 KB on some pages
        return "byteplus"
    if "ttVerifyCenter" in (body or "") and "tob_waf" in (body or ""):
        return "byteplus_slider"
    return None


def _session(url, proxy):
    from curl_cffi import requests
    key = (urllib.parse.urlparse(url).hostname, proxy)
    with _lock:
        s = _sessions.get(key)
        if s is None:
            s = _sessions[key] = requests.Session(impersonate="chrome")
            if proxy:
                s.proxies = {"http": proxy, "https": proxy}
        return s


def _leading_zero_bits(d):
    n = 0
    for x in d:
        if x:
            return n + 8 - x.bit_length()
        n += 8
    return n


def sg_solve(challenge):
    """SiteGround's proof of work → the bytes whose base64 is the answer (challenge string + counter)"""
    bits = int(challenge.split(":", 1)[0])
    pre, n = challenge.encode(), 0
    while n < 1 << 32:
        d = pre + n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")
        if int.from_bytes(hashlib.sha1(d).digest()[:4], "big") >> (32 - bits) == 0:
            return d
        n += 1
    raise Unsolved("sgcaptcha: no answer")


def _sg(s, url, body, headers):
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    if "sgchallenge" not in body:
        m = SG_STUB.search(body)
        if not m:
            raise Unsolved("sgcaptcha: no challenge link")
        body = s.get(base + m.group(0).replace("&amp;", "&"), headers=headers, timeout=TIMEOUT).text
    ch = re.search(r'sgchallenge="([^"]+)"', body)
    sub = re.search(r'sgsubmit_url="([^"]+)"', body)
    if not ch or not sub:
        raise Unsolved("sgcaptcha: no challenge in the page")
    t0 = time.time()
    d = sg_solve(ch.group(1))
    hashes = int.from_bytes(d[len(ch.group(1).encode()):], "big") + 1
    ms = int((time.time() - t0) * 1000)
    s.get(base + sub.group(1) + ("&" if "?" in sub.group(1) else "?") + "sol="
          + urllib.parse.quote(base64.b64encode(d).decode(), safe="") + f"&s={ms}:{hashes}",
          headers=headers, timeout=TIMEOUT, allow_redirects=False)


def _deflect(s, url, body):
    bits = int(re.search(r"new_solver\((\d+)\)", body).group(1))
    c = s.cookies.get("deflect_challenge3")
    if not c:
        raise Unsolved("deflect: no challenge cookie")
    raw = bytearray(base64.b64decode(urllib.parse.unquote(c)))
    for i in range(1 << 32):
        raw[44:52] = struct.pack(">Q", i)
        if _leading_zero_bits(hashlib.sha256(bytes(raw[:52])).digest()) >= bits:
            break
    host = urllib.parse.urlparse(url).hostname
    s.cookies.set("deflect_challenge3", base64.b64encode(bytes(raw)).decode(), domain=host, path="/")


def byteplus_answer(body):
    """BytePlus WAF's proof of work → the _wafchallengeid cookie value"""
    b = BYTEPLUS.search(body).group(1)
    j = json.loads(base64.b64decode(b + "=" * (-len(b) % 4)))
    a, c = base64.b64decode(j["v"]["a"]), base64.b64decode(j["v"]["c"])
    for n in range(BYTEPLUS_MAX + 1):
        if hashlib.sha256(a + str(n).encode()).digest() == c:
            j["d"] = base64.b64encode(str(n).encode()).decode()
            return base64.b64encode(json.dumps(j, separators=(",", ":")).encode()).decode()
    raise Unsolved("byteplus: no answer")


def get(url, proxy=None, headers=None, timeout=TIMEOUT):
    """url's page through its wall → text; raises Unsolved when the wall still stands after one answer, and an
    urllib.error.HTTPError for any other refusal"""
    import urllib.error
    s = _session(url, proxy)
    h = {"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8", **{k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}}
    r = s.get(url, headers=h, timeout=timeout)
    w = which(r.text, r.status_code)
    if w == "byteplus_slider":
        raise Unsolved("byteplus: slider captcha")
    if w:
        if w == "sgcaptcha":
            _sg(s, url, r.text, h)
        elif w == "byteplus":
            s.cookies.set("_wafchallengeid", byteplus_answer(r.text), domain=urllib.parse.urlparse(url).hostname, path="/")
        elif w == "js_clearance":
            s.cookies.set("js_clearance", JSC.search(r.text).group(1), domain=urllib.parse.urlparse(url).hostname, path="/")
        else:
            _deflect(s, url, r.text)
        r = s.get(url, headers=h, timeout=timeout)
        if which(r.text, r.status_code):
            raise Unsolved(f"{w}: still challenged after one answer")
    if r.status_code >= 400:
        raise urllib.error.HTTPError(url, r.status_code, f"HTTP {r.status_code}", {}, None)
    return r.text
