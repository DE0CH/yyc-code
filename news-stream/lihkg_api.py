"""One LIHKG client for every reader: the poller's 時事台 source (sources.lihkg_current), the full-text reader
(cache_fetch.fetch_one) and the whole-site job (replies_fetch.py lihkg) share

- the web app's request headers (X-LI-DEVICE etc.): LIHKG's Cloudflare answers the cluster IP's requests without
  them with 429 while it serves the ones with them (live 2026-10-20 11:00 HKT, same pod, same moment: 429 vs 60
  threads), and with them 80 thread pages in 2 minutes passed (2026-10-13);
- one pacing: every request from any process takes the next slot of one clock in Postgres (kv lihkg:next_at),
  GAP_DIRECT seconds apart direct from the server (GAP_PROXY through the proxy), so two readers never burst together
  (site protection);
- one back-off: a 429 / 403 / Cloudflare 1015 on a direct request by ANY reader records kv lihkg:blocked_until
  (now + BLOCK_SECONDS); until then every reader goes through RESI_PROXY (IPRoyal HK, sub-user yycstream) and
  nothing hits the server's IP again. The poller's request is retried through the proxy at once; a whole-site run
  going direct stops before its next request (replies_fetch.Proxied).

Through the proxy, a walled session moves to a new sticky session (kv lihkg:session) and the bytes are counted for the
monitor (resi.session, site lihkg).
"""
import hashlib, json, os, random, time

import corpus, resi

ORIGIN = "https://lihkg.com"
GAP_DIRECT, GAP_PROXY = 1.0, 0.5   # seconds between LIHKG requests, all readers together
BLOCK_SECONDS = 26 * 3600
K_BLOCKED, K_NEXT, K_SESSION = "lihkg:blocked_until", "lihkg:next_at", "lihkg:session"
TRIES = 4


class Blocked(Exception):
    """LIHKG rate-limited the server's own IP (429 / 1015), or another reader recorded that it did"""


def walled(status, text):
    return status in (403, 429) or "error code: 1015" in (text or "")[:200]


def blocked_until():
    return float(corpus.kv_get(K_BLOCKED) or 0)


def blocked():
    """the server's IP is in its back-off: every reader goes through the proxy"""
    return time.time() < blocked_until()


def block(reason):
    """record the back-off (kept at the later end when one is already recorded)"""
    until = max(blocked_until(), time.time() + BLOCK_SECONDS)
    corpus.kv_set(K_BLOCKED, until)
    print("LIHKG rate-limited the server's IP; every LIHKG reader uses RESI_PROXY for 26 h:", str(reason)[:160], flush=True)


def pace(direct=True):
    """wait for this request's slot on the one LIHKG clock shared by every reader and process"""
    gap = GAP_DIRECT if direct else GAP_PROXY
    t = time.time()
    end = corpus.db().execute(
        """INSERT INTO kv (k, v) VALUES (%s, to_jsonb(%s::float8)) ON CONFLICT (k) DO UPDATE
           SET v = to_jsonb(greatest(coalesce((kv.v #>> '{}')::float8, 0), %s::float8) + %s::float8)
           RETURNING (v #>> '{}')::float8""", (K_NEXT, t + gap, t, gap)).fetchone()[0]
    wait = float(end) - gap - t
    if wait > 0:
        time.sleep(wait)


def device():
    return hashlib.sha1(os.urandom(20)).hexdigest()


def headers(dev, referer=ORIGIN + "/"):
    """the web app's own request headers (the User-Agent is curl_cffi's Chrome one)"""
    return {"Referer": referer, "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
            "X-LI-DEVICE": dev, "X-LI-DEVICE-TYPE": "browser", "X-LI-LOAD-TIME": f"{random.uniform(2, 7):.7f}"}


class Client:
    """one device id and cookie jar per route (direct, proxied); the route of each request is decided by the shared
    back-off"""

    def __init__(self):
        self.device = device()
        self.direct_s = self.proxy_s = None
        self.session = None

    def _proxy(self):
        if not resi.configured():
            raise RuntimeError("RESI_PROXY not set")
        if not self.session:
            self.session = corpus.kv_get(K_SESSION) or resi.session_id(10)
        return resi.url("hk", session=self.session, lifetime="24h")

    def _get(self, url, h, direct):
        if direct:
            if self.direct_s is None:
                from curl_cffi import requests as cr
                self.direct_s = cr.Session(impersonate="chrome")
            return self.direct_s.get(url, headers=h, timeout=40, allow_redirects=False)
        if self.proxy_s is None:
            self.proxy_s = resi.session(label="lihkg")
        p = self._proxy()
        return self.proxy_s.get(url, headers=h, proxies={"http": p, "https": p}, timeout=40, allow_redirects=False)

    def get(self, url, referer=ORIGIN + "/"):
        """→ the body of a 200 answer"""
        url = url if url.startswith("http") else ORIGIN + "/api_v2/" + url
        for _ in range(TRIES):
            direct = not blocked()
            pace(direct)
            r = self._get(url, headers(self.device, referer), direct)
            if walled(r.status_code, r.text):
                if direct:
                    block(f"{url}: {r.status_code}")  # the next try goes through the proxy
                else:
                    self.session, self.proxy_s = resi.session_id(10), None  # this IP is walled: next sticky session
                continue
            if r.status_code != 200:
                raise RuntimeError(f"LIHKG {url[-80:]}: HTTP {r.status_code}")
            if not direct:
                corpus.kv_set(K_SESSION, self.session)
            return r.text
        raise Blocked(url)

    def json(self, path, referer=ORIGIN + "/"):
        """→ the API answer's response"""
        text = self.get(path, referer)
        d = json.loads(text)
        if not d.get("success"):
            raise RuntimeError(f"LIHKG {path[-80:]}: {text[:160]}")
        return d["response"]


_client = None


def client():
    """the process's client (its cookies carry over between polls)"""
    global _client
    if _client is None:
        _client = Client()
    return _client


def reset():
    global _client
    _client = None
