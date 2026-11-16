#!/usr/bin/env python3
"""The catalogue sites (sites.json): every publication of the WiseSearch catalogue that YYC did not collect yet (Deyao,
2026-10-20: "add the ones we are missing from Macau and Hong Kong"): Macau's papers and websites, Hong Kong's magazines,
forums, websites and blogs; and the outlets Google News found that no source read (Deyao, 2026-10-23: "add the real
gaps"; catalogue "… (source gaps, 2026-10-23)": Taiwan, overseas Chinese, Mainland and other outlets). One generic
reader for all of them, so a site is a line of data, not code.

Each site in sites.json: {"id": sid, "name", "catalogue" (the WiseSearch catalogue title it covers), "url", "kind", "region": "HK" | "Macau" | "Mainland" | "Taiwan" | "Global", "type": "Newspaper" | "Magazine"
| "News" | "Official" | "Forum" | "Blog", "lang", "every" (minutes, default by type), and per kind:
  rss            RSS 2.0 or Atom
  wp_json        a WordPress REST list (/wp-json/wp/v2/posts?per_page=20&_fields=link,date_gmt,title)
  sitemap        a <urlset>: title from <news:title>, else the URL's slug ("slug": false = skip untitled rows);
                 "keep": a regex the article URLs match
  sitemap_index  a <sitemapindex>: its "first" (newest-first) or "last" (oldest-first) N child sitemaps matching "match"
  listing        an HTML page: the <a> links whose href matches "href" (a regex; a named group `time` dates the link);
                 a link wrapping a card takes its heading (<h1>–<h6>) as the title (sources_extra.anchors)
  epaper         a dated table of contents: "url" with {yyyy} {mm} {dd} {ddmmyyyy} (today in Hong Kong time, else the
                 day before, back over "days" days, default 2: a weekly's last issue with "days": 7), "href" as for
                 listing (or "pattern" as for regex)
  regex          an HTML page where the title is not the link's own text (a heading next to a "read more" link):
                 "pattern", a regex (re.S) with named groups url and title, and time when the page dates it
  xml            a plain XML list (not RSS): "item" (the element of one row), "title", "time" (child elements),
                 "link" (a child element, or a {child} template such as read_news2.aspx?newsid={newsid}), "text" (a
                 child holding the whole story)
  json           a JSON list the site's pages read: "items" (dotted path to the list, '*' flattens), "title", "time"
                 (keys), "link" (a key or a {field} template), "base", "time_format" (json_list), "text" (a key
                 holding the whole story, HTML or plain)
  parser         a list that takes more than one request or a step the kinds above lack: "parser" names a function
                 of sources_extra.SITE_PARSERS (flamingwheels, line_today, pressreader, student_hk), given the site
                 url's text (none for a parser that fetches_itself: student.hk's signed API), the site and its
                 reader for further pages
 A dated URL ({yyyy} {mm} {dd}…) is today in Hong Kong time, or "back" days before (a list whose date parameter is
 "from": 地球物理氣象局's search lists from the day before, so a story of 23:55 is still listed after midnight).
 "browser": true = a site behind a challenge only a real browser passes (Cloudflare's managed challenge, AWS WAF): read
 by browser_fetch.py (Chrome under Xvfb, every 30 min) with the same parsers, its posts in browser.json, not here;
 with a "proxy" country Chrome goes through yycstream with that exit (Lifestyle Asia: CloudFront refuses the cluster's
 IP, AWS WAF's challenge passes on a Hong Kong residential one).
 Routes, in the order they are tried for a site that refuses the cluster's IP (recorded per site in "route"):
 direct; "proxy": "isp" = YYC's IPRoyal ISP IP 81.181.135.61 (static Hong Kong residential, flat rate, ISP_PROXY,
 one client at a time: the 'yyc youtube' lock, bytes in kv isp:bytes); "proxy": "hk" | "mo" | "tw" | any IPRoyal
 country code = RESI_PROXY, YYC's IPRoyal sub-user yycstream, with that exit country (per GB, counted per site,
 resi.py; Taiwan's sites that answer only Taiwan IPs: "tw"; the article pages take the same exit,
 cache_fetch.PROXY_COUNTRY); "browser" (below).
 "page_proxy": "hk" = its article pages (cache_fetch.fetch_one) go through yycstream whatever the list's route
 (uwants, 2026-10-21: its list reads on the ISP IP, every thread page there is a reCAPTCHA wall).
 "aggregator": true = a site whose posts are copies of other outlets' stories with a link to the original
 (headline4hk.com): the full-text readers follow that link into the corpus (follow.pointer) and keep the original's text.
 "impersonate": true = read with Chrome's TLS fingerprint (curl_cffi), for a Cloudflare that challenges urllib only.
 "link" (regex): a template over the pattern's groups when the page holds only an article's id.
 "data": a POST body for a list the site's pages fetch by POST (a string as sent, or an object sent as JSON; set
 "headers" Content-Type to match), "charset": the page encoding when it is not declared ("auto" otherwise), "tls_noverify": true for a site whose
 certificate chain is incomplete (eoc.org.hk leaves out its intermediate certificate).

A run (CronJob `sites`, every 10 min) reads every site that is due, one request at a time per host, and keeps the
links the poller has not read yet (table poll_seen) for KEEP hours. It writes the document sites.json (corpus.file_put):
{fetched, sites: {sid: {name, ok, error, items, at, fails}}, items: [...]}, read by sources.sites_file: one status line
per site on the monitor. A site's first read is its backlog: the corpus marks a new sid's first posts first_run, so
they are never filed or sent.

    python3 sites_fetch.py run            one pass
    python3 sites_fetch.py test SID       read one site now and print what it lists (no state written)
    python3 sites_fetch.py check [SID…]   read every site (or those) now: ok / items / newest / error, no state written
"""
import html, json, os, re, sys, time, urllib.parse, concurrent.futures as cf
from datetime import datetime, timedelta

import sources
from sources import HKT, clean, parse_time, rss
import sources_extra as se

HERE = os.path.dirname(os.path.abspath(__file__))
SITES_FILE = os.environ.get("SITES_FILE") or os.path.join(HERE, "sites.json")
DOC = "sites.json"
RUN_SECONDS = int(os.environ.get("SITES_RUN_SECONDS", 480))  # the CronJob runs every 10 min
THREADS = 24          # hosts read at once (1,155 sites due took 486 s at 12, 2026-10-20)
HOST_GAP = 1.0        # seconds between two requests to one host
KEEP = timedelta(hours=6)   # a link not yet read by the poller stays in the document this long
MAX_AGE = timedelta(days=2)  # older links are left out (the poller's window, app.MAX_AGE)
PER_SITE = 200        # links kept per site and read at most
EVERY = {"News": 10, "Newspaper": 30, "Official": 30, "Forum": 15, "Magazine": 60, "Blog": 60}  # minutes, by type
KINDS = ("rss", "wp_json", "sitemap", "sitemap_index", "listing", "epaper", "json", "xml", "regex", "parser")
TYPES = ("News", "Newspaper", "Magazine", "Official", "Forum", "Blog")
REGIONS = ("HK", "Macau", "Mainland", "Taiwan", "Global")  # the catalogue region, or the outlet's own when the catalogue misfiles it
# regions not read (Deyao, 2026-10-28: cost; 20-27 Oct their sites gave 31% of the posts scored and 14 that reached 50
# on any stream): their sites stay listed here, unread, and link following treats their hosts as ignored by Deyao
# (follow.ignored). Read anyway: the sites of those regions with 2 or more posts at 50 or a fifth at 20 or more in that
# week: Beijing's two Hong Kong offices (港澳辦 3 of 6 at 20+, 中聯辦 7 of 19), robotbelt.com (Galbot, 10 of 45) and
# Sing Tao Canada's Hong Kong news (3 of 176 at 50+)
OFF_REGIONS = ("Mainland", "Taiwan", "Global")
OFF_EXCEPT = ("cn_hmo", "cn_locpg", "gl_robotbelt", "gl_singtao_ca_rt")


def off(s):
    """is this sites.json entry left unread (OFF_REGIONS)?"""
    return s.get("region") in OFF_REGIONS and s.get("id") not in OFF_EXCEPT


def load_read(path=None):
    """the sites.json entries that are read (load() without the off ones)"""
    return [s for s in (load(path) if path else load()) if not off(s)]


def load(path=SITES_FILE):
    """sites.json → [site]; every entry checked (unique id, known kind, type and region, what the kind needs)"""
    with open(path) as f:
        sites = json.load(f)
    seen = set()
    for s in sites:
        sid = s.get("id") or ""
        if not re.fullmatch(r"[a-z0-9_]+", sid) or sid in seen:
            raise ValueError(f"sites.json: bad or repeated id {sid!r}")
        seen.add(sid)
        check(s)
    return sites


def check(s):
    """one sites.json entry: known kind, type and region, what the kind needs, a forum route that exists"""
    sid = s.get("id")
    if s.get("kind") not in KINDS or s.get("type") not in TYPES or s.get("region") not in REGIONS:
        raise ValueError(f"sites.json {sid}: kind/type/region {s.get('kind')}/{s.get('type')}/{s.get('region')}")
    if (s["kind"] == "listing" or (s["kind"] == "epaper" and not s.get("pattern"))) and not s.get("href"):
        raise ValueError(f"sites.json {sid}: {s['kind']} needs href")
    if s["kind"] == "regex" or s.get("pattern"):
        if not s.get("pattern"):
            raise ValueError(f"sites.json {sid}: regex needs pattern")
        if not {"url", "title"} <= set(re.compile(s["pattern"]).groupindex):
            raise ValueError(f"sites.json {sid}: pattern needs groups url and title")
    if s["kind"] == "xml" and not s.get("item"):
        raise ValueError(f"sites.json {sid}: xml needs item")
    if s["kind"] == "parser" and s.get("parser") not in se.SITE_PARSERS:
        raise ValueError(f"sites.json {sid}: parser {s.get('parser')!r} is not in sources_extra.SITE_PARSERS")
    if not s.get("name") or not s.get("url", "").startswith("http"):
        raise ValueError(f"sites.json {sid}: name and url")
    if s.get("forum"):
        import forum_fetch
        if s["forum"] not in forum_fetch.load() or s.get("browser") or s.get("proxy"):
            raise ValueError(f"sites.json {sid}: forum {s['forum']!r} is not in forum_threads.json, or set with browser/proxy")


AUTO_RECHECK = 86400  # a route the job found itself (escalate) is kept this long, then direct is tried again
QUIET = (1, 2, 4, 6)  # a quiet site's interval multiplier after 0, 1, 2, 3+ reads in a row with nothing new and dated


def every(site, st=None):
    """seconds between reads: the site's "every" or its type's, longer for a quiet site (Deyao, 2026-10-20: poll at the
    cadence the site posts at; nothing is skipped: a list read later still lists what came meanwhile). A site whose
    last reads brought nothing new and none of its rows dated in the last 2 days waits 2×, 4×, 6× (at most 6 h); one new
    post brings it back to its base cadence. A failing site is tried again at its base cadence (its next try may pass:
    escalate() moves it to another route)"""
    base = int(site.get("every") or EVERY[site["type"]]) * 60
    st = st or {}
    quiet = 0 if st.get("fails") else st.get("quiet") or 0
    return min(base * QUIET[min(quiet, len(QUIET) - 1)], max(base, 6 * 3600))


# ---- parsers ----

def atom(text):
    out = []
    for e in re.findall(r"<entry\b.*?>(.*?)</entry>", text, re.S):
        t = re.search(r"<title\b[^>]*>(.*?)</title>", e, re.S)
        links = re.findall(r"<link\b([^>]*)/?>", e)
        href = ""
        for a in links:
            h = re.search(r"""href=["']([^"']+)["']""", a)  # Blogger writes its attributes in single quotes
            if h and (re.search(r"""rel=["']alternate["']""", a) or "rel=" not in a):
                href = h.group(1)
                break
        d = re.search(r"<(published|updated)>([^<]+)</\1>", e)
        au = re.search(r"<author>\s*<name>(.*?)</name>", e, re.S)
        s = re.search(r"<(summary|content)\b[^>]*>(.*?)</\1>", e, re.S)
        out.append({"title": clean(sources.cdata(t.group(1))) if t else "", "url": html.unescape(href),
                    "time": d.group(2).strip() if d else "", "section": "",
                    "author": clean(au.group(1)) if au else None,
                    "desc": sources.paragraphs(html.unescape(sources.cdata(s.group(2)))) if s else ""})
    return out


def feed(text):
    return rss(text) if re.search(r"<item\b", text) else atom(text)


def wp_json(text):
    return [{"title": clean((p.get("title") or {}).get("rendered") or ""), "url": p.get("link") or "",
             "time": (p["date_gmt"] + "+00:00") if p.get("date_gmt") else p.get("date") or "", "section": ""}
            for p in json.loads(text) if isinstance(p, dict)]


def dig(d, path):
    """the value at a dotted path; a '*' segment flattens a list ("data.newsList.*.data" → every item of every
    newsList entry's data list)"""
    vals = [d]
    for k in [x for x in (path or "").split(".") if x]:
        nxt = []
        for v in vals:
            if k == "*":
                nxt += v if isinstance(v, list) else []
            elif isinstance(v, list):
                nxt.append(v[int(k)] if k.isdigit() and int(k) < len(v) else None)
            else:
                nxt.append((v or {}).get(k) if isinstance(v, dict) else None)
        vals = nxt
    if "*" in (path or "").split("."):
        return [x for v in vals for x in (v if isinstance(v, list) else [v])]
    return vals[0] if vals else None


def xml_list(site):
    """a plain XML list: every <item> element; its children by name (title, time, link or a {child} link template)"""
    def p(text):
        out = []
        for blk in re.findall(r"<%s\b[^>]*>(.*?)</%s>" % (site["item"], site["item"]), text, re.S):
            kids = {k: html.unescape(sources.cdata(v)).strip() for k, v in re.findall(r"<([\w:.-]+)\b[^>]*>(.*?)</\1>", blk, re.S)}
            lk = site.get("link", "link")
            try:
                link = lk.format_map(kids) if "{" in lk else kids.get(lk, "")
            except (KeyError, ValueError):
                continue
            if site.get("base"):
                link = urllib.parse.urljoin(site["base"], link)
            t = kids.get(site.get("time", "pubDate"), "")
            if t and site.get("time_format"):
                try:
                    t = datetime.strptime(t, site["time_format"]).replace(tzinfo=HKT).isoformat()
                except ValueError:
                    pass
            row = {"title": clean(kids.get(site.get("title", "title"), "")), "url": link, "time": t, "section": ""}
            if site.get("text") and kids.get(site["text"]):
                row["text"] = sources.paragraphs(kids[site["text"]])  # the list carries the whole story (行政長官辦公室)
            out.append(row)
        return out
    return p


def regex_list(site):
    """"link": a template over the pattern's groups when the page holds only an id (堅料網's page data:
    https://www.kinliu.hk/news/n/{url}.html); the first row of each URL"""
    rxp = re.compile(site["pattern"], re.S)

    def p(text):
        out, seen = [], set()
        for m in rxp.finditer(text):
            url = html.unescape(m.group("url"))
            if site.get("link"):
                url = site["link"].format_map(m.groupdict())
            if url in seen:
                continue
            seen.add(url)
            out.append({"title": clean(m.group("title")), "url": url, "time": (m.groupdict().get("time") or "").strip(),
                        "section": ""})
        return out
    return p


CST = re.compile(r"\s(CST|HKT|MOT)$")  # China / Hong Kong / Macau Standard Time (email.utils reads CST as US Central)


def json_list(site):
    """a JSON list: "items" (dotted path, '*' flattens), "title" / "time" (keys), "link" (a key, or a template with
    {field} names such as "https://www.example.com/news/{id}"), "base" (joined to a relative link), "time_format"
    (strptime, Hong Kong / Macau time, for shapes like 10/20/2026 12:11:00)"""
    def p(text):
        out = []
        items = dig(json.loads(text), site.get("items"))
        for x in items if isinstance(items, list) else []:
            if not isinstance(x, dict):
                continue
            lk = site.get("link", "url")
            try:
                link = lk.format_map({k: v for k, v in x.items() if not isinstance(v, (dict, list))}) if "{" in lk \
                    else str(dig(x, lk) or "")
            except (KeyError, ValueError):
                continue
            if site.get("base"):
                link = urllib.parse.urljoin(site["base"], link)
            t = dig(x, site.get("time", "time")) or ""
            if t and site.get("time_format"):
                try:
                    t = datetime.strptime(str(t).split(".")[0], site["time_format"]).replace(tzinfo=HKT).isoformat()
                except ValueError:
                    pass
            row = {"title": clean(str(dig(x, site.get("title", "title")) or "")), "url": link, "time": t, "section": ""}
            if site.get("text") and isinstance(body := dig(x, site["text"]), str) and body.strip():
                row["text"] = sources.paragraphs(body)  # the list carries the whole story (地球物理氣象局, 鉅亨號)
            out.append(row)
        return out
    return p


def quoted(text):
    """href='x' and href=x written as href="x" (sources_extra.anchors reads double-quoted hrefs)"""
    return re.sub(r"""\bhref\s*=\s*(?:'([^']*)'|([^\s"'>]+))""", lambda m: 'href="%s"' % (m.group(1) if m.group(1) is not None else m.group(2)), text)


def toc_url(site, day):
    """the URL with {yyyy} {mm} {dd} {yyyymmdd} set to the day (any other brace is left as it is)"""
    u = site["url"]
    for k, v in (("{yyyymmdd}", f"{day:%Y%m%d}"), ("{ddmmyyyy}", f"{day:%d%m%Y}"), ("{yyyy}", f"{day:%Y}"), ("{mm}", f"{day:%m}"), ("{dd}", f"{day:%d}")):
        u = u.replace(k, v)
    return u


_isp_lock = __import__("threading").Lock()
ISP_WAIT = 120  # seconds a read waits for the ISP route (a YouTube clip or another job on it); then LockTimeout: the
# page fails for this run and a later run reads it, so a busy route never runs a job past its deadline


@__import__("contextlib").contextmanager
def isp_lock(wait=None):
    """one client at a time on the ISP IP (a second gets "Tunnel connection failed: 403"): this process's lock, then
    the Postgres advisory lock 'yyc youtube' (corpus.lock) that media.youtube_lock and broadcast_fetch.py take; at
    most `wait` (default ISP_WAIT) seconds of waiting for both (corpus.LockTimeout)"""
    wait = ISP_WAIT if wait is None else wait
    t = __import__("time").monotonic()
    if not _isp_lock.acquire(timeout=wait):
        raise sources.corpus.LockTimeout(f"yyc youtube: this process's other reads held the ISP route for {wait:.0f} s")
    try:
        left = max(1, wait - (__import__("time").monotonic() - t))
        with sources.corpus.lock("yyc youtube", app_name="yyc-sites-isp", timeout=left):
            yield
    finally:
        _isp_lock.release()


class NotModified(Exception):
    """the list answered 304 to a conditional request: nothing new since the last read"""


_forum_lock = __import__("threading").Lock()
_forum_clients = {}  # forum key → forum_fetch.Client, one per run (its sticky session, cookies and proof of work)


def _forum_get(site, url):
    """a page of a site read through its forum's client (sites.json "forum"): the forum's route and checks"""
    import forum_fetch
    key = site["forum"]
    with _forum_lock:
        if key not in _forum_clients:
            _forum_clients[key] = forum_fetch.Client(forum_fetch.load()[key])
            _forum_clients[key].lock = __import__("threading").Lock()
    c = _forum_clients[key]
    with c.lock:  # the sites job reads hosts in parallel; a forum's client (cookies, session) serves one page at a time
        return c.get(url)


def get(site, url):
    if site.get("forum"):
        return _forum_get(site, url)
    px = site.get("proxy")
    kw = dict(headers=dict(site.get("headers") or {}), charset=site.get("charset") or "auto", verify=not site.get("tls_noverify"))
    cond = site.get("_cond")  # {etag, last_modified} of the last read of the list itself (run() passes it)
    if cond is not None and url == site["url"]:
        if cond.get("etag"):
            kw["headers"]["If-None-Match"] = cond["etag"]
        if cond.get("last_modified"):
            kw["headers"]["If-Modified-Since"] = cond["last_modified"]
        kw["meta"] = site.setdefault("_meta", {})
    try:
        return _get(site, url, px, kw)
    except sources.urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified() from e
        raise


def _impersonated(url, proxy, kw, isp=False):
    """a site whose Cloudflare challenges urllib but passes Chrome's TLS fingerprint ("impersonate": true; 堅料網), or
    whose TLS urllib can't finish at all (Cover Magazine): curl_cffi as Chrome, the site's proxy route if any (isp:
    the ISP IP under its lock, its bytes counted there)"""
    from curl_cffi import requests

    def go():
        return requests.get(url, impersonate="chrome", timeout=30, headers=kw.get("headers") or None,
                            verify=kw.get("verify", True), proxies={"http": proxy, "https": proxy} if proxy else None)
    if isp:
        with isp_lock():
            r = go()
        sources.resi.count_isp(sources.resi.wire(r))
    else:
        r = go()
        if proxy:
            sources.resi.count(len(r.content) + 3 * sources.resi.OVERHEAD, url)
    if r.status_code >= 400:
        raise sources.urllib.error.HTTPError(url, r.status_code, f"HTTP {r.status_code}", {}, None)
    return r.text


def _get(site, url, px, kw):
    if site.get("impersonate"):
        if px == "isp":
            if not os.environ.get("ISP_PROXY"):
                raise RuntimeError("ISP_PROXY is not set (Secret isp-proxy)")
            return _impersonated(url, os.environ["ISP_PROXY"], kw, isp=True)
        return _impersonated(url, sources.resi.url(country=px if isinstance(px, str) else "hk") if px else None, kw)
    if site.get("data") is not None and url == site["url"]:  # a list read by POST (a form body; headers as given)
        kw["data"] = site["data"].encode() if isinstance(site["data"], str) else json.dumps(site["data"]).encode()
    if px == "isp":
        isp = os.environ.get("ISP_PROXY")
        if not isp:
            raise RuntimeError("ISP_PROXY is not set (Secret isp-proxy)")
        try:
            with isp_lock():
                return sources.fetch(url, proxy=isp, isp=True, **kw)
        except Exception as e:  # IPRoyal refuses some hosts on the ISP IP ("Tunnel connection failed: 403"): yycstream hk
            if "Tunnel connection failed: 403" not in repr(e) or not sources.resi.configured():
                raise
            return sources.fetch(url, proxy=sources.resi.url(country="hk"), **kw)
    proxy = sources.resi.url(country=px if isinstance(px, str) else "hk") if px else None
    return sources.fetch(url, proxy=proxy, **kw)


def read(site, fetch=None):
    """one site now → [{title, url, time, section, author?, text?}] (absolute URLs, newest PER_SITE). fetch(url) → text
    replaces the plain HTTP read (browser_fetch.py passes a real browser's for the "browser" sites)"""
    get_ = (lambda u: fetch(u)) if fetch else (lambda u: get(site, u))
    k, url = site["kind"], site["url"]
    if k == "epaper":
        today = datetime.now(HKT)
        rows, err, answered = [], None, False
        for day in (today - timedelta(days=n) for n in range(int(site.get("days") or 2))):
            try:
                page = get_(toc_url(site, day))
            except sources.urllib.error.HTTPError as e:
                # an issue not out yet: 404, or an error page (正報 answers a day it has not published with 500 "System
                # Error", 2026-10-22: its four pages failed until the issue was up): the day before. Only
                # when no day answers is it the site's failure (a block answers every day the same)
                if e.code != 404:
                    err = e
                continue
            answered = True
            rows = regex_list(site)(page) if site.get("pattern") else \
                se.anchors(site["href"], toc_url(site, day), min_title=site.get("min_title", 4))(quoted(page))
            for r in rows:
                r["url"] = urllib.parse.urljoin(toc_url(site, day), r["url"])
            for r in rows:
                r["time"] = r["time"] or f"{day:%Y-%m-%d}"
            if rows:
                break
        if err is not None and not answered:
            raise err
        return rows[:PER_SITE]
    if "{yyyy}" in url or "{yyyymmdd}" in url or "{ddmmyyyy}" in url:  # a dated list (a JSON API by day): today in Hong Kong time
        today = datetime.now(HKT) - timedelta(days=int(site.get("back") or 0))  # ("back": a "from" date's lookback)
        url = toc_url(site, today)
        try:
            text = get_(url)
        except sources.urllib.error.HTTPError as e:
            # a list by year ({yyyy} only) has no page for the new year until its first post (MWC, HYAB, Smart City,
            # ITIB in early January): last year's; a list by day: yesterday's
            if e.code != 404:
                raise
            yearly = not any(k in site["url"] for k in ("{mm}", "{dd}", "{yyyymmdd}", "{ddmmyyyy}"))
            url = toc_url(site, today.replace(year=today.year - 1, day=min(today.day, 28)) if yearly else today - timedelta(days=1))
            text = get_(url)
    elif k == "parser" and getattr(se.SITE_PARSERS[site["parser"]], "fetches_itself", False):
        text = None  # a parser that makes its own requests (student.hk's signed API)
    else:
        text = get_(url)
    if k == "rss":
        rows = feed(text)
        for r in rows:
            if site["type"] in ("Newspaper", "Magazine", "Blog", "Official", "News") and len(r.get("desc") or "") >= 500:
                r["text"] = r["desc"]  # a feed that carries the whole story
    elif k == "wp_json":
        rows = wp_json(text)
    elif k == "sitemap":
        rows = se.news_sitemap(keep=site.get("keep"), slug=site.get("slug", True))(text)
    elif k == "sitemap_index":
        inner = lambda t: se.news_sitemap(keep=site.get("keep"), slug=site.get("slug", True))(t)  # noqa: E731
        locs = [html.unescape(x) for x in re.findall(r"<sitemap>\s*<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s\]]+)", text)]
        if site.get("match"):
            locs = [x for x in locs if re.search(site["match"], x)]
        locs = locs[:site["first"]] if site.get("first") else locs[-int(site.get("last") or 1):]
        rows = []
        for x in locs:
            rows += inner(get_(x))
        rows = se.recent_rows(rows)
    elif k == "listing":
        rows = se.anchors(site["href"], url, min_title=site.get("min_title", 6))(quoted(text))
    elif k == "regex":
        rows = regex_list(site)(text)
    elif k == "xml":
        rows = xml_list(site)(text)
    elif k == "parser":
        rows = se.SITE_PARSERS[site["parser"]](text, site, get_)
    else:
        rows = json_list(site)(text)
    out = []
    for r in rows:
        if isinstance(r.get("time"), str) and CST.search(r["time"]):
            r["time"] = CST.sub(" +0800", r["time"])
        if r.get("title") and r.get("url"):
            r["url"] = urllib.parse.urljoin(url, r["url"].strip())
            out.append(r)
    out.sort(key=lambda r: (parse_time(r.get("time")) or datetime.min.replace(tzinfo=HKT)), reverse=True)
    return out[:PER_SITE]


# ---- a run ----

def recent(r, now):
    t = parse_time(r.get("time"))
    if t is None:
        return True
    if isinstance(t, sources.corpus.DateOnly):
        return now.date() - t.date() <= timedelta(days=2)
    return now - t <= MAX_AGE and t <= now + timedelta(hours=1)


SEEN_MAX = 2000  # = app.SEEN_MAX: poll_seen's key is the URL, or sha256 of a longer one (app.seen_key)


def seen_key(u):
    import hashlib
    return u if len(u.encode()) <= SEEN_MAX else "sha256:" + hashlib.sha256(u.encode()).hexdigest()


def unseen(urls):
    """the URLs no poll has read yet (table poll_seen, the poller's own record)"""
    keys = {seen_key(u): u for u in urls}
    have = {r[0] for r in sources.corpus.db().execute("SELECT url FROM poll_seen WHERE url = ANY(%s)", (list(keys),))}
    return {u for k, u in keys.items() if k not in have}


def run(sites=None, now=None, deadline=None):
    corpus = sources.corpus
    sites = sites if sites is not None else load_read()
    now = now or datetime.now(HKT)
    start = time.time()
    deadline = deadline or start + RUN_SECONDS
    doc = corpus.file_get(DOC, None) or {"sites": {}, "items": []}
    state = doc.get("sites") or {}
    sites = [s for s in sites if not s.get("browser")]  # read by browser_fetch.py
    names = {s["id"] for s in sites}
    due = [s for s in sites if time.time() - (state.get(s["id"], {}).get("tried") or 0) >= every(s, state.get(s["id"])) - 60]
    by_host = {}
    for s in due:
        by_host.setdefault(urllib.parse.urlsplit(s["url"]).hostname, []).append(s)
    found = {}

    def escalate(s, prev, e):
        """a site the route it was read on stopped reaching (a 403/429/5xx, a challenge page where its JSON or feed was,
        a TLS or read timeout), on its FIRST failure (Deyao, 2026-10-21 "fix the failing sources": a WARP exit can be
        refused by sites another exit reads, and a site waiting
        for a second failure stayed failing for hours at its quiet cadence): Chrome's TLS fingerprint direct (curl_cffi:
        a TLS handshake urllib can't finish, Cover Magazine 2026-10-21), the ISP IP, then yycstream hk (Deyao,
        2026-10-20: direct first, the flat-rate ISP IP before the per-GB pool), each plain then as Chrome, leaving out
        the route that just failed. A site with a route of its own in sites.json escalates too. YouTube never goes over
        yycstream (its audio once took 5.19 GB of it). A route that brings rows wins over one that answers with none (a
        challenge page parses as an empty feed: BILLIONAIRE's sgcaptcha as Chrome direct). The route that reads is kept
        for a day (AUTO_RECHECK), then the site's own route is tried again → rows or None"""
        if s.get("browser") or s.get("forum"):
            return None
        tried = ("direct" if not s.get("proxy") else "hk" if s.get("proxy") is True else s["proxy"], bool(s.get("impersonate")))
        youtube = "youtube.com" in (urllib.parse.urlsplit(s["url"]).hostname or "")
        steps = [("direct", False), ("direct", True), ("isp", False), ("isp", True), ("hk", False), ("hk", True)]
        empty, done = None, {tried}
        for px, imp in steps:
            imp = imp or bool(s.get("impersonate"))  # a site read as Chrome (sites.json "impersonate") stays Chrome
            if time.time() > deadline:
                break
            if (px, imp) in done:
                continue
            done.add((px, imp))
            if px == "isp" and not os.environ.get("ISP_PROXY") or px == "hk" and (youtube or not sources.resi.configured()):
                continue
            try:
                rows = read(dict(s, proxy=None if px == "direct" else px, impersonate=imp, _cond=None))
            except Exception:  # noqa: BLE001: the next route (IPRoyal out of traffic included: the hk steps just fail)
                continue
            if not rows:
                empty = empty or (px, imp, rows)
                continue
            empty = (px, imp, rows)
            break
        if empty is None:
            return None
        px, imp, rows = empty
        prev.update(auto_route=px, auto_impersonate=imp, auto_since=time.time())
        state[s["id"]] = prev
        return rows

    def host_work(group):
        res = []
        for i, s in enumerate(group):
            if time.time() > deadline:
                break
            if i:
                time.sleep(HOST_GAP)
            t0 = time.time()
            prev = state.get(s["id"]) or {}
            s = dict(s, _cond={"etag": prev.get("etag"), "last_modified": prev.get("last_modified")})
            auto = prev.get("auto_route")  # a route this job found itself (escalate(), below)
            auto = auto if auto and time.time() - (prev.get("auto_since") or 0) < AUTO_RECHECK else None
            if auto:
                s["proxy"] = None if auto == "direct" else auto
                s["impersonate"] = s.get("impersonate") or prev.get("auto_impersonate")
            try:
                rows = read(s)
                if auto and not rows:  # a found route answering an empty list (BILLIONAIRE's sgcaptcha page as Chrome
                    got = escalate(s, prev, None)  # direct, kept by the old escalation): another route with rows wins
                    rows = got if got is not None else rows
                res.append((s, rows, None))
            except NotModified:
                res.append((s, None, None))
            except sources.resi.ProxyOut as e:
                res.append((s, [], ("off", repr(e)[:200])))
            except Exception as e:
                got = escalate(s, prev, e)
                res.append((s, got, None) if got is not None else (s, [], ("error", repr(e)[:200])))
            res[-1] += (time.time() - t0,)
        return res

    with cf.ThreadPoolExecutor(THREADS) as ex:
        for res in ex.map(host_work, by_host.values()):
            for s, rows, err, took in res:
                st = state.get(s["id"]) or {}
                st.update(name=s["name"], tried=time.time(), at=now.isoformat(timespec="seconds"), took=round(took, 1))
                if err and err[0] == "off":  # IPRoyal out of traffic: a pause, not a failure of the site
                    st.update(error=err[1], off=sources.resi.STATUS)
                elif err:
                    st.update(ok=False, error=err[1], items=0, fails=(st.get("fails") or 0) + 1, off=None)
                elif rows is None:  # 304: unchanged since the last read (its counts stand)
                    st.update(ok=True, error=None, fails=0, off=None, unchanged=(st.get("unchanged") or 0) + 1,
                              quiet=(st.get("quiet") or 0) + 1)
                else:
                    keep = [r for r in rows if recent(r, now)]
                    st.update(ok=True, error=None, items=len(rows), recent=len(keep), fails=0, off=None, unchanged=0,
                              newest=max([str(parse_time(r.get("time")) or "") for r in rows] or [""]) or None,
                              etag=(s.get("_meta") or {}).get("etag"), last_modified=(s.get("_meta") or {}).get("last_modified"))
                    found[s["id"]] = keep
                state[s["id"]] = st
    # the links the poller has not read yet: this run's, and the earlier runs' still within KEEP
    items = {}
    for it in doc.get("items") or []:
        if it["sid"] in names and now - datetime.fromisoformat(it["found"]) <= KEEP:
            items[it["url"]] = it
    for sid, rows in found.items():
        for r in rows:
            if r["url"] not in items:
                items[r["url"]] = {"sid": sid, "title": r["title"][:300], "url": r["url"], "published": r.get("time") or "",
                                   "section": r.get("section") or "", "author": r.get("author"), "text": r.get("text"),
                                   "found": now.isoformat(timespec="seconds")}
    left = unseen(list(items)) if items else set()
    for sid, rows in found.items():  # its cadence: a read that brought a link no poll has read resets it (every())
        n = sum(1 for r in rows if r["url"] in left)
        state[sid]["new"] = n
        state[sid]["quiet"] = 0 if n else (state[sid].get("quiet") or 0) + 1
    doc = {"fetched": now.isoformat(timespec="seconds"), "sites": {k: v for k, v in state.items() if k in names},
           "items": [{k: v for k, v in it.items() if v not in (None, "")} for u, it in items.items() if u in left],
           "due": len(due), "read": sum(1 for s in due if state[s["id"]].get("at") == now.isoformat(timespec="seconds")),
           "seconds": round(time.time() - start, 1)}
    corpus.file_put(DOC, doc)
    return doc


def main(argv):
    if argv[:1] == ["run"]:
        d = run()
        bad = [k for k, v in d["sites"].items() if v.get("ok") is False]
        print(f"sites: {d['read']}/{d['due']} due read in {d['seconds']} s, {len(d['items'])} links waiting, "
              f"{len(bad)} failing", flush=True)
    elif argv[:1] == ["test"] and len(argv) == 2:
        s = next(x for x in load() if x["id"] == argv[1])
        for r in read(s):
            print(r.get("time"), "|", r["title"][:80], "|", r["url"])
    elif argv[:1] == ["check"]:
        sites = [s for s in load() if not argv[1:] or s["id"] in argv[1:]]
        now = datetime.now(HKT)

        def one(s):
            try:
                rows = read(s)
                newest = max([str(parse_time(r.get("time")) or "") for r in rows] or [""])
                return s["id"], "ok", len(rows), sum(recent(r, now) for r in rows), newest, ""
            except Exception as e:
                return s["id"], "FAIL", 0, 0, "", repr(e)[:150]
        with cf.ThreadPoolExecutor(8) as ex:
            for row in ex.map(one, sites):
                print("\t".join(str(x) for x in row), flush=True)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
