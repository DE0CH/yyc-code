#!/usr/bin/env python3
"""The article's own public address: what 開啟原文 opens (Deyao, 2026-10-20).

A post's `url` is the address the stream found it at (a feed's link, a decoded Google News link, a listing's href); it
is the post's identity everywhere (stream_items, scored, coverage), so it never changes. Some of those addresses don't
open for a reader. 信報 (2026-10-20, 「午間新聞精選｜李家超:把握好APEC平台展示香港全面發展」): Google News linked
www.hkej.com/instantnews/current/article/4542647/…, but the article sits in section hongkong. A desktop browser gets the
article whatever the section, but a phone is redirected to m.hkej.com/instantnews/current/…, which answers 404: on
Deyao's iPhone the button opened 信報's error page. The page itself states its address (<link rel=canonical> /
og:url: www.hkej.com/instantnews/hongkong/article/4542647/…), which opens on a phone and on a desktop.

So every page the full-text readers fetch (fulltext_fetch.py, cache_fetch.py) is asked for its own address
(from_page), and when that differs from the post's url it is kept in posts.canonical_url. The site links to
canonical_url when there is one, else to url (item page 開啟原文, 搜尋's 原文, 數據分析's Original link).

A host that no reader can open gets the site's working host when the post is stored (rehost, REHOST; corpus.ingest),
without reading the page: hkcd.com.hk (香港商報, as Google News links it) answers with video.hkcd.com's certificate, so
every browser refuses it, and the pages state no address of their own; www.hkcd.com serves the same paths.

A page's stated address is taken only when it is an article address on the same site: http(s), the same registrable
domain (www / m. / www2. are one site), and not the site's front page (some sites state their home page for every
article). A twice-encoded address (信報's mobile pages: %25E5…) is decoded once.

    python3 canonical.py check [PER] [DAYS] [OUT.json]   sample PER posts (default 3) of every source seen in the last
                                                        DAYS days (default 7): does the address the site links to open
                                                        on a phone? (GET as iPhone Safari, redirects followed)
    python3 canonical.py fix HOST [DAYS] [SID]          every post on HOST (and its subdomains; of the last DAYS days,
                                                        default all) whose address doesn't open on a phone: read its
                                                        page, keep the address it states when that one opens
    python3 canonical.py rehost                         every stored post on a REHOST host: the site's working host
    python3 canonical.py <url>…                         what from_page finds for each page (test; writes nothing)
"""
import html, json, random, re, sys, urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import corpus

PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1")
TIMEOUT = 20
SECOND_LEVEL = {"com", "org", "net", "gov", "edu", "co", "ac", "idv"}  # foo.com.hk / bar.gov.hk / x.co.uk: 3 labels
REFUSED = (401, 403, 405, 412, 429, 451)  # a bot wall in front of our IP (HKET's 405 「Human Verification」, B站's 412),
                                          # not a missing page: a reader's browser gets through
REHOST = {"hkcd.com.hk": "www.hkcd.com"}  # host → the site's working host (the same paths)
# host → (path regex, replacement): a list that links an address the site answers 404 to (生活易's /en/news list links
# /news/<slug>; the page is /en/news/<slug>, 2026-10-20)
REPATH = {"www.esdlife.com": (r"^/news/", "/en/news/")}
SAFE = ":/?&=%#+,;@!$'()*~[]"


def site(host):
    """the registrable domain: www2.hkej.com and m.hkej.com → hkej.com; news.gov.hk → news.gov.hk"""
    labels = (host or "").lower().rstrip(".").split(".")
    n = 3 if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return ".".join(labels[-n:])


def rehost(url):
    """url on the site's working host when its own host can't be opened by anyone (REHOST), or with the path the site
    serves when the listed one answers 404 (REPATH) → None otherwise"""
    u = urllib.parse.urlsplit(url or "")
    h = (u.hostname or "").lower()
    if h in REPATH and re.search(REPATH[h][0], u.path):
        return urllib.parse.urlunsplit(("https", h, re.sub(REPATH[h][0], REPATH[h][1], u.path, count=1), u.query, u.fragment))
    to = REHOST.get(h)
    return urllib.parse.urlunsplit(("https", to, u.path, u.query, u.fragment)) if to else None


def clean(href, base):
    """href as an absolute ASCII http(s) address (relative and //host addresses resolved against base, a twice-encoded
    path decoded once) → None when it isn't one"""
    href = html.unescape((href or "").strip())
    if not href:
        return None
    u = urllib.parse.urlsplit(urllib.parse.urljoin(base, href))
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    path = u.path
    if re.search(r"%25[0-9A-Fa-f]{2}", path) and not re.search(r"%(?!25)[0-9A-Fa-f]{2}", path):
        path = urllib.parse.unquote(path)  # %25E5%258D → %E5%8D (信報's mobile pages)
    path = urllib.parse.quote(path, safe=SAFE)  # raw Chinese (信報's og:url) → percent-encoded
    query = urllib.parse.quote(u.query, safe=SAFE)
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), path or "/", query, ""))


def article_of(cand, url):
    """is cand an article address of url's site (same registrable domain, not the front page)?"""
    c, u = urllib.parse.urlsplit(cand), urllib.parse.urlsplit(url)
    if site(c.hostname) != site(u.hostname):
        return False
    front = lambda s: s.path.strip("/") == "" and not s.query
    return not front(c) or front(u)


def same(a, b):
    """the same address but for http/https, a trailing slash and percent-encoding case"""
    k = lambda x: re.sub(r"^https?://", "", urllib.parse.unquote(x or "")).rstrip("/").lower()
    return k(a) == k(b)


def from_page(page, url):
    """→ the address the page states for itself (<link rel=canonical>, else og:url) when it is an article address on
    url's site and differs from url; None otherwise"""
    if not page or not url:
        return None
    try:
        import lxml.html
        doc = lxml.html.fromstring(page)
    except Exception:
        return None
    for xp in ('//link[contains(concat(" ", normalize-space(translate(@rel, "CANONICAL", "canonical")), " "), " canonical ")]/@href',
               '//meta[@property="og:url"]/@content'):
        for href in doc.xpath(xp):
            c = clean(str(href), url)
            if c and article_of(c, url):
                return None if same(c, url) else c
    return None


def save(pid, url, page):
    """keep the page's own address for post pid when it differs from url (posts.canonical_url); → it or None"""
    try:
        c = from_page(page, url)
        if c:
            corpus.db().execute("UPDATE posts SET canonical_url=%s WHERE id=%s AND canonical_url IS DISTINCT FROM %s",
                                (c, pid, c))
        return c
    except Exception as e:  # never stops a reader: the post keeps linking its url
        print("canonical:", pid, repr(e)[:120], flush=True)
        return None


def opens(url, proxy=None):
    """GET url as a phone (iPhone Safari's fingerprint and UA, redirects followed) → (verdict, status, final url):
    verdict "ok", "broken" (404/410/5xx, a certificate no browser accepts, or a redirect to the site's front page),
    "refused" (a bot wall answering our IP: REFUSED, which a reader's browser passes) or "unreachable" (our own DNS or
    connection failed twice: says nothing about the link)"""
    import time
    from curl_cffi import requests
    for attempt in (1, 2):
        try:
            r = requests.get(url, impersonate="safari_ios", timeout=TIMEOUT, allow_redirects=True, max_redirects=10,
                             proxies={"http": proxy, "https": proxy} if proxy else None, headers={"User-Agent": PHONE_UA, "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"})
            break
        except Exception as e:
            why = repr(e)[:160]
            if proxy:
                import resi
                if resi.is_out(e):  # IPRoyal out of traffic: says nothing about the link; the run stops (fix)
                    raise resi.mark(repr(e)[:300])
            if "certificate" in why.lower() or "SSL: no alternative" in why:
                return "broken", None, why
            if attempt == 2:
                return "unreachable", None, why
            time.sleep(3)
    final = str(r.url)
    if r.status_code in REFUSED:
        return "refused", r.status_code, final
    if r.status_code >= 400:
        return "broken", r.status_code, final
    f, o = urllib.parse.urlsplit(final), urllib.parse.urlsplit(url)
    if f.path.strip("/") == "" and not f.query and (o.path.strip("/") or o.query):
        return "broken", r.status_code, final  # a soft 404: the article address lands on the front page
    return "ok", r.status_code, final


def _by_host(rows, fn, threads=16):
    """fn(row) for every row, one at a time per host, threads hosts at once → [(row, result)]"""
    groups = defaultdict(list)
    for r in rows:
        groups[urllib.parse.urlsplit(r["link"]).hostname or ""].append(r)
    out = []

    def run(items):
        for r in items:
            out.append((r, fn(r)))
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(run, groups.values()))
    return out


def check(per=3, days=7, out=None):
    """sample per posts of every source (sid) seen in the last days days; print per source how many open on a phone"""
    rows = corpus.db().execute(f"""
        SELECT id, sid, website, url, coalesce(canonical_url, url) AS link FROM (
          SELECT id, sid, website, url, canonical_url, row_number() OVER (PARTITION BY sid ORDER BY random()) AS n
          FROM posts WHERE thread IS NULL AND seen >= to_char(now() - interval '{int(days)} days', 'YYYY-MM-DD')) s
        WHERE n <= %s ORDER BY sid""", (per,)).fetchall()
    got = _by_host(rows, lambda r: opens(r["link"]))
    per_sid = defaultdict(lambda: defaultdict(list))
    for r, (verdict, status, final) in got:
        per_sid[r["sid"]][verdict].append({"id": r["id"], "link": r["link"], "status": status, "final": final})
    totals = defaultdict(int)
    for sid in sorted(per_sid):
        d = per_sid[sid]
        for k, v in d.items():
            totals[k] += len(v)
        if d.get("broken") or d.get("refused"):
            print(f"{sid:32} ok {len(d.get('ok', [])):2}  broken {len(d.get('broken', [])):2}  refused "
                  f"{len(d.get('refused', [])):2}  e.g. {(d.get('broken') or d.get('refused'))[0]['status']} "
                  f"{(d.get('broken') or d.get('refused'))[0]['link'][:90]}", flush=True)
    print(f"{len(per_sid)} sources, {len(rows)} posts: {dict(totals)}", flush=True)
    if out:
        with open(out, "w") as f:
            json.dump(per_sid, f, ensure_ascii=False, indent=1)
    return per_sid


def rehost_stored():
    """every stored post on a REHOST host gets the site's working host (a REPATH host: its working path) → how many"""
    n = 0
    for host in list(REHOST) + list(REPATH):
        rows = corpus.db().execute("SELECT id, url FROM posts WHERE url ~ %s AND canonical_url IS NULL",
                                   (r"^https?://" + re.escape(host) + "/",)).fetchall()
        rows = [r for r in rows if rehost(r["url"])]
        for r in rows:
            corpus.db().execute("UPDATE posts SET canonical_url=%s WHERE id=%s", (rehost(r["url"]), r["id"]))
        n += len(rows)
    print(f"rehosted {n} posts", flush=True)
    return n


def fix(host, days=3650, sid=None):
    """every post on host (a site: its subdomains too) seen in the last days days whose address doesn't open on a
    phone: read its page as the full-text readers do (RESI_PROXY for the hosts that refuse the cluster's IP, when
    set) and keep the address the page states, when that one opens; sid: only sources whose id starts with it
    (gnews_: Google News's links) → counts"""
    import cache_fetch, resi
    rows = corpus.db().execute(f"""SELECT id, sid, url, coalesce(canonical_url, url) AS link FROM posts
        WHERE thread IS NULL AND url ~ %s AND sid LIKE %s AND seen >= to_char(now() - interval '{int(days)} days', 'YYYY-MM-DD')""",
                               (r"^https?://([a-z0-9-]+\.)*" + re.escape(host) + "/", (sid or "") + "%")).fetchall()
    stats = defaultdict(int)

    def one(r):
        h = urllib.parse.urlsplit(r["url"]).hostname or ""
        proxy = resi.url("hk") if h.endswith(cache_fetch.PROXY_HOSTS) and resi.configured() else None
        verdict, status, _ = opens(r["link"], proxy)
        if verdict != "broken":
            return verdict
        try:
            page = cache_fetch.get(r["url"], proxy=proxy)
        except resi.ProxyOut:
            raise
        except Exception as e:
            print("unreadable:", r["id"], r["url"][:100], repr(e)[:80], flush=True)
            return "unreadable"
        c = from_page(page, r["url"])
        if not c:
            print("no address stated:", r["id"], r["url"][:100], flush=True)
            return "no_canonical"
        if opens(c, proxy)[0] == "broken":
            print("stated address broken too:", r["id"], c[:100], flush=True)
            return "canonical_broken"
        corpus.db().execute("UPDATE posts SET canonical_url=%s WHERE id=%s", (c, r["id"]))
        print("fixed:", r["id"], r["url"][:80], "→", c[:100], flush=True)
        return "fixed"
    for _, v in _by_host(rows, one, threads=4):
        stats[v] += 1
    print(f"{host}: {len(rows)} posts: {dict(stats)}", flush=True)
    return dict(stats)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["check"]:
        check(int(a[1]) if len(a) > 1 else 3, int(a[2]) if len(a) > 2 else 7, a[3] if len(a) > 3 else None)
    elif a[:1] == ["fix"] and len(a) > 1:
        fix(a[1], int(a[2]) if len(a) > 2 else 3650, a[3] if len(a) > 3 else None)
    elif a[:1] == ["rehost"]:
        rehost_stored()
    elif a:
        import cache_fetch
        for u in a:
            print(u, "→", from_page(cache_fetch.get(u), u))
    else:
        print(__doc__)
