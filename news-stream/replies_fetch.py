#!/usr/bin/env python3
"""Forum threads and replies into the corpus: every thread and reply of the whole LIHKG and 香港討論區 sites, filed with
corpus.add_comments as rows of their own, where 搜尋 finds words inside them and the stream scorer (streams.py)
scores each thread as one unit with its replies (twopass.py: at capture and at post time + 30 min).

Which threads:
  LIHKG       the WHOLE site (Deyao, 2026-10-14): 最新 (api_v2/thread/latest?cat_id=3, type=now: every board, latest
              reply first), paged back to the previous listing; every thread id between the last one seen (kv
              replies:lihkg:max_id) and the newest listed that 最新 didn't show is read directly, and its board (one
              最新 hides, kv replies:lihkg:hidden_cats) is listed from then on (api_v2/thread/category); plus the LIHKG
              threads the collectors filed in the last 2 days whose reply count grew. A new thread is read at once; a
              thread whose reply count grew is read again once its last read is REREAD old, or when its post time +
              30 min has passed since its last read (so streams.py's second scoring of the thread sees the replies up
              to then); threads not due yet wait in pending. Two-pass scoring (twopass.py): a thread's first full
              read marks it captured (its first scoring waits for that); a run that reads every due thread marks the
              threads whose second scoring is due as re-read (twopass.fetched_all; those in its listing were read in
              it). Runs every 5 min (CronJob
              lihkg), like 香港討論區 (CronJob replies); each site has its own lock.
              Thread pages: api_v2/thread/<id>/page/<n>?order=reply_time (25 posts). Direct from the server
              with curl_cffi impersonating Chrome (LIHKG answers urllib's TLS handshake with 429 from the cluster: 41
              of 41 curl_cffi requests passed where urllib got 429 at once, 2026-10-14), the web app's X-LI-DEVICE
              headers and a cookie jar (requests without them get Cloudflare's 1015 ban
              after ~12 thread pages; with them 80 pages in 2 minutes passed, 2026-10-13). Headers, pacing and the
              429 back-off are the one LIHKG client every reader shares (lihkg_api.py; the poller's 時事台 source and
              the full-text reader use it too): one request a second across all of them, and when LIHKG rate-limits
              the server's IP (429/1015) on any reader's request, every reader uses RESI_PROXY for 26 h (this job
              stops at once and its next run starts through the proxy).
  香港討論區  the WHOLE site (Deyao, 2026-10-15), every 5 min (CronJob replies), the LIHKG design: every board of the
              archiver's forum index (kv replies:discuss:boards, refreshed daily), each listed through its archiver
              pages (archiver/?fid-<fid>[-page-<n>].html: 50 threads a page with reply counts, latest reply first,
              ~6 KB through the proxy), paged on until BOUNDARY known threads with no new reply were passed. A board
              whose first page didn't change is listed every 2, 4, 8 … BOARD_MAX_RUNS runs (at least hourly), one
              that changed every run; boards listed longest ago go first and listing takes at most LIST_SHARE of the
              run. Thread ids are sequential site-wide, so an id between the last one seen (kv replies:discuss:max_id)
              and the newest listed that no listing showed is read directly. The site's first listing of a board is a
              baseline (its threads' reply counts recorded: each is read in full once it grows). New threads are read
              at once, newest first; a thread whose second scoring is due (post time + 30 min, twopass) is read again
              directly (or, listed with no new reply, marked re-read without a request). Plus the threads
              discuss_search / Google News filed in the last 2 days. Posts 1-100 come from the printable view (one
              request, ~5-14 KB), later posts from the mobile thread pages (15 posts, ~30 KB). From the server's own IP when it
              answers this run (discuss_route, one request first), else through the ISP IP, else RESI_PROXY
              (IPRoyal HK residential, sub-user yycstream) on a sticky session: an IP the site walls (reCAPTCHA HTTP
              202, 403) moves to a new session until one passes and keeps it; DISCUSS_GAP seconds between requests.

Incremental: kv replies:<site>:<thread id> = {"read": highest post number filed, "total": the site's reply count};
only the pages after `read` are fetched, and only when the listing's reply count grew. A reply's own time is its
published time, so a reply today in a thread from May lands on today. Quoted text inside a reply is dropped (the
quoted post is its own row). Threads not reached in a run (RUN_SECONDS per site; LIHKG_MAX_PAGES, which keeps LIHKG
from banning the server's IP) wait in kv replies:<site>:pending.

Proxy traffic: proxied bytes are counted (kv proxy:bytes:<site>:<YYYY-MM-DD> / <YYYY-MM> / total, resi.count) for the monitor;
nothing is paced or capped by them (Deyao, 2026-10-14: budgets are safety stops; IPRoyal's balance is the hard stop).

  replies_fetch.py                  one run (香港討論區, then LIHKG)
  replies_fetch.py lihkg|discuss    one site
  replies_fetch.py ... --since 30h  first-listing window for boards never listed before (default 3h)
  replies_fetch.py stats            reply rows per site, proxy bytes, last run
  replies_fetch.py discuss-clean    strip the picture attributes and attachment notes from stored 香港討論區 texts
Env: DATA_DIR, RESI_PROXY, RUN_SECONDS, LIHKG_MAX_PAGES, DISCUSS_GAP.
"""
import gzip, hashlib, html, http.cookiejar, json, os, re, sys, time, urllib.parse, urllib.request, zlib
from datetime import datetime, timezone, timedelta

import corpus, lihkg_api, resi, twopass
from lihkg_api import Blocked

HKT = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
MUA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"

# LIHKG: 最新 (cat 3) lists every board (api_v2/thread/latest); boards it hides are learnt (kv replies:lihkg:hidden_cats)
LIHKG_LATEST = 3
REREAD = 25 * 60  # seconds: a thread whose reply count grew is read again this long after its last read
MARK = 30 * 60    # seconds after a thread's post time: streams.py scores it again then, with its replies
LIHKG_PER_PAGE = 25
# 香港討論區 boards K-Matrix's mails cite: 香港及世界新聞討論 (fid 54) and 金融財經投資區 (57); plus 54's sub-boards
# 時政文化, 突發報料, 立法會事務. A thread from any other board still comes in when discuss_search files it.
DISCUSS_FIDS = {54: "香港及世界新聞討論", 57: "金融財經投資區", 106: "時政文化", 1121: "突發報料", 1136: "立法會事務"}
DISCUSS_PER_PAGE = 15
# the whole 香港討論區 site (Deyao, 2026-10-15): every board of the archiver's forum index (kv replies:discuss:boards,
# refreshed daily), each listed through its archiver pages (50 threads a page with reply counts, latest reply first)
DISCUSS_ARCHIVER = "https://www.discuss.com.hk/archiver/"
DISCUSS_LIST_PER_PAGE = 50
LIST_MAX_PAGES = 5            # site protection: archiver pages of one board in one run
LIST_SHARE = 0.5              # of a 香港討論區 run's RUN_SECONDS for listing boards (the rest reads threads)
BOUNDARY = 5                  # known threads with no new reply that end a board's listing (more than its stickies)
BOARD_MAX_RUNS = 12           # a board whose first page didn't change is listed every 2, 4, 8, 12 runs ...
BOARD_MAX_EVERY = 3600        # ... and at least once an hour
RUN_PERIOD = 300              # seconds between runs (CronJob replies every 5 min): a board's interval = every × this
DISCUSS_GAP_DEFAULT = 0.5     # seconds between 香港討論區 requests (politeness)
DISCUSS_GAP = float(os.environ.get("DISCUSS_GAP", DISCUSS_GAP_DEFAULT))
DISCUSS_WALLS = 3             # residential IPs a 香港討論區 page may meet the wall on; then Wall, which ends the run
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "720"))  # per site per run: the rest waits (the timer runs every 30 min)
LIHKG_MAX_PAGES = int(os.environ.get("LIHKG_MAX_PAGES", "1500"))  # site protection: LIHKG bans bursts (Cloudflare 1015)
COLLECTED_DAYS = 2  # collectors' threads followed this long after they were filed
SLACK = 900  # seconds of overlap with the previous run's listing


class Wall(Exception):
    """the site kept answering with its wall (discuss.com.hk's reCAPTCHA page, LIHKG's 429) on every session tried"""


class ProxyOut(resi.ProxyOut):
    """the proxy answered 402 Payment Required: the IPRoyal sub-user's traffic is used up (the run stops at once;
    recorded in kv proxy_out by resi.mark, one Discord message per outage)"""


def now():
    return datetime.now(HKT)


def proxy_out(e):
    """the proxy refused for its traffic (IPRoyal 402): urllib says '402 Payment Required', curl 'CONNECT tunnel
    failed, response 402'"""
    return resi.is_out(e)


def out(site, e):
    """the ProxyOut to raise, with the outage recorded (resi.mark)"""
    if not isinstance(e, resi.ProxyOut):
        resi.mark(repr(e)[:300])
    return ProxyOut(f"{site}: IPRoyal 402 Payment Required (the sub-user's traffic is used up)")


class NoRedirect(urllib.request.HTTPRedirectHandler):  # discuss answers 302 with the full page as the body
    def redirect_request(self, *a, **k):
        return None


class Proxied:
    """requests for one site with a cookie jar and (LIHKG) a device id. Through RESI_PROXY on a sticky IPRoyal session
    (kv replies:<site>:session), direct from the server (direct=True: LIHKG), or through YYC's IPRoyal ISP IP (isp=True:
    香港討論區, env ISP_PROXY, one client at a time: each request under the 'yyc youtube' lock, sites_fetch.isp_lock;
    bytes in kv isp:bytes, resi.count_isp). Proxy bytes are counted per HKT day and month (the monitor's figure);
    nothing is paced by them."""

    def __init__(self, site, direct=False, isp=False):
        self.site, self.direct, self.isp = site, direct, bool(isp) and not direct
        if self.isp and not os.environ.get("ISP_PROXY"):
            raise RuntimeError("ISP_PROXY not set (Secret isp-proxy)")
        if not direct and not self.isp and not resi.configured():
            raise RuntimeError("RESI_PROXY not set")
        self.session = corpus.kv_get(f"replies:{site}:session")
        self.bytes = 0
        self._new_jar()

    def _new_jar(self):
        self.jar = http.cookiejar.CookieJar()
        self.device = lihkg_api.device()
        self.chrome = None  # LIHKG: a curl_cffi session (its own cookies), made on the first request

    def fetch_chrome(self, url, headers, data=None):
        """curl_cffi impersonating Chrome (as sources.fetch_chrome for the stream's 時事台), one session per jar so
        the cookies carry over and the connection is kept alive. LIHKG answers urllib's TLS handshake with 429 from the
        cluster (2026-10-14); 香港討論區 through the proxy took 1.25 s a request on a new connection each time and
        0.43 s on a kept-alive one (2026-10-15). data: a form POST in the same session"""
        from curl_cffi import requests as cr
        if self.chrome is None:
            self.chrome = cr.Session(impersonate="chrome")
        h = {"Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}  # the User-Agent is curl_cffi's Chrome one
        h.update(headers)
        proxy = None if self.direct else self.proxy()
        kw = dict(headers=h, proxies={"http": proxy, "https": proxy} if proxy else None, timeout=40, allow_redirects=False)
        with self.isp_turn():
            r = self.chrome.post(url, data=data, **kw) if data is not None else self.chrome.get(url, **kw)
        size = getattr(r, "download_size", None)  # bytes on the wire (compressed); else the body
        self.bytes += (size if isinstance(size, (int, float)) and size > 0 else len(r.content)) + 800
        return r.status_code, r.text

    def isp_turn(self):
        """the ISP IP's lock for one request (a second client on it gets "Tunnel connection failed: 403"), or nothing"""
        if not self.isp:
            return __import__("contextlib").nullcontext()
        import sites_fetch
        return sites_fetch.isp_lock()

    def rotate(self):
        self.session = None
        self._new_jar()

    def proxy(self):
        if self.isp:
            return os.environ["ISP_PROXY"]
        if not self.session:
            self.session = resi.session_id(10)
        return resi.url("hk", session=self.session, lifetime="24h")

    def account(self):
        if not self.bytes:
            return
        if self.isp:
            resi.count_isp(self.bytes)
        elif not self.direct:
            resi.count(self.bytes, self.site)
        self.bytes = 0

    def fetch(self, url, headers):
        if self.site == "lihkg":
            return self.fetch_chrome(url, headers)
        if self.site == "discuss":
            h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
            h.update(headers)
            return self.fetch_chrome(url, h)
        h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8", "Accept-Encoding": "gzip, deflate"}
        h.update(headers)
        handlers = [NoRedirect, urllib.request.HTTPCookieProcessor(self.jar)]
        if not self.direct:
            proxy = self.proxy()
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        try:
            r = urllib.request.build_opener(*handlers).open(urllib.request.Request(url, headers=h), timeout=40)
            status, hd, raw = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, hd, raw = e.code, e.headers, e.read()
        self.bytes += len(raw) + 800  # + headers, TLS and proxy overhead (estimate)
        enc = hd.get("Content-Encoding", "")
        d = gzip.decompress(raw) if enc == "gzip" else zlib.decompress(raw) if enc == "deflate" else raw
        return status, d.decode("utf-8", "replace")

    def post(self, url, data, walled=lambda status, text: False):
        """a form POST in this session (香港討論區's 18+ notice), with the jar's cookies; one try"""
        if self.site == "discuss":
            try:
                _, text = self.fetch_chrome(url, {"User-Agent": UA, "Referer": url}, data=data)
            except Exception as e:
                if not self.direct and proxy_out(e):
                    raise out(self.site, e)
                raise
            time.sleep(DISCUSS_GAP)
            return text
        proxy = None if self.direct else self.proxy()
        handlers = [NoRedirect, urllib.request.HTTPCookieProcessor(self.jar)]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={
            "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": url})
        try:
            r = urllib.request.build_opener(*handlers).open(req, timeout=40)
            raw = r.read()
        except urllib.error.HTTPError as e:  # its answer is a 302 back to the thread
            raw = e.read()
        except Exception as e:
            if not self.direct and proxy_out(e):
                raise out(self.site, e)
            raise
        self.bytes += len(raw) + 800
        if self.site == "discuss":
            time.sleep(DISCUSS_GAP)
        return raw.decode("utf-8", "replace")

    def get(self, url, headers=None, walled=lambda status, text: False):
        walls = 0
        for attempt in range(8):
            if self.site == "lihkg" and self.direct and lihkg_api.blocked():  # another LIHKG reader was rate-limited
                raise Blocked(f"{url}: the server's IP is in LIHKG's back-off")
            try:
                status, text = self.fetch(url, dict(headers or {}))
                if self.site == "discuss":
                    time.sleep(DISCUSS_GAP)  # one request at a time, a pause between them
            except Exception as e:
                print(self.site, url[-70:], repr(e)[:120], flush=True)
                if not self.direct and not self.isp and proxy_out(e):
                    raise out(self.site, e)
                if (self.direct or self.isp) and attempt >= 2:
                    raise
                self.rotate()
                time.sleep(2)
                continue
            if status == 402 and not self.direct and not self.isp and resi.probe():  # a plain-http page: IPRoyal's 402 is the answer
                raise out(self.site, "HTTP 402 Payment Required through the proxy")
            if walled(status, text):
                if self.direct or self.isp:  # never retry into a rate limit from one fixed IP (the server's, the ISP IP)
                    if self.site == "lihkg" and self.direct:
                        lihkg_api.block(f"{url}: {status}")  # every LIHKG reader backs off
                    raise Blocked(f"{url}: {status}")
                walls += 1
                if self.site == "discuss" and walls >= DISCUSS_WALLS:  # 27 Oct - 17 Nov 2026 every residential IP got
                    break  # the wall: 8 sticky sessions per page, ~0.7 GB of yycstream a day for no reply (Wall below)
                self.rotate()  # this IP is walled: next sticky session
                continue
            if status in (200, 302) or (status == 404 and self.site == "discuss"):  # 香港討論區's 404: no such thread
                if not self.direct and not self.isp:
                    corpus.kv_set(f"replies:{self.site}:session", self.session)
                return text
            print(self.site, url[-70:], status, flush=True)
            self.rotate()
        raise Wall(url)


_ATTRS = r"""(?:"[^"]*"|'[^']*'|[^'">])*"""  # a tag's inside, quoted attribute values whole


def plain(s):
    """reply HTML → text without the quoted posts"""
    s = s or ""
    for _ in range(5):  # nested quotes: innermost first
        s2 = re.sub(r"<blockquote\b(?:(?!<blockquote\b).)*?</blockquote>", " ", s, flags=re.S | re.I)
        s2 = re.sub(r'<div class="quote"><h5>[^<]*</h5>', " ", s2)
        s2 = re.sub(r'<div class="post-ref">(?:(?!<div class="post-ref">).)*?</div><!--quote-end-->', " ", s2, flags=re.S)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)  # 香港討論區 keeps a commented-out copy of each picture
    # 香港討論區's attachments (2026-10-20): the file menu and 「圖片附件: name (time, size) / 該附件被下載次數 N」 + its
    # link are the forum's notes on the picture, not the post's words
    s = re.sub(r'<div class="t_attach"[^>]*>.*?</div></div>', " ", s, flags=re.S)
    s = re.sub(r"圖片附件:.*?該附件被下載次數 \d+(?:\s*<br />\s*https?://\S*?attachment\.php\?aid=\d+)?", " ", s, flags=re.S)
    s = re.sub(r"<img\b" + _ATTRS + r"\balt=\"([^\"]+)\"" + _ATTRS + ">", r"\1", s)  # emoji alt text
    s = re.sub(r"<br\s*/?>|</p>|</div>", "\n", s, flags=re.I)
    # a quoted attribute may hold ">" (onload="if(this.width >screen.width*0.8) …"): the tag ends after the quotes
    s = html.unescape(re.sub(r"<" + _ATTRS + ">", "", s))
    return re.sub(r"[ \t\u3000\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def ts(epoch):
    return datetime.fromtimestamp(epoch, HKT)


def state(site, tid):
    return corpus.kv_get(f"replies:{site}:{tid}", {"read": 0, "total": None, "checked": 0})


def save_state(site, tid, st):
    corpus.kv_set(f"replies:{site}:{tid}", st)


def collected(site_like):
    """threads the collectors filed in the last COLLECTED_DAYS: {url: (row, engagement)}"""
    since = (now() - timedelta(days=COLLECTED_DAYS)).strftime("%Y-%m-%d")
    out = {}
    for r in corpus.db().execute("SELECT * FROM posts WHERE thread IS NULL AND url LIKE %s AND day >= %s ORDER BY id", (site_like, since)):
        out[r["url"]] = r
    return out


# ---------------- LIHKG ----------------

def lihkg_json(px, path, referer="https://lihkg.com/"):
    lihkg_api.pace(px.direct)  # the one clock every LIHKG reader shares
    text = px.get("https://lihkg.com/api_v2/" + path, walled=lihkg_api.walled, headers=lihkg_api.headers(px.device, referer))
    d = json.loads(text)
    if not d.get("success"):
        raise RuntimeError(f"LIHKG {path}: {text[:160]}")
    return d["response"]


def lihkg_item(t):
    """a thread from the API → the item as sources.lihkg files it (same url, sid and source for 時事台)"""
    cat = t.get("cat_id")
    name = (t.get("category") or {}).get("name") or "時事台"
    return {"sid": "lihkg_current" if cat == 5 else f"lihkg_cat{cat}", "source": f"LIHKG {name}", "section": name,
            "title": t.get("title") or "", "url": f"https://lihkg.com/thread/{t['thread_id']}/page/1",
            "published": ts(t["create_time"]) if t.get("create_time") else None,
            "author": t.get("user_nickname") or None,
            "engagement": {k: t[f] for k, f in (("replies", "no_of_reply"), ("likes", "like_count"), ("dislikes", "dislike_count"))
                           if isinstance(t.get(f), int)}}


def lihkg_replies(r, item):
    """a thread page's replies (the opening post's text goes into item) → comments for corpus.add_comments, each with
    LIHKG's like and dislike counts"""
    out = []
    for m in r.get("item_data") or []:
        n = int(m.get("msg_num") or 0)
        if n == 1:
            item["text"] = plain(m.get("msg"))
        elif n > 1:
            out.append({"id": n, "author": m.get("user_nickname") or (m.get("user") or {}).get("nickname"),
                        "text": plain(m.get("msg")), "published": ts(m["reply_time"]) if m.get("reply_time") else None,
                        "likes": m.get("like_count") if isinstance(m.get("like_count"), int) else None,
                        "dislikes": m.get("dislike_count") if isinstance(m.get("dislike_count"), int) else None})
    return out


def due(st, total):
    """does a thread need fetching: its reply count grew, or it is unknown and unchecked for 3 h"""
    if st["total"] is None:
        return True
    if total is None:
        return time.time() - st.get("checked", 0) > 3 * 3600
    return total > st["total"]


CATS = {}  # thread id → its board, as its pages state it (the id walk learns hidden boards from it)


def lihkg_thread(px, tid, known, allowed):
    """fetch the new pages of one thread → (new rows, pages used). LIHKG's no_of_reply counts the opening post.
    known: the listing's {replies, created} or a bare reply count"""
    st = state("lihkg", tid)
    if not due(st, known.get("replies") if isinstance(known, dict) else known):
        return 0, 0
    url = f"https://lihkg.com/thread/{tid}/page/1"
    capture = st["total"] is None
    if capture:
        twopass.reading(url)  # its first scoring waits for the full read
    page, pages, new, item = st["read"] // LIHKG_PER_PAGE + 1, 0, 0, None
    while pages < allowed:
        r = lihkg_json(px, f"thread/{tid}/page/{page}?order=reply_time", f"https://lihkg.com/thread/{tid}/page/{page}")
        pages += 1
        item = item or lihkg_item(r)
        CATS[tid] = r.get("cat_id")
        comments = lihkg_replies(r, item)  # the page's earlier replies too: their likes/dislikes are refreshed
        total = r.get("no_of_reply")
        new += len(corpus.add_comments(item, comments, total=max(int(total) - 1, 0) if total else None))
        st["read"] = max([c["id"] for c in comments] + [st["read"], 1])
        st["at"] = time.time()
        last = page >= int(r.get("total_page") or 1)
        if last:
            st["total"], st["checked"] = total, time.time()
            (twopass.captured if capture else twopass.fetched)(url)
        save_state("lihkg", tid, st)
        if last:
            break
        page += 1
    return new, pages


def lihkg_list(px, cat, floor):
    """a board's threads (最新: every board) with a reply since floor (epoch) → ({thread id: {replies, created}}, pages)"""
    out, pages = {}, 0
    kind = "latest" if cat == LIHKG_LATEST else "category"
    for page in range(1, 60):
        items = lihkg_json(px, f"thread/{kind}?cat_id={cat}&page={page}&count=60&type=now").get("items") or []
        pages += 1
        for t in items:
            if t.get("last_reply_time", 0) >= floor:
                out[int(t["thread_id"])] = {"replies": t.get("no_of_reply"), "created": t.get("create_time")}
        if not items or sum(t.get("last_reply_time", 0) < floor for t in items) > len(items) // 2:
            break  # the list is in latest-reply order (loosely): past the floor
    return out, pages


def lihkg_collected():
    out = {}
    for url, row in collected("https://lihkg.com/thread/%").items():
        m = re.search(r"/thread/(\d+)/", url)
        if m:
            created = None
            try:
                created = datetime.fromisoformat(row["published"]).timestamp() if row["published"] else None
            except ValueError:
                pass
            out[int(m.group(1))] = {"replies": (json.loads(row["engagement"]) if row["engagement"] else {}).get("replies"),
                                    "created": created}
    return out


def lihkg_due(st, info, now_ts):
    """is a listed thread due for reading in this run: new (never read), its reply count grew and its last read is
    REREAD old, or it grew and its post time + MARK passed since its last read (the second scoring's replies).
    info: {replies, created} from a listing, or a bare reply count"""
    replies, created = (info.get("replies"), info.get("created")) if isinstance(info, dict) else (info, None)
    if isinstance(info, dict) and info.get("force") and st["total"] is not None and replies:
        return replies > st["total"]  # its second scoring waits for this read (no reply since: nothing to read)
    if st["total"] is None or replies is None:
        return due(st, replies)
    if replies <= st["total"]:
        return False
    last = st.get("at") or st.get("checked") or 0
    return now_ts - last >= REREAD or bool(created and last < created + MARK <= now_ts)


def lihkg_boards():
    """最新, then the boards it hides (learnt from threads only the id walk found)"""
    return {c: None for c in [LIHKG_LATEST] + list(corpus.kv_get("replies:lihkg:hidden_cats") or [])}


def gaps(site, listed):
    """thread ids between the last one seen (kv replies:<site>:max_id) and the newest listed that no listing showed
    (a board the listings don't show, or a thread deleted since): {id: {}} to read directly. Both sites' thread ids are
    sequential site-wide. The first run only starts the walk"""
    top = max(listed, default=None)
    last = corpus.kv_get(f"replies:{site}:max_id")
    if top is None:
        return {}
    if last is None or top <= last:
        corpus.kv_set(f"replies:{site}:max_id", max(top, last or 0))
        return {}
    return {t: {"replies": None, "created": None, "gap": True} for t in range(last + 1, top) if t not in listed}


# ---------------- 香港討論區 ----------------

def discuss_walled(status, text):
    """the site's reCAPTCHA wall (HTTP 202), or Cloudflare's challenge in front of it (the cluster's own IP, 29 Oct 2026)"""
    return status == 202 or ("請確認身份" in text and "g-recaptcha" in text) \
        or (status in (403, 503) and bool(re.search(r"<title>(Just a moment|請稍候)", text[:3000])))


def discuss_passes(route):
    """does the site answer this route now ("direct": the server's own IP, "isp": YYC's ISP IP)? One request for the
    archiver's index"""
    try:
        px = Proxied("discuss", direct=route == "direct", isp=route == "isp")
        status, text = px.fetch(DISCUSS_ARCHIVER, {})
        px.account()
    except Exception as e:  # noqa: BLE001
        print(f"discuss {route}:", repr(e)[:120], flush=True)
        return False
    ok = status == 200 and not discuss_walled(status, text) and "fid-" in text
    print(f"discuss {route}:", status, "passes" if ok else "walled", flush=True)
    return ok


def discuss_route():
    """the route of this run: the server's own IP when the site answers it (free), else YYC's ISP IP (flat rate, env
    ISP_PROXY), else yycstream (per GB) (Deyao, 2026-10-29: IPRoyal bandwidth). On 29 Oct the cluster's IP got
    Cloudflare's challenge and the ISP IP the reCAPTCHA wall; from 27 Oct to 17 Nov every residential IP got the wall
    (HTTP 202) while the ISP IP passed again (17 Nov: 200)"""
    if discuss_passes("direct"):
        return "direct"
    if os.environ.get("ISP_PROXY") and discuss_passes("isp"):
        return "isp"
    return "hk"


def count(s):
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([KkMm萬]?)", str(s or ""))
    if not m:
        return None
    return int(float(m.group(1).replace(",", "")) * {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "萬": 1e4}.get(m.group(2), 1))


def discuss_boards(px):
    """every board of the site: the archiver's forum index ({fid: name}), kept a day in kv replies:discuss:boards"""
    kept = corpus.kv_get("replies:discuss:boards") or {}
    if kept.get("boards") and time.time() - kept.get("at", 0) < 86400:
        return {int(k): v for k, v in kept["boards"].items()}
    try:
        text = px.get(DISCUSS_ARCHIVER, walled=discuss_walled)
        boards = {int(f): html.unescape(n).strip() for f, n in re.findall(r'\?fid-(\d+)\.html">([^<]+)<', text)}
    except (Wall, RuntimeError, ProxyOut) as e:
        print("discuss boards", repr(e)[:160], flush=True)
        boards = {}
    finally:
        px.account()
    if not boards:  # the index failed: the last list, else the news boards
        return {int(k): v for k, v in (kept.get("boards") or DISCUSS_FIDS).items()}
    corpus.kv_set("replies:discuss:boards", {"at": time.time(), "boards": {str(k): v for k, v in boards.items()}})
    return boards


RUN = {}  # this run's facts for the listers: max_id = the newest thread id seen before it (None on the first run)


def board_due(fid):
    """is a board listed in this run: one whose first page changed last time every run; a quiet one every 2, 4, 8 …
    BOARD_MAX_RUNS runs, and at least every BOARD_MAX_EVERY seconds (kv replies:discuss:board:<fid>)"""
    b = corpus.kv_get(f"replies:discuss:board:{fid}")
    if not b:
        return True
    if b["since"] + 1 >= b["every"] or time.time() - b["at"] >= BOARD_MAX_EVERY:
        return True
    corpus.kv_set(f"replies:discuss:board:{fid}", dict(b, since=b["since"] + 1))
    return False


def overdue(b, now_ts):
    """how overdue a board's listing is: time since it was last listed over its own interval (every runs, at most
    BOARD_MAX_EVERY), so a busy board listed 10 min ago (2.0) goes before a quiet one listed 61 min ago (1.02);
    a board never listed first. Strictly longest-ago first listed every one of 449 boards once in 48-71 min, the busy
    ones too (2026-10-15)"""
    if not b:
        return float("inf")
    return (now_ts - b.get("at", 0)) / min(b.get("every", 1) * RUN_PERIOD, BOARD_MAX_EVERY)


def discuss_listing(px, fid, floor=None):
    """a board's threads from its archiver pages (latest reply first, 50 a page, with reply counts) → ({tid: info},
    pages). Pages on until BOUNDARY threads we know with an unchanged count were passed (stickies aside, the rest of
    the board had no reply since); a thread we never saw above that is read, one below it only has its count recorded
    (state: read once it grows). LIST_MAX_PAGES at most. The site's first listing of a board is its baseline: page 1,
    each thread older than the id walk's start recorded, not read."""
    if not board_due(fid):
        return {}, 0
    prev = corpus.kv_get(f"replies:discuss:board:{fid}")
    baseline = prev is None
    out, pages, sig, unchanged = {}, 0, None, 0
    for page in range(1, LIST_MAX_PAGES + 1):
        text = px.get(f"{DISCUSS_ARCHIVER}?fid-{fid}.html" if page == 1 else f"{DISCUSS_ARCHIVER}?fid-{fid}-page-{page}.html",
                      walled=discuss_walled)
        pages += 1
        rows = [(int(t), plain(ti), int(n)) for t, ti, n in
                re.findall(r'<a href="archiver/\?tid-(\d+)\.html">(.*?)</a>\s*<em>\((\d+) 篇回覆\)</em>', text, re.S)]
        if page == 1:
            sig = hashlib.sha1(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
        for tid, title, n in rows:
            st = state("discuss", tid)
            new = RUN.get("max_id") is not None and tid > RUN["max_id"]  # the site's first run: nothing is new
            if st["total"] is not None and n <= st["total"]:
                unchanged += 1
            elif st["total"] is None and not new and (baseline or unchanged >= BOUNDARY):
                save_state("discuss", tid, {"read": 0, "total": n, "checked": time.time()})  # read once it grows
            out[tid] = {"title": title or None, "replies": n}
        if baseline or len(rows) < DISCUSS_LIST_PER_PAGE or unchanged >= BOUNDARY:
            break
    every = 1 if baseline or sig != prev.get("sig") else min(prev["every"] * 2, BOARD_MAX_RUNS)
    corpus.kv_set(f"replies:discuss:board:{fid}", {"every": every, "since": 0, "at": time.time(), "sig": sig})
    return out, pages


def discuss_page(text):
    """a mobile thread page → (meta, [posts])"""
    meta = {}
    m = re.search(r'<meta property="og:title" content="([^"]*)"', text)
    meta["title"] = re.sub(r"\s+-\s+香港討論區\s.*$", "", html.unescape(m.group(1))).strip() if m else None
    m = re.search(r'<meta name="publish_date" content="([^"]+)"', text)
    meta["published"] = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z").astimezone(HKT) if m else None
    m = re.search(r'thread-lastpage-btn"[^>]*href="[^"]*page=(\d+)"', text)
    meta["pages"] = int(m.group(1)) if m else None
    m = re.search(r"action=post&amp;do=reply&amp;fid=(\d+)", text)
    meta["fid"] = int(m.group(1)) if m else None
    m = re.search(r'action=forum&amp;fid=\d+">([^<]+)</a>', text)
    meta["board"] = html.unescape(m.group(1)).strip() if m else None
    posts = []
    for blk in re.split(r'<div class="post[^"]*" id="post_pid', text)[1:]:
        pid = re.match(r"(\d+)", blk)
        floor = re.search(r'data-floor="(\d+)"', blk[:300])
        au = re.search(r'class="host-name"[^>]*>(?:\s*<div class="host-name-title">[^<]*</div>)?\s*([^<]+?)\s*</a>', blk)
        when = re.search(r'<div class="info-date">\s*(\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d\d)', blk)
        body = re.search(r'<div class="post-content"[^>]*>(.*?)</div>\s*(?:<div class="quote-link-wapper">|<div class="post-tool">)', blk, re.S)
        if not (pid and floor):
            continue
        posts.append({"id": pid.group(1), "floor": int(floor.group(1)), "author": au.group(1) if au else None,
                      "published": datetime.strptime(when.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=HKT) if when else None,
                      "text": plain(body.group(1)) if body else ""})
    return meta, posts


PRINTABLE_MAX = 100  # the printable view (viewthread.php?action=printable) holds a thread's first 100 posts


def discuss_printable(text):
    """the printable view → (title, [posts]); a post's number is its place in the thread"""
    m = re.search(r"<b>標題: </b>(.*?)<b>", text, re.S)
    title = plain(m.group(1)) if m else None
    text = text.split('<hr noshade size="2" width="100%" color="#000000">')[0]
    posts = []
    for i, seg in enumerate(text.split('<hr noshade size="2" width="100%" color="#808080">')[1:], 1):
        au = re.search(r"<b>作者: </b>\s*(.*?)\s*&nbsp;", seg, re.S)
        when = re.search(r"<b>時間: </b>\s*(\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d\d)", seg)
        body = seg.split("<br /><br />", 1)[1] if "<br /><br />" in seg else ""
        posts.append({"floor": i, "author": plain(au.group(1)) if au else None,
                      "published": datetime.strptime(when.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=HKT) if when else None,
                      "text": plain(body)})
    return title, posts


def discuss_thread(px, tid, info, allowed):
    """file a thread's new replies → (new rows, requests used). Posts 1-100 come from the printable view in one request
    (~5-14 KB), later ones from the mobile thread pages (15 posts, ~30 KB each). The listing's 回覆 count excludes the
    opening post. A reply's id is its post number (#c<n>). info["force"]: read its newest page even when no listing
    showed it grew (its second scoring is due: twopass). A thread's first full read marks it captured (twopass), a
    later complete read re-read."""
    st = state("discuss", tid)
    total = info.get("replies")
    if not info.get("force") and not due(st, total):
        return 0, 0
    pages, new = 0, 0
    capture = st["read"] == 0
    url = f"https://www.discuss.com.hk/archiver/?tid-{tid}.html"
    item = {"sid": "discuss", "source": "香港討論區", "section": info.get("board"), "title": info.get("title"),
            "url": f"https://www.discuss.com.hk/archiver/?tid-{tid}.html", "published": None, "author": info.get("author"),
            "engagement": {k: info[k] for k in ("replies", "views") if isinstance(info.get(k), int)}}

    def file(posts):
        nonlocal new
        comments = []
        for p in posts:
            if p["floor"] == 1:
                item["text"], item["published"] = p["text"], p["published"]
                item["author"] = item["author"] or p["author"]
            elif p["floor"] > st["read"]:
                comments.append({"id": p["floor"], "author": p["author"], "text": p["text"], "published": p["published"]})
        if item["title"]:
            new += len(corpus.add_comments(item, comments, total=total))
        st["read"] = max([c["id"] for c in comments] + [st["read"], 1])

    more = True
    if st["read"] < PRINTABLE_MAX:
        printable = f"https://www.discuss.com.hk/viewthread.php?action=printable&tid={tid}"
        text = px.get(printable, walled=discuss_walled)
        gate = re.search(r'action="agreement18\.php".*?name="catid" value="(\d+)"', text, re.S)
        if gate:  # a betting board's 18+ notice: agree (the session's cookie keeps it) and read again
            px.post("https://www.discuss.com.hk/agreement18.php", {"catid": gate.group(1), "adult": "agreed"})
            text = px.get(printable, walled=discuss_walled)
            pages += 2
        title, posts = discuss_printable(text)
        pages += 1
        item["title"] = item["title"] or title
        if not posts or not item["title"]:
            st["checked"] = time.time()
            save_state("discuss", tid, dict(st, total=total))  # deleted, hidden or moved: don't retry until it grows
            return 0, pages
        if capture:
            twopass.reading(url)  # it exists: its first scoring waits for the full read
        file(posts)
        more = len(posts) >= PRINTABLE_MAX  # every later post too, over as many runs as a long thread needs
    page = st["read"] // DISCUSS_PER_PAGE + 1
    while more and pages < allowed and time.time() < RUN.get("deadline", float("inf")):  # the rest in the next run
        text = px.get(f"https://m.discuss.com.hk/index.php?action=thread&tid={tid}&page={page}",
                      headers={"User-Agent": MUA}, walled=discuss_walled)
        pages += 1
        meta, posts = discuss_page(text)
        item["title"] = item["title"] or meta["title"]
        item["section"] = (RUN.get("boards") or DISCUSS_FIDS).get(meta["fid"]) or item["section"] or meta["board"]
        item["published"] = item["published"] or meta["published"]
        file(posts)
        more = bool(posts) and page < (meta["pages"] or page)
        save_state("discuss", tid, st)
        page += 1
    if not more:
        st["total"], st["checked"] = (total if total is not None else st["read"] - 1), time.time()
    save_state("discuss", tid, st)
    if not more:
        (twopass.captured if capture else twopass.fetched)(url)
    return new, pages


def discuss_collected():
    out = {}
    for url, row in collected("https://www.discuss.com.hk/%").items():
        m = re.search(r"tid[-=](\d+)", url)
        if m:
            eng = json.loads(row["engagement"]) if row["engagement"] else {}
            out[int(m.group(1))] = {"title": row["title"], "replies": eng.get("replies"), "views": eng.get("views"),
                                    "author": row["author"], "board": row["section"]}
    return out


SITES = {  # site → (max requests a run: site protection, None = only RUN_SECONDS; boards {id: name}; list, thread, collected)
    "lihkg": lambda: (LIHKG_MAX_PAGES, lihkg_boards(), lihkg_list, lihkg_thread, lihkg_collected),
    "discuss": lambda: (None, discuss_boards, lambda px, fid, floor: discuss_listing(px, fid, floor),
                        lambda px, tid, info, allowed: discuss_thread(px, tid, dict(info or {}), allowed), discuss_collected),
}


def run(site, since_default):
    """one pass: list the boards that are due (each from its own last complete listing), add the collectors' threads,
    then read the threads: the collectors' ones first, then in the listings' latest-reply order, then the pending ones. Threads not reached (RUN_SECONDS,
    LIHKG's request cap) wait in kv replies:<site>:pending for the next run."""
    cap, boards, lister, reader, coll = SITES[site]()
    cap = cap if cap is not None else float("inf")
    # LIHKG goes direct, unless it rate-limited the server's IP (any reader's request): then through the proxy;
    # 香港討論區 on the route that passes this run (discuss_route: direct, the ISP IP, yycstream)
    route = discuss_route() if site == "discuss" else "direct" if not lihkg_api.blocked() else "hk"
    px = Proxied(site, direct=route == "direct", **({"isp": True} if route == "isp" else {}))
    blocked = None
    if callable(boards):  # 香港討論區: every board of the forum index
        boards = boards(px)
    RUN.clear()
    RUN.update(max_id=corpus.kv_get(f"replies:{site}:max_id"), boards=boards)
    pending = {int(k): v for k, v in (corpus.kv_get(f"replies:{site}:pending") or {}).items()}
    want, lists, errors, new, pages = {}, 0, 0, 0, 0
    run_start, complete = time.time(), True
    if site == "discuss":  # the most overdue boards first; listing takes at most half the run (the rest wait)
        boards = dict(sorted(boards.items(), key=lambda kv: overdue(corpus.kv_get(f"replies:discuss:board:{kv[0]}"), run_start),
                             reverse=True))
    try:
        for b, name in boards.items():
            if site == "discuss" and time.time() > run_start + LIST_SHARE * RUN_SECONDS:
                complete = False
                break
            last = corpus.kv_get(f"replies:{site}:listed:{b}")
            started = time.time()
            floor = (last - SLACK) if last else started - since_default
            try:
                got, n = lister(px, b, floor)
            except (Blocked, ProxyOut) as e:
                blocked = e
                break
            except Wall as e:
                print(site, "board", b, repr(e)[:160], flush=True)
                if site == "discuss":  # the route is walled (DISCUSS_WALLS sessions): the next run tries again
                    blocked = e
                    break
                continue
            except (RuntimeError, ValueError) as e:
                print(site, "board", b, repr(e)[:160], flush=True)
                continue
            finally:
                px.account()
            lists += n
            for tid, info in got.items():
                want[tid] = dict(info, board=name) if isinstance(info, dict) else info
            corpus.kv_set(f"replies:{site}:listed:{b}", started)
        walk = {}
        if not blocked:
            walk = gaps(site, set(want))
            want.update(walk)
        first = coll()
        if site == "lihkg":  # second scorings due: a thread in this listing is read now; one not in it had no reply
            for u in twopass.due_for_fetch("https://lihkg.com/thread/%"):  # since the last listing (current)
                m = re.search(r"/thread/(\d+)/", u)
                if m and int(m.group(1)) in want and isinstance(want[int(m.group(1))], dict):
                    want[int(m.group(1))]["force"] = True
        forced = {}
        if site == "discuss":  # second scorings due: read now (a thread listed with no new reply since: nothing to read)
            for u in twopass.due_for_fetch("https://www.discuss.com.hk/archiver/%"):
                m = re.search(r"tid-(\d+)", u)
                tid = int(m.group(1)) if m else None
                if tid is None:
                    continue
                known = (want.get(tid) or {}).get("replies") if isinstance(want.get(tid), dict) else None
                if known is not None and state(site, tid)["total"] == known:
                    twopass.fetched(u)
                else:
                    forced[tid] = dict(want.get(tid) or {}, force=True)
        # the listing's reply count is the current one; a collected thread's count is from when it was filed, so it
        # must never replace the listing's (it hid every later reply until the thread left the collected window)
        queue = {**pending, **first}
        for tid, info in want.items():
            queue[tid] = dict(queue[tid], **info) if isinstance(info, dict) and isinstance(queue.get(tid), dict) else info
        if site == "lihkg":  # newest first: the id walk's threads, then by post time (a new thread is one page; an old
            # thread's first full read can take many, and waits behind them)
            want = dict(sorted(want.items(), key=lambda kv: (bool((kv[1] or {}).get("gap")) if isinstance(kv[1], dict) else False,
                                                             ((kv[1] or {}).get("created") or 0) if isinstance(kv[1], dict) else 0,
                                                             kv[0]), reverse=True))
        order = list(dict.fromkeys([*first, *want, *pending]))  # the collectors' threads, then latest reply first
        if site == "discuss":  # new threads first (newest first), then the second scorings due, then the rest
            for tid, info in forced.items():
                queue[tid] = dict(queue[tid], **info) if isinstance(queue.get(tid), dict) else info
            newest = sorted((t for t in want if RUN["max_id"] is not None and t > RUN["max_id"]), reverse=True)
            order = list(dict.fromkeys([*newest, *forced, *order]))
        keep = lambda: {str(t): ({k: v for k, v in queue[t].items() if k != "force"} if isinstance(queue[t], dict) else queue[t])
                        for t in sorted(queue, reverse=True)[:3000] if t not in first}  # newest 3000;
        corpus.kv_set(f"replies:{site}:pending", keep())  # the collectors' threads come back on their own
        allowed, deadline = cap, time.time() + RUN_SECONDS
        RUN["deadline"] = deadline
        for tid in order:
            if allowed <= 0 or time.time() > deadline or blocked:
                complete = False
                break
            if site == "lihkg" and not lihkg_due(state(site, tid), queue[tid], time.time()):
                continue  # not due in this run: stays pending
            try:
                n, p = reader(px, tid, queue[tid], allowed)
            except (Blocked, ProxyOut) as e:
                blocked = e
                break
            except Wall as e:
                if site == "discuss":  # the route is walled: the thread waits for the next run
                    blocked = e
                    break
                errors += 1
                print(site, tid, repr(e)[:160], flush=True)
                n, p = 0, 1
            except Exception as e:
                errors += 1
                print(site, tid, repr(e)[:160], flush=True)
                n, p = 0, 1
            finally:
                px.account()
            queue.pop(tid)
            new, pages, allowed = new + n, pages + p, allowed - p
        left = keep()
        corpus.kv_set(f"replies:{site}:pending", left)
        if site == "lihkg":
            hidden = sorted({CATS[t] for t in walk if CATS.get(t) not in (None, LIHKG_LATEST)}
                            | set(corpus.kv_get("replies:lihkg:hidden_cats") or []))
            if hidden:
                corpus.kv_set("replies:lihkg:hidden_cats", hidden)
        if walk or want:
            corpus.kv_set(f"replies:{site}:max_id", max([corpus.kv_get(f"replies:{site}:max_id") or 0, *want]))
        if site == "lihkg":
            if complete and not blocked:
                twopass.fetched_all("https://lihkg.com/thread/%", datetime.fromtimestamp(run_start, HKT))
        if isinstance(blocked, ProxyOut):
            print(site, "stopped:", blocked, flush=True)
        elif blocked and site == "discuss":
            print(site, f"walled on route {route}; stopped until the next run:", repr(blocked)[:160], flush=True)
        elif blocked:
            print(site, "rate-limited on the server's IP; proxy for 26 h:", repr(blocked)[:160], flush=True)
    finally:
        px.account()
    return {"threads": len(want) + len(first), "list_pages": lists, "thread_requests": pages, "new_replies": new,
            "errors": errors, "pending": len(left), "direct": px.direct, "route": route,
            "blocked": bool(blocked) and not isinstance(blocked, ProxyOut), "proxy_out": isinstance(blocked, ProxyOut),
            "proxy_mb_today": mb_today(site)}


def mb_today(site):
    return round(float(corpus.kv_get(f"proxy:bytes:{site}:{now().strftime('%Y-%m-%d')}", 0)) / 1e6, 2)


def stats():
    c = corpus.db()
    for r in c.execute("""SELECT website, COUNT(*), MIN(day), MAX(day), COUNT(DISTINCT thread) FROM posts
                          WHERE thread IS NOT NULL AND source_type='Forum' GROUP BY website"""):
        print(f"{r[0]}: {r[1]} reply rows in {r[4]} threads, days {r[2]} … {r[3]}")
    for k, v in c.execute("SELECT k, v FROM kv WHERE k LIKE 'proxy:bytes:lihkg:%%' OR k LIKE 'proxy:bytes:discuss:%%' ORDER BY k"):
        print(k, round(v / 1e6, 2), "MB")
    for site in ("lihkg", "discuss"):
        print(site, corpus.kv_get(f"replies:{site}:status"))


# what the tag stripper left of 香港討論區's pictures in texts stored before 2026-10-20 (plain() drops them now)
DISCUSS_JUNK = [
    re.compile(r'\S*screen\.width\*0\.8\) this\.width=screen\.width\*0\.8" alt="" />(?:-->)?'),
    re.compile(r"圖片附件:.*?該附件被下載次數 \d+(?:\s*https?://\S*?attachment\.php\?aid=\d+)?", re.S),
    # the file menu: 「IMG_1.jpg (152.5 KB) 2024-9-18 17:17」 (「⭕⭕水母 (1).png」: a name may end in " (n)")
    re.compile(r"\S*(?: \(\d+\))?\.(?:jpe?g|png|gif|webp|heic|bmp) \([\d.]+ [KMG]B\)\s+\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}", re.I),
]


def discuss_clean(text):
    for rx in DISCUSS_JUNK:
        text = rx.sub(" ", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def discuss_clean_stored():
    """the stored 香港討論區 posts and replies whose text holds picture junk get it cleaned: → rows changed"""
    rows = corpus.db().execute("""SELECT id, text FROM posts WHERE url LIKE 'https://www.discuss.com.hk/%%'
                                  AND (text LIKE '%%screen.width*0.8%%' OR text LIKE '%%該附件被下載次數%%')""").fetchall()
    n = 0
    for r in rows:
        t = discuss_clean(r["text"])
        if t != r["text"]:
            corpus.set_text(r["id"], t)
            n += 1
    return n


def main(argv):
    if argv == ["discuss-clean"]:
        return print("discuss-clean:", discuss_clean_stored(), "rows cleaned")
    since = 3 * 3600
    if "--since" in argv:
        v = argv[argv.index("--since") + 1]
        since = int(v[:-1]) * 3600 if v.endswith("h") else int(v)
    if "stats" in argv:
        return stats()
    for site in [a for a in argv if a in ("lihkg", "discuss")] or ["discuss", "lihkg"]:
        with corpus.lock(f"replies:{site}", wait=False) as got:  # one run per site at a time
            if not got:
                print(f"another replies_fetch {site} run is in progress", flush=True)
                continue
            run_sites([site], since)


def run_sites(argv, since):
    sites = [a for a in argv if a in ("lihkg", "discuss")] or ["discuss", "lihkg"]
    for site in sites:
        t = time.time()
        try:
            res = run(site, since)
        except Exception as e:
            res = {"error": repr(e)[:300]}
        res["seconds"] = round(time.time() - t)
        corpus.kv_set(f"replies:{site}:status", dict(res, at=now().isoformat(timespec="seconds")))
        print(site, json.dumps(res, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
