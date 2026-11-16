"""WiseSearch relay (Deyao, 2026-10-13; the full read-only WiseSearch since 2026-10-29: "full feature relay for Wisers
… only the read-only endpoints, do not relay the write endpoints (e.g. bookmarks) … do not relay tracking components").
Runs WiseSearch on YYC's WisersOne account for the page https://newswatch.yyc-internal.com/app/wisers/ (web-app/src/routes/
-wisers/, the edge app), yyc-mcp's wisers_* tools and YYC AI (through yyc-mcp), through a JSON API at /app/wisers/api/
(yyc-edge sends that path to the cluster; the tunnel to this Deployment). Its own Access app lets in YYC's users (the
one access catalogue, the store's YYC_CATALOGUE_JSON), and every request's Access token is checked here again (signature, issuer,
ACCESS_AUD = that app); an allowed Access service token (WISERS_SERVICE_CLIENT_IDS) and yyc-mcp inside the cluster
(an internal token, straight to Service wisers) name the person in X-YYC-User.

Every WisersOne call is a row of paths.py (what it is, what it costs); anything else is refused, so no write (folders,
tags, saved searches, search history: createSearchHistoryFlag is always false), no tracking call and no e-mail reaches
Wisers, and the relay's browser never loads WisersOne's tag manager or analytics (paths.TRACKING_HOSTS).

  GET api/config                     the account's feature switches (getUserCustomConfig), a day
  GET api/options                    the form's options: media trees, negative categories, hot picks, databases, defaults
  GET api/labels?group=&id=          one level of a label tree; api/labels/suggest?group=&q=; api/labels/names?ids=
  GET api/suggest?q=                 keyword autocomplete
  GET api/media?q=&page=&lang=       the media picker; api/media/names?ids=; api/media/<id> a publication's profile
  GET api/check?…                    the search's checks (stop words, illegal characters, the date limit): no search
  GET api/search?…[&reason=][&fresh=1]  one search (query.criteria() lists the parameters) as a job: answers within
                                     POLL_SECS: {"job": {id, state running|ok|err, …}, "result": {items (query.item),
                                     total, …} | null, "failure": {message, outcome, charged} | null}
  GET api/job/<id>?wait=20           the same for a job, waiting up to `wait` (at most POLL_SECS) for it to end
  GET api/derived?job=&kind=…        a call scoped to the job's search (its criteria only; cached per job):
                                     counts (the tabs), media / match (the filter panel), labels&type=&id=, newscount&id=,
                                     hot / hot_negative (hot keywords), similar&doc=&key=, excerpt&doc=, merge (similar
                                     counts of the job's items), reports&page=, recommended
  GET api/doc/<docId>?job=&style=&reason=   one document: YYC's corpus first (fulltext.py), then the documents already
                                     read, then Wisers (click quota); the heading always from Wisers' search item
  GET api/doc/<docId>/images         the document's images: the image quota gate once per document, then how many
  GET api/doc/<docId>/image/<n>?size=thumb|full   one image (kept in the store for good)
  POST api/download {docs, content, format, highlight}   a download as a job; GET api/download/<id>[?file=1]
  GET api/log                        the search log and today's / this month's counts; api/log/calls every other call
  GET api/saved, api/saved/<file>    the saved responses (no new search); api/raw/<file> one verbatim
  GET /healthz, /livez               readiness (503 while draining), liveness
  GET :WISERS_STATS_PORT/stats       counts and the session's state, for the monitor

Costs (Deyao, 2026-10-20: every search costs the account quota; 2026-10-29: full text, "log it"): nothing repeats a
search or an open by itself; a call that timed out or whose connection broke may have been counted ("charged": "maybe")
and ends as an error; only a call Wisers refused because the session was over (a redirect to its login page, or its
E4000x codes, HTTP 401: not run) runs again, once, after the login. Searches are lines of searches.jsonl, every other Wisers call
(documents, images, downloads, the derived calls, lookups) a line of calls.jsonl, with who, why, what and the outcome.
Searches are jobs (Deyao, 2026-10-20: "requests time out too fast"): jobs live in the store's jobs/ (Postgres), so the
next pod of a rollout answers for a job the old one finishes while draining.

All its state is in Postgres (store.py; Deyao, 2026-11-08: state in the application layer, no Kubernetes volume, so the
relay runs on any node): the session, jobs, saved responses, documents, images, downloads and logs, under the same
names it had on its volume.

One signed-in Chrome page (the session in the store's state.json) makes each call as the app does, from inside the page: POST
/wevo/api {"requests":{"g0":{"resource":"CommonService","operation":"update","params":{"path":…,"criteria":{…}}}}}.
Calls run side by side (at most MAX_INFLIGHT, one search at a time): each starts a fetch in the page and the worker
collects the answers, so a 4-minute search no longer holds up a document or a label tree. Files (images, downloads)
are fetched with the session's cookies outside the page.

The WisersOne account is dedicated to the relay (company decision, Deyao 2026-10-21): no one else signs in to it, and
everything that searches Wisers goes through the relay. The relay holds the session long-term; nothing logs it out or
hands it back by itself (Deyao takes it back by hand when he wants to). WisersOne allows ONE session per user: a login
ends every earlier session (tested 2026-10-16: a copied session was valid, a fresh login, the copy expired). So:
- An expired session logs in again by itself (Deyao, 2026-10-16; kept 2026-10-21): when Wisers sends the page to its
  login page (on opening it, or on a call: a redirect, seen with fetch redirect "manual", an E4000x code, or HTTP 401:
  2026-11-06, every call 401 with an empty body for nine hours while the relay still said "held"), the worker
  waits for the calls in flight, first reopens from state.json if that is newer than what it loaded (a manual
  login.py, the previous pod), else logs in (login.py's login(), 2Captcha), saves the session and runs each refused call
  once more (they were not run). Logins are at least LOGIN_GAP seconds apart (default 60); a login that breaks (the
  page doesn't load) is tried once more at once. The login page is never waited on for "networkidle": it long-polls its
  WeChat QR login, so its network is never quiet for long (2026-11-05: two logins ran out at 60 s that way). When the
  login fails the relay is "logged out": each call answers EXPIRED, the stats port says so and the monitor pings Deyao
  once (Discord). A session saved by a manual login.py while logged out is opened by the idle worker within seconds.
- The health check (Deyao, 2026-11-05): every PROBE_SECS (default 300) the leader makes one free call (PROBE_PATH,
  getUserCustomConfig: no quota), so an expired session is found and logged in again in the background, not on a staff
  member's call; logged out, the same check retries the login after LOGIN_GAP, doubling up to RETRY_MAX. Its result
  (ok, seconds, failures in a row) is in session.json; the stats port adds the failed calls of the last 15 minutes and
  the logins of the last hour, and the monitor alerts on them (a failed check from the second in a row: one alone
  was noise, 2026-11-11). A failed check's events.jsonl line carries the page's own network trace (Playwright's request
  events: the /wevo/api request sent, whether headers came, whether on a reused connection, the remote address),
  so a hang says where it was; the check that answers after failed ones is a line too (after_fails).
- The session's state (held / logged_out, since when, the last call Wisers answered, the last login, the last health
  check) is kept in the store's session.json and served on the stats port (the monitor's WiseSearch panel); its story
  is the store's events.jsonl: each expiry (how Wisers said so, seconds since the last answered call and since the last
  login), each login (ok, error, seconds) and each failed health check, with the address Wisers saw (the egress pool's).
- Deploys hand the session over instead of logging in twice (Deyao, 2026-10-16: "No downtime"): only the holder of the
  leader lock (a Postgres advisory lock, podlock.py: taken again after a dropped connection; a pod that finds another
  holder then exits) drives Wisers. A new pod is ready at once (its
  requests wait in its queue); the old one, on SIGTERM, answers 503 on /healthz, keeps serving for WISERS_DRAIN_SECS
  (default 5), finishes what is queued and in flight, saves the session, closes its browser and releases the lock; the
  new pod takes the lock and opens the saved session (no login). tests/test_wisers_rollout.py.
Manual check or login: kubectl -n yyc exec deploy/wisers -- python3 /app/wisers/login.py /tmp/login

  python3 news-stream/wisers/relay.py [--port 8899] [--ns wisers] [--prefix /app/wisers/] [--host 0.0.0.0]
  (env: DATABASE_URL the store, WISERS_STORE_NS its namespace (tests use their own), WISERS_PREFIX,
  WISERS_HOST, WISERS_DRAIN_SECS, WISERS_SEARCH_SECS, WISERS_STATS_PORT, WISERS_LOGIN_GAP, WISERS_PROBE_SECS (0 = off), WISERS_PG_* (fulltext.py),
  WISERS_SERVICE_CLIENT_IDS, WISERS_INTERNAL_TOKENS, WISERS_AI_CAP)
"""
import argparse, base64, faulthandler, hashlib, html, http.client, json, os, queue, re, secrets, signal, socket, threading, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fulltext, paths, query  # wisers/, next to this file
import login as wlogin
import store

STATE, HOME, on_login = wlogin.STATE, wlogin.HOME, wlogin.on_login
ORIGIN = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(HOME))
LOGIN_GAP = float(os.environ.get("WISERS_LOGIN_GAP", "60"))
DRAIN_SECS = float(os.environ.get("WISERS_DRAIN_SECS", "5"))
SEARCH_SECS = float(os.environ.get("WISERS_SEARCH_SECS", "240"))  # one Wisers search at most (the in-page fetch is aborted)
CALL_SECS = 90  # any other call
# the health check (Deyao, 2026-11-05): one free call (PROBE_PATH, never cached, costs no quota) every PROBE_SECS, so an
# expired session is found and logged in again in the background before staff need it, and the monitor sees the relay
# answering; while logged out it is also the background retry of the login, after LOGIN_GAP, doubling up to RETRY_MAX
PROBE_SECS = float(os.environ.get("WISERS_PROBE_SECS", "300"))
PROBE_PATH = "getUserCustomConfig"
PROBE_TIMEOUT = 60  # seconds one check may take
RETRY_MAX = 900
LOGIN_TRIES = 2  # a login that breaks (a page that doesn't load) is tried once more at once; a refused one is not
# a CommonService answer with no code and no data is that list being empty, for these paths (getHotPicks: the account
# has no hot picks; it answered {"data": {}, "path": "getHotPicks"} to every call)
EMPTY_OK = {"getHotPicks"}
MAX_INFLIGHT = 6
POLL_SECS = 20  # one HTTP request waits at most this long for a job (well under the tunnel's 100 s)
CACHE_SECS = 86400  # the same search within a day is answered from its saved response (fresh=1 searches again)
# service callers (yyc-mcp, YYC AI) spend at most this many charged calls per person and hour (searches, documents)
AI_CAP = {k: int(v) for k, v in (x.split("=") for x in os.environ.get("WISERS_AI_CAP", "search=30,doc=60").split(","))}
HKT = timezone(timedelta(hours=8))
POD = socket.gethostname()
LOGINS = {"last": 0.0, "count": 0, "ok": None}  # the worker's own logins
PROBE = {"t": 0.0, "fails": 0}  # the last health check (this process)
# What the page's browser saw of its own requests (Playwright's request events), so a call that times out says where
# it hung (2026-11-11: one health check in 10-25 timed out at 60 s while the network and Wisers' edge answered every
# test): key -> {url, method, wall (sent, epoch s), and once known: status, headers (s after sent), reused (an open
# connection), ip (remote address:port), done (s), failed}. The newest NET_MAX kept. (A CDP session of its own made
# the tests' interpreter crash at exit about half the time.)
NET, NET_MAX, NET_LOCK = {}, 400, threading.Lock()
STARTED = time.time()
SESSION = {"state": "unknown", "since": None, "last_ok": None, "last_login": None, "logins": 0, "pod": None}
SESSION_FILE = ""
RUN = {"drain_at": None, "leader": False, "worker_done": threading.Event(), "active": 0, "tracking_blocked": 0}
RUN_LOCK = threading.Lock()
LOG = RAW = JOBDIR = DOCS = IMAGES = FILES = DOCINDEX = ""
COOKIES = {"header": "", "at": 0.0}  # the session's cookies for Wisers' host, for file fetches outside the page


def configure(ns=None):
    """the relay's state, in Postgres (store.py; Deyao, 2026-11-08: no volume): searches.jsonl, calls.jsonl, events.jsonl,
    raw/, jobs/, docs/, docindex/, images/, files/, session.json, state.json under store namespace `ns` (default
    WISERS_STORE_NS or "wisers"); the leader lock is a Postgres advisory lock"""
    global LOG, RAW, JOBDIR, SESSION_FILE, DOCS, IMAGES, FILES, DOCINDEX
    if ns:
        store.NS = ns
    LOG, RAW, JOBDIR = "", "raw", "jobs"
    DOCS, IMAGES, FILES, DOCINDEX = "docs", "images", "files", "docindex"
    SESSION_FILE = "session.json"


JOBS = queue.Queue()
PREFIX = ""
EXPIRED = "Wisers 登入已失效，自動重新登入亦失敗，請等候管理員重新登入（已通知 Deyao）。"
RECENT = "Wisers 登入已失效：轉接程式一分鐘內已重新登入過一次，暫不再登入。請稍後再試。"
TIMEOUT = "Wisers 沒有在 {secs:.0f} 秒內回應。Wisers 可能已計算這次的額度，所以沒有自動重試。"
NETERR = "與 Wisers 的連線在途中中斷（{err}）。Wisers 可能已計算這次的額度，所以沒有自動重試。"
LOST = "處理這次搜尋的轉接程式已重新啟動，沒有收到 Wisers 的結果。"
QUOTA = {"60001": "公司帳戶的額度已用完。", "60002": "這個 Wisers 用戶的額度已用完。", "60003": "文章閱讀額度已用完。",
         "60004": "圖片額度已用完。", "60005": "多媒體額度已用完。", "60009": "報告額度已用完。"}

# ---- who may use it: Cloudflare Access (its own app on /app/wisers) signs every request; check that signature here
# too, so a request that reaches the pod some other way gets nothing. Off when ACCESS_AUD is unset (a dev box).
ACCESS_TEAM = "https://yyc-hk.cloudflareaccess.com"
ACCESS_AUD = os.environ.get("ACCESS_AUD", "")
# Access service tokens let in as well, "client id=label" (the token's common_name), each naming the
# person it acts for in X-YYC-User. The /app/wisers Access app must also have a policy for the token.
SERVICE_IDS = dict((e.split("=", 1) + ["service"])[:2] for e in (x.strip() for x in os.environ.get("WISERS_SERVICE_CLIENT_IDS", "").split(",")) if e)
# yyc-mcp inside the cluster: a bearer token whose SHA-256 is listed here, "sha256=label", sent to Service wisers:8899
# directly (NetworkPolicy wisers-ingress admits only yyc-mcp and cloudflared). Never through the tunnel: a request with
# an Access token (everything cloudflared forwards to /app/wisers/api/) is judged by that token alone.
INTERNAL = dict((e.split("=", 1) + ["service"])[:2] for e in (x.strip() for x in os.environ.get("WISERS_INTERNAL_TOKENS", "").split(",")) if e)
STAFF = re.compile(r"^[^@\s,;<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_certs = {"at": 0, "keys": {}}


def _b64(x):
    return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))


def _keys():
    if time.time() - _certs["at"] > 3600 or not _certs["keys"]:
        with urllib.request.urlopen(ACCESS_TEAM + "/cdn-cgi/access/certs", timeout=15) as r:
            d = json.load(r)
        _certs["keys"] = {k["kid"]: (int.from_bytes(_b64(k["n"]), "big"), int.from_bytes(_b64(k["e"]), "big"))
                          for k in d["keys"] if k.get("kty") == "RSA"}
        _certs["at"] = time.time()
    return _certs["keys"]


def access_email(token):
    """the caller of a valid RS256 token of this app (aud ACCESS_AUD, issuer the team): its email if it is an allowed
    one, or "service:<label>" for an allowed service token (SERVICE_IDS); None otherwise"""
    try:
        h, p, sig = token.split(".")
        if json.loads(_b64(h)).get("alg") != "RS256":
            return None
        kid = json.loads(_b64(h)).get("kid")
        keys = _keys()
        if kid not in keys:
            _certs["at"] = 0
            keys = _keys()
        n, e = keys[kid]
        k = (n.bit_length() + 7) // 8
        em = pow(int.from_bytes(_b64(sig), "big"), e, n).to_bytes(k, "big")
        want = b"\x00\x01" + b"\xff" * (k - 3 - len(SHA256_PREFIX) - 32) + b"\x00" + SHA256_PREFIX + hashlib.sha256(f"{h}.{p}".encode()).digest()
        if em != want:
            return None
        c = json.loads(_b64(p))
        aud = c.get("aud") if isinstance(c.get("aud"), list) else [c.get("aud")]
        email = (c.get("email") or "").lower()
        if not (ACCESS_AUD in aud and c.get("iss") == ACCESS_TEAM and c.get("exp", 0) > time.time()):
            return None
        if email:
            return email  # the Access app on /app/wisers admitted it: YYC's users list (tofu access.tf, the one catalogue)
        cn = c.get("common_name") or ""
        return f"service:{SERVICE_IDS[cn]}" if cn and cn in SERVICE_IDS else None
    except Exception:
        return None
    return None


class Expired(Exception):
    """Wisers refused the call because the session is over: the call (a search too) was not run. The message is how
    Wisers said so (a redirect to its login page, or its code and message), for the events log"""


class Failed(Exception):
    """the call may have reached Wisers but gave no answer (timeout, broken connection): never run again by itself"""
    def __init__(self, msg, outcome):
        super().__init__(msg)
        self.outcome = outcome


def session_state():
    """the session's state as last recorded (by this pod or the previous one: session.json in the store)"""
    return _read(SESSION_FILE) or dict(SESSION) if SESSION_FILE else dict(SESSION)


def mark(state=None, **k):
    """record the session's state (held | logged_out) and what happened; written to SESSION_FILE for the stats port"""
    if not SESSION["pod"]:  # the first mark of this process: carry over the previous pod's record (a rollout)
        prev = _read(SESSION_FILE) if SESSION_FILE else None
        for key in ("state", "since", "last_ok", "last_login", "logins"):
            if prev and prev.get(key) is not None:
                SESSION[key] = prev[key]
        SESSION["pod"] = POD
    if state and state != SESSION["state"]:
        SESSION.update(state=state, since=iso_hkt(time.time()))
        print(f"Wisers session: {state}", flush=True)
    SESSION.update(k)
    if SESSION_FILE:
        try:
            _write(SESSION_FILE, SESSION)
        except Exception as e:  # noqa: BLE001
            print(f"could not write {SESSION_FILE}: {e}", flush=True)


def exit_ip():
    """the address Wisers sees this pod come from (the pool's proxy, HTTPS_PROXY): for the events log only"""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            return r.read(64).decode().strip()
    except Exception as e:
        return f"unknown ({str(e)[:60]})"


def event(kind, ip=False, **k):
    """one line of events.jsonl: the session's story (expired: how and after how long; login: ok, error, how long;
    probe: a failed health check with the page's network trace, and the first that answers again), so the monitor can count logins and a run of expiries can be explained. With
    ip the line also names the address Wisers saw (looked up in the background, the worker never waits)"""
    row = {"at": iso_hkt(time.time()), "event": kind, "pod": POD, **k}

    def write():
        if ip:
            row["exit_ip"] = exit_ip()
        try:
            store.append(os.path.join(LOG, "events.jsonl"), json.dumps(row, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001: a log line never stops the relay
            print(f"could not write events.jsonl: {e}", flush=True)
    if ip:
        threading.Thread(target=write, daemon=True).start()
    else:
        write()


def _ago(at):
    """seconds since an HKT ISO time of session.json, or None"""
    try:
        return round(time.time() - datetime.fromisoformat(at).timestamp())
    except (TypeError, ValueError):
        return None


def expired(how):
    """Wisers says the session is over: logged with how, how long since the last call it answered and since the last login"""
    mark()  # the previous pod's record carried over
    ll = SESSION.get("last_login") or {}
    print(f"Wisers session over: {how}", flush=True)
    event("expired", how=how[:240], idle_secs=_ago(SESSION.get("last_ok")), since_login_secs=_ago(ll.get("at")), ip=True)


def events_log(within=None):
    """the events of the last `within` seconds (all with None), oldest first"""
    rows = []
    try:
        for line in store.lines(os.path.join(LOG, "events.jsonl")):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                a = _ago(r.get("at"))
                if within is None or (a is not None and a <= within):
                    rows.append(r)
    except Exception as e:  # noqa: BLE001: the database unreachable: no events to show
        print(f"could not read events.jsonl: {e}", flush=True)
    return rows


def relogin(pg, solver=None):
    """log in on page `pg` (its context's cookies are replaced), unless the worker logged in under LOGIN_GAP ago"""
    if time.time() - LOGINS["last"] < LOGIN_GAP:
        mark("logged_out")
        raise RuntimeError(RECENT)
    LOGINS["last"] = t0 = time.time()
    LOGINS["count"] += 1
    mark()  # the previous pod's record carried over first (its login count)
    print("Wisers session expired: logging in", flush=True)
    ok, err, tries = False, None, 0
    while tries < LOGIN_TRIES:
        tries += 1
        err = None
        try:
            ok = wlogin.login(pg, log=lambda m: print(m, flush=True), solver=solver)
            break  # in or refused (3 captcha tries): not tried again now
        except Exception as e:
            print(f"Wisers login failed (try {tries} of {LOGIN_TRIES}): {e}", flush=True)
            ok, err = False, str(e)[:200]
    LOGINS["ok"] = ok
    print(f"Wisers login {'ok' if ok else 'FAILED'}: {pg.url[:120]}", flush=True)
    last = {"at": iso_hkt(time.time()), "ok": ok, "error": None if ok else (err or "still on the login page after 3 tries")}
    event("login", ok=ok, error=last["error"], tries=tries, secs=round(time.time() - t0, 1), ip=True)
    if not ok:
        mark("logged_out", last_login=last, logins=SESSION["logins"] + 1)
        raise RuntimeError(EXPIRED)
    save_state(pg.context)
    mark("held", last_login=last, logins=SESSION["logins"] + 1)


def save_state(ctx):
    wlogin.save_state(ctx)  # state.json in the store: one write, a reader never sees half of it
    OPENED["mtime"] = state_mtime()
    keep_cookies(ctx)


def keep_cookies(ctx):
    """the session's cookies for Wisers' host as one Cookie header (file fetches run outside the page)"""
    try:
        COOKIES["header"] = "; ".join(f"{c['name']}={c['value']}" for c in ctx.cookies(ORIGIN))
        COOKIES["at"] = time.time()
    except Exception as e:
        print(f"could not read the session's cookies: {e}", flush=True)


def state_mtime():
    return store.mtime(STATE) or 0.0


OPENED = {"mtime": 0.0}  # the state.json the current page was opened from (or last saved)


def goto_home(pg):
    pg.goto(HOME, wait_until="domcontentloaded", timeout=40000)
    pg.wait_for_timeout(3000)


def tracking(url):
    """a request to one of paths.TRACKING_HOSTS (WisersOne's tag manager, analytics): never sent"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower() if "://" in (url or "") else ""
    return any(host == h or host.endswith("." + h) for h in paths.TRACKING_HOSTS)


def block_tracking(ctx):
    def handle(route):
        RUN["tracking_blocked"] += 1
        return route.abort()
    ctx.route(tracking, handle)


def open_page(b, old=None, solver=None):
    """a page on WisersOne's home with the saved session, logging in when it has expired"""
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    OPENED["mtime"] = state_mtime()
    ctx = b.new_context(storage_state=wlogin.load_state())
    wlogin.nomedia.block(ctx, allow=wlogin.keep)  # text only (nomedia.py); the login page's captcha is kept
    block_tracking(ctx)  # after: Playwright asks the newest route first (WisersOne's home loads Google Tag Manager)
    pg = ctx.new_page()
    trace_network(ctx, pg)
    try:
        goto_home(pg)
        if on_login(pg.url):
            if SESSION["state"] != "logged_out":  # logged out already: a retry, not another expiry
                expired("opening WisersOne's home: sent to the login page")
            relogin(pg, solver)
            goto_home(pg)
            if on_login(pg.url):
                mark("logged_out")
                raise RuntimeError(EXPIRED)
        keep_cookies(ctx)
        mark("held")
    except Exception:
        ctx.close()
        raise
    return ctx, pg


def trace_network(ctx, pg):
    """record the page's requests in NET from Playwright's own request events (never fails the page: no trace then)"""
    ids = {}  # Request object -> NET key

    def sent(q):
        try:
            u = urllib.parse.urlsplit(q.url)
            with NET_LOCK:
                k = ids[id(q)] = f"r{len(ids)}-{time.time():.3f}"
                NET[k] = {"url": f"{u.netloc}{u.path}"[:120], "method": q.method, "wall": time.time(), "type": q.resource_type}
                while len(NET) > NET_MAX:
                    NET.pop(next(iter(NET)))
        except Exception:  # noqa: BLE001: tracing never breaks a call
            pass

    def response(x):
        try:
            with NET_LOCK:
                r = NET.get(ids.get(id(x.request)))
                if r is None:
                    return
                r.update(status=x.status, headers=round(time.time() - r["wall"], 2))
            t = x.request.timing or {}
            a = x.server_addr() or {}
            with NET_LOCK:  # connectStart -1: the request went on a connection already open (reused)
                r.update(reused=t.get("connectStart", 0) < 0, ip=f"{a.get('ipAddress', '')}:{a.get('port', '')}")
        except Exception:  # noqa: BLE001
            pass

    def ended(q, err=None):
        with NET_LOCK:
            r = NET.get(ids.pop(id(q), None))
            if r is not None:
                r["done"] = round(time.time() - r["wall"], 2)
                if err:
                    r["failed"] = str(err)[:120]

    pg.on("request", sent)
    pg.on("response", response)
    pg.on("requestfinished", lambda q: ended(q))
    pg.on("requestfailed", lambda q: ended(q, q.failure or "failed"))


def net_trace(t0, t1):
    """for a call sent at t0 that gave up at t1 (epoch s): the page's /wevo/api requests sent from 2 s before it until
    t1 (the call itself among them), and how many other requests of the page were still open when it was sent"""
    with NET_LOCK:
        rows = [dict(r) for r in NET.values() if r.get("wall") is not None]
    api = [r for r in rows if t0 - 2 <= r["wall"] <= t1 and r["url"].endswith("/wevo/api")]
    open_then = [r for r in rows if r["wall"] < t0 - 2 and (r.get("done") is None or r["wall"] + r["done"] > t0)]
    out = []
    for r in api:
        r["sent"] = round(r.pop("wall") - t0, 2)
        out.append({k: v for k, v in r.items() if v is not None})
    return {"api": out, "open_before": len(open_then), "open_urls": sorted({r["url"] for r in open_then})[:8],
            "traced": len(rows)}


# ---- calls made from inside the signed-in page: each starts a fetch and leaves its answer in window.__wr[id]
START = """([id, resource, path, c, method, ms]) => {
    window.__wr = window.__wr || {};
    let params;
    if (resource === "CommonService") {
        params = {path: path, criteria: c, timestamp: Date.now()};
        if (method) params.method = method;
    } else { params = {}; params[path] = c; }
    const body = {requests: {g0: {resource: resource, operation: "update", params: params, body: {}}}, context: {}};
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
    window.__wr[id] = null;
    fetch("/wevo/api", {method: "POST", credentials: "include", redirect: "manual", signal: ctl.signal,
        headers: {"content-type": "application/json", "x-requested-with": "XMLHttpRequest"}, body: JSON.stringify(body)})
      .then(async r => {
        if (r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) window.__wr[id] = ["redirect", r.status, r.url, ""];
        else window.__wr[id] = ["ok", r.status, r.url, await r.text()];
      })
      .catch(e => { window.__wr[id] = [e && e.name === "AbortError" ? "timeout" : "neterr", 0, "", String(e)]; })
      .finally(() => clearTimeout(t));
    return true; }"""
COLLECT = """() => { const out = {}; const w = window.__wr || {};
    for (const k of Object.keys(w)) if (w[k] !== null) { out[k] = w[k]; delete w[k]; } return out; }"""


def interpret(resource, res, secs, path=None):
    """an answer collected from the page → (data, text); Expired, Failed or RuntimeError as call() describes"""
    kind, status, url, text = res
    if kind == "redirect" or on_login(url):
        raise Expired("a redirect to the login page" + (f" (HTTP {status})" if status else "") if kind == "redirect"
                      else f"answered from {url[:120]}")
    if kind == "timeout":
        raise Failed(TIMEOUT.format(secs=secs), "timeout")
    if kind == "neterr":
        raise Failed(NETERR.format(err=text[:120]), "neterr")
    if status == 401:  # 2026-11-06 02:45 HKT: an over session answered every call HTTP 401 with an empty body (no redirect)
        raise Expired(f"HTTP 401 {text[:120]}".strip())
    try:
        g0 = json.loads(text)["g0"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(f"Wisers 回應無法讀取（HTTP {status}）：{text[:200]}")
    if resource != "CommonService":
        return g0, text
    d = (g0 or {}).get("data") or {}
    code = str(d.get("code"))
    if re.fullmatch(r"E4000\d", code):  # "System does not have the login record, please re-login." and the like
        raise Expired(f"{code} {str(d.get('message') or '')[:160]}".strip())
    if code == "None" and not d.get("data") and path in EMPTY_OK:
        return d, text
    if code != "50000":
        raise RuntimeError(f"Wisers 回應錯誤：{json.dumps(d, ensure_ascii=False)[:300]}")
    return d, text


def lost_lock():
    """another pod took the leader lock while this one's database connection was gone: two workers must never drive
    Wisers at once, so this process stops (Kubernetes starts it again; it then waits for the lock)"""
    print("leader lock lost to another pod: exiting", flush=True)
    os._exit(3)


def take_lock():
    """block until this process holds the leader lock (the previous pod releases it when it has drained): a Postgres
    advisory lock on a connection of its own (store.leader_lock: podlock), taken again after a dropped connection"""
    lock = store.leader_lock(threading.Event(), on_lost=lost_lock)
    t0 = time.time()
    lock.take()
    RUN["leader"] = True
    print(f"leader lock taken after {time.time() - t0:.1f} s", flush=True)
    return lock


def drained():
    """SIGTERM came at least DRAIN_SECS ago and nothing is queued: the worker may stop"""
    return RUN["drain_at"] is not None and time.time() - RUN["drain_at"] >= DRAIN_SECS and JOBS.empty()


class Call:
    """one queued Wisers call: a paths.py row, its criteria, and a box for the answer
    ("ok", data, text, secs) | ("err", message, outcome)"""
    n = 0

    def __init__(self, path, crit, secs=None, job=None, info=None):
        r = paths.row(path)
        if r is None:  # not a row of paths.py: never sent (a write, tracking, outward, or simply not WiseSearch)
            raise PermissionError(f"not relayed: {path}")
        Call.n += 1
        self.id, self.path, self.crit, self.job = f"c{Call.n}", path, crit, job
        self.resource, self.method, self.kind = r[1], r[2], r[3]
        self.secs = secs or (SEARCH_SECS if self.kind == "search" else CALL_SECS)
        self.box, self.attempt, self.started, self.info = queue.Queue(), 0, None, info


def browser_worker(solver=None):
    """the one thread that drives Wisers (Playwright's sync API is not thread-safe), while holding the leader lock:
    starts up to MAX_INFLIGHT calls in the page and collects their answers; on an expired session it lets the calls in
    flight end, logs in (or opens a newer saved session) once, and sends the refused calls again"""
    try:  # the news-stream image: Patchright + Google Chrome (CHROME); a dev box: Playwright's Chromium
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright
    fd = take_lock()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(**({"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}))
            st = {"ctx": None, "pg": None}
            idle = {"at": 0.0}
            if store.exists(STATE):  # open the saved session now (a rollout's handover: no login while it works)
                try:
                    st["ctx"], st["pg"] = open_page(b, None, solver)
                except Exception as e:
                    print(f"could not open the saved session at start: {e}", flush=True)
            waiting, inflight, refused = [], {}, []

            def reopen():
                old, st["ctx"], st["pg"] = st["ctx"], None, None
                st["ctx"], st["pg"] = open_page(b, old, solver)

            def fail_all(calls, msg, outcome):
                for c in calls:
                    c.box.put(("err", msg, outcome))

            while not (drained() and not inflight and not waiting and not refused):
                # take what is queued
                try:
                    while True:
                        waiting.append(JOBS.get_nowait())
                except queue.Empty:
                    pass
                if not waiting and not inflight and not refused:
                    if SESSION["state"] == "logged_out" and state_mtime() > OPENED["mtime"] and time.time() - idle["at"] > 5:
                        idle["at"] = time.time()  # a session saved by a manual login.py while logged out: open it now
                        try:
                            reopen()
                        except Exception as e:
                            st["ctx"] = st["pg"] = None
                            print(f"could not open the newer saved session: {e}", flush=True)
                    try:
                        waiting.append(JOBS.get(timeout=0.5))
                    except queue.Empty:
                        continue
                # an expired session: once nothing is in flight, a newer saved session or a login, then the refused again
                if refused and not inflight:
                    again = []
                    try:
                        if any(c.attempt >= 3 for c in refused):
                            mark("logged_out")
                            raise RuntimeError(EXPIRED)
                        if state_mtime() > OPENED["mtime"]:  # a newer saved session (login.py, the previous pod)
                            reopen()
                        else:
                            if st["pg"] is None:
                                reopen()
                            else:
                                relogin(st["pg"], solver)
                                goto_home(st["pg"])
                                keep_cookies(st["ctx"])
                        again = refused
                    except Exception as e:
                        if st["pg"] is not None and on_login(st["pg"].url):
                            st["pg"] = None
                        # logged out (the login failed, or one ran under LOGIN_GAP ago): Wisers refused them, not run
                        fail_all(refused, str(e), "logged_out" if str(e) in (EXPIRED, RECENT) else "error")
                    refused = []
                    waiting = again + waiting
                # start what may start
                if waiting and not refused:
                    if st["pg"] is None or "wevo" not in st["pg"].url:
                        if inflight:
                            pass  # never navigate while calls are in flight (it would abort them)
                        else:
                            try:
                                reopen()
                            except Exception as e:
                                fail_all(waiting, str(e), "logged_out" if str(e) in (EXPIRED, RECENT) else "error")
                                waiting = []
                    keep = []
                    for c in waiting:
                        searching = any(x.kind == "search" for x in inflight.values())
                        if st["pg"] is None or len(inflight) >= MAX_INFLIGHT or (c.kind == "search" and searching):
                            keep.append(c)
                            continue
                        try:
                            c.attempt += 1
                            c.started = time.time()
                            if c.job is not None:
                                c.job.setdefault("wisers_at", c.started)  # sent to Wisers (after the queue and any login)
                            st["pg"].evaluate(START, [c.id, c.resource, c.path, c.crit, c.method, int(c.secs * 1000)])
                            inflight[c.id] = c
                        except Exception as e:  # the page itself broke: this call was not sent
                            c.box.put(("err", f"轉接程式的瀏覽器出錯：{str(e)[:160]}", "error"))
                            st["pg"] = None
                    waiting = keep
                # collect answers
                if inflight:
                    try:
                        done = st["pg"].evaluate(COLLECT) if st["pg"] is not None else {}
                    except Exception as e:
                        print(f"could not read answers from the page: {e}", flush=True)
                        done, st["pg"] = {}, None
                    now = time.time()
                    for cid, c in list(inflight.items()):
                        res = done.get(cid)
                        if res is None:
                            if now - c.started > c.secs + 15 or st["pg"] is None:  # the page lost it (closed, navigated)
                                inflight.pop(cid)
                                if c.info is not None:
                                    c.info["net"] = net_trace(c.started, now)
                                c.box.put(("err", TIMEOUT.format(secs=c.secs), "timeout"))
                            continue
                        inflight.pop(cid)
                        if c.info is not None and res[0] in ("timeout", "neterr"):
                            c.info["net"] = net_trace(c.started, now)
                        try:
                            d, text = interpret(c.resource, res, c.secs, c.path)
                            c.box.put(("ok", d.get("data") if c.resource == "CommonService" else d, text, round(now - c.started, 1)))
                            mark("held", last_ok=iso_hkt(now))
                        except Expired as e:  # refused, not run: safe to run once more after the login
                            if not refused and SESSION["state"] != "logged_out":
                                expired(f"{c.path}: {e}")
                            refused.append(c)
                        except Failed as e:
                            c.box.put(("err", str(e), e.outcome))
                        except Exception as e:
                            c.box.put(("err", str(e), "error"))
                    if not inflight and st["ctx"] is not None and not refused:
                        try:
                            save_state(st["ctx"])  # keep the refreshed cookies (the next pod opens from them)
                        except Exception as e:
                            print(f"could not save the session: {e}", flush=True)
                    time.sleep(0.1)
            if st["ctx"] is not None:
                try:
                    save_state(st["ctx"])
                except Exception as e:
                    print(f"could not save the session at drain: {e}", flush=True)
            b.close()
    finally:
        fd.release()
        RUN["leader"] = False
        print("drained: session saved, browser closed, leader lock released", flush=True)
        RUN["worker_done"].set()


def ask(path, crit, secs=CALL_SECS, job=None, info=None):
    """one Wisers call through the worker; its answer within `secs` (+ the queue). `info` (a dict): the worker adds
    "net" (net_trace) when the call times out or breaks"""
    c = Call(path, crit, secs, job, info)
    JOBS.put(c)
    try:
        return c.box.get(timeout=secs + 120)
    except queue.Empty:
        return ("err", f"Wisers 沒有在 {secs:.0f} 秒內回應，請再試一次。", "timeout")


LOOKUPS = {}  # path + criteria -> (time, data): option lists, label trees, media change rarely
LOOKUP_LOCK = threading.Lock()


def lookup(path, crit=None, who=None):
    """a Wisers call that is not a search, cached for its paths.py row's time; every call sent is a calls.jsonl line"""
    r = paths.row(path)
    ttl = r[5] if r else 0
    key = path + json.dumps(crit or {}, sort_keys=True, ensure_ascii=False)
    with LOOKUP_LOCK:
        hit = LOOKUPS.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    t0 = time.time()
    res = ask(path, crit or {})
    call_line(path, who=who, outcome="ok" if res[0] == "ok" else res[2], secs=round(time.time() - t0, 1),
              error=None if res[0] == "ok" else res[1])
    if res[0] != "ok":
        raise RuntimeError(res[1])
    if ttl:
        with LOOKUP_LOCK:
            LOOKUPS[key] = (time.time(), res[1])
    return res[1]


def probe():
    """the health check: one free Wisers call through the worker (never cached, not a calls.jsonl line: it is no one's
    call), its answer and how long it took kept in session.json (the stats port); a failure is an events.jsonl line"""
    t0, info = time.time(), {}
    res = ask(PROBE_PATH, {}, secs=PROBE_TIMEOUT, info=info)
    ok = res[0] == "ok"
    PROBE["t"] = time.time()
    before = PROBE["fails"]
    PROBE["fails"] = 0 if ok else PROBE["fails"] + 1
    # fails: failed checks in a row (the monitor alerts from two: one alone was noise, 2026-11-11)
    rec = {"at": iso_hkt(PROBE["t"]), "ok": ok, "secs": round(PROBE["t"] - t0, 1), "path": PROBE_PATH,
           "outcome": "ok" if ok else res[2], "error": None if ok else str(res[1])[:200], "fails": PROBE["fails"]}
    mark(probe=rec)
    if not ok:
        event("probe", **{k: v for k, v in rec.items() if k != "at"}, **({"net": info["net"]} if info.get("net") else {}))
    elif before:  # the check that answered after failed ones: how long until it did
        event("probe", ok=True, secs=rec["secs"], path=PROBE_PATH, outcome="ok", after_fails=before)
    return rec


def prober():
    """the health check every PROBE_SECS while this pod drives Wisers; logged out: every LOGIN_GAP (doubling after
    each failure, at most RETRY_MAX), which logs in again in the background (staff's calls don't wait for it)"""
    while RUN["drain_at"] is None:
        time.sleep(2)
        if not RUN["leader"] or RUN["drain_at"] is not None:
            continue
        gap = PROBE_SECS
        if SESSION["state"] == "logged_out" or PROBE["fails"]:
            gap = min(RETRY_MAX, max(LOGIN_GAP, 5) * 2 ** min(max(PROBE["fails"] - 1, 0), 6))
        if time.time() - PROBE["t"] < gap:
            continue
        try:
            probe()
        except Exception as e:
            PROBE.update(t=time.time(), fails=PROBE["fails"] + 1)
            print(f"health check failed: {e}", flush=True)


FAILED = ("logged_out", "error", "timeout", "neterr", "lost")


def health(searches=None, window=900):
    """for the stats port: the last health check, the calls (searches too) that failed in the last `window` seconds by
    outcome and the latest of them, and the logins and expiries of the last hour (events.jsonl)"""
    cut, hour = iso_hkt(time.time() - window), 3600
    rows = [dict(r, path=r.get("path") or "search/searchresult") for r in (searches if searches is not None else search_log())
            if not r.get("cached") and (r.get("at") or "") >= cut]
    rows += [r for r in calls_log() if r.get("outcome") != "cached" and (r.get("at") or "") >= cut]
    fails = [r for r in rows if r.get("outcome") in FAILED]
    fails.sort(key=lambda r: r.get("at") or "", reverse=True)
    ev = events_log(hour)
    logins = [e for e in ev if e.get("event") == "login"]
    exp = [e for e in events_log() if e.get("event") == "expired"]
    return {"window_secs": window, "calls": len(rows),
            "failures": {o: sum(1 for r in fails if r.get("outcome") == o) for o in FAILED if any(r.get("outcome") == o for r in fails)},
            "recent_failures": [{k: r.get(k) for k in ("at", "path", "who", "outcome", "error")} for r in fails[:5]],
            "logins_1h": len(logins), "failed_logins_1h": sum(1 for e in logins if not e.get("ok")),
            "expiries_1h": sum(1 for e in ev if e.get("event") == "expired"), "last_expiry": exp[-1] if exp else None,
            "probe": (session_state() or {}).get("probe"), "probe_secs": PROBE_SECS,
            "started": iso_hkt(STARTED), "leader": RUN["leader"]}


def soft(path, crit=None, who=None):
    """a lookup whose failure leaves a gap rather than an error (an option list the account may not have)"""
    try:
        return lookup(path, crit, who)
    except Exception as e:
        return {"error": str(e)[:200]}


def options(who=None):
    """the search form's options"""
    m = lookup("search/mediamessage", {"keyList": [{"key": "type"}, {"key": "category"}, {"key": "region"}]}, who)
    n = lookup("getNegativeKeyWords", None, who)
    return {"media": m, "negative": n.get("keywordList") or [], "hotpicks": soft("getHotPicks", None, who),
            "databases": soft("getWipPackage", None, who), "defaults": soft("preference/defaultsearchoption", None, who),
            "trees": [{"key": t[0], "title": t[1], "group": t[3], "root": t[4]} for t in query.TREES]}


def labels(group, node, who=None):
    d = lookup(f"label/labels/{group}/{node}", None, who)
    return [{"id": x.get("labelId"), "node": x.get("entityId") or x.get("labelId"), "type": x.get("labelTypeId"),
             "name": x.get("name_hk") or x.get("labelName") or x.get("name_en"), "kids": int(x.get("childrenSize") or 0)}
            for x in d.get("subRoots") or []]


LOCK = threading.RLock()  # start_search logs a saved answer while holding it
ACTIVE = {}  # criteria key -> the job this process is running for it (with its "done" Event)


def crit_key(crit):
    return hashlib.sha256(json.dumps(crit, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def _write(path, d):
    store.write_json(path, d)  # one upsert: a reader (this pod or the next) never sees half a file


def _read(path):
    return store.read_json(path)


def job_file(jid):
    return os.path.join(JOBDIR, re.sub(r"[^\w-]", "", jid) + ".json")


def save_job(j):
    _write(job_file(j["id"]), {k: v for k, v in j.items() if k != "done"})


def read_job(jid):
    """a job as stored; one that has been "running" far longer than any search can (its pod died) has failed"""
    j = _read(job_file(jid))
    if j and j.get("state") == "running" and time.time() - j.get("created", 0) > SEARCH_SECS * 2 + 600:
        j.update(state="err", outcome="lost", charged="maybe", error=LOST)
    return j


def start_search(crit, page, who=None, reason="", fresh=False):
    """the job for this search: the one already running for the same criteria (here or on the other pod of a rollout),
    its saved answer of the last CACHE_SECS (unless fresh; logged as an answer from memory, no Wisers search), or a new
    job queued for the worker"""
    key = crit_key(crit)
    keyfile = os.path.join(JOBDIR, "key-" + key + ".json")
    with LOCK:
        j = ACTIVE.get(key)
        if j:
            return j
        last = _read(keyfile)
        prev = read_job(last["job"]) if last else None
        if prev and prev.get("state") == "running":
            return prev  # the previous pod is finishing it (a rollout)
        if prev and not fresh and prev.get("state") == "ok" and time.time() - prev.get("finished", 0) < CACHE_SECS \
                and store.exists(os.path.join(RAW, prev.get("raw") or "-")):
            hit = dict(prev, cached=True, served_at=time.time())
            log_line(hit, who=who, reason=reason, cached=True, secs=0)
            return hit
        jid = time.strftime("%Y%m%d%H%M%S-") + secrets.token_hex(4)
        j = {"id": jid, "key": key, "state": "running", "created": time.time(), "pod": POD, "who": who, "reason": reason,
             "crit": crit, "page": page, "cached": False, "done": threading.Event()}
        save_job(j)
        _write(keyfile, {"job": jid})
        ACTIVE[key] = j
    c = Call("search/searchresult", dict(crit, version=int(time.time() * 1000)), SEARCH_SECS, j)
    JOBS.put(c)
    threading.Thread(target=finish_search, args=(j, c.box), daemon=True).start()
    return j


def finish_search(j, box):
    """wait for the worker's answer (it bounds every call by SEARCH_SECS; logins and the queue come on top), keep the
    response verbatim in raw/, index its items (docindex/), store the job, write the log line"""
    try:
        res = box.get(timeout=SEARCH_SECS * 2 + 600)
    except queue.Empty:
        res = ("err", LOST, "lost")
    crit = j["crit"]
    if res[0] == "ok":  # every response Wisers sent, as sent, with the request: examine later without searching again
        name = time.strftime("%Y%m%d-%H%M%S-") + (re.sub(r"[^\w一-鿿-]+", "_", crit["keyword"])[:40] or "search") + f"-{crit['startIdx']}.json"
        store.write_json(os.path.join(RAW, name), {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "job": j["id"], "who": j.get("who"), "reason": j.get("reason"),
            "request": {"path": "search/searchresult", "criteria": crit}, "response": json.loads(res[2])}, indent=1)
        for i, x in enumerate((res[1] or {}).get("items") or []):  # the reader's heading, whichever source gives the text
            if re.fullmatch(r"[\w-]{1,80}", str(x.get("docId") or "")):
                _write(os.path.join(DOCINDEX, x["docId"] + ".json"), dict(x, _job=j["id"], _i=i))
        j.update(state="ok", raw=name, total=res[1].get("total"), wisers_secs=res[3], outcome="ok", charged="yes")
    else:
        outcome = res[2] if len(res) > 2 else "error"
        # timeout / broken connection: Wisers may have run it; Wisers' own error answer: it got the search
        charged = {"timeout": "maybe", "neterr": "maybe", "lost": "maybe", "logged_out": "no"}.get(
            outcome, "maybe" if j.get("wisers_at") else "no")
        j.update(state="err", error=res[1], outcome=outcome, charged=charged)
    j["finished"] = time.time()
    save_job(j)
    log_line(j, who=j.get("who"), reason=j.get("reason"), cached=False, secs=round(j["finished"] - j["created"], 1))
    with LOCK:
        ACTIVE.pop(j["key"], None)
    j["done"].set()


def wait_job(j, secs):
    """the job once it has ended, or as it is after `secs`"""
    if j.get("state") != "running":
        return j
    t = time.time()
    if "done" in j:  # this process runs it
        j["done"].wait(secs)
        return j
    while time.time() - t < secs:  # the other pod runs it: read its file
        time.sleep(0.5)
        k = read_job(j["id"]) or j
        if k.get("state") != "running":
            return k
    return read_job(j["id"]) or j


def find_job(jid):
    with LOCK:
        j = next((x for x in ACTIVE.values() if x["id"] == jid), None)
    return j or read_job(jid)


def job_answer(j):
    """what the page gets for a job"""
    now = time.time()
    end = j.get("finished") if j.get("state") != "running" else now
    info = {"id": j["id"], "state": j["state"], "cached": bool(j.get("cached")), "created": iso_hkt(j["created"]),
            "elapsed": round((end or now) - j["created"], 1), "queued": j.get("wisers_at") is None and j["state"] == "running",
            "wisers_secs": j.get("wisers_secs"), "pending_jobs": JOBS.qsize()}
    if j.get("cached"):
        info["searched_at"] = iso_hkt(j.get("finished") or j["created"])
    out = {"job": info, "result": None, "failure": None}
    if j["state"] == "ok":
        out["result"] = dict(result_of(j["raw"]), page=j.get("page", 1), cached=bool(j.get("cached")))
    elif j["state"] == "err":
        out["failure"] = {"message": j.get("error"), "outcome": j.get("outcome"), "charged": j.get("charged")}
    return out


def result_of(name):
    """a saved search response as the page reads it: the criteria, the counts, and each item (query.item, raw kept)"""
    r = _read(os.path.join(RAW, name)) or {}
    w = ((r.get("response") or {}).get("g0") or {}).get("data") or {}
    d = w.get("data") or {}
    crit = (r.get("request") or {}).get("criteria") or {}
    items = d.get("items") or []
    return {"at": r.get("at", ""), "crit": crit, "raw": name, "page_size": crit.get("pageSize"),
            "total": d.get("total"), "total_docs": d.get("totalDocCount"), "doc_count": d.get("docCount"),
            "max_docs": d.get("maxDocs"), "media_type": d.get("mediaType"), "media_count": d.get("mediaCount"),
            "progress": d.get("progress"), "criteria_id": d.get("searchCriteriaId"),
            "items": [dict(query.item(x), raw=x) for x in items]}


# ---- the logs (Deyao, 2026-10-20: every search with who ran it, why, what, how many, how long; 2026-10-29: every
# document read from Wisers too, "log it")

def iso_hkt(t):
    return datetime.fromtimestamp(t, HKT).isoformat(timespec="seconds")


DEFAULTS = {"rangeTitle": True, "rangeContent": True, "withinScope": "document", "mediaType": "all", "mediaCategory": "all",
            "mediaRegion": "all", "pubType": "all", "pubLanguage": "all", "sortOrder": "DESC:date", "thesaurusEnable": True,
            "startIdx": 1, "clusterType": "5", "supportExcerpt": True, "matches": "all", "pageSize": 50}
OPTION_KEYS = ["dateRangePeriod", "dateFrom", "dateTo", "rangeTitle", "rangeContent", "withinScope", "sortOrder", "pubType",
               "mediaType", "mediaCategory", "mediaRegion", "pubLanguage", "language", "author", "column", "section",
               "thesaurusEnable", "negativeCategories", "subjects", "listedCompanies", "industries", "companies", "brands",
               "includePubids", "excludePubids", "hotPickIds", "keyWordScope", "labelFilterList", "pubRegion",
               "importantMedia", "matches", "sensitives", "clusterType", "pageSize", "startIdx"]


def options_of(crit):
    """the criteria that differ from WiseSearch's defaults (what the search asked for besides the keyword)"""
    out = {}
    for k in OPTION_KEYS:
        v = crit.get(k)
        if v in (None, "", [], {}) or DEFAULTS.get(k, object()) == v:
            continue
        out[k] = v
    return out


def log_line(j, who, reason, cached, secs):
    crit = j["crit"]
    row = {"at": iso_hkt(time.time()), "job": j["id"], "who": who, "reason": reason or "", "keyword": crit.get("keyword"),
           "page": j.get("page", 1), "options": options_of(crit), "cached": cached, "ok": j.get("state") == "ok",
           "outcome": "cached" if cached else j.get("outcome"), "charged": "no" if cached else j.get("charged"),
           "total": j.get("total"), "raw": j.get("raw"), "secs": secs, "wisers_secs": None if cached else j.get("wisers_secs"),
           "error": None if j.get("state") == "ok" else (j.get("error") or "")[:300],
           "from": crit.get("dateFrom"), "to": crit.get("dateTo"), "type": crit.get("pubType")}
    store.append(os.path.join(LOG, "searches.jsonl"), json.dumps(row, ensure_ascii=False))


def call_line(path, who=None, reason="", outcome="ok", charged=None, secs=None, error=None, **k):
    """one calls.jsonl line: every Wisers call that is not a search (searches are searches.jsonl's)"""
    r = paths.row(path)
    if charged is None:  # free lookups and answers from memory cost nothing; a call that may have reached Wisers: maybe
        if outcome == "cached" or (r and r[4] == "free"):
            charged = "no"
        elif outcome == "ok":
            charged = "unknown"
        else:
            charged = "maybe" if outcome in ("timeout", "neterr") else "no"
    row = {"at": iso_hkt(time.time()), "kind": k.pop("kind", r[3] if r else "file"), "path": path, "who": who,
           "reason": reason or "", "outcome": outcome, "charged": charged, "secs": secs,
           "error": (error or "")[:300] or None, **k}
    store.append(os.path.join(LOG, "calls.jsonl"), json.dumps(row, ensure_ascii=False))


def _hkt(at):
    """a log time as HKT ISO; lines before 2026-10-20 have no zone and are the pod's clock, UTC"""
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return at
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(HKT).isoformat(timespec="seconds")


def search_log():
    """every logged search, newest first; older lines are completed from their saved response, and saved responses
    with no line (searches before the log had them) are added"""
    rows, seen = [], set()
    lines = store.lines(os.path.join(LOG, "searches.jsonl"))
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        r["at"] = _hkt(r.get("at"))
        r.setdefault("who", None)
        r.setdefault("reason", "")
        r.setdefault("outcome", "cached" if r.get("cached") else ("ok" if r.get("ok") else "error"))
        r.setdefault("charged", "no" if r.get("cached") else ("yes" if r.get("ok") else "maybe"))
        if "options" not in r:
            saved = _read(os.path.join(RAW, r["raw"])) if r.get("raw") else None
            crit = (saved or {}).get("request", {}).get("criteria") or {
                "dateRangePeriod": "custom" if r.get("from") else None, "dateFrom": r.get("from"), "dateTo": r.get("to"),
                "pubType": r.get("type")}
            r["options"] = options_of(crit)
        if r.get("raw"):
            seen.add(r["raw"])
        rows.append(r)
    for name in store.listdir(RAW):
        if not name.endswith(".json") or name in seen:
            continue
        saved = _read(os.path.join(RAW, name))
        if not saved:
            continue
        crit = saved.get("request", {}).get("criteria") or {}
        d = ((saved.get("response") or {}).get("g0") or {}).get("data") or {}
        rows.append({"at": _hkt(saved.get("at") or ""), "job": saved.get("job"), "who": saved.get("who"),
                     "reason": saved.get("reason") or "", "keyword": crit.get("keyword"),
                     "page": (int(crit.get("startIdx") or 1) - 1) // int(crit.get("pageSize") or 20) + 1,
                     "options": options_of(crit), "cached": False, "ok": True, "outcome": "ok", "charged": "yes",
                     "total": (d.get("data") or {}).get("total"), "raw": name, "secs": None, "wisers_secs": None,
                     "error": None, "backfilled": "saved response"})
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    return rows


def calls_log(kind=None):
    rows = []
    for line in store.lines(os.path.join(LOG, "calls.jsonl")):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not kind or r.get("kind") == kind:
            rows.append(r)
    rows.reverse()
    return rows


def log_summary(rows, calls=None):
    """searches sent to Wisers (answers from memory left out) today and this month, HKT; documents and images read
    from Wisers, and documents answered from YYC's corpus"""
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    sent = [r for r in rows if not r.get("cached")]
    out = {"today": sum(1 for r in sent if (r.get("at") or "").startswith(today)),
           "month": sum(1 for r in sent if (r.get("at") or "").startswith(today[:7])),
           "today_failed": sum(1 for r in sent if (r.get("at") or "").startswith(today) and not r.get("ok")),
           "all": len(sent), "first": sent[-1]["at"] if sent else None, "last": sent[0]["at"] if sent else None,
           "last_ok": sent[0].get("ok") if sent else None, "day": today}
    calls = calls_log() if calls is None else calls
    for kind in ("doc", "image", "download", "derived"):
        mine = [r for r in calls if r.get("kind") == kind and r.get("served", "wisers") == "wisers" and r.get("outcome") == "ok"]
        out[f"{kind}_today"] = sum(1 for r in mine if (r.get("at") or "").startswith(today))
        out[f"{kind}_month"] = sum(1 for r in mine if (r.get("at") or "").startswith(today[:7]))
    corpus = [r for r in calls if r.get("kind") == "doc" and r.get("served") == "corpus"]
    out["corpus_today"] = sum(1 for r in corpus if (r.get("at") or "").startswith(today))
    out["corpus_month"] = sum(1 for r in corpus if (r.get("at") or "").startswith(today[:7]))
    return out


def capped(who, kind):
    """a service caller (yyc-mcp, YYC AI) over its hourly cap of charged calls for this person"""
    if "(yyc-mcp)" not in (who or "") and "(YYC AI)" not in (who or ""):
        return False
    cap = AI_CAP.get(kind)
    if not cap:
        return False
    since = iso_hkt(time.time() - 3600)
    if kind == "search":
        n = sum(1 for r in search_log() if r.get("who") == who and not r.get("cached") and (r.get("at") or "") >= since)
    else:
        n = sum(1 for r in calls_log(kind) if r.get("who") == who and r.get("served", "wisers") == "wisers" and (r.get("at") or "") >= since)
    return n >= cap


# ---- scoped to a search: always the job's own criteria (a caller cannot send any)

def derived(j, kind, q, who=None):
    """one derived call for job j (cached in jobs/<id>.extra.json, so the other pod of a rollout has it too)"""
    g = lambda k: (q.get(k) or [""])[0].strip()
    crit = dict(j["crit"], createSearchHistoryFlag=False)
    for k in ("version",):
        crit.pop(k, None)
    if kind == "counts":
        path, c = "search/getMediaTypeCount", crit
    elif kind == "media":
        path, c = "search/getSearchResultFilter", crit
    elif kind == "match":
        path, c = "search/getSearchResultMatchFilter", crit
    elif kind == "labels":
        if not (re.fullmatch(r"\w{1,30}", g("type")) and re.fullmatch(r"[\w-]{1,40}", g("id") or "0")):
            raise query.Bad("type / id")
        path, c = f"label/resultfilter/{g('type')}/{g('id') or '0'}", crit
    elif kind == "newscount":
        if not re.fullmatch(r"[\w-]{1,40}", g("id")):
            raise query.Bad("id")
        path, c = f"label/newscount/{g('id')}", None
    elif kind == "hot":
        path, c = "search/getHotKeywordsReport", crit
    elif kind == "hot_negative":
        path, c = "search/getHotNegativeWords", crit
    elif kind in ("similar", "excerpt"):
        if not re.fullmatch(r"[\w-]{1,80}", g("doc")):
            raise query.Bad("doc")
        if kind == "excerpt":
            path, c = "search/getExcerptByDocId", {"docId": g("doc")}
        else:
            c = {k: v for k, v in crit.items() if k != "clusterType"}
            c.update(docId=g("doc"), pageSize="20")
            if g("key"):
                c["clusterAttrKey"] = g("key")
            path = "search/getSimilarDocuments"
    elif kind == "merge":
        res = result_of(j["raw"]) if j.get("raw") else {"items": []}
        ids = [x["cluster_key"] for x in res["items"] if x.get("cluster_key")]
        path, c = "search/searchMergeCount", dict(crit, cluster_ids=ids)
    elif kind == "reports":
        page = int(g("page") or 1) if (g("page") or "1").isdigit() else 1
        path, c = "search/report", dict(crit, startIdx=(page - 1) * 5 + 1, pageSize=5)
    elif kind == "recommended":
        path, c = "search/recommendReport", crit
    else:
        raise query.Bad("kind")
    ck = hashlib.sha256(f"{path}{json.dumps(c, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()[:16]
    extra_file = os.path.join(JOBDIR, re.sub(r"[^\w-]", "", j["id"]) + ".extra.json")
    with LOCK:
        extra = _read(extra_file) or {}
    if ck in extra:
        return extra[ck]
    t0 = time.time()
    res = ask(path, c)
    call_line(path, who=who, outcome="ok" if res[0] == "ok" else res[2], secs=round(time.time() - t0, 1), job=j["id"],
              error=None if res[0] == "ok" else res[1], derived=kind)
    if res[0] != "ok":
        raise RuntimeError(res[1])
    with LOCK:
        extra = _read(extra_file) or {}
        extra[ck] = res[1]
        _write(extra_file, extra)
    return res[1]


# ---- documents: YYC's corpus first, then a document already read, then Wisers (click quota)

def quota_of(d):
    """WisersOne's quota rule (CommonUtil.canOpenDocumentByQuota): {code, soft, message, blocking}"""
    norm = lambda v: str(v or "").strip()
    main = norm(d.get("quotaLimitCode")) or norm(d.get("returnCode"))
    unc = norm(d.get("quotaLimitUnCheckCode") or d.get("quotaLimitUncheckCode"))
    ok = lambda c: c in ("", "50000")
    if not ok(main):
        return {"code": main, "blocking": True, "message": QUOTA.get(main, f"Wisers 額度限制（{main}）。")}
    if not ok(unc):
        return {"code": unc, "blocking": False, "message": QUOTA.get(unc, f"Wisers 額度提示（{unc}）。")}
    return None


ALLOWED_TAGS = {"p", "br", "b", "i", "u", "strong", "em", "table", "thead", "tbody", "tr", "td", "th", "ul", "ol", "li", "h3", "h4"}


def clean_html(s):
    """Wisers' content to an allowlist of tags (no attributes); its hit marks become <mark>"""
    s = re.sub(r'<font class="highlight">(.*?)</font>', lambda m: "\x01" + m.group(1) + "\x02", s or "", flags=re.S)
    s = re.sub(r"<(script|style|iframe|object)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)

    def tag(m):
        close, name = m.group(1), m.group(2).lower()
        return f"<{close}{name}>" if name in ALLOWED_TAGS else ""
    s = re.sub(r"<(/?)([a-zA-Z0-9]+)[^>]*>", tag, s)
    return s.replace("\x01", "<mark>").replace("\x02", "</mark>")


def text_html(text, terms):
    """a corpus post's plain text as paragraphs, the search's terms marked as Wisers marks its hits"""
    out = []
    pat = re.compile("(" + "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True) if t) + ")", re.I) if terms else None
    for para in re.split(r"\n\s*\n|\n", text or ""):
        p = html.escape(para.strip())
        if not p:
            continue
        if pat:
            p = pat.sub(r"<mark>\1</mark>", p)
        out.append(f"<p>{p}</p>")
    return "".join(out)


def terms_of(crit):
    """the words a search looked for (its keyword without operators, and the search-within words)"""
    kw = re.sub(r"\b(AND|OR|NOT)\b|[()\"]", " ", (crit or {}).get("keyword") or "")
    words = [w for w in re.split(r"\s+", kw) if len(w) >= 2]
    return words + [x.get("keyword") for x in (crit or {}).get("keyWordScope") or [] if x.get("keyword")]


DOC_LOCKS = {}


def doc(doc_id, style="all", job=None, who=None, reason=""):
    """one document as the reader shows it"""
    with LOCK:
        lk = DOC_LOCKS.setdefault(doc_id, threading.Lock())
    with lk:  # two people opening the same document at once: one Wisers call
        return _doc(doc_id, style, job, who, reason)


def _doc(doc_id, style, job, who, reason):
    it = _read(os.path.join(DOCINDEX, doc_id + ".json")) or {}
    j = find_job(job) if job else (find_job(it.get("_job")) if it.get("_job") else None)
    terms = terms_of(j["crit"] if j else None)
    head = query.item(it) if it else None
    has = (head or {}).get("has") or {}
    out = {"doc_id": doc_id, "heading": head, "style": style, "job": j["id"] if j else None, "index": it.get("_i"),
           "same_page": None, "images": None, "redirect": None, "quota": None, "from": None, "html": "", "post": None,
           "meta": None}
    if it and it.get("pubType") and it.get("pageNo"):
        try:
            sp = lookup("search/getSamePageDocs", query.same_page_criteria(it), who)
            out["same_page"] = (sp or {}).get("same_page_docs") or []
        except Exception as e:
            out["same_page_error"] = str(e)[:200]
    needs_media = style in ("img", "video", "audio") or has.get("video") or has.get("audio")
    # 1. a document already read from Wisers: free
    cached = _read(os.path.join(DOCS, doc_id + ".json"))
    if cached:
        call_line("search/getDocumentByDocId", who=who, reason=reason, outcome="cached", served="cache", doc=doc_id,
                  headline=(head or {}).get("headline"))
        return fill_from_wisers(out, cached, terms)
    # 2. YYC's corpus: free (not when the reader needs what only Wisers' document carries: video / audio)
    if it and not needs_media:
        m, why = fulltext.find(it)
        if m:
            call_line("search/getDocumentByDocId", who=who, reason=reason, outcome="ok", charged="no", served="corpus",
                      doc=doc_id, post_id=m["post_id"], match=m["match"], headline=(head or {}).get("headline"))
            out.update({"from": "corpus", "html": text_html(m["text"], terms),
                        "post": {"id": m["post_id"], "url": m["url"], "website": m["website"], "match": m["match"]},
                        "images": {"has": bool(has.get("img")), "count": None}})
            return out
        out["corpus_miss"] = why
    if capped(who, "doc"):
        raise RuntimeError(f"這個帳戶過去一小時經 AI 打開的 Wisers 文章已達上限（{AI_CAP.get('doc')} 篇）。")
    # 3. Wisers: click quota (60003)
    t0 = time.time()
    res = ask("search/getDocumentByDocId", {"language": "", "docId": doc_id, "hasImage": "0", "isFavDoc": "0",
                                            "style": style if style in ("all", "text", "img", "web", "video", "audio") else "all",
                                            "key": (j or {}).get("crit", {}).get("searchCriteriaId", "") if j else ""}, CALL_SECS)
    secs = round(time.time() - t0, 1)
    if res[0] != "ok":
        call_line("search/getDocumentByDocId", who=who, reason=reason, outcome=res[2], secs=secs, error=res[1], served="wisers",
                  doc=doc_id, headline=(head or {}).get("headline"))
        raise RuntimeError(res[1])
    d = res[1] or {}
    q = quota_of(d)
    if q and q["blocking"]:  # refused for quota: not read, nothing kept
        call_line("search/getDocumentByDocId", who=who, reason=reason, outcome="quota", charged="no", secs=secs, served="wisers",
                  doc=doc_id, quota=q["code"], headline=(head or {}).get("headline"))
        out["quota"] = q
        return out
    _write(os.path.join(DOCS, doc_id + ".json"), d)
    x = d.get("item") or d.get("document") or {}  # the heading for the log when the search item is not indexed
    call_line("search/getDocumentByDocId", who=who, reason=reason, outcome="ok", charged="yes", secs=secs, served="wisers",
              doc=doc_id, headline=(head or {}).get("headline") or x.get("headline"), source=(head or {}).get("source") or x.get("source"),
              quota=(q or {}).get("code"),
              corpus_miss=out.get("corpus_miss"))
    return fill_from_wisers(out, d, terms, q)


REDIRECT_CODE = "50008"  # displayType: the document is only a link (DocumentDetailApp's REDIRECT_CODE)


def fill_from_wisers(out, d, terms, q=None):
    x = d.get("item") or d.get("document") or {}
    if str(x.get("displayType")) == REDIRECT_CODE and x.get("url"):
        out["redirect"] = query.clean_url(x["url"])
    out["from"] = "wisers"
    out["quota"] = q
    body = x.get("content") or x.get("text") or ""
    if not body and x.get("excerpt"):
        body = x["excerpt"]
    out["html"] = clean_html(body)
    if not out.get("heading"):
        out["heading"] = query.item(x)
    n = int(x.get("imageCount") or 0) if str(x.get("imageCount") or "0").isdigit() else 0
    out["images"] = {"has": n > 0 or bool((out.get("heading") or {}).get("has", {}).get("img")), "count": n or None}
    out["meta"] = {k: x.get(k) for k in ("intDocId", "adValue", "imageSize", "industrys", "brands", "products", "videoUrl",
                                          "audioUrl", "circulation", "wordcount", "pageNo", "section", "columnName")
                   if x.get(k) not in (None, "", [], {})}
    for k in ("videoUrl", "audioUrl"):
        if x.get(k):
            out["meta"][k] = query.clean_url(x[k])
    return out


# ---- images: the quota gate once per document (as WisersOne: checkQuotaExceed img, then writeClickLogByClickType img,
# which books it), then the files kept in the store for good

def image_gate(doc_id, who=None, reason=""):
    """{"ok": True} or {"quota": …}; kept in images/<docId>/gate.json so a document's images are booked once"""
    d = os.path.join(IMAGES, doc_id)
    gate = _read(os.path.join(d, "gate.json"))
    if gate and gate.get("ok"):
        return gate
    if capped(who, "image"):
        raise RuntimeError("這個帳戶過去一小時經 AI 打開的 Wisers 圖片已達上限。")
    t0 = time.time()
    res = ask("search/checkQuotaExceed", {"style": "img", "docId": doc_id})
    if res[0] != "ok":
        call_line("search/checkQuotaExceed", who=who, reason=reason, outcome=res[2], error=res[1], doc=doc_id, served="wisers")
        raise RuntimeError(res[1])
    q = quota_of(res[1] or {})
    if q and q["blocking"]:
        call_line("search/checkQuotaExceed", who=who, reason=reason, outcome="quota", charged="no", doc=doc_id, quota=q["code"], served="wisers")
        return {"quota": q}
    res2 = ask("search/writeClickLogByClickType", {"clickType": "img", "docId": doc_id})
    q2 = quota_of(res2[1] or {}) if res2[0] == "ok" else None
    call_line("search/writeClickLogByClickType", who=who, reason=reason, outcome="ok" if res2[0] == "ok" else res2[2],
              charged="yes" if res2[0] == "ok" else None, secs=round(time.time() - t0, 1), doc=doc_id, served="wisers",
              quota=(q2 or {}).get("code"), error=None if res2[0] == "ok" else res2[1])
    if res2[0] != "ok":
        raise RuntimeError(res2[1])
    if q2 and q2["blocking"]:
        return {"quota": q2}
    gate = {"ok": True, "at": iso_hkt(time.time()), "who": who}
    _write(os.path.join(d, "gate.json"), gate)
    return gate


class FileExpired(Exception):
    pass


def fetch_file(path, method="GET", data=None, secs=60):
    """a file from Wisers' host with the session's cookies (outside the page) → (content type, bytes); a redirect to
    the login page is FileExpired (the worker logs in on its next call)"""
    if not any(path.startswith(f) for f in paths.FILES):
        raise PermissionError(f"not relayed: {path}")
    u = urllib.parse.urlsplit(ORIGIN + path)
    conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=secs)
    try:
        conn.request(method, u.path + (("?" + u.query) if u.query else ""), body=data,
                     headers={"Cookie": COOKIES["header"], "User-Agent": "Mozilla/5.0", "Referer": HOME,
                              **({"Content-Type": "application/x-www-form-urlencoded"} if method == "POST" else {})})
        r = conn.getresponse()
        body = r.read()
        if 300 <= r.status < 400:
            if on_login(r.getheader("Location") or ""):
                raise FileExpired()
            raise RuntimeError(f"Wisers 轉向 {r.getheader('Location')}")
        if r.status == 401:  # an over session (as on the page's calls)
            raise FileExpired()
        if r.status != 200:
            raise RuntimeError(f"Wisers HTTP {r.status}")
        return (r.getheader("Content-Type") or "application/octet-stream").split(";")[0], body
    finally:
        conn.close()


def image(doc_id, n, size, who=None, reason=""):
    """(content type, bytes) of a document's image n (1-based), thumb or full; None when it has no such image"""
    d = os.path.join(IMAGES, doc_id)
    for name in store.listdir(d):
        if name.startswith(f"{size}-{n}.") and not name.endswith(".type"):
            body = store.read(os.path.join(d, name))
            if body is not None:
                return _read(os.path.join(d, name + ".type")) or "image/jpeg", body
    gate = image_gate(doc_id, who, reason)
    if gate.get("quota"):
        return gate
    kind = "getThumbnailImageByDocId" if size == "thumb" else "getImageByDocId"
    t0 = time.time()
    try:
        ctype, body = fetch_file(f"/getFile/{kind}/{urllib.parse.quote(doc_id)}/{n}")
    except FileExpired:
        refresh_session()
        ctype, body = fetch_file(f"/getFile/{kind}/{urllib.parse.quote(doc_id)}/{n}")
    except RuntimeError as e:
        call_line(f"/getFile/{kind}", kind="image", who=who, reason=reason, outcome="error", error=str(e), doc=doc_id, n=n, served="wisers")
        return None
    if not ctype.startswith("image/") or not body:
        return None
    ext = {"image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(ctype, "jpg")
    store.write(os.path.join(d, f"{size}-{n}.{ext}"), body)
    _write(os.path.join(d, f"{size}-{n}.{ext}.type"), ctype)
    call_line(f"/getFile/{kind}", kind="image", who=who, reason=reason, outcome="ok", charged="no", secs=round(time.time() - t0, 1),
              doc=doc_id, n=n, bytes=len(body), served="wisers")
    return ctype, body


def images_of(doc_id, who=None, reason=""):
    """how many images a document has: from its Wisers document when read, else by fetching thumbnails until one is
    missing (after the gate; at most 20)"""
    d = _read(os.path.join(DOCS, doc_id + ".json"))
    x = (d or {}).get("item") or (d or {}).get("document") or {}
    if str(x.get("imageCount") or "").isdigit() and int(x["imageCount"]) > 0:
        gate = image_gate(doc_id, who, reason)
        return {"count": int(x["imageCount"]), **({"quota": gate["quota"]} if gate.get("quota") else {})}
    cnt = _read(os.path.join(IMAGES, doc_id, "count.json"))
    if cnt:
        return cnt
    n = 0
    for i in range(1, 21):
        r = image(doc_id, i, "thumb", who, reason)
        if isinstance(r, dict):
            return {"count": 0, "quota": r.get("quota")}
        if not r:
            break
        n = i
    out = {"count": n}
    _write(os.path.join(IMAGES, doc_id, "count.json"), out)
    return out


def refresh_session():
    """cookies the files were refused with: the worker's next call checks the session (and logs in if it must)"""
    try:
        lookup("getUserCustomConfig")
    except Exception:
        pass
    LOOKUPS.pop("getUserCustomConfig{}", None)


# ---- downloads: one at a time (the options go through the account's one session), always explicit docIds

DL_LOCK = threading.Lock()
CONTENTS = ("headline-content", "headline-content-image", "headline-image", "headline", "image")
FORMATS = ("pdf", "clipbook-pdf", "doc", "html", "clipbook-html", "rtf", "clipbook-rtf", "txt", "jpg", "xls", "mdb")


def start_download(spec, who=None, reason=""):
    docs = [d for d in spec.get("docs") or [] if re.fullmatch(r"[\w-]{1,80}", str(d))][:2000]
    content = spec.get("content") if spec.get("content") in CONTENTS else "headline-content"
    fmt = spec.get("format") if spec.get("format") in FORMATS else "pdf"
    if not docs:
        raise query.Bad("請先選擇文章。")
    if content == "image" and fmt != "jpg":
        fmt = "jpg"
    did = time.strftime("%Y%m%d%H%M%S-") + secrets.token_hex(4)
    j = {"id": did, "state": "running", "created": time.time(), "who": who, "reason": reason, "docs": docs,
         "content": content, "format": fmt, "highlight": bool(spec.get("highlight", True))}
    _write(os.path.join(FILES, did + ".json"), j)
    threading.Thread(target=run_download, args=(j,), daemon=True).start()
    return j


def run_download(j):
    g = {"onWhat": "selected", "startPageNo": 0, "endPageNo": 0, "pageSize": 50, "docIds": j["docs"],
         "magazineCover": "not", "isIncludeUploadImage": "false", "includeProductInfo": "",
         "highlight": "true" if j["highlight"] else "false", "format": j["format"], "exportOption": "false",
         "downloadOption": j["content"], "isInclueWaterMark": "false", "templateId": "", "itemId": "", "attr_key": "search"}
    t0 = time.time()
    try:
        with DL_LOCK:
            res = ask("validateDownloadQuota", g)
            if res[0] != "ok":
                raise RuntimeError(res[1])
            q = quota_of(res[1] or {})
            if q and q["blocking"]:
                j.update(state="err", error=q["message"], quota=q)
                call_line("validateDownloadQuota", who=j["who"], reason=j["reason"], outcome="quota", charged="no", quota=q["code"], docs=len(j["docs"]))
                return
            res = ask("downloadDocument", g)
            if res[0] != "ok":
                raise RuntimeError(res[1])
            try:
                ctype, body = fetch_file("/wevo/download/downloadDocuments", "POST", b"", secs=240)
            except FileExpired:
                raise RuntimeError(EXPIRED)
        ext = {"application/pdf": "pdf", "application/zip": "zip", "image/jpeg": "jpg", "text/plain": "txt", "text/html": "html",
               "application/msword": "doc", "application/rtf": "rtf", "application/vnd.ms-excel": "xls"}.get(ctype, j["format"].split("-")[-1])
        name = f"{j['id']}.{ext}"
        store.write(os.path.join(FILES, name), body)
        j.update(state="ok", file=name, type=ctype, bytes=len(body))
        call_line("validateDownloadQuota", kind="download", who=j["who"], reason=j["reason"], outcome="ok", charged="yes",
                  secs=round(time.time() - t0, 1), docs=len(j["docs"]), format=j["format"], content=j["content"], bytes=len(body))
    except Exception as e:
        j.update(state="err", error=str(e)[:300])
        call_line("validateDownloadQuota", kind="download", who=j["who"], reason=j["reason"], outcome="error", error=str(e),
                  docs=len(j["docs"]), charged="maybe")
    finally:
        j["finished"] = time.time()
        _write(os.path.join(FILES, j["id"] + ".json"), j)


def prune_files():
    """downloads are kept an hour"""
    for p, mt in store.files(FILES):
        if time.time() - mt > 3600:
            store.remove(p)


# ---- the saved responses (no new search)

def saved_list():
    """the saved responses, newest first"""
    rows = []
    for name in sorted(store.listdir(RAW), reverse=True):
        if not name.endswith(".json"):
            continue
        try:
            r = store.read_json(os.path.join(RAW, name))
            c, d = r["request"]["criteria"], r["response"]["g0"]["data"]["data"]
            per = f"{c.get('dateFrom')} 至 {c.get('dateTo')}" if c.get("dateRangePeriod") == "custom" else c.get("dateRangePeriod")
            rows.append({"file": name, "at": r.get("at", ""), "keyword": c.get("keyword", ""), "period": per,
                         "total": d.get("total"), "items": len(d.get("items") or []), "startIdx": c.get("startIdx", 1)})
        except Exception:
            continue
    return rows


def _words(v):
    """a list of words from whatever shape WisersOne answers with (a list, a comma-separated string, a {word: …} map)"""
    if isinstance(v, list):
        return [str(x.get("word") or x.get("keyword") or x) if isinstance(x, dict) else str(x) for x in v if x not in (None, "")]
    if isinstance(v, dict):
        return [str(k) for k in v]
    return [w for w in re.split(r"[,，\s]+", str(v or "")) if w]


def check(crit, who=None):
    """WisersOne's checks before a search (no search): illegal characters and stop words (search/validateKeywords), the
    date limit (validateSearchDate). An answer in a shape this doesn't know is no problem found (the search itself is the
    judge), never an error: the page then just searches."""
    problems = []
    v = lookup("search/validateKeywords", {"keyword": crit["keyword"], "validateStopWords": "true"}, who)
    v = v if isinstance(v, dict) else {}
    if _words(v.get("illegalChar")):
        problems.append({"kind": "illegal", "words": _words(v.get("illegalChar"))})
    stop = _words(v.get("stop-keyword")) or _words(v.get("stopWords"))
    if stop:
        problems.append({"kind": "stop", "words": stop})
    if crit.get("dateRangePeriod") == "custom":  # the account's limit (maxSearchYears): WisersOne's own validateSearchDate
        try:  # never answered the relay (90 s timeouts, 2026-10-29), so the switch is read and the date compared here
            conf = {x.get("key"): x.get("value") for x in (lookup("getUserCustomConfig", None, who) or {}).get("config") or []}
            years = int(conf.get("maxSearchYears") or 5)
        except Exception:
            years = 5
        first = date.today().replace(year=date.today().year - years)
        if (crit.get("dateFrom") or "9999") < first.isoformat():
            problems.append({"kind": "date", "detail": f"最早可搜尋 {first.isoformat()}（帳戶可搜尋最近 {years} 年）"})
    return {"ok": not problems, "problems": problems}


def on_behalf(who, staff):
    """the log's "who" for a service token: the staff member it names in X-YYC-User, marked with the service's label
    (a header that is not an email is left out); other callers are logged as themselves"""
    if not (who or "").startswith("service:"):
        return who
    label = who[len("service:"):]
    staff = staff.strip().lower()
    return f"{staff} ({label})" if STAFF.match(staff) else f"{who} ({label})"


def internal_caller(authorization, access_token):
    """"service:<label>" for a listed internal token (INTERNAL), sent without an Access token; None otherwise"""
    if access_token or not authorization.startswith("Bearer "):
        return None
    label = INTERNAL.get(hashlib.sha256(authorization[7:].strip().encode()).hexdigest())
    return f"service:{label}" if label else None


def unverified_email(tok):
    """a dev box or a test (no ACCESS_AUD): the email in the token as sent, unchecked (only for the logs)"""
    try:
        c = json.loads(_b64(tok.split(".")[1]))
        if c.get("email"):
            return c["email"].lower()
        cn = c.get("common_name")
        return f"service:{SERVICE_IDS.get(cn, 'service')}" if cn else None
    except Exception:
        return None


class Stats(BaseHTTPRequestHandler):
    """the stats port (WISERS_STATS_PORT): counts only, for the monitor inside the cluster"""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/stats":
            rows = search_log()
            body, code = json.dumps(dict(log_summary(rows), session=session_state(), health=health(rows),
                                         tracking_blocked=RUN["tracking_blocked"])).encode(), 200
        else:
            body, code = b'{"error": "not found"}', 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ID = re.compile(r"[\w-]{1,80}")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", headers=()):
        b = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "private, max-age=86400")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self.serve("GET")

    def do_POST(self):
        self.serve("POST")

    def serve(self, method):
        with RUN_LOCK:
            RUN["active"] += 1
        try:
            self.answer(method)
        finally:
            with RUN_LOCK:
                RUN["active"] -= 1

    def answer(self, method):
        if self.path == "/livez":  # liveness: the process answers (also while draining)
            return self.send(200, "ok", "text/plain")
        if self.path == "/healthz":
            return self.send(503 if RUN["drain_at"] else 200, "draining" if RUN["drain_at"] else "ok", "text/plain")
        tok = self.headers.get("Cf-Access-Jwt-Assertion") or ""
        who = internal_caller(self.headers.get("Authorization") or "", tok) or (access_email(tok) if ACCESS_AUD else unverified_email(tok))
        if ACCESS_AUD and not who:
            return self.send(403, {"error": "只供獲批准的帳戶使用 (Cloudflare Access)"})
        who = on_behalf(who, self.headers.get("X-YYC-User") or "")
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if PREFIX and path.startswith(PREFIX):  # served under newswatch.yyc-internal.com/app/wisers/
            path = "/" + path[len(PREFIX):]
        q = urllib.parse.parse_qs(u.query)
        g = lambda k, d="": (q.get(k) or [d])[0].strip()
        reason = g("reason")[:200]
        try:
            if method == "POST":
                if path == "/api/download":
                    n = int(self.headers.get("Content-Length") or 0)
                    spec = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
                    j = start_download(spec, who, str(spec.get("reason") or reason)[:200])
                    return self.send(200, {"download": {k: v for k, v in j.items() if k != "docs"}})
                return self.send(404, {"error": "not found"})
            if path == "/api/config":
                return self.send(200, {"config": lookup("getUserCustomConfig", None, who)})
            if path == "/api/options":
                return self.send(200, options(who))
            if path == "/api/labels":
                group, node = g("group"), g("id", "0") or "0"
                if group not in query.GROUPS or not re.fullmatch(r"[\w-]{1,40}", node):
                    return self.send(400, {"error": "bad request"})
                return self.send(200, {"labels": labels(group, node, who)})
            if path == "/api/labels/suggest":
                group, s = g("group"), g("q")[:50]
                if group not in query.GROUPS or not s:
                    return self.send(400, {"error": "bad request"})
                return self.send(200, {"labels": lookup(f"label/labelautocomplete/{group}", {"inputStr": s}, who)})
            if path == "/api/labels/names":
                return self.send(200, {"names": lookup("label/getLabelNameList", {"inputStr": g("ids")[:2000]}, who)})
            if path == "/api/suggest":
                s = g("q")[:50]
                if not s:
                    return self.send(200, {"words": []})
                d = lookup("search/keywords", {"keyword": s}, who)
                words = d.get("autoComplete") if isinstance(d, dict) else d
                if isinstance(words, str):
                    try:
                        words = json.loads(words)
                    except ValueError:
                        words = []
                return self.send(200, {"words": words or []})
            if path == "/api/media":
                page = int(g("page", "0")) if g("page", "0").isdigit() else 0
                crit = {"page": page, "mediaType": g("type"), "inputName": g("q")[:80], "enName": "", "zhName": "",
                        "lang": "", "region": g("region") or "all", "pubLanguage": g("lang") or "all"}
                return self.send(200, {"media": lookup("media", crit, who)})
            if path == "/api/media/names":
                ids = [x for x in g("ids").split(",") if ID.fullmatch(x)][:200]
                return self.send(200, {"names": lookup("medianame", {"ids": ids}, who) if ids else {}})
            if path.startswith("/api/media/"):
                mid = path[len("/api/media/"):]
                if not ID.fullmatch(mid):
                    return self.send(400, {"error": "bad request"})
                return self.send(200, {"media": lookup(f"getPublication/{mid}", None, who)})
            if path == "/api/check":
                crit, _ = query.criteria(q)
                return self.send(200, check(crit, who))
            if path == "/api/search":
                crit, page = query.criteria(q)
                if capped(who, "search"):
                    return self.send(429, {"error": f"這個帳戶過去一小時經 AI 送出的 Wisers 搜尋已達上限（{AI_CAP.get('search')} 次）。"})
                j = start_search(crit, page, who=who, reason=reason, fresh=g("fresh") == "1")
                return self.send(200, job_answer(wait_job(j, POLL_SECS)))
            if path.startswith("/api/job/"):
                j = find_job(path[len("/api/job/"):])
                if not j:
                    return self.send(404, {"error": "找不到這次搜尋（轉接程式沒有它的紀錄）。"})
                try:
                    wait = min(POLL_SECS, max(0.0, float(g("wait", "0"))))
                except ValueError:
                    wait = 0.0
                return self.send(200, job_answer(wait_job(j, wait)))
            if path == "/api/derived":
                j = find_job(g("job"))
                if not j or j.get("state") != "ok":
                    return self.send(404, {"error": "找不到這次搜尋的結果。"})
                return self.send(200, {"data": derived(j, g("kind"), q, who)})
            m = re.fullmatch(r"/api/doc/([\w-]{1,80})(/images|/image/(\d{1,2}))?", path)
            if m:
                doc_id = m.group(1)
                if m.group(2) == "/images":
                    return self.send(200, images_of(doc_id, who, reason))
                if m.group(3):
                    r = image(doc_id, int(m.group(3)), "thumb" if g("size") == "thumb" else "full", who, reason)
                    if isinstance(r, dict):
                        return self.send(402, {"error": r["quota"]["message"], "quota": r["quota"]})
                    if not r:
                        return self.send(404, {"error": "沒有這張圖片。"})
                    return self.send(200, r[1], r[0])
                return self.send(200, doc(doc_id, g("style", "all") or "all", g("job") or None, who, reason))
            if path.startswith("/api/download/"):
                did = path[len("/api/download/"):]
                j = _read(os.path.join(FILES, re.sub(r"[^\w-]", "", did) + ".json"))
                if not j:
                    return self.send(404, {"error": "找不到這次下載（下載檔只保留一小時）。"})
                if g("file") == "1" and j.get("state") == "ok":
                    body = store.read(os.path.join(FILES, j["file"]))
                    if body is None:
                        return self.send(404, {"error": "找不到這次下載（下載檔只保留一小時）。"})
                    return self.send(200, body, j.get("type") or "application/octet-stream",
                                     [("Content-Disposition", f'attachment; filename="wisers-{j["file"]}"')])
                return self.send(200, {"download": {k: v for k, v in j.items() if k != "docs"}})
            if path == "/api/log":
                rows = search_log()
                return self.send(200, {"rows": rows, "summary": log_summary(rows)})
            if path == "/api/log/calls":
                return self.send(200, {"rows": calls_log(g("kind") or None)[:2000]})
            if path == "/api/saved":
                return self.send(200, {"saved": saved_list()})
            for pre, fn in (("/api/saved/", result_of), ("/api/raw/", None)):
                if path.startswith(pre):
                    name = os.path.basename(urllib.parse.unquote(path[len(pre):]))
                    if not (name.endswith(".json") and store.exists(os.path.join(RAW, name))):
                        return self.send(404, {"error": "找不到這個已儲存的回應。"})
                    if fn:
                        return self.send(200, fn(name))
                    return self.send(200, store.read(os.path.join(RAW, name)))
        except query.Bad as e:
            return self.send(400, {"error": str(e)})
        except PermissionError as e:
            return self.send(403, {"error": str(e)})
        except Exception as e:  # 500, not 502: Cloudflare replaces a 502's body with its own page (the message was lost)
            return self.send(500, {"error": str(e)[:400]})
        return self.send(404, {"error": "not found"})


def main():
    global PREFIX
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--ns", default=os.environ.get("WISERS_STORE_NS", "wisers"),
                    help="the store namespace of its state in Postgres (env WISERS_STORE_NS; store.py)")
    ap.add_argument("--prefix", default=os.environ.get("WISERS_PREFIX", ""), help="URL path it is served under, e.g. /app/wisers/")
    ap.add_argument("--host", default=os.environ.get("WISERS_HOST", "127.0.0.1"))
    a = ap.parse_args()
    PREFIX = a.prefix.rstrip("/") + "/" if a.prefix else ""
    configure(a.ns)
    seed = os.environ.get("WISERS_STATE_SEED")
    if seed and not store.exists(STATE):  # a saved session to start from, so the first search needs no login
        store.write(STATE, seed)
    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.daemon_threads = True

    def term(*_):
        if RUN["drain_at"] is None:
            RUN["drain_at"] = time.time()
            print(f"SIGTERM: draining ({DRAIN_SECS:.0f} s, then what is queued)", flush=True)
    signal.signal(signal.SIGTERM, term)
    signal.signal(signal.SIGINT, term)
    faulthandler.register(signal.SIGUSR1, all_threads=True)  # kill -USR1: every thread's stack to the log (a hang)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=browser_worker, daemon=True).start()
    if PROBE_SECS > 0:
        threading.Thread(target=prober, daemon=True).start()

    def janitor():
        while True:
            time.sleep(600)
            prune_files()
    threading.Thread(target=janitor, daemon=True).start()
    sport = int(os.environ.get("WISERS_STATS_PORT") or 0)
    if sport:
        st = ThreadingHTTPServer((a.host, sport), Stats)
        st.daemon_threads = True
        threading.Thread(target=st.serve_forever, daemon=True).start()
    print(f"WiseSearch relay on {a.host}:{a.port}{PREFIX}", flush=True)
    while not RUN["worker_done"].is_set() or RUN["drain_at"] is None:
        RUN["worker_done"].wait(1)
        if RUN["worker_done"].is_set() and RUN["drain_at"] is None:  # the worker died without a SIGTERM
            print("the browser worker stopped; exiting", flush=True)
            os._exit(1)
    t = time.time()
    while ACTIVE and time.time() - t < 30:  # the last searches' results stored (the next pod answers from them)
        time.sleep(0.1)
    for j in list(ACTIVE.values()):  # still waiting (came in after the worker stopped): ended, so no pod waits on it
        j.update(state="err", outcome="lost", charged="maybe" if j.get("wisers_at") else "no", error=LOST, finished=time.time())
        save_job(j)
    t = time.time()
    while RUN["active"] and time.time() - t < 10:  # the last answers on their way out
        time.sleep(0.1)
    srv.shutdown()
    print("relay stopped", flush=True)


if __name__ == "__main__":
    main()
