"""YYC's residential proxy: ONE IPRoyal sub-user, `yycstream`, for every job (Deyao, 2026-10-15).

RESI_PROXY is the sub-user's bare login, http://yycstream:<password>@geo.iproyal.com:12321 (no suffix). Each request
builds its own password suffix on it (IPRoyal's syntax): the exit country (`_country-hk` by default: 香港討論區, 信報,
TVB, Facebook, Instagram, YouTube; GB for Reuters, US/HK for Bloomberg) and, where a job needs one IP for a while, a
sticky session (`_session-<id>_lifetime-<24h|30m>`). Sessions are per job, so jobs never share an IP by accident.

Every proxied byte is counted per site (kv proxy:bytes:<site>:<HKT day | month | total>; resi.count), so the
monitor shows which job used the traffic now that IPRoyal's report has a single line. The counts are wire estimates
(curl's own byte counts where curl_cffi is used, the compressed body + OVERHEAD for urllib, file sizes for yt-dlp);
IPRoyal's usage report stays the figure for expense claims (YYC EXPENSES.md).

Out of traffic (Deyao, 2026-10-15: yycstream ran out three times that day): IPRoyal answers 402 Payment Required (to
the CONNECT of an https request, as the answer itself to a plain-http one). That is a pause, never a failure, for
every proxied job: `ProxyOut` is raised at once (no retries into it), no try is counted, no cursor advances, the item
stays queued, the job's status says `proxy_out` (a source: off 'proxy_out'), and the next run carries on by itself.
One record for every job, kv proxy_out {since, at, message, jobs: {job: at}, notified} (`mark`), one Discord message
per outage (none when the last was under NOTIFY_EVERY ago: a flapping balance), shown as ONE line on the monitor;
the first proxied request that began after the outage did and worked clears it (`ok`).
"""
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
import datefmt

HKT = timezone(timedelta(hours=8))
OVERHEAD = 800  # headers, TLS and proxy overhead per urllib request (estimate)
# host labels that aren't a site's own name (the registrable part): CDNs and short links of a counted site
ALIASES = {"googlevideo": "youtube", "youtu": "youtube", "ytimg": "youtube", "fbcdn": "facebook", "cdninstagram": "instagram"}
SUFFIXES = {"com", "net", "org", "hk", "uk", "co", "ph", "is", "today", "li", "md", "fo", "vn", "gov", "edu", "io", "tw",
            "cn", "jp", "sg", "us"}


def configured():
    return bool(os.environ.get("RESI_PROXY"))


def parts(country="hk", session=None, lifetime=None):
    """→ (scheme, host, port, username, password with the suffix), unquoted; None when RESI_PROXY is not set"""
    base = os.environ.get("RESI_PROXY")
    if not base:
        return None
    u = urllib.parse.urlsplit(base)
    # '_' separates IPRoyal's suffix from the password (its passwords are alphanumeric): every suffix is resi's to set
    pw = urllib.parse.unquote(u.password or "").split("_")[0] + f"_country-{country}"
    if session:
        pw += f"_session-{session}" + (f"_lifetime-{lifetime}" if lifetime else "")
    return u.scheme, u.hostname, u.port, urllib.parse.unquote(u.username or ""), pw


def url(country="hk", session=None, lifetime=None):
    """the proxy URL for one request (or one sticky session) → str, or None when RESI_PROXY is not set"""
    p = parts(country, session, lifetime)
    if not p:
        return None
    scheme, host, port, user, pw = p
    q = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731
    return f"{scheme}://{q(user)}:{q(pw)}@{host}" + (f":{port}" if port else "")


def playwright(country="hk", session=None, lifetime=None):
    """the proxy for a Playwright/Patchright browser: {server} of a local forwarder that adds this login to every
    request (forwarder), or None. Chrome sends proxy credentials only after a 407, and IPRoyal answers an
    unauthenticated CONNECT with 402 (seen 16 Oct 2026), so the browser never got through with the login given to it"""
    p = parts(country, session, lifetime)
    if not p:
        return None
    scheme, host, port, user, pw = p
    return {"server": "http://127.0.0.1:%d" % forwarder(host, port or 80, user, pw)}


_forwarders, _fwd_lock = {}, __import__("threading").Lock()


def _serve(host, port, user, pw, counter=None):
    """a local HTTP proxy server (127.0.0.1, a free port) that passes each connection on to host:port with
    Proxy-Authorization added: a CONNECT tunnel as is after its head, a plain-http request with Connection: close (one
    request per connection, each with the login). counter: a dict whose "bytes" gets every byte sent to or received
    from host:port (what the proxy provider bills)"""
    import base64, socket, socketserver, threading
    auth = b"Proxy-Authorization: Basic " + base64.b64encode(f"{user}:{pw}".encode()) + b"\r\n"
    lock = threading.Lock()

    def add(n):
        if counter is not None:
            with lock:
                counter["bytes"] = counter.get("bytes", 0) + n

    def pipe(a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                add(len(d))
                b.sendall(d)
        except OSError:
            pass
        finally:
            for x in (a, b):
                try:
                    x.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            c, head = self.request, b""
            while b"\r\n\r\n" not in head:
                d = c.recv(65536)
                if not d:
                    return
                head += d
                if len(head) > 65536:
                    return
            top, rest = head.split(b"\r\n\r\n", 1)
            lines = [x for x in top.split(b"\r\n") if not x.lower().startswith((b"proxy-authorization:",))]
            if not lines[0].upper().startswith(b"CONNECT "):
                lines = [x for x in lines if not x.lower().startswith((b"connection:", b"proxy-connection:"))]
                lines.append(b"Connection: close")
            out = b"\r\n".join(lines[:1]) + b"\r\n" + auth + b"\r\n".join(lines[1:]) + b"\r\n\r\n" + rest
            try:
                u = socket.create_connection((host, port), timeout=30)
            except OSError:
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                return
            u.settimeout(None)
            u.sendall(out)
            add(len(out))
            t = threading.Thread(target=pipe, args=(u, c), daemon=True)
            t.start()
            # upstream → client is counted in that thread; client → upstream here
            pipe(c, u)
            t.join(300)
            u.close()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = allow_reuse_address = True

    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def forwarder(host, port, user, pw):
    """a local HTTP proxy (127.0.0.1, its port returned; one per login and process) to host:port with the login added
    (_serve)"""
    key = (host, port, user, pw)
    with _fwd_lock:
        if key not in _forwarders:
            _forwarders[key] = _serve(host, port, user, pw).server_address[1]
        return _forwarders[key]


@__import__("contextlib").contextmanager
def metered_url(proxy):
    """like metered, for any proxy URL with its login (the ISP IP: media.youtube_clip_audio) → yields (local proxy URL,
    {"bytes": n}); n is final when the block ends"""
    u = urllib.parse.urlsplit(proxy)
    n = {"bytes": 0}
    srv = _serve(u.hostname, u.port, urllib.parse.unquote(u.username or ""), urllib.parse.unquote(u.password or ""), n)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", n
    finally:
        srv.shutdown()
        srv.server_close()


def count_isp(n):
    """add n bytes sent to or received from YYC's IPRoyal ISP IP (order #85121454) for this HKT day, month and in total,
    and per job and hour; apart from yycstream's proxy:bytes (the ISP IP is not the per-GB pool)"""
    n = int(n or 0)
    if n <= 0:
        return
    import corpus, costlog
    t = datetime.now(HKT)
    for k in (t.strftime("%Y-%m-%d"), t.strftime("%Y-%m"), "total"):
        corpus.kv_add(f"isp:bytes:{k}", n)
    corpus.kv_add(f"isp:jobbytes:{costlog.job()}:{t.strftime('%Y-%m-%dT%H')}", n)


@__import__("contextlib").contextmanager
def metered(country="hk", session=None, lifetime=None):
    """a proxy URL for one piece of work whose traffic is measured on the wire: a local forwarder to RESI_PROXY that
    counts every byte to and from IPRoyal → yields (proxy URL, {"bytes": n}); n is final when the block ends. The
    caller counts it (resi.count): media.py's YouTube subtitle fetch (Deyao, 2026-10-16: "measure bytes per video")"""
    p = parts(country, session, lifetime)
    if not p:
        raise RuntimeError("RESI_PROXY not set")
    _, host, port, user, pw = p
    n = {"bytes": 0}
    srv = _serve(host, port, user, pw, n)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", n
    finally:
        srv.shutdown()
        srv.server_close()


def session_id(n=8):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


# ---- byte counting ----

def site(url_or_label):
    """a URL → its site's short name (www1.hkej.com → hkej, m.discuss.com.hk → discuss, archive.ph → archive);
    anything without '://' is already a label"""
    s = str(url_or_label or "")
    if "://" not in s:
        return s or "other"
    host = (urllib.parse.urlsplit(s).hostname or "").lower()
    labels = [x for x in host.split(".") if x]
    while len(labels) > 1 and labels[-1] in SUFFIXES:
        labels.pop()
    name = labels[-1] if labels else "other"
    return ALIASES.get(name, name)


def count(n, where):
    """add n proxied bytes to the site of `where` (a URL or a label) for this HKT hour, day, month and in total"""
    n = int(n or 0)
    if n <= 0:
        return
    import corpus
    t = datetime.now(HKT)
    label = site(where)
    for k in (t.strftime("%Y-%m-%dT%H"), t.strftime("%Y-%m-%d"), t.strftime("%Y-%m"), "total"):
        corpus.kv_add(f"proxy:bytes:{label}:{k}", n)
    import costlog  # per job and site per hour (the cost profile)
    corpus.kv_add(f"proxy:jobbytes:{costlog.job()}:{label}:{t.strftime('%Y-%m-%dT%H')}", n)


def by_site(t=None):
    """{site: {today_mb, month_mb}} for the HKT day and month of t (default now), biggest month first"""
    import corpus
    t = t or datetime.now(HKT)
    day, month = t.strftime("%Y-%m-%d"), t.strftime("%Y-%m")
    out = {}
    for k, v in corpus.db().execute("SELECT k, v FROM kv WHERE k LIKE 'proxy:bytes:%%' AND (k LIKE %s OR k LIKE %s)",
                                    ("%:" + day, "%:" + month)):
        label, period = k[len("proxy:bytes:"):].rsplit(":", 1)
        row = out.setdefault(label, {"today_mb": 0.0, "month_mb": 0.0})
        row["today_mb" if period == day else "month_mb"] = round(float(v) / 1e6, 4)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["month_mb"]))


# ---- out of traffic: a pause ----

# every wording the clients give for IPRoyal's 402: urllib 'Tunnel connection failed: 402 Payment Required', curl
# 'CONNECT tunnel failed, response 402', yt-dlp quoting either; a 407 that names the traffic/balance
OUT_RE = re.compile(r"Tunnel connection failed: 402|CONNECT tunnel failed, response 402|402 Payment Required"
                    r"|\b407\b[^\n]{0,160}(traffic|balance|bandwidth)", re.I)
STATUS = "proxy_out"
NOTIFY_EVERY = timedelta(minutes=30)
JOB = os.environ.get("YYC_JOB") or os.path.splitext(os.path.basename(sys.argv[0] or "python"))[0] or "python"
OK_EVERY = 30  # seconds between two looks at the record from one process (ok())
_ok_checked = 0.0


class ProxyOut(Exception):
    """IPRoyal refused the request for traffic (402): a pause, never a failed try"""


def _texts(e):
    seen = 0
    while e is not None and seen < 6:
        yield e if isinstance(e, str) else repr(e)
        if isinstance(e, str):
            return
        e, seen = e.__cause__ or e.__context__, seen + 1


def is_out(e, proxied=False):
    """is this error (exception or text) IPRoyal out of traffic? A plain 402 ANSWER (a plain-http URL through the
    proxy: urllib's HTTPError 402, a curl response 402) only when `proxied`, and confirmed by a probe through the proxy
    (a site may say 402 itself)"""
    if isinstance(e, ProxyOut):
        return True
    if any(OUT_RE.search(t) for t in _texts(e)):
        return True
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    return bool(proxied and code == 402 and probe())


def probe(timeout=15):
    """does the proxy itself answer 402 now? One tiny request through it (an https CONNECT to icanhazip)"""
    p = url("hk")
    if not p:
        return False
    try:
        o = urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))
        o.open("https://ipv4.icanhazip.com/", timeout=timeout).read()
        return False
    except Exception as e:  # noqa: BLE001
        return any(OUT_RE.search(t) for t in _texts(e))


def check(e, proxied=True):
    """inside an except block of a proxied request: IPRoyal out of traffic → record it and raise ProxyOut (from e);
    anything else returns (the caller handles it as before)"""
    if isinstance(e, ProxyOut):
        raise e
    if is_out(e, proxied):
        raise mark(repr(e)[:300]) from e


def check_browser(e):
    """a Playwright/Chrome error on the proxy: Chrome names no status (net::ERR_TUNNEL_CONNECTION_FAILED), so a probe
    through the proxy tells whether it is out of traffic → raises ProxyOut, else returns"""
    if isinstance(e, ProxyOut):
        raise e
    t = repr(e)
    if is_out(e) or (re.search(r"ERR_TUNNEL_CONNECTION_FAILED|ERR_PROXY_CONNECTION_FAILED|ERR_HTTP_RESPONSE_CODE_FAILURE", t)
                     and probe()):
        raise mark(t[:300]) from e


def mark(message, job=None):
    """record that the proxy is out of traffic (kv proxy_out) and tell Deyao once per outage → the ProxyOut to raise.
    The stage this thread works for is blocked: once traffic is back it starts from then (corpus.stage)"""
    import corpus, notify
    job = job or JOB
    corpus.stage_blocked("proxy out of traffic")
    t = datetime.now(HKT).isoformat(timespec="seconds")
    try:
        with corpus.lock("proxy_out"):
            rec = corpus.kv_get("proxy_out") or {"since": t, "notified": False, "jobs": {}}
            rec.update(at=t, message=str(message)[:300])
            rec.setdefault("jobs", {})[job] = t
            if not rec.get("notified"):
                told = corpus.kv_get("proxy_out_told")
                recent = told and datetime.fromisoformat(told) > datetime.now(HKT) - NOTIFY_EVERY
                if recent:
                    rec["notified"] = True
                else:
                    print("IPRoyal yycstream out of traffic:", message, flush=True)
                    corpus.kv_set("proxy_out_told", t)
                    rec["notified"] = notify.deyao(
                        "Waiting on you: IPRoyal sub-user yycstream is out of traffic (402 Payment Required) since "
                        f"{datefmt.when(rec['since'])} HKT. Every proxied job (信報, 香港討論區, Facebook, Instagram, transcripts, "
                        "paywalled pages) is paused, nothing is given up; they carry on by themselves once yycstream "
                        "has traffic again.") or True
            corpus.kv_set("proxy_out", rec)
    except Exception as ex:  # noqa: BLE001: the record never stops the pause itself
        print("proxy_out: record failed:", repr(ex)[:160], flush=True)
    return ProxyOut(f"IPRoyal yycstream out of traffic (402 Payment Required): {str(message)[:200]}")


def ok(since=None):
    """a proxied request worked: clear the record when the request began after the outage did (unix time `since`,
    default now). Looks at the record at most every OK_EVERY seconds per process"""
    global _ok_checked
    if time.time() - _ok_checked < OK_EVERY:
        return
    _ok_checked = time.time()
    import corpus
    try:
        rec = corpus.kv_get("proxy_out")
        if rec and (since or time.time()) >= datetime.fromisoformat(rec["since"]).timestamp():
            corpus.kv_set("proxy_out", None)
            print("IPRoyal yycstream answers again: proxy_out cleared", flush=True)
    except Exception as ex:  # noqa: BLE001
        print("proxy_out: clear failed:", repr(ex)[:160], flush=True)


def stopped():
    """the current proxy_out record, or None"""
    import corpus
    return corpus.kv_get("proxy_out") or None


def wire(r):
    """a curl_cffi response's bytes on the wire (curl's own counts: request, compressed body, headers)"""
    n = (getattr(r, "request_size", 0) or 0) + (getattr(r, "download_size", 0) or 0) + (getattr(r, "header_size", 0) or 0)
    return n or len(getattr(r, "content", b"") or b"") + OVERHEAD


def session(proxy=None, impersonate="chrome", label=None):
    """a curl_cffi Session that counts every proxied request (session-wide `proxy`, or a request's own proxies=)
    under its URL's site (or `label`). A stream=True response is counted by the caller (resi.count), since its
    body is read later."""
    from curl_cffi import requests

    class Counting(requests.Session):
        def request(self, method, url, *a, **k):
            proxied = bool(k.get("proxies") or self.proxies)
            t0 = time.time()
            try:
                r = super().request(method, url, *a, **k)
            except Exception as e:
                if proxied:
                    check(e)  # IPRoyal out of traffic → ProxyOut, at once
                raise
            if proxied:
                if getattr(r, "status_code", None) == 402 and is_out(r, proxied=True):  # a plain-http URL: the 402 is the answer
                    raise mark(f"HTTP 402 Payment Required through the proxy: {url[:120]}")
                ok(t0)
                if not k.get("stream"):
                    count(wire(r), label or url)
            return r

    return Counting(impersonate=impersonate, proxies={"http": proxy, "https": proxy} if proxy else None)


def count_browser(ctx, label):
    """count a Playwright browser context's (or browser's page) traffic through the proxy under `label`: every
    finished request's sizes (headers + body, as the browser reports them) → a function that writes the total"""
    got = [0]

    def done(req):
        try:
            s = req.sizes()
            got[0] += sum(int(s.get(k) or 0) for k in ("requestHeadersSize", "requestBodySize",
                                                        "responseHeadersSize", "responseBodySize"))
        except Exception:  # noqa: BLE001: a request the browser dropped has no sizes; counting never stops the job
            pass
    ctx.on("requestfinished", done)

    def flush():
        count(got[0], label)
        got[0] = 0
    return flush
