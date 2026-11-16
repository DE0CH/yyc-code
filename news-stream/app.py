#!/usr/bin/env python3
"""YYC news stream: YYC's monitoring streams (streams.json): the poller and the AI server.

Every POLL_SECONDS it reads every source in sources.py and files every item into the corpus. No keyword decides
what belongs to a stream: the scorer (streams.py, a thread of the poller) scores every new post 0–100 against every
stream's brief, and a post at or above the stream's threshold becomes a stream item, filed into the half-hour batch
it was scored in, and a member of the stream's profile on 數據分析. The streams' search terms (news_queries,
news_queries_en, discuss_queries, social_queries) are only what is sent to the platforms that can only be searched (Google News,
香港討論區, the social platforms).

Two roles, run as separate services so a deploy never interrupts readers:
  python app.py                 the poller (yyc-poller): the poll loop above, the corpus after each poll
                                (insights.after_poll), the scorer and the Telegram sender
  gunicorn app:application      the AI server (Deployment `web`): /healthz and web.py's AI routes (the persona
                                button, its cost estimate, the data API for AI). The site's pages and reads are
                                the edge app's (web-app/, reading the same Postgres).

Env (Secret app-env on the cluster):
  OPENROUTER_API  required for scoring (posts wait unscored while it fails)
  MODEL           default qwen/qwen3.7-flash
  RESI_PROXY      the bare login of YYC's one IPRoyal residential sub-user, yycstream (resi.py adds the country and
                  session per request): HKEJ, which refuses the server's IP, 香港討論區 (searched every DISCUSS_SECONDS,
                  default 1800), Google News links while Google refuses the cluster
  DATABASE_URL    Postgres (corpus.py): every piece of state
  TELEGRAM_BOT_TOKEN  posts every kept item to its stream's Telegram channel (telegram_alerts.py); unset = off
  DATA_DIR        default ./data (built reports, browser profiles)   POLL_SECONDS default 300
"""
import hashlib, json, os, re, signal, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from http import HTTPStatus

import resi
import sources
import sources_extra
import corpus, edge_config, fulltext_now, live, insights, social_twopass, streams, telegram_alerts, web
import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
HKT = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(HERE, "data"))
MODEL = os.environ.get("MODEL", "qwen/qwen3.7-flash")
POLL = int(os.environ.get("POLL_SECONDS", "300"))
DISCUSS_SECONDS = int(os.environ.get("DISCUSS_SECONDS", "1800"))
MAX_AGE = timedelta(days=2)  # ignore items published longer ago than this (first run, stale feeds)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"

lock = threading.Lock()
state = {"last_poll": None, "source_status": {}}  # the poller's copy; Postgres holds the truth


def load_json(name):
    with open(os.path.join(HERE, name)) as f:
        return json.load(f)


STREAMS = load_json("streams.json")
GNEWS = (sources.gnews_queries([q for st in STREAMS for q in st.get("news_queries", [])]) +
         sources.gnews_queries([q for st in STREAMS for q in st.get("news_queries_en", [])], "en"))
EXTRA = list(sources_extra.EXTRA) + GNEWS  # the brief's magazines, columns, The Standard, 香港仔, 鳳凰, FSTB LinkedIn


# ---- fetching (sources.py) ----

def get(url, timeout=30):
    return sources.fetch(url)  # timeout: sources.fetch uses 30 s


GOOGLE_CONSENT = {"Cookie": "CONSENT=YES+cb; SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}


GNEWS_PER_POLL = 30   # Google News links decoded per poll at most, newest first; the rest wait for the next poll
GNEWS_PAUSE = 1800    # seconds without direct requests to Google after it refuses (429 / google.com/sorry)


class GoogleBlocked(RuntimeError):
    """Google refused the request: 429, or a redirect to its google.com/sorry captcha page"""


def google_get(method, url, proxy=None, data=None):
    """one news.google.com request with Chrome's TLS fingerprint and Google's consent cookie (from the cluster's EU WARP
    IP urllib gets 429 and a cookie-less Chrome lands on consent.google.com's "Before you continue", 2026-10-14) → the
    body; GoogleBlocked when Google refuses"""
    from curl_cffi import requests as cr
    if proxy:  # resi.session counts it; IPRoyal out of traffic → resi.ProxyOut
        r = resi.session().request(method, url, data=data, timeout=20, headers=GOOGLE_CONSENT,
                                   proxies={"http": proxy, "https": proxy})
    else:
        r = cr.request(method, url, data=data, impersonate="chrome", timeout=20, headers=GOOGLE_CONSENT)
    if r.status_code == 429 or "google.com/sorry" in (r.url or ""):
        raise GoogleBlocked(f"HTTP {r.status_code} {(r.url or '')[:40]}")
    r.raise_for_status()
    return r.text


def gnews_url(url, proxy=None):
    """a news.google.com/rss/articles/<id> link → the publisher's URL (Google's own page signs a batchexecute call;
    the same method as the googlenewsdecoder package). The /rss/articles page with the edition's hl/gl/ceid is the
    smallest that carries the signature (~120 KB gzipped, against ~1 MB for /articles, measured 2026-10-15). Returns
    the input when it can't be decoded; GoogleBlocked when Google refuses"""
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([^?/]+)", url)
    if not m:
        return url
    aid = m.group(1)
    page = google_get("GET", f"https://news.google.com/rss/articles/{aid}?oc=5&hl=zh-HK&gl=HK&ceid=HK:zh-Hant", proxy)
    try:
        sg = re.search(r'data-n-a-sg="([^"]+)"', page).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page).group(1)
        inner = json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None,
                                            None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                            aid, int(ts), sg])
        body = google_get("POST", "https://news.google.com/_/DotsSplashUi/data/batchexecute", proxy,
                          data={"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])})
        data = json.loads(json.loads(body.split("\n\n", 1)[1])[0][2])
        return data[1] if isinstance(data[1], str) and data[1].startswith("http") else url
    except (GoogleBlocked, resi.ProxyOut):
        raise
    except Exception:
        return url


def decode_gnews(pending):
    """decode the poll's Google News links (items, any order) → (decoded items, status). Newest first, at most
    GNEWS_PER_POLL. Google refusing a direct request pauses direct requests for GNEWS_PAUSE seconds
    (state gnews_direct_at, kept in poll_state); meanwhile links go through RESI_PROXY, or wait (unseen) without one.
    Live 2026-10-15: retrying every pending link directly every poll (448 links, two requests each, every 5 min) kept
    Google's block on the cluster's IP going for a day"""
    proxy = resi.url("hk")
    pending = sorted(pending, key=lambda it: str(it.get("published") or ""), reverse=True)
    done, err, via, out = [], None, {"direct": 0, "proxy": 0}, False
    for it in pending[:GNEWS_PER_POLL]:
        how = "direct"
        try:
            if time.time() >= state.get("gnews_direct_at", 0):
                try:
                    u, how = gnews_url(it["url"]), "direct"
                except GoogleBlocked as e:
                    state["gnews_direct_at"] = time.time() + GNEWS_PAUSE
                    err = f"Google refused direct requests ({e}); paused {GNEWS_PAUSE // 60} min"
                    if not proxy:
                        break
                    how = "proxy"
                    u = gnews_url(it["url"], proxy)
            elif proxy:
                how = "proxy"
                u = gnews_url(it["url"], proxy)
            else:
                err = err or "Google refused direct requests; paused, no RESI_PROXY to decode through"
                break
        except resi.ProxyOut as e:  # out of traffic (resi.py): the links wait, unseen, a pause
            err, out = f"{how}: {e!r}"[:200], True
            break
        except Exception as e:  # the proxy refused too (GoogleBlocked, a timeout): try next poll
            err = f"{how}: {e!r}"[:200]
            break
        if "news.google.com" in u:
            continue  # not decoded (a page without the signature): not seen, the next poll tries again
        via[how] += 1
        done.append((it, u))
    ok = bool(done) or not pending or err is None
    st = {"ok": ok, "items": len(done), "pending": len(pending), "via": via, "error": None if ok else err}
    if out and not done:
        st["off"] = resi.STATUS
    return done, st


SEEN_MAX = 2000  # bytes: poll_seen's key is a btree (2,704 bytes a row at most); a longer URL is kept as its digest


def seen_key(u):
    """poll_seen's key for a URL: the URL, or 'sha256:<hex>' when it is too long for the btree (a 3,633-byte Google
    News link stopped every poll for 2.5 h, 16 Oct 2026: tests/test_long_urls.py)"""
    if len(u.encode()) <= SEEN_MAX:
        return u
    return "sha256:" + hashlib.sha256(u.encode()).hexdigest()


def seen_urls(urls, since=None):
    """which of these URLs an earlier poll already read (table poll_seen), since `since` when given"""
    keys = {}
    for u in urls:
        keys.setdefault(seen_key(u), []).append(u)
    if not keys:
        return set()
    sql, args = "SELECT url FROM poll_seen WHERE url = ANY(%s)", [list(keys)]
    if since is not None:
        sql, args = sql + " AND at::timestamptz >= %s", args + [since]
    return {u for r in corpus.db().execute(sql, args) for u in keys.get(r[0], ())}


def poll_get(k, default=None):
    r = corpus.db().execute("SELECT v FROM poll_state WHERE k=%s", (k,)).fetchone()
    return r[0] if r else default


def poll_put(c, k, v):
    c.execute("INSERT INTO poll_state (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v=excluded.v", (k, corpus.J(v)))


def load_state():
    state.update(last_poll=poll_get("last_poll"), source_status=poll_get("source_status", {}),
                 discuss_at=poll_get("discuss_at", 0), gnews_direct_at=poll_get("gnews_direct_at", 0))


def track(v, p):
    """a source's health across polls, for Deyao's monitor (monitor.py): last_ok (the last poll it answered), fails
    (polls failed in a row), fail_since (the first of them), last_error / last_error_at (the latest failure, kept after
    it recovers). A source off on purpose (v["off"]: a feature flag, the safety stop) is not failing: its count clears,
    and once it is back on it is judged afresh"""
    at = datetime.now(HKT).isoformat(timespec="seconds")
    if v.get("off"):
        v.update(last_ok=p.get("last_ok"), fails=0, fail_since=None, last_error=p.get("last_error"),
                 last_error_at=p.get("last_error_at"))
    elif v["ok"]:
        v.update(last_ok=at, fails=0, fail_since=None, last_error=p.get("last_error"), last_error_at=p.get("last_error_at"))
    else:
        v.update(last_ok=p.get("last_ok"), fails=(p.get("fails") or 0) + 1,
                 fail_since=p.get("fail_since") if p.get("fails") else at, last_error=v.get("error"), last_error_at=at)
    return v


PROBLEM_FAILS = 2  # polls failed in a row before the live dashboard calls a source failing (one miss is noise)


def note_problem(sid, v, p):
    """the live dashboard's Problems panel (events.py): a source failing PROBLEM_FAILS polls in a row, and its recovery"""
    import events
    if not v.get("off") and not v["ok"] and v.get("fails") == PROBLEM_FAILS:
        events.emit("problem", None, what="source", key=sid, state="failing", since=v.get("fail_since"),
                    reason=v.get("error"))
    elif v["ok"] and (p.get("fails") or 0) >= PROBLEM_FAILS:
        events.emit("problem", None, what="source", key=sid, state="recovered", since=p.get("fail_since"),
                    reason=p.get("last_error"), fails=p.get("fails"))


def poll_once():
    now = datetime.now(HKT)
    fresh = []
    got, status = sources.fetch_all(EXTRA)
    for it in got:  # every post is read, stored and sent under its address without tracking parameters (uwants' home
        it["url"] = corpus.strip_tracking(it["url"])  # page gives a new tr_h on every read: one thread stored ~20 times)
    seen = seen_urls(it["url"] for it in got)
    listed = [(it.get("sid"), it["url"]) for it in got]  # before decoding replaces the Google links
    new_urls, google = [], []
    # 香港討論區 through the residential proxy: every DISCUSS_SECONDS only (paid per GB)
    if time.time() - state.get("discuss_at", 0) >= DISCUSS_SECONDS:
        state["discuss_at"] = time.time()
        try:
            d = sources.discuss_search([q for st in STREAMS for q in st.get("discuss_queries", [])])
            status["discuss"] = {"ok": True, "items": len(d), "error": None}
            got += d
        except resi.ProxyOut as e:  # a pause (resi.py): tried again at its next turn
            status["discuss"] = {"ok": False, "items": 0, "error": repr(e)[:200], "off": resi.STATUS}
        except Exception as e:
            status["discuss"] = {"ok": False, "items": 0, "error": repr(e)[:200]}
    elif "discuss" in state.get("source_status", {}):
        status["discuss"] = state["source_status"]["discuss"]
    prev = state.get("source_status", {})
    for k, v in status.items():
        if v is not prev.get(k):  # 香港討論區 between its runs carries its last result: not a new check
            track(v, prev.get(k) or {})
            note_problem(k, v, prev.get(k) or {})
        v["at"] = now.isoformat(timespec="seconds")
    carry = sources.scheduled(EXTRA)
    for k, v in prev.items():  # scheduled sources not due this poll keep their last result; nothing else is carried
        if k in carry:
            status.setdefault(k, v)
    for it in got:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        new_urls.append(it["url"])
        if too_old(it.get("published"), now):
            continue
        if "news.google.com" in it["url"] and not it.get("decoded"):  # the publisher's URL (for the corpus + source gaps)
            new_urls.remove(it["url"])  # seen only once decoded (decode_gnews): until then the next poll tries again
            google.append(it)
            continue
        fresh.append(it)
    if google or "gnews_decode" in prev:
        decoded, status["gnews_decode"] = decode_gnews(google)
        track(status["gnews_decode"], prev.get("gnews_decode") or {})
        status["gnews_decode"]["at"] = now.isoformat(timespec="seconds")
        for it, u in decoded:
            new_urls.append(it["url"])
            # a 香港討論區 thread under the address the replies job files it at (its opening post's text)
            it["url"], it["decoded"] = sources.discuss_url(u) or u, True
            fresh.append(it)
    note_new_24h(status, listed, set(new_urls), now)
    with lock:
        c = corpus.db()
        with c:  # one transaction: the URLs read and the poll's status
            c.executemany("INSERT INTO poll_seen (url, at) VALUES (%s, %s) ON CONFLICT (url) DO NOTHING",
                          [(seen_key(u), now.isoformat(timespec="seconds")) for u in new_urls])
            poll_put(c, "last_poll", now.isoformat(timespec="seconds"))
            poll_put(c, "source_status", status)
            poll_put(c, "discuss_at", state.get("discuss_at", 0))
            poll_put(c, "gnews_direct_at", state.get("gnews_direct_at", 0))
        state["last_poll"] = now.isoformat(timespec="seconds")
        state["source_status"] = status
    print(f"{now:%F %T} +{len(fresh)} new posts; failed sources: {[k for k, v in status.items() if not v['ok'] and not v.get('off')]}", flush=True)
    state["after"] = threading.Thread(target=after_poll, args=(got, fresh), daemon=True)  # the drain joins it
    state["after"].start()


def note_new_24h(status, listed, new_now, now):
    """per source: how many of the URLs it listed this poll were first read in the last 24 h, by it or by any other
    source (status new_24h). A URL is stored under the first source that lists it, so a source whose stories another
    source lists first (SCMP's news sitemap before its Hong Kong feed, 明報's sitemaps before 港聞) stores few posts under
    its own id while still bringing current news: monitor.sources reads this before calling it quiet"""
    urls = list({u for _, u in listed})
    recent = seen_urls(urls, now - timedelta(hours=24))
    counts = {}
    for sid, u in listed:
        if u in recent or u in new_now:
            counts[sid] = counts.get(sid, 0) + 1
    for sid, v in status.items():  # a status carried from an earlier run (a scheduled source not due) keeps its own
        if v.get("ok") and "new_24h" not in v:
            v["new_24h"] = counts.get(sid, 0)


def too_old(pub, now):
    """published longer ago than MAX_AGE? A date-only post (corpus.DateOnly) counts from the time ingest gives it
    (corpus.within_day: now, kept within its day), not from that day's midnight"""
    if not pub:
        return False
    if isinstance(pub, corpus.DateOnly):
        pub = datetime.fromisoformat(corpus.within_day(pub, now.isoformat()))
    return now - pub > MAX_AGE


def after_poll(got, fresh):
    """the corpus (every fetched post; streams.py scores them and sends the alerts)"""
    with after_lock:
        try:
            insights.after_poll(got, fresh)
        except Exception as e:
            print("after_poll failed:", repr(e)[:300], flush=True)


after_lock = threading.Lock()


def loop():
    """a poll every POLL seconds, start to start (waiting POLL after each poll made the cycle the poll's own time
    longer: 5.5-6 min, 15 Oct 2026)"""
    while not STOP.is_set():
        t0 = time.monotonic()
        try:
            poll_once()
        except Exception as e:
            print("poll failed:", e, flush=True)
        STOP.wait(max(0, POLL - (time.monotonic() - t0)))


# ---- one poller at a time, handed over without a gap (a deploy is a rolling update: the new pod waits on the lock
# while the old one drains) ----

STOP = threading.Event()  # SIGTERM: finish the poll in hand and its after_poll, then exit, releasing the lock
# set only by the drain's end (the process exits): keep_lock speaks on the lock's connection until then, NOT until STOP.
# The connection ends itself after corpus.LOCK_IDLE idle seconds (a dead holder's lock frees itself), so a heartbeat
# that stopped on SIGTERM freed the lock 90 s into the drain and the next pod polled beside the draining one
# (2026-10-21 03:29 and 03:31 UTC; tests/test_drain_holds_lock.py)
RELEASED = threading.Event()
STARTED, ALIVE = "/tmp/poller-started", "/tmp/poller-alive"  # the pod's readiness and liveness probes
held = {"conn": None}
LOCK_CHECK = 10  # seconds between checks that the lock's connection (and so the lock) is still there
RETRY_MAX = 30  # the longest wait between tries while the database is unreachable


def _lock_connect():
    return corpus.lock_connect("yyc-poller-lock")  # ended by the server when idle: keep_lock speaks every LOCK_CHECK s


def db_retry(fn, what, stop=None):
    """fn() until it gets through: while Postgres is down (an OOM kill, a CNPG switchover: seconds to a minute) the
    poller waits with backoff (1 s doubling to RETRY_MAX) instead of exiting into a crash loop. None once `stop`
    (default STOP) is set"""
    stop = stop or STOP
    delay = 1
    while not stop.is_set():
        try:
            return fn()
        except psycopg.OperationalError as e:
            print(f"database unavailable ({what}): {str(e).strip().splitlines()[0][:200]}; retrying in {delay} s",
                  flush=True)
            stop.wait(delay)
            delay = min(delay * 2, RETRY_MAX)
    return None


def take_poller_lock():
    """a session advisory lock on a connection of its own (not the pool), held for the process's life. Postgres drops
    it when the process exits; server and client TCP keepalives drop a vanished pod's within about a minute. A database
    that is down (or goes down while this pod waits) is waited for, not a reason to exit"""
    print("waiting for poller lock", flush=True)

    def take():
        c = _lock_connect()
        try:
            c.execute("SELECT pg_advisory_lock(hashtext('yyc poller'))")
        except BaseException:
            c.close()
            raise
        return c
    c = db_retry(take, "poller lock")
    if c is None:  # SIGTERM while waiting
        raise SystemExit(0)
    held["conn"] = c
    print("took poller lock", flush=True)
    threading.Thread(target=keep_lock, daemon=True, name="keep_lock").start()


def keep_lock():
    """a database restart ends the lock's session and so the lock: take it again on a new connection (without waiting
    on it). When another poller got it during the gap (a rolling deploy's new pod), this one drains and exits, so two
    pollers never run at once. Runs through the drain (until RELEASED): the lock is this pod's until it exits"""
    while not RELEASED.wait(LOCK_CHECK):
        c = held["conn"]
        try:
            c.execute("SELECT 1")
            continue
        except psycopg.Error as e:
            if RELEASED.is_set():
                return
            print("poller lock connection lost:", str(e).strip().splitlines()[0][:200], flush=True)
        try:
            c.close()
        except Exception:
            pass

        def retake():
            n = _lock_connect()
            try:
                return n, n.execute("SELECT pg_try_advisory_lock(hashtext('yyc poller'))").fetchone()[0]
            except BaseException:
                n.close()
                raise
        got = db_retry(retake, "taking the poller lock again", stop=RELEASED)
        if got is None:
            return
        n, ok = got
        if ok and RELEASED.is_set():
            n.close()
            return
        if ok:
            held["conn"] = n
            print("retook poller lock", flush=True)
            continue
        n.close()
        print("poller lock lost to another poller: draining and exiting", flush=True)
        stop_all()
        return


def stop_all():
    STOP.set()
    telegram_alerts.STOP.set()
    streams.STOP.set()
    fulltext_now.STOP.set()
    social_twopass.STOP.set()


DRAIN_START = []  # time.monotonic() of the SIGTERM: the drain's length is logged (deploy/apps/poller.yaml's grace)


def on_term(*_):
    stop_all()
    if held["conn"] is None:  # still waiting for the lock: nothing to drain
        raise SystemExit(0)
    DRAIN_START.append(time.monotonic())
    print("SIGTERM: draining (finishing the poll in hand); the lock is kept until the drain is done", flush=True)


def heartbeat():
    """liveness, independent of the lock: a waiting pod is alive too"""
    while True:
        open(ALIVE, "w").close()
        time.sleep(30)


def drain(tg, sc, ft):
    if state.get("after"):
        state["after"].join()  # the corpus, tags and alerts of the last poll
    for t in ft:
        t.join()  # each page fetch in hand
    for t in sc:
        t.join()  # the scorer and its backfill: each stops after the batch in hand
    if tg:
        tg.join()  # stops after the message in hand
    print(f"drained in {time.monotonic() - DRAIN_START[0]:.0f} s; releasing poller lock" if DRAIN_START else
          "drained; releasing poller lock", flush=True)
    RELEASED.set()
    # exit at once: interpreter shutdown can hang on a pool connection a daemon thread (corpus.reindex) was frozen in;
    # everything is committed, Postgres rolls back that thread's open transaction and drops the lock
    os._exit(0)


# ---- the AI server (gunicorn app:application; routes in web.py) ----

class Headers(dict):
    """request headers, looked up case-insensitively (web.py reads h.headers.get("Cf-Access-Jwt-Assertion"))"""
    def get(self, k, default=None):
        return super().get(k.lower(), default)


class Handler:
    """one request: the routes below and web.py call self.send / self.json, which record the response"""
    def __init__(self, environ):
        self.command = environ["REQUEST_METHOD"]
        raw = environ.get("RAW_URI")  # gunicorn: the path as sent (still percent-encoded), with the query
        self.path = raw or urllib.parse.quote(environ.get("PATH_INFO", "/")) + (
            "?" + environ["QUERY_STRING"] if environ.get("QUERY_STRING") else "")
        self.headers = Headers({k[5:].replace("_", "-").lower(): v for k, v in environ.items() if k.startswith("HTTP_")})
        for k in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if environ.get(k):
                self.headers[k.replace("_", "-").lower()] = environ[k]
        self.rfile = environ["wsgi.input"]
        self.response = None

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200_000:
            return self.json(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.json(400, {"error": "JSON body expected"})
        try:
            if web.handle_post(self, u.path, body):
                return
        except Exception as e:
            print("POST", u.path, "failed:", repr(e)[:300], flush=True)
            return self.json(500, {"error": "server error: " + repr(e)[:200]})
        self.send(404, "text/plain", b"not found")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if web.handle_get(self, u.path, q):
                return
        except Exception as e:
            print("GET", u.path, "failed:", repr(e)[:300], flush=True)
            return self.json(500, {"error": "server error: " + repr(e)[:200]})
        if u.path == "/healthz":
            return self.send(200, "text/plain", b"ok")
        self.send(404, "text/plain", b"not found")

    def json(self, code, obj):
        self.send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False, default=str).encode())

    def send(self, code, ctype, body, extra=None):
        extra = dict(extra or {})
        headers = [("Content-Type", ctype), ("Cache-Control", extra.pop("Cache-Control", "no-store"))] + list(extra.items())
        self.response = (code, latin1(headers) + [("Content-Length", str(len(body)))], body)

    def stream(self, st):
        """a web.Stream: NDJSON lines sent as they come (no Content-Length; nginx must not buffer it)"""
        self.response = (200, [("Content-Type", "application/x-ndjson; charset=utf-8"), ("Cache-Control", "no-store"),
                               ("X-Accel-Buffering", "no")], st.lines())


class BadHeader(Exception):
    """a response header gunicorn would refuse (it drops the connection, the browser shows nothing)"""


def latin1(headers):
    """HTTP header values must be Latin-1 (RFC 9110); fail loudly here rather than silently in gunicorn"""
    for k, v in headers:
        try:
            (k + v).encode("latin-1")
        except UnicodeEncodeError:
            raise BadHeader(f"{k} is not Latin-1: {v[:80]!r}") from None
        if "\r" in v or "\n" in v:
            raise BadHeader(f"{k} has a line break")
    return headers


def application(environ, start_response):
    """the WSGI entry point (gunicorn app:application)"""
    h = Handler(environ)
    if h.command in ("GET", "HEAD"):
        h.do_GET()
    elif h.command == "POST":
        h.do_POST()
    else:
        h.send(405, "text/plain", b"method not allowed")
    code, headers, body = h.response or (500, [("Content-Type", "text/plain")], b"no response")
    start_response(f"{code} {HTTPStatus(code).phrase}", headers)
    if h.command == "HEAD":
        return [b""]
    return [body] if isinstance(body, bytes) else body  # bytes, or a stream's line generator


def main():  # the poller (yyc-poller); the web is gunicorn app:application
    signal.signal(signal.SIGTERM, on_term)
    open(STARTED, "w").close()
    threading.Thread(target=heartbeat, daemon=True).start()
    take_poller_lock()
    os.makedirs(DATA_DIR, exist_ok=True)
    db_retry(load_state, "loading the poll state")
    # the search columns of rows indexed with another searchtext.VERSION (the history, a dictionary edit): rewritten in
    # the background, searched meanwhile
    threading.Thread(target=corpus.reindex, daemon=True).start()
    ft = fulltext_now.start()  # each new article's full page, read as soon as it is ingested
    social_twopass.start()  # social posts' comments read at capture and for the second scoring (twopass.py)
    sc = streams.start(STREAMS)  # every new post scored against every stream; shown ones become stream items
    tg = telegram_alerts.start(STREAMS)  # every stream item to its stream's Telegram channel
    # what the site (the edge app, reading Postgres) needs from here: streams, flags, thresholds, labels (edge_config.py)
    threading.Thread(target=edge_config.loop, args=(len(sources.SOURCES) + len(EXTRA), threading.Event()), daemon=True).start()
    threading.Thread(target=live.run, args=(threading.Event(),), daemon=True).start()  # open pages told what changed
    loop()
    drain(tg, sc, ft)


if __name__ == "__main__":
    main()
