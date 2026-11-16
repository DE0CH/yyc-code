#!/usr/bin/env python3
"""Instagram and Threads accounts for the YYC stream (K-Matrix parity: its alerts link posts on both).

Logged out, Instagram's profile page carries the account's 12 newest posts as data (polaris_ordered_timeline_connection:
code, caption, pk) when it is fetched from a residential IP with a Chrome fingerprint (curl_cffi); ~163 KB compressed per
profile (measured 2026-10-12). A post's time is not in that data, but Instagram media ids encode it: the top bits of
pk are milliseconds since Instagram's epoch (2011-08-24 21:07:01.721 UTC), like Twitter ids.

Threads (Meta, same approach): the logged-out profile page (threads.com/@user, ~152 KB compressed) carries the newest
posts with code, caption and taken_at; accounts with "platform": "threads".

Route (Deyao, 2026-10-29: IPRoyal bandwidth): YYC's IPRoyal ISP IP first (ISP_PROXY, flat rate, Hong Kong; both sites
answered it with posts from the cluster on 29 Oct, while the cluster's own IP got Instagram's login wall and Threads'
429), one client at a time under the 'yyc youtube' lock that every ISP-IP job takes, queued for it in Postgres (isp_turn);
its bytes go to kv isp:bytes. An account the ISP IP can't serve (the lock busy for ISP_WAIT s, a tunnel or HTTP error,
a login wall) is read through RESI_PROXY (YYC's one IPRoyal residential sub-user `yycstream`, Hong Kong exits; resi.py), its
bytes counted under the sites "instagram" and "threads"; after ISP_FAILS such accounts in a run the rest go straight to
RESI_PROXY. Writes the document instagram.json (corpus.file_put), which sources.py reads.
Accounts: the instagram and threads rows of watchlist.json (rows added on or after watchlist.WATCH_START count their
traffic for the watching's daily safety stop, watchlist.py, and wait for the next day once it is reached). The CronJob runs every 15 minutes: an account that posted within the last 24 h
is read every ACTIVE_MIN minutes, the others every QUIET_MIN (every_minutes; Deyao, 2026-10-29). A read whose posts are all newer
than the newest post known (more than 12 posts since the last read: the logged-out profile gives no older page) pages
back through TikHub's user posts (/instagram/v2/fetch_user_posts, 12 a page, YYC's key) until it meets a known post,
so nothing between two reads is lost. The file keeps each account's posts of the last KEEP_H hours.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone

import corpus
import resi
import tikhub_fetch

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(HERE, "data"))
HKT = timezone(timedelta(hours=8))
IG_EPOCH_MS = 1314220021721
ACTIVE_MIN = 60    # minutes between reads of an account that posted within the last 24 h
QUIET_MIN = 360    # … of the others
ISP_WAIT = 60      # seconds a read waits for the ISP IP's lock before it goes through RESI_PROXY
RUN_SECONDS = 600  # reads start only this long into a run (the CronJob's deadline is 840 s); the rest wait, as not due
ISP_TRIES = 3      # the ISP IP answers "CONNECT tunnel failed, response 403" while another client holds it
ISP_FAILS = 3      # accounts the ISP IP couldn't serve in one run before the rest go straight to RESI_PROXY
KEEP_H = 6
TIKHUB = "https://api.tikhub.io"


def every_minutes(times, now_ts):
    """minutes between reads of an account from its posts' times (epoch seconds): ACTIVE_MIN when one is within 24 h"""
    return ACTIVE_MIN if any(t > now_ts - 86400 for t in times or ()) else QUIET_MIN


def tikhub(path, params):
    """one TikHub API call (YYC's key, TIKHUB_API) → its "data" """
    import costlog
    import time
    from curl_cffi import requests
    for attempt in range(3):
        r = requests.get(TIKHUB + path, params=params, timeout=90,
                         headers={"Authorization": "Bearer " + os.environ["TIKHUB_API"], "Accept": "application/json"})
        costlog.record("tikhub", path, status=r.status_code, ok=r.status_code == 200)  # the cost profile
        if r.status_code == 200:
            return r.json().get("data") or {}
        time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"TikHub {path} HTTP {r.status_code}: {r.text[:120]}")


def gap_posts(username, newest_ts, pages=30):
    """the account's posts newer than newest_ts through TikHub's paginated user posts → ([{url, text, published, ts}], calls)"""
    out, tok, calls = {}, None, 0
    for _ in range(pages):
        d = tikhub("/api/v1/instagram/v2/fetch_user_posts", dict({"username": username}, **({"pagination_token": tok} if tok else {})))
        calls += 1
        found, stack, tok = [], [d], None
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                tok = tok or o.get("pagination_token")
                t, c = o.get("taken_at") or o.get("taken_at_timestamp"), o.get("code") or o.get("shortcode")
                if isinstance(t, int) and c and len(c) >= 8:
                    found.append((c, t, " ".join((((o.get("caption") or {}).get("text") if isinstance(o.get("caption"), dict) else "") or "").split()),
                                  tikhub_fetch.meta_media(o, c)))
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
        for c, t, text, (pics, clips) in found:
            if t > newest_ts:
                out[c] = {"url": f"https://www.instagram.com/p/{c}/", "text": text, "ts": t, "images": pics, "clips": clips,
                          "published": datetime.fromtimestamp(t, HKT).isoformat(timespec="minutes")}
        # pinned posts can be old: stop once a page holds 2 or more posts at or before the newest known
        if not found or not tok or sum(1 for _c, t, _x, _m in found if t <= newest_ts) >= 2:
            break
    return list(out.values()), calls


def posts(html):
    for blob in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
        if "polaris_ordered_timeline_connection" not in blob:
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "polaris_ordered_timeline_connection" in o:
                    out = []
                    for e in (o["polaris_ordered_timeline_connection"] or {}).get("edges") or []:
                        n = e.get("node") or {}
                        if not n.get("code") or not n.get("pk"):
                            continue
                        ms = (int(n["pk"]) >> 23) + IG_EPOCH_MS
                        pics, clips = tikhub_fetch.meta_media(n, n["code"])
                        out.append({"url": f"https://www.instagram.com/p/{n['code']}/", "ts": ms // 1000,
                                    "images": pics, "clips": clips,
                                    "text": " ".join(((n.get("caption") or {}).get("text") or "").split()),
                                    "published": datetime.fromtimestamp(ms / 1000, HKT).isoformat(timespec="minutes")})
                    return out
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    return []


def threads_posts(html, user):
    out = {}
    for blob in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
        if '"taken_at"' not in blob:
            continue
        try:
            stack = [json.loads(blob)]
        except ValueError:
            continue
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "taken_at" in o and o.get("code") and isinstance(o.get("taken_at"), int):
                    tp = o.get("text_post_app_info") or {}
                    eng = {"likes": o.get("like_count"), "replies": tp.get("direct_reply_count"),
                           "shares": sum(v for v in (tp.get("repost_count"), tp.get("quote_count"), tp.get("reshare_count")) if isinstance(v, int))
                           if any(isinstance(tp.get(k), int) for k in ("repost_count", "quote_count", "reshare_count")) else None}
                    pics, clips = tikhub_fetch.meta_media(o, o["code"], threads=True)
                    text = " ".join(((o.get("caption") or {}).get("text") or "").split())
                    link = (tp.get("link_preview_attachment") or {}).get("url") if isinstance(tp.get("link_preview_attachment"), dict) else None
                    if link:  # a link post's target (l.threads.com wraps it: follow.unwrap reads it out)
                        import follow
                        link = follow.unwrap(link)
                        if link not in text:
                            text = (text + "\n" + link).strip()
                    out[o["code"]] = {"url": f"https://www.threads.net/@{user}/post/{o['code']}",
                                      "images": pics, "clips": clips,
                                      "text": text,
                                      "published": datetime.fromtimestamp(o["taken_at"], HKT).isoformat(timespec="minutes"),
                                      "ts": o["taken_at"], "engagement": {k: v for k, v in eng.items() if isinstance(v, int)}}
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    return list(out.values())


def fetch(http, url, hdr, cut, isp=False):
    """→ (response, html), through the ISP IP (isp: the caller holds its lock) or RESI_PROXY. cut: stop reading an
    Instagram profile page right after the script holding its posts (polaris_ordered_timeline_connection sits at ~63%
    of the document): ~100 KB on the wire instead of ~150 KB"""
    for i in range(ISP_TRIES if isp else 1):
        try:
            r = http.get(url, proxies={"https": os.environ["ISP_PROXY"] if isp else resi.url("hk")}, timeout=40,
                         headers=hdr, stream=True)
            break
        except Exception as e:
            if isp:
                if re.search(r"tunnel.{0,40}403", str(e), re.I) and i < ISP_TRIES - 1:
                    __import__("time").sleep(3)
                    continue
                raise
            resi.check(e)  # IPRoyal out of traffic → resi.ProxyOut (a pause)
            raise
    buf = b""
    try:
        for ch in r.iter_content():
            buf += ch
            i = buf.find(b"polaris_ordered_timeline_connection") if cut else -1
            if i >= 0 and buf.find(b"</script>", i) >= 0:
                break
    finally:
        r.close()
        n = len(buf) // 5 + resi.OVERHEAD  # compressed on the wire (~1/5, comments_fetch.py)
        resi.count_isp(n) if isp else resi.count(n, url)
    return r, buf.decode("utf-8", "replace")


def walled(r):
    return r.status_code != 200 or "/accounts/login" in str(r.url)


@__import__("contextlib").contextmanager
def isp_turn(wait=ISP_WAIT):
    """the ISP IP's lock 'yyc youtube' (sites_fetch.isp_lock, media.youtube_lock), waited for in Postgres's queue:
    pg_advisory_lock under lock_timeout, so a released lock goes to this waiter. The sites and forums jobs take it per
    request with pg_try_advisory_lock every half second, and a try loop like theirs lost to them for its whole 30 s
    on every account (29 Oct 2026). corpus.LockTimeout after `wait` seconds"""
    with corpus.retrying(lambda: corpus.lock_connect("yyc-instagram-isp"), "lock connection") as conn:
        conn.execute(f"SET lock_timeout = '{int(wait * 1000)}ms'")
        try:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", ("yyc youtube",))
        except __import__("psycopg").errors.LockNotAvailable:
            raise corpus.LockTimeout(f"yyc youtube: held by others for {wait} s")
        try:
            yield
        finally:
            try:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("yyc youtube",))
            except __import__("psycopg").OperationalError:
                pass  # the connection died during the block (a switchover), and its session's lock with it


def read_isp(http, url, hdr, cut):
    """the account's page through the ISP IP under its lock → (response, html), or None when the ISP IP can't serve
    it now (lock busy, tunnel or HTTP error, login wall)"""
    try:
        with isp_turn():
            r, html = fetch(http, url, hdr, cut, isp=True)
    except Exception as e:  # noqa: BLE001: RESI_PROXY reads it instead
        print("isp", url, repr(e)[:120], flush=True)
        return None
    if walled(r):
        print("isp", url, "walled", r.status_code, str(r.url)[:80], flush=True)
        return None
    return r, html


def is_threads(url):
    return "://www.threads." in url


def main():
    from curl_cffi import requests
    import watchlist
    accounts = [dict(r, username=r["handle"]) for r in watchlist.accounts("instagram") + watchlist.accounts("threads")
                if not r.get("inactive")]
    paused = watchlist.paused() if any(watchlist.is_new(a) for a in accounts) else False
    http = requests.Session(impersonate="chrome")
    prev = corpus.file_get("instagram.json", {"accounts": {}, "items": []})
    now = datetime.now(HKT)
    now_ts = int(now.timestamp())
    items, state, calls, pout = [], {}, 0, None
    isp_fails = 0 if os.environ.get("ISP_PROXY") else ISP_FAILS
    deadline = __import__("time").time() + RUN_SECONDS
    for a in accounts:
        u = a["username"]
        threads = a.get("platform") == "threads"
        key = "threads:" + u if threads else u  # one handle on both platforms is two accounts
        ps = prev["accounts"].get(key) or {}
        old = [i for i in prev["items"] if i.get("account") == u and is_threads(i["url"]) == threads]
        recent = ps.get("recent") or [int(datetime.fromisoformat(i["published"]).timestamp()) for i in old if i.get("published")]
        last = ps.get("checked")
        # the account's first read: the posts it shows (the newest 12, pinned old ones too) are its backlog (first_run)
        since = ps.get("since") or (now_ts if not last and not ps.get("newest_ts") else None)
        if paused and watchlist.is_new(a):  # the daily safety stop: keep its last posts, read it tomorrow
            state[key] = {"ok": None, **ps, "off": "safety stop"}  # ps may hold "off" already
            items += old
            continue
        if pout:  # the proxy is out of traffic: the rest wait, as they were
            state[key] = ps
            items += old
            continue
        if __import__("time").time() > deadline or \
                last and now - datetime.fromisoformat(last) < timedelta(minutes=every_minutes(recent, now_ts) - 2):
            state[key] = ps  # not due (or the run's time is up): keep its last result and posts
            items += old
            continue
        try:
            url = f"https://www.threads.com/@{u}" if threads else f"https://www.instagram.com/{u}/"
            hdr = {"Accept": "text/html", "Accept-Language": "zh-HK,zh;q=0.9", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
            got_isp = read_isp(http, url, hdr, not threads) if isp_fails < ISP_FAILS else None
            if got_isp:
                r, html = got_isp
            else:
                isp_fails += 1
                try:
                    r, html = fetch(http, url, hdr, not threads)
                except resi.ProxyOut:
                    raise
                except Exception:  # the residential proxy refuses a connection now and then (407): one retry
                    r, html = fetch(http, url, hdr, not threads)
            if watchlist.is_new(a) and not got_isp:  # the ISP IP is flat rate: only yycstream bytes count
                watchlist.add_proxy_bytes(len(html.encode()) // 5)  # compressed on the wire (~1/5, comments_fetch.py)
            got = threads_posts(html, u) if threads else posts(html)
            newest = ps.get("newest_ts")
            gap = 0
            if got and newest and not threads and min(g["ts"] for g in got) > newest:
                # every post on the profile is newer than the newest known: more than 12 since the last read
                more, n = gap_posts(u, newest)
                calls += n
                if watchlist.is_new(a):
                    watchlist.add_tikhub(n * 0.002)  # /instagram/v2/fetch_user_posts, USD 0.002 a page
                have = {g["url"] for g in got}
                gap = len([m for m in more if m["url"] not in have])
                got += [m for m in more if m["url"] not in have]
            state[key] = {"ok": bool(got), "posts": len(got), "gap": gap, "checked": now.isoformat(timespec="seconds"), "since": since,
                        "newest_ts": max([newest or 0] + [g["ts"] for g in got]) or None,
                        "recent": sorted({t for t in recent + [g["ts"] for g in got] if t > now_ts - 3 * 86400}),
                        "error": None if got else ("login wall" if "/accounts/login" in str(r.url) else f"no posts (HTTP {r.status_code})")}
            fresh = [{"source": a["name"] + (" (Threads)" if threads else " (Instagram)"), "title": g["text"][:160] or "(post without caption)",
                      "url": g["url"], "published": g["published"], "account": u, "text": g["text"],
                      "engagement": g.get("engagement") or {}, "at": now_ts,
                      **({"first_run": True} if since and g["ts"] < since else {}),
                      **{k: g[k] for k in ("images", "clips") if g.get(k)}} for g in got]
            seen = {f["url"] for f in fresh}
            items += fresh + [i for i in old if i["url"] not in seen and (i.get("at") or 0) > now_ts - KEEP_H * 3600]
        except resi.ProxyOut as e:  # IPRoyal out of traffic (resi.py): a pause; this and the rest keep their place
            pout = repr(e)[:200]
            state[key] = ps
            items += old
        except Exception as e:
            state[key] = dict(ps, ok=False, posts=0, error=repr(e)[:160], checked=now.isoformat(timespec="seconds"), since=since)
            items += old
    once = {}
    for i in items:  # each post once (the freshest copy comes first)
        once.setdefault(i["url"], i)
    items = list(once.values())
    ok = sum(1 for v in state.values() if v["ok"])
    out = {"fetched": datetime.now(HKT).isoformat(timespec="seconds"), "ok": ok > 0,
           "error": None if ok else "no account answered", "accounts": state, "items": items, "tikhub_calls": calls}
    if pout:  # a pause, not a failure (sources.instagram_file: off)
        out.update(ok=False, off=resi.STATUS, error=pout)
    corpus.file_put("instagram.json", out)
    print(f"instagram+threads: {ok}/{len(accounts)} accounts, {len(items)} posts, {calls} TikHub calls for gaps")


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    main()
